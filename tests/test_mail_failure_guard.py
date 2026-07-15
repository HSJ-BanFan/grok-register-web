import os
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

import core.database as database_module
from core.database import Database
from core.failure_policy import is_mail_fetch_error


class MailFailureGuardTest(unittest.TestCase):
    def test_detects_verification_code_errors(self):
        self.assertTrue(is_mail_fetch_error(
            'Failed to get verification code after 5 attempts'
        ))
        self.assertTrue(is_mail_fetch_error(
            'Mail.tm: failed to get verification code'
        ))
        self.assertTrue(is_mail_fetch_error(
            'Token refresh failed on all endpoints'
        ))
        self.assertTrue(is_mail_fetch_error(
            'Microsoft Graph mail failed: HTTP 403 (ErrorAccessDenied)'
        ))

    def test_ignores_unrelated_registration_errors(self):
        self.assertFalse(is_mail_fetch_error(
            '注册提交未生效，页面未跳转且无SSO，需要重新尝试'
        ))
        self.assertFalse(is_mail_fetch_error(
            'Failed to fill email or find submit button'
        ))
        self.assertFalse(is_mail_fetch_error(
            'Failed to fill/confirm verification code'
        ))


class AliasBudgetLogicTest(unittest.TestCase):
    def test_failure_budget_constant(self):
        from core.database import Database
        self.assertGreaterEqual(Database.ALIAS_FAILURE_BUDGET, 2)
        self.assertLessEqual(Database.ALIAS_FAILURE_BUDGET, 5)

    def test_registration_defaults_to_single_worker(self):
        self.assertEqual(
            database_module.DEFAULT_SETTINGS['registration_concurrency'], '1',
        )


class LegacyAliasMigrationTest(unittest.TestCase):
    def test_adds_and_backfills_terminal_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, 'legacy.db')
            legacy = sqlite3.connect(db_path)
            legacy.executescript('''
                CREATE TABLE accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT UNIQUE NOT NULL,
                    password TEXT DEFAULT '',
                    client_id TEXT NOT NULL,
                    refresh_token TEXT NOT NULL,
                    status TEXT DEFAULT 'ready',
                    max_aliases INTEGER DEFAULT 5,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE aliases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL,
                    alias_email TEXT NOT NULL,
                    alias_index INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'ready',
                    sso_value TEXT DEFAULT '',
                    error_reason TEXT DEFAULT '',
                    retry_count INTEGER DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    used_at DATETIME
                );
                INSERT INTO accounts (email, client_id, refresh_token)
                VALUES ('legacy@example.com', 'client-id', 'refresh-token');
                INSERT INTO aliases (
                    account_id, alias_email, status, error_reason, created_at
                ) VALUES (
                    1, 'legacy@example.com', 'failed',
                    'Failed to get verification code after 3 attempts',
                    '2026-01-02 03:04:05'
                );
            ''')
            legacy.commit()
            legacy.close()

            previous_instance = Database._instance
            try:
                with patch.object(database_module, 'DB_PATH', db_path):
                    Database._instance = None
                    db = Database()
                    db.init_database()
                    columns = {
                        row['name']
                        for row in db.conn.execute('PRAGMA table_info(aliases)').fetchall()
                    }
                    row = db.conn.execute(
                        '''SELECT failure_category, completed_at
                           FROM aliases WHERE id=1'''
                    ).fetchone()
                    self.assertIn('failure_category', columns)
                    self.assertIn('completed_at', columns)
                    self.assertEqual(row['failure_category'], 'mail_fetch')
                    self.assertEqual(row['completed_at'], '2026-01-02 03:04:05')
                    db.conn.close()
            finally:
                Database._instance = previous_instance


