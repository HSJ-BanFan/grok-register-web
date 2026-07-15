import sqlite3
import threading
import logging
from datetime import datetime, timedelta
from config import DB_PATH
from core.failure_policy import (
    FAILURE_CATEGORY_MAIL_FETCH,
    FAILURE_CATEGORY_REGISTRATION,
    account_disable_reason,
    classify_failure,
)

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS = {
    'max_aliases_per_account': '5',
    'max_code_retries': '3',
    'max_confirm_retries': '3',
    'max_retries_per_alias': '3',
    'registration_timeout': '300',
    'registration_concurrency': '1',
    'browser_headless': 'false',
    'turnstile_auto': 'true',
    'random_name_enabled': 'true',
    'extract_numbers_enabled': 'false',
    'password_mode': 'auto',
    'manual_password': '',
    'export_format': 'txt',
    'export_dir': './data',
    'grok2api_auto_upload': 'false',
    'grok2api_url': 'http://127.0.0.1:21434',
    'grok2api_username': 'admin',
    'grok2api_password': '',
    # Default OFF: opening grok.com after every register triggers managed CF
    # challenges that cannot be fully auto-solved. Upload/Build convert still work
    # without browser CF cookies. Use batch reactivation when CF context is needed.
    'grok_web_activation': 'false',
    # Browser network proxy, e.g. http://127.0.0.1:7897
    # Aligns with repos/automation/tooling/grok-register which avoids most CF challenges via proxy.
    'browser_proxy': '',
}

DEPRECATED_SETTING_KEYS = ('email_provider',)


