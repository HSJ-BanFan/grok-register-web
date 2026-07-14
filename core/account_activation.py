import logging
import random
import struct
import time
from dataclasses import dataclass
from datetime import date

from curl_cffi import requests


logger = logging.getLogger('register')


@dataclass
class ActivationContext:
    ready: bool
    message: str
    user_agent: str = ''
    cloudflare_cookies: str = ''


def _birth_date():
    today = date.today()
    return f'{today.year-random.randint(20, 40)}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}T16:00:00.000Z'


def _cookie_value(item, key):
    if isinstance(item, dict):
        return str(item.get(key, '') or '')
    return str(getattr(item, key, '') or '')


def _extract_browser_context(page):
    cookies = page.cookies(all_domains=True, all_info=True) or []
    allowed = []
    for item in cookies:
        name = _cookie_value(item, 'name').strip()
        value = _cookie_value(item, 'value').strip()
        domain = _cookie_value(item, 'domain').strip().lower().lstrip('.')
        if not name or not value or not (domain == 'grok.com' or domain.endswith('.grok.com')):
            continue
        if name in {'cf_clearance', '__cf_bm'}:
            allowed.append(f'{name}={value}')
    user_agent = str(page.run_js('return navigator.userAgent;') or '').strip()
    return '; '.join(allowed), user_agent


def _try_click_turnstile(page):
    """Best-effort Turnstile interaction, ported from automation/tooling/grok-register.

    Prefer shadow-root → iframe → body shadow-root → checkbox path.
    Avoid repeated main-page spam clicks that reset managed challenges.
    """
    clicked = False

    # 1) Preferred path used by the other project: shadow root checkbox click.
    try:
        challenge_input = page.ele('@name=cf-turnstile-response', timeout=0.5)
    except Exception:
        challenge_input = None

    if challenge_input:
        try:
            wrapper = challenge_input.parent()
            iframe = None
            try:
                iframe = wrapper.shadow_root.ele('tag:iframe', timeout=1)
            except Exception:
                iframe = None
            if iframe:
                try:
                    iframe.run_js(r"""
window.dtp = 1;
function getRandomInt(min, max) { return Math.floor(Math.random() * (max - min + 1)) + min; }
let sx = getRandomInt(800, 1200);
let sy = getRandomInt(400, 700);
Object.defineProperty(MouseEvent.prototype, 'screenX', { value: sx });
Object.defineProperty(MouseEvent.prototype, 'screenY', { value: sy });
                    """)
                except Exception:
                    pass
                try:
                    body_sr = iframe.ele('tag:body', timeout=1).shadow_root
                    btn = body_sr.ele('tag:input', timeout=1)
                    if btn:
                        btn.click()
                        clicked = True
                        logger.info('Turnstile checkbox clicked via shadow-root path')
                except Exception as exc:
                    logger.debug('shadow-root checkbox click failed: %s', exc)
        except Exception as exc:
            logger.debug('Turnstile shadow path failed: %s', exc)

    # 2) If token already exists, treat as success without extra clicking.
    try:
        token = page.run_js(r"""
try {
  const byInput = String((document.querySelector('input[name="cf-turnstile-response"]') || {}).value || '').trim();
  if (byInput) return byInput;
  if (window.turnstile && typeof turnstile.getResponse === 'function') {
    return String(turnstile.getResponse() || '').trim();
  }
  return '';
} catch (e) { return ''; }
        """) or ''
        if len(str(token).strip()) >= 80:
            return True
    except Exception:
        pass

    # 3) Soft fallback: one click on a turnstile-looking container (not every second).
    if not clicked:
        try:
            hit = page.run_js(r"""
const nodes = Array.from(document.querySelectorAll('div,span,iframe,input')).filter((n) => {
  const txt = ((n.className || '') + ' ' + (n.id || '') + ' ' + (n.getAttribute?.('src') || '') + ' ' + (n.getAttribute?.('name') || '')).toLowerCase();
  return txt.includes('turnstile') || txt.includes('cf-turnstile') || txt.includes('challenge');
});
if (!nodes.length) return false;
const target = nodes[0];
try { target.scrollIntoView({block:'center'}); } catch (e) {}
try { target.click(); } catch (e) {}
return true;
            """)
            if hit:
                clicked = True
        except Exception as exc:
            logger.debug('Turnstile soft fallback failed: %s', exc)

    return clicked


