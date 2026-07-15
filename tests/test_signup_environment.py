import os
import tempfile
import unittest
from unittest.mock import ANY, Mock, patch

from core.browser import (
    BrowserError,
    BrowserManager,
    redact_proxy_url,
    validate_proxy_endpoint,
)
from core.register import RegistrationEngine
from core.registration.signup import (
    SignupEnvironmentError,
    SignupPageSnapshot,
    SignupPageStage,
    classify_signup_page,
    save_signup_diagnostics,
)
from core.registration.state import RegistrationState
from core.runtime import resolve_browser_headless, resolve_registration_concurrency


class SignupPageClassificationTest(unittest.TestCase):
    def test_classifies_cloudflare_hard_block(self):
        snapshot = SignupPageSnapshot(
            title='Attention Required! | Cloudflare',
            body_text=(
                'Sorry, you have been blocked. You are unable to access x.ai. '
                'Cloudflare Ray ID: a1b7224fbcf2f9b0'
            ),
        )

        self.assertEqual(classify_signup_page(snapshot), SignupPageStage.BLOCKED)
        self.assertEqual(snapshot.ray_id, 'a1b7224fbcf2f9b0')

    def test_classifies_proxy_error_before_generic_stall(self):
        snapshot = SignupPageSnapshot(
            title='accounts.x.ai',
            body_text='ERR_PROXY_CONNECTION_FAILED',
        )
        self.assertEqual(
            classify_signup_page(snapshot), SignupPageStage.PROXY_ERROR,
        )

    def test_classifies_normal_email_entry_as_ready(self):
        snapshot = SignupPageSnapshot(
            title='Create Your Grok Account | Grok',
            has_email_signup=True,
            button_labels=('Sign up with email',),
        )
        self.assertEqual(classify_signup_page(snapshot), SignupPageStage.READY)

    def test_signup_diagnostics_write_json_and_screenshot(self):
        class Page:
            def get_screenshot(self, path=None, name=None, full_page=False):
                result = os.path.join(path, name)
                with open(result, 'wb') as handle:
                    handle.write(b'png')
                return result

        snapshot = SignupPageSnapshot(
            title='Attention Required! | Cloudflare',
            body_text='Account user@example.com blocked. Cloudflare Ray ID: abc123',
        )
        with tempfile.TemporaryDirectory() as directory:
            result = save_signup_diagnostics(
                Page(), SignupPageStage.BLOCKED, snapshot,
                reason='cloudflare_blocked', directory=directory,
            )
            self.assertTrue(os.path.exists(result['json']))
            self.assertTrue(os.path.exists(result['screenshot']))
            with open(result['json'], encoding='utf-8') as handle:
                payload = handle.read()
            self.assertIn('<redacted-email>', payload)
            self.assertNotIn('user@example.com', payload)


class ProxyEnvironmentTest(unittest.TestCase):
    def test_proxy_endpoint_is_checked_from_current_process(self):
        connection = Mock()
        with patch('core.browser.socket.create_connection', return_value=connection) as create:
            endpoint = validate_proxy_endpoint('http://127.0.0.1:7897')

        self.assertEqual(endpoint, ('127.0.0.1', 7897))
        create.assert_called_once_with(('127.0.0.1', 7897), timeout=3)
        connection.close.assert_called_once_with()

    def test_proxy_failure_is_actionable_and_credentials_are_redacted(self):
        with patch(
            'core.browser.socket.create_connection',
            side_effect=ConnectionRefusedError('refused'),
        ):
            with self.assertRaisesRegex(BrowserError, 'network namespace') as raised:
                validate_proxy_endpoint('http://user:secret@proxy.example:7897')

        self.assertNotIn('secret', str(raised.exception))
        self.assertEqual(
            redact_proxy_url('http://user:secret@proxy.example:7897'),
            'http://proxy.example:7897',
        )

    def test_linux_headful_mode_requires_virtual_display(self):
        browser = BrowserManager(headless=False)
        with patch('core.browser.sys.platform', 'linux'), patch.dict(
            os.environ, {'DISPLAY': '', 'WAYLAND_DISPLAY': ''}, clear=False,
        ):
            with self.assertRaisesRegex(BrowserError, 'run_with_xvfb'):
                browser.start()


class RuntimeOverrideTest(unittest.TestCase):
    def test_xvfb_environment_forces_headful_single_worker(self):
        with patch.dict(os.environ, {
            'GROK_REGISTER_BROWSER_HEADLESS': 'false',
            'GROK_REGISTER_CONCURRENCY': '1',
        }):
            self.assertFalse(resolve_browser_headless({'browser_headless': 'true'}))
            self.assertEqual(resolve_registration_concurrency(8), 1)


class RegistrationEnvironmentAbortTest(unittest.TestCase):
    def test_wait_raises_environment_error_and_keeps_diagnostics(self):
        browser = Mock()
        engine = RegistrationEngine(Mock(), browser, Mock(), Mock(), RegistrationState())
        snapshot = SignupPageSnapshot(
            url='https://accounts.x.ai/sign-up',
            title='Attention Required! | Cloudflare',
            body_text='Sorry, you have been blocked. Cloudflare Ray ID: abc123',
        )
        engine._capture_signup_snapshot = Mock(return_value=snapshot)
        engine._save_signup_diagnostics = Mock(return_value={
            'json': '/tmp/signup.json',
            'screenshot': '/tmp/signup.png',
        })

        with self.assertRaises(SignupEnvironmentError) as raised:
            engine._wait_for_signup_ready(timeout=0.1)

        self.assertEqual(raised.exception.reason, 'cloudflare_blocked')
        self.assertEqual(raised.exception.diagnostics['json'], '/tmp/signup.json')

    def test_environment_block_releases_alias_without_retry_or_failure_count(self):
        db = Mock()
        db.create_registration.return_value = 17
        db.abort_registration_attempt.return_value = True
        socketio = Mock()
        state = RegistrationState()
        engine = RegistrationEngine(db, Mock(), Mock(), socketio, state)
        error = SignupEnvironmentError(
            'cloudflare_blocked',
            SignupPageSnapshot(
                url='https://accounts.x.ai/sign-up',
                title='Attention Required! | Cloudflare',
            ),
            {'json': '/tmp/signup.json', 'screenshot': '/tmp/signup.png'},
        )
        engine._open_signup_page = Mock(side_effect=error)

        engine._do_one_round(
            {'id': 3, 'alias_email': 'alias@example.com'},
            round_num=1,
            max_retries=3,
            settings={'password_mode': 'manual', 'manual_password': 'Password123!'},
            lease_owner='lease-1',
            worker_id='worker-1',
        )

        db.abort_registration_attempt.assert_called_once()
        db.finish_registration_attempt.assert_not_called()
        self.assertTrue(state.should_stop())
        self.assertEqual(state.failed, 0)
        socketio.emit.assert_any_call('error', ANY)


if __name__ == '__main__':
    unittest.main()
