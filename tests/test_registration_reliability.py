import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from core.register import MIN_VERIFICATION_CODE_POLLS, RegistrationEngine
from core.registration.state import RegistrationState


class RegistrationReliabilityTest(unittest.TestCase):
    def _engine(self):
        db = Mock()
        db.create_registration.return_value = 42
        db.complete_registration_success.return_value = {
            'account_done': False,
            'account_id': 7,
        }
        email = Mock()
        email.get_code_for_alias.return_value = 'ABC123'
        engine = RegistrationEngine(
            db, Mock(), email, Mock(), RegistrationState(),
        )
        engine._get_password = Mock(return_value='password')
        engine._open_signup_page = Mock()
        engine._fill_email = Mock(return_value=datetime.now(timezone.utc))
        engine._fill_and_confirm_code = Mock()
        engine._fill_profile = Mock()
        engine._extract_sso = Mock(return_value='unique-sso')
        engine._capture_cloudflare_context = Mock()
        engine._restart_browser = Mock()
        engine._emit_status = Mock()
        return engine, db, email

    @staticmethod
    def _alias():
        return {
            'id': 9,
            'account_id': 7,
            'alias_email': 'user+1@example.com',
            'main_email': 'user@example.com',
            'client_id': 'client-id',
            'refresh_token': 'refresh-token',
        }

    @staticmethod
    def _settings():
        return {
            'max_code_retries': '3',
            'grok_web_activation': 'false',
            'extract_numbers_enabled': 'false',
            'grok2api_auto_upload': 'false',
        }

    @patch('core.register.upload_registered_sso', return_value=None)
    def test_registration_enforces_extended_mail_poll_window(self, _upload):
        engine, db, email = self._engine()
        db.find_existing_sso.return_value = None

        engine._do_one_round(
            self._alias(), 1, 1, self._settings(), 'worker-lease', 'worker-1',
        )

        self.assertGreaterEqual(MIN_VERIFICATION_CODE_POLLS, 10)
        self.assertEqual(
            email.get_code_for_alias.call_args.kwargs['max_retries'],
            MIN_VERIFICATION_CODE_POLLS,
        )
        db.complete_registration_success.assert_called_once()
        self.assertEqual(
            engine._fill_profile.call_args.kwargs['alias']['id'],
            self._alias()['id'],
        )
        self.assertEqual(
            engine._fill_profile.call_args.kwargs['alias_email'],
            self._alias()['alias_email'],
        )

    @patch('core.register.upload_registered_sso', return_value=None)
    def test_duplicate_sso_is_not_committed_or_uploaded(self, upload):
        engine, db, _email = self._engine()
        db.find_existing_sso.return_value = {
            'email': 'previous@example.com',
            'fingerprint': 'abcdef0123456789',
        }
        db.finish_registration_attempt.return_value = {
            'retry_count': 1,
            'terminal': False,
        }

        engine._do_one_round(
            self._alias(), 1, 1, self._settings(), 'worker-lease', 'worker-1',
        )

        db.complete_registration_success.assert_not_called()
        upload.assert_not_called()
        self.assertEqual(
            db.finish_registration_attempt.call_args.kwargs['max_retries'],
            2,
        )
        engine._restart_browser.assert_called_once_with(force_close=True)

    @patch('core.register.time.sleep')
    def test_existing_account_recovery_falls_back_to_signin_route(self, _sleep):
        engine, _db, email = self._engine()
        alias = self._alias()
        alias['provider'] = 'cloud_mail'
        engine._click_existing_account_email_login = Mock(return_value=True)
        engine._detect_existing_account_notice = Mock(return_value='Existing account found')
        engine._dismiss_cookie_banner = Mock()
        engine._profile_completion_reason = Mock(return_value='sso-cookie:20')

        recovered = engine._recover_existing_account_session(
            alias,
            {**self._settings(), 'cloud_mail_api_base': 'https://mail.example'},
        )

        self.assertTrue(recovered)
        engine.browser.page.get.assert_called_once_with(
            'https://accounts.x.ai/sign-in?redirect=grok-com'
        )
        self.assertEqual(engine._click_existing_account_email_login.call_count, 2)
        email.get_code_for_alias.assert_called_once()
        self.assertEqual(
            email.get_code_for_alias.call_args.kwargs['provider'],
            'cloud_mail',
        )

    @patch('core.register.time.sleep')
    @patch('core.register.upload_registered_sso', return_value=None)
    def test_known_existing_account_skips_signup_and_recovers_directly(
        self, _upload, _sleep,
    ):
        engine, db, _email = self._engine()
        alias = self._alias()
        alias['existing_account'] = True
        engine._recover_existing_account_session = Mock(return_value=True)
        db.find_existing_sso.return_value = None

        engine._do_one_round(
            alias, 1, 1, self._settings(), 'worker-lease', 'worker-1',
        )

        engine.browser.page.get.assert_called_once_with(
            'https://accounts.x.ai/sign-in?redirect=grok-com'
        )
        engine._open_signup_page.assert_not_called()
        engine._fill_email.assert_not_called()
        engine._recover_existing_account_session.assert_called_once_with(
            alias, self._settings(),
        )
        db.complete_registration_success.assert_called_once()


if __name__ == '__main__':
    unittest.main()
