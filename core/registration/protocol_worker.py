"""Protocol registration worker — lease/email/DB reuse, HTTP transport for signup."""

from __future__ import annotations

import logging
import random
import re
import secrets
import threading
import time
from datetime import datetime, timezone

import requests
from urllib.parse import urlparse

from config import SIGNUP_URL
from core.grok2api_client import upload_registered_sso
from core.registration.backend import (
    ExistingAccountActionError,
    ProtocolEnvironmentError,
    ProtocolRegistrationBackend,
    SignupParameterDiscovery,
    apply_cookies_to_session,
    apply_sso_cookies,
    build_protocol_session,
    build_signup_payload,
    follow_sso_http,
    redact_sensitive_text,
    resolve_protocol_proxy,
)
from core.registration.state import (
    DuplicateSSOError,
    ExistingAccountError,
    VerificationRequestError,
    email_request_slot,
    is_xai_permission_denied,
)
from core.registration.turnstile import (
    BrowserTurnstileProvider,
    ExternalTurnstileProvider,
    TurnstileSolveError,
    resolve_turnstile_settings,
)
from core.runtime import resolve_browser_headless, resolve_registration_concurrency

logger = logging.getLogger('register')

MIN_VERIFICATION_CODE_POLLS = 10


class ProtocolRegistrationWorker:
    """Account worker that drives ProtocolRegistrationBackend end-to-end.

    Transport preference (Asset/grok1 style):

    1. Pure curl_cffi HTTP for discovery / gRPC / Server Action / SSO follow
    2. External Turnstile (YesCaptcha or local solver) when configured
    3. Browser bootstrap + BrowserTurnstileProvider only as fallback
       (disabled when ``allow_browser_fallback=false`` / ``turnstile_provider=external``)
    """

    def __init__(self, db, browser_mgr, email_mgr, socketio, state):
        self.db = db
        self.browser = browser_mgr
        self.email_mgr = email_mgr
        self.socketio = socketio
        self.state = state
        self._params = None
        self._session = None
        self._provider = None
        self._external_provider = None
        self._backend = None
        self._pure_http = False
        self._browser_started = False
        self._allow_browser_fallback = True
        self._turnstile_mode = 'none'
        self._sso_follow_mode = 'none'
        self._transport_mode = 'http'

    def run(self, max_rounds=0, max_retries=3, concurrency=1):
        self.state.status = 'running'
        settings = self.db.get_settings()
        concurrency = resolve_registration_concurrency(concurrency)
        batch_id = secrets.token_hex(6)
        claimed_any = threading.Event()
        worker_ready_any = threading.Event()
        logger.info(
            '[protocol] Registration started (max_rounds=%s, max_retries=%s, concurrency=%s)',
            max_rounds or 'unlimited', max_retries, concurrency,
        )

        workers = []
        try:
            for index in range(concurrency):
                worker_id = f'worker-{index + 1}'
                browser = self.browser.clone(worker_id=worker_id) if self.browser else None
                worker = ProtocolRegistrationWorker(
                    self.db, browser, self.email_mgr, self.socketio, self.state,
                )
                thread = threading.Thread(
                    target=worker._run_worker,
                    args=(
                        worker_id,
                        f'{batch_id}:{worker_id}',
                        max_rounds,
                        max_retries,
                        settings,
                        claimed_any,
                        worker_ready_any,
                    ),
                    name=f'protocol-{worker_id}',
                    daemon=True,
                )
                workers.append(thread)
                thread.start()
            for thread in workers:
                thread.join()
        finally:
            self.state.status = 'stopped'
            self._emit_status()
            if not worker_ready_any.is_set() and not self.state.should_stop():
                self._emit_error(
                    'PROTOCOL_WORKER_START',
                    'All protocol registration workers failed to start',
                    fatal=True,
                )
            elif not claimed_any.is_set() and not self.state.should_stop():
                self._emit_error('NO_ALIASES', 'No available aliases', fatal=True)
            snapshot = self.state.get_snapshot()
            logger.info(
                '[protocol] Registration ended. Completed: %s, Success: %s, Failed: %s',
                snapshot['completed'], snapshot['success'], snapshot['failed'],
            )

    def _run_worker(self, worker_id, lease_owner, max_rounds, max_retries,
                    settings, claimed_any, worker_ready_any):
        lease_seconds = max(
            120,
            int(settings.get('registration_timeout', 300) or 300) * 2,
        )
        try:
            self._prepare_transport(settings)
            worker_ready_any.set()
        except Exception as exc:
            logger.error('[protocol][%s] transport bootstrap failed: %s', worker_id, exc)
            self._emit_error(
                'PROTOCOL_BOOTSTRAP',
                f'[{worker_id}] Protocol bootstrap failed: {exc}',
                fatal=False,
            )
            return

        try:
            while not self.state.should_stop():
                self.state.check_pause()
                if self.state.should_stop():
                    break
                if not self.state.has_worker_round_capacity(max_rounds):
                    break
                try:
                    alias = self.email_mgr.claim_registration_alias(
                        settings,
                        max_retries=max_retries,
                        lease_owner=lease_owner,
                        lease_seconds=lease_seconds,
                    )
                except Exception as exc:
                    logger.error(
                        '[protocol][%s] Email provider failed: %s', worker_id, exc,
                    )
                    self._emit_error(
                        'EMAIL_PROVIDER',
                        f'[{worker_id}] Email provider failed: {exc}',
                        fatal=True,
                    )
                    self.state.stop()
                    break
                if not alias:
                    break
                claimed_any.set()

                round_num = self.state.reserve_worker_round(
                    worker_id, alias, max_rounds,
                )
                if round_num is None:
                    self.db.release_alias_claim(alias['id'], lease_owner)
                    break

                self._emit_status()
                heartbeat_stop = threading.Event()
                heartbeat = threading.Thread(
                    target=self._lease_heartbeat,
                    args=(alias['id'], lease_owner, lease_seconds, heartbeat_stop),
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
                            '[protocol][%s] Unexpected worker round failure: %s',
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
            self._shutdown_transport()

    def _prepare_transport(self, settings):
        proxy = resolve_protocol_proxy(settings)
        headless = resolve_browser_headless(settings)
        timeout = int(settings.get('registration_timeout', 300) or 300)
        turnstile_timeout = max(45, timeout // 4)
        auto_turnstile = str(settings.get('turnstile_auto', 'true')).lower() != 'false'
        turnstile_cfg = resolve_turnstile_settings(settings)
        self._allow_browser_fallback = turnstile_cfg.get('allow_browser_fallback') != 'false'
        self._turnstile_mode = 'none'
        self._sso_follow_mode = 'none'
        self._transport_mode = 'http'

        self._session = build_protocol_session(settings)
        self._external_provider = ExternalTurnstileProvider.from_settings(
            settings, timeout=turnstile_timeout,
        )
        self._pure_http = False
        self._browser_started = False
        self._provider = None

        # Phase 1: try pure HTTP discovery (Asset/grok1 style).
        try:
            self._params = self._discover_parameters_http()
            self._pure_http = True
            self._transport_mode = 'http'
            logger.info(
                '[protocol] transport=http ready impersonate=%s proxy=%s '
                'turnstile_provider=%s allow_browser_fallback=%s',
                getattr(self._session, '_protocol_impersonate', '') or '?',
                'yes' if proxy else 'no',
                (
                    self._external_provider.name
                    if self._external_provider is not None
                    else ('browser' if self._allow_browser_fallback else 'none')
                ),
                self._allow_browser_fallback,
            )
        except ProtocolEnvironmentError as exc:
            if not self._allow_browser_fallback:
                raise ProtocolEnvironmentError(
                    'protocol pure HTTP blocked and browser fallback is disabled '
                    f'(allow_browser_fallback=false): {exc}',
                    reason='blocked_no_fallback',
                    diagnostics=str(exc),
                ) from exc
            logger.warning(
                '[protocol] pure HTTP discovery failed (%s); falling back to browser bootstrap',
                exc.reason,
            )
            if self.browser is None:
                raise ProtocolEnvironmentError(
                    'protocol pure HTTP blocked and no BrowserManager available for bootstrap',
                    reason='no_browser',
                    diagnostics=str(exc),
                ) from exc
            self._provider = BrowserTurnstileProvider(
                self.browser,
                auto=auto_turnstile,
                timeout=turnstile_timeout,
            )
            self._provider.ensure_started(headless=headless, proxy=proxy)
            self._browser_started = True
            self._params = self._discover_parameters_browser()
            self._pure_http = False
            self._transport_mode = 'browser'

        # Browser provider is still useful as Turnstile fallback when pure HTTP
        # works but no external solver is configured — only if fallback allowed.
        if (
            self._provider is None
            and self.browser is not None
            and self._allow_browser_fallback
        ):
            self._provider = BrowserTurnstileProvider(
                self.browser,
                auto=auto_turnstile,
                timeout=turnstile_timeout,
            )
            # Lazily started only when solve() needs it.
        self._bind_backend()

    def _shutdown_transport(self):
        if self._provider is not None and self._browser_started:
            try:
                self._provider.stop()
            except Exception:
                pass
        self._provider = None
        self._external_provider = None
        self._backend = None
        self._session = None
        self._params = None
        self._pure_http = False
        self._browser_started = False
        self._turnstile_mode = 'none'
        self._sso_follow_mode = 'none'
        self._transport_mode = 'http'

    def _mode_summary(self) -> str:
        transport = self._transport_mode or ('http' if self._pure_http else 'browser')
        turnstile = self._turnstile_mode or 'none'
        sso_follow = self._sso_follow_mode or 'none'
        return f'transport={transport} turnstile={turnstile} sso_follow={sso_follow}'

    def _bind_backend(self):
        """(Re)build protocol backend; pure HTTP uses session, hybrid uses browser fetch."""
        if self._session is None or self._params is None:
            raise ProtocolEnvironmentError(
                'protocol backend missing session/params',
                reason='not_ready',
            )
        use_browser_transport = (
            not self._pure_http
            and self._provider is not None
            and self._browser_started
        )
        self._backend = ProtocolRegistrationBackend(
            self._session,
            self._params,
            request_func=self._browser_request if use_browser_transport else None,
        )
        # Only wire browser SSO navigate when Chrome is already running. Pure HTTP
        # extract_sso prefers HTTP multi-hop first regardless.
        if self._provider is not None and self._browser_started:
            self._backend._cookie_getter = self._browser_cookie
            self._backend._navigate_for_sso = self._provider.navigate_for_sso

    def _ensure_browser_ready(self, *, force_reload: bool = False):
        """Recover a disconnected page between protocol rounds (hybrid path only)."""
        if self._pure_http or self._provider is None:
            return
        if not self._browser_started:
            return
        browser = self._provider.browser
        proxy = getattr(browser, 'proxy', '') or ''
        headless = bool(getattr(browser, 'headless', False))
        healthy = False
        try:
            page = browser.page
            if page is not None:
                _ = page.url
                if not force_reload:
                    healthy = True
        except Exception:
            healthy = False

        if not healthy or force_reload:
            logger.info('[protocol] recycling browser context (force_reload=%s)', force_reload)
            try:
                self._provider.stop()
            except Exception:
                pass
            self._provider._started = False
            self._provider.ensure_started(headless=headless, proxy=proxy)
            self._browser_started = True
            try:
                self._provider.browser.get(SIGNUP_URL)
                time.sleep(1.0)
            except Exception as exc:
                logger.warning('[protocol] signup reload after recycle failed: %s', exc)
            try:
                if not self._pure_http:
                    self._params = self._discover_parameters_browser()
                self._bind_backend()
            except Exception as exc:
                logger.warning('[protocol] rediscover after recycle failed: %s', exc)
                self._bind_backend()
            return

        # Soft reset: clear identity cookies, keep CF, return to signup origin.
        try:
            page = browser.page
            if page is not None:
                try:
                    page.run_cdp(
                        'Network.clearDataForOrigin',
                        origin='https://accounts.x.ai',
                        storageTypes='cookies',
                    )
                except Exception:
                    pass
                for item in list(self._provider._export_cookies() or []):
                    name = str(item.get('name') or '')
                    domain = str(item.get('domain') or '')
                    if name in ('sso', 'sso-rw', 'sso_token') or name.lower().startswith('sso'):
                        try:
                            page.run_cdp(
                                'Network.deleteCookies',
                                name=name,
                                domain=domain or None,
                            )
                        except Exception:
                            pass
            self._provider.browser.get(SIGNUP_URL)
            time.sleep(0.8)
        except Exception as exc:
            logger.warning('[protocol] soft browser reset failed: %s', exc)
            self._ensure_browser_ready(force_reload=True)

    def _discover_parameters_http(self):
        discovery = SignupParameterDiscovery(self._session)
        # Warmup like Asset/grok1 — establishes __cf_bm on good impersonations.
        try:
            self._session.get(SIGNUP_URL, timeout=15)
        except Exception:
            pass
        return discovery.discover(SIGNUP_URL)

    def _discover_parameters_browser(self):
        if self._provider is None:
            raise ProtocolEnvironmentError(
                'browser discovery requires BrowserTurnstileProvider',
                reason='no_browser',
            )
        if not self._browser_started:
            proxy = resolve_protocol_proxy(self.db.get_settings() if self.db else {})
            self._provider.ensure_started(proxy=proxy)
            self._browser_started = True
        discovery = SignupParameterDiscovery(self._session)
        boot = self._provider.bootstrap_signup(SIGNUP_URL)
        applied = apply_cookies_to_session(self._session, boot.get('cookies') or [])
        logger.info('[protocol] browser cookies applied=%s', applied)
        html = boot.get('html') or ''
        script_texts = boot.get('script_texts') or []
        return discovery.discover_from_html(
            html,
            signup_url='',
            script_texts=script_texts,
        )

    def _discover_parameters(self, settings):
        """Compatibility wrapper: pure HTTP first, browser bootstrap on block."""
        try:
            params = self._discover_parameters_http()
            self._pure_http = True
            return params
        except ProtocolEnvironmentError:
            params = self._discover_parameters_browser()
            self._pure_http = False
            return params

    def _solve_turnstile(self, *, url: str, site_key: str) -> str:
        """Prefer external solver; fall back to browser widget only when allowed."""
        errors = []
        if self._external_provider is not None:
            try:
                if self._external_provider.yescaptcha_key or self._external_provider.available():
                    token = self._external_provider.solve(
                        url=url, site_key=site_key, session=self._session,
                    )
                    self._turnstile_mode = (
                        'yescaptcha' if self._external_provider.yescaptcha_key else 'local_solver'
                    )
                    return token
                errors.append('external:unavailable')
            except Exception as exc:
                errors.append(f'external:{exc}')
                logger.warning('[protocol] external Turnstile failed: %s', exc)

        if not self._allow_browser_fallback:
            raise TurnstileSolveError(
                'strict external Turnstile failed and browser fallback is disabled '
                '(set allow_browser_fallback=true or turnstile_provider=auto to permit Chrome)'
                + (f'; attempts={errors}' if errors else '')
            )

        if self._provider is None:
            raise TurnstileSolveError(
                'no Turnstile provider available (configure YESCAPTCHA_KEY / '
                'turnstile_solver_url, or provide a BrowserManager)'
                + (f'; attempts={errors}' if errors else '')
            )

        if not self._browser_started:
            proxy = resolve_protocol_proxy(self.db.get_settings() if self.db else {})
            headless = resolve_browser_headless(self.db.get_settings() if self.db else {})
            self._provider.ensure_started(headless=headless, proxy=proxy)
            self._browser_started = True
            # Keep pure HTTP transport for API calls; browser only for token /
            # optional hybrid SSO navigate. Rebind so navigate helper is available.
            self._bind_backend()

        try:
            token = self._provider.solve(url=url, site_key=site_key, session=self._session)
            self._turnstile_mode = 'browser'
            return token
        except Exception as exc:
            errors.append(f'browser:{exc}')
            raise TurnstileSolveError(
                'Turnstile solve failed: ' + '; '.join(errors)
            ) from exc

    def _browser_request(self, method, url, headers=None, data=None, timeout=20):
        if self._provider is None or not self._browser_started:
            raise ProtocolEnvironmentError(
                'browser transport requested but browser is not started',
                reason='no_browser',
            )
        path = urlparse(url).path or url
        try:
            current = ''
            if self._provider.browser and self._provider.browser.page:
                current = self._provider.browser.page.url or ''
            if current and 'accounts.x.ai' not in current:
                logger.info('[protocol] restoring accounts origin before %s %s', method, path)
                self._provider.browser.get(SIGNUP_URL)
                time.sleep(0.6)
        except Exception:
            pass
        logger.info('[protocol] browser transport %s %s', method, path)
        return self._provider.fetch(
            url, method=method, headers=headers, data=data, timeout=timeout,
        )

    def _browser_cookie(self, name: str) -> str:
        if self._provider is None or not self._browser_started:
            return ''
        try:
            cookies = self._provider._export_cookies()
        except Exception:
            return ''
        for item in cookies or []:
            if str(item.get('name') or '') == name:
                return str(item.get('value') or '')
        return ''

    def _post_success_init(self, sso: str, settings=None):
        """Optional pure-HTTP TOS + birth-date init after SSO (Asset/grok1 style)."""
        settings = settings or {}
        if str(settings.get('protocol_post_init', 'true')).lower() == 'false':
            return
        sso = (sso or '').strip()
        if not sso:
            return
        try:
            from core.account_activation import _birth_date, _set_tos
        except Exception as exc:
            logger.debug('[protocol] post-init imports failed: %s', exc)
            return

        try:
            from curl_cffi import requests as creq
        except Exception:
            creq = None

        proxy = resolve_protocol_proxy(settings)
        impersonate = getattr(self._session, '_protocol_impersonate', '') or 'chrome110'
        try:
            ua = str(self._session.headers.get('User-Agent') or self._session.headers.get('user-agent') or '')
        except Exception:
            ua = ''
        if not ua:
            ua = (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                '(KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36'
            )
        try:
            if creq is not None:
                session = creq.Session(impersonate=impersonate if impersonate != 'default' else 'chrome110')
            else:
                session = requests.Session()
            session.headers.update({'user-agent': ua})
            if proxy:
                session.proxies.update({'http': proxy, 'https': proxy})
            # Stamp SSO on both .x.ai (TOS) and .grok.com (birth-date). Also keep
            # an explicit cookie dict for curl_cffi domain quirks.
            cookie_dict = apply_sso_cookies(session, sso)
            try:
                tos_ok = _set_tos(session, proxy_url=proxy)
            except Exception as exc:
                logger.warning('[protocol] post-init TOS failed: %s', type(exc).__name__)
                tos_ok = False
            birth_ok = False
            try:
                resp = session.post(
                    'https://grok.com/rest/auth/set-birth-date',
                    json={'birthDate': _birth_date()},
                    headers={
                        'content-type': 'application/json',
                        'origin': 'https://grok.com',
                        'referer': 'https://grok.com/',
                        'user-agent': ua,
                    },
                    cookies=cookie_dict,
                    timeout=15,
                )
                birth_ok = 200 <= getattr(resp, 'status_code', 0) < 300
                if not birth_ok:
                    logger.warning(
                        '[protocol] post-init birth-date HTTP %s',
                        getattr(resp, 'status_code', '?'),
                    )
            except Exception as exc:
                logger.warning('[protocol] post-init birth-date failed: %s', type(exc).__name__)
            logger.info('[protocol] post-init tos=%s birth=%s', tos_ok, birth_ok)
        except Exception as exc:
            logger.warning('[protocol] post-init skipped: %s', type(exc).__name__)

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
                        return
                    logger.warning('[protocol] Alias %s lease was lost', alias_id)
                    return
            except Exception as exc:
                logger.warning('[protocol] Alias %s lease heartbeat failed: %s', alias_id, exc)

    def _do_one_round(self, alias, round_num, max_retries, settings,
                      lease_owner, worker_id):
        alias_email = alias['alias_email']
        logger.info('[protocol][%s] Round %s: using alias %s', worker_id, round_num, alias_email)

        password = self._get_password(settings)
        given_name, family_name = self._generate_random_name(settings)
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
            self.state.check_pause()
            # Hybrid path only: recover page disconnects from previous navigations.
            if not self._pure_http:
                self._ensure_browser_ready(force_reload=False)
            if self._params is None:
                self._params = self._discover_parameters(settings)
            self._bind_backend()

            # Keep browser cookies fresh for protocol session when hybrid.
            if (
                not self._pure_http
                and self._provider is not None
                and self._browser_started
            ):
                try:
                    apply_cookies_to_session(self._session, self._provider._export_cookies())
                except Exception:
                    pass

            self.state.check_pause()
            mode = self._mode_summary()
            logger.info('[protocol] sending verification code for %s (%s)', alias_email, mode)
            verification_requested_at = datetime.now(timezone.utc)
            try:
                with email_request_slot():
                    self._backend.send_email_code(alias_email, SIGNUP_URL)
            except ProtocolEnvironmentError:
                raise
            except requests.HTTPError as exc:
                raise VerificationRequestError(str(exc)) from exc
            except Exception as exc:
                raise VerificationRequestError(str(exc)) from exc

            self.state.check_pause()
            logger.info('[protocol] polling mailbox for verification code…')
            code = self.email_mgr.get_code_for_alias(
                alias_email, alias['account_id'],
                alias['client_id'], alias['refresh_token'],
                max_retries=max(
                    MIN_VERIFICATION_CODE_POLLS,
                    int(settings.get('max_code_retries', 3) or 3),
                ),
                main_email=alias.get('main_email'),
                requested_after=verification_requested_at,
                provider=alias.get('provider', 'microsoft'),
                settings=settings,
            )

            self.state.check_pause()
            logger.info('[protocol] verifying email code')
            try:
                self._backend.verify_email_code(alias_email, code, SIGNUP_URL)
            except ProtocolEnvironmentError:
                raise
            except requests.HTTPError as exc:
                text = str(exc).lower()
                if 'exist' in text or 'already' in text:
                    raise ExistingAccountError(
                        '注册邮箱已存在：xAI reports Existing account found'
                    ) from exc
                raise

            self.state.check_pause()
            token = self._solve_turnstile(
                url=SIGNUP_URL,
                site_key=self._params.site_key,
            )
            if not token:
                raise TurnstileSolveError('empty Turnstile token')

            # Castle is optional; pure-HTTP path (Asset/grok1) does not send it.
            castle_token = ''
            if (
                not self._pure_http
                and self._provider is not None
                and self._browser_started
            ):
                try:
                    castle_token = self._provider.create_castle_token() or ''
                except Exception as exc:
                    logger.warning('[protocol] castle token skipped: %s', exc)

            payload = build_signup_payload(
                email=alias_email,
                password=password,
                given_name=given_name,
                family_name=family_name,
                email_validation_code=code,
                turnstile_token=token,
                castle_request_token=castle_token,
            )
            logger.info('[protocol] submitting signup for %s', alias_email)
            try:
                response = self._backend.submit_signup(payload, SIGNUP_URL, token)
            except ExistingAccountActionError as exc:
                raise ExistingAccountError(
                    f'注册邮箱已存在：{exc}'
                ) from exc
            except ProtocolEnvironmentError:
                raise
            except requests.HTTPError as exc:
                text = str(exc).lower()
                if 'exist' in text or 'already' in text:
                    raise ExistingAccountError(
                        '注册邮箱已存在：xAI reports Existing account found'
                    ) from exc
                raise

            result = self._backend.extract_sso(response)
            sso = (result.sso or '').strip()
            follow_mode = getattr(self._backend, 'last_sso_follow', '') or ''
            if follow_mode:
                self._sso_follow_mode = follow_mode
            if not sso:
                time.sleep(1.0)
                try:
                    from core.registration.backend import read_sso_cookie_from_session
                    sso = read_sso_cookie_from_session(self._session)
                    if sso and not self._sso_follow_mode:
                        self._sso_follow_mode = 'cookie'
                except Exception:
                    sso = ''
            if not sso:
                sso = self._browser_cookie('sso')
                if sso:
                    self._sso_follow_mode = 'browser'
            if not sso:
                body = getattr(response, 'text', '') or ''
                match = re.search(
                    r'https://[^"\s\\]+set-cookie\?q=[^"\s\\]+',
                    body.replace('\\/', '/'),
                    re.I,
                )
                if match:
                    set_cookie_url = match.group(0).rstrip('",;)}]')
                    # Prefer pure HTTP multi-hop again before touching Chrome.
                    try:
                        sso = follow_sso_http(self._session, set_cookie_url) or ''
                        if sso:
                            self._sso_follow_mode = 'http'
                    except Exception:
                        sso = ''
                    if (
                        not sso
                        and self._allow_browser_fallback
                        and self._provider is not None
                        and self._browser_started
                    ):
                        sso = self._provider.navigate_for_sso(set_cookie_url)
                        if sso:
                            self._sso_follow_mode = 'browser'
            if not sso:
                body = redact_sensitive_text(getattr(response, 'text', '') or '', limit=400)
                raise RuntimeError(
                    f'protocol signup completed without SSO cookie; status='
                    f'{getattr(response, "status_code", "?")} body={body}'
                )

            duplicate = self.db.find_existing_sso(sso)
            if duplicate:
                fingerprint = duplicate.get('fingerprint', '')[:12]
                raise DuplicateSSOError(
                    f'Duplicate SSO identity detected (sha256={fingerprint}, '
                    f'previous={duplicate.get("email", "unknown")})'
                )

            # Optional pure-HTTP post-init (TOS / birth). Failures do not fail the round.
            try:
                self._post_success_init(sso, settings)
            except Exception as init_exc:
                logger.warning('[protocol] post-success init failed: %s', init_exc)

            duration = time.time() - start_time
            grok2api_enabled = settings.get('grok2api_auto_upload', 'false') == 'true'
            completion = self.db.complete_registration_success(
                reg_id, alias['id'], lease_owner, sso, duration=duration,
                grok2api_pending=grok2api_enabled,
            )
            success_committed = True

            if grok2api_enabled:
                self.db.begin_grok2api_upload(reg_id)
                try:
                    upload_result = upload_registered_sso(
                        settings, sso, email=alias_email,
                        user_agent='',
                        cloudflare_cookies='',
                    )
                    self.db.finish_grok2api_upload(reg_id, True)
                    if upload_result is not None:
                        imported = upload_result.get('import', {})
                        converted = upload_result.get('conversion', {})
                        logger.info(
                            '[protocol] grok2api auto pipeline completed: '
                            'web_created=%s build_created=%s',
                            imported.get('created', 0),
                            converted.get('created', 0),
                        )
                except Exception as upload_error:
                    self.db.finish_grok2api_upload(reg_id, False, upload_error)
                    logger.warning('[protocol] grok2api auto upload failed: %s', upload_error)

            self.state.record_success(worker_id)
            mode = self._mode_summary()
            logger.info(
                '[protocol] Round %s SUCCESS! Duration: %.1fs %s',
                round_num, duration, mode,
            )
            self.socketio.emit('round_complete', {
                'round': round_num,
                'email': alias_email,
                'success': True,
                'sso': sso[:50] + '...' if len(sso) > 50 else sso,
                'duration': round(duration, 1),
                'backend': 'protocol',
                'transport': mode,
            })
            self._emit_status()
            if completion.get('account_done'):
                logger.info(
                    '[protocol] Account %s aliases exhausted, marked as done',
                    alias.get('main_email') or alias_email,
                )
            if not self._pure_http:
                try:
                    self._ensure_browser_ready(force_reload=False)
                except Exception as recycle_exc:
                    logger.warning('[protocol] post-success browser reset failed: %s', recycle_exc)

        except Exception as e:
            duration = time.time() - start_time
            error_msg = str(e)
            if success_committed:
                logger.warning(
                    '[protocol] Round %s completed successfully but cleanup failed: %s',
                    round_num, error_msg,
                )
                return
            self._handle_round_failure(
                e, error_msg, duration, reg_id, alias, lease_owner,
                max_retries, round_num, worker_id, alias_email,
            )

    def _handle_round_failure(self, exc, error_msg, duration, reg_id, alias,
                              lease_owner, max_retries, round_num, worker_id,
                              alias_email):
        if isinstance(exc, ProtocolEnvironmentError):
            released = self.db.abort_registration_attempt(
                reg_id=reg_id,
                alias_id=alias['id'],
                lease_owner=lease_owner,
                error=error_msg,
                duration=duration,
            )
            self.state.stop()
            logger.error(
                '[protocol] Round %s stopped by environment block (%s); alias %s was %s',
                round_num, exc.reason, alias_email,
                'released' if released else 'not released (lease lost)',
            )
            self.socketio.emit('round_complete', {
                'round': round_num,
                'email': alias_email,
                'success': False,
                'environment_blocked': True,
                'reason': exc.reason,
                'duration': round(duration, 1),
                'backend': 'protocol',
            })
            self._emit_error(
                'PROTOCOL_ENVIRONMENT_BLOCKED',
                'Protocol signup was blocked by Cloudflare/network environment. '
                'The alias was preserved. Align browser_proxy and retry. '
                f'Diagnostics: {exc.diagnostics}',
                fatal=True,
            )
            self._emit_status()
            return

        if isinstance(exc, ExistingAccountError):
            outcome = self.db.skip_existing_account_attempt(
                reg_id=reg_id,
                alias_id=alias['id'],
                lease_owner=lease_owner,
                error=error_msg,
                duration=duration,
            )
            self.state.record_failure(worker_id)
            logger.warning(
                '[protocol] Existing account skipped without retry: %s (lease_lost=%s)',
                alias_email, outcome.get('lease_lost'),
            )
            self.socketio.emit('round_complete', {
                'round': round_num,
                'email': alias_email,
                'success': False,
                'skipped': True,
                'reason': 'existing_account',
                'duration': round(duration, 1),
                'backend': 'protocol',
            })
            self._emit_status()
            return

        if isinstance(exc, DuplicateSSOError):
            duplicate_limit = max_retries + 1
            outcome = self.db.finish_registration_attempt(
                reg_id=reg_id,
                alias_id=alias['id'],
                lease_owner=lease_owner,
                error=error_msg,
                duration=duration,
                max_retries=duplicate_limit,
            )
            logger.warning(
                '[protocol] Round %s duplicate SSO; retry=%s terminal=%s',
                round_num, outcome.get('retry_count', 0), outcome.get('terminal', False),
            )
            if outcome.get('terminal'):
                self.state.record_failure(worker_id)
            self._emit_status()
            return

        if isinstance(exc, VerificationRequestError) and is_xai_permission_denied(exc):
            released = self.db.abort_registration_attempt(
                reg_id=reg_id,
                alias_id=alias['id'],
                lease_owner=lease_owner,
                error=error_msg,
                duration=duration,
            )
            self.state.record_failure(worker_id)
            self.state.stop()
            logger.error(
                '[protocol] Round %s stopped by xAI permission_denied 403; alias %s was %s',
                round_num, alias_email,
                'released' if released else 'not released (lease lost)',
            )
            self._emit_error(
                'XAI_PERMISSION_DENIED',
                'xAI rejected the verification-code request with HTTP 403. '
                'No email was sent; the alias was preserved.',
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
        logger.error('[protocol] Round %s FAILED: %s', round_num, error_msg)
        if outcome.get('lease_lost'):
            logger.warning('[protocol] Alias %s lease was lost before failure commit', alias['id'])
        elif outcome.get('terminal'):
            self.state.record_failure(worker_id)
            logger.info(
                '[protocol] Alias %s exhausted %s retries, marked failed',
                alias_email, max_retries,
            )
        else:
            logger.info(
                '[protocol] Alias %s will retry (%s/%s)',
                alias_email, outcome.get('retry_count', 0), max_retries,
            )
        self.socketio.emit('round_complete', {
            'round': round_num,
            'email': alias_email,
            'success': False,
            'duration': round(duration, 1),
            'backend': 'protocol',
            'error': error_msg[:200],
        })
        self._emit_status()
        # Page-disconnect style failures need a hard browser recycle.
        lowered = error_msg.lower()
        if any(token in lowered for token in (
            'failed to run js', 'disconnect', 'disconnected', 'failed to fetch',
            '页面', '断开', '刷新',
        )):
            try:
                self._ensure_browser_ready(force_reload=True)
            except Exception as recycle_exc:
                logger.warning('[protocol] post-failure browser recycle failed: %s', recycle_exc)

    def _get_password(self, settings=None):
        if settings is None:
            settings = self.db.get_settings()
        if settings.get('password_mode', 'auto') == 'manual':
            manual = settings.get('manual_password', '')
            return manual or self._generate_password()
        return self._generate_password()

    @staticmethod
    def _generate_password():
        return 'N' + secrets.token_hex(4) + '!a7#' + secrets.token_urlsafe(6)

    @staticmethod
    def _generate_random_name(settings=None):
        settings = settings or {}
        if str(settings.get('random_name_enabled', 'true')).lower() == 'false':
            return 'Test', 'User'
        first_names = [
            'James', 'Mary', 'Robert', 'Patricia', 'John', 'Jennifer', 'Michael', 'Linda',
            'David', 'Elizabeth', 'William', 'Barbara', 'Richard', 'Susan', 'Joseph', 'Jessica',
        ]
        last_names = [
            'Smith', 'Johnson', 'Williams', 'Brown', 'Jones', 'Garcia', 'Miller', 'Davis',
            'Wilson', 'Anderson', 'Thomas', 'Taylor', 'Moore', 'Jackson', 'Martin', 'Lee',
        ]
        return random.choice(first_names), random.choice(last_names)

    def _emit_status(self):
        self.socketio.emit('status_update', self.state.get_snapshot())

    def _emit_error(self, code, message, fatal=False):
        self.socketio.emit('error', {
            'code': code,
            'message': message,
            'fatal': fatal,
        })
