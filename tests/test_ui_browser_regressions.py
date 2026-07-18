import importlib.util
import json
import os
from pathlib import Path
import shutil
import threading
import unittest
from urllib.parse import urlparse

from werkzeug.serving import make_server

from app import app


try:
    PLAYWRIGHT_AVAILABLE = importlib.util.find_spec('playwright.sync_api') is not None
except ModuleNotFoundError:
    PLAYWRIGHT_AVAILABLE = False


def _browser_executable(playwright):
    candidates = [
        os.environ.get('GROK_REGISTER_BROWSER_PATH', ''),
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
        shutil.which('google-chrome') or '',
        shutil.which('chromium') or '',
        shutil.which('chromium-browser') or '',
        playwright.chromium.executable_path,
    ]
    return next((path for path in candidates if path and Path(path).is_file()), None)


@unittest.skipUnless(PLAYWRIGHT_AVAILABLE, 'Playwright is not installed')
class UIBrowserRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright

        cls.server = make_server('127.0.0.1', 0, app, threaded=True)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.base_url = f'http://127.0.0.1:{cls.server.server_port}'
        cls.playwright = sync_playwright().start()
        executable = _browser_executable(cls.playwright)
        if not executable:
            cls.playwright.stop()
            cls.server.shutdown()
            raise unittest.SkipTest('No Chromium-compatible browser is installed')
        try:
            cls.browser = cls.playwright.chromium.launch(
                headless=True,
                executable_path=executable,
            )
        except Exception as exc:
            cls.playwright.stop()
            cls.server.shutdown()
            raise unittest.SkipTest(f'Unable to launch browser: {exc}') from exc

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'browser'):
            cls.browser.close()
        if hasattr(cls, 'playwright'):
            cls.playwright.stop()
        if hasattr(cls, 'server'):
            cls.server.shutdown()
        if hasattr(cls, 'server_thread'):
            cls.server_thread.join(timeout=2)

    def _page(self, payloads, requests=None):
        page = self.browser.new_page(viewport={'width': 1440, 'height': 1000})

        def handle_api(route):
            path = urlparse(route.request.url).path
            if requests is not None:
                requests.append(route.request)
            payload = payloads.get(path, {'success': True, 'data': []})
            route.fulfill(
                status=200,
                content_type='application/json',
                body=json.dumps(payload),
            )

        page.route('**/api/**', handle_api)
        return page

    def test_auto_turnstile_save_enables_browser_fallback(self):
        requests = []
        page = self._page({
            '/api/settings': {
                'success': True,
                'data': {
                    'registration_backend': 'protocol',
                    'turnstile_provider': 'auto',
                    'allow_browser_fallback': 'false',
                },
            },
        }, requests=requests)
        try:
            page.goto(f'{self.base_url}/#/settings', wait_until='networkidle')
            page.locator('#save-settings-btn').click()
            page.wait_for_timeout(100)

            put_request = next(
                request for request in reversed(requests)
                if request.method == 'PUT'
                and urlparse(request.url).path == '/api/settings'
            )
            payload = put_request.post_data_json
            self.assertEqual(payload['turnstile_provider'], 'auto')
            self.assertEqual(payload['allow_browser_fallback'], 'true')
        finally:
            page.close()

    def test_solver_status_and_manual_connection_test(self):
        requests = []
        page = self._page({
            '/api/settings': {
                'success': True,
                'data': {
                    'turnstile_solver_url': 'http://127.0.0.1:5072',
                },
            },
            '/api/settings/turnstile-solver/test': {
                'success': True,
                'data': {
                    'online': True,
                    'reason': 'online',
                    'status_code': 200,
                    'latency_ms': 18,
                },
            },
        }, requests=requests)
        try:
            page.goto(f'{self.base_url}/#/settings', wait_until='networkidle')
            status = page.locator('#solver-health-status')
            detail = page.locator('#solver-health-detail')
            self.assertEqual(status.text_content(), '在线')
            self.assertIn('HTTP 200', detail.text_content())
            self.assertIn('18 ms', detail.text_content())

            page.locator('#test-solver-btn').click()
            page.wait_for_timeout(100)
            probes = [
                request for request in requests
                if request.method == 'POST'
                and urlparse(request.url).path
                == '/api/settings/turnstile-solver/test'
            ]
            self.assertGreaterEqual(len(probes), 2)
            self.assertEqual(
                probes[-1].post_data_json['url'],
                'http://127.0.0.1:5072',
            )
        finally:
            page.close()

    def test_solver_status_shows_connection_failure(self):
        page = self._page({
            '/api/settings': {
                'success': True,
                'data': {
                    'turnstile_solver_url': 'http://127.0.0.1:5072',
                },
            },
            '/api/settings/turnstile-solver/test': {
                'success': True,
                'data': {
                    'online': False,
                    'reason': 'connection_error',
                    'status_code': None,
                    'latency_ms': 3,
                },
            },
        })
        try:
            page.goto(f'{self.base_url}/#/settings', wait_until='networkidle')
            self.assertEqual(
                page.locator('#solver-health-status').text_content(), '离线',
            )
            self.assertIn(
                '请确认服务和端口已经启动',
                page.locator('#solver-health-detail').text_content(),
            )
        finally:
            page.close()

    def test_settings_values_are_escaped_and_mailbox_menu_is_visible(self):
        hostile = '\"><img id="settings-xss" src=x onerror="window.__xss=1">'
        page = self._page({
            '/api/settings': {
                'success': True,
                'data': {
                    'email_provider': 'microsoft',
                    'password_mode': 'manual',
                    'manual_password': hostile,
                },
            },
        })
        try:
            page.goto(f'{self.base_url}/#/settings', wait_until='networkidle')
            field = page.locator('#s-manual-password')
            self.assertEqual(field.input_value(), hostile)
            self.assertEqual(field.get_attribute('type'), 'password')
            self.assertEqual(page.locator('#settings-xss').count(), 0)
            self.assertIsNone(page.evaluate('window.__xss'))

            page.locator('#s-email-provider-trigger').click()
            menu = page.locator('body > .ui-select-menu:not([hidden])')
            self.assertEqual(menu.locator('.ui-select-option').count(), 5)
            visible = menu.evaluate(
                """el => {
                    const rect = el.getBoundingClientRect();
                    const hit = document.elementFromPoint(rect.left + 20, rect.top + 20);
                    return rect.width > 0 && rect.height > 0 && el.contains(hit);
                }"""
            )
            self.assertTrue(visible)
        finally:
            page.close()

    def test_results_api_values_render_as_text(self):
        hostile = '<img id="results-xss" src=x onerror="window.__xss=1">'
        stats = {
            'total_accounts': hostile,
            'success_rate': hostile,
            'avg_duration': 1,
        }
        page = self._page({
            '/api/accounts/stats': {'success': True, 'data': stats},
            '/api/results/sso': {
                'success': True,
                'data': [{
                    'id': 1,
                    'email': hostile,
                    'sso_value': 'token',
                    'created_at': hostile,
                }],
            },
            '/api/results/accounts': {
                'success': True,
                'data': [{
                    'id': 2,
                    'email': hostile,
                    'account_password': hostile,
                    'created_at': hostile,
                }],
            },
        })
        try:
            page.goto(f'{self.base_url}/#/results', wait_until='networkidle')
            self.assertEqual(page.locator('#results-xss').count(), 0)
            self.assertIsNone(page.evaluate('window.__xss'))
            self.assertIn(hostile, page.locator('#sso-table').text_content())
            self.assertIn(hostile, page.locator('#acc-table').text_content())
        finally:
            page.close()

    def test_settings_load_failure_does_not_render_save_controls(self):
        page = self._page({
            '/api/settings': {'success': False, 'message': 'offline'},
        })
        try:
            page.goto(f'{self.base_url}/#/settings', wait_until='networkidle')
            self.assertEqual(page.locator('#save-settings-btn').count(), 0)
            self.assertIn('系统设置加载失败', page.locator('#main-content').text_content())
        finally:
            page.close()

    def test_task_controls_fold_panels_and_mobile_navigation_sync_state(self):
        page = self._page({
            '/api/register/status': {
                'success': True,
                'data': {'status': 'paused', 'completed': 0, 'success': 0, 'failed': 0},
            },
            '/api/accounts/stats': {'success': True, 'data': {}},
            '/api/results/sso': {'success': True, 'data': []},
            '/api/results/accounts': {'success': True, 'data': []},
            '/api/oauth/status': {'success': True, 'data': {'authorized': False}},
            '/api/accounts': {'success': True, 'data': []},
        })
        try:
            page.goto(f'{self.base_url}/#/register', wait_until='networkidle')
            pause = page.locator('#pause-btn')
            self.assertEqual(pause.get_attribute('data-action'), 'resume')
            self.assertIn('继续任务', pause.text_content())

            page.goto(f'{self.base_url}/#/results', wait_until='networkidle')
            fold_body = page.locator('#fold-sso-body')
            self.assertEqual(fold_body.get_attribute('aria-hidden'), 'true')
            self.assertIsNotNone(fold_body.get_attribute('inert'))
            page.locator('#fold-sso-toggle').click()
            self.assertEqual(fold_body.get_attribute('aria-hidden'), 'false')
            self.assertIsNone(fold_body.get_attribute('inert'))

            page.set_viewport_size({'width': 800, 'height': 700})
            page.goto(f'{self.base_url}/#/email', wait_until='networkidle')
            menu_button = page.locator('#mobile-menu')
            sidebar = page.locator('#sidebar')
            self.assertEqual(menu_button.get_attribute('aria-expanded'), 'false')
            self.assertIsNotNone(sidebar.get_attribute('inert'))
            menu_button.click()
            self.assertEqual(menu_button.get_attribute('aria-expanded'), 'true')
            self.assertIsNone(sidebar.get_attribute('inert'))
            self.assertTrue(page.locator('.nav-item').first.evaluate('el => el === document.activeElement'))
            page.keyboard.press('Escape')
            self.assertEqual(menu_button.get_attribute('aria-expanded'), 'false')
            self.assertIsNotNone(sidebar.get_attribute('inert'))
            self.assertTrue(menu_button.evaluate('el => el === document.activeElement'))
        finally:
            page.close()

    def test_rapid_navigation_does_not_leave_page_transparent(self):
        page = self._page({
            '/api/oauth/status': {'success': True, 'data': {'authorized': False}},
            '/api/accounts': {'success': True, 'data': []},
            '/api/accounts/stats': {'success': True, 'data': {}},
            '/api/results/sso': {'success': True, 'data': []},
            '/api/results/accounts': {'success': True, 'data': []},
        })
        try:
            page.goto(f'{self.base_url}/#/email', wait_until='networkidle')
            page.evaluate(
                """() => {
                    location.hash = '#/results';
                    setTimeout(() => { location.hash = '#/email'; }, 20);
                }"""
            )
            page.wait_for_timeout(600)
            main = page.locator('#main-content')
            classes = main.get_attribute('class') or ''
            self.assertEqual(page.evaluate('location.hash'), '#/email')
            self.assertNotIn('is-page-exit', classes)
            self.assertNotIn('is-page-hold', classes)
            self.assertNotIn('is-page-enter', classes)
            self.assertEqual(page.locator('#page-title').text_content(), '邮箱')
        finally:
            page.close()


if __name__ == '__main__':
    unittest.main()
