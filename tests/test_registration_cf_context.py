import unittest
from unittest.mock import Mock

from core.register import RegistrationEngine
from core.registration.state import RegistrationState


class RegistrationCloudflareContextTest(unittest.TestCase):
    def _engine(self):
        browser = Mock()
        page = Mock()
        page.cookies.return_value = [
            {'name': 'cf_clearance', 'value': 'clearance', 'domain': '.grok.com'},
            {'name': '__cf_bm', 'value': 'bm', 'domain': '.grok.com'},
            {'name': 'sso', 'value': 'identity', 'domain': '.x.ai'},
        ]
        page.run_js.return_value = 'Registered Browser UA'
        page.run_cdp = Mock()
        page.set = Mock()
        page.set.cookies = Mock()
        page.set.cookies.remove = Mock()
        page.close = Mock()

        new_page = Mock()
        new_page.run_cdp = Mock()
        new_page.set = Mock()
        new_page.set.cookies = Mock()

        chromium = Mock()
        chromium.new_tab.return_value = new_page
        browser.page = page
        browser.browser = chromium

        engine = RegistrationEngine(
            Mock(), browser, Mock(), Mock(), RegistrationState(),
        )
        return engine, browser, page, new_page

    def test_round_recycle_preserves_cloudflare_and_clears_identity_only(self):
        engine, browser, page, new_page = self._engine()

        engine._restart_browser(force_close=False)

        commands = [call.args[0] for call in page.run_cdp.call_args_list if call.args]
        self.assertNotIn('Network.clearBrowserCookies', commands)
        self.assertNotIn('Network.clearBrowserCache', commands)
        self.assertIn('Network.deleteCookies', commands)
        self.assertTrue(engine._cloudflare_context.ready)
        restored = [
            call for call in new_page.run_cdp.call_args_list
            if call.args and call.args[0] == 'Network.setCookie'
        ]
        self.assertEqual(
            {call.kwargs['name'] for call in restored},
            {'cf_clearance', '__cf_bm'},
        )
        self.assertIs(browser._page, new_page)

    def test_round_recycle_without_clearance_keeps_full_cleanup(self):
        engine, _, page, _ = self._engine()
        page.cookies.return_value = []

        engine._restart_browser(force_close=False)

        commands = [call.args[0] for call in page.run_cdp.call_args_list if call.args]
        self.assertIn('Network.clearBrowserCookies', commands)
        self.assertIn('Network.clearBrowserCache', commands)
        self.assertIsNone(engine._cloudflare_context)


if __name__ == '__main__':
    unittest.main()
