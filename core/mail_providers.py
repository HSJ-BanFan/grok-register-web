"""Temporary mailbox adapters used by the registration pipeline.

The public interface intentionally stays small: provision one isolated mailbox,
then poll that mailbox for the verification code. Provider-specific URLs,
authentication and response shapes remain local to this module.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import html
import logging
import re
import secrets
import string
import time

import requests


logger = logging.getLogger(__name__)

MICROSOFT_PROVIDER = 'microsoft'
TEMPORARY_PROVIDERS = ('duckmail', 'yyds', 'cloudflare', 'cloud_mail')
SUPPORTED_PROVIDERS = (MICROSOFT_PROVIDER,) + TEMPORARY_PROVIDERS


class MailProviderError(Exception):
    """A mailbox provider could not create or read a mailbox."""


@dataclass(frozen=True)
class ProvisionedMailbox:
    provider: str
    address: str
    credential: str


def normalize_provider(value):
    provider = str(value or MICROSOFT_PROVIDER).strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise MailProviderError(f'Unsupported email provider: {provider}')
    return provider


def extract_verification_code(text, subject=''):
    """Extract xAI's alphanumeric or numeric verification code.

    Prefer subject-line codes (``SpaceXAI confirmation code: WKT-B4B`` /
    ``WKT-B4B xAI``). Body HTML often contains false positives like CSS
    tokens (``PER100``), so hyphenated 3-3 codes always win over bare
    6-char alphanumeric fallbacks.
    """
    subject = str(subject or '')
    body = str(text or '')
    haystacks = []
    if subject:
        haystacks.append(subject)
    if body:
        haystacks.append(body)

    # 1) Subject-first: modern xAI / SpaceXAI subject formats.
    subject_patterns = (
        r'(?:SpaceXAI|xAI|Grok).*?(?:confirmation|verification)\s*code[:\s]+([A-Z0-9]{3}-[A-Z0-9]{3})',
        r'(?:confirmation|verification)\s*code[:\s]+([A-Z0-9]{3}-[A-Z0-9]{3})',
        r'^\s*([A-Z0-9]{3}-[A-Z0-9]{3})\s+xAI',
        r'\b([A-Z0-9]{3}-[A-Z0-9]{3})\b',
    )
    for pattern in subject_patterns:
        match = re.search(pattern, subject, re.IGNORECASE)
        if match:
            return match.group(1).upper().replace('-', '')

    # 2) Body: prefer hyphenated 3-3 codes (real xAI OTP shape).
    body_hyphen = (
        r'(?:confirmation|verification)\s*code[:\s]+([A-Z0-9]{3}-[A-Z0-9]{3})',
        r'\b([A-Z0-9]{3}-[A-Z0-9]{3})\b.*confirmation\s*code',
        r'\b([A-Z0-9]{3}-[A-Z0-9]{3})\b',
    )
    for pattern in body_hyphen:
        match = re.search(pattern, body, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).upper().replace('-', '')

    # 3) Numeric / labeled codes.
    numeric_patterns = (
        r'(?:verification\s*code|your\s*code|confirm(?:ation)?\s*code)[:\s]+(\d{4,8})',
        r'(?:验证码|代码|确认码)[:\s为]+(\d{4,8})',
        r'\b(\d{6})\b',
    )
    for pattern in numeric_patterns:
        for hay in haystacks:
            match = re.search(pattern, hay, re.IGNORECASE | re.DOTALL)
            if match:
                return match.group(1).upper().replace('-', '')

    # 4) Last-resort alphanumeric — only from non-HTML-looking plain text,
    # and only when the token contains a digit (avoids CSS class noise).
    plain = body
    if '<' in body and '>' in body:
        plain = re.sub(r'<[^>]+>', ' ', body)
        plain = html.unescape(plain)
    match = re.search(
        r'\b((?=[A-Z0-9]{0,5}\d)[A-Z0-9]{6})\b',
        plain,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).upper().replace('-', '')
    return None


class TemporaryMailboxProviders:
    """Provision and read temporary mailboxes through supported adapters."""

    def __init__(self, http=None, sleep=None):
        self.http = http or requests
        self.sleep = sleep or time.sleep
        self._seen_message_ids = {}
        self._cloud_mail_tokens = {}
        self._cloudflare_domain_index = 0

    def provision(self, provider, settings):
        provider = normalize_provider(provider)
        if provider == MICROSOFT_PROVIDER:
            raise MailProviderError('Microsoft mailboxes must be imported or authorized')
        handler = getattr(self, f'_provision_{provider}')
        try:
            mailbox = handler(settings or {})
        except MailProviderError:
            raise
        except Exception as exc:
            raise MailProviderError(
                f'{self._display_name(provider)} mailbox provisioning failed: {exc}'
            ) from exc
        if not mailbox.address or not mailbox.credential:
            raise MailProviderError(
                f'{self._display_name(provider)} mailbox provisioning returned incomplete credentials'
            )
        return mailbox

    def get_verification_code(self, provider, address, credential, settings,
                              max_retries=10, requested_after=None):
        provider = normalize_provider(provider)
        if provider == MICROSOFT_PROVIDER:
            raise MailProviderError('Microsoft verification is handled by EmailManager')
        if not credential:
            raise MailProviderError(
                f'{self._display_name(provider)} mailbox credential is empty'
            )

        attempts = max(1, int(max_retries or 1))
        requested_after = self._as_utc(requested_after)
        seen = self._seen_message_ids.setdefault(
            (provider, str(address or '').strip().lower()), set()
        )
        last_error = None
        self.sleep(8)
        for attempt in range(1, attempts + 1):
            try:
                messages = self._fetch_messages(
                    provider, address, credential, settings or {},
                )
                for message in messages:
                    message_id = self._message_id(message)
                    if message_id and message_id in seen:
                        continue
                    if requested_after and self._message_is_older(message, requested_after):
                        if message_id:
                            seen.add(message_id)
                        continue
                    if not self._recipient_matches(message, address):
                        continue
                    subject, content = self._message_content(
                        provider, message, credential, settings or {},
                    )
                    code = extract_verification_code(content, subject)
                    if message_id:
                        seen.add(message_id)
                    if code:
                        logger.info(
                            'Verification code obtained via %s for %s',
                            provider,
                            address,
                        )
                        return code
            except Exception as exc:
                last_error = exc
                logger.warning(
                    '%s mail attempt %s/%s failed: %s',
                    provider,
                    attempt,
                    attempts,
                    exc,
                )
            if attempt < attempts:
                self.sleep(5)

        detail = f'; last error: {last_error}' if last_error else ''
        raise MailProviderError(
            f'{self._display_name(provider)} failed to get verification code '
            f'after {attempts} attempts{detail}'
        )

    @staticmethod
    def _display_name(provider):
        return {
            'duckmail': 'DuckMail',
            'yyds': 'YYDS',
            'cloudflare': 'Cloudflare Mail',
            'cloud_mail': 'Cloud Mail',
            'microsoft': 'Microsoft',
        }.get(provider, provider)

    @staticmethod
    def _random_username(length=10):
        chars = string.ascii_lowercase + string.digits
        return ''.join(secrets.choice(chars) for _ in range(length))

    @staticmethod
    def _random_password():
        return secrets.token_urlsafe(12)

    @staticmethod
    def _base(settings, key, default=''):
        return str(settings.get(key, default) or default).strip().rstrip('/')

    @staticmethod
    def _path(settings, key, default):
        value = str(settings.get(key, default) or default).strip()
        return value if value.startswith('/') else f'/{value}'

    @staticmethod
    def _pick_list(data):
        if isinstance(data, list):
            return data
        if not isinstance(data, dict):
            return []
        for key in ('results', 'hydra:member', 'messages'):
            if isinstance(data.get(key), list):
                return data[key]
        nested = data.get('data')
        if isinstance(nested, list):
            return nested
        if isinstance(nested, dict):
            for key in ('messages', 'results'):
                if isinstance(nested.get(key), list):
                    return nested[key]
        return []

    def _request(self, method, url, settings, **kwargs):
        kwargs.setdefault('timeout', 30)
        proxy = str(settings.get('browser_proxy', '') or '').strip()
        if proxy and 'proxies' not in kwargs:
            kwargs['proxies'] = {'http': proxy, 'https': proxy}
        response = self.http.request(method, url, **kwargs)
        response.raise_for_status()
        return response

    def _json(self, method, url, settings, **kwargs):
        response = self._request(method, url, settings, **kwargs)
        try:
            return response.json()
        except Exception as exc:
            preview = str(getattr(response, 'text', '') or '')[:300]
            raise MailProviderError(f'{url} returned invalid JSON: {preview}') from exc

    @staticmethod
    def _auth_headers(token='', api_key='', mode='bearer', json_body=False):
        headers = {'Content-Type': 'application/json'} if json_body else {}
        if token:
            headers['Authorization'] = f'Bearer {token}'
        elif api_key:
            if mode == 'x-api-key':
                headers['X-API-Key'] = api_key
            elif mode == 'x-admin-auth':
                headers['x-admin-auth'] = api_key
            elif mode != 'none' and mode != 'query-key':
                headers['Authorization'] = f'Bearer {api_key}'
        return headers

    @staticmethod
    def _query_auth(settings, params=None):
        result = dict(params or {})
        if (
            str(settings.get('cloudflare_auth_mode', 'none')).lower() == 'query-key'
            and settings.get('cloudflare_api_key')
        ):
            result['key'] = settings['cloudflare_api_key']
        return result

    @staticmethod
    def _pick_domain(domains, provider):
        cleaned = []
        for item in domains or []:
            if isinstance(item, str):
                cleaned.append({'domain': item.lstrip('@'), 'isVerified': True})
            elif isinstance(item, dict):
                cleaned.append(item)
        private = [
            item for item in cleaned
            if item.get('domain') and item.get('isVerified')
            and (item.get('ownerId') or item.get('isPublic') is False)
        ]
        verified = [
            item for item in cleaned
            if item.get('domain') and item.get('isVerified')
        ]
        candidates = private or verified or [item for item in cleaned if item.get('domain')]
        if not candidates:
            raise MailProviderError(f'{provider} returned no usable domains')
        return str(candidates[0]['domain']).lstrip('@')

    def _provision_duckmail(self, settings):
        base = self._base(settings, 'duckmail_api_base', 'https://api.duckmail.sbs')
        api_key = str(settings.get('duckmail_api_key', '') or '').strip()
        headers = self._auth_headers(api_key=api_key) if api_key else {}
        domains = self._pick_list(self._json('GET', f'{base}/domains', settings, headers=headers))
        domain = self._pick_domain(domains, 'DuckMail')
        address = f'{self._random_username()}@{domain}'
        password = self._random_password()
        create_headers = dict(headers)
        create_headers['Content-Type'] = 'application/json'
        self._json(
            'POST', f'{base}/accounts', settings,
            headers=create_headers,
            json={'address': address, 'password': password, 'expiresIn': 0},
        )
        token_data = self._json(
            'POST', f'{base}/token', settings,
            headers={'Content-Type': 'application/json'},
            json={'address': address, 'password': password},
        )
        token = token_data.get('token') if isinstance(token_data, dict) else ''
        return ProvisionedMailbox('duckmail', address, token or '')

    def _yyds_headers(self, settings, json_body=False, credential=''):
        jwt = credential or str(settings.get('yyds_jwt', '') or '').strip()
        key = str(settings.get('yyds_api_key', '') or '').strip()
        return self._auth_headers(
            token=jwt,
            api_key=key,
            mode='x-api-key',
            json_body=json_body,
        )

    def _provision_yyds(self, settings):
        base = self._base(settings, 'yyds_api_base', 'https://maliapi.215.im/v1')
        if not settings.get('yyds_jwt') and not settings.get('yyds_api_key'):
            raise MailProviderError('YYDS API Key or JWT is required')
        data = self._json(
            'GET', f'{base}/domains', settings,
            headers=self._yyds_headers(settings),
        )
        domains = data.get('data', []) if isinstance(data, dict) and data.get('success') else []
        domain = self._pick_domain(domains, 'YYDS')
        username = self._random_username()
        create = self._json(
            'POST', f'{base}/accounts', settings,
            headers=self._yyds_headers(settings, json_body=True),
            json={'address': username, 'domain': domain},
        )
        result = create.get('data', {}) if isinstance(create, dict) and create.get('success') else {}
        address = result.get('address') or f'{username}@{domain}'
        token = result.get('token') or ''
        if not token:
            token_data = self._json(
                'POST', f'{base}/token', settings,
                headers=self._yyds_headers(settings, json_body=True),
                json={'address': address},
            )
            if isinstance(token_data, dict) and token_data.get('success'):
                token = (token_data.get('data') or {}).get('token') or ''
        return ProvisionedMailbox('yyds', address, token)

    def _cloudflare_headers(self, settings, json_body=False):
        return self._auth_headers(
            api_key=str(settings.get('cloudflare_api_key', '') or '').strip(),
            mode=str(settings.get('cloudflare_auth_mode', 'none') or 'none').lower(),
            json_body=json_body,
        )

    def _next_cloudflare_domain(self, settings):
        domains = [
            item.strip().lstrip('@')
            for item in str(settings.get('cloudflare_default_domains', '') or '').split(',')
            if item.strip()
        ]
        if not domains:
            return ''
        domain = domains[self._cloudflare_domain_index % len(domains)]
        self._cloudflare_domain_index += 1
        return domain

    def _provision_cloudflare(self, settings):
        base = self._base(settings, 'cloudflare_api_base')
        if not base:
            raise MailProviderError('Cloudflare API Base is required')
        path = self._path(settings, 'cloudflare_path_accounts', '/api/new_address')
        domain = self._next_cloudflare_domain(settings)
        admin_create = path.rstrip('/').lower() == '/admin/new_address'
        payload = {'name': self._random_username(), 'enablePrefix': True} if admin_create else {}
        if domain:
            payload['domain'] = domain
        try:
            data = self._json(
                'POST', f'{base}{path}', settings,
                headers=(
                    self._cloudflare_headers(settings, json_body=True)
                    if admin_create else {'Content-Type': 'application/json'}
                ),
                json=payload,
            )
            if isinstance(data, dict) and data.get('address') and data.get('jwt'):
                return ProvisionedMailbox('cloudflare', data['address'], data['jwt'])
        except Exception as primary_error:
            logger.info('Cloudflare new-address adapter unavailable: %s', primary_error)

        domains_path = self._path(settings, 'cloudflare_path_domains', '/domains')
        domains = self._pick_list(self._json(
            'GET', f'{base}{domains_path}', settings,
            headers=self._cloudflare_headers(settings),
            params=self._query_auth(settings),
        ))
        domain = self._pick_domain(domains, 'Cloudflare')
        address = f'{self._random_username()}@{domain}'
        password = self._random_password()
        self._json(
            'POST', f'{base}{path}', settings,
            headers=self._cloudflare_headers(settings, json_body=True),
            params=self._query_auth(settings),
            json={'address': address, 'password': password, 'expiresIn': 0},
        )
        token_path = self._path(settings, 'cloudflare_path_token', '/token')
        token_data = self._json(
            'POST', f'{base}{token_path}', settings,
            headers=self._cloudflare_headers(settings, json_body=True),
            params=self._query_auth(settings),
            json={'address': address, 'password': password},
        )
        token = token_data.get('token') if isinstance(token_data, dict) else ''
        if not token and isinstance(token_data, dict) and isinstance(token_data.get('data'), dict):
            token = token_data['data'].get('token') or ''
        return ProvisionedMailbox('cloudflare', address, token)

    def _cloud_mail_token(self, settings):
        base = self._base(settings, 'cloud_mail_api_base')
        api_key = str(settings.get('cloud_mail_api_key', '') or '').strip()
        if api_key:
            return api_key
        cache_key = (
            base,
            str(settings.get('cloud_mail_admin_email', '') or ''),
        )
        if self._cloud_mail_tokens.get(cache_key):
            return self._cloud_mail_tokens[cache_key]
        email_address = str(settings.get('cloud_mail_admin_email', '') or '').strip()
        password = str(settings.get('cloud_mail_admin_password', '') or '').strip()
        if not email_address:
            raise MailProviderError(
                'Cloud Mail cloud_mail_admin_email is required when API key is empty'
            )
        if not password:
            raise MailProviderError(
                'Cloud Mail cloud_mail_admin_password is required when API key is empty'
            )
        data = self._json(
            'POST', f'{base}/api/public/genToken', settings,
            headers={'Content-Type': 'application/json'},
            json={'email': email_address, 'password': password},
        )
        token = ((data.get('data') or {}).get('token') if isinstance(data, dict) else '')
        if not token:
            raise MailProviderError('Cloud Mail genToken returned no token')
        self._cloud_mail_tokens[cache_key] = token
        return token

    def _provision_cloud_mail(self, settings):
        base = self._base(settings, 'cloud_mail_api_base')
        if not base:
            raise MailProviderError('Cloud Mail API Base is required')
        token = self._cloud_mail_token(settings)
        headers = {'Authorization': token}
        config = self._json(
            'GET', f'{base}/api/setting/websiteConfig', settings,
            headers=headers,
        )
        domains = ((config.get('data') or {}).get('domainList') if isinstance(config, dict) else [])
        domain = self._pick_domain(domains, 'Cloud Mail')
        address = f'{self._random_username()}@{domain}'
        password = self._random_password()
        self._json(
            'POST', f'{base}/api/public/addUser', settings,
            headers={'Authorization': token, 'Content-Type': 'application/json'},
            json={'list': [{'email': address, 'password': password}]},
        )
        return ProvisionedMailbox('cloud_mail', address, token)

    def _fetch_messages(self, provider, address, credential, settings):
        if provider == 'duckmail':
            base = self._base(settings, 'duckmail_api_base', 'https://api.duckmail.sbs')
            data = self._json(
                'GET', f'{base}/messages', settings,
                headers=self._auth_headers(token=credential),
            )
            return self._pick_list(data)
        if provider == 'yyds':
            base = self._base(settings, 'yyds_api_base', 'https://maliapi.215.im/v1')
            data = self._json(
                'GET', f'{base}/messages', settings,
                headers=self._yyds_headers(settings, credential=credential),
                params={'address': address},
            )
            if isinstance(data, dict) and data.get('success'):
                return ((data.get('data') or {}).get('messages') or [])
            return []
        if provider == 'cloudflare':
            base = self._base(settings, 'cloudflare_api_base')
            if not base:
                raise MailProviderError('Cloudflare cloudflare_api_base is required')
            path = self._path(settings, 'cloudflare_path_messages', '/api/mails')
            data = self._json(
                'GET', f'{base}{path}', settings,
                headers=self._auth_headers(token=credential),
                params=self._query_auth(settings, {'limit': 20, 'offset': 0}),
            )
            return self._pick_list(data)
        if provider == 'cloud_mail':
            base = self._base(settings, 'cloud_mail_api_base')
            if not base:
                raise MailProviderError('Cloud Mail cloud_mail_api_base is required')
            data = self._json(
                'POST', f'{base}/api/public/emailList', settings,
                headers={'Authorization': credential, 'Content-Type': 'application/json'},
                json={'toEmail': address, 'size': 20},
            )
            return data.get('data') or [] if isinstance(data, dict) else []
        return []

    def _message_content(self, provider, message, credential, settings):
        detail = {}
        message_id = self._message_id(message)
        if message_id and provider in ('duckmail', 'yyds', 'cloudflare'):
            detail = self._fetch_detail(
                provider, message_id, credential, settings,
            )
        subject = str(detail.get('subject') or message.get('subject') or '')
        parts = []
        for source in (message, detail):
            if not isinstance(source, dict):
                continue
            for field in ('text', 'raw', 'content', 'intro', 'body', 'snippet'):
                value = source.get(field)
                if not isinstance(value, str) or not value.strip():
                    continue
                # Cloud Mail puts full HTML in ``content``; strip tags so
                # extractors see the human-readable body, not CSS tokens.
                if field == 'content' and '<' in value and '>' in value:
                    parts.append(re.sub(r'<[^>]+>', ' ', html.unescape(value)))
                else:
                    parts.append(value)
            html_values = source.get('html') or []
            if isinstance(html_values, str):
                html_values = [html_values]
            for value in html_values:
                if isinstance(value, str):
                    parts.append(re.sub(r'<[^>]+>', ' ', html.unescape(value)))
        return subject, '\n'.join(parts)

    def _fetch_detail(self, provider, message_id, credential, settings):
        if provider == 'duckmail':
            base = self._base(settings, 'duckmail_api_base', 'https://api.duckmail.sbs')
            return self._json(
                'GET', f'{base}/messages/{message_id}', settings,
                headers=self._auth_headers(token=credential),
            )
        if provider == 'yyds':
            base = self._base(settings, 'yyds_api_base', 'https://maliapi.215.im/v1')
            data = self._json(
                'GET', f'{base}/messages/{message_id}', settings,
                headers=self._yyds_headers(settings, credential=credential),
            )
            return data.get('data', {}) if isinstance(data, dict) and data.get('success') else {}
        if provider == 'cloudflare':
            base = self._base(settings, 'cloudflare_api_base')
            path = self._path(settings, 'cloudflare_path_messages', '/api/mails')
            errors = []
            for url in (f'{base}/api/mail/{message_id}', f'{base}{path}/{message_id}'):
                try:
                    data = self._json(
                        'GET', url, settings,
                        headers=self._auth_headers(token=credential),
                        params=self._query_auth(settings),
                    )
                    if isinstance(data, dict) and isinstance(data.get('data'), dict):
                        return data['data']
                    return data if isinstance(data, dict) else {}
                except Exception as exc:
                    errors.append(str(exc))
            raise MailProviderError('; '.join(errors))
        return {}

    @staticmethod
    def _message_id(message):
        return str(
            message.get('id')
            or message.get('msgid')
            or message.get('emailId')
            or ''
        )

    @staticmethod
    def _recipient_matches(message, address):
        target = str(address or '').strip().lower()
        recipients = []
        for item in message.get('to') or []:
            if isinstance(item, str):
                recipients.append(item.lower())
            elif isinstance(item, dict):
                recipients.append(str(item.get('address') or item.get('email') or '').lower())
        direct = str(
            message.get('toEmail')
            or message.get('address')
            or message.get('recipient')
            or ''
        ).strip().lower()
        if recipients:
            return target in recipients
        if direct:
            return direct == target
        # These are isolated mailboxes; some adapters omit recipient metadata.
        return True

    @classmethod
    def _message_is_older(cls, message, cutoff):
        for key in ('createdAt', 'created_at', 'receivedAt', 'received_at', 'date'):
            parsed = cls._as_utc(message.get(key))
            if parsed:
                return parsed < cutoff
        return False

    @staticmethod
    def _as_utc(value):
        if not value:
            return None
        if isinstance(value, datetime):
            parsed = value
        else:
            try:
                parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
            except (TypeError, ValueError):
                return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
