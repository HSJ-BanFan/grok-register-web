import os
import sys
import argparse
import logging
import webbrowser
import threading

from flask import Flask, render_template
from flask_socketio import SocketIO

from config import DEFAULT_HOST, DEFAULT_PORT
from core.database import Database
from core.browser import BrowserManager
from core.email_manager import EmailManager
from core.oauth import OAuthManager
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
app.config['SECRET_KEY'] = 'grok-register-local-key'

socketio = SocketIO(app, async_mode='threading', cors_allowed_origins='*')

# ── Core modules ───────────────────────────────────────────
db = Database()
browser_mgr = BrowserManager(
    headless=False,
    extension_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'turnstilePatch')
)
email_mgr = EmailManager(db)
oauth_mgr = OAuthManager(db)

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

# ── Page route ─────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')

# ── Main ───────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='Grok Auto-Register Web Platform')
    parser.add_argument('--host', default=DEFAULT_HOST, help=f'Bind address (default: {DEFAULT_HOST})')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT, help=f'Port (default: {DEFAULT_PORT})')
    args = parser.parse_args()

    # Initialize database
    db.init_database()

    # Recover stale registrations
    settings = db.get_settings()
    timeout = int(settings.get('registration_timeout', 300))
    db.recover_stale(timeout)

    # Update browser headless / proxy settings
    if settings.get('browser_headless', 'false') == 'true':
        browser_mgr.headless = True
    browser_mgr.proxy = (settings.get('browser_proxy', '') or '').strip()
    if browser_mgr.proxy:
        logger.info(f"Browser proxy configured: {browser_mgr.proxy}")

    url = f'http://{"localhost" if args.host in ("127.0.0.1", "0.0.0.0") else args.host}:{args.port}'
    logger.info(f"Starting Grok Register Platform at {url}")

    # Open browser after a short delay
    if args.host in ('127.0.0.1', 'localhost'):
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    socketio.run(app, host=args.host, port=args.port, debug=False, allow_unsafe_werkzeug=True)


if __name__ == '__main__':
    main()
