"""Deliver registered Grok SSO cookies into a remote sub2api instance.

Mirrors the web-server importer path:
  SSO cookie → xAI Device Flow OAuth → POST /api/v1/admin/accounts/batch (1 item).

Transport uses curl so Cloudflare-fronted deployments accept the request
(Python urllib TLS fingerprints are commonly blocked with error 1010).
"""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from datetime import datetime, timezone
from typing import Any

from core.grok2api_client import (
    CLIENT_ID,
    Grok2APIError,
    SCOPES,
    sso_to_build_credential,
)

logger = logging.getLogger('register')

DEFAULT_BASE_URL = 'https://cli-chat-proxy.grok.com/v1'


def _fingerprint(sso: str) -> str:
    return hashlib.sha256((sso or '').strip().encode('utf-8')).hexdigest()[:24]


def _email_name(email: str) -> str:
    email = (email or '').strip()
    return email.split('@', 1)[0] if '@' in email else email


def _request_json(
    method: str,
    base_url: str,
    api_key: str,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    command = [
        'curl', '-sS', '--noproxy', '*', '--compressed',
        '--connect-timeout', str(min(timeout, 15)),
        '--max-time', str(timeout),
        '-X', method,
        '-H', f'x-api-key: {api_key}',
        '-H', 'accept: application/json',
        '-w', '\nHTTP_STATUS:%{http_code}\n',
    ]
    input_data: bytes | None = None
    if payload is not None:
        command.extend(['-H', 'content-type: application/json', '--data-binary', '@-'])
        input_data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    command.append(base_url.rstrip('/') + path)
    try:
        result = subprocess.run(
            command,
            input=input_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout + 5,
            check=False,
        )
    except FileNotFoundError as exc:
        raise Grok2APIError('curl is required to reach sub2api (Cloudflare-safe transport)') from exc
    except subprocess.TimeoutExpired as exc:
        raise Grok2APIError(f'sub2api request timed out after {timeout}s') from exc
    if result.returncode != 0:
        detail = result.stderr.decode('utf-8', errors='replace').strip()
        raise Grok2APIError(f'sub2api curl failed: {detail[:500]}')
    output = result.stdout.decode('utf-8', errors='replace')
    marker = '\nHTTP_STATUS:'
    if marker not in output:
        raise Grok2APIError(f'sub2api returned malformed response: {output[:300]}')
    body, status_text = output.rsplit(marker, 1)
    try:
        status = int(status_text.strip())
    except ValueError as exc:
        raise Grok2APIError(f'sub2api HTTP status parse failed: {status_text[:40]}') from exc
    if status >= 400:
        raise Grok2APIError(f'sub2api HTTP {status}: {body[:800]}')
    if not body.strip():
        return {}
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise Grok2APIError(f'sub2api non-JSON body: {body[:300]}') from exc
    return data if isinstance(data, dict) else {'data': data}


def build_sub2api_account(
    credential: dict[str, Any],
    *,
    group_id: int,
    email: str = '',
    proxy_id: int | None = None,
    sso_cookie: str = '',
    source: str = 'grok_register_auto',
) -> dict[str, Any]:
    access = str(credential.get('access_token') or '')
    refresh = str(credential.get('refresh_token') or '')
    if not access and not refresh:
        raise Grok2APIError('sub2api delivery requires access_token or refresh_token')

    resolved_email = (email or str(credential.get('email') or '')).strip().lower()
    sub = str(credential.get('user_id') or credential.get('sub') or '').strip()
    team_id = str(credential.get('team_id') or '').strip()
    expires_at = ''
    exp = credential.get('exp') or credential.get('expires_at')
    if isinstance(exp, int):
        expires_at = datetime.fromtimestamp(exp, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    elif isinstance(exp, str) and exp:
        expires_at = exp
    elif credential.get('expires_in'):
        try:
            expires_at = datetime.fromtimestamp(
                __import__('time').time() + int(credential['expires_in']),
                tz=timezone.utc,
            ).strftime('%Y-%m-%dT%H:%M:%SZ')
        except (TypeError, ValueError, OverflowError):
            expires_at = ''

    if resolved_email:
        name = _email_name(resolved_email)
    elif sub:
        name = f"grok-{sub.split('-', 1)[0]}"
    else:
        name = f"grok-sso-{_fingerprint(sso_cookie)[:8] or 'auto'}"

    credentials: dict[str, Any] = {
        'client_id': CLIENT_ID,
        'base_url': str(credential.get('base_url') or DEFAULT_BASE_URL),
        'token_type': credential.get('token_type') or 'Bearer',
        'scope': SCOPES,
    }
    if access:
        credentials['access_token'] = access
    if refresh:
        credentials['refresh_token'] = refresh
    if id_token := str(credential.get('id_token') or ''):
        credentials['id_token'] = id_token
    if resolved_email:
        credentials['email'] = resolved_email
    if sub:
        credentials['sub'] = sub
    if team_id:
        credentials['team_id'] = team_id
    if expires_at:
        credentials['expires_at'] = expires_at

    account: dict[str, Any] = {
        'name': name,
        'platform': 'grok',
        'type': 'oauth',
        'credentials': credentials,
        'extra': {
            'source': source,
            'email': resolved_email,
            'sub': sub,
            'sso_fingerprint': _fingerprint(sso_cookie) if sso_cookie else '',
        },
        'group_ids': [int(group_id)],
        'concurrency': 1,
        'priority': 1,
        'auto_pause_on_expired': True,
        'confirm_mixed_channel_risk': True,
    }
    if proxy_id is not None:
        account['proxy_id'] = int(proxy_id)
    return account


def create_sub2api_account(
    base_url: str,
    api_key: str,
    account: dict[str, Any],
    timeout: int = 60,
) -> dict[str, Any]:
    """Create one account via single-item batch (the supported single-import path)."""
    resp = _request_json(
        'POST',
        base_url,
        api_key,
        '/api/v1/admin/accounts/batch',
        {'accounts': [account]},
        timeout=timeout,
    )
    result = resp.get('data', resp)
    if not isinstance(result, dict):
        return {'success': True, 'raw': result}
    failed = int(result.get('failed') or 0)
    if failed:
        detail = ''
        items = result.get('results') or []
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and not item.get('success'):
                    detail = str(item.get('error') or item.get('message') or item)
                    break
        raise Grok2APIError(detail or f'sub2api create failed: {result}')
    items = result.get('results')
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return items[0]
    return result


def export_sso_to_sub2api(settings: dict, sso_cookie: str, email: str = '') -> dict[str, Any]:
    """Mint OAuth from SSO and push a single grok account into sub2api."""
    if (settings.get('sub2api_auto_upload') or 'false').lower() != 'true':
        logger.info('sub2api auto upload disabled')
        return {'skipped': True}

    base_url = (settings.get('sub2api_url') or '').strip()
    api_key = (settings.get('sub2api_api_key') or '').strip()
    group_raw = settings.get('sub2api_group_id') or ''
    try:
        group_id = int(group_raw)
    except (TypeError, ValueError):
        group_id = 0
    proxy_raw = settings.get('sub2api_proxy_id')
    proxy_id: int | None
    try:
        proxy_id = int(proxy_raw) if str(proxy_raw or '').strip() not in {'', '0', 'none', 'null'} else None
    except (TypeError, ValueError):
        proxy_id = None

    if not base_url or not api_key or group_id <= 0:
        raise Grok2APIError(
            'sub2api auto upload is enabled but sub2api_url / sub2api_api_key / sub2api_group_id is incomplete'
        )

    token = (sso_cookie or '').strip()
    if not token:
        raise Grok2APIError('SSO cookie is empty')

    logger.info('sub2api mint via SSO device flow: email=%s group=%s', email or '(none)', group_id)
    credential = sso_to_build_credential(token, email=email)
    account = build_sub2api_account(
        credential,
        group_id=group_id,
        email=email,
        proxy_id=proxy_id,
        sso_cookie=token,
        source='grok_register_auto',
    )
    created = create_sub2api_account(base_url, api_key, account, timeout=90)
    account_id = None
    if isinstance(created, dict):
        account_id = created.get('id') or created.get('account_id')
        if account_id is None and isinstance(created.get('account'), dict):
            account_id = created['account'].get('id')
    logger.info(
        'sub2api account created: name=%s id=%s email=%s',
        account.get('name'), account_id, email or credential.get('email') or '',
    )
    return {
        'account_id': account_id,
        'name': account.get('name'),
        'email': account.get('extra', {}).get('email') if isinstance(account.get('extra'), dict) else '',
        'group_id': group_id,
        'result': created,
    }
