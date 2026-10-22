import unittest
from unittest.mock import Mock, patch

from core.grok2api_client import Grok2APIChatPermissionError
from core.grok2api_retry import Grok2APIRetryWorker
from core.registration.state import RegistrationState


class Grok2APIRetryWorkerTest(unittest.TestCase):
    def test_retries_claimed_delivery_and_marks_success(self):
        db = Mock()
        db.get_settings.return_value = {
            'grok2api_auto_upload': 'true',
            'grok2api_url': 'http://[IP]:21434',
        }
        db.claim_grok2api_retries.return_value = [{
            'id': 9,
            'email': '[邮箱]',
            'sso_value': 'sso-token',
        }]
        worker = Grok2APIRetryWorker(db)

        with patch('core.grok2api_retry.upload_registered_sso') as upload:
            upload.return_value = {
                'grok2api': {'probe': {'ok': True, 'status': 200}},
            }
            self.assertEqual(worker.run_once(), 1)

        upload.assert_called_once_with(
            db.get_settings.return_value,
            'sso-token',
            email='[邮箱]',
            # sub2api 引入后，重试器显式声明只走 grok2api，
            # 否则会重复投递给另一个后端。
            only='grok2api',
        )
        db.finish_grok2api_upload.assert_called_once_with(9, True)

    def test_failed_delivery_is_kept_for_later_retry(self):
        db = Mock()
        db.get_settings.return_value = {'grok2api_auto_upload': 'true'}
        db.claim_grok2api_retries.return_value = [{
            'id': 10,
            'email': '[邮箱]',
            'sso_value': 'sso-token',
        }]
        worker = Grok2APIRetryWorker(db)

        with patch(
            'core.grok2api_retry.upload_registered_sso',
            side_effect=RuntimeError('temporary'),
        ):
            self.assertEqual(worker.run_once(), 0)

        db.finish_grok2api_upload.assert_called_once()
        self.assertFalse(db.finish_grok2api_upload.call_args.args[1])

    def test_durable_retry_updates_live_state_and_emits_status(self):
        db = Mock()
        db.get_settings.return_value = {'grok2api_auto_upload': 'true'}
        db.claim_grok2api_retries.return_value = [{
            'id': 42,
            'email': '[邮箱]',
            'sso_value': 'sso-token',
        }]
        state = RegistrationState()
        # Simulate first-pass mint rate-limit already counted as failed.
        state.record_chat_probe_from_upload(
            error=RuntimeError('device/code 429'),
            reg_id=42,
        )
        emitted = []
        worker = Grok2APIRetryWorker(
            db,
            state_getter=lambda: state,
            status_emitter=lambda snap: emitted.append(snap),
        )

        with patch(
            'core.grok2api_retry.upload_registered_sso',
            return_value={'grok2api': {'probe': {'ok': True, 'status': 200}}},
        ):
            self.assertEqual(worker.run_once(), 1)

        snap = state.get_snapshot()
        self.assertEqual(snap['chat_probe_failed'], 0)
        self.assertEqual(snap['chat_probe_passed'], 1)
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0]['chat_probe_passed'], 1)

    def test_durable_permission_denied_updates_denied_counter(self):
        db = Mock()
        db.get_settings.return_value = {'grok2api_auto_upload': 'true'}
        db.claim_grok2api_retries.return_value = [{
            'id': 43,
            'email': '[邮箱]',
            'sso_value': 'sso-token',
        }]
        state = RegistrationState()
        state.record_chat_probe_from_upload(
            error=RuntimeError('device/code 429'),
            reg_id=43,
        )
        worker = Grok2APIRetryWorker(db, state_getter=lambda: state)

        with patch(
            'core.grok2api_retry.upload_registered_sso',
            side_effect=Grok2APIChatPermissionError({
                'status': 403,
                'error': 'Access denied',
            }),
        ):
            self.assertEqual(worker.run_once(), 0)

        db.finish_grok2api_probe.assert_called_once()
        snap = state.get_snapshot()
        self.assertEqual(snap['chat_probe_failed'], 0)
        self.assertEqual(snap['chat_probe_denied'], 1)


if __name__ == '__main__':
    unittest.main()
