"""Deliver registered Grok SSO cookies into Sub2API via admin SSO import."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests


logger = logging.getLogger('register')

# Device Flow inside Sub2API can take well over a minute.
DEFAULT_TIMEOUT_SEC = 180
_TOKEN_REFRESH_SKEW = 5 * 60

# Per-base-url JWT cache: {base_url: (token, expires_at_epoch)}
_token_cache: dict[str, tuple[str, float]] = {}
_token_cache_lock = threading.Lock()

# Per-base-url detected auth method: 'api_key' | 'bearer'
# Avoids retrying the wrong header on every request once we know what works.
_auth_method_cache: dict[str, str] = {}
_auth_method_cache_lock = threading.Lock()

# Invalidate cached auth method when admin_api_key 401s so we fall back to login.
_AUTH_METHOD_INVALIDATED = object()


def _clean_credential(value: str) -> str:
    """Strip invisible characters that survive str.strip() (BOM, ZWSP, etc.)."""
    return ''.join(ch for ch in (value or '') if 0x21 <= ord(ch) <= 0x7E)


class Sub2APIError(RuntimeError):
    """Sub2API delivery or admin API failure."""


def normalize_base_url(url: str) -> str:
    raw = (url or '').strip().rstrip('/')
    if not raw:
        return ''
    parsed = urlsplit(raw)
    if not parsed.scheme or not parsed.netloc:
        return raw
    path = parsed.path.rstrip('/')
    lower = path.lower()
    for marker in ('/api/v1', '/api'):
        index = lower.find(marker)
        if index >= 0:
            path = path[:index]
            break
    return urlunsplit((parsed.scheme, parsed.netloc, path.rstrip('/'), '', ''))


def parse_group_ids(value: Any) -> list[int]:
    if value is None or value == '':
        return []
    if isinstance(value, int):
        return [value] if value > 0 else []
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            try:
                num = int(item)
            except (TypeError, ValueError):
                continue
            if num > 0:
                out.append(num)
        return out
    text = str(value).strip()
    if not text:
        return []
    # Support JSON-like "[1,2]" or "1,2" / "1 2"
    text = text.strip('[]')
    out = []
    for part in text.replace(' ', ',').split(','):
        part = part.strip()
        if not part:
            continue
        try:
            num = int(part)
        except ValueError:
            continue
        if num > 0:
            out.append(num)
    return out


def _unwrap_envelope(payload: Any) -> Any:
    if isinstance(payload, dict) and 'data' in payload and (
        'code' in payload or 'message' in payload
    ):
        return payload.get('data')
    return payload


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _token_diagnostics(token: str) -> dict[str, Any]:
    """Return a non-sensitive summary of token shape for debug output.

    Never includes the token itself, only counts/character-class hints so a
    UI can spot invisible characters (BOM, zero-width, full-width spaces) that
    would cause a server-side ``INVALID_TOKEN`` even though the user copied
    what looks like the right string.
    """
    raw = token or ''
    sample = raw[:32]
    return {
        'length': len(raw),
        'has_whitespace': any(ch.isspace() for ch in raw),
        'has_control': any((ord(ch) < 32 or ord(ch) == 127) for ch in raw),
        'has_non_ascii': any(ord(ch) > 126 for ch in raw),
        'starts_with': sample,
    }


class Sub2APIClient:
    def __init__(
        self,
        base_url: str,
        *,
        api_token: str = '',
        email: str = '',
        password: str = '',
        timeout: float = DEFAULT_TIMEOUT_SEC,
        session: requests.Session | None = None,
    ):
        self.base_url = normalize_base_url(base_url)
        if not self.base_url:
            raise Sub2APIError('sub2api_url is empty')
        self.api_token = (api_token or '').strip()
        self.email = (email or '').strip()
        self.password = password or ''
        self.timeout = max(30.0, float(timeout or DEFAULT_TIMEOUT_SEC))
        self.session = session or requests.Session()

    def _login(self) -> tuple[str, float]:
        if not self.email or not self.password:
            raise Sub2APIError('sub2api login requires email and password')
        url = f'{self.base_url}/api/v1/auth/login'
        try:
            response = self.session.post(
                url,
                json={'email': self.email, 'password': self.password},
                headers={'Accept': 'application/json', 'Content-Type': 'application/json'},
                timeout=min(30.0, self.timeout),
            )
        except requests.RequestException as exc:
            raise Sub2APIError(f'sub2api login request failed: {exc}') from exc
        if not response.ok:
            body = (response.text or '')[:300]
            raise Sub2APIError(f'sub2api login failed: HTTP {response.status_code}: {body}')
        try:
            payload = response.json()
        except ValueError as exc:
            raise Sub2APIError('sub2api login returned non-JSON') from exc
        data = _unwrap_envelope(payload)
        if not isinstance(data, dict):
            data = payload if isinstance(payload, dict) else {}
        token = str(
            data.get('access_token')
            or data.get('accessToken')
            or data.get('token')
            or ''
        ).strip()
        if not token:
            raise Sub2APIError('sub2api login did not return access_token')
        expires_in = _as_int(data.get('expires_in'), 3600)
        expires_at = time.time() + max(60, expires_in) - _TOKEN_REFRESH_SKEW
        return token, expires_at

    def resolve_token(self, *, force_refresh: bool = False) -> str:
        cache_key = self.base_url

        # When api_token is configured we *prefer* the admin-api-key path
        # (header x-api-key, see sub2api backend admin_auth.go:50-57). If the
        # server rejects it we transparently fall back to email/password
        # login and remember the working method for subsequent calls.
        if self.api_token:
            with _auth_method_cache_lock:
                cached_method = _auth_method_cache.get(cache_key)
            if cached_method == 'bearer':
                if not (self.email and self.password):
                    # Cached told us bearer is required, but the user hasn't
                    # configured login credentials. Fall back to api-key
                    # rather than crashing — the user may have just toggled
                    # settings and the cache is stale.
                    return self.api_token
                # Server told us this base_url expects a Bearer JWT — honor it.
            elif cached_method != _AUTH_METHOD_INVALIDATED:
                # First try / known to work: api-key path.
                return self.api_token
            # Cached invalid → fall through to login path below.

        if not force_refresh:
            with _token_cache_lock:
                cached = _token_cache.get(cache_key)
                if cached and cached[1] > time.time():
                    return cached[0]
        token, expires_at = self._login()
        with _token_cache_lock:
            _token_cache[cache_key] = (token, expires_at)
        with _auth_method_cache_lock:
            _auth_method_cache[cache_key] = 'bearer'
        return token

    def _auth_headers(self, token: str) -> dict[str, str]:
        # Sub2API's admin_auth middleware (>=0.1.162) only recognizes the
        # admin api-key via the x-api-key header — Authorization: Bearer is
        # reserved for JWTs from /api/v1/auth/login. Pick the header based on
        # which path produced the token.
        cleaned = _clean_credential(token)
        headers = {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
        }
        if self.api_token and cleaned == _clean_credential(self.api_token):
            headers['x-api-key'] = cleaned
        else:
            headers['Authorization'] = f'Bearer {cleaned}'
        return headers

    def invalidate_auth_method(self) -> None:
        """Forget the working auth method so the next call retries detection."""
        with _auth_method_cache_lock:
            _auth_method_cache.pop(self.base_url, None)

    def _retry_token_after_401(
        self,
        prev_token: str,
        method: str,
        path: str,
        body: dict | None,
        wait: float,
    ) -> str:
        """Recover from a 401 by retrying once with a fresh credential.

        Two scenarios are handled here:

        1. Bearer path: cached JWT may have expired → force a fresh login.
        2. api-key path that the server rejected: invalidate the api-key
           preference, fall through to email/password login if credentials
           are available. If no email/password is configured, raise with the
           server's diagnostic so the user can fix their settings.
        """
        if self.api_token and prev_token == self.api_token:
            # We just sent x-api-key and got 401. Server says this base_url
            # does not accept that key (or has none configured).
            self.invalidate_auth_method()
            with _auth_method_cache_lock:
                _auth_method_cache[self.base_url] = _AUTH_METHOD_INVALIDATED
            if not (self.email and self.password):
                # No fallback credentials — surface the server's error
                # verbatim so the user can see what the server said.
                raise Sub2APIError(
                    f'sub2api {method} {path} HTTP 401: '
                    'admin api-key rejected and no email/password fallback configured'
                )
            # Fall back to login. resolve_token will see the invalidated flag
            # and skip the api-key branch.
            return self.resolve_token(force_refresh=True)

        # Bearer path: refresh cached JWT.
        return self.resolve_token(force_refresh=True)

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        timeout: float | None = None,
        retry_on_401: bool = True,
    ) -> Any:
        token = self.resolve_token()
        url = f'{self.base_url}{path}'
        wait = self.timeout if timeout is None else timeout
        try:
            response = self.session.request(
                method,
                url,
                json=body,
                headers=self._auth_headers(token),
                timeout=wait,
            )
        except requests.RequestException as exc:
            raise Sub2APIError(f'sub2api {method} {path} failed: {exc}') from exc

        if response.status_code == 401 and retry_on_401:
            token = self._retry_token_after_401(token, method, path, body, wait)
            try:
                response = self.session.request(
                    method,
                    url,
                    json=body,
                    headers=self._auth_headers(token),
                    timeout=wait,
                )
            except requests.RequestException as exc:
                raise Sub2APIError(f'sub2api {method} {path} failed: {exc}') from exc

        if not response.ok:
            body_text = (response.text or '')[:400]
            raise Sub2APIError(
                f'sub2api {method} {path} HTTP {response.status_code}: {body_text}'
            )
        if not (response.text or '').strip():
            return None
        try:
            payload = response.json()
        except ValueError as exc:
            raise Sub2APIError(f'sub2api {method} {path} returned non-JSON') from exc
        # Business envelope with non-zero code
        if isinstance(payload, dict) and 'code' in payload:
            code = payload.get('code')
            if code not in (0, '0', None, 200, '200'):
                message = payload.get('message') or payload.get('error') or str(code)
                raise Sub2APIError(f'sub2api {method} {path} rejected: {message}')
        return _unwrap_envelope(payload)

    def test_connection(self) -> dict[str, Any]:
        """Login (if needed) and list groups as a connectivity probe.

        ``auth`` in the returned dict reflects the method that *actually*
        worked end-to-end: ``api_key`` if the admin api-key was accepted,
        ``login`` if we had to fall back to email/password. ``api_key_invalid``
        means the api-key was rejected and no fallback credentials were
        configured.
        """
        token = self.resolve_token(force_refresh=bool(self.email and self.password))
        try:
            data = self._request_json(
                'GET',
                '/api/v1/admin/groups/all',
                timeout=min(30.0, self.timeout),
                retry_on_401=True,
            )
        except Sub2APIError as exc:
            # Surface a more diagnostic message when api-key is rejected.
            msg = str(exc)
            if self.api_token and 'admin api-key rejected' in msg:
                msg = (
                    'sub2api rejected the configured admin API key '
                    '(check sub2api_api_token matches the value in '
                    'sub2api 后台 → 设置 → 管理员 API Key)'
                )
                return {'ok': False, 'error': msg, 'auth': 'api_key_invalid'}
            raise
        groups = data if isinstance(data, list) else []
        if isinstance(data, dict):
            for key in ('items', 'data', 'list'):
                value = data.get(key)
                if isinstance(value, list):
                    groups = value
                    break
        grok_groups = [
            g for g in groups
            if isinstance(g, dict)
            and str(g.get('platform') or '').strip().lower() in {'', 'grok'}
        ]
        with _auth_method_cache_lock:
            detected = _auth_method_cache.get(self.base_url)
        if detected == 'bearer':
            auth_label = 'login'
        elif self.api_token:
            auth_label = 'api_key'
        else:
            auth_label = 'login'
        return {
            'ok': True,
            'base_url': self.base_url,
            'auth': auth_label,
            'group_count': len(groups),
            'grok_group_count': len(grok_groups),
            'token_preview': f'{token[:8]}…' if token else '',
            'token_diag': _token_diagnostics(self.api_token if self.api_token else token),
        }

    def import_sso(
        self,
        sso_cookie: str,
        *,
        email: str = '',
        name: str = '',
        group_ids: list[int] | None = None,
        proxy_id: int | None = None,
        concurrency: int = 1,
        priority: int = 1,
        auto_pause_on_expired: bool = True,
        notes: str | None = None,
        credentials: dict | None = None,
        extra: dict | None = None,
    ) -> dict[str, Any]:
        sso = (sso_cookie or '').strip()
        if not sso:
            raise Sub2APIError('SSO cookie is empty')

        body: dict[str, Any] = {
            'sso_tokens': [sso],
            'concurrency': max(0, int(concurrency)),
            'priority': int(priority),
            'auto_pause_on_expired': bool(auto_pause_on_expired),
        }
        account_name = (name or email or '').strip()
        if account_name:
            body['name'] = account_name
        if notes is not None:
            body['notes'] = notes
        if group_ids:
            body['group_ids'] = [int(g) for g in group_ids if int(g) > 0]
        if proxy_id and int(proxy_id) > 0:
            body['proxy_id'] = int(proxy_id)
        if credentials:
            body['credentials'] = credentials
        if extra:
            body['extra'] = extra

        logger.info(
            'sub2api SSO import started: account=%s endpoint=%s groups=%s',
            account_name or '(unnamed)', self.base_url, body.get('group_ids') or [],
        )
        data = self._request_json(
            'POST',
            '/api/v1/admin/grok/sso-to-oauth',
            body=body,
            timeout=self.timeout,
        )
        if not isinstance(data, dict):
            raise Sub2APIError('sub2api SSO import returned unexpected payload')

        created = data.get('created') if isinstance(data.get('created'), list) else []
        failed = data.get('failed') if isinstance(data.get('failed'), list) else []

        if failed:
            first = failed[0] if isinstance(failed[0], dict) else {}
            err = str(first.get('error') or 'SSO import failed')
            raise Sub2APIError(f'sub2api SSO import failed: {err}')
        if not created:
            raise Sub2APIError('sub2api SSO import returned no created accounts')

        item = created[0] if isinstance(created[0], dict) else {}
        account = item.get('account') if isinstance(item.get('account'), dict) else {}
        logger.info(
            'sub2api SSO import completed: account=%s email=%s id=%s',
            account_name or item.get('name') or '(unnamed)',
            item.get('email') or email or '',
            account.get('id') or '',
        )
        return {
            'ok': True,
            'created_count': len(created),
            'failed_count': len(failed),
            'name': item.get('name') or account_name,
            'email': item.get('email') or email or '',
            'account_id': account.get('id'),
            'account': account,
            'raw': data,
        }


def client_from_settings(settings: dict) -> Sub2APIClient:
    timeout = _as_int(settings.get('sub2api_timeout_sec'), DEFAULT_TIMEOUT_SEC)
    return Sub2APIClient(
        settings.get('sub2api_url', ''),
        api_token=str(settings.get('sub2api_api_token') or '').strip(),
        email=str(settings.get('sub2api_email') or '').strip(),
        password=settings.get('sub2api_password') or '',
        timeout=timeout,
    )


def export_sso_to_sub2api(settings: dict, sso_cookie: str, email: str = '') -> dict[str, Any]:
    """Import one SSO into Sub2API. Raises Sub2APIError on failure."""
    if (settings.get('sub2api_auto_upload') or 'false').lower() != 'true':
        raise Sub2APIError('sub2api_auto_upload is disabled')

    base = normalize_base_url(str(settings.get('sub2api_url') or ''))
    token = str(settings.get('sub2api_api_token') or '').strip()
    user = str(settings.get('sub2api_email') or '').strip()
    password = settings.get('sub2api_password') or ''
    if not base:
        raise Sub2APIError('sub2api auto upload is enabled but URL is empty')
    if not token and not (user and password):
        raise Sub2APIError(
            'sub2api auto upload is enabled but api_token or email/password is incomplete'
        )

    client = client_from_settings(settings)
    proxy_raw = str(settings.get('sub2api_proxy_id') or '').strip()
    proxy_id = _as_int(proxy_raw, 0) or None
    group_ids = parse_group_ids(settings.get('sub2api_group_ids'))
    name_prefix = str(settings.get('sub2api_name_prefix') or '').strip()
    name = email or name_prefix
    auto_pause = (settings.get('sub2api_auto_pause_on_expired') or 'true').lower() == 'true'
    concurrency = _as_int(settings.get('sub2api_concurrency'), 1)
    priority = _as_int(settings.get('sub2api_priority'), 1)

    return client.import_sso(
        sso_cookie,
        email=email,
        name=name,
        group_ids=group_ids,
        proxy_id=proxy_id,
        concurrency=concurrency,
        priority=priority,
        auto_pause_on_expired=auto_pause,
    )


def test_sub2api_connection(settings: dict) -> dict[str, Any]:
    """Probe Sub2API with current settings (does not require auto_upload)."""
    base = normalize_base_url(str(settings.get('sub2api_url') or ''))
    token = str(settings.get('sub2api_api_token') or '').strip()
    user = str(settings.get('sub2api_email') or '').strip()
    password = settings.get('sub2api_password') or ''
    if not base:
        return {'ok': False, 'error': 'sub2api_url is empty'}
    if not token and not (user and password):
        return {'ok': False, 'error': 'provide sub2api_api_token or email/password'}
    try:
        client = client_from_settings(settings)
        return client.test_connection()
    except Exception as exc:
        return {'ok': False, 'error': str(exc)}

