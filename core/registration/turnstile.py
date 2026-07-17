"""Turnstile providers for the protocol registration path."""

from __future__ import annotations

import logging
import os
import time
from typing import Any
from urllib.parse import quote, urlparse

import requests

logger = logging.getLogger('register')


class TurnstileSolveError(RuntimeError):
    """Could not obtain a Turnstile token."""


def resolve_turnstile_settings(settings=None) -> dict[str, str]:
    """Resolve external Turnstile solver settings from DB / env."""
    settings = settings or {}
    yescaptcha_key = (
        str(settings.get('yescaptcha_key', '') or '').strip()
        or str(os.environ.get('YESCAPTCHA_KEY', '') or '').strip()
        or str(os.environ.get('GROK_REGISTER_YESCAPTCHA_KEY', '') or '').strip()
    )
    solver_url = (
        str(settings.get('turnstile_solver_url', '') or '').strip()
        or str(os.environ.get('TURNSTILE_SOLVER_URL', '') or '').strip()
        or str(os.environ.get('GROK_REGISTER_TURNSTILE_SOLVER_URL', '') or '').strip()
        or 'http://127.0.0.1:5072'
    )
    mode = (
        str(settings.get('turnstile_provider', '') or '').strip().lower()
        or str(os.environ.get('GROK_REGISTER_TURNSTILE_PROVIDER', '') or '').strip().lower()
        or 'auto'
    )
    allow_fallback_raw = (
        str(settings.get('allow_browser_fallback', '') or '').strip().lower()
        or str(os.environ.get('GROK_REGISTER_ALLOW_BROWSER_FALLBACK', '') or '').strip().lower()
    )
    # external / strict_external / yescaptcha / solver: no Chrome fallback by default.
    if allow_fallback_raw in {'0', 'false', 'no', 'off'}:
        allow_browser_fallback = 'false'
    elif allow_fallback_raw in {'1', 'true', 'yes', 'on'}:
        allow_browser_fallback = 'true'
    elif mode in {'external', 'strict_external', 'strict', 'yescaptcha', 'solver'}:
        allow_browser_fallback = 'false'
    else:
        allow_browser_fallback = 'true'

    from core.registration.backend import resolve_protocol_proxy

    proxy = resolve_protocol_proxy(settings)
    return {
        'yescaptcha_key': yescaptcha_key,
        'solver_url': solver_url.rstrip('/'),
        'mode': mode or 'auto',
        'allow_browser_fallback': allow_browser_fallback,
        'proxy': proxy,
    }


def parse_proxy_for_yescaptcha(proxy_url: str) -> dict[str, str] | None:
    """Map ``http://user:pass@host:port`` into YesCaptcha TurnstileTask fields."""
    proxy_url = (proxy_url or '').strip()
    if not proxy_url:
        return None
    try:
        parsed = urlparse(proxy_url)
    except Exception:
        return None
    host = (parsed.hostname or '').strip()
    if not host:
        return None
    port = parsed.port
    if port is None:
        scheme = (parsed.scheme or 'http').lower()
        port = 443 if scheme == 'https' else 80
    scheme = (parsed.scheme or 'http').lower()
    if scheme in {'socks5', 'socks5h'}:
        proxy_type = 'socks5'
    elif scheme in {'socks4', 'socks4a'}:
        proxy_type = 'socks4'
    else:
        proxy_type = 'http'
    out = {
        'proxyType': proxy_type,
        'proxyAddress': host,
        'proxyPort': str(int(port)),
    }
    if parsed.username:
        out['proxyLogin'] = parsed.username
    if parsed.password:
        out['proxyPassword'] = parsed.password
    return out


