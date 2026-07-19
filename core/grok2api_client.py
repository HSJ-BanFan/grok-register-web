import base64
import json
import logging
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

import requests
from curl_cffi import requests as curl_requests


logger = logging.getLogger('register')

CLIENT_ID = 'b1a00492-073a-47ea-816f-4c329264a828'
OIDC_ISSUER = 'https://auth.x.ai'
SCOPES = (
    'openid profile email offline_access grok-cli:access '
    'api:access conversations:read conversations:write'
)


class Grok2APIError(RuntimeError):
    pass


class Grok2APIChatPermissionError(Grok2APIError):
    """The minted Build credential cannot use chat after all configured retries."""

    def __init__(self, probe):
        self.probe = dict(probe or {})
        status = int(self.probe.get('status') or 0)
        detail = self.probe.get('error') or self.probe.get('body') or 'permission denied'
        super().__init__(f'grok2api chat probe permission denied: HTTP {status}: {detail}')


def _decode_jwt_payload(token):
    try:
        segment = token.split('.')[1]
        segment += '=' * (-len(segment) % 4)
        return json.loads(base64.urlsafe_b64decode(segment))
    except Exception:
        return {}


def _post_form(url, data, timeout=15):
    payload = urllib.parse.urlencode(data).encode()
    request = urllib.request.Request(
        url,
        data=payload,
        method='POST',
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors='replace')[:300]
        raise Grok2APIError(f'{url} returned HTTP {exc.code}: {body}') from exc


def _poll_token(device_code, interval, expires_in, timeout=60):
    deadline = time.time() + min(int(expires_in), timeout)
    while time.time() < deadline:
        time.sleep(interval)
        try:
            return _post_form(
                f'{OIDC_ISSUER}/oauth2/token',
                {
                    'grant_type': 'urn:ietf:params:oauth:grant-type:device_code',
                    'client_id': CLIENT_ID,
                    'device_code': device_code,
                },
            )
        except Grok2APIError as exc:
            message = str(exc)
            if 'authorization_pending' in message:
                continue
            if 'slow_down' in message:
                interval += 5
                continue
            raise
    raise Grok2APIError('Device Flow token polling timed out')


def sso_to_build_credential(sso_cookie, email=''):
    session = curl_requests.Session()
    session.cookies.set('sso', sso_cookie, domain='.x.ai')
    response = session.get('https://accounts.x.ai/', impersonate='chrome', timeout=15)
    if 'sign-in' in response.url or 'sign-up' in response.url:
        raise Grok2APIError('SSO cookie is invalid')

    device = _post_form(
        f'{OIDC_ISSUER}/oauth2/device/code',
        {'client_id': CLIENT_ID, 'scope': SCOPES},
    )
    verification_url = device.get('verification_uri_complete')
    user_code = device.get('user_code')
    if not verification_url or not user_code or not device.get('device_code'):
        raise Grok2APIError('Device Flow response is incomplete')

    session.get(verification_url, impersonate='chrome', timeout=15)
    response = session.post(
        f'{OIDC_ISSUER}/oauth2/device/verify',
        data={'user_code': user_code},
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        impersonate='chrome',
        timeout=15,
        allow_redirects=True,
    )
    if 'consent' not in response.url:
        raise Grok2APIError(f'Device verification failed: {response.url}')

    response = session.post(
        f'{OIDC_ISSUER}/oauth2/device/approve',
        data={
            'user_code': user_code,
            'action': 'allow',
            'principal_type': 'User',
            'principal_id': '',
        },
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        impersonate='chrome',
        timeout=15,
        allow_redirects=True,
    )
    if 'done' not in response.url:
        raise Grok2APIError(f'Device approval failed: {response.url}')

    token = _poll_token(
        device['device_code'],
        int(device.get('interval', 5)),
        int(device.get('expires_in', 1800)),
    )
    access_token = token.get('access_token', '')
    refresh_token = token.get('refresh_token', '')
    if not access_token and not refresh_token:
        raise Grok2APIError('Device Flow returned no access or refresh token')

    claims = _decode_jwt_payload(access_token)
    return {
        'provider': 'grok_build',
        'name': email or claims.get('email') or claims.get('sub') or 'Grok Build account',
        'client_id': CLIENT_ID,
        'access_token': access_token,
        'refresh_token': refresh_token,
        'token_type': token.get('token_type', 'Bearer'),
        'expires_in': int(token.get('expires_in', 0)),
        'email': email or claims.get('email', ''),
        'user_id': claims.get('sub') or claims.get('principal_id', ''),
        'principal_id': claims.get('principal_id', ''),
        'team_id': claims.get('team_id', ''),
    }


