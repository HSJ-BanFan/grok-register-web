import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

logger = logging.getLogger('register')


class BrowserError(Exception):
    pass


class BrowserManager:
    def __init__(self, headless=False, extension_path=None, user_data_path=None,
                 proxy=''):
        self.headless = headless
        self.extension_path = extension_path
        self.user_data_path = user_data_path
        self.proxy = (proxy or '').strip()
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
        co.set_pref('credentials_enable_service', False)
        co.set_pref('profile.password_manager_enabled', False)

        proxy = (self.proxy or '').strip()
        if proxy:
            applied = False
            if hasattr(co, 'set_proxy'):
                try:
                    co.set_proxy(proxy)
                    applied = True
                except Exception as exc:
                    logger.warning('set_proxy failed, falling back to --proxy-server: %s', exc)
            if not applied:
                try:
                    co.set_argument(f'--proxy-server={proxy}')
                    applied = True
                except Exception:
                    try:
                        co.set_argument('--proxy-server', proxy)
                        applied = True
                    except Exception as exc:
                        logger.warning('Failed to apply browser proxy %s: %s', proxy, exc)
            if applied:
                logger.info('Browser proxy enabled: %s', proxy)

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

        def _create():
            try:
                result[0] = Chromium(co)
            except Exception as e:
                error[0] = e

        start_timeout = 45 if self.user_data_path else 20
        t = threading.Thread(target=_create, daemon=True)
        t.start()
        t.join(timeout=start_timeout)

        if t.is_alive():
            raise BrowserError(f"Browser startup timed out (>{start_timeout}s)")
        if error[0]:
            self._cleanup_runtime_profile()
            raise BrowserError(f"Failed to start browser: {error[0]}")
        if result[0] is None:
            self._cleanup_runtime_profile()
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