class ExternalTurnstileProvider:
    """Solve Turnstile via YesCaptcha or a local HTTP solver (Asset/grok1 style).

    Does not open the registration browser. Suitable for pure-HTTP protocol workers
    on servers without a desktop session. When a registration proxy is configured,
    YesCaptcha uses ``TurnstileTask`` (with proxy) so the challenge is solved on
    the same egress as the signup session.
    """

    def __init__(
        self,
        *,
        yescaptcha_key: str = '',
        solver_url: str = 'http://127.0.0.1:5072',
        proxy: str = '',
        timeout: int = 90,
        poll_interval: float = 2.0,
    ):
        self.yescaptcha_key = (yescaptcha_key or '').strip()
        self.solver_url = (solver_url or 'http://127.0.0.1:5072').rstrip('/')
        self.proxy = (proxy or '').strip()
        self.timeout = max(15, int(timeout or 90))
        self.poll_interval = max(0.5, float(poll_interval or 2.0))
        self._http = requests.Session()

    @classmethod
    def from_settings(cls, settings=None, *, timeout: int = 90) -> 'ExternalTurnstileProvider | None':
        cfg = resolve_turnstile_settings(settings)
        mode = cfg['mode']
        if mode in {'browser', 'none', 'off', 'disabled'}:
            return None
        if cfg['yescaptcha_key'] or mode in {
            'external', 'strict_external', 'strict', 'yescaptcha', 'solver', 'auto',
        }:
            return cls(
                yescaptcha_key=cfg['yescaptcha_key'],
                solver_url=cfg['solver_url'],
                proxy=cfg.get('proxy') or '',
                timeout=timeout,
            )
        return None

    @property
    def name(self) -> str:
        if self.yescaptcha_key:
            return 'yescaptcha+proxy' if self.proxy else 'yescaptcha'
        return 'local_solver'

    def available(self) -> bool:
        if self.yescaptcha_key:
            return True
        try:
            resp = self._http.get(f'{self.solver_url}/', timeout=2)
            return resp.status_code < 500
        except Exception:
            return False

    def solve(self, *, url: str, site_key: str, session: requests.Session) -> str:
        # Prefer proxy from the registration session when the constructor had none.
        if not self.proxy and session is not None:
            try:
                proxies = getattr(session, 'proxies', None) or {}
                self.proxy = str(
                    proxies.get('https') or proxies.get('http') or ''
                ).strip()
            except Exception:
                pass
        if not site_key:
            raise TurnstileSolveError('missing Turnstile site_key')
        website = (url or 'https://accounts.x.ai').strip()
        if self.yescaptcha_key:
            token = self._solve_yescaptcha(website, site_key)
        else:
            token = self._solve_local(website, site_key)
        if not token or token == 'CAPTCHA_FAIL':
            raise TurnstileSolveError(f'{self.name} returned empty Turnstile token')
        logger.info(
            '[protocol] Turnstile solved via %s len=%s proxy=%s',
            self.name, len(token), 'yes' if self.proxy else 'no',
        )
        return token

    def create_castle_token(self) -> str:
        # Pure HTTP path intentionally skips Castle (Asset/grok1 does not send it).
        return ''

    def _solve_yescaptcha(self, website: str, site_key: str) -> str:
        proxy_fields = parse_proxy_for_yescaptcha(self.proxy)
        if proxy_fields:
            task = {
                'type': 'TurnstileTask',
                'websiteURL': website,
                'websiteKey': site_key,
                **proxy_fields,
            }
        else:
            task = {
                'type': 'TurnstileTaskProxyless',
                'websiteURL': website,
                'websiteKey': site_key,
            }
        create = self._http.post(
            'https://api.yescaptcha.com/createTask',
            json={
                'clientKey': self.yescaptcha_key,
                'task': task,
            },
            timeout=30,
        )
        create.raise_for_status()
        data = create.json()
        if data.get('errorId') not in (0, None):
            raise TurnstileSolveError(
                f'YesCaptcha createTask failed: {data.get("errorDescription") or data}'
            )
        task_id = data.get('taskId')
        if not task_id:
            raise TurnstileSolveError('YesCaptcha createTask returned no taskId')

        deadline = time.time() + self.timeout
        time.sleep(min(5.0, self.timeout / 3))
        while time.time() < deadline:
            result = self._http.post(
                'https://api.yescaptcha.com/getTaskResult',
                json={'clientKey': self.yescaptcha_key, 'taskId': task_id},
                timeout=30,
            )
            result.raise_for_status()
            payload = result.json()
            if payload.get('errorId') not in (0, None):
                raise TurnstileSolveError(
                    f'YesCaptcha getTaskResult failed: {payload.get("errorDescription") or payload}'
                )
            if payload.get('status') == 'ready':
                token = (payload.get('solution') or {}).get('token') or ''
                return str(token).strip()
            time.sleep(self.poll_interval)
        raise TurnstileSolveError(f'YesCaptcha timed out after {self.timeout}s')

    def _solve_local(self, website: str, site_key: str) -> str:
        create_url = (
            f'{self.solver_url}/turnstile'
            f'?url={quote(website, safe="")}'
            f'&sitekey={quote(site_key, safe="")}'
        )
        if self.proxy:
            create_url += f'&proxy={quote(self.proxy, safe="")}'
        try:
            create = self._http.get(create_url, timeout=30)
            create.raise_for_status()
            task_id = (create.json() or {}).get('taskId')
        except Exception as exc:
            raise TurnstileSolveError(f'local solver create failed: {exc}') from exc
        if not task_id:
            raise TurnstileSolveError('local solver returned no taskId')

        deadline = time.time() + self.timeout
        time.sleep(min(5.0, self.timeout / 3))
        while time.time() < deadline:
            try:
                result = self._http.get(
                    f'{self.solver_url}/result?id={quote(str(task_id), safe="")}',
                    timeout=20,
                )
                result.raise_for_status()
                payload = result.json() or {}
                token = (payload.get('solution') or {}).get('token')
                if token:
                    return str(token).strip()
            except Exception as exc:
                logger.debug('[protocol] local solver poll error: %s', exc)
            time.sleep(self.poll_interval)
        raise TurnstileSolveError(f'local solver timed out after {self.timeout}s')