class Database:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._write_lock = threading.RLock()
        import os
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.row_factory = sqlite3.Row

    def init_database(self):
        with self._write_lock:
            cur = self.conn.cursor()
            cur.executescript('''
                CREATE TABLE IF NOT EXISTS accounts (
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

                CREATE TABLE IF NOT EXISTS aliases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                    alias_email TEXT NOT NULL,
                    alias_index INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'ready',
                    sso_value TEXT DEFAULT '',
                    error_reason TEXT DEFAULT '',
                    failure_category TEXT DEFAULT '',
                    retry_count INTEGER DEFAULT 0,
                    lease_owner TEXT DEFAULT '',
                    lease_expires_at DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    used_at DATETIME,
                    completed_at DATETIME
                );

                CREATE TABLE IF NOT EXISTS registrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alias_id INTEGER REFERENCES aliases(id) ON DELETE SET NULL,
                    email TEXT NOT NULL,
                    account_password TEXT DEFAULT '',
                    sso_value TEXT DEFAULT '',
                    status TEXT DEFAULT 'pending',
                    error_message TEXT DEFAULT '',
                    duration_seconds REAL DEFAULT 0,
                    round_number INTEGER DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_alias_account_index
                    ON aliases(account_id, alias_index);
                CREATE INDEX IF NOT EXISTS idx_aliases_account
                    ON aliases(account_id);
                CREATE INDEX IF NOT EXISTS idx_aliases_status
                    ON aliases(status);
                CREATE INDEX IF NOT EXISTS idx_registrations_status
                    ON registrations(status);
                CREATE INDEX IF NOT EXISTS idx_registrations_created
                    ON registrations(created_at);
            ''')
            self._migrate_alias_terminal_metadata(cur)
            cur.execute(
                '''CREATE UNIQUE INDEX IF NOT EXISTS idx_aliases_active_account
                   ON aliases(account_id) WHERE status = 'processing' '''
            )
            cur.execute(
                '''CREATE INDEX IF NOT EXISTS idx_aliases_lease_expiry
                   ON aliases(status, lease_expires_at)'''
            )
            for key, value in DEFAULT_SETTINGS.items():
                cur.execute(
                    'INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)',
                    (key, value)
                )
            self._remove_deprecated_settings(cur)
            self.conn.commit()
            logger.info("Database initialized successfully")

    @staticmethod
    def _remove_deprecated_settings(cursor):
        cursor.executemany(
            'DELETE FROM settings WHERE key = ?',
            ((key,) for key in DEPRECATED_SETTING_KEYS),
        )

    @staticmethod
    def _migrate_alias_terminal_metadata(cursor):
        columns = {
            row['name'] for row in cursor.execute('PRAGMA table_info(aliases)').fetchall()
        }
        if 'failure_category' not in columns:
            cursor.execute(
                "ALTER TABLE aliases ADD COLUMN failure_category TEXT DEFAULT ''"
            )
        if 'completed_at' not in columns:
            cursor.execute('ALTER TABLE aliases ADD COLUMN completed_at DATETIME')
        if 'lease_owner' not in columns:
            cursor.execute("ALTER TABLE aliases ADD COLUMN lease_owner TEXT DEFAULT ''")
        if 'lease_expires_at' not in columns:
            cursor.execute('ALTER TABLE aliases ADD COLUMN lease_expires_at DATETIME')

        rows = cursor.execute(
            '''SELECT id, status, error_reason, failure_category,
                      created_at, used_at, completed_at
               FROM aliases
               WHERE status IN ('used', 'failed')'''
        ).fetchall()
        for row in rows:
            category = row['failure_category'] or (
                classify_failure(row['error_reason'])
                if row['status'] == 'failed' else ''
            )
            completed_at = row['completed_at'] or row['used_at'] or row['created_at']
            cursor.execute(
                '''UPDATE aliases SET failure_category=?, completed_at=?
                   WHERE id=?''',
                (category, completed_at, row['id']),
            )

    # ── Accounts CRUD ──────────────────────────────────────────

    def get_accounts(self, status_filter=None):
        sql = '''SELECT a.*,
                    (SELECT COUNT(*) FROM aliases WHERE account_id = a.id) AS alias_count,
                    (SELECT COUNT(*) FROM aliases WHERE account_id = a.id AND status = 'used') AS used_count,
                    (SELECT COUNT(*) FROM registrations r
                        JOIN aliases al ON r.alias_id = al.id
                        WHERE al.account_id = a.id AND r.status = 'success') AS success_count
                 FROM accounts a'''
        params = ()
        if status_filter and status_filter != 'all':
            sql += ' WHERE a.status = ?'
            params = (status_filter,)
        sql += ' ORDER BY a.id ASC'
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_account(self, account_id):
        row = self.conn.execute(
            'SELECT * FROM accounts WHERE id = ?', (account_id,)
        ).fetchone()
        return dict(row) if row else None

    def upsert_account(self, email, password, client_id, refresh_token):
        with self._write_lock:
            cur = self.conn.cursor()
            existing = cur.execute(
                'SELECT id FROM accounts WHERE email = ?', (email,)
            ).fetchone()
            if existing:
                if password:
                    cur.execute(
                        '''UPDATE accounts SET password=?, client_id=?, refresh_token=?,
                           updated_at=CURRENT_TIMESTAMP WHERE email=?''',
                        (password, client_id, refresh_token, email)
                    )
                else:
                    cur.execute(
                        '''UPDATE accounts SET client_id=?, refresh_token=?,
                           updated_at=CURRENT_TIMESTAMP WHERE email=?''',
                        (client_id, refresh_token, email)
                    )
                self.conn.commit()
                return existing['id']
            else:
                cur.execute(
                    '''INSERT INTO accounts (email, password, client_id, refresh_token)
                       VALUES (?, ?, ?, ?)''',
                    (email, password, client_id, refresh_token)
                )
                self.conn.commit()
                return cur.lastrowid

    def delete_accounts(self, ids):
        if not ids:
            return
        with self._write_lock:
            placeholders = ','.join('?' * len(ids))
            self.conn.execute(
                f'DELETE FROM accounts WHERE id IN ({placeholders})', ids
            )
            self.conn.commit()

    def reset_account(self, account_id):
        with self._write_lock:
            self.conn.execute(
                "UPDATE accounts SET status='ready', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (account_id,)
            )
            self.conn.execute(
                """UPDATE aliases SET status='ready', sso_value='', error_reason='',
                   failure_category='', retry_count=0, used_at=NULL, completed_at=NULL
                   WHERE account_id=?""",
                (account_id,)
            )
            self.conn.commit()

    def update_refresh_token(self, account_id, token):
        with self._write_lock:
            try:
                self.conn.execute(
                    'UPDATE accounts SET refresh_token=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
                    (token, account_id)
                )
                self.conn.commit()
            except Exception as e:
                logger.warning(f"Failed to write back refresh token for account {account_id}: {e}")

    def update_account_status(self, account_id, status):
        with self._write_lock:
            self.conn.execute(
                'UPDATE accounts SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
                (status, account_id)
            )
            self.conn.commit()

    def get_account_stats(self):
        total = self.conn.execute('SELECT COUNT(*) FROM accounts').fetchone()[0]
        # done = accounts that reached max_aliases successful registrations (via actual alias counts, not status field)
        done = self.conn.execute(
            '''SELECT COUNT(*) FROM accounts a
               WHERE (SELECT COUNT(*) FROM aliases WHERE account_id = a.id AND status = 'used') >= a.max_aliases'''
        ).fetchone()[0]
        disabled = self.conn.execute("SELECT COUNT(*) FROM accounts WHERE status='disabled'").fetchone()[0]
        # used = accounts with at least one alias attempted (non-ready) and not disabled
        used_accounts = self.conn.execute(
            '''SELECT COUNT(DISTINCT a.id) FROM accounts a
               INNER JOIN aliases al ON al.account_id = a.id
               WHERE al.status != 'ready' AND a.status != 'disabled' '''
        ).fetchone()[0]
        unused_accounts = total - used_accounts - disabled

        total_aliases = self.conn.execute('SELECT COUNT(*) FROM aliases').fetchone()[0]
        used_aliases = self.conn.execute("SELECT COUNT(*) FROM aliases WHERE status='used'").fetchone()[0]
        ready_aliases = self.conn.execute("SELECT COUNT(*) FROM aliases WHERE status='ready'").fetchone()[0]
        failed_aliases = self.conn.execute("SELECT COUNT(*) FROM aliases WHERE status='failed'").fetchone()[0]

        total_sso = self.conn.execute("SELECT COUNT(*) FROM registrations WHERE status='success'").fetchone()[0]
        today = datetime.now().strftime('%Y-%m-%d')
        today_sso = self.conn.execute(
            "SELECT COUNT(*) FROM registrations WHERE status='success' AND DATE(created_at)=?",
            (today,)
        ).fetchone()[0]
        non_pending = self.conn.execute(
            "SELECT COUNT(*) FROM registrations WHERE status != 'pending'"
        ).fetchone()[0]
        success_count = self.conn.execute(
            "SELECT COUNT(*) FROM registrations WHERE status='success'"
        ).fetchone()[0]
        success_rate = round(success_count / non_pending * 100, 1) if non_pending > 0 else 0
        avg_duration = self.conn.execute(
            "SELECT AVG(duration_seconds) FROM registrations WHERE status='success'"
        ).fetchone()[0] or 0

        return {
            'total_accounts': total,
            'used_accounts': used_accounts,
            'unused_accounts': unused_accounts,
            'done_accounts': done,
            'disabled_accounts': disabled,
            'total_aliases': total_aliases,
            'used_aliases': used_aliases,
            'ready_aliases': ready_aliases,
            'failed_aliases': failed_aliases,
            'total_sso': total_sso,
            'today_sso': today_sso,
            'success_rate': success_rate,
            'avg_duration': round(avg_duration, 1),
        }

    # ── Aliases CRUD ───────────────────────────────────────────

    # Extra failed aliases allowed per account before we stop minting replacements.
    # Prevents a dead mailbox from generating +1..+N forever (used count stays 0).
    ALIAS_FAILURE_BUDGET = 3
    DEFAULT_LEASE_SECONDS = 900

    @staticmethod
    def _lease_expiry(lease_seconds):
        seconds = max(30, int(lease_seconds or Database.DEFAULT_LEASE_SECONDS))
        return (datetime.now() + timedelta(seconds=seconds)).isoformat()

    def _count_consecutive_mail_failed_aliases_locked(self, cursor, account_id):
        rows = cursor.execute(
            '''SELECT status, failure_category FROM aliases
               WHERE account_id = ? AND status IN ('used', 'failed')
               ORDER BY julianday(completed_at) DESC, id DESC''',
            (account_id,),
        ).fetchall()
        count = 0
        for row in rows:
            if (
                row['status'] != 'failed'
                or row['failure_category'] != FAILURE_CATEGORY_MAIL_FETCH
            ):
                break
            count += 1
        return count

    def _account_disable_reason_locked(self, cursor, account_id):
        account = cursor.execute(
            'SELECT id, email, status, max_aliases FROM accounts WHERE id = ?',
            (account_id,),
        ).fetchone()
        if not account or account['status'] != 'ready':
            return account, ''

        counts = cursor.execute(
            '''SELECT
                   SUM(CASE WHEN status = 'used' THEN 1 ELSE 0 END) AS used_cnt,
                   COUNT(*) AS total_cnt,
                   SUM(CASE WHEN status = 'processing' THEN 1 ELSE 0 END) AS active_cnt
               FROM aliases WHERE account_id = ?''',
            (account_id,),
        ).fetchone()
        # Never disable an account while another alias is actively using its mailbox.
        if counts['active_cnt']:
            return account, ''

        reason = account_disable_reason(
            consecutive_mail_fails=self._count_consecutive_mail_failed_aliases_locked(
                cursor, account_id,
            ),
            used_count=counts['used_cnt'] or 0,
            total_count=counts['total_cnt'] or 0,
            max_aliases=account['max_aliases'],
            failure_budget=self.ALIAS_FAILURE_BUDGET,
        )
        return account, reason

    def _disable_account_locked(self, cursor, account_id, reason):
        completed_at = datetime.now().isoformat()
        cursor.execute(
            "UPDATE accounts SET status='disabled', updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (account_id,),
        )
        cursor.execute(
            '''UPDATE aliases SET status='failed', error_reason=?,
               failure_category=?, completed_at=?, lease_owner='', lease_expires_at=NULL
               WHERE account_id=? AND status='ready' ''',
            (
                f'account disabled: {reason}',
                FAILURE_CATEGORY_REGISTRATION,
                completed_at,
                account_id,
            ),
        )

    def _maybe_disable_unusable_account_locked(self, cursor, account_id):
        account, reason = self._account_disable_reason_locked(cursor, account_id)
        if not reason:
            return False, '', account
        self._disable_account_locked(cursor, account_id, reason)
        return True, reason, account

    def _recover_expired_alias_leases_locked(self, cursor, _max_retries, now_iso=None):
        now_iso = now_iso or datetime.now().isoformat()
        error = 'Registration interrupted: worker lease expired'
        rows = cursor.execute(
            '''SELECT id, account_id, retry_count FROM aliases
               WHERE status='processing'
                 AND (lease_expires_at IS NULL
                      OR julianday(lease_expires_at) <= julianday(?))''',
            (now_iso,),
        ).fetchall()
        recovered = []
        for row in rows:
            cursor.execute(
                '''UPDATE registrations SET status='interrupted', error_message=?
                   WHERE alias_id=? AND status='pending' ''',
                (error, row['id']),
            )
            retry_count = int(row['retry_count'] or 0)
            cursor.execute(
                '''UPDATE aliases SET status='ready', retry_count=?, error_reason='',
                   failure_category='', completed_at=NULL, used_at=NULL,
                   lease_owner='', lease_expires_at=NULL WHERE id=?''',
                (retry_count, row['id']),
            )
            recovered.append({
                'alias_id': row['id'],
                'account_id': row['account_id'],
                'retry_count': retry_count,
                'terminal': False,
                'account_disabled': False,
                'disable_reason': '',
            })
        return recovered

    def claim_next_alias(self, max_retries, lease_owner,
                         lease_seconds=DEFAULT_LEASE_SECONDS):
        """Atomically lease one alias while allowing only one active alias per account."""
        if not lease_owner:
            raise ValueError('lease_owner is required')

        with self._write_lock:
            cur = self.conn.cursor()
            try:
                cur.execute('BEGIN IMMEDIATE')
                self._recover_expired_alias_leases_locked(cur, max_retries)
                lease_expires_at = self._lease_expiry(lease_seconds)

                row = cur.execute(
                    '''SELECT al.*, a.client_id, a.refresh_token,
                              a.email AS main_email,
                              a.max_aliases AS account_max_aliases
                       FROM aliases al
                       JOIN accounts a ON al.account_id = a.id
                       WHERE al.status = 'ready'
                         AND al.retry_count < ?
                         AND a.status = 'ready'
                         AND NOT EXISTS (
                             SELECT 1 FROM aliases active
                             WHERE active.account_id = al.account_id
                               AND active.status = 'processing'
                         )
                       ORDER BY al.account_id ASC, al.alias_index ASC
                       LIMIT 1''',
                    (max_retries,),
                ).fetchone()

                if row:
                    cur.execute(
                        '''UPDATE aliases SET status='processing', lease_owner=?,
                           lease_expires_at=? WHERE id=? AND status='ready' ''',
                        (lease_owner, lease_expires_at, row['id']),
                    )
                    claimed = dict(row)
                    claimed.update({
                        'status': 'processing',
                        'lease_owner': lease_owner,
                        'lease_expires_at': lease_expires_at,
                    })
                    self.conn.commit()
                    return claimed

                failure_budget = self.ALIAS_FAILURE_BUDGET
                account = cur.execute(
                    '''SELECT a.* FROM accounts a
                       WHERE a.status = 'ready'
                         AND NOT EXISTS (
                             SELECT 1 FROM aliases active
                             WHERE active.account_id = a.id
                               AND active.status = 'processing'
                         )
                         AND (SELECT COUNT(*) FROM aliases
                              WHERE account_id = a.id AND status = 'used') < a.max_aliases
                         AND (SELECT COUNT(*) FROM aliases
                              WHERE account_id = a.id) < (a.max_aliases + ?)
                       ORDER BY a.id ASC
                       LIMIT 1''',
                    (failure_budget,),
                ).fetchone()
                if not account:
                    self.conn.commit()
                    return None

                account_id = account['id']
                next_index = cur.execute(
                    'SELECT COALESCE(MAX(alias_index), -1) + 1 FROM aliases WHERE account_id = ?',
                    (account_id,),
                ).fetchone()[0]
                main_email = account['email']
                if next_index == 0:
                    alias_email = main_email
                else:
                    at_pos = main_email.index('@')
                    alias_email = f"{main_email[:at_pos]}+{next_index}{main_email[at_pos:]}"

                cur.execute(
                    '''INSERT INTO aliases (
                           account_id, alias_email, alias_index, status,
                           lease_owner, lease_expires_at
                       ) VALUES (?, ?, ?, 'processing', ?, ?)''',
                    (
                        account_id, alias_email, next_index,
                        lease_owner, lease_expires_at,
                    ),
                )
                alias_id = cur.lastrowid
                self.conn.commit()
                return {
                    'id': alias_id,
                    'account_id': account_id,
                    'alias_email': alias_email,
                    'alias_index': next_index,
                    'status': 'processing',
                    'sso_value': '',
                    'error_reason': '',
                    'retry_count': 0,
                    'lease_owner': lease_owner,
                    'lease_expires_at': lease_expires_at,
                    'client_id': account['client_id'],
                    'refresh_token': account['refresh_token'],
                    'main_email': main_email,
                    'account_max_aliases': account['max_aliases'],
                }
            except Exception:
                self.conn.rollback()
                raise

    def heartbeat_alias_lease(self, alias_id, lease_owner,
                              lease_seconds=DEFAULT_LEASE_SECONDS):
        with self._write_lock:
            cur = self.conn.execute(
                '''UPDATE aliases SET lease_expires_at=?
                   WHERE id=? AND status='processing' AND lease_owner=?''',
                (self._lease_expiry(lease_seconds), alias_id, lease_owner),
            )
            self.conn.commit()
            return cur.rowcount == 1

    def release_alias_claim(self, alias_id, lease_owner):
        with self._write_lock:
            cur = self.conn.execute(
                '''UPDATE aliases SET status='ready', lease_owner='', lease_expires_at=NULL
                   WHERE id=? AND status='processing' AND lease_owner=?''',
                (alias_id, lease_owner),
            )
            self.conn.commit()
            return cur.rowcount == 1

    def abort_registration_attempt(self, reg_id, alias_id, lease_owner,
                                   error, duration):
        """Record an upstream-wide abort without consuming the claimed alias."""
        with self._write_lock:
            cur = self.conn.cursor()
            try:
                cur.execute('BEGIN IMMEDIATE')
                cur.execute(
                    '''UPDATE registrations SET status='failed', error_message=?,
                       duration_seconds=? WHERE id=?''',
                    (error, duration, reg_id),
                )
                cur.execute(
                    '''UPDATE aliases SET status='ready', error_reason='',
                       failure_category='', used_at=NULL, completed_at=NULL,
                       lease_owner='', lease_expires_at=NULL
                       WHERE id=? AND status='processing' AND lease_owner=?''',
                    (alias_id, lease_owner),
                )
                released = cur.rowcount == 1
                self.conn.commit()
                return released
            except Exception:
                self.conn.rollback()
                raise

    def get_next_alias(self, max_retries):
        with self._write_lock:
            cur = self.conn.cursor()
            # Step 1: prefer existing ready aliases on non-disabled accounts
            row = cur.execute(
                '''SELECT al.*, a.client_id, a.refresh_token, a.email AS main_email,
                          a.max_aliases AS account_max_aliases
                   FROM aliases al
                   JOIN accounts a ON al.account_id = a.id
                   WHERE al.status = 'ready'
                     AND al.retry_count < ?
                     AND a.status = 'ready'
                   ORDER BY al.account_id ASC, al.alias_index ASC
                   LIMIT 1''',
                (max_retries,)
            ).fetchone()
            if row:
                return dict(row)

            # Step 2: find an account that can still generate new aliases.
            # - Need more successful (used) aliases than max_aliases
            # - Cap TOTAL aliases (used+failed+ready) so failures cannot mint forever
            failure_budget = self.ALIAS_FAILURE_BUDGET
            account = cur.execute(
                '''SELECT a.* FROM accounts a
                   WHERE a.status = 'ready'
                     AND (SELECT COUNT(*) FROM aliases
                          WHERE account_id = a.id AND status = 'used') < a.max_aliases
                     AND (SELECT COUNT(*) FROM aliases
                          WHERE account_id = a.id) < (a.max_aliases + ?)
                   ORDER BY a.id ASC
                   LIMIT 1''',
                (failure_budget,)
            ).fetchone()
            if not account:
                return None

            account_id = account['id']
            next_index = cur.execute(
                'SELECT COALESCE(MAX(alias_index), -1) + 1 FROM aliases WHERE account_id = ?',
                (account_id,)
            ).fetchone()[0]

            # Generate alias: index 0 = bare email, index > 0 = plus addressing
            main_email = account['email']
            if next_index == 0:
                alias_email = main_email
            else:
                at_pos = main_email.index('@')
                alias_email = f"{main_email[:at_pos]}+{next_index}{main_email[at_pos:]}"

            cur.execute(
                '''INSERT INTO aliases (account_id, alias_email, alias_index)
                   VALUES (?, ?, ?)''',
                (account_id, alias_email, next_index)
            )
            self.conn.commit()
            alias_id = cur.lastrowid

            return {
                'id': alias_id,
                'account_id': account_id,
                'alias_email': alias_email,
                'alias_index': next_index,
                'status': 'ready',
                'sso_value': '',
                'error_reason': '',
                'retry_count': 0,
                'client_id': account['client_id'],
                'refresh_token': account['refresh_token'],
                'main_email': main_email,
                'account_max_aliases': account['max_aliases'],
            }

    def count_consecutive_mail_failed_aliases(self, account_id: int) -> int:
        """Count recent terminal aliases until success or another failure breaks the streak."""
        with self._write_lock:
            return self._count_consecutive_mail_failed_aliases_locked(
                self.conn.cursor(), account_id,
            )

    def maybe_disable_unusable_account(self, account_id: int, error_msg: str = '') -> bool:
        """Disable account when mailbox is clearly unusable or alias budget exhausted.

        Returns True if the account was disabled.
        """
        with self._write_lock:
            cur = self.conn.cursor()
            try:
                cur.execute('BEGIN IMMEDIATE')
                disabled, reason, account = self._maybe_disable_unusable_account_locked(
                    cur, account_id,
                )
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        if not disabled:
            return False
        logger.warning(
            'Account %s disabled: %s (last_error=%s)',
            account['email'] if account else account_id,
            reason,
            (error_msg or '')[:120],
        )
        return True

    def create_alias(self, account_id, alias_email, alias_index):
        with self._write_lock:
            cur = self.conn.cursor()
            cur.execute(
                'INSERT INTO aliases (account_id, alias_email, alias_index) VALUES (?, ?, ?)',
                (account_id, alias_email, alias_index)
            )
            self.conn.commit()
            return cur.lastrowid

    def update_alias_status(self, alias_id, status, sso='', error=''):
        with self._write_lock:
            terminal = status in ('used', 'failed')
            completed_at = datetime.now().isoformat() if terminal else None
            used_at = completed_at if status == 'used' else None
            failure_category = classify_failure(error) if status == 'failed' else ''
            self.conn.execute(
                '''UPDATE aliases SET status=?, sso_value=?, error_reason=?,
                   failure_category=?, used_at=?, completed_at=?,
                   lease_owner='', lease_expires_at=NULL
                   WHERE id=?''',
                (
                    status, sso, error, failure_category,
                    used_at, completed_at, alias_id,
                )
            )
            self.conn.commit()

    def increment_alias_retry(self, alias_id):
        with self._write_lock:
            self.conn.execute(
                'UPDATE aliases SET retry_count = retry_count + 1 WHERE id = ?',
                (alias_id,)
            )
            self.conn.commit()

    def reset_aliases(self, account_id):
        with self._write_lock:
            self.conn.execute(
                """UPDATE aliases SET status='ready', sso_value='', error_reason='',
                   failure_category='', retry_count=0, used_at=NULL, completed_at=NULL,
                   lease_owner='', lease_expires_at=NULL
                   WHERE account_id=?""",
                (account_id,)
            )
            self.conn.commit()

    def check_account_aliases_full(self, account_id):
        """Account is done when successfully used aliases reach max_aliases."""
        row = self.conn.execute(
            '''SELECT a.max_aliases,
                      (SELECT COUNT(*) FROM aliases WHERE account_id = a.id AND status = 'used') AS used_cnt
               FROM accounts a WHERE a.id = ?''',
            (account_id,)
        ).fetchone()
        if row and row['used_cnt'] >= row['max_aliases']:
            return True
        return False

    # ── Registrations CRUD ─────────────────────────────────────

    def create_registration(self, alias_id, email, password, round_number,
                            lease_owner=None):
        with self._write_lock:
            cur = self.conn.cursor()
            if lease_owner is not None:
                alias = cur.execute(
                    '''SELECT id FROM aliases
                       WHERE id=? AND status='processing' AND lease_owner=?''',
                    (alias_id, lease_owner),
                ).fetchone()
                if not alias:
                    raise RuntimeError(f'Alias lease lost before registration: {alias_id}')
            cur.execute(
                '''INSERT INTO registrations (alias_id, email, account_password, round_number)
                   VALUES (?, ?, ?, ?)''',
                (alias_id, email, password, round_number)
            )
            self.conn.commit()
            return cur.lastrowid

    def update_registration(self, reg_id, status, sso='', error='', duration=0):
        with self._write_lock:
            self.conn.execute(
                '''UPDATE registrations SET status=?, sso_value=?, error_message=?,
                   duration_seconds=? WHERE id=?''',
                (status, sso, error, duration, reg_id)
            )
            self.conn.commit()

    def complete_registration_success(self, reg_id, alias_id, lease_owner,
                                      sso, duration=0):
        """Atomically persist a successful registration and release its alias lease."""
        with self._write_lock:
            cur = self.conn.cursor()
            try:
                cur.execute('BEGIN IMMEDIATE')
                alias = cur.execute(
                    '''SELECT account_id FROM aliases
                       WHERE id=? AND status='processing' AND lease_owner=?''',
                    (alias_id, lease_owner),
                ).fetchone()
                if not alias:
                    raise RuntimeError(f'Alias lease lost before success commit: {alias_id}')

                completed_at = datetime.now().isoformat()
                cur.execute(
                    '''UPDATE registrations SET status='success', sso_value=?,
                       error_message='', duration_seconds=? WHERE id=?''',
                    (sso, duration, reg_id),
                )
                cur.execute(
                    '''UPDATE aliases SET status='used', sso_value=?, error_reason='',
                       failure_category='', used_at=?, completed_at=?,
                       lease_owner='', lease_expires_at=NULL WHERE id=?''',
                    (sso, completed_at, completed_at, alias_id),
                )

                account_done = bool(cur.execute(
                    '''SELECT 1 FROM accounts a
                       WHERE a.id=? AND
                         (SELECT COUNT(*) FROM aliases
                          WHERE account_id=a.id AND status='used') >= a.max_aliases''',
                    (alias['account_id'],),
                ).fetchone())
                if account_done:
                    cur.execute(
                        "UPDATE accounts SET status='done', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (alias['account_id'],),
                    )
                self.conn.commit()
                return {'account_done': account_done, 'account_id': alias['account_id']}
            except Exception:
                self.conn.rollback()
                raise

    def finish_registration_attempt(self, reg_id, alias_id, lease_owner,
                                    error, duration, max_retries):
        """Atomically fail one attempt, decide retry/terminal state, and release its lease."""
        with self._write_lock:
            cur = self.conn.cursor()
            try:
                cur.execute('BEGIN IMMEDIATE')
                cur.execute(
                    '''UPDATE registrations SET status='failed', error_message=?,
                       duration_seconds=? WHERE id=?''',
                    (error, duration, reg_id),
                )
                alias = cur.execute(
                    '''SELECT account_id, retry_count, status, lease_owner
                       FROM aliases WHERE id=?''',
                    (alias_id,),
                ).fetchone()
                if (
                    not alias
                    or alias['status'] != 'processing'
                    or alias['lease_owner'] != lease_owner
                ):
                    self.conn.commit()
                    return {
                        'lease_lost': True,
                        'retry_count': int(alias['retry_count'] or 0) if alias else 0,
                        'terminal': False,
                        'account_disabled': False,
                        'disable_reason': '',
                    }

                retry_count = int(alias['retry_count'] or 0) + 1
                terminal = retry_count >= max_retries
                if terminal:
                    completed_at = datetime.now().isoformat()
                    cur.execute(
                        '''UPDATE aliases SET status='failed', retry_count=?,
                           error_reason=?, failure_category=?, used_at=NULL,
                           completed_at=?, lease_owner='', lease_expires_at=NULL
                           WHERE id=?''',
                        (
                            retry_count,
                            error,
                            classify_failure(error),
                            completed_at,
                            alias_id,
                        ),
                    )
                    disabled, reason, _ = self._maybe_disable_unusable_account_locked(
                        cur, alias['account_id'],
                    )
                else:
                    cur.execute(
                        '''UPDATE aliases SET status='ready', retry_count=?,
                           error_reason='', failure_category='', used_at=NULL,
                           completed_at=NULL, lease_owner='', lease_expires_at=NULL
                           WHERE id=?''',
                        (retry_count, alias_id),
                    )
                    disabled, reason = False, ''
                self.conn.commit()
                return {
                    'lease_lost': False,
                    'retry_count': retry_count,
                    'terminal': terminal,
                    'account_disabled': disabled,
                    'disable_reason': reason,
                }
            except Exception:
                self.conn.rollback()
                raise

    def get_registrations(self, reg_type='sso'):
        if reg_type == 'sso':
            rows = self.conn.execute(
                '''SELECT id, email, sso_value, created_at
                   FROM registrations WHERE status='success' AND sso_value != ''
                   ORDER BY created_at DESC'''
            ).fetchall()
        else:
            rows = self.conn.execute(
                '''SELECT id, email, account_password, created_at
                   FROM registrations WHERE status='success' AND account_password != ''
                   ORDER BY created_at DESC'''
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_registrations(self, ids=None, reg_type=None):
        with self._write_lock:
            if ids is not None and ids:
                placeholders = ','.join('?' * len(ids))
                self.conn.execute(
                    f'DELETE FROM registrations WHERE id IN ({placeholders})', ids
                )
            elif ids is None:
                if reg_type == 'sso':
                    self.conn.execute("DELETE FROM registrations WHERE sso_value != ''")
                elif reg_type == 'accounts':
                    self.conn.execute("DELETE FROM registrations WHERE account_password != ''")
                else:
                    self.conn.execute('DELETE FROM registrations')
            self.conn.commit()

    def get_registration_stats(self):
        return self.get_account_stats()

    def get_pending_registrations(self):
        rows = self.conn.execute(
            "SELECT * FROM registrations WHERE status='pending'"
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Settings CRUD ──────────────────────────────────────────

    def get_settings(self):
        rows = self.conn.execute('SELECT key, value FROM settings').fetchall()
        return {r['key']: r['value'] for r in rows}

    def update_settings(self, settings):
        with self._write_lock:
            for key, value in settings.items():
                self.conn.execute(
                    '''INSERT INTO settings (key, value, updated_at)
                       VALUES (?, ?, CURRENT_TIMESTAMP)
                       ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP''',
                    (key, str(value))
                )
            self.conn.commit()

    def reset_settings(self):
        with self._write_lock:
            self._remove_deprecated_settings(self.conn)
            for key, value in DEFAULT_SETTINGS.items():
                self.conn.execute(
                    '''INSERT INTO settings (key, value, updated_at)
                       VALUES (?, ?, CURRENT_TIMESTAMP)
                       ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP''',
                    (key, value)
                )
            self.conn.commit()

    # ── Recovery ───────────────────────────────────────────────

    def recover_expired_alias_leases(self, max_retries, now_iso=None):
        with self._write_lock:
            cur = self.conn.cursor()
            try:
                cur.execute('BEGIN IMMEDIATE')
                recovered = self._recover_expired_alias_leases_locked(
                    cur, max_retries, now_iso=now_iso,
                )
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        if recovered:
            logger.info('Recovered %s expired alias lease(s)', len(recovered))
        return recovered

    def recover_stale(self, timeout_seconds):
        recovery_error = 'Registration interrupted: stale pending record recovered on startup'
        cutoff_modifier = f'-{max(0, int(timeout_seconds))} seconds'
        with self._write_lock:
            cur = self.conn.cursor()
            try:
                cur.execute('BEGIN IMMEDIATE')
                expired = self._recover_expired_alias_leases_locked(
                    cur, 0,
                )
                # Let SQLite compare timestamps in UTC. CURRENT_TIMESTAMP is UTC.
                stale = cur.execute(
                    '''SELECT r.id, r.alias_id
                       FROM registrations r
                       WHERE r.status='pending'
                         AND julianday(r.created_at) < julianday('now', ?)''',
                    (cutoff_modifier,),
                ).fetchall()
                for row in stale:
                    cur.execute(
                        '''UPDATE registrations SET status='interrupted', error_message=?
                           WHERE id=?''',
                        (recovery_error, row['id']),
                    )
                    if row['alias_id']:
                        cur.execute(
                            '''UPDATE aliases SET status='ready', error_reason='',
                               failure_category='', used_at=NULL, completed_at=NULL,
                               lease_owner='', lease_expires_at=NULL
                               WHERE id=? AND status NOT IN ('used', 'failed')''',
                            (row['alias_id'],),
                        )
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        total = len(expired) + len(stale)
        if total:
            logger.info(
                'Recovered %s expired lease(s) and %s stale registration(s)',
                len(expired), len(stale),
            )
        return total
