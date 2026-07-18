"""Unit tests for vendored local Turnstile solver process management."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app import app
from services import solver_manager


class SolverManagerPolicyTest(unittest.TestCase):
    def test_should_auto_start_when_local_and_no_yescaptcha(self):
        self.assertTrue(solver_manager.should_auto_start({
            'turnstile_provider': 'auto',
            'yescaptcha_key': '',
            'turnstile_solver_url': 'http://127.0.0.1:5072',
        }))

    def test_should_not_auto_start_with_yescaptcha(self):
        self.assertFalse(solver_manager.should_auto_start({
            'turnstile_provider': 'auto',
            'yescaptcha_key': 'client-key',
            'turnstile_solver_url': 'http://127.0.0.1:5072',
        }))

    def test_should_not_auto_start_browser_only(self):
        self.assertFalse(solver_manager.should_auto_start({
            'turnstile_provider': 'browser',
            'yescaptcha_key': '',
            'turnstile_solver_url': 'http://127.0.0.1:5072',
        }))

    def test_should_not_auto_start_remote_url(self):
        self.assertFalse(solver_manager.should_auto_start({
            'turnstile_provider': 'auto',
            'yescaptcha_key': '',
            'turnstile_solver_url': 'http://solver.example:5072',
        }))

    def test_parse_loopback_manageable(self):
        endpoint = solver_manager.parse_solver_endpoint('http://127.0.0.1:5072')
        self.assertTrue(endpoint['manageable'])
        self.assertEqual(endpoint['port'], 5072)

    def test_parse_remote_not_manageable(self):
        endpoint = solver_manager.parse_solver_endpoint('http://10.0.0.8:5072')
        self.assertFalse(endpoint['manageable'])


class SolverManagerStartTest(unittest.TestCase):
    def setUp(self):
        solver_manager._consecutive_failures = 0
        solver_manager._last_failure_reason = ''
        solver_manager._proc = None
        solver_manager._owned_by_us = False

    def tearDown(self):
        solver_manager._proc = None
        solver_manager._owned_by_us = False
        solver_manager._consecutive_failures = 0
        solver_manager._last_failure_reason = ''

    @patch('services.solver_manager.is_running', return_value=True)
    def test_start_short_circuits_when_already_online(self, _online):
        status = solver_manager.start({
            'turnstile_solver_url': 'http://127.0.0.1:5072',
        })
        self.assertTrue(status['running'] or status.get('online') is True)

    @patch('services.solver_manager._ensure_camoufox_browser', return_value=True)
    @patch('services.solver_manager.subprocess.Popen')
    def test_start_spawns_child_and_waits_ready(self, popen, _camoufox):
        proc = MagicMock()
        proc.poll.return_value = None
        proc.pid = 4242
        proc.stderr = MagicMock()
        popen.return_value = proc

        # First call: not online yet (enter start path); subsequent: ready.
        online_calls = {'n': 0}

        def fake_online(_url=None):
            online_calls['n'] += 1
            return online_calls['n'] >= 2

        with patch('services.solver_manager.is_running', side_effect=fake_online), \
                patch('services.solver_manager.time.sleep', return_value=None):
            status = solver_manager.start({
                'turnstile_solver_url': 'http://127.0.0.1:5072',
                'browser_proxy': '',
            }, browser_type='chromium', thread=1, proxy=False, force=True)

        self.assertEqual(status.get('pid'), 4242)
        self.assertTrue(status.get('managed'))
        popen.assert_called_once()
        cmd = popen.call_args[0][0]
        self.assertIn('start.py', cmd[1].replace('\\', '/'))
        self.assertIn('--port', cmd)
        self.assertIn('5072', cmd)


class SolverControlApiTest(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    @patch('api.settings.solver_manager.get_status')
    @patch('api.settings.probe_turnstile_solver')
    def test_status_endpoint(self, probe, get_status):
        probe.return_value = {
            'online': False,
            'reason': 'connection_error',
            'status_code': None,
            'latency_ms': 5,
        }
        get_status.return_value = {
            'running': False,
            'online': False,
            'managed': False,
            'pid': None,
            'url': 'http://127.0.0.1:5072',
            'port': 5072,
            'manageable': True,
            'consecutive_failures': 0,
        }
        response = self.client.get('/api/settings/turnstile-solver/status')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertIn('auto_start', payload['data'])
        self.assertIn('probe', payload['data'])

    @patch('api.settings.solver_manager.is_running', return_value=True)
    @patch('api.settings.solver_manager.start')
    def test_start_endpoint(self, start, _running):
        start.return_value = {
            'running': True,
            'online': True,
            'managed': True,
            'pid': 99,
            'url': 'http://127.0.0.1:5072',
            'port': 5072,
            'manageable': True,
            'consecutive_failures': 0,
        }
        response = self.client.post(
            '/api/settings/turnstile-solver/start',
            json={'url': 'http://127.0.0.1:5072'},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        start.assert_called_once()

    @patch('api.settings.solver_manager.stop')
    def test_stop_endpoint(self, stop):
        stop.return_value = {
            'running': False,
            'online': False,
            'managed': False,
            'pid': None,
            'url': 'http://127.0.0.1:5072',
            'port': 5072,
            'manageable': True,
            'consecutive_failures': 0,
        }
        response = self.client.post('/api/settings/turnstile-solver/stop', json={})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        stop.assert_called_once_with(kill_orphans=True)


if __name__ == '__main__':
    unittest.main()
