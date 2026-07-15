import logging
import threading
import time
import string
import random
import secrets
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from DrissionPage.errors import PageDisconnectedError
from config import SIGNUP_URL
from core.grok2api_client import upload_registered_sso
from core.account_activation import activate_grok_web
from core.registration.state import (
    EMAIL_REQUEST_MIN_INTERVAL,
    ExistingAccountError,
    RegistrationState,
    VerificationRequestError,
    email_request_slot,
    is_xai_permission_denied,
    submit_is_in_flight,
)
from core.registration.profile import (
    ProfileSubmitSnapshot,
    ProfileSubmitStage,
    classify_profile_submit,
    save_profile_diagnostics,
)

logger = logging.getLogger('register')


class RegistrationEngine:
    def __init__(self, db, browser_mgr, email_mgr, socketio, state):
        self.db = db
        self.browser = browser_mgr
        self.email_mgr = email_mgr
        self.socketio = socketio
        self.state = state
        self._cookie_banner_dismissed = False

    def run(self, max_rounds=0, max_retries=3, concurrency=1):
        self.state.status = 'running'
        settings = self.db.get_settings()
        concurrency = max(1, min(10, int(concurrency or 1)))
        batch_id = secrets.token_hex(6)
        claimed_any = threading.Event()
        browser_started_any = threading.Event()
        logger.info(
            'Registration started (max_rounds=%s, max_retries=%s, concurrency=%s)',
            max_rounds or 'unlimited', max_retries, concurrency,
        )

        workers = []
        try:
            for index in range(concurrency):
                worker_id = f'worker-{index + 1}'
                browser = self.browser.clone(worker_id=worker_id)
                worker_engine = RegistrationEngine(
                    self.db, browser, self.email_mgr, self.socketio, self.state,
                )
                thread = threading.Thread(
                    target=worker_engine._run_worker,
                    args=(
                        worker_id,
                        f'{batch_id}:{worker_id}',
                        max_rounds,
                        max_retries,
                        settings,
                        claimed_any,
                        browser_started_any,
                    ),
                    name=f'register-{worker_id}',
                    daemon=True,
                )
                workers.append(thread)
                thread.start()
            for thread in workers:
                thread.join()
        finally:
            self.state.status = 'stopped'
            self._emit_status()
            if not browser_started_any.is_set() and not self.state.should_stop():
                self._emit_error(
                    'BROWSER_START', 'All registration browsers failed to start',
                    fatal=True,
                )
            elif not claimed_any.is_set() and not self.state.should_stop():
                self._emit_error('NO_ALIASES', 'No available aliases', fatal=True)
            snapshot = self.state.get_snapshot()
            logger.info(
                'Registration ended. Completed: %s, Success: %s, Failed: %s',
                snapshot['completed'], snapshot['success'], snapshot['failed'],
            )

    def _run_worker(self, worker_id, lease_owner, max_rounds, max_retries,
                    settings, claimed_any, browser_started_any):
        self.browser.headless = settings.get('browser_headless', 'false') == 'true'
        self.browser.proxy = (settings.get('browser_proxy', '') or '').strip()
        lease_seconds = max(
            120,
            int(settings.get('registration_timeout', 300) or 300) * 2,
        )
        try:
            self.browser.start()
        except Exception as exc:
            logger.error('[%s] Failed to start browser: %s', worker_id, exc)
            self._emit_error(
                'BROWSER_START', f'[{worker_id}] Failed to start browser: {exc}',
                fatal=False,
            )
            return
        browser_started_any.set()

        try:
            while not self.state.should_stop():
                self.state.check_pause()
                if self.state.should_stop():
                    break
                alias = self.db.claim_next_alias(
                    max_retries=max_retries,
                    lease_owner=lease_owner,
                    lease_seconds=lease_seconds,
                )
                if not alias:
                    break
                claimed_any.set()

                round_num = self.state.reserve_round(max_rounds)
                if round_num is None:
                    self.db.release_alias_claim(alias['id'], lease_owner)
                    break

                self.state.set_worker_active(worker_id, round_num, alias)
                self._emit_status()
                heartbeat_stop = threading.Event()
                heartbeat = threading.Thread(
                    target=self._lease_heartbeat,
                    args=(
                        alias['id'], lease_owner, lease_seconds, heartbeat_stop,
                    ),
                    name=f'lease-{worker_id}',
                    daemon=True,
                )
                heartbeat.start()
                try:
                    try:
                        self._do_one_round(
                            alias, round_num, max_retries, settings,
                            lease_owner, worker_id,
                        )
                    except Exception as exc:
                        logger.exception(
                            '[%s] Unexpected worker round failure: %s',
                            worker_id, exc,
                        )
                        self.db.release_alias_claim(alias['id'], lease_owner)
                        self._emit_error(
                            'WORKER_ROUND',
                            f'[{worker_id}] Unexpected worker failure: {exc}',
                            fatal=False,
                        )
                finally:
                    heartbeat_stop.set()
                    heartbeat.join(timeout=2)
                    self.state.clear_worker(worker_id)
                    self._emit_status()
        finally:
            self.state.clear_worker(worker_id)
            try:
                self.browser.stop()
            except Exception:
                pass

    def _lease_heartbeat(self, alias_id, lease_owner, lease_seconds, stop_event):
        interval = max(5, min(60, lease_seconds // 3))
        while not stop_event.wait(interval):
            try:
                if not self.db.heartbeat_alias_lease(
                    alias_id, lease_owner, lease_seconds,
                ):
                    alias_state = self.db.get_alias_lease_state(alias_id) or {}
                    if (
                        alias_state.get('status') in ('used', 'failed')
                        and not alias_state.get('lease_owner')
                    ):
                        logger.debug(
                            'Alias %s lease ended after terminal completion',
                            alias_id,
                        )
                        return
                    logger.warning('Alias %s lease was lost', alias_id)
                    return
            except Exception as exc:
                logger.warning('Alias %s lease heartbeat failed: %s', alias_id, exc)

    # ── Browser helpers ────────────────────────────────────────

    def _refresh_active_page(self):
        """Re-acquire the active page handle (page may disconnect after navigation)."""
        try:
            self.browser.refresh_active_page()
        except Exception as e:
            logger.warning(f"Refresh page failed: {e}, restarting browser")
            self.browser.start()

    def _restart_browser(self, force_close=False):
        """Clear cookies/cache, optionally close and reopen browser."""
        try:
            page = self.browser.page
            if page:
                page.run_cdp('Network.clearBrowserCookies')
                page.run_cdp('Network.clearBrowserCache')
                for origin in ['https://accounts.x.ai', 'https://grok.com', 'https://auth.x.ai', 'https://x.ai']:
                    try:
                        page.run_cdp('Storage.clearDataForOrigin', origin=origin, storageTypes='all')
                    except Exception:
                        pass
        except Exception:
            pass

        if force_close:
            try:
                self.browser.stop()
            except Exception:
                pass
            time.sleep(1.5)
            self.browser.start()
            self._cookie_banner_dismissed = False
            logger.info("Browser restarted (force close)")
        else:
            try:
                if self.browser.browser:
                    new_page = self.browser.browser.new_tab('about:blank')
                    try:
                        if self.browser.page:
                            self.browser.page.close()
                    except Exception:
                        pass
                    self.browser._page = new_page
                    self._cookie_banner_dismissed = False
            except Exception:
                pass
            logger.info("Browser: cleared cookies, opened new tab")
        time.sleep(1)

    # ── Single round ───────────────────────────────────────────

    def _do_one_round(self, alias, round_num, max_retries, settings,
                      lease_owner, worker_id):
        alias_email = alias['alias_email']
        logger.info('[%s] Round %s: using alias %s', worker_id, round_num, alias_email)

        password = self._get_password(settings)
        reg_id = self.db.create_registration(
            alias_id=alias['id'],
            email=alias_email,
            password=password,
            round_number=round_num,
            lease_owner=lease_owner,
        )
        start_time = time.time()
        success_committed = False

        try:
            # 1. Open signup page
            self.state.check_pause()
            logger.info("Opening registration page...")
            self._open_signup_page()

            # 2. Fill email
            self.state.check_pause()
            logger.info(f"Filling email: {alias_email}")
            verification_requested_at = self._fill_email(alias_email)

            # 3. Get verification code
            self.state.check_pause()
            logger.info("Requesting verification code...")
            code = self.email_mgr.get_code_for_alias(
                alias_email, alias['account_id'],
                alias['client_id'], alias['refresh_token'],
                max_retries=int(settings.get('max_code_retries', 3)),
                main_email=alias.get('main_email'),
                requested_after=verification_requested_at,
            )

            # 4. Fill code and confirm
            self.state.check_pause()
            logger.info(f"Filling verification code: {code}")
            self._fill_and_confirm_code(code)

            # 5. Fill profile
            self.state.check_pause()
            logger.info("Filling profile information...")
            self._fill_profile(password, settings, alias_email=alias_email)

            # 6. Extract SSO (turnstile is handled inside _fill_profile)
            logger.info("Extracting SSO token...")
            sso = self._extract_sso()

            # Once SSO is obtained, registration is successful.
            # Web activation / CF challenge must NEVER fail the whole round —
            # otherwise we lose a good account and skip grok2api upload.
            activation = None
            if settings.get('grok_web_activation', 'true') == 'true':
                try:
                    logger.info('Activating Grok Web before clearing browser cookies...')
                    activation = activate_grok_web(
                        self.browser,
                        sso,
                        proxy_url=(settings.get('browser_proxy', '') or '').strip(),
                    )
                    if activation and activation.ready:
                        logger.info(f'Grok Web activation completed: {activation.message}')
                    else:
                        msg = activation.message if activation else 'no activation result'
                        logger.warning(f'Grok Web activation incomplete (non-fatal): {msg}')
                except Exception as act_err:
                    logger.warning(f'Grok Web activation raised (non-fatal): {act_err}')
                    activation = None

            # 8. Extract visible numbers (optional)
            if settings.get('extract_numbers_enabled', 'false') == 'true':
                numbers = self._extract_visible_numbers()
                if numbers:
                    logger.info(f"Extracted page numbers: {numbers}")

            # 9. Save results — SSO already in hand, mark success first
            duration = time.time() - start_time
            completion = self.db.complete_registration_success(
                reg_id, alias['id'], lease_owner, sso, duration=duration,
            )
            success_committed = True

            try:
                upload_result = upload_registered_sso(
                    settings, sso, email=alias_email,
                    user_agent=activation.user_agent if activation else '',
                    cloudflare_cookies=activation.cloudflare_cookies if activation else '',
                )
                if upload_result is not None:
                    imported = upload_result.get('import', {})
                    converted = upload_result.get('conversion', {})
                    logger.info(
                        'grok2api auto pipeline completed: web_created=%s web_updated=%s '
                        'web_synced=%s web_sync_failed=%s build_created=%s linked=%s '
                        'skipped=%s failed=%s build_synced=%s build_sync_failed=%s',
                        imported.get('created', 0),
                        imported.get('updated', 0),
                        imported.get('synced', 0),
                        imported.get('syncFailed', 0),
                        converted.get('created', 0),
                        converted.get('linked', 0),
                        converted.get('skipped', 0),
                        converted.get('failed', 0),
                        converted.get('synced', 0),
                        converted.get('syncFailed', 0),
                    )
            except Exception as upload_error:
                logger.warning(f'grok2api auto upload failed: {upload_error}')

            self.state.record_success()
            logger.info(f"Round {round_num} SUCCESS! Duration: {duration:.1f}s")

            self.socketio.emit('round_complete', {
                'round': round_num,
                'email': alias_email,
                'success': True,
                'sso': sso[:50] + '...' if len(sso) > 50 else sso,
                'duration': round(duration, 1),
            })

            # 10. Clear cookies for next round (don't close browser)
            self._restart_browser(force_close=False)
            self._emit_status()

            if completion['account_done']:
                logger.info(f"Account {alias['main_email']} aliases exhausted, marked as done")

        except Exception as e:
            duration = time.time() - start_time
            error_msg = str(e)
            if success_committed:
                logger.warning(
                    'Round %s completed successfully but cleanup failed: %s',
                    round_num, error_msg,
                )
                return
            if isinstance(e, ExistingAccountError):
                outcome = self.db.skip_existing_account_attempt(
                    reg_id=reg_id,
                    alias_id=alias['id'],
                    lease_owner=lease_owner,
                    error=error_msg,
                    duration=duration,
                )
                self.state.record_failure()
                if outcome['lease_lost']:
                    logger.warning(
                        'Existing account %s detected, but alias lease was lost before skip commit',
                        alias_email,
                    )
                else:
                    logger.warning(
                        'Existing account skipped without retry: %s',
                        alias_email,
                    )
                    if outcome['account_disabled']:
                        logger.warning(
                            'Skipped remaining aliases for account %s: %s',
                            alias.get('main_email') or alias_email,
                            outcome['disable_reason'],
                        )
                self.socketio.emit('round_complete', {
                    'round': round_num,
                    'email': alias_email,
                    'success': False,
                    'skipped': True,
                    'reason': 'existing_account',
                    'duration': round(duration, 1),
                })
                self._emit_status()
                if not self.state.should_stop():
                    try:
                        self._restart_browser(force_close=True)
                    except Exception:
                        pass
                return
            if isinstance(e, VerificationRequestError) and is_xai_permission_denied(e):
                released = self.db.abort_registration_attempt(
                    reg_id=reg_id,
                    alias_id=alias['id'],
                    lease_owner=lease_owner,
                    error=error_msg,
                    duration=duration,
                )
                self.state.record_failure()
                self.state.stop()
                logger.error(
                    'Round %s stopped by xAI permission_denied 403; alias %s '
                    'was %s without consuming a retry',
                    round_num,
                    alias_email,
                    'released' if released else 'not released (lease lost)',
                )
                self._emit_error(
                    'XAI_PERMISSION_DENIED',
                    'xAI rejected the verification-code request with HTTP 403. '
                    'No email was sent; the alias was preserved. Retry later or '
                    'change the network/IP before restarting registration.',
                    fatal=True,
                )
                self._emit_status()
                return
            outcome = self.db.finish_registration_attempt(
                reg_id=reg_id,
                alias_id=alias['id'],
                lease_owner=lease_owner,
                error=error_msg,
                duration=duration,
                max_retries=max_retries,
            )
            logger.error(f"Round {round_num} FAILED: {error_msg}")
            current_retries = outcome['retry_count']
            if outcome['lease_lost']:
                logger.warning('Alias %s lease was lost before failure commit', alias['id'])
            elif outcome['terminal']:
                self.state.record_failure()
                logger.info(f"Alias {alias_email} exhausted {max_retries} retries, marked failed")
                if outcome['account_disabled']:
                    logger.warning(
                        'Skipped remaining aliases for account %s: %s',
                        alias.get('main_email') or alias_email,
                        outcome['disable_reason'],
                    )
            else:
                logger.info(f"Alias {alias_email} will retry ({current_retries}/{max_retries})")
            self._emit_status()
            # If stop was requested, just exit without restarting browser
            if self.state.should_stop():
                return
            try:
                self._restart_browser(force_close=True)
            except Exception:
                pass

    # ── Page interaction (JS-based, matching original script) ──

    def _open_signup_page(self):
        self._cookie_banner_dismissed = False
        logger.info("Refreshing active page...")
        self._refresh_active_page()
        logger.info(f"Navigating to {SIGNUP_URL}")
        try:
            self.browser.get(SIGNUP_URL)
        except Exception as e:
            logger.warning(f"Navigation failed, retrying: {e}")
            self._refresh_active_page()
            self.browser.browser.new_tab(SIGNUP_URL)
        time.sleep(2)
        self._wait_for_signup_ready(timeout=60)
        self._dismiss_cookie_banner()
        logger.info("Looking for email signup button...")
        self._click_email_signup_button()

    def _wait_for_signup_ready(self, timeout=60):
        """Wait out Cloudflare/challenge pages before looking for signup controls."""
        deadline = time.time() + timeout
        last_notice = 0
        while time.time() < deadline:
            try:
                state = self.browser.run_js(r"""
const title = String(document.title || '').toLowerCase();
const body = String((document.body && document.body.innerText) || '').toLowerCase();
const href = String(location.href || '').toLowerCase();
const challenge = title.includes('just a moment')
  || body.includes('verifying you are human')
  || body.includes('performing security verification')
  || !!document.querySelector('#challenge-stage, script[src*="challenge-platform"]');
const hasEmailField = !!document.querySelector('input[type="email"], input[name="email"], input[autocomplete="email"]');
const buttons = Array.from(document.querySelectorAll('button, a, [role="button"]')).map(n =>
  ((n.innerText || n.textContent || '') + ' ' + (n.getAttribute('aria-label') || '')).toLowerCase()
);
const hasEmailSignup = buttons.some(t =>
  t.includes('使用邮箱注册')
  || t.includes('邮箱注册')
  || (t.includes('sign up') && t.includes('email'))
  || t.includes('signup with email')
  || t.includes('continue with email')
  || t.includes('register with email')
);
return {challenge, href, hasEmailField, hasEmailSignup, title: document.title};
                """) or {}
                if state.get('challenge'):
                    now = time.time()
                    if now - last_notice >= 10:
                        logger.info('Signup page is still on Cloudflare challenge, waiting...')
                        last_notice = now
                    time.sleep(1)
                    continue
                if state.get('hasEmailField') or state.get('hasEmailSignup'):
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        logger.warning('Signup page readiness wait timed out; continuing anyway')
        return False

    def _click_email_signup_button(self, timeout=20):
        """Click the email signup entry button after page loads.

        Supports both Chinese ('使用邮箱注册') and English ('Sign up with email') UI.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                # Prefer a real Chromium click. xAI can distinguish DOM
                # element.click() from trusted pointer input.
                for element in self.browser.page.eles(
                    'css:button, [role="button"]'
                ):
                    label = str(element.text or '').strip()
                    normalized = re.sub(r'\s+', ' ', label).lower()
                    compact = normalized.replace(' ', '')
                    if any(word in normalized for word in ('google', 'apple', 'cookie')):
                        continue
                    if (
                        '使用邮箱' in label
                        or compact in ('signupwithemail', 'continuewithemail')
                        or 'sign up with email' in normalized
                        or 'continue with email' in normalized
                    ):
                        element.click()
                        logger.info('Clicked email signup button natively: %s', label)
                        return True
            except Exception as exc:
                logger.debug('Native email entry click unavailable: %s', exc)
            try:
                # If email field is already visible, no entry button is needed.
                already = self.browser.run_js(r"""
return !!document.querySelector('input[type="email"], input[name="email"], input[autocomplete="email"]');
                """)
                if already:
                    logger.info('Email input already visible; skipping email signup button')
                    return True

                clicked = self.browser.run_js(r"""
function normalize(text) {
  return String(text || '').replace(/\s+/g, ' ').trim().toLowerCase();
}
function isEmailSignupLabel(text) {
  const t = normalize(text).replace(/\s+/g, '');
  if (!t) return false;
  if (t.includes('使用邮箱注册') || t.includes('邮箱注册')) return true;
  if (t.includes('signupwithemail') || t.includes('signupwithe-mail')) return true;
  if (t.includes('continuewithemail') || t.includes('registerwithemail')) return true;
  // spaced variants
  const s = normalize(text);
  if (s.includes('sign up with email') || s.includes('sign-up with email')) return true;
  if (s.includes('continue with email') || s.includes('register with email')) return true;
  if (s.includes('use email') && (s.includes('sign') || s.includes('register'))) return true;
  return false;
}
const candidates = Array.from(document.querySelectorAll('button, a, [role="button"]'));
const target = candidates.find((node) => {
  const label = (node.innerText || node.textContent || '') + ' ' + (node.getAttribute('aria-label') || '');
  return isEmailSignupLabel(label);
});
if (!target) {
  return {
    clicked: false,
    labels: candidates.map(n => ((n.innerText || n.textContent || '') + ' ' + (n.getAttribute('aria-label') || '')).replace(/\s+/g, ' ').trim()).filter(Boolean).slice(0, 12)
  };
}
try { target.scrollIntoView({block: 'center'}); } catch (e) {}
target.click();
return {clicked: true, label: (target.innerText || target.textContent || '').replace(/\s+/g, ' ').trim()};
                """) or {}
                if isinstance(clicked, dict) and clicked.get('clicked'):
                    logger.info("Clicked email signup button: %s", clicked.get('label') or '')
                    return True
                if isinstance(clicked, dict) and clicked.get('labels'):
                    logger.debug('Email signup button candidates: %s', clicked.get('labels'))
            except Exception:
                pass
            # Cookie banners can cover the entry button on English UI.
            self._dismiss_cookie_banner()
            time.sleep(0.5)
        raise Exception('未找到邮箱注册入口按钮（支持：使用邮箱注册 / Sign up with email）')

    def _fill_email(self, email_addr, timeout=15):
        """Fill and submit the email form, preferring trusted browser input."""
        logger.info(f"Filling email: {email_addr}")
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                # Real keyboard and pointer events are preferred over synthetic
                # JS events because xAI uses browser-behavior signals when it
                # authorizes the verification-code request.
                email_input = self.browser.page.ele(
                    'css:input[data-testid="email"], input[name="email"], '
                    'input[type="email"], input[autocomplete="email"]',
                    timeout=2,
                )
                if email_input:
                    email_input.click()
                    email_input.input(email_addr, clear=True, by_js=False)
                    time.sleep(random.uniform(0.7, 1.3))
                    candidates = self.browser.page.eles(
                        'css:button[type="submit"], button'
                    )
                    submit = None
                    for element in candidates:
                        label = str(element.text or '').strip()
                        normalized = re.sub(r'\s+', ' ', label).lower()
                        compact = normalized.replace(' ', '')
                        if any(word in normalized for word in ('google', 'apple', 'cookie')):
                            continue
                        if (
                            compact in ('signup', 'sign-up', 'continue', 'next', 'submit', '注册')
                            or normalized in ('sign up', 'sign-up')
                        ):
                            submit = element
                            break
                    if submit is None:
                        submit = next(
                            (
                                element for element in candidates
                                if str(element.attr('type') or '').lower() == 'submit'
                            ),
                            None,
                        )
                    if submit:
                        with email_request_slot():
                            requested_at = datetime.now(timezone.utc)
                            submit.click()
                            logger.info(
                                'Filled email and clicked submit natively (%s): %s',
                                str(submit.text or '').strip() or 'submit',
                                email_addr,
                            )
                            self._wait_for_verification_request(email_addr)
                        return requested_at
            except VerificationRequestError:
                raise
            except Exception as exc:
                logger.debug('Native email form interaction unavailable: %s', exc)
            try:
                filled = self.browser.run_js(
                    """
const email = arguments[0];
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
const input = Array.from(document.querySelectorAll(
    'input[data-testid="email"], input[name="email"], input[type="email"], input[autocomplete="email"]'
)).find(n => isVisible(n) && !n.disabled && !n.readOnly) || null;
if (!input) return 'not-ready';
input.focus();
input.click();
const valueSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
const tracker = input._valueTracker;
if (tracker) tracker.setValue('');
if (valueSetter) valueSetter.call(input, email);
else input.value = email;
input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, data: email, inputType: 'insertText' }));
input.dispatchEvent(new InputEvent('input', { bubbles: true, data: email, inputType: 'insertText' }));
input.dispatchEvent(new Event('change', { bubbles: true }));
if ((input.value || '').trim() !== email || !input.checkValidity()) return false;
input.blur();
return 'filled';
                    """,
                    email_addr,
                )

                if filled == 'not-ready':
                    time.sleep(0.5)
                    continue

                if filled == 'filled':
                    time.sleep(0.8)
                    email_turnstile = self.browser.run_js(r"""
