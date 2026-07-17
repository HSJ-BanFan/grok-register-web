import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

from config import SCOPES, TOKEN_URL
from core.mail_providers import (
    MICROSOFT_PROVIDER,
    MailProviderError,
    TemporaryMailboxProviders,
    extract_verification_code,
    normalize_provider,
)


logger = logging.getLogger('register')


class EmailError(Exception):
    pass


class EmailPermissionError(EmailError):
    """The mailbox token cannot read mail and retrying will not help."""


class EmailManager:
    def __init__(self, db):
        self.db = db
        self.providers = TemporaryMailboxProviders()
        self._seen_message_ids = {'graph': set(), 'outlook': set()}
        self._seen_message_lock = threading.Lock()

    def claim_registration_alias(self, settings, max_retries, lease_owner,
                                 lease_seconds):
        """Claim an imported Microsoft alias or provision one temporary mailbox."""
        try:
            provider = normalize_provider(
                (settings or {}).get('email_provider', MICROSOFT_PROVIDER)
            )
        except MailProviderError as exc:
            raise EmailError(str(exc)) from exc

        alias = self.db.claim_next_alias(
            max_retries=max_retries,
            lease_owner=lease_owner,
            lease_seconds=lease_seconds,
            provider=provider,
        )
        if alias or provider == MICROSOFT_PROVIDER:
            return alias

        try:
            mailbox = self.providers.provision(provider, settings or {})
        except MailProviderError as exc:
            raise EmailError(str(exc)) from exc
        self.db.create_temporary_account(
            mailbox.address,
            mailbox.provider,
            mailbox.credential,
        )
        return self.db.claim_next_alias(
            max_retries=max_retries,
            lease_owner=lease_owner,
            lease_seconds=lease_seconds,
            provider=provider,
        )

    def _get_seen_message_ids(self, mail_api):
        with self._seen_message_lock:
            return set(self._seen_message_ids.setdefault(mail_api, set()))

    def _remember_message_ids(self, mail_api, message_ids):
        with self._seen_message_lock:
            known = self._seen_message_ids.setdefault(mail_api, set())
            known.update(message_ids)
            # Bound memory in a long-running local process. Message IDs are
            # only a second line of defence behind recipient and timestamp.
            if len(known) > 2000:
                self._seen_message_ids[mail_api] = set(list(known)[-1000:])

    def refresh_token(self, account_id, client_id, old_refresh_token):
        """Refresh a mail token and match it to its authorized API audience."""
        endpoints = [
            # Legacy imported tokens commonly issue opaque Outlook REST tokens
            # only when no scope override is supplied.
            (TOKEN_URL, {}),
            ('https://login.microsoftonline.com/consumers/oauth2/v2.0/token', {}),
            # Newly authorized accounts use Microsoft Graph Mail.Read.
            (
                'https://login.microsoftonline.com/consumers/oauth2/v2.0/token',
                {'scope': SCOPES},
            ),
            (TOKEN_URL, {'scope': SCOPES}),
        ]
        errors = []
        for url, extra in endpoints:
            try:
                data = {
                    'client_id': client_id,
                    'refresh_token': old_refresh_token,
                    'grant_type': 'refresh_token',
                    **extra,
                }
                response = requests.post(url, data=data, timeout=30)
                try:
                    token_data = response.json()
                except ValueError:
                    token_data = {}
                logger.info(
                    'Token endpoint: status=%s, has_access_token=%s, error=%s',
                    response.status_code,
                    bool(token_data.get('access_token')),
                    token_data.get('error', 'none'),
                )
                access_token = token_data.get('access_token')
                if access_token:
                    new_token = token_data.get('refresh_token', old_refresh_token)
                    self.db.update_refresh_token(account_id, new_token)
                    old_refresh_token = new_token
                    mail_api, probe_error = self._detect_mail_api(access_token)
                    if mail_api:
                        logger.info(
                            'Mailbox token refreshed OK: api=%s, token_len=%s',
                            mail_api,
                            len(access_token),
                        )
                        return new_token, access_token, mail_api
                    errors.append(probe_error)
                    continue

                description = str(
                    token_data.get('error_description')
                    or token_data.get('error')
                    or response.text
                    or 'no access token'
                ).strip()
                errors.append(f'HTTP {response.status_code}: {description[:160]}')
            except Exception as exc:
                errors.append(str(exc)[:160])
                logger.warning('Token endpoint failed: %s', exc)

        detail = ' | '.join(errors)
        raise EmailPermissionError(
            f'Microsoft mailbox token refresh failed or access check failed'
            f'{f": {detail}" if detail else ""}'
        )

    def get_verification_code(self, email_addr, client_id, refresh_token,
                              max_retries=3, account_id=None, main_email=None,
                              requested_after=None):
        """Poll the token's mail API for the verification mail sent to an alias."""
        # _fill_email() has just completed the xAI send-code request. A small
        # clock tolerance avoids excluding mail stamped a few seconds earlier
        # by Microsoft's servers while still rejecting codes from old rounds.
        requested_after = requested_after or datetime.now(timezone.utc) - timedelta(minutes=1)
        if requested_after.tzinfo is None:
            requested_after = requested_after.replace(tzinfo=timezone.utc)
        requested_after = requested_after.astimezone(timezone.utc) - timedelta(seconds=5)
        # The OAuth token belongs to the main mailbox; plus-address aliases are
        # delivered to that same mailbox, so no provider/address switch is needed.
        _, access_token, mail_api = self.refresh_token(
            account_id or 0, client_id, refresh_token,
        )
        seen_message_ids = self._get_seen_message_ids(mail_api)

        # Give xAI a moment to deliver the email before the first poll.
        time.sleep(8)

        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(
                    'Fetching verification code for %s via %s... (attempt %s/%s)',
                    email_addr,
                    mail_api,
                    attempt,
                    max_retries,
                )
                if mail_api == 'graph':
                    code = self._graph_get_code(
                        access_token,
                        target_email=email_addr,
                        main_email=main_email,
                        received_after=requested_after,
                        seen_message_ids=seen_message_ids,
                    )
                else:
                    code = self._outlook_get_code(
                        access_token,
                        target_email=email_addr,
                        main_email=main_email,
                        received_after=requested_after,
                        seen_message_ids=seen_message_ids,
                    )
                self._remember_message_ids(mail_api, seen_message_ids)
                if code:
                    logger.info(
                        'Verification code obtained (%s): %s',
                        mail_api,
                        code,
                    )
                    return code
            except EmailPermissionError:
                # Repeating the same request or switching aliases cannot make
                # an unreadable token gain mail access.
                raise
            except EmailError as exc:
                self._remember_message_ids(mail_api, seen_message_ids)
                last_error = exc
                logger.warning('%s mail attempt %s failed: %s', mail_api, attempt, exc)

            if attempt < max_retries:
                time.sleep(5)

        if last_error:
            raise EmailError(
                f'Failed to get verification code after {max_retries} attempts; '
                f'last mail error: {last_error}'
            )
        raise EmailError(
            f'Failed to get verification code after {max_retries} attempts'
        )

    def _detect_mail_api(self, access_token):
        """Return the mail API authorized for an imported access token."""
        probes = (
            (
                'graph',
                'https://graph.microsoft.com/v1.0/me/messages?$top=1&$select=id',
            ),
            (
                'outlook',
                'https://outlook.office.com/api/v2.0/me/messages?$top=1&$select=Id',
            ),
        )
        errors = []
        for name, url in probes:
            try:
                response = requests.get(
                    url,
                    headers={
                        'Authorization': f'Bearer {access_token}',
                        'Accept': 'application/json',
                    },
                    timeout=30,
                )
            except Exception as exc:
                errors.append(f'{name} probe: {exc}')
                continue
            if response.status_code == 200:
                return name, ''
            code, message = self._mail_api_error(response)
            detail = ': '.join(part for part in (code, message) if part)
            errors.append(
                f'{name} probe HTTP {response.status_code}'
                f'{f" ({detail})" if detail else ""}'
            )
        return None, '; '.join(errors)

    @staticmethod
    def _normalize_email(value):
        return str(value or '').strip().lower()

    @classmethod
    def _recipient_match(cls, recipients, target_email, main_email=None):
        """Return True for exact match, False for mismatch, None if ambiguous."""
        target = cls._normalize_email(target_email)
        main = cls._normalize_email(main_email)
        normalized = {
            cls._normalize_email(item) for item in recipients if item
        }
        if not target:
            return None
        if target in normalized:
            return True
        if not normalized:
            return None
        # Microsoft may rewrite a plus-address recipient to the mailbox's main
        # address. This cannot identify the alias by itself, so mark it as
        # ambiguous and let the caller apply the strict fallback policy.
        if main and main in normalized and '+' in target.split('@', 1)[0]:
            return None
        return False

    @classmethod
    def _header_recipients(cls, headers):
        result = []
        recipient_headers = {
            'to', 'delivered-to', 'x-original-to', 'envelope-to',
        }
        for header in headers or []:
            if not isinstance(header, dict):
                continue
            name = str(header.get('name') or header.get('Name') or '').lower()
            if name not in recipient_headers:
                continue
            value = str(header.get('value') or header.get('Value') or '')
            result.extend(re.findall(
                r'[A-Z0-9.!#$%&\'*+/=?^_`{|}~-]+@[A-Z0-9.-]+',
                value,
                re.IGNORECASE,
            ))
        return result

    @classmethod
    def _graph_recipients(cls, message):
        result = []
        for recipient in message.get('toRecipients') or []:
            if isinstance(recipient, str):
                result.append(recipient)
                continue
            email = recipient.get('emailAddress') or {}
            if isinstance(email, dict):
                result.append(email.get('address') or '')
        result.extend(cls._header_recipients(
            message.get('internetMessageHeaders') or []
        ))
        return result

    @classmethod
    def _outlook_recipients(cls, message):
        result = []
        for recipient in message.get('ToRecipients') or []:
            if isinstance(recipient, str):
                result.append(recipient)
                continue
            email = recipient.get('EmailAddress') or {}
            if isinstance(email, dict):
                result.append(email.get('Address') or '')
        result.extend(cls._header_recipients(
            message.get('InternetMessageHeaders') or []
        ))
        return result

    @classmethod
    def _allow_ambiguous_recipient_fallback(cls, target_email, main_email=None):
        target = cls._normalize_email(target_email)
        main = cls._normalize_email(main_email)
        if not target:
            return True
        if main and target == main:
            return True
        return '+' not in target.split('@', 1)[0]

    @staticmethod
    def _received_at(value):
        try:
            parsed = datetime.fromisoformat(str(value or '').replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _message_fingerprint(message, graph=True):
        if graph:
            return str(
                message.get('id')
                or f"{message.get('receivedDateTime', '')}|{message.get('subject', '')}"
            )
        return str(
            message.get('Id')
            or f"{message.get('ReceivedDateTime', '')}|{message.get('Subject', '')}"
        )

    def _graph_get_code(self, access_token, target_email=None, main_email=None,
                        received_after=None, seen_message_ids=None):
        """Fetch recent verification messages through Microsoft Graph."""
        import html as html_module

        logger.info('Graph _graph_get_code: token_len=%s', len(access_token))
        cutoff = received_after or datetime.now(timezone.utc) - timedelta(minutes=5)
        seen_message_ids = seen_message_ids if seen_message_ids is not None else set()
        url = (
            'https://graph.microsoft.com/v1.0/me/messages'
            '?$top=25'
            '&$select=id,subject,from,toRecipients,internetMessageHeaders,'
            'receivedDateTime,bodyPreview,body'
            '&$orderby=receivedDateTime desc'
        )
        response = requests.get(
            url,
            headers={
                'Authorization': f'Bearer {access_token}',
                'Accept': 'application/json',
            },
            timeout=30,
        )
        if response.status_code != 200:
            code, message = self._mail_api_error(response)
            detail = ': '.join(part for part in (code, message) if part)
            error = (
                f'Microsoft Graph mail failed: HTTP {response.status_code}'
                f'{f" ({detail})" if detail else ""}'
            )
            if response.status_code in (401, 403):
                raise EmailPermissionError(error)
            raise EmailError(error)

        try:
            messages = response.json().get('value', [])
        except (AttributeError, ValueError) as exc:
            raise EmailError(f'Microsoft Graph returned invalid JSON: {exc}') from exc

        keywords = (
            'x.ai', 'xai', 'grok', 'verification',
            'code', 'confirm', 'confirmation',
        )
        fallback_codes = []
        for message in messages:
            message_id = self._message_fingerprint(message, graph=True)
            if message_id in seen_message_ids:
                continue
            received = message.get('receivedDateTime') or ''
            received_at = self._received_at(received)
            if received_at and received_at < cutoff:
                continue

            subject = message.get('subject') or ''
            sender_data = message.get('from') or {}
            email_address = (
                sender_data.get('emailAddress') or {}
                if isinstance(sender_data, dict) else {}
            )
            sender = (
                f"{email_address.get('name', '')} "
                f"{email_address.get('address', '')}"
            )
            preview = message.get('bodyPreview') or ''
            body_data = message.get('body') or {}
            if not isinstance(body_data, dict):
                body_data = {}
            raw_body = body_data.get('content') or ''
            if (body_data.get('contentType') or '').upper() == 'HTML':
                body = re.sub(
                    r'\s+',
                    ' ',
                    re.sub(
                        r'<[^>]+>',
                        ' ',
                        html_module.unescape(raw_body),
                    ),
                ).strip()
            else:
                body = raw_body

            combined = f'{subject} {sender} {preview} {body}'.lower()
            if not any(keyword in combined for keyword in keywords):
                seen_message_ids.add(message_id)
                continue

            code = self._extract_code_from_email(
                f'{subject}\n{preview}\n{body}'
            )
            if code:
                seen_message_ids.add(message_id)
                matched = self._recipient_match(
                    self._graph_recipients(message),
                    target_email,
                    main_email,
                )
                if matched is True:
                    return code
                if matched is None and self._allow_ambiguous_recipient_fallback(
                    target_email, main_email,
                ):
                    fallback_codes.append(code)

        if len(fallback_codes) == 1:
            logger.info(
                'Graph recipient metadata was ambiguous; using the only new xAI message'
            )
            return fallback_codes[0]
        if len(fallback_codes) > 1:
            logger.warning(
                'Graph returned multiple new xAI messages without an exact recipient match'
            )

        logger.debug('No verification code found via Microsoft Graph')
        return None

    def _outlook_get_code(self, access_token, target_email=None, main_email=None,
                          received_after=None, seen_message_ids=None):
        """Fetch mail for legacy opaque tokens authorized for Outlook REST."""
        import html as html_module

        logger.info('Outlook _outlook_get_code: token_len=%s', len(access_token))
        cutoff = received_after or datetime.now(timezone.utc) - timedelta(minutes=5)
        seen_message_ids = seen_message_ids if seen_message_ids is not None else set()
        url = (
            'https://outlook.office.com/api/v2.0/me/messages'
            '?$top=25'
            '&$select=Id,Subject,From,ToRecipients,InternetMessageHeaders,'
            'ReceivedDateTime,BodyPreview,Body'
            '&$orderby=ReceivedDateTime desc'
        )
        response = requests.get(
            url,
            headers={
                'Authorization': f'Bearer {access_token}',
                'Accept': 'application/json',
            },
            timeout=30,
        )
        if response.status_code != 200:
            code, message = self._mail_api_error(response)
            detail = ': '.join(part for part in (code, message) if part)
            error = (
                f'Outlook REST mail failed: HTTP {response.status_code}'
                f'{f" ({detail})" if detail else ""}'
            )
            if response.status_code in (401, 403):
                raise EmailPermissionError(error)
            raise EmailError(error)

        try:
            messages = response.json().get('value', [])
        except (AttributeError, ValueError) as exc:
            raise EmailError(f'Outlook REST returned invalid JSON: {exc}') from exc

        keywords = (
            'x.ai', 'xai', 'grok', 'verification',
            'code', 'confirm', 'confirmation',
        )
        fallback_codes = []
        for message in messages:
            message_id = self._message_fingerprint(message, graph=False)
            if message_id in seen_message_ids:
                continue
            received = message.get('ReceivedDateTime') or ''
            received_at = self._received_at(received)
            if received_at and received_at < cutoff:
                continue

            subject = message.get('Subject') or ''
            sender_data = message.get('From') or {}
            email_address = (
                sender_data.get('EmailAddress') or {}
                if isinstance(sender_data, dict) else {}
            )
            sender = (
                f"{email_address.get('Name', '')} "
                f"{email_address.get('Address', '')}"
            )
            preview = message.get('BodyPreview') or ''
            body_data = message.get('Body') or {}
            if not isinstance(body_data, dict):
                body_data = {}
            raw_body = body_data.get('Content') or ''
            if (body_data.get('ContentType') or '').upper() == 'HTML':
                body = re.sub(
                    r'\s+',
                    ' ',
                    re.sub(
                        r'<[^>]+>',
                        ' ',
                        html_module.unescape(raw_body),
                    ),
                ).strip()
            else:
                body = raw_body

            combined = f'{subject} {sender} {preview} {body}'.lower()
            if not any(keyword in combined for keyword in keywords):
                seen_message_ids.add(message_id)
                continue
            code = self._extract_code_from_email(
                f'{subject}\n{preview}\n{body}'
            )
            if code:
                seen_message_ids.add(message_id)
                matched = self._recipient_match(
                    self._outlook_recipients(message),
                    target_email,
                    main_email,
                )
                if matched is True:
                    return code
                if matched is None and self._allow_ambiguous_recipient_fallback(
                    target_email, main_email,
                ):
                    fallback_codes.append(code)

        if len(fallback_codes) == 1:
            logger.info(
                'Outlook recipient metadata was ambiguous; using the only new xAI message'
            )
            return fallback_codes[0]
        if len(fallback_codes) > 1:
            logger.warning(
                'Outlook returned multiple new xAI messages without an exact recipient match'
            )

        logger.debug('No verification code found via Outlook REST')
        return None

    @staticmethod
    def _mail_api_error(response):
        try:
            error = response.json().get('error') or {}
        except (AttributeError, ValueError):
            error = {}
        code = str(error.get('code') or '').strip()
        message = str(error.get('message') or '').strip()
        if not code and not message:
            message = str(getattr(response, 'text', '') or '')[:200].strip()
        return code, message

    def _extract_code_from_email(self, body):
        """Extract the xAI verification code from a message body."""
        return extract_verification_code(body)

    def get_code_for_alias(self, alias_email, account_id, client_id,
                           refresh_token, max_retries=3, main_email=None,
                           requested_after=None, provider=MICROSOFT_PROVIDER,
                           settings=None):
        """Fetch the code for the mailbox address already submitted to xAI.

        After the signup form has been filled with ``alias_email``, switching
        to another provider cannot receive the code and only burns rate limits.
        """
        try:
            provider = normalize_provider(provider)
        except MailProviderError as exc:
            raise EmailError(str(exc)) from exc
        if provider != MICROSOFT_PROVIDER:
            try:
                return self.providers.get_verification_code(
                    provider,
                    alias_email,
                    refresh_token,
                    settings or {},
                    max_retries=max_retries,
                    requested_after=requested_after,
                )
            except MailProviderError as exc:
                raise EmailError(str(exc)) from exc

        return self.get_verification_code(
            alias_email,
            client_id,
            refresh_token,
            max_retries,
            account_id=account_id,
            main_email=main_email,
            requested_after=requested_after,
        )
