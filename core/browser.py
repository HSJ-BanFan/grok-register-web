import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger('register')


class BrowserError(Exception):
    pass


def _contains_winerror(exc, code):
    """Return whether an exception chain contains a Windows error code."""
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if getattr(current, 'winerror', None) == code:
            return True
        if getattr(current, 'errno', None) == code:
            return True
        current = getattr(current, '__cause__', None) or getattr(current, '__context__', None)
    return False


def _windows_chrome_candidates():
    """Return common Chrome executable locations on Windows."""
    if not sys.platform.startswith('win'):
        return []
    roots = [
        os.environ.get('PROGRAMFILES'),
        os.environ.get('PROGRAMFILES(X86)'),
        os.environ.get('LOCALAPPDATA'),
    ]
    suffix = os.path.join('Google', 'Chrome', 'Application', 'chrome.exe')
    return [os.path.join(root, suffix) for root in roots if root]


def _browser_path_candidates(configured_path, default_path='chrome'):
    """Build deterministic browser path fallbacks without hiding explicit config."""
    configured = (configured_path or '').strip()
    if configured:
        return [configured]

    candidates = []
    for path in (default_path, *_windows_chrome_candidates()):
        path = str(path or '').strip()
        if path and path not in candidates:
            candidates.append(path)
    return candidates


def _parsed_proxy_url(proxy):
    value = (proxy or '').strip()
    if not value:
        return None
    parsed = urlsplit(value if '://' in value else f'http://{value}')
    if not parsed.hostname:
        raise BrowserError(f'Invalid browser proxy URL: {value}')
    return parsed


def redact_proxy_url(proxy):
    """Return a log-safe proxy URL without embedded credentials."""
    parsed = _parsed_proxy_url(proxy)
    if parsed is None:
        return ''
    host = parsed.hostname or ''
    if ':' in host and not host.startswith('['):
        host = f'[{host}]'
    try:
        port = parsed.port
    except ValueError as exc:
        raise BrowserError(f'Invalid browser proxy port: {exc}') from exc
    netloc = f'{host}:{port}' if port else host
    return urlunsplit((parsed.scheme or 'http', netloc, '', '', ''))


def validate_proxy_endpoint(proxy, timeout=3):
    """Verify that the configured proxy endpoint is reachable from this process."""
    parsed = _parsed_proxy_url(proxy)
    if parsed is None:
        return None
    try:
        port = parsed.port
    except ValueError as exc:
        raise BrowserError(f'Invalid browser proxy port: {exc}') from exc
    if port is None:
        port = (
            443 if parsed.scheme == 'https'
            else 1080 if parsed.scheme.startswith('socks')
            else 80
        )
    try:
        connection = socket.create_connection((parsed.hostname, port), timeout=timeout)
        connection.close()
    except OSError as exc:
        safe_proxy = redact_proxy_url(proxy)
        raise BrowserError(
            f'Browser proxy is not reachable from this process/network namespace: '
            f'{safe_proxy} ({type(exc).__name__}: {exc})'
        ) from exc
    return parsed.hostname, port


