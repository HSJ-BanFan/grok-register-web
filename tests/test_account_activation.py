import unittest
from unittest.mock import Mock, patch

from core.account_activation import (
    _extract_browser_context,
    inject_sso_cookie,
    switch_sso_cookie,
    activate_grok_web,
)


class AccountActivationTest(unittest.TestCase):
    def test_extracts_only_grok_cloudflare_context(self):
        class Page:
            def cookies(self, **kwargs):
                return [
                    {'name': 'cf_clearance', 'value': 'wrong', 'domain': '.example.com'},
                    {'name': 'cf_clearance', 'value': 'clearance', 'domain': '.grok.com'},
                    {'name': '__cf_bm', 'value': 'bm', 'domain': 'grok.com'},
                    {'name': 'sso', 'value': 'secret', 'domain': '.x.ai'},
                ]
            def run_js(self, script):
                return 'Registered Browser UA'

        cookies, user_agent = _extract_browser_context(Page())

        self.assertEqual(cookies, 'cf_clearance=clearance; __cf_bm=bm')
        self.assertEqual(user_agent, 'Registered Browser UA')

    def test_inject_sso_cookie_sets_cdp_and_page_cookies(self):
        page = Mock()
        page.run_cdp = Mock()
        page.set = Mock()
        page.set.cookies = Mock()

        inject_sso_cookie(page, 'sso-token-value')

        self.assertGreaterEqual(page.run_cdp.call_count, 1)
        first = page.run_cdp.call_args_list[0]
        self.assertEqual(first.args[0], 'Network.setCookie')
        self.assertEqual(first.kwargs['name'], 'sso')
        self.assertEqual(first.kwargs['value'], 'sso-token-value')
        page.set.cookies.assert_called()

    def test_inject_sso_cookie_rejects_empty(self):
        with self.assertRaises(ValueError):
            inject_sso_cookie(Mock(), '  ')

    def test_switch_sso_cookie_clears_then_injects(self):
        page = Mock()
        page.run_cdp = Mock()
        page.set = Mock()
        page.set.cookies = Mock()
        page.set.cookies.remove = Mock()

        switch_sso_cookie(page, 'new-sso')

        deleted = [c for c in page.run_cdp.call_args_list if c.args and c.args[0] == 'Network.deleteCookies']
        set_calls = [c for c in page.run_cdp.call_args_list if c.args and c.args[0] == 'Network.setCookie']
        self.assertTrue(deleted)
        self.assertTrue(set_calls)
        self.assertEqual(set_calls[0].kwargs['value'], 'new-sso')

    def test_activate_grok_web_injects_sso_before_opening_sites(self):
        browser = Mock()
        page = Mock()
        browser.page = page
        page.get = Mock()
        page.cookies = Mock(return_value=[
            {'name': 'cf_clearance', 'value': 'clearance', 'domain': '.grok.com'},
            {'name': '__cf_bm', 'value': 'bm', 'domain': '.grok.com'},
        ])
        page.run_js = Mock(side_effect=[
            # existing CF extract before switch
            'Mozilla/5.0 TestUA',
            # wait loop state
            {'challenge': False, 'url': 'https://grok.com/'},
            # post-open UA extract
            'Mozilla/5.0 TestUA',
            # birth
            {'status': 200, 'body': 'ok'},
            # probe
            {'status': 200, 'path': '/rest/app-chat/conversations'},
            # session state
            {'href': 'https://grok.com/', 'challenge': False, 'signedOut': False, 'hasAppShell': True},
        ])
        page.run_cdp = Mock()
        page.set = Mock()
        page.set.cookies = Mock()
        page.set.cookies.remove = Mock()

        with patch('core.account_activation._set_tos', return_value=True), \
             patch('core.account_activation.requests.Session') as session_cls:
            session = Mock()
            session.headers = {}
            session.cookies = Mock()
            session_cls.return_value = session
            result = activate_grok_web(browser, 'historical-sso')

        self.assertTrue(result.ready)
        self.assertIn('cf_clearance=clearance', result.cloudflare_cookies)
        self.assertEqual(result.user_agent, 'Mozilla/5.0 TestUA')
        self.assertIn('session_ok=True', result.message)
        urls = [call.args[0] for call in page.get.call_args_list]
        self.assertIn('https://accounts.x.ai/', urls)
        self.assertIn('https://grok.com/', urls)

    def test_activate_ready_when_probe_501_but_tos_and_session_ok(self):
        browser = Mock()
        page = Mock()
        browser.page = page
        page.get = Mock()
        page.cookies = Mock(return_value=[
            {'name': 'cf_clearance', 'value': 'clearance', 'domain': '.grok.com'},
        ])
        page.run_js = Mock(side_effect=[
            'UA',
            {'challenge': False, 'url': 'https://grok.com/chat'},
            'UA',
            {'status': 409, 'body': 'already'},
            {'status': 501, 'path': '/rest/app-chat/conversations'},
            {'href': 'https://grok.com/chat', 'challenge': False, 'signedOut': False, 'hasAppShell': True},
        ])
        page.run_cdp = Mock()
        page.set = Mock()
        page.set.cookies = Mock()
        page.set.cookies.remove = Mock()

        with patch('core.account_activation._set_tos', return_value=True), \
             patch('core.account_activation.requests.Session') as session_cls:
            session = Mock()
            session.headers = {}
            session.cookies = Mock()
            session_cls.return_value = session
            result = activate_grok_web(browser, 'sso')

        self.assertTrue(result.ready)
        self.assertIn('probe=501', result.message)
        self.assertIn('tos=True', result.message)
        self.assertIn('session_ok=True', result.message)

    def test_activate_continues_when_run_js_raises_page_refreshed(self):
        browser = Mock()
        page = Mock()
        browser.page = page
        page.get = Mock()
        page.cookies = Mock(return_value=[
            {'name': 'cf_clearance', 'value': 'clearance', 'domain': '.grok.com'},
        ])
        page.run_js = Mock(side_effect=[
            'UA',
            {'challenge': False, 'url': 'https://grok.com/'},
            'UA',
            RuntimeError('页面已被刷新，请尝试等待页面刷新完成后再执行操作。'),
            RuntimeError('页面已被刷新'),
            {'href': 'https://grok.com/', 'challenge': False, 'signedOut': False, 'hasAppShell': True},
        ])
        page.run_cdp = Mock()
        page.set = Mock()
        page.set.cookies = Mock()
        page.set.cookies.remove = Mock()

        with patch('core.account_activation._set_tos', return_value=True), \
             patch('core.account_activation.requests.Session') as session_cls:
            session = Mock()
            session.headers = {}
            session.cookies = Mock()
            session_cls.return_value = session
            result = activate_grok_web(browser, 'sso')

        self.assertTrue(result.ready)
        self.assertIn('session_ok=True', result.message)


if __name__ == '__main__':
    unittest.main()
