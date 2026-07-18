"""Registration backends and challenge-provider interfaces.

The browser backend remains the default in :mod:`core.register`.  This module
contains the protocol-facing seams so deployments can add a non-browser
transport without coupling it to the worker lifecycle.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Mapping, Protocol
from urllib.parse import urljoin, urlparse

import requests

logger = logging.getLogger("register")


class ExistingAccountActionError(RuntimeError):
    """Server Action reported that the email already belongs to an account."""


DEFAULT_PROTOCOL_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/110.0.0.0 Safari/537.36"
)

# Prefer older Chrome / Edge impersonations that currently pass Cloudflare on
# accounts.x.ai. Newer chrome131-style profiles are frequently 403'd.
PROTOCOL_IMPERSONATE_CANDIDATES = (
    'chrome110',
    'chrome119',
    'chrome116',
    'chrome104',
    'chrome101',
    'edge101',
    'chrome120',
    'chrome124',
    'chrome131',
    'chrome',
)

TRUSTED_SSO_HOSTS = frozenset({
    'accounts.x.ai',
    'auth.x.ai',
    'auth.grok.com',
    'auth.grokipedia.com',
})

# Full app-router tree used by the working Asset/grok1 pure-HTTP client.
DEFAULT_SIGNUP_STATE_TREE = (
    '["",{"children":["(app)",{"children":["(auth)",{"children":["sign-up",'
    '{"children":["__PAGE__",{},"/sign-up","refresh"]}]},null,null]},'
    'null,null]},null,null,true]'
)


class ProtocolEnvironmentError(RuntimeError):
    """Signup page or protocol session was blocked before a real attempt."""

    def __init__(self, message, *, reason="blocked", diagnostics=""):
        super().__init__(message)
        self.reason = reason
        self.diagnostics = diagnostics or message


class TurnstileProvider(Protocol):
    """Resolve a Turnstile challenge for a registration request."""

    def solve(self, *, url: str, site_key: str, session: requests.Session) -> str:
        ...


class RegistrationBackend(Protocol):
    """Common transport contract consumed by a registration worker."""

    def send_email_code(self, email: str, signup_url: str) -> None: ...
    def verify_email_code(self, email: str, code: str, signup_url: str) -> None: ...

    def submit_signup(self, payload: Mapping[str, Any], signup_url: str, turnstile_token: str) -> requests.Response: ...

    def extract_sso(self, response: requests.Response) -> RegistrationResult: ...


@dataclass(frozen=True)
class RegistrationResult:
    """Secret-safe result returned by a transport after signup submission."""

    sso: str
    response_url: str = ''


class BrowserRegistrationBackend:
    """Marker adapter for the legacy browser implementation.

    The existing ``RegistrationEngine`` remains the owner of browser actions;
    this adapter intentionally does not duplicate that large workflow. It is
    useful to factories and dependency injection code that need to distinguish
    the legacy transport from the protocol transport.
    """

    name = 'browser'


def build_registration_backend(mode: str, *, session=None, params=None):
    """Build a transport without changing the legacy browser default."""
    normalized = str(mode or 'browser').strip().lower()
    if normalized == 'browser':
        return BrowserRegistrationBackend()
    if normalized == 'protocol':
        if session is None or params is None:
            raise ValueError('protocol backend requires an HTTP session and signup parameters')
        return ProtocolRegistrationBackend(session, params)
    raise ValueError(f'unsupported registration backend: {mode}')


def resolve_protocol_proxy(settings=None) -> str:
    """Resolve protocol egress: settings → GROK_PROXY → HTTP(S)_PROXY."""
    import os

    settings = settings or {}
    for candidate in (
        str(settings.get('browser_proxy', '') or '').strip(),
        str(os.environ.get('GROK_PROXY', '') or '').strip(),
        str(os.environ.get('HTTPS_PROXY', '') or os.environ.get('https_proxy', '') or '').strip(),
        str(os.environ.get('HTTP_PROXY', '') or os.environ.get('http_proxy', '') or '').strip(),
    ):
        if candidate:
            return candidate
    return ''


def build_protocol_session(
    settings=None,
    *,
    user_agent: str = '',
    impersonate: str = '',
) -> requests.Session:
    """Create an HTTP session aligned with the browser egress settings.

    Prefers curl_cffi Chrome impersonation profiles known to pass Cloudflare on
    accounts.x.ai. Proxy comes from ``browser_proxy`` / ``GROK_PROXY`` so the
    protocol path can share the same network path as the browser when needed.
    """
    settings = settings or {}
    proxy = resolve_protocol_proxy(settings)
    ua = (user_agent or DEFAULT_PROTOCOL_UA).strip()
    headers = {
        'User-Agent': ua,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
    }

    session = None
    chosen_profile = ''
    try:
        from curl_cffi import requests as creq

        preferred = []
        if impersonate:
            preferred.append(str(impersonate).strip())
        preferred.extend(PROTOCOL_IMPERSONATE_CANDIDATES)
        seen = set()
        for profile in preferred:
            if not profile or profile in seen:
                continue
            seen.add(profile)
            try:
                session = creq.Session(impersonate=profile)
                chosen_profile = profile
                logger.info('[protocol] session impersonate=%s', profile)
                break
            except Exception:
                session = None
        if session is None:
            session = creq.Session()
            chosen_profile = 'default'
    except Exception as exc:
        logger.info('[protocol] curl_cffi unavailable (%s); using requests', type(exc).__name__)
        session = requests.Session()

    session.headers.update(headers)
    # Stamp the chosen profile so workers/tests can inspect it.
    try:
        session._protocol_impersonate = chosen_profile  # type: ignore[attr-defined]
    except Exception:
        pass
    if proxy:
        session.proxies.update({'http': proxy, 'https': proxy})
        logger.info('[protocol] session proxy configured')
    return session


def apply_cookies_to_session(session: requests.Session, cookies) -> int:
    """Copy browser cookies into a protocol session. Returns count applied."""
    applied = 0
    if not cookies:
        return applied
    items = cookies
    if isinstance(cookies, dict):
        items = [
            {'name': key, 'value': value, 'domain': '', 'path': '/'}
            for key, value in cookies.items()
        ]
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get('name') or item.get('Name') or '').strip()
        value = item.get('value', item.get('Value', ''))
        if not name or value is None:
            continue
        domain = str(item.get('domain') or item.get('Domain') or '').strip() or None
        path = str(item.get('path') or item.get('Path') or '/').strip() or '/'
        try:
            session.cookies.set(name, str(value), domain=domain, path=path)
            applied += 1
        except Exception:
            try:
                session.cookies.set(name, str(value))
                applied += 1
            except Exception:
                continue
    return applied


def clear_identity_cookies(session: requests.Session) -> int:
    """Drop SSO / identity cookies so the next round cannot reuse a prior login.

    Keep Cloudflare / anti-bot cookies (``__cf_bm``, ``cf_clearance``) so pure-HTTP
    transport still benefits from the warmed session. Returns how many cookies
    were removed.

    curl_cffi notes:
    - ``list(session.cookies)`` yields *names* (str), not Cookie objects
    - ``Cookies.delete(name)`` only removes one domain entry; SSO is stamped on
      several domains (``.x.ai``, ``x.ai``, ``.grok.com``, …)
    - Prefer walking ``session.cookies.jar`` and clearing each match by
      ``(domain, path, name)``
    """
    if session is None:
        return 0
    cookies = getattr(session, 'cookies', None)
    if cookies is None:
        return 0

    identity_names = {
        'sso',
        'sso-rw',
        'sso_token',
        'sso-token',
        'auth_token',
        'session',
        'sessionid',
    }

    def _is_identity(name: str) -> bool:
        lower = str(name or '').strip().lower()
        return bool(lower) and (lower in identity_names or lower.startswith('sso'))

    removed = 0
    jar = getattr(cookies, 'jar', None)

    # Primary path: http.cookiejar under curl_cffi / requests.
    if jar is not None:
        try:
            snapshot = list(jar)
        except Exception:
            snapshot = []
        for cookie in snapshot:
            try:
                name = str(getattr(cookie, 'name', '') or '')
            except Exception:
                name = ''
            if not _is_identity(name):
                continue
            domain = getattr(cookie, 'domain', None)
            path = getattr(cookie, 'path', None) or '/'
            try:
                jar.clear(domain, path, name)
                removed += 1
                continue
            except Exception:
                pass
            # curl_cffi Cookies.delete(name) as secondary.
            delete = getattr(cookies, 'delete', None)
            if callable(delete):
                try:
                    delete(name)
                    removed += 1
                except Exception:
                    pass

    # Mapping-style fallback when jar is missing or incomplete.
    try:
        for name in list(cookies.keys()):
            if not _is_identity(name):
                continue
            deleted = False
            delete = getattr(cookies, 'delete', None)
            if callable(delete):
                try:
                    delete(name)
                    deleted = True
                except Exception:
                    deleted = False
            if not deleted:
                try:
                    cookies.pop(name, None)
                    deleted = True
                except Exception:
                    deleted = False
            if deleted:
                removed += 1
    except Exception:
        pass

    # If any identity cookie is still readable, wipe the whole jar.
    try:
        leftover = read_sso_cookie_from_session(session)
    except Exception:
        leftover = ''
    if leftover:
        clear = getattr(cookies, 'clear', None)
        if callable(clear):
            try:
                clear()
                removed = max(removed, 1)
            except Exception:
                pass
        elif jar is not None:
            try:
                jar.clear()
                removed = max(removed, 1)
            except Exception:
                pass
    return removed


def redact_sensitive_text(text: str, *, limit: int = 400) -> str:
    """Strip JWT / set-cookie / SSO secrets before logging or WebSocket emit."""
    if not text:
        return ''
    redacted = str(text)
    redacted = re.sub(
        r'(set-cookie\?q=)[^"\s\\]+',
        r'\1[REDACTED]',
        redacted,
        flags=re.I,
    )
    redacted = re.sub(
        r'((?:"|\')?sso(?:-rw)?(?:"|\')?\s*:\s*(?:"|\'))[^"\']+',
        r'\1[REDACTED]',
        redacted,
        flags=re.I,
    )
    redacted = re.sub(
        r'((?:^|[;\s])sso(?:-rw)?=)[^;\s"&]+',
        r'\1[REDACTED]',
        redacted,
        flags=re.I,
    )
    redacted = re.sub(
        r'eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9_-]*)?',
        '[JWT]',
        redacted,
    )
    return redacted[: max(0, int(limit or 0)) or 400].replace('\n', ' ')


def is_trusted_sso_url(url: str) -> bool:
    """Allow SSO navigation only to known HTTPS authentication endpoints."""
    try:
        parsed = urlparse(str(url or '').strip())
        return bool(
            parsed.scheme.lower() == 'https'
            and (parsed.hostname or '').lower() in TRUSTED_SSO_HOSTS
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 443)
        )
    except Exception:
        return False


def expand_set_cookie_chain(url: str) -> list[str]:
    """Decode nested set-cookie JWT ``success_url`` hops into an ordered list.

    Shared by pure-HTTP SSO follow and the browser navigate fallback so both
    paths walk the same auth chain without depending on Chrome.
    """
    import base64
    from urllib.parse import parse_qs, unquote

    def b64url_json(data: str):
        pad = '=' * (-len(data) % 4)
        raw = base64.urlsafe_b64decode((data + pad).encode('ascii'))
        return json.loads(raw.decode('utf-8'))

    ordered: list[str] = []
    initial = str(url or '').strip()
    queue = [initial] if is_trusted_sso_url(initial) else []
    seen: set[str] = set()
    while queue:
        current = queue.pop(0)
        if not current or current in seen:
            continue
        seen.add(current)
        ordered.append(current)
        try:
            parsed = urlparse(current)
            q = parse_qs(parsed.query).get('q', [None])[0]
            if not q:
                continue
            q = unquote(q)
            payload = None
            parts = q.split('.')
            if len(parts) >= 2:
                try:
                    payload = b64url_json(parts[1])
                except Exception:
                    payload = None
            if payload is None:
                try:
                    payload = b64url_json(q)
                except Exception:
                    continue
            cfg = payload.get('config') if isinstance(payload, dict) else None
            if not isinstance(cfg, dict):
                cfg = payload if isinstance(payload, dict) else {}
            for key in ('success_url', 'successUrl'):
                nxt = cfg.get(key)
                if (
                    isinstance(nxt, str)
                    and is_trusted_sso_url(nxt)
                    and nxt not in seen
                ):
                    queue.append(nxt)
        except Exception:
            continue
    return ordered


def read_sso_cookie_from_session(session: requests.Session) -> str:
    """Best-effort SSO cookie lookup across jar variants."""
    if session is None:
        return ''
    # Prefer iterating the jar: multi-domain stamps of the same name make
    # RequestsCookieJar.get() raise CookieConflictError.
    try:
        jar = getattr(session.cookies, 'jar', None) or session.cookies
        preferred = ('sso', 'sso-rw', 'sso_token')
        by_name: dict[str, str] = {}
        for cookie in jar:
            name = str(getattr(cookie, 'name', '') or '')
            value = str(getattr(cookie, 'value', '') or '').strip()
            if name in preferred and value and name not in by_name:
                by_name[name] = value
        for name in preferred:
            if by_name.get(name):
                return by_name[name]
    except Exception:
        pass
    for name in ('sso', 'sso-rw', 'sso_token'):
        try:
            value = session.cookies.get(name) or ''
        except Exception:
            value = ''
        if value:
            return str(value).strip()
    return ''


def follow_sso_http(
    session: requests.Session,
    url: str,
    *,
    get_func=None,
    max_hops: int = 8,
) -> str:
    """Follow set-cookie / JWT success_url hops with pure HTTP (no browser).

    ``get_func`` defaults to ``session.get(..., allow_redirects=True)``. Returns
    the SSO cookie value when minted, else empty string.
    """
    if not url or session is None:
        return ''

    def _get(target: str):
        if callable(get_func):
            return get_func(target)
        return session.get(target, allow_redirects=True, timeout=20)

    hops = [
        h for h in expand_set_cookie_chain(url)
        if h and 'auth-error' not in h and is_trusted_sso_url(h)
    ]
    visited: set[str] = set()
    hop_limit = max(1, int(max_hops or 8))
    index = 0
    processed = 0
    while index < len(hops) and processed < hop_limit:
        hop = hops[index]
        index += 1
        if hop in visited:
            continue
        if not is_trusted_sso_url(hop):
            continue
        visited.add(hop)
        processed += 1
        try:
            response = _get(hop)
        except Exception as exc:
            logger.info(
                '[protocol] http sso hop failed: %s (%s)',
                redact_sensitive_text(hop, limit=100),
                type(exc).__name__,
            )
            continue
        sso = read_sso_cookie_from_session(session)
        if sso:
            logger.info('[protocol] SSO cookie found after HTTP hop (len=%s)', len(sso))
            return sso
        body = getattr(response, 'text', '') or ''
        unescaped = (
            body.replace('\\u0026', '&')
                .replace('\\/', '/')
                .replace('\\u003d', '=')
                .replace('\\"', '"')
        )
        nested = re.findall(
            r'https://[^"\s\\]+set-cookie\?q=[^"\s\\]+',
            unescaped,
            re.I,
        )
        for raw in nested:
            cleaned = re.sub(r'1:$', '', raw).rstrip('",;)}]')
            if (
                cleaned
                and is_trusted_sso_url(cleaned)
                and cleaned not in visited
            ):
                hops.append(cleaned)
                for extra in expand_set_cookie_chain(cleaned):
                    if (
                        extra
                        and is_trusted_sso_url(extra)
                        and extra not in visited
                        and 'auth-error' not in extra
                    ):
                        hops.append(extra)
    return read_sso_cookie_from_session(session)


def apply_sso_cookies(
    session: requests.Session,
    sso: str,
    *,
    domains: tuple[str, ...] = ('.x.ai', 'x.ai', '.grok.com', 'grok.com'),
) -> dict[str, str]:
    """Stamp SSO on session jar for both x.ai and grok.com, return request cookies.

    curl_cffi / requests domain scoping can drop cross-site cookies; callers
    should also pass the returned dict as ``cookies=`` on grok.com requests.
    """
    token = (sso or '').strip()
    cookie_dict = {'sso': token, 'sso-rw': token} if token else {}
    if not token or session is None:
        return cookie_dict
    for domain in domains:
        for name in ('sso', 'sso-rw'):
            try:
                session.cookies.set(name, token, domain=domain, path='/')
            except Exception:
                try:
                    session.cookies.set(name, token)
                except Exception:
                    pass
    return cookie_dict


def build_signup_payload(
    *,
    email: str,
    password: str,
    given_name: str,
    family_name: str,
    email_validation_code: str,
    turnstile_token: str = '',
    castle_request_token: str = '',
    conversion_id: str = '',
    tos_accepted_version: Any = '$undefined',
    prompt_on_duplicate_email: bool = True,
    include_conversion_id: bool = False,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize the Server Action signup body used by the protocol transport.

    Shape matches the working Asset/grok1 pure-HTTP client and the live
    accounts.x.ai client::

        {
          emailValidationCode,
          createUserAndSessionRequest: {email, givenName, familyName,
                                        clearTextPassword, tosAcceptedVersion},
          turnstileToken,
          promptOnDuplicateEmail,
          [conversionId], [castleRequestToken]
        }

    ``tosAcceptedVersion`` defaults to the literal ``"$undefined"`` used by the
    browser bundle; callers may still pass an int when a concrete version is
    known. Castle / conversionId are optional and only sent when non-empty.
    """
    import uuid

    if tos_accepted_version is None:
        tos_value: Any = '$undefined'
    elif isinstance(tos_accepted_version, str):
        tos_value = tos_accepted_version
    else:
        tos_value = int(tos_accepted_version)

    payload: dict[str, Any] = {
        'emailValidationCode': email_validation_code,
        'createUserAndSessionRequest': {
            'email': email,
            'givenName': given_name,
            'familyName': family_name,
            'clearTextPassword': password,
            'tosAcceptedVersion': tos_value,
        },
        'turnstileToken': turnstile_token,
        'promptOnDuplicateEmail': bool(prompt_on_duplicate_email),
    }
    if include_conversion_id or conversion_id:
        payload['conversionId'] = conversion_id or str(uuid.uuid4())
    if castle_request_token:
        payload['castleRequestToken'] = castle_request_token
    if extra:
        payload.update(dict(extra))
    return payload


