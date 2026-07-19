import unittest

from core.grok2api_client import Grok2APIChatPermissionError
from core.registration.state import RegistrationState


class ChatProbeStatsTest(unittest.TestCase):
    def test_snapshot_includes_probe_counters(self):
        state = RegistrationState()
        snap = state.get_snapshot()
        self.assertEqual(snap['chat_probe_passed'], 0)
        self.assertEqual(snap['chat_probe_denied'], 0)
        self.assertEqual(snap['chat_probe_failed'], 0)
        self.assertEqual(snap['chat_probe_skipped'], 0)

    def test_record_from_upload_result_shapes(self):
        state = RegistrationState()
        state.record_chat_probe_from_upload({
            'grok2api': {'probe': {'ok': True, 'status': 200}},
        })
        state.record_chat_probe_from_upload({
            'grok2api': {'probe': {'ok': True, 'skipped': True}},
        })
        state.record_chat_probe_from_upload({
            'grok2api_probe_denied': {'status': 403, 'error': 'Access denied.'},
        })
        state.record_chat_probe_from_upload({
            'grok2api': {'probe': {'ok': False, 'status': 429, 'error': 'rate'}},
        })

        snap = state.get_snapshot()
        self.assertEqual(snap['chat_probe_passed'], 1)
        self.assertEqual(snap['chat_probe_skipped'], 1)
        self.assertEqual(snap['chat_probe_denied'], 1)
        self.assertEqual(snap['chat_probe_failed'], 1)

    def test_record_from_permission_error(self):
        state = RegistrationState()
        state.record_chat_probe_from_upload(
            error=Grok2APIChatPermissionError({'status': 403, 'error': 'Access denied.'}),
        )
        self.assertEqual(state.get_snapshot()['chat_probe_denied'], 1)

    def test_record_from_probe_runtime_error(self):
        state = RegistrationState()
        state.record_chat_probe_from_upload(
            error=RuntimeError('grok2api chat probe failed: HTTP 429: rate'),
        )
        self.assertEqual(state.get_snapshot()['chat_probe_failed'], 1)


if __name__ == '__main__':
    unittest.main()
