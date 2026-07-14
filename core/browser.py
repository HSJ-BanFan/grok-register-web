import logging
import os
import threading

logger = logging.getLogger('register')


class BrowserError(Exception):
    pass


class BrowserManager:
    def __init__(self, headless=False, extension_path=None, user_data_path=None, proxy=''):
        self.headless = headless
        self.extension_path = extension_path
        self.user_data_path = user_data_path
        self.proxy = (proxy or '').strip()
        self._browser = None
        self._page = None

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

        if self.user_data_path:
            os.makedirs(self.user_data_path, exist_ok=True)
            co.set_user_data_path(self.user_data_path)
            logger.info(f"Browser user data path: {self.user_data_path}")
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
            raise BrowserError(f"Failed to start browser: {error[0]}")
        if result[0] is None:
            raise BrowserError("Browser startup returned None")

        self._browser = result[0]
        tabs = self._browser.get_tabs()
        self._page = tabs[-1] if tabs else self._browser.new_tab()
        self._apply_stealth_js(self._page)
        logger.info(f"Browser started, {len(tabs)} tab(s)")

    def _apply_stealth_js(self, page):
        """Hide navigator.webdriver and soft-patch common automation probes."""
        if not page:
            return
        script = r"""
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
try {
  window.chrome = window.chrome || {runtime: {}};
} catch (e) {}
try {
  Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
} catch (e) {}
try {
  Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
} catch (e) {}
"""
        try:
            if hasattr(page, 'add_init_js'):
                page.add_init_js(script)
            page.run_js(script)
        except Exception as exc:
            logger.debug('Failed to apply stealth JS: %s', exc)

    def stop(self):
        """Stop browser with timeout protection to prevent hanging."""
        if self._browser is not None:
            def _safe_quit():
                try:
                    self._browser.quit()
                except Exception:
                    pass

            t = threading.Thread(target=_safe_quit, daemon=True)
            t.start()
            t.join(timeout=5)
            logger.info("Browser stopped")
        self._browser = None
        self._page = None

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
            self._apply_stealth_js(self._page)
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