@dataclass(frozen=True)
class SignupParameters:
    site_key: str
    state_tree: str
    action_id: str


class SignupParameterDiscovery:
    """Discover Next.js signup parameters without opening a browser."""

    _SITE_KEY = re.compile(r'["\']sitekey["\']\s*:\s*["\'](0x4[a-zA-Z0-9_-]+)["\']', re.I)
    # RSC / flight payloads escape quotes: \"sitekey\":\"0x4...\"
    _SITE_KEY_ESC = re.compile(r'\\["\']sitekey\\["\']\s*:\s*\\["\'](0x4[a-zA-Z0-9_-]+)\\["\']', re.I)
    _SITE_KEY_ATTR = re.compile(r'data-sitekey=["\'](0x4[a-zA-Z0-9_-]+)["\']', re.I)
    _STATE_TREE = re.compile(r'["\']next-router-state-tree["\']\s*:\s*["\']([^"\']+)["\']')
    _ACTION_ID = re.compile(r'(?<![a-f0-9])(7f[a-f0-9]{40})(?![a-f0-9])', re.I)
    _ACTION_CREATE_REF = re.compile(
        r'createServerReference\)\(\s*["\'](7f[a-f0-9]{40})["\']', re.I,
    )

    # Full app-router tree for /sign-up when the page does not embed it.
    # Matches Asset/grok1's working pure-HTTP default.
    DEFAULT_SIGNUP_STATE_TREE = DEFAULT_SIGNUP_STATE_TREE

    def __init__(self, session: requests.Session):
        self.session = session

    def discover(self, signup_url: str) -> SignupParameters:
        try:
            response = self.session.get(signup_url, timeout=20)
        except Exception as exc:
            raise ProtocolEnvironmentError(
                f'signup page request failed: {exc}',
                reason='request_failed',
                diagnostics=str(exc),
            ) from exc

        status = getattr(response, 'status_code', 0) or 0
        html = getattr(response, 'text', '') or ''
        if status in (401, 403, 429, 503) or self._looks_blocked(html):
            raise ProtocolEnvironmentError(
                f'signup page blocked (HTTP {status})',
                reason='blocked',
                diagnostics=f'status={status} body_len={len(html)}',
            )
        if status >= 400:
            raise ProtocolEnvironmentError(
                f'signup page returned HTTP {status}',
                reason='http_error',
                diagnostics=f'status={status}',
            )

        script_texts = []
        for script in re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html, re.I):
            if "_next/static" not in script:
                continue
            try:
                js = self.session.get(urljoin(signup_url, script), timeout=20)
            except Exception:
                continue
            if getattr(js, 'status_code', 0) == 200:
                script_texts.append(getattr(js, 'text', '') or '')
        return self.extract_from_sources(html, script_texts)

    def discover_from_html(
        self,
        html: str,
        *,
        signup_url: str = '',
        script_texts: list[str] | None = None,
    ) -> SignupParameters:
        """Parse parameters from already-loaded HTML/scripts (browser bootstrap)."""
        try:
            return self.extract_from_sources(html, script_texts or [])
        except ProtocolEnvironmentError:
            if not signup_url:
                raise
            # Supplement with network discovery once cookies are present.
            return self.discover(signup_url)

    def extract_from_sources(self, html: str, script_texts: list[str] | None = None) -> SignupParameters:
        html = html or ''
        script_texts = list(script_texts or [])
        site_key = (
            self._first(self._SITE_KEY, html)
            or self._first(self._SITE_KEY_ESC, html)
            or self._first(self._SITE_KEY_ATTR, html)
        )
        for blob in script_texts:
            if site_key:
                break
            site_key = (
                self._first(self._SITE_KEY, blob)
                or self._first(self._SITE_KEY_ESC, blob)
            )

        state_tree = self._first(self._STATE_TREE, html) or self.DEFAULT_SIGNUP_STATE_TREE
        action_id = self._find_action_id(html, *script_texts)

        if not site_key or not action_id:
            raise ProtocolEnvironmentError(
                'signup sources missing required parameters',
                reason='missing_params',
                diagnostics=(
                    f'site_key={bool(site_key)} state_tree={bool(state_tree)} '
                    f'action_id={bool(action_id)} scripts={len(script_texts)}'
                ),
            )
        logger.info(
            '[protocol] discovered site_key=%s… action_id=%s…',
            site_key[:12], action_id[:10],
        )
        return SignupParameters(site_key, state_tree, action_id)

    def _find_action_id(self, *blobs: str) -> str:
        for blob in blobs:
            if not blob:
                continue
            match = self._ACTION_CREATE_REF.search(blob)
            if match:
                return match.group(1) if match.lastindex else match.group(0)
        for blob in blobs:
            if not blob:
                continue
            match = self._ACTION_ID.search(blob)
            if match:
                return match.group(1) if match.lastindex else match.group(0)
        return ''

    @staticmethod
    def _looks_blocked(html: str) -> bool:
        text = (html or '').lower()
        markers = (
            'just a moment',
            'attention required',
            'sorry, you have been blocked',
            'cf-browser-verification',
            'cloudflare',
            'enable javascript and cookies',
        )
        # Cloudflare challenge pages are short and marker-heavy.
        if any(m in text for m in markers) and len(text) < 20000:
            # Real signup HTML also mentions cloudflare rarely; require strong markers.
            strong = (
                'just a moment',
                'attention required',
                'sorry, you have been blocked',
                'cf-browser-verification',
            )
            return any(m in text for m in strong)
        return False

    @staticmethod
    def _first(pattern: re.Pattern[str], value: str) -> str:
        match = pattern.search(value or '')
        return match.group(1) if match else ""