class BrowserManager:
    def __init__(self, headless=False, extension_path=None, user_data_path=None,
                 proxy='', browser_path=''):
        self.headless = headless
        self.extension_path = extension_path
        self.user_data_path = user_data_path
        self.proxy = (proxy or '').strip()
        self.browser_path = (browser_path or '').strip()
        self._browser = None
        self._page = None
        self._runtime_user_data_path = None
        self._owns_runtime_user_data = False

    def clone(self, worker_id=None):
        """Create an isolated browser manager for one registration worker."""
        user_data_path = self.user_data_path
        if user_data_path and worker_id:
            user_data_path = f'{user_data_path}-worker-{worker_id}'
        return BrowserManager(
            headless=self.headless,
            extension_path=self.extension_path,
            user_data_path=user_data_path,
            proxy=self.proxy,
            browser_path=self.browser_path,
        )

    def _prepare_user_data_path(self):
        if self.user_data_path:
            path = os.path.abspath(self.user_data_path)
            os.makedirs(path, exist_ok=True)
            self._owns_runtime_user_data = False
        else:
            path = tempfile.mkdtemp(prefix='grok-register-browser-')
            self._owns_runtime_user_data = True
        self._runtime_user_data_path = path
        return path

    def start(self):
        from DrissionPage import Chromium, ChromiumOptions
        logger.info("Starting browser...")
        if sys.platform.startswith('linux') and not self.headless:
            if not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
                raise BrowserError(
                    'Headful Chrome requires DISPLAY/WAYLAND_DISPLAY on Linux. '
                    'Start the application with scripts/run_with_xvfb.sh.'
                )
        if self.proxy:
            validate_proxy_endpoint(self.proxy)
            logger.info(
                'Browser proxy endpoint reachable from current network namespace: %s',
                redact_proxy_url(self.proxy),
            )
        co = ChromiumOptions()
        co.auto_port()
        co.set_timeouts(base=1)
        # Prevent Chrome from opening its own tabs (welcome, onboarding, etc.)
        co.set_argument('--no-first-run')
        co.set_argument('--no-default-browser-check')
        co.set_argument('--disable-features=ChromeWhatsNewUI')
        # Reduce common automation fingerprints for Cloudflare managed challenges.
        co.set_argument('--disable-blink-features=AutomationControlled')
        co.set_argument('--disable-infobars')
        co.set_argument('--lang=en-US')
        co.set_argument('--window-size=1365,900')
        # Docker / root chromium needs these or startup hangs / crashes.
        for _flag in (
            '--no-sandbox',
            '--disable-dev-shm-usage',
            '--disable-gpu',
            '--disable-software-rasterizer',
            '--mute-audio',
            '--disable-background-networking',
            '--remote-allow-origins=*',
        ):
            co.set_argument(_flag)
        co.set_pref('credentials_enable_service', False)
        co.set_pref('profile.password_manager_enabled', False)

        proxy = (self.proxy or '').strip()
        if proxy:
            # DrissionPage set_proxy only supports HTTP(S). Chrome accepts socks5 via
            # --proxy-server; never call set_proxy for socks or startup can hang.
            scheme = proxy.split('://', 1)[0].lower() if '://' in proxy else 'http'
            chrome_proxy = proxy
            if scheme.startswith('socks'):
                # normalize socks5h -> socks5 for Chromium
                chrome_proxy = 'socks5://' + proxy.split('://', 1)[1]
            applied = False
            if scheme in ('http', 'https') and hasattr(co, 'set_proxy'):
                try:
                    co.set_proxy(proxy)
                    applied = True
                except Exception as exc:
                    logger.warning('set_proxy failed, falling back to --proxy-server: %s', exc)
            if not applied:
                try:
                    co.set_argument(f'--proxy-server={chrome_proxy}')
                    applied = True
                except Exception as exc:
                    logger.warning(
                        'Failed to apply browser proxy %s: %s',
                        redact_proxy_url(proxy), exc,
                    )
            if applied:
                logger.info('Browser proxy enabled: %s', redact_proxy_url(chrome_proxy))

        runtime_user_data_path = self._prepare_user_data_path()
        co.set_user_data_path(runtime_user_data_path)
        logger.info(
            "Browser user data path: %s%s",
            runtime_user_data_path,
            " (temporary)" if self._owns_runtime_user_data else "",
        )
        if self.headless:
            co.headless()
        if self.extension_path and os.path.isdir(self.extension_path):
            co.add_extension(self.extension_path)
            logger.info(f"Extension loaded: {self.extension_path}")

        result = [None]
        error = [None]
        paths = _browser_path_candidates(self.browser_path, co.browser_path)
        start_timeout = int(os.environ.get("GROK_REGISTER_BROWSER_START_TIMEOUT", "90"))

        for index, browser_path in enumerate(paths):
            result[0] = None
            error[0] = None
            co.set_browser_path(browser_path)
            logger.info('Browser executable candidate: %s', browser_path)

            def _create():
                try:
                    result[0] = Chromium(co)
                except Exception as e:
                    error[0] = e

            t = threading.Thread(target=_create, daemon=True)
            t.start()
            t.join(timeout=start_timeout)

            if t.is_alive():
                self._cleanup_runtime_profile()
                raise BrowserError(f"Browser startup timed out (>{start_timeout}s)")
            if not error[0] and result[0] is not None:
                break
            if error[0] and _contains_winerror(error[0], 216) and index + 1 < len(paths):
                logger.warning(
                    'Browser executable is incompatible with Windows: %s; trying fallback %s',
                    browser_path,
                    paths[index + 1],
                )
                continue
            self._cleanup_runtime_profile()
            if error[0] and _contains_winerror(error[0], 216):
                raise BrowserError(
                    'Chrome executable is incompatible with this Windows installation '
                    f'({browser_path}). Install the official Windows Chrome build or '
                    'set GROK_REGISTER_BROWSER_PATH to a valid chrome.exe.'
                ) from error[0]
            if error[0]:
                raise BrowserError(f"Failed to start browser: {error[0]}") from error[0]
            raise BrowserError("Browser startup returned None")

        self._browser = result[0]
        tabs = self._browser.get_tabs()
        self._page = tabs[-1] if tabs else self._browser.new_tab()
        logger.info(f"Browser started, {len(tabs)} tab(s)")

    @staticmethod
    def _process_id(browser):
        process = getattr(browser, 'process', None)
        if isinstance(process, int):
            return process
        return getattr(process, 'pid', None)

    @staticmethod
    def _process_is_alive(browser):
        process = getattr(browser, 'process', None)
        poll = getattr(process, 'poll', None)
        if callable(poll):
            return poll() is None
        pid = BrowserManager._process_id(browser)
        if not pid:
            return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    @staticmethod
    def _terminate_process_tree(browser):
        pid = BrowserManager._process_id(browser)
        if not pid:
            return
        try:
            if sys.platform.startswith('win'):
                subprocess.run(
                    ['taskkill', '/F', '/T', '/PID', str(pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=10,
                )
            else:
                os.kill(pid, signal.SIGTERM)
        except Exception as exc:
            logger.warning('Failed to terminate browser process %s: %s', pid, exc)

    def _cleanup_runtime_profile(self):
        path = self._runtime_user_data_path
        owned = self._owns_runtime_user_data
        self._runtime_user_data_path = None
        self._owns_runtime_user_data = False
        if not path or not owned:
            return
        try:
            resolved = Path(path).resolve()
            temp_root = Path(tempfile.gettempdir()).resolve()
            if resolved == temp_root or temp_root not in resolved.parents:
                logger.warning('Refusing to remove unexpected browser profile path: %s', resolved)
                return
            shutil.rmtree(resolved, ignore_errors=False)
            logger.debug('Removed temporary browser profile: %s', resolved)
        except FileNotFoundError:
            pass
        except Exception as exc:
            logger.warning('Failed to remove temporary browser profile %s: %s', path, exc)

    def stop(self):
        """Stop browser, confirm process exit, and remove owned temp profile."""
        browser = self._browser
        if browser is not None:
            def _safe_quit():
                try:
                    browser.quit(del_data=self._owns_runtime_user_data)
                except TypeError:
                    browser.quit()
                except Exception:
                    pass

            t = threading.Thread(target=_safe_quit, daemon=True)
            t.start()
            t.join(timeout=5)
            deadline = time.time() + 3
            while self._process_is_alive(browser) and time.time() < deadline:
                time.sleep(0.1)
            if self._process_is_alive(browser):
                logger.warning('Browser process did not exit cleanly; terminating process tree')
                self._terminate_process_tree(browser)
            logger.info("Browser stopped")
        self._browser = None
        self._page = None
        self._cleanup_runtime_profile()

    def restart(self, force_close=False):
        if force_close or self._browser:
            self.stop()
            self.start()
        else:
            self.start()
        logger.info("Browser restarted")

    def refresh_active_page(self):
        """Re-acquire active page handle. Auto-restart browser on failure."""
        if self._browser is None:
            self.start()
            return self._page
        try:
            tabs = self._browser.get_tabs()
            self._page = tabs[-1] if tabs else self._browser.new_tab()
            return self._page
        except Exception:
            logger.warning("Failed to refresh page, restarting browser")
            self.restart(force_close=True)
            return self._page

    def run_js(self, script, *args):
        try:
            if not self._page:
                self.refresh_active_page()
            return self._page.run_js(script, *args)
        except Exception as e:
            raise BrowserError(f"Failed to run JS: {e}")

    def clear_cookies(self):
        try:
            if not self._browser:
                return
            page = self._browser.latest_page
            if page:
                page.run_cdp('Network.clearBrowserCookies')
                page.run_cdp('Network.clearBrowserCache')
                logger.debug("Cookies and cache cleared via CDP")
        except Exception as e:
            logger.warning(f"Failed to clear cookies: {e}")

    @property
    def page(self):
        if self._page is None:
            self.refresh_active_page()
        return self._page

    @property
    def browser(self):
        return self._browser

    def get(self, url):
        try:
            if not self._page:
                self.refresh_active_page()
            self._page.get(url)
        except Exception as e:
            raise BrowserError(f"Failed to navigate to {url}: {e}")