class Grok2APIClient:
    def __init__(self, base_url, username, password, timeout=30):
        self.base_url = base_url.rstrip('/')
        self.username = username
        self.password = password
        self.timeout = timeout
        self.session = requests.Session()

    def _login(self):
        response = self.session.post(
            f'{self.base_url}/api/admin/v1/auth/login',
            json={'username': self.username, 'password': self.password},
            timeout=self.timeout,
        )
        if response.status_code != 200:
            raise Grok2APIError(f'grok2api login failed: HTTP {response.status_code}')
        payload = response.json()
        token = payload.get('data', {}).get('tokens', {}).get('accessToken')
        if not token:
            raise Grok2APIError('grok2api login response has no access token')
        return token

    def import_build_credential(self, credential):
        token = self._login()
        document = json.dumps({'accounts': [credential]}, ensure_ascii=False).encode()
        response = self.session.post(
            f'{self.base_url}/api/admin/v1/accounts/import',
            headers={'Authorization': f'Bearer {token}', 'Accept': 'text/event-stream'},
            files={'file': ('grok-build-account.json', document, 'application/json')},
            timeout=max(self.timeout, 120),
        )
        if response.status_code != 200:
            raise Grok2APIError(f'grok2api import failed: HTTP {response.status_code}')
        result = None
        for block in response.text.replace('\r\n', '\n').split('\n\n'):
            event = ''
            data = ''
            for line in block.splitlines():
                if line.startswith('event:'):
                    event = line[6:].strip()
                elif line.startswith('data:'):
                    data += line[5:].strip()
            if not data:
                continue
            payload = json.loads(data)
            if event == 'error':
                raise Grok2APIError(payload.get('message') or payload.get('code') or 'grok2api import failed')
            if event == 'complete':
                result = payload
        if result is None:
            raise Grok2APIError('grok2api import returned no completion event')
        return result

    def _run_sse_task(self, path, json_body=None, files=None, result_event='complete'):
        token = self._login()
        response = self.session.post(
            f'{self.base_url}{path}',
            headers={'Authorization': f'Bearer {token}', 'Accept': 'text/event-stream'},
            files=files,
            json=json_body,
            timeout=max(self.timeout, 120),
        )
        if response.status_code != 200:
            raise Grok2APIError(f'grok2api task failed: HTTP {response.status_code}')
        result = None
        for block in response.text.replace('\r\n', '\n').split('\n\n'):
            event = ''
            data = ''
            for line in block.splitlines():
                if line.startswith('event:'):
                    event = line[6:].strip()
                elif line.startswith('data:'):
                    data += line[5:].strip()
            if not data:
                continue
            payload = json.loads(data)
            if event == 'error':
                raise Grok2APIError(payload.get('message') or payload.get('code') or 'grok2api task failed')
            if event == result_event:
                result = payload
        if result is None:
            raise Grok2APIError('grok2api task returned no completion event')
        return result

    def import_web_sso_and_convert(self, sso_cookie, email=''):
        token = self._login()
        account_name = email.strip() or f'Grok Web {secrets.token_hex(4)}'
        logger.info('grok2api Web import started: account=%s', account_name)
        document = json.dumps({
            'provider': 'grok_web',
            'accounts': [{'name': account_name, 'sso_token': sso_cookie.strip(), 'tier': 'auto'}],
        }, ensure_ascii=False).encode()
        response = self.session.post(
            f'{self.base_url}/api/admin/v1/accounts/web/import',
            headers={'Authorization': f'Bearer {token}', 'Accept': 'text/event-stream'},
            files={'file': ('registered-web-account.json', document, 'application/json')},
            timeout=max(self.timeout, 120),
        )
        if response.status_code != 200:
            raise Grok2APIError(f'grok2api web import failed: HTTP {response.status_code}')
        imported = None
        for block in response.text.replace('\r\n', '\n').split('\n\n'):
            event = ''
            data = ''
            for line in block.splitlines():
                if line.startswith('event:'):
                    event = line[6:].strip()
                elif line.startswith('data:'):
                    data += line[5:].strip()
            if not data:
                continue
            payload = json.loads(data)
            if event == 'error':
                raise Grok2APIError(payload.get('message') or payload.get('code') or 'grok2api web import failed')
            if event == 'complete':
                imported = payload
        if imported is None:
            raise Grok2APIError('grok2api web import returned no completion event')
        logger.info(
            'grok2api Web import completed: account=%s created=%s updated=%s '
            'synced=%s sync_failed=%s',
            account_name,
            imported.get('created', 0),
            imported.get('updated', 0),
            imported.get('synced', 0),
            imported.get('syncFailed', 0),
        )

        logger.info('grok2api locating imported Web account: account=%s', account_name)
        lookup = self.session.get(
            f'{self.base_url}/api/admin/v1/accounts',
            headers={'Authorization': f'Bearer {token}'},
            params={'provider': 'grok_web', 'search': account_name, 'page': 1, 'pageSize': 20},
            timeout=self.timeout,
        )
        if lookup.status_code != 200:
            raise Grok2APIError(f'grok2api account lookup failed: HTTP {lookup.status_code}')
        payload = lookup.json().get('data', {})
        items = payload.get('items') or payload.get('data') or []
        account = next((item for item in items if item.get('name') == account_name), None)
        if not account or not account.get('id'):
            raise Grok2APIError(f'grok2api could not locate imported Web account {account_name}')

        account_id = str(account['id'])
        logger.info(
            'grok2api Build conversion started: account=%s web_account_id=%s',
            account_name, account_id,
        )
        converted = self._run_sse_task(
            '/api/admin/v1/accounts/web/convert-to-build',
            json_body={'ids': [account_id]},
        )
        if int(converted.get('failed', 0) or 0) > 0:
            # Conversion can fail transiently after the Web record has already
            # been imported (for example, a short upstream/network timeout).
            # Retry once; grok2api's conversion is identity-aware and will
            # return linked/skipped when the first attempt partially succeeded.
            logger.warning(
                'grok2api Build conversion reported failed=%s; retrying once: web_account_id=%s',
                converted.get('failed', 0),
                account_id,
            )
            converted = self._run_sse_task(
                '/api/admin/v1/accounts/web/convert-to-build',
                json_body={'ids': [account_id]},
            )
        if int(converted.get('failed', 0) or 0) > 0:
            raise Grok2APIError(
                f'grok2api Build conversion failed for Web account {account_id}'
            )
        logger.info(
            'grok2api Build conversion completed: account=%s web_account_id=%s '
            'created=%s linked=%s skipped=%s failed=%s synced=%s sync_failed=%s',
            account_name,
            account_id,
            converted.get('created', 0),
            converted.get('linked', 0),
            converted.get('skipped', 0),
            converted.get('failed', 0),
            converted.get('synced', 0),
            converted.get('syncFailed', 0),
        )
        return {'import': imported, 'conversion': converted}

    def upsert_web_egress_context(self, user_agent, cloudflare_cookies):
        if not user_agent or not cloudflare_cookies:
            raise Grok2APIError('Web egress context requires User-Agent and Cloudflare cookies')
        token = self._login()
        headers = {'Authorization': f'Bearer {token}'}
        response = self.session.get(
            f'{self.base_url}/api/admin/v1/egress-nodes',
            headers=headers,
            params={'scope': 'grok_web', 'page': 1, 'pageSize': 100},
            timeout=self.timeout,
        )
        if response.status_code != 200:
            raise Grok2APIError(f'grok2api egress lookup failed: HTTP {response.status_code}')
        data = response.json().get('data', {})
        items = data.get('items') or data.get('data') or []
        existing = next((item for item in items if item.get('name') == 'grok-register-web'), None)
        body = {
            'name': 'grok-register-web', 'scope': 'grok_web', 'enabled': True,
            'userAgent': user_agent, 'cloudflareCookies': cloudflare_cookies,
        }
        if existing:
            url = f'{self.base_url}/api/admin/v1/egress-nodes/{existing["id"]}'
            result = self.session.put(url, headers=headers, json=body, timeout=self.timeout)
        else:
            url = f'{self.base_url}/api/admin/v1/egress-nodes'
            body['proxyURL'] = ''
            result = self.session.post(url, headers=headers, json=body, timeout=self.timeout)
        if result.status_code not in (200, 201):
            raise Grok2APIError(f'grok2api egress update failed: HTTP {result.status_code}')
        return result.json().get('data', {})


