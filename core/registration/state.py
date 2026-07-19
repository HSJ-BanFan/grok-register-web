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


class ExistingAccountError(RuntimeError):
    """xAI reports that the email already belongs to an existing account."""


class DuplicateSSOError(RuntimeError):
    """The completed flow returned an SSO identity already seen locally."""


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
        # Session-scoped chat probe outcomes (reset when a new RegistrationState is created).
        self._chat_probe_passed = 0
        self._chat_probe_denied = 0
        self._chat_probe_failed = 0
        self._chat_probe_skipped = 0
        self._active_workers = {}
        self._provisional_workers = set()
        self._next_round_at = None
        self._next_round_in = 0

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

    def reserve_worker_round(self, worker_id, alias, max_rounds=0):
        """Reserve a target slot and publish the worker atomically.

        Retries that do not reach a terminal success/failure do not consume a
        target slot, while concurrent workers cannot reserve beyond max_rounds.
        """
        with self._lock:
            has_provisional_slot = worker_id in self._provisional_workers
            if (
                not has_provisional_slot
                and max_rounds > 0
                and self._completed
                + len(self._active_workers)
                + len(self._provisional_workers) >= max_rounds
            ):
                return None
            self._provisional_workers.discard(worker_id)
            self._current_round += 1
            self._active_workers[worker_id] = {
                'worker_id': worker_id,
                'round': self._current_round,
                'email': alias['alias_email'],
                'account_id': alias['account_id'],
                'alias_id': alias['id'],
            }
            self._legacy_current_email = ''
            return self._current_round

    def reserve_worker_capacity(self, worker_id, max_rounds=0):
        """Atomically reserve capacity before an alias may be provisioned."""
        with self._lock:
            if worker_id in self._active_workers or worker_id in self._provisional_workers:
                return True
            if (
                max_rounds > 0
                and self._completed
                + len(self._active_workers)
                + len(self._provisional_workers) >= max_rounds
            ):
                return False
            self._provisional_workers.add(worker_id)
            return True

    def release_worker_capacity(self, worker_id):
        """Release an unused provisional slot after alias acquisition fails."""
        with self._lock:
            self._provisional_workers.discard(worker_id)

    def has_worker_round_capacity(self, max_rounds=0):
        """Check target capacity before a worker provisions or claims an alias."""
        with self._lock:
            return not (
                max_rounds > 0
                and self._completed
                + len(self._active_workers)
                + len(self._provisional_workers) >= max_rounds
            )

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
            self._provisional_workers.discard(worker_id)

    def record_success(self, worker_id=None):
        with self._lock:
            self._success += 1
            self._completed += 1
            if worker_id:
                self._active_workers.pop(worker_id, None)
                self._provisional_workers.discard(worker_id)

    def record_failure(self, worker_id=None):
        with self._lock:
            self._failed += 1
            self._completed += 1
            if worker_id:
                self._active_workers.pop(worker_id, None)
                self._provisional_workers.discard(worker_id)

    def record_chat_probe(self, outcome):
        """Count one pre-upload chat probe outcome for the live dashboard.

        Outcomes:
          - passed: HTTP 2xx from cli-chat-proxy
          - denied: 401/403 / permission denied (no chat entitlement)
          - failed: probe ran but failed for other reasons (429 exhausted, network, …)
          - skipped: probe disabled or not attempted
        """
        key = str(outcome or '').strip().lower()
        with self._lock:
            if key == 'passed':
                self._chat_probe_passed += 1
            elif key == 'denied':
                self._chat_probe_denied += 1
            elif key == 'failed':
                self._chat_probe_failed += 1
            elif key == 'skipped':
                self._chat_probe_skipped += 1

    def record_chat_probe_from_upload(self, upload_result=None, error=None):
        """Derive probe stats from upload_registered_sso result or raised error."""
        try:
            from core.grok2api_client import Grok2APIChatPermissionError
        except Exception:  # pragma: no cover - import cycle guard
            Grok2APIChatPermissionError = type('Grok2APIChatPermissionError', (Exception,), {})

        if error is not None and isinstance(error, Grok2APIChatPermissionError):
            self.record_chat_probe('denied')
            return
        if error is not None:
            # Upload failed after/around probe for a non-permission reason.
            detail = str(error).lower()
            if 'chat probe' in detail:
                self.record_chat_probe('failed')
            return
        if not isinstance(upload_result, dict):
            return
        if upload_result.get('grok2api_probe_denied'):
            self.record_chat_probe('denied')
            return
        probe = {}
        grok2 = upload_result.get('grok2api')
        if isinstance(grok2, dict) and isinstance(grok2.get('probe'), dict):
            probe = grok2['probe']
        elif isinstance(upload_result.get('probe'), dict):
            probe = upload_result['probe']
        if not probe:
            return
        if probe.get('skipped'):
            self.record_chat_probe('skipped')
        elif probe.get('ok'):
            self.record_chat_probe('passed')
        else:
            self.record_chat_probe('failed')

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

    def wait_for_next_round(self, seconds, on_tick=None):
        """Wait between rounds while remaining responsive to pause and stop."""
        remaining = max(0.0, float(seconds or 0))
        if remaining <= 0:
            return not self.should_stop()

        logger.info('Waiting %.0fs before starting the next registration', remaining)
        last_reported = None
        while remaining > 0 and not self.should_stop():
            if not self._pause_event.is_set():
                self.check_pause()
                if self.should_stop():
                    break

            step = min(1.0, remaining)
            with self._lock:
                self._status = 'waiting'
                self._next_round_in = max(1, int(remaining + 0.999))
                self._next_round_at = time.time() + remaining
                report = self._next_round_in
            if report != last_reported and on_tick:
                on_tick()
                last_reported = report
            started = time.monotonic()
            time.sleep(step)
            if self._pause_event.is_set():
                remaining -= time.monotonic() - started

        with self._lock:
            self._next_round_at = None
            self._next_round_in = 0
            if not self._stop_flag:
                self._status = 'running'
        if on_tick:
            on_tick()
        return not self.should_stop()

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
                'chat_probe_passed': self._chat_probe_passed,
                'chat_probe_denied': self._chat_probe_denied,
                'chat_probe_failed': self._chat_probe_failed,
                'chat_probe_skipped': self._chat_probe_skipped,
                'next_round_at': self._next_round_at,
                'next_round_in': self._next_round_in,
            }
