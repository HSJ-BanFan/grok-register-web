"""Unit tests for vendored local Turnstile solver process management."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
import core.database as database_module
from core.database import Database
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
    """API 测试需要一个真的 settings 表。

    原来 setUp 只建了 test_client，没初始化数据库 ——
    于是读 settings 时报 "no such table: settings"，被 Flask 包成 500。

    坑在于 Database 是单例，且 __init__ 有 _initialized 守卫：
      1. 光重置 _instance 不够，新实例不会重新连 DB_PATH
      2. 但如果把旧实例的 conn 关掉，上一个测试的模块还握着它 → 立刻炸

    所以：重置 _instance 和 _initialized，建新库，不碰旧连接。
    旧连接留给 interpreter 回收 —— 测试进程本来就短命。
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path_patch = patch.object(
            database_module,
            'DB_PATH',
            os.path.join(self.temp_dir.name, 'test.db'),
        )
        self.db_path_patch.start()

        self.previous_instance = Database._instance
        Database._instance = None

        self.db = Database()
        self.db.init_database()

        import app as app_module
        app_module.db = self.db
        import api.settings as settings_module
        settings_module.db = self.db

        self.client = app.test_client()

    def tearDown(self):
        Database._initialized = False
        if self.previous_instance is not None:
            self.previous_instance._initialized = False
        Database._instance = self.previous_instance
        self.db_path_patch.stop()
        self.temp_dir.cleanup()
