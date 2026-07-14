import imaplib
import email
import re
import logging
import time

import requests

from config import TOKEN_URL

logger = logging.getLogger('register')


class EmailError(Exception):
    pass


class EmailManager:
    def __init__(self, db):
        self.db = db
        self._mail_tm_fallback = False
        self._imap_fail_count = 0

    def reset_fallback(self):
        self._mail_tm_fallback = False
        self._imap_fail_count = 0

    def refresh_token(self, account_id, client_id, old_refresh_token):
        """Refresh OAuth2 token, trying two endpoints like original script."""
        endpoints = [
            (TOKEN_URL, {}),
            ('https://login.microsoftonline.com/consumers/oauth2/v2.0/token',
             {'scope': 'offline_access https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/User.Read'}),
        ]
        for url, extra in endpoints:
            try:
                data = {
                    'client_id': client_id,
                    'refresh_token': old_refresh_token,
                    'grant_type': 'refresh_token',
                    **extra,
                }
                resp = requests.post(url, data=data, timeout=30)
                token_data = resp.json()
                logger.info(f"Token endpoint: status={resp.status_code}, has_access_token={bool(token_data.get('access_token'))}, error={token_data.get('error', 'none')}")
                if token_data.get('access_token'):
                    new_token = token_data.get('refresh_token', old_refresh_token)
                    self.db.update_refresh_token(account_id, new_token)
                    logger.info(f"Token refreshed OK, token_len={len(token_data['access_token'])}")
                    return new_token, token_data['access_token']
                else:
                    logger.warning(f"No access_token: {token_data.get('error_description', '')[:100]}")
            except Exception as e:
                logger.warning(f"Token endpoint failed: {e}")
                continue
        raise EmailError('Token refresh failed on all endpoints')

    def get_verification_code(self, email_addr, client_id, refresh_token, max_retries=3, account_id=None, main_email=None):
        # Refresh token once before all attempts (like original)
        new_refresh, access_token = self.refresh_token(account_id or 0, client_id, refresh_token)
        if new_refresh:
            refresh_token = new_refresh

        # OAuth2 token is bound to the main mailbox; plus-address aliases land there too.
        mailbox_email = main_email or email_addr

        # Give xAI a moment to deliver the email before the first poll.
        time.sleep(8)

        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"Fetching verification code... (attempt {attempt}/{max_retries})")
                # Prefer Outlook REST: consumer MSA tokens often auth IMAP then fail with
                # "User is authenticated but not connected".
                code = self._rest_get_code(access_token)
                if code:
                    logger.info(f"Verification code obtained (REST): {code}")
                    return code
            except Exception as e:
                logger.warning(f"REST mail attempt {attempt} failed: {e}")
                try:
                    code = self._imap_get_code(mailbox_email, access_token)
                    if code:
                        logger.info(f"Verification code obtained (IMAP): {code}")
                        return code
                except Exception as e2:
                    logger.warning(f"IMAP attempt {attempt} failed: {e2}")

            if attempt < max_retries:
                time.sleep(5)

        raise EmailError(f"Failed to get verification code after {max_retries} attempts")

    def _rest_get_code(self, access_token):
        """Fetch verification code via Outlook REST API (works with MSA OAuth tokens)."""
        from datetime import datetime, timezone, timedelta
        import html as _html

        logger.info(f"REST _rest_get_code: token_len={len(access_token)}")
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
        url = (
            "https://outlook.office.com/api/v2.0/me/messages"
            "?$top=15"
            "&$select=Subject,From,ReceivedDateTime,BodyPreview,Body"
            "&$orderby=ReceivedDateTime desc"
        )
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,
        )
        if resp.status_code != 200:
            raise EmailError(f"Outlook REST mail failed: HTTP {resp.status_code} {resp.text[:200]}")

        keywords = ["x.ai", "xai", "grok", "verification", "code", "confirm", "confirmation"]
        messages = resp.json().get("value", [])
        for msg in messages:
            received = msg.get("ReceivedDateTime") or ""
            try:
                # e.g. 2026-07-14T02:10:57Z
                dt = datetime.fromisoformat(received.replace("Z", "+00:00"))
                if dt < cutoff:
                    continue
            except Exception:
                pass

            subject = msg.get("Subject") or ""
            from_obj = msg.get("From") or {}
            sender = ""
            if isinstance(from_obj, dict):
                ea = from_obj.get("EmailAddress") or {}
                sender = f"{ea.get('Name', '')} {ea.get('Address', '')}"
            preview = msg.get("BodyPreview") or ""
            body_obj = msg.get("Body") or {}
            raw_body = body_obj.get("Content") or ""
            if (body_obj.get("ContentType") or "").upper() == "HTML":
                body = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _html.unescape(raw_body))).strip()
            else:
                body = raw_body

            combined = f"{subject} {sender} {preview} {body}".lower()
            if not any(kw in combined for kw in keywords):
                continue

            code = self._extract_code_from_email(f"{subject}\n{preview}\n{body}")
            if code:
                return code

        logger.debug("No verification code found via Outlook REST")
        return None

    def _imap_get_code(self, email_addr, access_token):
        """Fetch verification code from IMAP, matching original script's approach."""
        from email.utils import parsedate_to_datetime
        from datetime import datetime, timezone

        logger.info(f"IMAP _imap_get_code: email={email_addr}, token_len={len(access_token)}")
        imap = self._imap_connect(email_addr, access_token)

        # Only consider emails from the last 5 minutes (like original's filter_after_ts)
        filter_after_ts = int((time.time() - 300) * 1000)

        try:
            imap.select('INBOX')
            # Search ALL messages (like original), not just from specific sender
            status, data = imap.search(None, 'ALL')
            if status != 'OK' or not data or not data[0]:
                logger.debug("No emails found in inbox")
                return None

            msg_ids = data[0].split()[-10:]  # Last 10 messages (like original)
            keywords = ['x.ai', 'xai', 'grok', 'verification', 'code', 'confirm']

            for mid in reversed(msg_ids):  # Newest first
                _, msg_data = imap.fetch(mid, '(RFC822)')
                if not msg_data or not msg_data[0]:
                    continue
                raw_bytes = msg_data[0][1]
                if not isinstance(raw_bytes, bytes):
                    continue

                msg = email.message_from_bytes(raw_bytes)

                # Time filter: skip emails older than 5 minutes (like original)
                date_str = msg.get('Date')
                if date_str:
                    try:
                        dt = parsedate_to_datetime(date_str)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        msg_ts = int(dt.timestamp() * 1000)
                        if msg_ts < filter_after_ts:
                            continue
                    except Exception:
                        pass

                subject = msg.get('Subject', '')
                sender = msg.get('From', '')

                # Extract body
                body = ''
                if msg.is_multipart():
                    for part in msg.walk():
                        ct = part.get_content_type()
                        if ct == 'text/plain':
                            body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                            break
                        elif ct == 'text/html':
                            html_body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                            import re as _re
                            import html as _html
                            body = _re.sub(r'\s+', ' ', _re.sub(r'<[^>]+>', ' ', _html.unescape(html_body))).strip()
                else:
                    body = msg.get_payload(decode=True).decode('utf-8', errors='ignore')

                # Check keywords (like original)
                combined = f"{subject} {sender} {body}".lower()
                if not any(kw in combined for kw in keywords):
                    continue

                code = self._extract_code_from_email(f"{subject}\n{body}")
                if code:
                    return code

            # Fallback: try all messages without keyword filter (still time-filtered)
            for mid in reversed(msg_ids):
                _, msg_data = imap.fetch(mid, '(RFC822)')
                if not msg_data or not msg_data[0]:
                    continue
                raw_bytes = msg_data[0][1]
                if not isinstance(raw_bytes, bytes):
                    continue
                msg = email.message_from_bytes(raw_bytes)

                # Time filter
                date_str = msg.get('Date')
                if date_str:
                    try:
                        dt = parsedate_to_datetime(date_str)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        msg_ts = int(dt.timestamp() * 1000)
                        if msg_ts < filter_after_ts:
                            continue
                    except Exception:
                        pass

                subject = msg.get('Subject', '')
                body = ''
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == 'text/plain':
                            body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                            break
                else:
                    body = msg.get_payload(decode=True).decode('utf-8', errors='ignore')
                code = self._extract_code_from_email(f"{subject}\n{body}")
                if code:
                    return code

            logger.debug("No verification code found in recent emails")
            return None
        finally:
            try:
                imap.close()
                imap.logout()
            except Exception:
                pass

    def _imap_connect(self, email_addr, access_token):
        logger.info(f"IMAP connect: {email_addr}, token_len={len(access_token)}")
        imap = imaplib.IMAP4_SSL('outlook.office365.com', 993, timeout=45)
        auth_string = f"user={email_addr}\x01auth=Bearer {access_token}\x01\x01"
        imap.authenticate('XOAUTH2', lambda x: auth_string.encode())
        logger.info("IMAP authenticated successfully")
        return imap

    def _extract_code_from_email(self, body):
        """Extract verification code, matching original script's patterns."""
        # Try XXX-XXX format first (like original)
        m = re.search(r'\b([A-Z0-9]{3}-[A-Z0-9]{3})\b.*confirmation\s*code', body or '', re.IGNORECASE)
        if m:
            return m.group(1).upper().replace('-', '')

        patterns = [
            r'code\s+below\s+to\s+validate.*?\n\s*([A-Z0-9]{3}-[A-Z0-9]{3})\s*\n',
            r'\b([A-Z0-9]{3}-[A-Z0-9]{3})\b',
            r'(?:verification\s*code|code\s+)[:\s]+(\d{6})',
            r'(?:验证码|代码|确认码)[:\s为]+(\d{6})',
            r'(?:code|验证码)[:\s]+(\d{6})',
            r'\b(\d{6})\b',
            r'\b([A-Z0-9]{6})\b',
        ]
        for pattern in patterns:
            match = re.search(pattern, body or '', re.IGNORECASE | re.DOTALL)
            if match:
                code = match.group(1)
                return code.upper().replace('-', '') if '-' in code else code.upper()
        return None

    # ── Mail.tm fallback ───────────────────────────────────────

    def _mail_tm_get_email_and_token(self):
        try:
            domains_resp = requests.get('https://api.mail.tm/domains', timeout=15)
            domains_resp.raise_for_status()
            domains = domains_resp.json().get('hydra:member', [])
            if not domains:
                raise EmailError("No Mail.tm domains available")
            domain = domains[0]['domain']

            import random, string
            addr = ''.join(random.choices(string.ascii_lowercase + string.digits, k=12)) + f'@{domain}'
            password = ''.join(random.choices(string.ascii_letters + string.digits, k=16))

            create_resp = requests.post('https://api.mail.tm/accounts', json={
                'address': addr,
                'password': password,
            }, timeout=15)
            create_resp.raise_for_status()

            token_resp = requests.post('https://api.mail.tm/token', json={
                'address': addr,
                'password': password,
            }, timeout=15)
            token_resp.raise_for_status()
            token = token_resp.json()['token']

            logger.info(f"Mail.tm fallback: created {addr}")
            return addr, token
        except Exception as e:
            raise EmailError(f"Mail.tm fallback failed: {e}")

    def _mail_tm_get_code(self, mail_tm_email, mail_tm_token, max_retries=3):
        for attempt in range(1, max_retries + 1):
            try:
                resp = requests.get(
                    'https://api.mail.tm/messages',
                    headers={'Authorization': f'Bearer {mail_tm_token}'},
                    timeout=15
                )
                resp.raise_for_status()
                messages = resp.json().get('hydra:member', [])
                for msg in messages:
                    body = msg.get('text', '') or msg.get('html', [''])[0] if msg.get('html') else msg.get('text', '')
                    code = self._extract_code_from_email(str(body))
                    if code:
                        return code
            except Exception as e:
                logger.warning(f"Mail.tm attempt {attempt} failed: {e}")
            if attempt < max_retries:
                time.sleep(5)
        raise EmailError("Mail.tm: failed to get verification code")

    # ── Fallback orchestration ─────────────────────────────────

    def get_code_with_fallback(self, alias_email, account_id, client_id, refresh_token,
                                max_retries=3, on_fallback_notify=None, main_email=None):
        if self._mail_tm_fallback:
            logger.info("Using Mail.tm (fallback active this round)")
            mtm_email, mtm_token = self._mail_tm_get_email_and_token()
            return mtm_email, self._mail_tm_get_code(mtm_email, mtm_token, max_retries)

        try:
            code = self.get_verification_code(alias_email, client_id, refresh_token, max_retries,
                                              account_id=account_id, main_email=main_email)
            self._imap_fail_count = 0
            return alias_email, code
        except EmailError:
            self._imap_fail_count += 1
            if self._imap_fail_count >= 3:
                logger.warning("IMAP failed 3 times, switching to Mail.tm for this round")
                self._mail_tm_fallback = True
                if on_fallback_notify:
                    on_fallback_notify()
                mtm_email, mtm_token = self._mail_tm_get_email_and_token()
                return mtm_email, self._mail_tm_get_code(mtm_email, mtm_token, max_retries)
            raise
