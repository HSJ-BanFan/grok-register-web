import os
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import core.database as database_module
from core.database import Database
from core.register import RegistrationEngine, RegistrationState


class ConcurrentAliasClaimTest(unittest.TestCase):
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

    def tearDown(self):
        self.db.conn.close()
        Database._instance = self.previous_instance
        self.db_path_patch.stop()
        self.temp_dir.cleanup()

    def _add_accounts(self, count):
        for index in range(count):
            self.db.upsert_account(
                f'user{index}@example.com',
                '',
                f'client-{index}',
                f'refresh-{index}',
            )

    def test_parallel_claims_use_unique_aliases_and_accounts(self):
        worker_count = 4
        self._add_accounts(worker_count)
        barrier = threading.Barrier(worker_count + 1)
        claims = []
        errors = []
        result_lock = threading.Lock()

        def claim(index):
            try:
                barrier.wait()
                result = self.db.claim_next_alias(
                    max_retries=3,
                    lease_owner=f'worker-{index}',
                    lease_seconds=60,
                )
                with result_lock:
                    claims.append(result)
            except Exception as exc:
                with result_lock:
                    errors.append(exc)

        threads = [
            threading.Thread(target=claim, args=(index,))
            for index in range(worker_count)
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=5)

        self.assertFalse(errors)
        self.assertEqual(len(claims), worker_count)
        self.assertNotIn(None, claims)
        self.assertEqual(len({item['id'] for item in claims}), worker_count)
        self.assertEqual(
            len({item['account_id'] for item in claims}),
            worker_count,
        )
        self.assertTrue(all(item['status'] == 'processing' for item in claims))

    def test_same_account_allows_only_one_active_alias(self):
        self._add_accounts(1)

        first = self.db.claim_next_alias(3, 'worker-1', lease_seconds=60)
        second = self.db.claim_next_alias(3, 'worker-2', lease_seconds=60)

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        active = self.db.conn.execute(
            "SELECT COUNT(*) AS c FROM aliases WHERE status='processing'",
        ).fetchone()['c']
        self.assertEqual(active, 1)

    def test_expired_worker_lease_is_recovered_and_reclaimed(self):
        self._add_accounts(1)
        first = self.db.claim_next_alias(3, 'crashed-worker', lease_seconds=60)
        reg_id = self.db.create_registration(
            first['id'], first['alias_email'], 'password', 1,
            lease_owner='crashed-worker',
        )
        self.db.conn.execute(
            "UPDATE aliases SET lease_expires_at='2000-01-01 00:00:00' WHERE id=?",
            (first['id'],),
        )
        self.db.conn.commit()

        reclaimed = self.db.claim_next_alias(
            3, 'replacement-worker', lease_seconds=60,
        )

        self.assertEqual(reclaimed['id'], first['id'])
        self.assertEqual(reclaimed['retry_count'], 0)
        alias = self.db.conn.execute(
            '''SELECT status, retry_count, lease_owner
               FROM aliases WHERE id=?''',
            (first['id'],),
        ).fetchone()
        registration = self.db.conn.execute(
            'SELECT status, error_message FROM registrations WHERE id=?',
            (reg_id,),
        ).fetchone()
        self.assertEqual(alias['status'], 'processing')
        self.assertEqual(alias['retry_count'], 0)
        self.assertEqual(alias['lease_owner'], 'replacement-worker')
        self.assertEqual(registration['status'], 'interrupted')
        self.assertIn('lease expired', registration['error_message'].lower())

    def test_failure_transition_updates_retry_and_alias_atomically(self):
        self._add_accounts(1)
        alias = self.db.claim_next_alias(2, 'worker-1', lease_seconds=60)
        reg_id = self.db.create_registration(
            alias['id'], alias['alias_email'], 'password', 1,
            lease_owner='worker-1',
        )

        outcome = self.db.finish_registration_attempt(
            reg_id, alias['id'], 'worker-1',
            'Turnstile challenge timed out', 1.2, 2,
        )

        row = self.db.conn.execute(
            'SELECT status, retry_count, lease_owner FROM aliases WHERE id=?',
            (alias['id'],),
        ).fetchone()
        registration = self.db.conn.execute(
            'SELECT status FROM registrations WHERE id=?',
            (reg_id,),
        ).fetchone()
        self.assertFalse(outcome['terminal'])
        self.assertEqual(outcome['retry_count'], 1)
        self.assertEqual(row['status'], 'ready')
        self.assertEqual(row['retry_count'], 1)
        self.assertEqual(row['lease_owner'], '')
        self.assertEqual(registration['status'], 'failed')

    def test_upstream_permission_abort_preserves_alias_retry_budget(self):
        self._add_accounts(1)
        alias = self.db.claim_next_alias(2, 'worker-1', lease_seconds=60)
        reg_id = self.db.create_registration(
            alias['id'], alias['alias_email'], 'password', 1,
            lease_owner='worker-1',
        )

        released = self.db.abort_registration_attempt(
            reg_id,
            alias['id'],
            'worker-1',
            '[permission_denied] HTTP 403',
            0.8,
        )

        row = self.db.conn.execute(
            '''SELECT status, retry_count, lease_owner, error_reason
               FROM aliases WHERE id=?''',
            (alias['id'],),
        ).fetchone()
        registration = self.db.conn.execute(
            'SELECT status, error_message FROM registrations WHERE id=?',
            (reg_id,),
        ).fetchone()
        self.assertTrue(released)
        self.assertEqual(row['status'], 'ready')
        self.assertEqual(row['retry_count'], 0)
        self.assertEqual(row['lease_owner'], '')
        self.assertEqual(row['error_reason'], '')
        self.assertEqual(registration['status'], 'failed')
        self.assertIn('permission_denied', registration['error_message'])

    def test_existing_account_is_skipped_without_retrying_alias(self):
        self._add_accounts(1)
        alias = self.db.claim_next_alias(3, 'worker-1', lease_seconds=60)
        reg_id = self.db.create_registration(
            alias['id'], alias['alias_email'], 'password', 1,
            lease_owner='worker-1',
        )

        outcome = self.db.skip_existing_account_attempt(
            reg_id,
            alias['id'],
            'worker-1',
            '注册邮箱已存在：xAI reports Existing account found',
            0.9,
        )

        row = self.db.conn.execute(
            '''SELECT status, retry_count, failure_category, lease_owner
               FROM aliases WHERE id=?''',
            (alias['id'],),
        ).fetchone()
        registration = self.db.conn.execute(
            'SELECT status, error_message FROM registrations WHERE id=?',
            (reg_id,),
        ).fetchone()
        next_alias = self.db.claim_next_alias(
            3, 'worker-2', lease_seconds=60,
        )

        self.assertFalse(outcome['lease_lost'])
        self.assertEqual(row['status'], 'failed')
        self.assertEqual(row['retry_count'], 0)
        self.assertEqual(row['failure_category'], 'existing_account')
        self.assertEqual(row['lease_owner'], '')
        self.assertEqual(registration['status'], 'skipped')
        self.assertIn('Existing account found', registration['error_message'])
        self.assertNotEqual(next_alias['id'], alias['id'])
        self.assertEqual(next_alias['alias_index'], 1)