const input = document.querySelector('input[name="cf-turnstile-response"]');
if (!input) return 'not-found';
return String(input.value || '').trim().length >= 50 ? 'ready' : 'pending';
                    """)
                    if email_turnstile == 'pending':
                        logger.info('Turnstile pending before email submission, solving...')
                        self._solve_turnstile()
                    # Click submit/register button
                    with email_request_slot():
                        requested_at = datetime.now(timezone.utc)
                        clicked = self.browser.run_js(r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function normalize(text) {
    return String(text || '').replace(/\s+/g, ' ').trim().toLowerCase();
}
function isEmailSubmitLabel(text) {
    const s = normalize(text);
    const t = s.replace(/\s+/g, '');
    if (!t) return false;
    // Chinese
    if (t === '注册' || t.includes('注册') || t.includes('继续') || t.includes('下一步')) return true;
    // English
    if (t === 'signup' || t === 'sign-up' || t === 'continue' || t === 'next' || t === 'submit') return true;
    if (s === 'sign up' || s === 'sign-up' || s.includes('continue') || s.includes('next')) return true;
    // Avoid social/oauth/cookie buttons
    if (s.includes('google') || s.includes('apple') || s.includes(' cookie') || s.includes('reject') || s.includes('accept all')) return false;
    return false;
}
const input = Array.from(document.querySelectorAll(
    'input[data-testid="email"], input[name="email"], input[type="email"], input[autocomplete="email"]'
)).find(n => isVisible(n) && !n.disabled && !n.readOnly) || null;
if (!input || !input.checkValidity() || !(input.value || '').trim()) return {ok:false, reason:'invalid-email'};
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button')).filter(n =>
    isVisible(n) && !n.disabled && n.getAttribute('aria-disabled') !== 'true'
);
// Prefer exact primary action near the form.
let submitButton = buttons.find(n => {
    const text = n.innerText || n.textContent || '';
    const t = normalize(text).replace(/\s+/g, '');
    return t === 'signup' || t === 'sign-up' || t === '注册';
});
if (!submitButton) {
    submitButton = buttons.find(n => isEmailSubmitLabel(n.innerText || n.textContent || ''));
}
if (!submitButton) {
    // Fallback: first enabled submit button that is not cookie-related.
    submitButton = buttons.find(n => n.type === 'submit' && !/cookie|reject|accept|allow|confirm my choices/i.test(n.innerText || ''));
}
if (!submitButton || submitButton.disabled) {
    return {ok:false, reason:'no-submit', labels: buttons.map(n => (n.innerText||'').trim()).filter(Boolean).slice(0,10)};
}
try { submitButton.scrollIntoView({block:'center'}); } catch (e) {}
submitButton.click();
return {ok:true, label:(submitButton.innerText || submitButton.textContent || '').replace(/\s+/g,' ').trim()};
                        """)
                    if isinstance(clicked, dict) and clicked.get('ok'):
                        logger.info(
                            "Filled email and clicked submit (%s): %s",
                            clicked.get('label') or 'submit',
                            email_addr,
                        )
                        self._wait_for_verification_request(email_addr)
                        return requested_at
                    if isinstance(clicked, dict) and clicked.get('labels'):
                        logger.debug('Email submit candidates: %s', clicked.get('labels'))
                    elif clicked is True:
                        logger.info(f"Filled email and clicked submit: {email_addr}")
                        self._wait_for_verification_request(email_addr)
                        return requested_at

            except VerificationRequestError:
                raise
            except Exception:
                pass
            time.sleep(0.5)

        raise Exception("Failed to fill email or find submit button")

    def _wait_for_verification_request(self, email_addr, timeout=20):
        """Wait until xAI accepts the send-code request or exposes its error."""
        deadline = time.time() + timeout
        last_state = {}
        while time.time() < deadline:
            state = self.browser.run_js(r"""
function visible(node) {
  if (!node) return false;
  const style = getComputedStyle(node);
  const rect = node.getBoundingClientRect();
  return style.display !== 'none' && style.visibility !== 'hidden'
    && style.opacity !== '0' && rect.width > 0 && rect.height > 0;
}
const codeInput = Array.from(document.querySelectorAll('input')).find((node) => {
  if (!visible(node) || node.disabled || node.readOnly) return false;
  const meta = [
    node.name, node.id, node.autocomplete, node.placeholder,
    node.getAttribute('data-testid'), node.getAttribute('aria-label')
  ].join(' ').toLowerCase();
  const maxLength = Number(node.maxLength || 0);
  return meta.includes('code') || meta.includes('otp')
    || meta.includes('verification') || meta.includes('one-time')
    || maxLength === 6;
});
const emailInput = Array.from(document.querySelectorAll(
  'input[type="email"], input[name="email"], input[autocomplete="email"]'
)).find(visible);
const alerts = Array.from(document.querySelectorAll(
  '[role="alert"], [aria-live="assertive"], [data-testid*="error"], .error, .text-error, .text-red-500'
)).filter(visible).map(node => String(node.innerText || node.textContent || '').trim());
const body = String(document.body?.innerText || '').replace(/\s+/g, ' ').trim();
const lower = body.toLowerCase();
let error = alerts.filter(Boolean).join(' | ');
if (!error && (
  lower.includes('permission_denied') || lower.includes('http 403')
  || lower.includes('permission denied') || lower.includes('access denied')
  || lower.includes('too many requests') || lower.includes('try again later')
)) {
  const marker = lower.search(/permission_denied|http 403|permission denied|access denied|too many requests|try again later/);
  error = body.slice(Math.max(0, marker - 100), marker + 240);
}
const readyText = lower.includes('check your email')
  || lower.includes('enter the code') || lower.includes('verification code')
  || lower.includes('confirmation code') || lower.includes('验证码');
return {
  ready: !!codeInput || (readyText && !emailInput),
  error,
  href: location.href,
  title: document.title,
  emailVisible: !!emailInput,
  body: body.slice(0, 500),
};
            """) or {}
            last_state = state if isinstance(state, dict) else {}
            error = str(last_state.get('error') or '').strip()
            if error:
                raise VerificationRequestError(
                    f'xAI verification-code request rejected for {email_addr}: '
                    f'{error[:300]}'
                )
            if last_state.get('ready'):
                logger.info('xAI accepted verification-code request for %s', email_addr)
                return
            time.sleep(0.5)

        raise VerificationRequestError(
            'xAI verification-code request did not reach the code-entry page: '
            f'url={last_state.get("href", "")} '
            f'body={str(last_state.get("body") or "")[:240]}'
        )

    def _fill_and_confirm_code(self, code, timeout=180):
        """Fill verification code and confirm, with multiple retry strategies."""
        deadline = time.time() + timeout

        while time.time() < deadline:
            try:
                filled = self.browser.run_js(
                    """
const code = String(arguments[0] || '').trim();
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function setNativeValue(input, value) {
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
    const tracker = input._valueTracker;
    if (tracker) tracker.setValue('');
    if (setter) { setter.call(input, ''); setter.call(input, value); }
    else { input.value = ''; input.value = value; }
}
function dispatchInputEvents(input, value) {
    input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, cancelable: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new InputEvent('input', { bubbles: true, cancelable: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
}

const input = Array.from(document.querySelectorAll(
    'input[data-input-otp="true"], input[name="code"], input[autocomplete="one-time-code"], input[inputmode="numeric"], input[inputmode="text"]'
)).find(n => isVisible(n) && !n.disabled && !n.readOnly && Number(n.maxLength || code.length || 6) > 1) || null;

const otpBoxes = Array.from(document.querySelectorAll('input')).filter(n => {
    if (!isVisible(n) || n.disabled || n.readOnly) return false;
    const maxLength = Number(n.maxLength || 0);
    const autocomplete = String(n.autocomplete || '').toLowerCase();
    return maxLength === 1 || autocomplete === 'one-time-code';
});

if (!input && otpBoxes.length < code.length) return 'not-ready';

if (input) {
    input.focus();
    input.click();
    setNativeValue(input, code);
    dispatchInputEvents(input, code);
    const normalizedValue = String(input.value || '').trim();
    if (normalizedValue !== code) {
        if (otpBoxes.length >= code.length) {
            const orderedBoxes = otpBoxes.slice(0, code.length);
            for (let i = 0; i < orderedBoxes.length; i++) {
                const box = orderedBoxes[i];
                box.focus(); box.click();
                setNativeValue(box, code[i] || '');
                dispatchInputEvents(box, code[i] || '');
                box.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: code[i] }));
                box.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: code[i] }));
                box.blur();
            }
            return orderedBoxes.map(n => String(n.value || '').trim()).join('') === code ? 'filled' : 'box-mismatch';
        }
        return 'aggregate-mismatch';
    }
    input.blur();
    return 'filled';
}

const orderedBoxes = otpBoxes.slice(0, code.length);
for (let i = 0; i < orderedBoxes.length; i++) {
    const box = orderedBoxes[i];
    box.focus(); box.click();
    setNativeValue(box, code[i] || '');
    dispatchInputEvents(box, code[i] || '');
    box.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: code[i] }));
    box.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: code[i] }));
    box.blur();
}
return orderedBoxes.map(n => String(n.value || '').trim()).join('') === code ? 'filled' : 'box-mismatch';
                    """,
                    code,
                )
            except PageDisconnectedError:
                # Page navigated after confirmation, handle like original
                self._refresh_active_page()
                if self._wait_for_stable_profile_form(timeout=3):
                    logger.info("Page navigated after code submission, profile form detected")
                    return
                time.sleep(1)
                continue
            except Exception as e:
                logger.warning(f"Code fill error: {e}")
                time.sleep(1)
                continue

            if filled == 'not-ready':
                if (
                    self._has_profile_form()
                    and self._wait_for_stable_profile_form(timeout=3)
                ):
                    logger.info("Already on profile page, skipping code confirmation")
                    return
                time.sleep(0.5)
                continue

            if filled == 'filled':
                time.sleep(1.2)
                # Click confirm button
                try:
                    clicked = self.browser.run_js(r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
const aggregateInput = Array.from(document.querySelectorAll(
    'input[data-input-otp="true"], input[name="code"], input[autocomplete="one-time-code"], input[inputmode="numeric"], input[inputmode="text"]'
)).find(n => isVisible(n) && !n.disabled && !n.readOnly && Number(n.maxLength || 0) > 1) || null;
let value = '';
if (aggregateInput) {
    value = String(aggregateInput.value || '').trim();
    if (!value) return false;
} else {
    const otpBoxes = Array.from(document.querySelectorAll('input')).filter(n => {
        if (!isVisible(n) || n.disabled || n.readOnly) return false;
        return Number(n.maxLength || 0) === 1 || String(n.autocomplete || '').toLowerCase() === 'one-time-code';
    });
    value = otpBoxes.map(n => String(n.value || '').trim()).join('');
    if (!value || value.length < 6) return false;
}
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button')).filter(n =>
    isVisible(n) && !n.disabled && n.getAttribute('aria-disabled') !== 'true'
);
const confirmButton = buttons.find(n => {
    const raw = (n.innerText || n.textContent || '');
    const compact = raw.replace(/\s+/g, '');
    const lower = raw.replace(/\s+/g, ' ').trim().toLowerCase();
    return compact === '确认邮箱' || compact.includes('确认邮箱')
        || compact === '继续' || compact.includes('继续')
        || compact === '下一步' || compact.includes('下一步')
        || lower === 'confirm email' || lower.includes('confirm email')
        || lower === 'continue' || lower === 'next' || lower === 'verify';
});
if (!confirmButton) return 'no-button';
confirmButton.focus();
confirmButton.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true}));
confirmButton.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, cancelable: true}));
confirmButton.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
confirmButton.click();
return 'clicked';
                    """)
                except PageDisconnectedError:
                    # Page navigated after click, like original
                    self._refresh_active_page()
                    if self._wait_for_stable_profile_form(timeout=3):
                        logger.info("Profile form detected after confirm click")
                        return
                    clicked = 'disconnected'
                except Exception as e:
                    logger.warning(f"Confirm button error: {e}")
                    clicked = 'error'

                if clicked == 'clicked':
                    logger.info(f"Filled code and clicked confirm: {code}")
                    # Wait for page transition (up to 15s)
                    old_url = self.browser.page.url if self.browser.page else ''
                    for _ in range(30):
                        time.sleep(0.5)
                        try:
                            if (
                                self._has_profile_form()
                                and self._wait_for_stable_profile_form(timeout=3)
                            ):
                                logger.info("Profile page ready after code confirmation")
                                return
                            new_url = self.browser.page.url if self.browser.page else ''
                            if new_url != old_url:
                                logger.info(f"Page navigated: {new_url}")
                                return
                        except Exception:
                            pass

                    # Page didn't change, try Enter key
                    logger.info("Page unchanged, trying Enter key...")
                    try:
                        self.browser.run_js(r"""
