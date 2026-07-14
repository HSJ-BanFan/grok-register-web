import sqlite3
import threading
import logging
from datetime import datetime, timedelta
from config import DB_PATH

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS = {
    'max_aliases_per_account': '5',
    'max_code_retries': '3',
    'max_confirm_retries': '3',
    'max_retries_per_alias': '3',
    'registration_timeout': '300',
    'browser_headless': 'false',
    'turnstile_auto': 'true',
    'random_name_enabled': 'true',
    'email_provider': 'hotmail',
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
        self._write_lock = threading.Lock()
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
                    retry_count INTEGER DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    used_at DATETIME
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
            for key, value in DEFAULT_SETTINGS.items():
                cur.execute(
                    'INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)',
                    (key, value)
                )
            self.conn.commit()
            logger.info("Database initialized successfully")

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
                "UPDATE aliases SET status='ready', sso_value='', error_reason='', retry_count=0, used_at=NULL WHERE account_id=?",
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

    def get_next_alias(self, max_retries):
        with self._write_lock:
            cur = self.conn.cursor()
            # Step 1: prefer existing ready aliases
            row = cur.execute(
                '''SELECT al.*, a.client_id, a.refresh_token, a.email AS main_email,
                          a.max_aliases AS account_max_aliases
                   FROM aliases al
                   JOIN accounts a ON al.account_id = a.id
                   WHERE al.status = 'ready' AND al.retry_count < ?
                   ORDER BY al.account_id ASC, al.alias_index ASC
                   LIMIT 1''',
                (max_retries,)
            ).fetchone()
            if row:
                return dict(row)

            # Step 2: find an account that can still generate new aliases
            # Allow creating replacement aliases for failed ones: only count successfully used aliases
            account = cur.execute(
                '''SELECT a.* FROM accounts a
                   WHERE a.status = 'ready'
                     AND (SELECT COUNT(*) FROM aliases WHERE account_id = a.id AND status = 'used') < a.max_aliases
                   ORDER BY a.id ASC
                   LIMIT 1'''
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
            used_at = datetime.now().isoformat() if status == 'used' else None
            self.conn.execute(
                '''UPDATE aliases SET status=?, sso_value=?, error_reason=?, used_at=?
                   WHERE id=?''',
                (status, sso, error, used_at, alias_id)
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
                "UPDATE aliases SET status='ready', sso_value='', error_reason='', retry_count=0, used_at=NULL WHERE account_id=?",
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

    def create_registration(self, alias_id, email, password, round_number):
        with self._write_lock:
            cur = self.conn.cursor()
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
            for key, value in DEFAULT_SETTINGS.items():
                self.conn.execute(
                    '''INSERT INTO settings (key, value, updated_at)
                       VALUES (?, ?, CURRENT_TIMESTAMP)
                       ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP''',
                    (key, value)
                )
            self.conn.commit()

    # ── Recovery ───────────────────────────────────────────────

    def recover_stale(self, timeout_seconds):
        with self._write_lock:
            cutoff = (datetime.now() - timedelta(seconds=timeout_seconds)).isoformat()
            stale = self.conn.execute(
                "SELECT id, alias_id FROM registrations WHERE status='pending' AND created_at < ?",
                (cutoff,)
            ).fetchall()
            if not stale:
                return
            settings = self.get_settings()
            max_retries = int(settings.get('max_retries_per_alias', DEFAULT_SETTINGS['max_retries_per_alias']))
            for row in stale:
                self.conn.execute(
                    "UPDATE registrations SET status='failed', error_message='Timeout on startup recovery' WHERE id=?",
                    (row['id'],)
                )
                if row['alias_id']:
                    self.conn.execute(
                        'UPDATE aliases SET retry_count = retry_count + 1 WHERE id = ?',
                        (row['alias_id'],)
                    )
                    alias = self.conn.execute(
                        'SELECT retry_count FROM aliases WHERE id = ?', (row['alias_id'],)
                    ).fetchone()
                    if alias and alias['retry_count'] < max_retries:
                        self.conn.execute(
                            "UPDATE aliases SET status='ready' WHERE id=?",
                            (row['alias_id'],)
                        )
            self.conn.commit()
            logger.info(f"Recovered {len(stale)} stale registration(s)")
