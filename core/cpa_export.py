"""Export registered SSO cookies into CPA xai-*.json hotload files."""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.grok2api_client import Grok2APIError, sso_to_build_credential

logger = logging.getLogger('register')

CLIENT_ID = 'b1a00492-073a-47ea-816f-4c329264a828'
DEFAULT_BASE_URL = 'https://cli-chat-proxy.grok.com/v1'
DEFAULT_HEADERS = {
    'x-grok-client-version': '0.2.99',
    'x-xai-token-auth': 'xai-grok-cli',
    'x-authenticateresponse': 'authenticate-response',
    'x-grok-client-identifier': 'grok-shell',
    'User-Agent': 'grok-shell/0.2.99 (linux; x86_64)',
}


def _sanitize(value: str) -> str:
    out = []
    for ch in (value or '').strip():
        if ch.isalnum() or ch in {'@', '.', '_', '-'}:
            out.append(ch)
        else:
            out.append('-')
    return ''.join(out).strip('-')


def _jwt_payload(token: str) -> dict[str, Any]:
    try:
        segment = token.split('.')[1]
        segment += '=' * (-len(segment) % 4)
        return json.loads(base64.urlsafe_b64decode(segment))
    except Exception:
        return {}


def build_payload(credential: dict[str, Any]) -> dict[str, Any]:
    access = (credential.get('access_token') or '').strip()
    refresh = (credential.get('refresh_token') or '').strip()
    if not access or not refresh:
        raise Grok2APIError('CPA export requires access_token and refresh_token')
    claims = _jwt_payload(access)
    exp = int(claims.get('exp') or 0)
    iat = int(claims.get('iat') or (exp - 21600 if exp else 0))
    expired = (
        datetime.fromtimestamp(exp, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        if exp else ''
    )
    email = (credential.get('email') or claims.get('email') or '').strip()
    sub = (credential.get('user_id') or claims.get('sub') or '').strip()
    now = datetime.now(tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    return {
        'type': 'xai',
        'auth_kind': 'oauth',
        'email': email,
        'sub': sub,
        'access_token': access,
        'refresh_token': refresh,
        'id_token': credential.get('id_token') or '',
        'token_type': credential.get('token_type') or 'Bearer',
        'expires_in': int(credential.get('expires_in') or max(exp - iat, 0) or 21600),
        'expired': expired,
        'last_refresh': now,
        'client_id': CLIENT_ID,
        'base_url': DEFAULT_BASE_URL,
        'token_endpoint': 'https://auth.x.ai/oauth2/token',
        'redirect_uri': 'http://127.0.0.1:56121/callback',
        'disabled': False,
        'headers': dict(DEFAULT_HEADERS),
    }


def write_auth(auth_dir: str | Path, payload: dict[str, Any]) -> Path:
    auth_dir = Path(auth_dir)
    auth_dir.mkdir(parents=True, exist_ok=True)
    email = _sanitize(str(payload.get('email') or ''))
    sub = _sanitize(str(payload.get('sub') or ''))
    name = f'xai-{email or sub or int(time.time()*1000)}.json'
    dest = auth_dir / name
    data = json.dumps(payload, indent=2, ensure_ascii=False) + '\n'
    fd, tmp = tempfile.mkstemp(prefix='.xai-', suffix='.tmp', dir=str(auth_dir))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, dest)
        os.chmod(dest, 0o600)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return dest


def _proxy_candidates(proxy: str | None) -> list[str | None]:
    """Prefer socks5h (remote DNS); fall back to socks5 / direct."""
    if not proxy:
        return [None]
    p = proxy.strip()
    out: list[str | None] = []
    if p.startswith('socks5://'):
        out.append('socks5h://' + p[len('socks5://'):])
        out.append(p)
    elif p.startswith('socks5h://'):
        out.append(p)
        out.append('socks5://' + p[len('socks5h://'):])
    else:
        out.append(p)
    # last resort: direct (some accounts only open after a different egress)
    out.append(None)
    # de-dupe preserve order
    seen = set()
    uniq = []
    for item in out:
        key = item or ''
        if key in seen:
            continue
        seen.add(key)
        uniq.append(item)
    return uniq


def probe_chat(access_token: str, *, proxy: str | None = None, timeout: float = 45.0) -> dict[str, Any]:
    url = f'{DEFAULT_BASE_URL}/chat/completions'
    payload = {
        'model': 'grok-4.5',
        'messages': [{'role': 'user', 'content': 'ping'}],
        'max_tokens': 4,
        'stream': False,
    }
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        **DEFAULT_HEADERS,
    }
    last: dict[str, Any] = {'ok': False, 'status': 0, 'error': 'no attempt'}
    for px in _proxy_candidates(proxy):
        label = px or 'direct'
        try:
            from curl_cffi import requests as curl_requests
            proxies = {'http': px, 'https': px} if px else None
            resp = curl_requests.post(
                url, json=payload, headers=headers, proxies=proxies,
                impersonate='chrome', timeout=timeout,
            )
            ok = 200 <= resp.status_code < 300
            last = {
                'ok': ok,
                'status': resp.status_code,
                'body': (resp.text or '')[:500] if ok else None,
                'error': None if ok else (resp.text or '')[:500],
                'proxy': label,
            }
            if ok:
                return last
            # hard permission-denied: still try other egress once, then give up attempts for this round
            logger.info('CPA chat probe via %s: status=%s', label, resp.status_code)
        except Exception as e:  # noqa: BLE001
            last = {'ok': False, 'status': 0, 'error': str(e), 'proxy': label}
            logger.warning('CPA chat probe via %s failed: %s', label, e)
    return last