const otpInput = document.querySelector('input[data-input-otp="true"]') ||
    Array.from(document.querySelectorAll('input')).find(n => n.maxLength > 5 && n.type !== 'password');
if (otpInput) {
    otpInput.focus();
    otpInput.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true}));
    otpInput.dispatchEvent(new KeyboardEvent('keyup', {key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true}));
}
const form = document.querySelector('form');
if (form) form.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}));
                        """)
                    except Exception:
                        pass
                    time.sleep(3)

                    # Still no change, click button again
                    if not self._has_profile_form():
                        logger.info("Enter had no effect, clicking button again...")
                        try:
                            self.browser.run_js(r"""
const btns = Array.from(document.querySelectorAll('button')).filter(n => {
    const raw = (n.innerText || '');
    const t = raw.replace(/\s+/g, '');
    const lower = raw.replace(/\s+/g, ' ').trim().toLowerCase();
    return t.includes('确认邮箱') || t.includes('继续') || t.includes('下一步')
        || lower.includes('confirm email') || lower === 'continue' || lower === 'next' || lower === 'verify';
});
if (btns.length) {
    btns[0].scrollIntoView({block: 'center'});
    btns[0].focus();
    btns[0].click();
}
                            """)
                        except Exception:
                            pass
                        time.sleep(3)

                    if self._wait_for_stable_profile_form(timeout=3):
                        logger.info("Profile page ready after retry")
                        return

                    raise Exception("确认邮箱失败，页面无响应，需要关闭浏览器重试")

                if clicked == 'no-button':
                    if self._wait_for_stable_profile_form(timeout=3):
                        logger.info("Code filled and profile form became stable")
                        return
                    time.sleep(0.5)
                    continue

                if clicked == 'disconnected':
                    time.sleep(1)
                    continue

                if clicked == 'error':
                    time.sleep(1)
                    continue

            time.sleep(0.5)

        # Timeout: dump DOM for debugging
        try:
            snapshot = self.browser.run_js(r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
const inputs = Array.from(document.querySelectorAll('input')).filter(isVisible).map(n => ({
    type: n.type || '', name: n.name || '', maxLength: Number(n.maxLength || 0), value: String(n.value || ''),
}));
const buttons = Array.from(document.querySelectorAll('button')).filter(isVisible).map(n => ({
    text: String(n.innerText || n.textContent || '').replace(/\s+/g, ' ').trim(), disabled: !!n.disabled,
}));
return { url: location.href, inputs, buttons };
            """)
            logger.error(f"Code confirm timeout, DOM snapshot: {snapshot}")
        except Exception:
            pass
        raise Exception("Failed to fill/confirm verification code")

    def _has_profile_form(self):
        """Check if we're on the final profile registration page."""
        try:
            self._refresh_active_page()
            return bool(self.browser.run_js(
                """
const givenInput = document.querySelector('input[data-testid="givenName"], input[name="givenName"], input[autocomplete="given-name"]');
const familyInput = document.querySelector('input[data-testid="familyName"], input[name="familyName"], input[autocomplete="family-name"]');
const passwordInput = document.querySelector('input[data-testid="password"], input[name="password"], input[type="password"]');
const codeInput = Array.from(document.querySelectorAll('input')).find((node) => {
  const style = getComputedStyle(node);
  const rect = node.getBoundingClientRect();
  if (style.display === 'none' || style.visibility === 'hidden'
      || rect.width <= 0 || rect.height <= 0) return false;
  const meta = [node.name, node.id, node.placeholder, node.autocomplete,
    node.getAttribute('data-testid'), node.getAttribute('aria-label')]
    .join(' ').toLowerCase();
  return meta.includes('code') || meta.includes('otp')
    || meta.includes('verification') || Number(node.maxLength || 0) === 6;
});
return !!(givenInput && familyInput && passwordInput && !codeInput);
                """
            ))
        except Exception:
            return False

    def _wait_for_stable_profile_form(self, timeout=3, consecutive=3):
        """Require the profile form to remain visible before leaving OTP flow."""
        deadline = time.time() + timeout
        stable = 0
        while time.time() < deadline:
            if self._has_profile_form():
                stable += 1
                if stable >= consecutive:
                    return True
            else:
                stable = 0
            time.sleep(0.5)
        return False

    def _warn_profile_probe(self, category, detail, interval=5):
        """Rate-limit repeated page/tab probe warnings during unstable navigation."""
        now = time.time()
        cache = getattr(self, '_profile_probe_warning_cache', {})
        previous = cache.get(category, {})
        if (
            previous.get('detail') == detail
            and now - previous.get('time', 0) < interval
        ):
            return
        logger.warning('%s: %s', category, detail)
        cache[category] = {'detail': detail, 'time': now}
        self._profile_probe_warning_cache = cache

    def _browser_tabs(self):
        """Return every live browser tab without assuming the last one is active."""
        try:
            chromium = getattr(self.browser, 'browser', None)
            if chromium is not None:
                tabs = list(chromium.get_tabs() or [])
                if tabs:
                    return tabs
        except Exception as exc:
            self._warn_profile_probe(
                'Failed to enumerate browser tabs',
                f'{type(exc).__name__}: {exc}',
            )
        try:
            page = getattr(self.browser, '_page', None)
            if page is not None:
                return [page]
        except Exception:
            pass
        return []

    @staticmethod
    def _profile_completion_url(url):
        """Return whether an xAI/Grok URL is beyond the sign-up page."""
        value = str(url or '').strip()
        if not value:
            return False
        lower = value.lower()
        if 'sign-up' in lower or 'signup' in lower:
            return False
        try:
            parsed = urlparse(value)
            host = (parsed.hostname or '').lower()
        except Exception:
            return False
        return bool(
            parsed.scheme in ('http', 'https')
            and (
                host == 'grok.com'
                or host.endswith('.grok.com')
                or host == 'x.ai'
                or host.endswith('.x.ai')
            )
        )

    def _select_browser_tab(self, tab):
        try:
            self.browser._page = tab
        except Exception:
            pass

    @staticmethod
    def _tab_id(tab, index):
        for name in ('tab_id', '_tab_id', 'id'):
            try:
                value = getattr(tab, name, None)
                if value:
                    return str(value)
            except Exception:
                pass
        return f'tab-{index}'

    def _sanitize_profile_diagnostic(self, value):
        if isinstance(value, dict):
            return {
                str(key): self._sanitize_profile_diagnostic(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self._sanitize_profile_diagnostic(item) for item in value]
        if isinstance(value, str):
            text = self._redact_network_text(value, 1200)
            return re.sub(
                r'(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b',
                '<redacted-email>',
                text,
            )
        return value

    def _collect_profile_tab_diagnostics(self):
        """Capture URL/title and a secret-safe DOM summary for every browser tab."""
        tabs = self._browser_tabs()
        current = getattr(self.browser, '_page', None)
        entries = []
        for index, tab in enumerate(tabs, start=1):
            entry = {
                'index': index,
                'tab_id': self._tab_id(tab, index),
                'is_selected': tab is current,
                'url': '',
                'title': '',
                'dom': {},
                'capture_error': '',
            }
            try:
                entry['url'] = str(getattr(tab, 'url', '') or '')
            except Exception as exc:
                entry['capture_error'] = f'url: {type(exc).__name__}: {exc}'
            try:
                entry['title'] = str(getattr(tab, 'title', '') or '')
            except Exception as exc:
                prior = entry['capture_error']
                entry['capture_error'] = f'{prior}; ' if prior else ''
                entry['capture_error'] += f'title: {type(exc).__name__}: {exc}'
            try:
                entry['dom'] = tab.run_js(r"""
function isVisible(node) {
  if (!node) return false;
  const style = window.getComputedStyle(node);
  const rect = node.getBoundingClientRect();
  return style.display !== 'none' && style.visibility !== 'hidden'
    && style.opacity !== '0' && rect.width > 0 && rect.height > 0;
}
const inputs = Array.from(document.querySelectorAll('input')).slice(0, 30).map(n => ({
  type: String(n.type || ''),
  name: String(n.name || ''),
  id: String(n.id || ''),
  testId: String(n.getAttribute('data-testid') || ''),
  autocomplete: String(n.autocomplete || ''),
  visible: isVisible(n),
  disabled: !!n.disabled,
  readOnly: !!n.readOnly,
  valueLength: String(n.value || '').length,
}));
const buttons = Array.from(document.querySelectorAll('button, [role="button"], input[type="submit"]'))
  .slice(0, 30).map(n => ({
    text: String(n.innerText || n.textContent || n.value || '').replace(/\s+/g, ' ').trim().slice(0, 120),
    type: String(n.type || ''),
    visible: isVisible(n),
    disabled: !!n.disabled,
    ariaDisabled: String(n.getAttribute('aria-disabled') || ''),
  }));
const notices = Array.from(document.querySelectorAll(
  '[role="alert"], [data-testid="error-message"], h1, h2, h3'
)).filter(isVisible).slice(0, 20).map(n =>
  String(n.innerText || n.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 240)
).filter(Boolean);
const cf = document.querySelector('input[name="cf-turnstile-response"]');
return {
  href: String(location.href || ''),
  readyState: String(document.readyState || ''),
  bodyTextLength: String(document.body?.innerText || '').length,
  hasGivenName: !!document.querySelector('input[data-testid="givenName"], input[name="givenName"], input[autocomplete="given-name"]'),
  hasFamilyName: !!document.querySelector('input[data-testid="familyName"], input[name="familyName"], input[autocomplete="family-name"]'),
  hasPassword: !!document.querySelector('input[data-testid="password"], input[name="password"], input[type="password"]'),
  cfLength: cf ? String(cf.value || '').length : -1,
  inputs,
  buttons,
  notices,
};
                """) or {}
            except Exception as exc:
                prior = entry['capture_error']
                entry['capture_error'] = f'{prior}; ' if prior else ''
                entry['capture_error'] += f'dom: {type(exc).__name__}: {exc}'
            entries.append(self._sanitize_profile_diagnostic(entry))
        return tabs, {
            'captured_at': datetime.now(timezone.utc).isoformat(),
            'tab_count': len(tabs),
            'tabs': entries,
        }

    @staticmethod
    def _tab_log_summary(details):
        result = []
        for item in (details or {}).get('tabs', []):
            dom = item.get('dom') or {}
            result.append({
                'index': item.get('index'),
                'selected': item.get('is_selected'),
                'url': item.get('url') or dom.get('href'),
                'ready': dom.get('readyState'),
                'profile': bool(
                    dom.get('hasGivenName')
                    and dom.get('hasFamilyName')
                    and dom.get('hasPassword')
                ),
                'cfLen': dom.get('cfLength'),
                'error': item.get('capture_error'),
            })
        return result

    def _save_profile_diagnostics(self, stage, snapshot=None, reason='',
                                  details=None):
        try:
            tabs, tab_details = self._collect_profile_tab_diagnostics()
            merged_details = dict(details or {})
            merged_details['browser'] = tab_details
            result = save_profile_diagnostics(
                self.browser.page,
                stage,
                snapshot=snapshot,
                reason=reason,
                details=merged_details,
                pages=tabs,
            )
            logger.warning(
                'Profile diagnostics saved: json=%s screenshots=%s',
                result.get('json') or 'none',
                result.get('screenshots') or 'none',
            )
            return result
        except Exception as exc:
            logger.warning('Failed to save profile diagnostics: %s', exc)
            return {}

    @staticmethod
    def _redact_network_text(value, limit=500):
        """Keep network diagnostics useful without persisting secrets."""
        text = str(value or '')
        text = re.sub(r'(?i)(password|passwd|token|code|secret)\s*[=:]\s*[^,;&\s]+',
                      r'\1=<redacted>', text)
        text = re.sub(r'(?i)Bearer\s+[A-Za-z0-9._~+/=-]+',
                      'Bearer <redacted>', text)
        return text[:limit]

    def _start_profile_network_capture(self):
        """Listen for POST responses generated by the profile submit."""
        try:
            listener = getattr(self.browser.page, 'listen', None)
            if listener is None:
                return None
            try:
                listener.stop()
            except Exception:
                pass
            listener.clear()
            listener.start(method=('POST',), res_type=True)
            return listener
        except Exception as exc:
            logger.debug('Profile network capture unavailable: %s', exc)
            return None

    def _collect_profile_network_capture(self, listener, timeout=8):
        """Collect POST response metadata after profile submit."""
        if listener is None:
            return []
        packets = []
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline and len(packets) < 12:
                remaining = max(0.1, min(0.75, deadline - time.monotonic()))
                packet = listener.wait(timeout=remaining, raise_err=False)
                if not packet:
                    continue
                raw_request = getattr(packet, '_raw_request', {}) or {}
                raw_response = getattr(packet, '_raw_response', {}) or {}
                request = raw_request.get('request', {}) or {}
                response = getattr(packet, 'response', None)
                body = ''
                try:
                    body = response.body if response is not None else ''
                except Exception:
                    body = ''
                packets.append({
                    'method': str(request.get('method') or 'POST'),
                    'url': self._redact_network_text(raw_response.get('url') or request.get('url'), 300),
                    'status': raw_response.get('status'),
                    'status_text': self._redact_network_text(raw_response.get('statusText'), 100),
                    'resource_type': str(raw_response.get('mimeType') or ''),
                    'body': self._redact_network_text(body),
                    'failed': bool(getattr(packet, 'is_failed', False)),
                })
        except Exception as exc:
            logger.debug('Profile network capture failed: %s', exc)
        finally:
            try:
                listener.stop()
            except Exception:
                pass
        return packets

    def _log_profile_network_capture(self, packets, alias_email=''):
        if not packets:
            logger.warning(
                'Profile network diagnostic: no POST response captured for %s',
                alias_email,
            )
            return
        for packet in packets:
            logger.info(
                'Profile network diagnostic: email=%s method=%s status=%s '
                'failed=%s url=%s body=%s',
                alias_email,
                packet.get('method'),
                packet.get('status'),
                packet.get('failed'),
                packet.get('url'),
                packet.get('body'),
            )

    def _profile_completion_reason(self):
        """Detect successful registration across every live browser tab."""
        tabs = self._browser_tabs()
        probe_errors = []
        for index, tab in enumerate(tabs, start=1):
            sso_value = ''
            try:
                cookies = tab.cookies(all_domains=True, all_info=True) or []
                for item in cookies:
                    name = (
                        str(item.get('name', '')).strip()
                        if isinstance(item, dict)
                        else str(getattr(item, 'name', '')).strip()
                    )
                    value = (
                        str(item.get('value', '')).strip()
                        if isinstance(item, dict)
                        else str(getattr(item, 'value', '')).strip()
                    )
                    if name == 'sso' and value:
                        sso_value = value
                        break
            except Exception as exc:
                probe_errors.append(
                    f'tab-{index} cookies: {type(exc).__name__}: {exc}'
                )
            if not sso_value:
                try:
                    js_cookies = str(tab.run_js('return document.cookie') or '')
                    for pair in js_cookies.split(';'):
                        if '=' not in pair:
                            continue
                        name, value = pair.strip().split('=', 1)
                        if name.strip() == 'sso' and value.strip():
                            sso_value = value.strip()
                            break
                except Exception as exc:
                    probe_errors.append(
                        f'tab-{index} document.cookie: '
                        f'{type(exc).__name__}: {exc}'
                    )
            if sso_value:
                self._select_browser_tab(tab)
                return f'sso-cookie:{len(sso_value)}:tab-{index}'
        for index, tab in enumerate(tabs, start=1):
            try:
                url = str(tab.url or '')
                if self._profile_completion_url(url):
                    self._select_browser_tab(tab)
                    return f'navigated-tab-{index}:{url[:160]}'
            except Exception as exc:
                probe_errors.append(
                    f'tab-{index} url: {type(exc).__name__}: {exc}'
                )
        if probe_errors:
            self._warn_profile_probe(
                'Profile completion tab probe errors',
                '; '.join(probe_errors)[:1200],
            )
        return ''

    def _dismiss_cookie_banner(self):
        """Dismiss OneTrust/cookie consent banners that block form submit."""
        if self._cookie_banner_dismissed:
            return False
        try:
            result = self.browser.run_js(r"""
// Prefer known OneTrust / common consent selectors first
const selectors = [
  '#onetrust-accept-btn-handler',
  '#accept-recommended-btn-handler',
  'button#onetrust-accept-btn-handler',
  '.onetrust-accept-btn-handler',
  '#onetrust-pc-btn-handler',
  'button[id*="accept"]',
  'button[class*="accept-all"]',
  'button[class*="acceptAll"]',
];
for (const sel of selectors) {
  const el = document.querySelector(sel);
  if (el) {
    try { el.click(); return 'clicked-sel:' + sel; } catch (e) {}
  }
}
const labels = ['全部允许', '全部接受', '接受全部', '允许全部', 'Accept All', 'Allow All', 'Allow all', 'I Accept', 'Agree', 'Accept'];
const buttons = Array.from(document.querySelectorAll('button, [role="button"], a, input[type="button"], input[type="submit"]'));
const matches = buttons.map(n => {
  const t = (n.innerText || n.textContent || n.value || n.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
  return { t, d: !!n.disabled, match: labels.some(l => t === l || t.includes(l)) };
}).filter(x => x.match);
const btn = buttons.find(n => {
  if (!n || n.disabled) return false;
  const t = (n.innerText || n.textContent || n.value || n.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
  return labels.some(l => t === l || t.includes(l));
});
if (!btn) {
  return matches.length ? ('found-but-not-clicked:' + JSON.stringify(matches.slice(0, 5))) : 'none';
}
try {
  btn.scrollIntoView({ block: 'center' });
  btn.focus();
  btn.click();
  btn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
} catch (e) {
  return 'error:' + String(e);
}
return 'clicked-text:' + (btn.innerText || btn.value || '').trim().slice(0, 40);
            """)
            if result and str(result).startswith('clicked'):
                self._cookie_banner_dismissed = True
                logger.info(f"Cookie banner dismissed: {result}")
                time.sleep(1.0)
                return True
            # DrissionPage fallback by visible text
            for label in ('全部允许', 'Accept All', 'Allow All'):
                try:
                    el = self.browser.page.ele(f'tag:button@@text()={label}', timeout=0.5)
                    if el:
                        el.click()
                        self._cookie_banner_dismissed = True
                        logger.info(f"Cookie banner dismissed via DP: {label}")
                        time.sleep(1.0)
                        return True
                except Exception:
                    pass
            if result and result != 'none':
                logger.info(f"Cookie banner result: {result}")
            else:
                logger.debug(f"Cookie banner result: {result}")
        except Exception as e:
            logger.warning(f"Cookie banner check: {e}")
        return False

    def _fill_profile(self, password, settings, alias_email='', timeout=120):
        """Fill profile form (name + password) and submit with turnstile handling."""
        if settings.get('random_name_enabled', 'true') == 'true':
            first, last = self._generate_random_name()
        else:
            first, last = 'Test', 'User'

        deadline = time.time() + timeout
        last_transient_error = ''
        last_transient_error_log = 0.0
        last_not_ready_log = 0.0
        self._dismiss_cookie_banner()

        while time.time() < deadline:
            try:
                completion = self._profile_completion_reason()
                if completion:
                    logger.info(
                        'Profile registration completed before explicit submit: %s',
                        completion,
                    )
                    return
                self._dismiss_cookie_banner()
                existing_account = self.browser.run_js(r"""
const body = String(document.body?.innerText || '').replace(/\s+/g, ' ').trim();
const lower = body.toLowerCase();
if (lower.includes('existing account found')
    || lower.includes('an account already exists which is associated with this email address')) {
  return body.slice(0, 500);
}
return '';
                """)
                if existing_account:
                    raise ExistingAccountError(
                        '注册邮箱已存在：xAI reports Existing account found'
                    )
                filled = self.browser.run_js(
                    """
const givenName = arguments[0];
const familyName = arguments[1];
const password = arguments[2];
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function pickInput(selector) {
    return Array.from(document.querySelectorAll(selector)).find(n => isVisible(n) && !n.disabled && !n.readOnly) || null;
}
function setInputValue(input, value) {
    if (!input) return false;
    input.focus(); input.click();
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
    const tracker = input._valueTracker;
    if (tracker) tracker.setValue('');
    if (setter) { setter.call(input, ''); setter.call(input, value); }
    else { input.value = ''; input.value = value; }
    input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, cancelable: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new InputEvent('input', { bubbles: true, cancelable: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    input.dispatchEvent(new Event('blur', { bubbles: true }));
    return String(input.value || '') === String(value || '');
}

const givenInput = pickInput('input[data-testid="givenName"], input[name="givenName"], input[autocomplete="given-name"]');
const familyInput = pickInput('input[data-testid="familyName"], input[name="familyName"], input[autocomplete="family-name"]');
const passwordInput = pickInput('input[data-testid="password"], input[name="password"], input[type="password"]');
if (!givenInput || !familyInput || !passwordInput) return 'not-ready';

const givenOk = setInputValue(givenInput, givenName);
const familyOk = setInputValue(familyInput, familyName);
const passwordOk = setInputValue(passwordInput, password);
if (!givenOk || !familyOk || !passwordOk) return 'filled-failed';

return [
    String(givenInput.value || '').trim() === String(givenName || '').trim(),
    String(familyInput.value || '').trim() === String(familyName || '').trim(),
    String(passwordInput.value || '') === String(password || ''),
].every(Boolean) ? 'filled' : 'verify-failed';
                    """,
                    first, last, password,
                )

                if filled == 'not-ready':
                    now = time.time()
                    if now - last_not_ready_log >= 10:
                        last_not_ready_log = now
                        _, tab_details = self._collect_profile_tab_diagnostics()
                        logger.info(
                            'Profile form not ready; tab state=%s',
                            self._tab_log_summary(tab_details),
                        )
                    time.sleep(0.5)
                    continue

                if filled == 'filled':
                    # Verify values
                    values_ok = self.browser.run_js(
                        """
const expectedGiven = arguments[0];
const expectedFamily = arguments[1];
const expectedPassword = arguments[2];
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function pickInput(selector) {
    return Array.from(document.querySelectorAll(selector)).find(n => isVisible(n) && !n.disabled && !n.readOnly) || null;
}
const givenInput = pickInput('input[data-testid="givenName"], input[name="givenName"], input[autocomplete="given-name"]');
const familyInput = pickInput('input[data-testid="familyName"], input[name="familyName"], input[autocomplete="family-name"]');
const passwordInput = pickInput('input[data-testid="password"], input[name="password"], input[type="password"]');
if (!givenInput || !familyInput || !passwordInput) return false;
return String(givenInput.value || '').trim() === String(expectedGiven || '').trim()
    && String(familyInput.value || '').trim() === String(expectedFamily || '').trim()
    && String(passwordInput.value || '') === String(expectedPassword || '');
                        """,
                        first, last, password,
                    )
                    if not values_ok:
                        logger.debug("Profile field values mismatch, retrying...")
                        time.sleep(0.5)
                        continue

                    # xAI may submit automatically as soon as Turnstile resolves,
                    # so listen before solving rather than only before button click.
                    profile_listener = self._start_profile_network_capture()

                    # Check turnstile BEFORE clicking submit (like original script)
                    turnstile_state = self.browser.run_js("""
const ci = document.querySelector('input[name="cf-turnstile-response"]');
if (!ci) return 'not-found';
const v = String(ci.value || '').trim();
return v ? 'ready' : 'pending';
                    """)

                    if turnstile_state == 'pending':
                        logger.info("Turnstile pending on profile page, solving...")
                        self._solve_turnstile()
                        # Sync token to input if we got it
                        turnstile_token = self.browser.run_js("""
try { return turnstile.getResponse() } catch(e) { return null }
                        """)
                        if turnstile_token and len(str(turnstile_token)) > 50:
                            self.browser.run_js("""
const token = arguments[0];
const ci = document.querySelector('input[name="cf-turnstile-response"]');
if (ci) {
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
    if (setter) setter.call(ci, token); else ci.value = token;
    ci.dispatchEvent(new Event('input', { bubbles: true }));
    ci.dispatchEvent(new Event('change', { bubbles: true }));
}
                            """, turnstile_token)
                            logger.info(f"Turnstile token synced to form (len={len(str(turnstile_token))})")
                        else:
                            logger.warning(f"Turnstile token missing/short: {str(turnstile_token)[:40] if turnstile_token else None}")
                        # Give React form a moment to enable submit after CF callback
                        time.sleep(1.5)

                    # Some xAI variants auto-submit after the Turnstile callback and
                    # remove the profile form without exposing a final button.
                    auto_deadline = time.time() + 12
                    while time.time() < auto_deadline:
                        completion = self._profile_completion_reason()
                        if completion:
                            network_packets = self._collect_profile_network_capture(
                                profile_listener,
                                timeout=1,
                            )
                            self._log_profile_network_capture(
                                network_packets,
                                alias_email,
                            )
                            logger.info(
                                'Profile completed automatically after Turnstile: %s',
                                completion,
                            )
                            logger.info(f"Filled profile: {first} {last}")
                            return
                        time.sleep(0.5)

                    self._dismiss_cookie_banner()

                    # Wait until 完成注册 is enabled (do NOT click a disabled button)
                    enabled_btn = False
                    for _wait in range(20):
                        state = self.browser.run_js(r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function isSubmitLabel(text) {
    const raw = String(text || '');
    const t = raw.replace(/\s+/g, '').toLowerCase();
    const s = raw.replace(/\s+/g, ' ').trim().toLowerCase();
    return t.includes('完成注册') || t === 'signup' || t.includes('createaccount')
        || t.includes('sign-up') || t.includes('completesignup')
        || s === 'complete sign up' || s.includes('complete sign up')
        || s === 'create account' || s.includes('create account')
        || (t.includes('注册') && t.includes('完成'));
}
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button'));
const candidates = buttons.filter(n => isVisible(n) && isSubmitLabel(n.innerText || n.textContent || ''));
if (!candidates.length) return 'missing';
const enabled = candidates.find(n => !n.disabled && n.getAttribute('aria-disabled') !== 'true');
return enabled ? 'enabled' : 'disabled';
                        """)
                        if state == 'enabled':
                            enabled_btn = True
                            break
                        if _wait in (0, 5, 10, 15):
                            logger.info(f"Waiting for submit button enabled... state={state}")
                            self._dismiss_cookie_banner()
                            # Re-try turnstile interaction if still disabled
                            try:
                                self._solve_turnstile()
                            except Exception:
                                pass
                        time.sleep(1)

                    if not enabled_btn:
                        logger.warning("Submit button still disabled after wait; refusing to force-enable")

                    # Click submit only when the page has enabled it. Never mutate
                    # disabled state: React/Turnstile uses that state as part of
                    # its server-side submission contract.
                    clicked = self.browser.run_js(r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function isSubmitLabel(text) {
    const raw = String(text || '');
    const t = raw.replace(/\s+/g, '').toLowerCase();
    const s = raw.replace(/\s+/g, ' ').trim().toLowerCase();
    return t === '完成注册' || t.includes('完成注册')
        || t === 'signup' || t === 'sign-up' || t === 'createaccount'
        || t.includes('createaccount') || t.includes('sign-up')
        || t.includes('completesignup')
        || s === 'complete sign up' || s.includes('complete sign up')
        || s === 'create account' || s.includes('create account')
        || (t.includes('注册') && t.includes('完成'));
}
const ci = document.querySelector('input[name="cf-turnstile-response"]');
const hasCF = !!(ci && String(ci.value || '').trim().length > 50);
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button'));
const candidates = buttons.filter(n => isVisible(n) && isSubmitLabel(n.innerText || n.textContent || ''));
let btn = candidates.find(n => !n.disabled && n.getAttribute('aria-disabled') !== 'true') || null;
if (!btn) {
    const labels = buttons.filter(isVisible).map(n => ({
        t: (n.innerText||'').trim().slice(0,40),
        d: !!n.disabled
    }));
    return 'no-enabled-button:' + JSON.stringify(labels.slice(0, 8));
}
btn.scrollIntoView({ block: 'center', inline: 'center' });
btn.focus();
// One native click is enough for React's delegated handler. Dispatching synthetic
// clicks and requestSubmit as well can send the same registration several times.
btn.click();
return hasCF ? 'clicked-cf' : 'clicked-no-cf';
                    """)
                    network_packets = self._collect_profile_network_capture(
                        profile_listener,
                        timeout=8,
                    )
                    self._log_profile_network_capture(network_packets, alias_email)
                    logger.info(f"Profile submit result: {clicked}")
                    if not clicked or str(clicked).startswith(('no-enabled-button', 'no-button', 'disabled')):
                        raise Exception(
                            f"注册提交按钮未启用（Turnstile/页面状态未就绪）: {clicked}"
                        )
                    if clicked == 'clicked-no-cf':
                        raise Exception(
                            "注册提交缺少有效 Turnstile token，已禁止提交"
                        )

                    logger.info(f"Filled profile: {first} {last}")
                    submit_stage = ProfileSubmitStage.SUBMITTED
                    last_snapshot = ProfileSubmitSnapshot()

                    def _submit_ui_state():
                        """Detect post-submit loading / success / error on the profile page."""
                        return self.browser.run_js(r"""
function isVisible(node) {
  if (!node) return false;
  const style = window.getComputedStyle(node);
  if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
  const rect = node.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}
const buttons = Array.from(document.querySelectorAll('button'));
const primary = buttons.find(n => {
  if (!isVisible(n)) return false;
  const raw = (n.innerText || n.textContent || '');
  const t = raw.replace(/\s+/g, '').toLowerCase();
  const s = raw.replace(/\s+/g, ' ').trim().toLowerCase();
  return t.includes('完成注册') || t === 'signup' || t.includes('createaccount')
    || t.includes('sign-up') || t.includes('completesignup')
    || s.includes('complete sign up') || s.includes('create account');
}) || buttons.find(n => isVisible(n) && n.type === 'submit') || null;

const hasSpinner = !!(primary && primary.querySelector(
  'svg.animate-spin, [class*="spinner"], [class*="loading"], .animate-spin'
));
const ariaBusy = !!(primary && (primary.getAttribute('aria-busy') === 'true' || primary.dataset.loading === 'true'));
const primaryDisabled = !!(primary && (primary.disabled || primary.getAttribute('aria-disabled') === 'true'));
const primaryText = primary ? (primary.innerText || primary.textContent || '').replace(/\s+/g, ' ').trim() : '';
// This probe runs only after clicking submit. A disabled primary button therefore means
// the form still owns an in-flight submission, even if the original label remains visible.
const loading = hasSpinner || ariaBusy || primaryDisabled;
const err = document.querySelector('[role="alert"], .error, [data-testid="error-message"], .text-red-500, .text-error');
const errText = err ? (err.innerText || err.textContent || '').trim().substring(0, 200) : '';
const cf = document.querySelector('input[name="cf-turnstile-response"]');
return {
  loading: !!loading,
  primaryDisabled: !!primaryDisabled,
  primaryText: primaryText.slice(0, 40),
  errText: errText,
  url: location.href,
  cfLen: cf ? String(cf.value || '').length : -1,
  turnstileOk: !!(document.body && document.body.innerText && document.body.innerText.indexOf('成功') >= 0),
};
                        """)

                    # Wait much longer after submit. If the button is spinning ("loading"),
                    # the request is in-flight — do NOT reload (that aborts registration).
                    wait_started = time.time()
                    wait_deadline = wait_started + 90
                    loading_deadline_extended = False
                    saw_loading = False
                    last_state_log = 0
                    while time.time() < wait_deadline:
                        if self.state.should_stop():
                            submit_stage = ProfileSubmitStage.STOPPED
                            self._save_profile_diagnostics(
                                submit_stage, last_snapshot,
                                'Registration stop requested during profile submit wait',
                            )
                            raise Exception("Registration stop requested during submit wait")
                        try:
                            url = self.browser.page.url
                            if 'sign-up' not in url and 'signup' not in url:
                                submit_stage = ProfileSubmitStage.SUCCEEDED
                                logger.info(f"Registration submitted, page: {url}")
                                return
                        except Exception:
                            pass
                        try:
                            sso_early = self._check_sso_cookie()
                            if sso_early:
                                submit_stage = ProfileSubmitStage.SUCCEEDED
                                logger.info(f"SSO cookie detected early ({len(sso_early)} chars)")
                                return
                        except Exception:
                            pass

                        try:
                            ui = _submit_ui_state() or {}
                        except Exception:
                            ui = {}
                        last_snapshot = ProfileSubmitSnapshot.from_mapping(ui)
                        submit_stage = classify_profile_submit(last_snapshot)
                        in_flight = last_snapshot.in_flight
                        if in_flight:
                            saw_loading = True
                            if not loading_deadline_extended:
                                wait_deadline = wait_started + 180
                                loading_deadline_extended = True
                                logger.info("Submit is loading; extending wait to 180s and keeping page untouched")
                        if last_snapshot.error_text:
                            submit_stage = ProfileSubmitStage.FAILED
                            self._save_profile_diagnostics(
                                submit_stage, last_snapshot,
                                last_snapshot.error_text,
                            )
                            raise Exception(f"注册提交失败: {last_snapshot.error_text}")

                        now = time.time()
                        if now - last_state_log >= 5:
                            last_state_log = now
                            logger.info(
                                f"Post-submit wait: loading={in_flight} "
                                f"stage={submit_stage.value} "
                                f"btn='{last_snapshot.primary_text}' disabled={last_snapshot.primary_disabled} "
                                f"cfLen={last_snapshot.cf_length} turnstileOk={last_snapshot.turnstile_ok}"
                            )

                        # If we previously saw loading and it finished, give a short grace period then exit loop
                        if saw_loading and not in_flight and now - wait_started >= 20:
                            # loading ended without navigation — small extra wait for redirect/cookie
                            time.sleep(3)
                            try:
                                url = self.browser.page.url
                                if 'sign-up' not in url and 'signup' not in url:
                                    submit_stage = ProfileSubmitStage.SUCCEEDED
                                    logger.info(f"Registration submitted after loading, page: {url}")
                                    return
                            except Exception:
                                pass
                            try:
                                sso_early = self._check_sso_cookie()
                                if sso_early:
                                    submit_stage = ProfileSubmitStage.SUCCEEDED
                                    logger.info(f"SSO cookie after loading end ({len(sso_early)} chars)")
                                    return
                            except Exception:
                                pass
                            break

                        time.sleep(1)

                    # Diagnostics (never reload while still loading — that kills the API call)
                    try:
                        ui = _submit_ui_state() or {}
                        last_snapshot = ProfileSubmitSnapshot.from_mapping(ui)
                        logger.warning(
                            'Submit diagnostics: %s', last_snapshot,
                        )
                    except Exception as e:
                        ui = {}
                        last_snapshot = ProfileSubmitSnapshot()
                        logger.warning(f"Submit diagnostics failed: {e}")

                    submit_stage = classify_profile_submit(
                        last_snapshot, timed_out=True,
                    )
                    if submit_stage == ProfileSubmitStage.TIMED_OUT:
                        # Still spinning after the extended deadline — leave page alone and
                        # propagate the failure; never loop back into another click attempt.
                        logger.warning("Submit still loading after 180s; not reloading (would abort request)")
                        self._save_profile_diagnostics(
                            submit_stage, last_snapshot,
                            'Profile submit remained in flight past deadline',
                        )
                        raise Exception("注册提交超时（按钮一直 loading），需要重新尝试")

                    if submit_stage == ProfileSubmitStage.FAILED:
                        self._save_profile_diagnostics(
                            submit_stage, last_snapshot,
                            last_snapshot.error_text,
                        )
                        raise Exception(f"注册提交失败: {last_snapshot.error_text}")

                    # Only soft-refresh if request clearly finished and still stuck on sign-up
                    logger.info("No navigation after submit (not loading); soft check only, no forced reload")
                    try:
                        sso_after = self._check_sso_cookie()
                        if sso_after:
                            submit_stage = ProfileSubmitStage.SUCCEEDED
                            logger.info(f"SSO cookie found without navigation ({len(sso_after)} chars)")
                            return
                    except Exception:
                        pass

                    logger.warning("Submission appears to have failed (no navigation, no SSO, not loading).")
                    submit_stage = ProfileSubmitStage.STALLED
                    self._save_profile_diagnostics(
                        submit_stage, last_snapshot,
                        'No navigation, SSO cookie, loading state, or visible error',
                    )
                    raise Exception("注册提交未生效（页面未跳转且无SSO），需要重新尝试")

            except Exception as e:
                # Re-raise real submit failures instead of looping until timeout
                msg = str(e)
                if isinstance(e, ExistingAccountError):
                    raise
                if any(k in msg for k in (
                    '注册提交失败', '注册提交未生效', '注册提交超时',
                    '注册提交按钮未启用', '注册提交缺少有效', 'Turnstile',
                    '注册邮箱已存在',
                )):
                    raise
                completion = self._profile_completion_reason()
                if completion:
                    logger.info(
                        'Profile registration completed after transient page error: %s',
                        completion,
                    )
                    return
                last_transient_error = f'{type(e).__name__}: {msg or repr(e)}'
                now = time.time()
                if (
                    last_transient_error != getattr(
                        self, '_last_profile_transient_error', '',
                    )
                    or now - last_transient_error_log >= 5
                ):
                    self._last_profile_transient_error = last_transient_error
                    last_transient_error_log = now
                    logger.warning(
                        'Profile flow transient exception (retrying): %s',
                        last_transient_error,
                    )
                    logger.debug(
                        'Profile flow transient exception traceback',
                        exc_info=True,
                    )
            time.sleep(0.5)

        completion = self._profile_completion_reason()
        if completion:
            logger.info(
                'Profile registration completed at timeout boundary: %s',
                completion,
            )
            return
        _, tab_details = self._collect_profile_tab_diagnostics()
        logger.warning(
            'Profile final timeout tab state: %s',
            self._tab_log_summary(tab_details),
        )
        reason = 'Profile form or enabled submit button was not found before deadline'
        if last_transient_error:
            reason += f'; last transient error: {last_transient_error}'
        self._save_profile_diagnostics(
            ProfileSubmitStage.STALLED,
            ProfileSubmitSnapshot(),
            reason,
            details={'timeout_browser_snapshot': tab_details},
        )
        raise Exception("未找到最终注册表单或完成注册按钮")

    def _solve_turnstile(self):
        """Solve Turnstile challenge using JS interaction."""
        settings = self.db.get_settings()
        if settings.get('turnstile_auto', 'true') != 'true':
            logger.info("Turnstile set to manual, waiting for user...")
            time.sleep(30)
            return

        try:
            # Check if turnstile exists
            turnstile_state = self.browser.run_js("""
const ci = document.querySelector('input[name="cf-turnstile-response"]');
if (!ci) return 'not-found';
const v = String(ci.value || '').trim();
return v ? 'ready' : 'pending';
            """)

            if turnstile_state == 'not-found':
                logger.debug("No Turnstile detected")
                return

            if turnstile_state == 'ready':
                logger.info("Turnstile already solved")
                return

            logger.info("Turnstile detected, attempting solve...")
            # Try to interact with turnstile iframe
            for i in range(15):
                try:
                    turnstile_response = self.browser.run_js("try { return turnstile.getResponse() } catch(e) { return null }")
                    if turnstile_response:
                        logger.info("Turnstile solved via getResponse()")
                        # Sync token to input
                        self.browser.run_js("""
const token = arguments[0];
const ci = document.querySelector('input[name="cf-turnstile-response"]');
if (ci) {
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
    if (setter) setter.call(ci, token); else ci.value = token;
    ci.dispatchEvent(new Event('input', { bubbles: true }));
    ci.dispatchEvent(new Event('change', { bubbles: true }));
}
                        """, turnstile_response)
                        return

                    # Try clicking the turnstile box
                    self.browser.run_js("""
const box = document.querySelector('.cf-turnstile, .turnstile, [data-sitekey]');
if (box) {
    box.scrollIntoView({ behavior: 'smooth', block: 'center' });
    const rect = box.getBoundingClientRect();
    box.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: rect.left + rect.width/2, clientY: rect.top + rect.height/2 }));
}
                    """)

                    # Try interacting with iframe
                    try:
                        challenge_solution = self.browser.page.ele("@name=cf-turnstile-response", timeout=2)
                        challenge_wrapper = challenge_solution.parent()
                        challenge_iframe = challenge_wrapper.shadow_root.ele("tag:iframe", timeout=2)
                        challenge_iframe.run_js("""
window.dtp = 1;
Object.defineProperty(MouseEvent.prototype, 'screenX', { value: Math.floor(Math.random() * 400 + 800) });
Object.defineProperty(MouseEvent.prototype, 'screenY', { value: Math.floor(Math.random() * 200 + 400) });
                        """)
                        challenge_iframe_body = challenge_iframe.ele("tag:body", timeout=2).shadow_root
                        challenge_button = challenge_iframe_body.ele("tag:input", timeout=2)
                        challenge_button.click()
                    except Exception:
                        pass

                except Exception:
                    pass
                time.sleep(1)

            logger.warning("Turnstile auto-solve timeout, continuing anyway")

        except Exception as e:
            logger.warning(f"Turnstile handling: {e}")

    def _click_oauth_authorize(self):
        """If on OAuth authorize page, click the Authorize/Allow button."""
        try:
            url = self.browser.page.url if self.browser.page else ''
            if 'oauth2/authorize' not in url and 'authorize' not in url:
                return False

            logger.info(f"OAuth authorize page detected: {url[:80]}..., clicking authorize...")
            clicked = self.browser.run_js(r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
const buttons = Array.from(document.querySelectorAll('button, input[type="submit"], [role="button"]'));
const target = buttons.find(n => {
    if (!isVisible(n) || n.disabled) return false;
    const text = (n.innerText || n.value || n.textContent || '').replace(/\s+/g, '').toLowerCase();
    return text === 'authorize' || text === 'allow' || text.includes('授权') || text.includes('允许')
        || text === 'accept' || text.includes('同意');
});
if (target) {
    target.focus();
    target.click();
    return true;
}
// Fallback: try form submit
const form = document.querySelector('form');
if (form) {
    form.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}));
    return 'form-submitted';
}
return false;
            """)
            if clicked:
                logger.info("OAuth authorize button clicked")
                time.sleep(3)
                return True
            else:
                logger.warning("OAuth authorize page detected but no authorize button found")
                # Dump buttons for debugging
                try:
                    btn_info = self.browser.run_js("""