def _wait_for_grok(page, timeout=60, auto_click=None):
    # timeout <= 0 means wait indefinitely.
    indefinite = timeout is None or int(timeout) <= 0
    # Default: allow a few precise shadow-root clicks even in long-wait mode.
    if auto_click is None:
        auto_click = True
    deadline = None if indefinite else (time.time() + timeout)
    last_notice = 0
    last_click = 0
    auto_attempts = 0
    while indefinite or time.time() < deadline:
        try:
            state = page.run_js(r"""
const title = String(document.title || '').toLowerCase();
const body = String(document.body?.innerText || '').toLowerCase();
const challenge = title.includes('just a moment')
  || body.includes('verifying you are human')
  || body.includes('performing security verification')
  || !!document.querySelector('#challenge-stage, script[src*="challenge-platform"]');
return {challenge, url: location.href, title: document.title};
            """) or {}
        except Exception as exc:
            logger.debug('wait_for_grok state probe failed: %s', exc)
            time.sleep(1)
            continue
        if not state.get('challenge') and 'grok.com' in str(state.get('url', '')).lower():
            return True

        now = time.time()
        # At most a few precise auto attempts; repeated spam clicks break Turnstile mid-verify.
        if (
            auto_click
            and state.get('challenge')
            and auto_attempts < 3
            and now - last_click >= 4
        ):
            try:
                if _try_click_turnstile(page):
                    auto_attempts += 1
                    logger.info(
                        'Attempted automatic Cloudflare Turnstile interaction (%s/3)',
                        auto_attempts,
                    )
            except Exception as click_exc:
                logger.debug('auto turnstile click raised: %s', click_exc)
            last_click = now

        if state.get('challenge') and now - last_notice >= 10:
            if indefinite:
                logger.info(
                    'Waiting for Cloudflare human verification on grok.com '
                    '(auto shadow-root click enabled; complete remaining check if still stuck; no timeout)...'
                )
            else:
                remaining = max(0, int(deadline - now))
                logger.info(
                    'Waiting for Cloudflare human verification on grok.com '
                    '(auto shadow-root click; complete remaining check if needed). %ss left...',
                    remaining,
                )
            last_notice = now
        time.sleep(1)
    return False


def _set_tos(session):
    payload = struct.pack('B', (2 << 3) | 0) + struct.pack('B', 1)
    data = b'\x00' + struct.pack('>I', len(payload)) + payload
    response = session.post(
        'https://accounts.x.ai/auth_mgmt.AuthManagement/SetTosAcceptedVersion',
        data=data,
        headers={
            'content-type': 'application/grpc-web+proto',
            'x-grpc-web': '1',
            'x-user-agent': 'connect-es/2.1.1',
            'origin': 'https://accounts.x.ai',
            'referer': 'https://accounts.x.ai/accept-tos',
        },
        timeout=20,
    )
    return 200 <= response.status_code < 300


def clear_sso_cookies(page):
    """Remove only SSO identity cookies so Cloudflare clearance can be reused."""
    for domain in ('.x.ai', 'accounts.x.ai', 'x.ai', '.grok.com', 'grok.com', 'auth.x.ai', '.auth.x.ai'):
        for name in ('sso', 'sso-rw'):
            try:
                page.run_cdp('Network.deleteCookies', name=name, domain=domain)
            except Exception:
                pass
            try:
                page.set.cookies.remove(name, domain=domain)
            except Exception:
                pass


def inject_sso_cookie(page, sso_cookie):
    """Inject the xAI SSO cookie into the browser for account switching."""
    token = (sso_cookie or '').strip()
    if not token:
        raise ValueError('SSO cookie is empty')

    # Prefer CDP so domain cookies can be set even on about:blank.
    for domain in ('.x.ai', 'accounts.x.ai', '.grok.com', 'grok.com'):
        try:
            page.run_cdp(
                'Network.setCookie',
                name='sso',
                value=token,
                domain=domain,
                path='/',
                secure=True,
                httpOnly=True,
                sameSite='None',
            )
        except Exception as exc:
            logger.debug('Failed to set sso cookie on %s: %s', domain, exc)

    # Fallback via DrissionPage cookie setter (domain-aware).
    try:
        page.set.cookies([
            {'name': 'sso', 'value': token, 'domain': '.x.ai', 'path': '/', 'secure': True},
            {'name': 'sso', 'value': token, 'domain': '.grok.com', 'path': '/', 'secure': True},
        ])
    except Exception as exc:
        logger.debug('page.set.cookies fallback failed: %s', exc)


def switch_sso_cookie(page, sso_cookie):
    """Replace browser SSO while preserving Cloudflare cookies when possible."""
    clear_sso_cookies(page)
    inject_sso_cookie(page, sso_cookie)


def _safe_run_js(page, script, *args, timeout=20, default=None):
    """Run page JS without crashing the whole activation on refresh/disconnect."""
    try:
        result = page.run_js(script, *args, timeout=timeout)
        return default if result is None else result
    except Exception as exc:
        logger.warning('page.run_js failed (will continue with fallback): %s', exc)
        return default if default is not None else {}


