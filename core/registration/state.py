import logging
import threading
import time
from contextlib import contextmanager


logger = logging.getLogger('register')

_EMAIL_REQUEST_LOCK = threading.Lock()
_EMAIL_REQUEST_LAST_AT = 0.0
EMAIL_REQUEST_MIN_INTERVAL = 12.0


@contextmanager
def email_request_slot(min_interval=EMAIL_REQUEST_MIN_INTERVAL):
    """Serialize xAI send-code requests across concurrent browser workers."""
    global _EMAIL_REQUEST_LAST_AT
    with _EMAIL_REQUEST_LOCK:
        wait_for = max(
            0.0,
            _EMAIL_REQUEST_LAST_AT + float(min_interval) - time.monotonic(),
        )
        if wait_for:
            logger.info(
                'Waiting %.1fs before next xAI verification-code request',
                wait_for,
            )
            time.sleep(wait_for)
        _EMAIL_REQUEST_LAST_AT = time.monotonic()
        yield


def submit_is_in_flight(ui_state):
    """A disabled post-click primary button means the form still owns the request."""
    state = ui_state or {}
    return bool(state.get('loading') or state.get('primaryDisabled'))


def is_xai_permission_denied(error):
    text = str(error or '').lower()
    return 'permission_denied' in text and '403' in text


class VerificationRequestError(RuntimeError):
    """xAI rejected or did not complete the send-code request."""


class RegistrationState:
    def __init__(self):
        self._pause_event = threading.Event()
        self._lock = threading.RLock()
        self._stop_flag = False
        self._pause_event.set()
        self._status = 'stopped'
        self._current_round = 0
        self._legacy_current_email = ''
        self._completed = 0
        self._success = 0
        self._failed = 0
        self._active_workers = {}

    @property
    def status(self):
        with self._lock:
            return self._status

    @status.setter
    def status(self, value):
        with self._lock:
            self._status = value

    @property
    def current_round(self):
        with self._lock:
            return self._current_round

    @current_round.setter
    def current_round(self, value):
        with self._lock:
            self._current_round = value

    @property
    def current_email(self):
        with self._lock:
            if self._active_workers:
                return ', '.join(
                    item['email'] for item in self._active_workers.values()
                )
            return self._legacy_current_email

    @current_email.setter
    def current_email(self, value):
        with self._lock:
            self._legacy_current_email = value or ''

    @property
    def completed(self):
        with self._lock:
            return self._completed

    @completed.setter
    def completed(self, value):
        with self._lock:
            self._completed = value

    @property
    def success(self):
        with self._lock:
            return self._success

    @success.setter
    def success(self, value):
        with self._lock:
            self._success = value

    @property
    def failed(self):
        with self._lock:
            return self._failed

    @failed.setter
    def failed(self, value):
        with self._lock:
            self._failed = value

    def check_pause(self):
        self._pause_event.wait()

    def should_stop(self):
        with self._lock:
            return self._stop_flag

    def reserve_round(self, max_rounds=0):
        with self._lock:
            if max_rounds > 0 and self._current_round >= max_rounds:
                return None
            self._current_round += 1
            return self._current_round

    def set_worker_active(self, worker_id, round_number, alias):
        with self._lock:
            self._active_workers[worker_id] = {
                'worker_id': worker_id,
                'round': round_number,
                'email': alias['alias_email'],
                'account_id': alias['account_id'],
                'alias_id': alias['id'],
            }
            self._legacy_current_email = ''

    def clear_worker(self, worker_id):
        with self._lock:
            self._active_workers.pop(worker_id, None)

    def record_success(self):
        with self._lock:
            self._success += 1
            self._completed += 1

    def record_failure(self):
        with self._lock:
            self._failed += 1
            self._completed += 1

    def pause(self):
        self._pause_event.clear()
        with self._lock:
            self._status = 'paused'
        logger.info('Registration paused')

    def resume(self):
        self._pause_event.set()
        with self._lock:
            self._status = 'running'
        logger.info('Registration resumed')

    def stop(self):
        with self._lock:
            self._stop_flag = True
            self._status = 'stopped'
        self._pause_event.set()
        logger.info('Registration stop requested')

    def get_snapshot(self):
        with self._lock:
            workers = [
                dict(item) for _, item in sorted(self._active_workers.items())
            ]
            current_email = ', '.join(item['email'] for item in workers)
            if not current_email:
                current_email = self._legacy_current_email
            return {
                'status': self._status,
                'current_round': self._current_round,
                'current_email': current_email,
                'active_workers': workers,
                'completed': self._completed,
                'success': self._success,
                'failed': self._failed,
            }