return Array.from(document.querySelectorAll('button, input[type="submit"], [role="button"]')).map(n => ({
    tag: n.tagName, text: (n.innerText || n.value || '').substring(0, 50),
    visible: n.offsetWidth > 0 && n.offsetHeight > 0, disabled: !!n.disabled
}));
                    """)
                    logger.info(f"Available buttons: {btn_info}")
                except Exception:
                    pass
                return False
        except Exception as e:
            logger.warning(f"OAuth authorize click failed: {e}")
            return False

    def _check_sso_cookie(self):
        """Quick one-shot check for SSO cookie (no polling). Returns cookie value or None."""
        try:
            self._refresh_active_page()
            if not self.browser.page:
                return None
            # Method 1: DrissionPage cookies API
            try:
                cookies = self.browser.page.cookies(all_domains=True, all_info=True) or []
                for item in cookies:
                    name = str(item.get('name', '')).strip() if isinstance(item, dict) else str(getattr(item, 'name', '')).strip()
                    value = str(item.get('value', '')).strip() if isinstance(item, dict) else str(getattr(item, 'value', '')).strip()
                    if name == 'sso' and value:
                        return value
            except Exception:
                pass
            # Method 2: JS document.cookie
            try:
                js_cookies = self.browser.run_js('return document.cookie') or ''
                for pair in js_cookies.split(';'):
                    pair = pair.strip()
                    if '=' in pair:
                        name, value = pair.split('=', 1)
                        if name.strip() == 'sso' and value.strip():
                            return value.strip()
            except Exception:
                pass
        except Exception:
            pass
        return None

    def _extract_sso(self, timeout=120):
        """Extract SSO cookie with polling (up to 120s), matching original script."""
        deadline = time.time() + timeout
        last_report = 0
        last_seen_names = set()
        last_authorize_attempt = 0
        stuck_on_signup_start = None  # track if we're stuck on sign-up page

        while time.time() < deadline:
            try:
                self._refresh_active_page()
                if not self.browser.page:
                    time.sleep(1)
                    continue

                # Auto-click OAuth authorize button if on authorize page (retry every 5s)
                if time.time() - last_authorize_attempt > 5:
                    try:
                        current_url = self.browser.page.url if self.browser.page else ''
                        if 'authorize' in current_url:
                            last_authorize_attempt = time.time()
                            self._click_oauth_authorize()
                            continue
                    except Exception:
                        pass

                # Method 1: DrissionPage cookies API (all domains)
                try:
                    cookies = self.browser.page.cookies(all_domains=True, all_info=True) or []
                    for item in cookies:
                        if isinstance(item, dict):
                            name = str(item.get('name', '')).strip()
                            value = str(item.get('value', '')).strip()
                        else:
                            name = str(getattr(item, 'name', '')).strip()
                            value = str(getattr(item, 'value', '')).strip()
                        if name:
                            last_seen_names.add(name)
                        if name == 'sso' and value:
                            logger.info(f"SSO cookie found ({len(value)} chars)")
                            return value
                except Exception:
                    pass

                # Method 2: JS document.cookie
                try:
                    js_cookies = self.browser.run_js('return document.cookie') or ''
                    for pair in js_cookies.split(';'):
                        pair = pair.strip()
                        if '=' in pair:
                            name, value = pair.split('=', 1)
                            if name.strip() == 'sso' and value.strip():
                                logger.info(f"SSO found via JS ({len(value.strip())} chars)")
                                return value.strip()
                except Exception:
                    pass

                # Method 3: localStorage
                try:
                    ls = self.browser.run_js("""