def upload_registered_sso(settings, sso_cookie, email='', user_agent='', cloudflare_cookies=''):
    """Deliver a successful registration to optional backends.

    - CPA (CLIProxyAPI) hotload when ``cpa_auto_export`` is true.
    - grok2api Web import + Build convert when ``grok2api_auto_upload`` is true.
    Either path may run alone; failures on an enabled path raise so durable retry can re-run.
    """
    result = {}
    cpa_enabled = (settings.get('cpa_auto_export') or 'false').lower() == 'true'
    grok_enabled = (settings.get('grok2api_auto_upload') or 'false').lower() == 'true'

    if cpa_enabled:
        try:
            from core.cpa_export import export_sso_to_cpa
            result['cpa'] = export_sso_to_cpa(settings, sso_cookie, email=email)
        except Exception as exc:
            raise Grok2APIError(f'CPA export failed: {exc}') from exc

    if not grok_enabled:
        if not cpa_enabled:
            logger.info('No delivery backend enabled (cpa_auto_export / grok2api_auto_upload)')
        return result if result else None

    base_url = settings.get('grok2api_url', '').strip()
    username = settings.get('grok2api_username', '').strip()
    password = settings.get('grok2api_password', '')
    if not base_url or not username or not password:
        if cpa_enabled:
            logger.warning('grok2api enabled but incomplete credentials; CPA-only')
            return result
        raise Grok2APIError('grok2api auto upload is enabled but URL/username/password is incomplete')

    client = Grok2APIClient(base_url, username, password)
    logger.info('grok2api auto pipeline started: account=%s endpoint=%s', email or '(unnamed)', base_url)
    if user_agent and cloudflare_cookies:
        logger.info('Updating grok2api Grok Web egress Cloudflare context...')
        client.upsert_web_egress_context(user_agent, cloudflare_cookies)
    try:
        probe = {'ok': True, 'skipped': True}
        if (settings.get('grok2api_probe_chat') or 'false').lower() == 'true':
            from core.cpa_export import probe_chat_with_retries

            logger.info('grok2api pre-upload chat probe mint started: account=%s', email or '(unnamed)')
            credential = sso_to_build_credential(sso_cookie, email=email)
            probe = probe_chat_with_retries(
                credential['access_token'],
                proxy=(
                    settings.get('grok2api_probe_proxy')
                    or settings.get('browser_proxy')
                    or ''
                ).strip() or None,
                delay_sec=float(settings.get('grok2api_probe_delay_sec') or 45),
                retries=int(settings.get('grok2api_probe_retries') or 2),
                retry_gap_sec=float(settings.get('grok2api_probe_retry_gap_sec') or 60),
            )
            if not probe.get('ok'):
                status = int(probe.get('status') or 0)
                detail = str(probe.get('error') or '').lower()
                classification = str(probe.get('classification') or '')
                # Only hard chat-permission failures block upload.
                # Model-field mismatch is soft and never reaches here (ok stays true).
                if (
                    status in (401, 403)
                    or classification == 'chat_permission_denied'
                    or 'permission' in detail
                    or 'denied' in detail
                ):
                    raise Grok2APIChatPermissionError(probe)
                raise Grok2APIError(
                    f'grok2api chat probe failed: HTTP {status}: '
                    f'{probe.get("error") or "unknown error"}'
                )
            if probe.get('model_ok') is False and not probe.get('skipped'):
                logger.warning(
                    'grok2api pre-upload chat probe model soft-fail, still uploading: '
                    'account=%s model=%s classification=%s error=%s',
                    email or '(unnamed)', probe.get('model') or '',
                    probe.get('classification') or '', (probe.get('error') or '')[:160],
                )
            else:
                logger.info(
                    'grok2api pre-upload chat probe passed: account=%s model=%s model_ok=%s',
                    email or '(unnamed)', probe.get('model') or '', probe.get('model_ok'),
                )
        result['grok2api'] = client.import_web_sso_and_convert(sso_cookie, email=email)
        result['grok2api']['probe'] = probe
    except Exception as exc:
        if cpa_enabled:
            # CPA already succeeded; do not fail the whole delivery on secondary backend.
            logger.warning('grok2api pipeline failed after CPA export (ignored): %s', exc)
            if isinstance(exc, Grok2APIChatPermissionError):
                result['grok2api_probe_denied'] = exc.probe
            result['grok2api_error'] = str(exc)
            return result
        raise
    return result
