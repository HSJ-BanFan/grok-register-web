import os
import sys
import argparse
import logging
import secrets
import webbrowser
import threading
import mimetypes

# Override any incorrect MIME mappings loaded from the Windows registry.
mimetypes.add_type('application/javascript', '.js')
mimetypes.add_type('text/css', '.css')

from flask import Flask, abort, render_template, request
from flask_socketio import SocketIO

from config import DEFAULT_HOST, DEFAULT_PORT
from core.database import Database
from core.browser import BrowserManager, redact_proxy_url
from core.email_manager import EmailManager
from core.runtime import resolve_browser_headless
from core.oauth import OAuthManager
from core.grok2api_retry import Grok2APIRetryWorker
from core.web_security import is_loopback_host, origin_matches_host
from api.accounts import init_accounts_api
from api.register import init_register_api
from api.results import init_results_api
from api.settings import init_settings_api
from api.websocket import init_websocket

# ── Logging setup ──────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ── Flask + SocketIO ───────────────────────────────────────
app = Flask(__name__,
            static_folder='static',
            template_folder='templates')
app.config.update(
    SECRET_KEY=os.environ.get('GROK_REGISTER_SECRET_KEY') or secrets.token_hex(32),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Strict',
)

# Flask-SocketIO's default same-origin validation is intentional here.
socketio = SocketIO(app, async_mode='threading')

# ── Core modules ───────────────────────────────────────────
db = Database()
browser_mgr = BrowserManager(
    headless=False,
    extension_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'turnstilePatch')
)
email_mgr = EmailManager(db)
oauth_mgr = OAuthManager(db)
grok2api_retry_worker = Grok2APIRetryWorker(db)

# ── Register API Blueprints ────────────────────────────────
app.register_blueprint(init_accounts_api(db, oauth_mgr))
app.register_blueprint(init_register_api(db, browser_mgr, email_mgr, socketio))
app.register_blueprint(init_results_api(db))
app.register_blueprint(init_settings_api(db))

# ── WebSocket ──────────────────────────────────────────────
import api.register as register_api
socket_handler = init_websocket(socketio, state_getter=lambda: register_api._state)
register_logger = logging.getLogger('register')
register_logger.setLevel(logging.INFO)
register_logger.addHandler(socket_handler)


@app.before_request
def enforce_same_origin_for_writes():
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        origin = request.headers.get('Origin', '')
        if origin and not origin_matches_host(origin, request.host):
            abort(403)

# ── Page route ─────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')

# ── Main ───────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='Grok Auto-Register Web Platform')
    parser.add_argument('--host', default=DEFAULT_HOST, help=f'Bind address (default: {DEFAULT_HOST})')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT, help=f'Port (default: {DEFAULT_PORT})')
    parser.add_argument(
        '--allow-remote', action='store_true',
        help='Allow binding to a non-loopback address (trusted networks only)',
    )
    args = parser.parse_args()

    is_loopback = is_loopback_host(args.host)
    if not is_loopback and not args.allow_remote:
        parser.error('Refusing non-loopback bind without --allow-remote')

    # Initialize database
    db.init_database()
    grok2api_retry_worker.start()

    # Recover stale registrations
    settings = db.get_settings()
    timeout = int(settings.get('registration_timeout', 300))
    db.recover_stale(timeout)

    # Update browser headless / proxy settings
    browser_mgr.headless = resolve_browser_headless(settings)
    browser_mgr.proxy = (settings.get('browser_proxy', '') or '').strip()
    if browser_mgr.proxy:
        logger.info('Browser proxy configured: %s', redact_proxy_url(browser_mgr.proxy))

    url = f'http://{"localhost" if args.host in ("127.0.0.1", "0.0.0.0") else args.host}:{args.port}'
    logger.info(f"Starting Grok Register Platform at {url}")

    # Open browser after a short delay
    if args.host in ('127.0.0.1', 'localhost'):
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    socketio.run(app, host=args.host, port=args.port, debug=False, allow_unsafe_werkzeug=True)


if __name__ == '__main__':
    main()