class BrowserTurnstileProvider:
    """Solve Turnstile via the existing headful Chrome + turnstilePatch path.

    The protocol session must share the same proxy/egress as the browser so the
    resulting token is accepted by xAI.
    """

    def __init__(self, browser_mgr, *, auto: bool = True, timeout: int = 45):
        self.browser = browser_mgr
        self.auto = auto
        self.timeout = max(10, int(timeout or 45))
        self._started = False

    def ensure_started(self, *, headless: bool = False, proxy: str = '') -> None:
        if proxy:
            self.browser.proxy = (proxy or '').strip()
        self.browser.headless = bool(headless)
        if self._started and self.browser.page is not None:
            return
        self.browser.start()
        self._started = True

    def stop(self) -> None:
        if not self._started:
            return
        try:
            self.browser.stop()
        except Exception:
            pass
        self._started = False

    def bootstrap_signup(self, signup_url: str) -> dict[str, Any]:
        """Open the signup page and return HTML/scripts/cookies for discovery."""
        self.ensure_started()
        logger.info('[protocol] browser bootstrap: opening signup page')
        self.browser.get(signup_url)
        deadline = time.time() + self.timeout
        html = ''
        while time.time() < deadline:
            try:
                html = self.browser.run_js(
                    'return document.documentElement ? document.documentElement.outerHTML : ""'
                ) or ''
            except Exception:
                html = ''
            lower = html.lower()
            # RSC payloads use escaped quotes: \"sitekey\":\"0x4...\"
            if 'sitekey' in lower or '0x4aaaa' in lower or 'sign up with email' in lower:
                # Give the app router a moment to finish chunk loads.
                time.sleep(1.5)
                try:
                    html = self.browser.run_js(
                        'return document.documentElement ? document.documentElement.outerHTML : ""'
                    ) or html
                except Exception:
                    pass
                break
            if 'just a moment' in lower or 'verifying you are human' in lower:
                time.sleep(1.0)
                continue
            time.sleep(0.5)

        script_urls = []
        try:
            script_urls = self.browser.run_js(
                "return Array.from(document.scripts).map(s => s.src)"
                ".filter(s => s && s.includes('/_next/static'))"
            ) or []
        except Exception:
            script_urls = []

        script_texts = []
        # Prefer chunks that historically hold Server Actions / signup.
        ranked = sorted(
            script_urls,
            key=lambda u: (
                0 if any(k in u for k in ('sign', 'auth', 'page', '125d', '0i6j')) else 1,
                len(u),
            ),
        )
        for src in ranked[:40]:
            try:
                text = self.browser.run_js(
                    "return fetch(arguments[0], {credentials:'include'})"
                    ".then(r => r.ok ? r.text() : '').catch(() => '')",
                    src,
                )
            except Exception:
                text = ''
            if isinstance(text, str) and len(text) > 50:
                script_texts.append(text)

        cookies = self._export_cookies()
        logger.info(
            '[protocol] bootstrap ready html_len=%s scripts=%s cookies=%s',
            len(html or ''), len(script_texts), len(cookies),
        )
        return {
            'html': html,
            'script_texts': script_texts,
            'cookies': cookies,
            'url': signup_url,
        }

    def solve(self, *, url: str, site_key: str, session: requests.Session) -> str:
        """Return a Turnstile token, syncing browser cookies into ``session``."""
        self.ensure_started()
        logger.info('[protocol] solving Turnstile via browser')
        current = ''
        try:
            current = self.browser.page.url if self.browser.page else ''
        except Exception:
            current = ''
        if not current or 'accounts.x.ai' not in current:
            self.browser.get(url)
            time.sleep(1.5)

        # Landing page often has no widget until the email form/profile step.
        # Inject a same-origin Turnstile render so protocol submit can proceed.
        if site_key:
            self._ensure_widget(site_key)

        if not self.auto:
            logger.info('[protocol] Turnstile manual mode — waiting %ss', self.timeout)
            time.sleep(min(self.timeout, 30))
            token = self._read_token()
            if token:
                apply_count = self._sync_cookies(session)
                logger.info('[protocol] Turnstile manual token len=%s cookies=%s', len(token), apply_count)
                return token
            raise TurnstileSolveError('manual Turnstile wait elapsed without token')

        deadline = time.time() + self.timeout
        last_log = 0.0
        while time.time() < deadline:
            token = self._read_token()
            if token:
                apply_count = self._sync_cookies(session)
                logger.info(
                    '[protocol] Turnstile solved len=%s cookies_synced=%s',
                    len(token), apply_count,
                )
                return token
            self._nudge_widget()
            now = time.time()
            if now - last_log >= 8:
                logger.info('[protocol] Turnstile still pending…')
                last_log = now
            time.sleep(1.0)
        raise TurnstileSolveError(f'Turnstile solve timed out after {self.timeout}s')

    def _ensure_widget(self, site_key: str) -> None:
        """Render a same-origin Turnstile widget when the page has none yet."""
        try:
            self.browser.run_js(
                """
const siteKey = arguments[0];
if (!siteKey) return 'no-key';
if (document.querySelector('.cf-turnstile, [data-sitekey], input[name="cf-turnstile-response"]')) {
  return 'exists';
}
function mount() {
  let host = document.getElementById('protocol-turnstile-host');
  if (!host) {
    host = document.createElement('div');
    host.id = 'protocol-turnstile-host';
    host.style.cssText = 'position:fixed;right:12px;bottom:12px;z-index:2147483647;';
    document.body.appendChild(host);
  }
  host.innerHTML = '';
  const box = document.createElement('div');
  box.className = 'cf-turnstile';
  box.setAttribute('data-sitekey', siteKey);
  host.appendChild(box);
  try {
    if (window.turnstile && turnstile.render) {
      turnstile.render(box, {sitekey: siteKey, theme: 'light'});
      return 'rendered';
    }
  } catch (e) {}
  return 'mounted';
}
if (window.turnstile) return mount();
let s = document.querySelector('script[src*="challenges.cloudflare.com/turnstile"]');
if (!s) {
  s = document.createElement('script');
  s.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
  s.async = true;
  s.onload = () => { try { mount(); } catch (e) {} };
  document.head.appendChild(s);
  return 'loading-api';
}
return mount();
                """,
                site_key,
            )
            time.sleep(1.0)
        except Exception as exc:
            logger.debug('[protocol] turnstile inject failed: %s', exc)

    def _read_token(self) -> str:
        try:
            token = self.browser.run_js(
                "try { return turnstile.getResponse() || '' } catch (e) {"
                "  const ci = document.querySelector('input[name=\"cf-turnstile-response\"]');"
                "  return ci ? String(ci.value || '') : '';"
                "}"
            )
            return str(token or '').strip()
        except Exception:
            return ''

    def _nudge_widget(self) -> None:
        try:
            self.browser.run_js(
                """
const box = document.querySelector('.cf-turnstile, .turnstile, [data-sitekey]');
if (box) {
  box.scrollIntoView({behavior: 'smooth', block: 'center'});
  const rect = box.getBoundingClientRect();
  box.dispatchEvent(new MouseEvent('click', {
    bubbles: true,
    clientX: rect.left + rect.width / 2,
    clientY: rect.top + rect.height / 2,
  }));
}
                """
            )
        except Exception:
            pass
        try:
            challenge_solution = self.browser.page.ele('@name=cf-turnstile-response', timeout=1)
            challenge_wrapper = challenge_solution.parent()
            challenge_iframe = challenge_wrapper.shadow_root.ele('tag:iframe', timeout=1)
            challenge_iframe_body = challenge_iframe.ele('tag:body', timeout=1).shadow_root
            challenge_button = challenge_iframe_body.ele('tag:input', timeout=1)
            challenge_button.click()
        except Exception:
            pass

    def _export_cookies(self) -> list[dict]:
        try:
            page = self.browser.page
            if not page:
                return []
            raw = page.run_cdp('Network.getAllCookies') or {}
            return list(raw.get('cookies') or [])
        except Exception as exc:
            logger.debug('[protocol] cookie export failed: %s', exc)
            return []

    def _sync_cookies(self, session: requests.Session) -> int:
        from core.registration.backend import apply_cookies_to_session

        return apply_cookies_to_session(session, self._export_cookies())

    def create_castle_token(self) -> str:
        """Load Castle.js if needed and return a request token."""
        self.ensure_started()
        try:
            result = self.browser.run_js(
                """
return (async () => {
  const pk = 'pk_p8GGWvD3TmFJZRsX3BQcqAv9aFVispNz';
  if (!window.__protocolCastleReady) {
    await new Promise((resolve) => {
      const existing = document.querySelector('script[data-protocol-castle]');
      if (existing) { existing.addEventListener('load', () => resolve()); resolve(); return; }
      const s = document.createElement('script');
      s.src = 'https://cdn.castle.io/v2/castle.js?key=' + pk;
      s.async = true;
      s.dataset.protocolCastle = '1';
      s.onload = () => resolve();
      s.onerror = () => resolve();
      document.head.appendChild(s);
      setTimeout(resolve, 2500);
    });
    window.__protocolCastleReady = true;
  }
  try {
    if (typeof _castle === 'function') {
      const token = await _castle('createRequestToken');
      return String(token || '');
    }
  } catch (e) {}
  try {
    if (window.Castle && Castle.createRequestToken) {
      return String(await Castle.createRequestToken() || '');
    }
  } catch (e) {}
  return '';
})();
                """
            )
            token = str(result or '').strip()
            if token:
                logger.info('[protocol] castle token len=%s', len(token))
            else:
                logger.warning('[protocol] castle token empty')
            return token
        except Exception as exc:
            logger.warning('[protocol] castle token failed: %s', exc)
            return ''

    def navigate_for_sso(self, url: str, *, timeout: int = 20) -> str:
        """Navigate the browser through nested set-cookie hops and return SSO."""
        self.ensure_started()
        hops = self._expand_set_cookie_chain(url)
        # Skip terminal error pages in the chain.
        hops = [h for h in hops if 'auth-error' not in h]
        logger.info(
            '[protocol] navigating for SSO hops=%s first=%s',
            len(hops), (hops[0] if hops else '')[:120],
        )
        before = {
            str(item.get('name')): str(item.get('value') or '')
            for item in self._export_cookies()
            if item.get('name')
        }
        sso = ''
        for hop in hops:
            try:
                self.browser.get(hop)
            except Exception as exc:
                logger.warning('[protocol] SSO hop failed: %s (%s)', hop[:100], exc)
                continue
            hop_deadline = time.time() + max(4, min(12, int(timeout or 20) // max(1, len(hops))))
            while time.time() < hop_deadline:
                sso = self._find_sso_cookie()
                if sso:
                    break
                try:
                    current = self.browser.page.url if self.browser.page else ''
                except Exception:
                    current = ''
                if current and current != hop and (
                    'set-cookie' in current
                    or any(h in current for h in ('grok.com', 'x.ai', 'grokusercontent', 'grokipedia'))
                ):
                    time.sleep(0.4)
                    continue
                time.sleep(0.4)
            if sso:
                break

        if sso:
            logger.info('[protocol] SSO cookie found after navigate (len=%s)', len(sso))
        else:
            names = sorted({str(c.get('name')) for c in self._export_cookies()})
            try:
                current = self.browser.page.url if self.browser.page else ''
            except Exception:
                current = ''
            logger.warning(
                '[protocol] no SSO after navigate; cookies=%s url=%s',
                names[:20], current[:160],
            )

        try:
            from config import SIGNUP_URL
            self.browser.get(SIGNUP_URL)
            time.sleep(0.8)
        except Exception as exc:
            logger.debug('[protocol] restore signup page after SSO nav failed: %s', exc)

        if not sso:
            after = {
                str(item.get('name')): str(item.get('value') or '')
                for item in self._export_cookies()
                if item.get('name')
            }
            for key in ('sso', 'sso-rw', 'sso_token', 'session'):
                if after.get(key) and after.get(key) != before.get(key):
                    sso = after[key]
                    break
        return sso or self._find_sso_cookie()

    @staticmethod
    def _expand_set_cookie_chain(url: str) -> list[str]:
        """Decode nested set-cookie JWT success_url hops into an ordered list."""
        from core.registration.backend import expand_set_cookie_chain

        return expand_set_cookie_chain(url)

    def _find_sso_cookie(self) -> str:
        preferred = ('sso', 'sso-rw', 'sso_token')
        cookies = self._export_cookies()
        by_name = {}
        for item in cookies:
            name = str(item.get('name') or '')
            value = str(item.get('value') or '').strip()
            if name and value:
                by_name[name] = value
        for name in preferred:
            if by_name.get(name):
                return by_name[name]
        # Heuristic: long cookie on x.ai / grok domains often is the session.
        for item in cookies:
            name = str(item.get('name') or '')
            domain = str(item.get('domain') or '')
            value = str(item.get('value') or '').strip()
            if not value or len(value) < 40:
                continue
            if 'sso' in name.lower() and any(d in domain for d in ('x.ai', 'grok', 'grokipedia')):
                return value
        try:
            raw = self.browser.run_js("return document.cookie || ''") or ''
            for part in str(raw).split(';'):
                part = part.strip()
                if part.startswith('sso='):
                    return part[4:].strip()
        except Exception:
            pass
        return ''

    def fetch(
        self,
        url: str,
        *,
        method: str = 'GET',
        headers: dict | None = None,
        data: bytes | str | None = None,
        timeout: int = 30,
    ) -> 'BrowserFetchResponse':
        """Issue a same-browser-context HTTP request (inherits CF cookies/UA)."""
        self.ensure_started()
        method = (method or 'GET').upper()
        headers = dict(headers or {})
        body_b64 = ''
        if data is not None:
            import base64
            if isinstance(data, str):
                raw = data.encode('utf-8')
            else:
                raw = bytes(data)
            body_b64 = base64.b64encode(raw).decode('ascii')

        # Avoid sending hop-by-hop / forbidden fetch headers from page JS.
        for drop in ('host', 'content-length', 'connection'):
            headers.pop(drop, None)
            headers.pop(drop.title(), None)

        result = self.browser.run_js(
            """
const url = arguments[0];
const method = arguments[1];
const headers = arguments[2] || {};
const bodyB64 = arguments[3] || '';
const timeoutMs = Math.max(1000, Number(arguments[4] || 30000));
function b64ToUint8(b64) {
  if (!b64) return null;
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}
const controller = new AbortController();
const timer = setTimeout(() => controller.abort(), timeoutMs);
const init = {method, headers, credentials: 'include', signal: controller.signal};
const body = b64ToUint8(bodyB64);
if (body) init.body = body;
return fetch(url, init).then(async (resp) => {
  clearTimeout(timer);
  const buf = new Uint8Array(await resp.arrayBuffer());
  let binary = '';
  const chunk = 0x8000;
  for (let i = 0; i < buf.length; i += chunk) {
    binary += String.fromCharCode.apply(null, buf.subarray(i, i + chunk));
  }
  const bodyB64Out = btoa(binary);
  const headerMap = {};
  resp.headers.forEach((v, k) => { headerMap[k] = v; });
  return {
    ok: resp.ok,
    status: resp.status,
    url: resp.url,
    headers: headerMap,
    body_b64: bodyB64Out,
  };
}).catch((e) => {
  clearTimeout(timer);
  return {ok:false, status:0, url, headers:{}, body_b64:'', error:String(e)};
});
            """,
            url,
            method,
            headers,
            body_b64,
            int(timeout * 1000),
        )
        if not isinstance(result, dict):
            raise TurnstileSolveError(f'browser fetch returned invalid payload: {type(result)!r}')
        if result.get('error') and not result.get('status'):
            raise TurnstileSolveError(f'browser fetch failed: {result.get("error")}')
        return BrowserFetchResponse(result)


class BrowserFetchResponse:
    """Minimal response object compatible with protocol backend helpers."""

    def __init__(self, payload: dict):
        import base64

        self.status_code = int(payload.get('status') or 0)
        self.url = str(payload.get('url') or '')
        self.headers = payload.get('headers') or {}
        body_b64 = payload.get('body_b64') or ''
        try:
            self.content = base64.b64decode(body_b64) if body_b64 else b''
        except Exception:
            self.content = b''
        try:
            self.text = self.content.decode('utf-8', errors='replace')
        except Exception:
            self.text = ''
        self.cookies = requests.cookies.RequestsCookieJar()
        self.ok = bool(payload.get('ok')) or (200 <= self.status_code < 300)
