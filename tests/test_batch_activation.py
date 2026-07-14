import unittest
from unittest.mock import Mock, patch

from core.account_activation import ActivationContext
from core.batch_activation import BatchActivationEngine
from core.register import RegistrationState


class BatchActivationEngineTest(unittest.TestCase):
    def test_load_records_reverses_and_applies_limit(self):
        db = Mock()
        db.get_registrations.return_value = [
            {'id': 3, 'email': 'c@example.com', 'sso_value': 'c'},
            {'id': 2, 'email': 'b@example.com', 'sso_value': 'b'},
            {'id': 1, 'email': 'a@example.com', 'sso_value': 'a'},
        ]
        engine = BatchActivationEngine(db, Mock(), Mock(), RegistrationState())

        rows = engine._load_records(limit=2)

        self.assertEqual([row['id'] for row in rows], [1, 2])

    def test_load_records_filters_ids(self):
        db = Mock()
        db.get_registrations.return_value = [
            {'id': 3, 'email': 'c@example.com', 'sso_value': 'c'},
            {'id': 2, 'email': 'b@example.com', 'sso_value': 'b'},
            {'id': 1, 'email': 'a@example.com', 'sso_value': 'a'},
        ]
        engine = BatchActivationEngine(db, Mock(), Mock(), RegistrationState())

        rows = engine._load_records(ids=[3, 1])

        self.assertEqual([row['id'] for row in rows], [1, 3])

    def test_activate_one_updates_egress_but_does_not_import(self):
        db = Mock()
        browser = Mock()
        socketio = Mock()
        state = RegistrationState()
        state.current_round = 1
        engine = BatchActivationEngine(db, browser, socketio, state)
        engine._clear_session = Mock()
        engine._sync_egress = Mock()
        record = {'id': 9, 'email': 'user@example.com', 'sso_value': 'sso-token'}
        settings = {'grok2api_auto_upload': 'true'}
        context = ActivationContext(True, 'ok', user_agent='UA', cloudflare_cookies='cf_clearance=x')

        with patch('core.batch_activation.activate_grok_web', return_value=context) as activate:
            result = engine._activate_one(record, settings)

        activate.assert_called_once_with(browser, 'sso-token', timeout=0, reuse_cloudflare=True)
        engine._sync_egress.assert_called_once_with(settings, context)
        self.assertTrue(result.ready)
        socketio.emit.assert_called()
        event = socketio.emit.call_args.args[0]
        self.assertEqual(event, 'round_complete')
        payload = socketio.emit.call_args.args[1]
        self.assertEqual(payload['mode'], 'reactivate')
        self.assertEqual(payload['email'], 'user@example.com')

    def test_run_stops_when_no_records(self):
        db = Mock()
        db.get_settings.return_value = {'browser_headless': 'false'}
        db.get_registrations.return_value = []
        browser = Mock()
        socketio = Mock()
        state = RegistrationState()
        engine = BatchActivationEngine(db, browser, socketio, state)

        engine.run()

        browser.start.assert_not_called()
        self.assertEqual(state.status, 'stopped')


if __name__ == '__main__':
    unittest.main()
