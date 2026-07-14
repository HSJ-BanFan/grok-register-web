import logging
import threading
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urlencode

import requests

from urllib.parse import urlparse as _urlparse
from config import AUTHORIZE_URL, TOKEN_URL, REDIRECT_URI, SCOPES, OAUTH_TIMEOUT

_CALLBACK_PORT = _urlparse(REDIRECT_URI).port or 53682

logger = logging.getLogger('register')


class PortInUseError(Exception):
    pass


class OAuthInProgressError(Exception):
    pass


class OAuthManager:
    def __init__(self, db):
        self.db = db
        self._server = None
        self._server_thread = None
        self._current_client_id = None
        self._status = {'authorized': False, 'email': '', 'time': ''}
        self._lock = threading.Lock()

    def start_authorization(self, client_id):
        with self._lock:
            if self._server_thread and self._server_thread.is_alive():
                raise OAuthInProgressError("Authorization is already in progress")
            self._current_client_id = client_id

        auth_url = self._build_auth_url(client_id)
        self._start_callback_server()
        return auth_url

    def get_status(self):
        return dict(self._status)

    def _build_auth_url(self, client_id):
        params = {
            'client_id': client_id,
            'response_type': 'code',
            'redirect_uri': REDIRECT_URI,
            'scope': SCOPES,
        }
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    def _start_callback_server(self):
        manager = self

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlparse(self.path)
                params = parse_qs(parsed.query)
                if 'code' in params:
                    code = params['code'][0]
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.end_headers()
                    self.wfile.write(b'<html><body><h2>Authorization successful! You can close this window.</h2></body></html>')
                    threading.Thread(target=manager._handle_callback, args=(code,), daemon=True).start()
                else:
                    error = params.get('error', ['unknown'])[0]
                    self.send_response(400)
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.end_headers()
                    self.wfile.write(f'<html><body><h2>Authorization failed: {error}</h2></body></html>'.encode())
                    manager._shutdown_server()

            def log_message(self, format, *args):
                logger.debug(f"OAuth callback: {format % args}")

        try:
            self._server = HTTPServer(('127.0.0.1', _CALLBACK_PORT), CallbackHandler)
        except OSError:
            raise PortInUseError(f"Port {_CALLBACK_PORT} is in use. Please close the occupying program and try again.")

        self._server.timeout = OAUTH_TIMEOUT

        def run_server():
            logger.info("OAuth callback server started on port 53682")
            self._server.serve_forever(poll_interval=0.5)
            logger.info("OAuth callback server stopped")

        self._server_thread = threading.Thread(target=run_server, daemon=True)
        self._server_thread.start()

        timer = threading.Timer(OAUTH_TIMEOUT, self._shutdown_server)
        timer.daemon = True
        timer.start()

    def _shutdown_server(self):
        try:
            if self._server:
                self._server.shutdown()
                self._server = None
        except Exception:
            pass

    def _handle_callback(self, code):
        try:
            data = {
                'client_id': self._current_client_id,
                'code': code,
                'grant_type': 'authorization_code',
                'redirect_uri': REDIRECT_URI,
            }
            resp = requests.post(TOKEN_URL, data=data, timeout=30)
            resp.raise_for_status()
            token_data = resp.json()

            access_token = token_data['access_token']
            refresh_token = token_data.get('refresh_token', '')

            user_resp = requests.get(
                'https://graph.microsoft.com/v1.0/me',
                headers={'Authorization': f'Bearer {access_token}'},
                timeout=15
            )
            user_resp.raise_for_status()
            user_info = user_resp.json()
            email = user_info.get('mail') or user_info.get('userPrincipalName', '')

            if email and refresh_token:
                account_id = self.db.upsert_account(
                    email=email,
                    password='',
                    client_id=self._current_client_id,
                    refresh_token=refresh_token
                )
                from datetime import datetime
                self._status = {
                    'authorized': True,
                    'email': email,
                    'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                }
                logger.info(f"OAuth authorized for {email}")
            else:
                logger.error("OAuth callback: missing email or refresh_token")

        except Exception as e:
            logger.error(f"OAuth callback failed: {e}")
        finally:
            self._shutdown_server()