def activate_grok_web(browser, sso_cookie, timeout=60, reuse_cloudflare=True):
    page = browser.page
    sso = (sso_cookie or '').strip()
    if not sso:
        return ActivationContext(False, 'SSO cookie is empty')

    try:
        existing_cf, _ = _extract_browser_context(page)
    except Exception:
        existing_cf = ''
    has_cf = 'cf_clearance=' in existing_cf

    logger.info('Switching SSO cookie before Grok Web activation...')
    switch_sso_cookie(page, sso)

    # Establish accounts.x.ai session first so grok.com can recognize the identity.
    try:
        page.get('https://accounts.x.ai/')
        time.sleep(1.5)
    except Exception as exc:
        logger.warning('accounts.x.ai warmup failed: %s', exc)

    if reuse_cloudflare and has_cf:
        logger.info('Reusing existing Cloudflare clearance while opening grok.com...')
    else:
        logger.info('Opening grok.com to establish the Web Cloudflare session...')
    try:
        page.get('https://grok.com/')
    except Exception as exc:
        logger.warning('grok.com navigation warning: %s', exc)
        try:
            if hasattr(browser, 'refresh_active_page'):
                browser.refresh_active_page()
                page = browser.page
            page.get('https://grok.com/')
        except Exception as retry_exc:
            return ActivationContext(False, f'failed to open grok.com: {retry_exc}')

    # Allow a few precise auto clicks (shadow-root path). timeout<=0 only removes the deadline.
    if not _wait_for_grok(page, timeout=timeout, auto_click=True):
        return ActivationContext(False, 'grok.com Cloudflare challenge did not complete')

    # Ensure SSO is present after navigation (some navigations drop host-only cookies).
    inject_sso_cookie(page, sso)
    time.sleep(0.8)

    try:
        cloudflare_cookies, user_agent = _extract_browser_context(page)
    except Exception as exc:
        logger.warning('Failed to extract browser context: %s', exc)
        cloudflare_cookies, user_agent = '', ''
    if 'cf_clearance=' not in cloudflare_cookies:
        return ActivationContext(
            False,
            'grok.com opened but no cf_clearance cookie was issued',
            user_agent=user_agent,
        )

    birth = _safe_run_js(
        page,
        r"""
const birthDate = String(arguments[0] || '');
return fetch('/rest/auth/set-birth-date', {
  method: 'POST', credentials: 'include',
  headers: {'content-type': 'application/json'},
  body: JSON.stringify({birthDate}),
}).then(function(response) {
  return response.text().then(function(text) {
    return {status: response.status, body: String(text || '').slice(0, 120)};
  });
}).catch(function(err) {
  return {status: 0, body: String(err)};
});
        """,
        _birth_date(),
        timeout=20,
        default={},
    )

    session = requests.Session(impersonate='chrome')
    session.headers.update({'user-agent': user_agent or 'Mozilla/5.0'})
    session.cookies.set('sso', sso, domain='.x.ai')
    for part in cloudflare_cookies.split(';'):
        name, _, value = part.strip().partition('=')
        if name and value:
            session.cookies.set(name, value, domain='.grok.com')
    try:
        tos_ok = _set_tos(session)
    except Exception as exc:
        logger.warning('TOS request failed: %s', exc)
        tos_ok = False

    # Avoid top-level await (DrissionPage / page refresh fragile). Promise chain only.
    probe = _safe_run_js(
        page,
        r"""
return fetch('/rest/app-chat/conversations?pageSize=1', {credentials: 'include'})
  .then(function(response) { return {status: response.status, path: '/rest/app-chat/conversations'}; })
  .catch(function() {
    return fetch('/', {credentials: 'include'})
      .then(function(response) { return {status: response.status, path: '/'}; })
      .catch(function(err) { return {status: 0, path: '/', error: String(err)}; });
  });
        """,
        timeout=20,
        default={},
    )

    session_state = _safe_run_js(
        page,
        r"""
const href = location.href || '';
const body = String((document.body && document.body.innerText) || '').toLowerCase();
const title = String(document.title || '').toLowerCase();
const challenge = title.includes('just a moment')
  || body.includes('verifying you are human')
  || body.includes('performing security verification');
const signedOut = body.includes('sign in') || body.includes('log in') || href.includes('sign-in');
return {
  href: href,
  challenge: !!challenge,
  signedOut: !!signedOut,
  hasAppShell: !!(document.querySelector('main, [data-testid], nav, header')),
};
        """,
        timeout=10,
        default={},
    )

    probe_status = int(probe.get('status', 0) or 0)
    probe_ok = 200 <= probe_status < 400 and probe_status != 501
    birth_status = int(birth.get('status', 0) or 0)
    # Birth date may already be set or endpoint may no-op; treat 2xx/4xx(already) as soft.
    birth_ok = 200 <= birth_status < 300 or birth_status in {400, 409, 422}
    session_ok = (
        not session_state.get('challenge')
        and not session_state.get('signedOut')
        and 'grok.com' in str(session_state.get('href', '')).lower()
    )

    has_cf = 'cf_clearance=' in cloudflare_cookies
    # Core success for historical accounts: TOS accepted + CF context ready + session usable.
    # If TOS request fails transiently but session is clearly signed-in on grok.com with CF,
    # still treat as ready so batch reactivation can progress.
    ready = bool(has_cf and ((tos_ok and (probe_ok or session_ok)) or (session_ok and birth_ok)))
    message = (
        f'Web activation probe={probe_status} tos={tos_ok} birth={birth_ok} '
        f'session_ok={session_ok} cf={has_cf}'
    )
    if not ready:
        logger.warning('Activation details: probe=%s birth=%s session=%s', probe, birth, session_state)
    return ActivationContext(ready, message, user_agent=user_agent, cloudflare_cookies=cloudflare_cookies)
