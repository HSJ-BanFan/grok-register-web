import unittest
from unittest.mock import Mock, patch

import requests

from app import app
from core.registration.turnstile import probe_turnstile_solver


class TurnstileSolverProbeTest(unittest.TestCase):
    def test_probe_reports_online_status_and_latency(self):
        response = Mock(status_code=200)
        session = Mock()
        session.get.return_value = response

        with patch(
            'core.registration.turnstile.requests.Session',
            return_value=session,
        ), patch(
            'core.registration.turnstile.time.perf_counter',
            side_effect=[10.0, 10.125],
        ):
            result = probe_turnstile_solver('http://127.0.0.1:5072')

        self.assertTrue(result['online'])
        self.assertEqual(result['reason'], 'online')
        self.assertEqual(result['status_code'], 200)
        self.assertEqual(result['latency_ms'], 125)
        self.assertFalse(session.trust_env)
        session.get.assert_called_once_with(
            'http://127.0.0.1:5072/', timeout=2.0, allow_redirects=False,
        )

    def test_probe_reports_connection_failure_without_leaking_exception(self):
        session = Mock()
        session.get.side_effect = requests.ConnectionError(
            'secret-user:secret-pass@solver.internal refused',
        )

        with patch(
            'core.registration.turnstile.requests.Session',
            return_value=session,
        ):
            result = probe_turnstile_solver('http://solver.internal:5072')

        self.assertFalse(result['online'])
        self.assertEqual(result['reason'], 'connection_error')
        self.assertIsNone(result['status_code'])
        self.assertNotIn('secret', str(result))

    def test_probe_rejects_non_http_urls(self):
        result = probe_turnstile_solver('file:///tmp/solver.sock')

        self.assertFalse(result['online'])
        self.assertEqual(result['reason'], 'invalid_url')


class TurnstileSolverStatusApiTest(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    @patch('api.settings.probe_turnstile_solver')
    def test_test_endpoint_probes_supplied_unsaved_url(self, probe):
        probe.return_value = {
            'online': True,
            'reason': 'online',
            'status_code': 200,
            'latency_ms': 12,
        }

        response = self.client.post(
            '/api/settings/turnstile-solver/test',
            json={'url': 'http://solver.example:5072'},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertTrue(payload['data']['online'])
        probe.assert_called_once_with('http://solver.example:5072')


if __name__ == '__main__':
    unittest.main()