var r = '';
for (var i = 0; i < localStorage.length; i++) {
    var k = localStorage.key(i);
    if (k.toLowerCase().indexOf('sso') >= 0 || k.toLowerCase().indexOf('token') >= 0) {
        r = localStorage.getItem(k);
        break;
    }
}
return r;
                    """)
                    if ls:
                        logger.info(f"SSO found via localStorage ({len(ls)} chars)")
                        return ls
                except Exception:
                    pass

            except PageDisconnectedError:
                self._refresh_active_page()
            except Exception:
                pass

            if time.time() - last_report >= 10:
                current_url = ''
                try:
                    current_url = self.browser.page.url if self.browser.page else 'unknown'
                except Exception:
                    current_url = 'unknown'
                logger.info(f"Waiting for SSO cookie... URL: {current_url}, seen cookies: {sorted(last_seen_names)}")
                last_report = time.time()
                # If stuck on sign-up page for >20s with no SSO, submission likely failed
                if 'sign-up' in current_url or 'signup' in current_url:
                    if stuck_on_signup_start is None:
                        stuck_on_signup_start = time.time()
                    elif time.time() - stuck_on_signup_start > 20:
                        raise Exception(f"Stuck on sign-up page with no SSO for 20s — submission likely failed")

            time.sleep(1)

        raise Exception(f"SSO cookie not found within {timeout}s, seen cookies: {sorted(last_seen_names)}")

    def _extract_visible_numbers(self):
        try:
            result = self.browser.run_js(r"""