class ProtocolRegistrationBackend:
    """Low-level HTTP transport used by the protocol registration worker.

    The worker owns account leasing, retries and persistence.  This class only
    handles protocol requests and therefore can be tested with a fake session.

    ``request_func`` is an optional browser-context transport::

        request_func(method, url, headers=..., data=..., timeout=...) -> response

    When provided, protocol calls prefer it so Cloudflare cookies/UA from the
    bootstrap browser are reused. The requests session remains for cookie jar /
    SSO extraction helpers.
    """

    def __init__(self, session: requests.Session, params: SignupParameters, request_func=None):
        self.session = session
        self.params = params
        self.request_func = request_func
        # cookie | http | browser — set by extract_sso / _follow_for_sso
        self.last_sso_follow = ''

    @staticmethod
    def _grpc_message(*values: str) -> bytes:
        payload = b"".join(
            bytes([(index << 3) | 2, len(value.encode("utf-8"))]) + value.encode("utf-8")
            for index, value in enumerate(values, 1)
        )
        return b"\x00" + len(payload).to_bytes(4, "big") + payload

    def send_email_code(self, email: str, signup_url: str) -> None:
        url = urljoin(signup_url, "/auth_mgmt.AuthManagement/CreateEmailValidationCode")
        response = self._post(
            url,
            data=self._grpc_message(email),
            headers=self._headers(signup_url),
            timeout=20,
        )
        self._raise_for_protocol(response, action='send_email_code')

    def verify_email_code(self, email: str, code: str, signup_url: str) -> None:
        url = urljoin(signup_url, "/auth_mgmt.AuthManagement/VerifyEmailValidationCode")
        response = self._post(
            url,
            data=self._grpc_message(email, code),
            headers=self._headers(signup_url),
            timeout=20,
        )
        self._raise_for_protocol(response, action='verify_email_code')

    def submit_signup(self, payload: Mapping[str, Any], signup_url: str, turnstile_token: str = '') -> requests.Response:
        headers = self._headers(signup_url)
        # Next.js expects the router state tree URI-encoded in the header.
        from urllib.parse import quote
        state_tree = self.params.state_tree or DEFAULT_SIGNUP_STATE_TREE
        if state_tree and not state_tree.startswith('%'):
            state_tree = quote(state_tree, safe='')
        body_obj = dict(payload)
        if turnstile_token and not body_obj.get('turnstileToken'):
            body_obj['turnstileToken'] = turnstile_token
        # Align UA with the curl_cffi session when present.
        ua = ''
        try:
            ua = str(self.session.headers.get('User-Agent') or self.session.headers.get('user-agent') or '')
        except Exception:
            ua = ''
        headers.update({
            "accept": "text/x-component",
            "content-type": "text/plain;charset=UTF-8",
            "next-router-state-tree": state_tree,
            "next-action": self.params.action_id,
        })
        if ua:
            headers['user-agent'] = ua
        # Drop gRPC-only headers that are wrong for Server Actions.
        headers.pop('x-grpc-web', None)
        headers.pop('x-user-agent', None)
        # Asset/grok1 posts JSON list body; keep separators tight like the browser.
        body = json.dumps([body_obj], separators=(",", ":"))
        response = self._post(signup_url, data=body, headers=headers, timeout=30)
        self._raise_for_protocol(response, action='submit_signup')
        self._raise_for_action_error(response, action='submit_signup')
        return response

    def extract_sso(self, response: requests.Response) -> RegistrationResult:
        """Extract SSO from response cookies or the set-cookie redirect payload.

        Prefer pure HTTP multi-hop JWT follow. Browser navigate is only used when
        explicitly bound (hybrid path) and HTTP follow did not mint SSO.
        """
        text = getattr(response, 'text', '') or ''
        # Unescape common JSON/RSC escapes so URLs are navigable.
        unescaped = (
            text.replace('\\u0026', '&')
                .replace('\\/', '/')
                .replace('\\u003d', '=')
                .replace('\\"', '"')
        )

        sso = self._read_sso_cookie()
        if sso:
            self.last_sso_follow = 'cookie'
            return RegistrationResult(sso, getattr(response, 'url', '') or '')

        # Asset/grok1 pattern: (...set-cookie?q=...)1:  — also accept plain URLs.
        set_cookie_urls = re.findall(
            r'https://[^"\s\\]+set-cookie\?q=[^"\s\\]+',
            unescaped,
            re.I,
        )
        # Trim trailing RSC stream markers like "1:" that sometimes stick to the URL.
        set_cookie_urls = [re.sub(r'1:$', '', u).rstrip('",;)}]') for u in set_cookie_urls]
        # Prefer auth.x.ai / accounts-related hosts first; grokipedia is a
        # secondary audience host and may not mint the primary SSO cookie alone.
        auth_urls = re.findall(
            r'https://auth\.(?:x\.ai|grok\.com|grokipedia\.com)[^"\s\\]+',
            unescaped,
            re.I,
        )

        def _rank(url: str) -> tuple[int, str]:
            host = urlparse(url).netloc
            if host.endswith('auth.x.ai') or 'accounts.x.ai' in host:
                return (0, url)
            if 'grok.com' in host:
                return (1, url)
            if 'grokipedia.com' in host:
                return (2, url)
            return (3, url)

        candidates = []
        for url in set_cookie_urls + auth_urls:
            cleaned = url.rstrip('",;)}]')
            if is_trusted_sso_url(cleaned) and cleaned not in candidates:
                candidates.append(cleaned)
        candidates.sort(key=_rank)

        for url in candidates:
            sso = self._follow_for_sso(url)
            if sso:
                return RegistrationResult(sso, url)

        # Final cookie re-check after any side effects.
        sso = self._read_sso_cookie()
        if sso and not getattr(self, 'last_sso_follow', ''):
            self.last_sso_follow = 'cookie'
        return RegistrationResult(sso or '', getattr(response, 'url', '') or '')

    def _read_sso_cookie(self) -> str:
        sso = read_sso_cookie_from_session(self.session)
        if sso:
            return sso
        return self._cookie_from_browser('sso')

    def _follow_for_sso(self, url: str) -> str:
        """Follow a set-cookie / auth URL with pure HTTP first, browser as fallback.

        Cross-origin auth hosts often block page ``fetch`` (CORS). Pure HTTP
        session.get can still walk JWT success_url hops. Browser navigation is
        only used when hybrid transport has already started Chrome.
        """
        if not is_trusted_sso_url(url):
            logger.warning('[protocol] rejected untrusted SSO URL')
            return ''
        # Always try pure HTTP multi-hop first so zero-browser mode is honest.
        try:
            sso = follow_sso_http(
                self.session,
                url,
                get_func=lambda target: self._get(target, timeout=20),
            )
            if sso:
                self.last_sso_follow = 'http'
                return str(sso).strip()
        except Exception as exc:
            logger.info('[protocol] http follow-for-sso failed: %s', type(exc).__name__)

        navigate = getattr(self, '_navigate_for_sso', None)
        if callable(navigate):
            try:
                sso = navigate(url)
                if sso:
                    self.last_sso_follow = 'browser'
                    return str(sso).strip()
            except Exception as exc:
                logger.info('[protocol] navigate-for-sso failed: %s', exc)

        sso = self._cookie_from_browser('sso')
        if sso:
            self.last_sso_follow = 'browser'
        return sso

    def _post(self, url: str, *, data, headers: dict, timeout: int = 20):
        if self.request_func is not None:
            return self.request_func('POST', url, headers=headers, data=data, timeout=timeout)
        return self.session.post(url, data=data, headers=headers, timeout=timeout)

    def _get(self, url: str, *, timeout: int = 20):
        if self.request_func is not None:
            return self.request_func('GET', url, headers={}, data=None, timeout=timeout)
        return self.session.get(url, allow_redirects=True, timeout=timeout)

    def _cookie_from_browser(self, name: str) -> str:
        getter = getattr(self, '_cookie_getter', None)
        if not callable(getter):
            return ''
        try:
            value = getter(name)
            return str(value or '').strip()
        except Exception:
            return ''

    def _headers(self, signup_url: str) -> dict[str, str]:
        parsed = urlparse(signup_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        return {
            "content-type": "application/grpc-web+proto",
            "x-grpc-web": "1",
            "x-user-agent": "connect-es/2.1.1",
            "origin": origin,
            "referer": signup_url,
        }

    @staticmethod
    def _raise_for_action_error(response, *, action: str) -> None:
        """Surface embedded Next Server Action application errors."""
        text = getattr(response, 'text', '') or ''
        # Prefer well-known error markers from the xAI client.
        patterns = (
            r'\[not_found\]\s*([^"\\]+)',
            r'\[already_exists\]\s*([^"\\]+)',
            r'\[invalid_argument\]\s*([^"\\]+)',
            r'\[permission_denied\]\s*([^"\\]+)',
            r'WKE=([a-z0-9:_-]+)',
            r'"error"\s*:\s*"(\[[^\"]+\])"',
        )
        for pat in patterns:
            match = re.search(pat, text, re.I)
            if not match:
                continue
            detail = match.group(1) if match.lastindex else match.group(0)
            detail = detail.strip()
            if detail in {'$undefined', '$26', 'undefined'}:
                continue
            lowered = detail.lower()
            if 'exist' in lowered or 'already' in lowered or 'email_in_use' in lowered:
                raise ExistingAccountActionError(detail)
            raise RuntimeError(f'{action} application error: {detail[:200]}')

    @staticmethod
    def _raise_for_protocol(response, *, action: str) -> None:
        status = getattr(response, 'status_code', 0) or 0
        text = (getattr(response, 'text', '') or '')[:500]
        lowered = text.lower()
        cf_markers = (
            'just a moment',
            'attention required',
            'cf-browser-verification',
            'sorry, you have been blocked',
            'cloudflare',
            '<!doctype html>',
        )
        if status in (401, 403, 429, 503):
            # Real xAI gRPC denial embeds permission_denied; CF returns HTML.
            if 'permission_denied' in lowered and not any(m in lowered for m in cf_markers[:4]):
                raise requests.HTTPError(
                    f'{action} permission_denied HTTP {status}: {text[:120]}',
                    response=response,
                )
            if any(m in lowered for m in cf_markers) or status in (401, 429, 503) or (
                status == 403 and '<html' in lowered
            ):
                raise ProtocolEnvironmentError(
                    f'{action} blocked (HTTP {status})',
                    reason='blocked',
                    diagnostics=f'status={status}',
                )
            if status == 403:
                raise requests.HTTPError(
                    f'{action} permission_denied HTTP {status}: {text[:120]}',
                    response=response,
                )
            raise ProtocolEnvironmentError(
                f'{action} blocked (HTTP {status})',
                reason='blocked',
                diagnostics=f'status={status}',
            )
        if status >= 400:
            raise requests.HTTPError(
                f'{action} failed HTTP {status}: {text[:120]}',
                response=response,
            )