class AccountDisablePolicyTest(unittest.TestCase):
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
        self.account_id = self.db.upsert_account(
            'main@example.com', '', 'client-id', 'refresh-token'
        )

    def tearDown(self):
        self.db.conn.close()
        Database._instance = self.previous_instance
        self.db_path_patch.stop()
        self.temp_dir.cleanup()

    def _finish_alias(self, status, error=''):
        alias = self.db.get_next_alias(max_retries=3)
        self.assertIsNotNone(alias)
        self.db.update_alias_status(alias['id'], status, error=error)
        return self.db.maybe_disable_unusable_account(
            self.account_id,
            error,
        )

    def test_success_breaks_consecutive_mail_failures(self):
        mail_error = 'Failed to get verification code after 3 attempts'

        self.assertFalse(self._finish_alias('failed', mail_error))
        self.assertFalse(self._finish_alias('used'))
        self.assertFalse(self._finish_alias('failed', mail_error))
        self.assertEqual(self.db.get_account(self.account_id)['status'], 'ready')

        self.assertTrue(self._finish_alias('failed', mail_error))
        self.assertEqual(self.db.get_account(self.account_id)['status'], 'disabled')

    def test_consecutive_failures_follow_completion_order_not_alias_index(self):
        mail_error = 'Failed to get verification code after 3 attempts'
        alias_0 = self.db.create_alias(self.account_id, 'main@example.com', 0)
        alias_1 = self.db.create_alias(self.account_id, 'main+1@example.com', 1)
        alias_2 = self.db.create_alias(self.account_id, 'main+2@example.com', 2)

        self.db.update_alias_status(alias_2, 'used')
        time.sleep(0.002)
        self.db.update_alias_status(alias_0, 'failed', error=mail_error)
        time.sleep(0.002)
        self.db.update_alias_status(alias_1, 'failed', error=mail_error)

        self.assertTrue(self.db.maybe_disable_unusable_account(
            self.account_id,
            mail_error,
        ))

    def test_unrelated_failure_breaks_consecutive_mail_failures(self):
        mail_error = 'Failed to get verification code after 3 attempts'

        self.assertFalse(self._finish_alias('failed', mail_error))
        self.assertFalse(self._finish_alias('failed', 'Turnstile challenge timed out'))
        self.assertFalse(self._finish_alias('failed', mail_error))
        self.assertEqual(self.db.get_account(self.account_id)['status'], 'ready')

        self.assertTrue(self._finish_alias('failed', mail_error))
        self.assertEqual(self.db.get_account(self.account_id)['status'], 'disabled')

    def test_non_mail_failures_only_disable_after_alias_budget_is_exhausted(self):
        account = self.db.get_account(self.account_id)
        budget = account['max_aliases'] + Database.ALIAS_FAILURE_BUDGET

        for _ in range(budget - 1):
            self.assertFalse(self._finish_alias('failed', 'Turnstile challenge timed out'))

        self.assertTrue(self._finish_alias('failed', 'Turnstile challenge timed out'))
        self.assertEqual(self.db.get_account(self.account_id)['status'], 'disabled')

    def test_database_initialization_removes_deprecated_email_provider_setting(self):
        self.db.update_settings({'email_provider': 'mail.tm'})

        self.db.init_database()

        self.assertNotIn('email_provider', self.db.get_settings())

    def test_reset_settings_removes_deprecated_email_provider_setting(self):
        self.db.update_settings({'email_provider': 'mail.tm'})

        self.db.reset_settings()

        self.assertNotIn('email_provider', self.db.get_settings())

    def test_recover_stale_does_not_touch_recent_pending_registration(self):
        alias = self.db.get_next_alias(max_retries=3)
        reg_id = self.db.create_registration(
            alias['id'], alias['alias_email'], 'password', 1,
        )

        self.db.recover_stale(timeout_seconds=300)

        registration = self.db.conn.execute(
            'SELECT status FROM registrations WHERE id=?', (reg_id,),
        ).fetchone()
        alias_row = self.db.conn.execute(
            'SELECT status, retry_count FROM aliases WHERE id=?', (alias['id'],),
        ).fetchone()
        self.assertEqual(registration['status'], 'pending')
        self.assertEqual(alias_row['status'], 'ready')
        self.assertEqual(alias_row['retry_count'], 0)

    def test_recover_stale_marks_registration_interrupted_without_consuming_retry(self):
        self.db.conn.execute(
            'UPDATE accounts SET max_aliases=1 WHERE id=?', (self.account_id,),
        )
        self.db.conn.commit()

        budget = 1 + Database.ALIAS_FAILURE_BUDGET
        for index in range(budget - 1):
            alias_id = self.db.create_alias(
                self.account_id, f'main+failed{index}@example.com', index,
            )
            self.db.update_alias_status(
                alias_id, 'failed', error='Turnstile challenge timed out',
            )

        pending_alias_id = self.db.create_alias(
            self.account_id, 'main+pending@example.com', budget - 1,
        )
        reg_id = self.db.create_registration(
            pending_alias_id, 'main+pending@example.com', 'password', 1,
        )
        self.db.conn.execute(
            "UPDATE registrations SET created_at='2000-01-01 00:00:00' WHERE id=?",
            (reg_id,),
        )
        self.db.conn.commit()
        self.db.update_settings({'max_retries_per_alias': '1'})

        self.db.recover_stale(timeout_seconds=300)

        registration = self.db.conn.execute(
            'SELECT status, error_message FROM registrations WHERE id=?',
            (reg_id,),
        ).fetchone()
        alias_row = self.db.conn.execute(
            '''SELECT status, retry_count, failure_category, completed_at
               FROM aliases WHERE id=?''',
            (pending_alias_id,),
        ).fetchone()
        self.assertEqual(registration['status'], 'interrupted')
        self.assertIn('interrupted', registration['error_message'].lower())
        self.assertEqual(alias_row['status'], 'ready')
        self.assertEqual(alias_row['retry_count'], 0)
        self.assertEqual(alias_row['failure_category'], '')
        self.assertIsNone(alias_row['completed_at'])
        self.assertEqual(self.db.get_account(self.account_id)['status'], 'ready')


if __name__ == '__main__':
    unittest.main()