class RegistrationStateConcurrencyTest(unittest.TestCase):
    def test_active_workers_and_counters_are_thread_safe(self):
        state = RegistrationState()
        worker_count = 8
        barrier = threading.Barrier(worker_count + 1)

        def update(index):
            alias = {
                'id': index,
                'account_id': index,
                'alias_email': f'user{index}@example.com',
            }
            barrier.wait()
            round_number = state.reserve_round()
            state.set_worker_active(f'worker-{index}', round_number, alias)
            state.record_success()

        threads = [
            threading.Thread(target=update, args=(index,))
            for index in range(worker_count)
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=5)

        snapshot = state.get_snapshot()
        self.assertEqual(snapshot['current_round'], worker_count)
        self.assertEqual(snapshot['completed'], worker_count)
        self.assertEqual(snapshot['success'], worker_count)
        self.assertEqual(len(snapshot['active_workers']), worker_count)


class RegistrationCoordinatorTest(unittest.TestCase):
    def test_each_worker_receives_an_independent_browser_manager(self):
        db = Mock()
        db.get_settings.return_value = {
            'browser_headless': 'true',
            'browser_proxy': '',
            'registration_timeout': '300',
        }
        db.claim_next_alias.return_value = None
        browser_template = Mock()
        browsers = [Mock(), Mock(), Mock()]
        browser_template.clone.side_effect = browsers
        socketio = Mock()
        state = RegistrationState()
        engine = RegistrationEngine(
            db, browser_template, Mock(), socketio, state,
        )

        engine.run(max_rounds=0, max_retries=3, concurrency=3)

        self.assertEqual(browser_template.clone.call_count, 3)
        self.assertEqual(
            [call.kwargs['worker_id'] for call in browser_template.clone.call_args_list],
            ['worker-1', 'worker-2', 'worker-3'],
        )
        for browser in browsers:
            browser.start.assert_called_once_with()
            browser.stop.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
