"""Durable background delivery for successful registrations."""

import logging
import threading

from core.grok2api_client import Grok2APIChatPermissionError, upload_registered_sso


logger = logging.getLogger('register')


class Grok2APIRetryWorker:
    def __init__(self, db, interval_seconds=60, state_getter=None, status_emitter=None):
        self.db = db
        self.interval_seconds = max(10, int(interval_seconds))
        self._state_getter = state_getter
        self._status_emitter = status_emitter
        self._stop = threading.Event()
        self._thread = None

    def set_hooks(self, state_getter=None, status_emitter=None):
        """Wire live dashboard hooks after app modules finish initializing."""
        if state_getter is not None:
            self._state_getter = state_getter
        if status_emitter is not None:
            self._status_emitter = status_emitter

    def _current_state(self):
        getter = self._state_getter
        if not getter:
            return None
        try:
            return getter()
        except Exception:
            return None

    def _record_and_emit(self, reg_id, upload_result=None, error=None):
        state = self._current_state()
        if state is not None:
            try:
                state.record_chat_probe_from_upload(
                    upload_result=upload_result,
                    error=error,
                    reg_id=reg_id,
                )
            except Exception:
                logger.exception(
                    'Failed to update chat probe stats after durable retry: registration_id=%s',
                    reg_id,
                )
        emitter = self._status_emitter
        if not emitter:
            return
        try:
            if state is not None:
                emitter(state.get_snapshot())
        except Exception:
            logger.exception(
                'Failed to emit status after durable retry: registration_id=%s',
                reg_id,
            )

    def _run_grok2api_once(self, settings):
        if settings.get('grok2api_auto_upload', 'false') != 'true':
            return 0
        records = self.db.claim_grok2api_retries(limit=20)
        completed = 0
        for record in records:
            reg_id = record['id']
            try:
                result = upload_registered_sso(
                    settings,
                    record['sso_value'],
                    email=record['email'],
                    only='grok2api',
                )
            except Exception as exc:
                if isinstance(exc, Grok2APIChatPermissionError):
                    self.db.finish_grok2api_probe(reg_id, exc.probe)
                else:
                    self.db.finish_grok2api_upload(reg_id, False, exc)
                self._record_and_emit(reg_id, error=exc)
                logger.warning(
                    'grok2api durable retry failed: registration_id=%s error=%s',
                    reg_id, exc,
                )
            else:
                if isinstance(result, dict) and result.get('grok2api_probe_denied'):
                    self.db.finish_grok2api_probe(
                        reg_id, result['grok2api_probe_denied'],
                    )
                    self._record_and_emit(reg_id, upload_result=result)
                elif isinstance(result, dict) and result.get('grok2api_error'):
                    self.db.finish_grok2api_upload(reg_id, False, result['grok2api_error'])
                    self._record_and_emit(reg_id, upload_result=result)
                    logger.warning(
                        'grok2api durable retry soft-failed: registration_id=%s error=%s',
                        reg_id, result['grok2api_error'],
                    )
                else:
                    self.db.finish_grok2api_upload(reg_id, True)
                    completed += 1
                    self._record_and_emit(reg_id, upload_result=result)
                    logger.info(
                        'grok2api durable retry completed: registration_id=%s',
                        reg_id,
                    )
        return completed

    def _run_sub2api_once(self, settings):
        if settings.get('sub2api_auto_upload', 'false') != 'true':
            return 0
        records = self.db.claim_sub2api_retries(limit=20)
        completed = 0
        for record in records:
            reg_id = record['id']
            try:
                result = upload_registered_sso(
                    settings,
                    record['sso_value'],
                    email=record['email'],
                    only='sub2api',
                )
            except Exception as exc:
                self.db.finish_sub2api_upload(reg_id, False, exc)
                logger.warning(
                    'sub2api durable retry failed: registration_id=%s error=%s',
                    reg_id, exc,
                )
            else:
                if isinstance(result, dict) and result.get('sub2api_error'):
                    self.db.finish_sub2api_upload(reg_id, False, result['sub2api_error'])
                    logger.warning(
                        'sub2api durable retry soft-failed: registration_id=%s error=%s',
                        reg_id, result['sub2api_error'],
                    )
                else:
                    self.db.finish_sub2api_upload(reg_id, True)
                    completed += 1
                    logger.info(
                        'sub2api durable retry completed: registration_id=%s',
                        reg_id,
                    )
        return completed

    def run_once(self):
        settings = self.db.get_settings()
        completed = 0
        completed += self._run_grok2api_once(settings)
        completed += self._run_sub2api_once(settings)
        return completed

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name='delivery-retry',
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._thread = None

    def _run(self):
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as exc:
                logger.warning('delivery durable retry worker failed: %s', exc)
            self._stop.wait(self.interval_seconds)