function isVisible(el) {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
const selector = ['h1','h2','h3','h4','h5','h6','div','span','p','strong','b','small','[data-testid]','[class]','[role="heading"]'].join(',');
const seen = new Set();
const matches = [];
for (const node of document.querySelectorAll(selector)) {
    if (!isVisible(node)) continue;
    const text = String(node.innerText || node.textContent || '').replace(/\s+/g, ' ').trim();
    if (!text) continue;
    const found = text.match(/\d+(?:\.\d+)?/g);
    if (!found) continue;
    for (const value of found) {
        const key = value + '@@' + text;
        if (seen.has(key)) continue;
        seen.add(key);
        matches.push({ value, text });
    }
}
return matches.slice(0, 30);
            """)
            if result:
                for item in result:
                    logger.info(f"  Number: {item['value']} | Context: {item['text']}")
            return result
        except Exception:
            return []

    def _get_password(self, settings=None):
        if settings is None:
            settings = self.db.get_settings()
        if settings.get('password_mode', 'auto') == 'manual':
            return settings.get('manual_password', self._generate_password())
        return self._generate_password()

    def _generate_password(self):
        """Generate password matching original script format."""
        return 'N' + secrets.token_hex(4) + '!a7#' + secrets.token_urlsafe(6)

    def _generate_random_name(self):
        first_names = ['James', 'Mary', 'Robert', 'Patricia', 'John', 'Jennifer', 'Michael', 'Linda',
                       'David', 'Elizabeth', 'William', 'Barbara', 'Richard', 'Susan', 'Joseph', 'Jessica',
                       'Thomas', 'Sarah', 'Charles', 'Karen', 'Daniel', 'Lisa', 'Matthew', 'Nancy',
                       'Anthony', 'Betty', 'Mark', 'Margaret', 'Donald', 'Sandra', 'Steven', 'Ashley',
                       'Paul', 'Dorothy', 'Andrew', 'Kimberly', 'Joshua', 'Emily', 'Kenneth', 'Donna']
        last_names = ['Smith', 'Johnson', 'Williams', 'Brown', 'Jones', 'Garcia', 'Miller', 'Davis',
                      'Rodriguez', 'Martinez', 'Hernandez', 'Lopez', 'Gonzalez', 'Wilson', 'Anderson',
                      'Thomas', 'Taylor', 'Moore', 'Jackson', 'Martin', 'Lee', 'Perez', 'Thompson',
                      'White', 'Harris', 'Sanchez', 'Clark', 'Ramirez', 'Lewis', 'Robinson']
        return random.choice(first_names), random.choice(last_names)

    def _emit_status(self):
        self.socketio.emit('status_update', self.state.get_snapshot())

    def _emit_error(self, code, message, fatal=False):
        self.socketio.emit('error', {
            'code': code,
            'message': message,
            'fatal': fatal,
        })