def probe_chat_with_retries(
    access_token: str,
    *,
    proxy: str | None = None,
    delay_sec: float = 45.0,
    retries: int = 2,
    retry_gap_sec: float = 60.0,
) -> dict[str, Any]:
    """Minted tokens often get temporary permission-denied; wait then retry."""
    if delay_sec > 0:
        logger.info('CPA chat probe delay %.0fs before first attempt', delay_sec)
        time.sleep(delay_sec)
    attempts = max(1, int(retries) + 1)
    last: dict[str, Any] = {'ok': False, 'status': 0, 'error': 'not attempted'}
    for i in range(attempts):
        last = probe_chat(access_token, proxy=proxy)
        logger.info(
            'CPA chat probe attempt %s/%s: ok=%s status=%s proxy=%s',
            i + 1, attempts, last.get('ok'), last.get('status'), last.get('proxy'),
        )
        if last.get('ok'):
            return last
        if i + 1 < attempts and retry_gap_sec > 0:
            logger.info('CPA chat probe retry sleep %.0fs', retry_gap_sec)
            time.sleep(retry_gap_sec)
    return last


def export_sso_to_cpa(settings: dict, sso_cookie: str, email: str = '') -> dict[str, Any]:
    if (settings.get('cpa_auto_export') or 'true').lower() != 'true':
        logger.info('CPA auto export disabled')
        return {'skipped': True}
    auth_dir = (settings.get('cpa_auth_dir') or '/cpa/auths').strip()
    dead_dir = (settings.get('cpa_dead_dir') or '/cpa/auths-chat-dead').strip()
    proxy = (settings.get('cpa_proxy') or settings.get('browser_proxy') or '').strip() or None
    require_chat = (settings.get('cpa_probe_chat') or 'true').lower() == 'true'
    delay_sec = float(settings.get('cpa_probe_delay_sec') or 45)
    retries = int(settings.get('cpa_probe_retries') or 2)
    retry_gap = float(settings.get('cpa_probe_retry_gap_sec') or 60)
    logger.info('CPA mint via SSO device flow: email=%s', email or '(none)')
    cred = sso_to_build_credential(sso_cookie, email=email)
    payload = build_payload(cred)
    probe = {'ok': True, 'skipped': True}
    if require_chat:
        probe = probe_chat_with_retries(
            payload['access_token'],
            proxy=proxy,
            delay_sec=delay_sec,
            retries=retries,
            retry_gap_sec=retry_gap,
        )
        logger.info('CPA chat probe final: ok=%s status=%s', probe.get('ok'), probe.get('status'))
        if not probe.get('ok'):
            dest = write_auth(
                dead_dir,
                {
                    **payload,
                    'disabled': True,
                    'disabled_reason': 'chat_probe_failed',
                    'probe_error': (probe.get('error') or '')[:300],
                },
            )
            raise Grok2APIError(
                f'CPA chat probe failed: {probe.get("error") or probe}; archived {dest.name}'
            )
    dest = write_auth(auth_dir, payload)
    logger.info('CPA auth hotloaded: %s', dest)
    return {'path': str(dest), 'email': payload.get('email'), 'probe': probe}


def revive_dead_auths(
    auth_dir: str | Path,
    dead_dir: str | Path,
    *,
    proxy: str | None = None,
    max_files: int = 20,
    min_age_sec: float = 120.0,
) -> dict[str, Any]:
    """Re-probe recent chat-dead files; move survivors into hot pool."""
    auth_dir = Path(auth_dir)
    dead_dir = Path(dead_dir)
    if not dead_dir.is_dir():
        return {'checked': 0, 'revived': 0}
    now = time.time()
    files = sorted(dead_dir.glob('xai-*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
    checked = 0
    revived = 0
    for path in files[:max_files]:
        age = now - path.stat().st_mtime
        if age < min_age_sec:
            continue
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            continue
        at = (payload.get('access_token') or '').strip()
        if not at:
            continue
        # skip already expired access if we can read exp
        claims = _jwt_payload(at)
        exp = int(claims.get('exp') or 0)
        if exp and exp < now + 60:
            continue
        checked += 1
        probe = probe_chat(at, proxy=proxy)
        if not probe.get('ok'):
            continue
        payload['disabled'] = False
        payload.pop('disabled_reason', None)
        payload.pop('probe_error', None)
        payload['last_refresh'] = datetime.now(tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        dest = write_auth(auth_dir, payload)
        try:
            path.unlink()
        except OSError:
            pass
        revived += 1
        logger.info('CPA revived dead auth -> %s', dest.name)
    return {'checked': checked, 'revived': revived}
