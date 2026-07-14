import threading
import logging
from flask import Blueprint, request, jsonify

register_bp = Blueprint('register', __name__)

_register_lock = threading.Lock()
_register_thread = None
_engine = None
_state = None
_browser_mgr = None
_email_mgr = None
_socketio = None
_db = None
_mode = None

logger = logging.getLogger('register')


def init_register_api(db, browser_mgr, email_mgr, socketio):
    global _browser_mgr, _email_mgr, _socketio, _db
    _db = db
    _browser_mgr = browser_mgr
    _email_mgr = email_mgr
    _socketio = socketio

    @register_bp.route('/api/register/start', methods=['POST'])
    def start():
        global _register_thread, _engine, _state, _mode
        if not _register_lock.acquire(blocking=False):
            return jsonify({'success': False, 'data': None, 'message': 'Registration task is already running', 'code': 'ALREADY_RUNNING'}), 409

        try:
            data = request.get_json() or {}
            max_rounds = int(data.get('max_rounds', 0))
            max_retries = int(data.get('max_retries', 3))
        except (ValueError, TypeError) as e:
            _register_lock.release()
            return jsonify({'success': False, 'data': None, 'message': f'Invalid parameter: {e}', 'code': 'INVALID_PARAMS'}), 400

        # Sync max_retries to settings
        _db.update_settings({'max_retries_per_alias': str(max_retries)})

        from core.register import RegistrationState, RegistrationEngine
        _state = RegistrationState()
        _engine = RegistrationEngine(_db, _browser_mgr, _email_mgr, _socketio, _state)
        _mode = 'register'

        def _run_and_release():
            try:
                _engine.run(max_rounds=max_rounds, max_retries=max_retries)
            except Exception as e:
                logger.error(f"Registration thread error: {e}")
            finally:
                _register_lock.release()

        _register_thread = threading.Thread(target=_run_and_release, daemon=True)
        _register_thread.start()
        return jsonify({'success': True, 'data': {'max_rounds': max_rounds, 'max_retries': max_retries}, 'message': 'Registration started'})

    @register_bp.route('/api/register/reactivate', methods=['POST'])
    def reactivate():
        """Batch re-activate historical success SSO accounts (TOS + birth + CF context)."""
        global _register_thread, _engine, _state, _mode
        if not _register_lock.acquire(blocking=False):
            return jsonify({
                'success': False,
                'data': None,
                'message': 'Another task is already running',
                'code': 'ALREADY_RUNNING',
            }), 409

        try:
            data = request.get_json() or {}
            limit = int(data.get('limit', 0) or 0)
            ids = data.get('ids') or None
            if ids is not None:
                ids = [int(item) for item in ids]
        except (ValueError, TypeError) as e:
            _register_lock.release()
            return jsonify({
                'success': False,
                'data': None,
                'message': f'Invalid parameter: {e}',
                'code': 'INVALID_PARAMS',
            }), 400

        from core.register import RegistrationState
        from core.batch_activation import BatchActivationEngine

        _state = RegistrationState()
        _engine = BatchActivationEngine(_db, _browser_mgr, _socketio, _state)
        _mode = 'reactivate'

        def _run_and_release():
            try:
                _engine.run(limit=limit, ids=ids)
            except Exception as e:
                logger.error(f'Batch reactivation thread error: {e}')
            finally:
                _register_lock.release()

        _register_thread = threading.Thread(target=_run_and_release, daemon=True)
        _register_thread.start()
        return jsonify({
            'success': True,
            'data': {'limit': limit, 'ids': ids, 'mode': 'reactivate'},
            'message': 'Batch Web reactivation started',
        })

    @register_bp.route('/api/register/stop', methods=['POST'])
    def stop():
        if _state:
            _state.stop()
            return jsonify({'success': True, 'data': None, 'message': 'Stop requested'})
        return jsonify({'success': False, 'data': None, 'message': 'No registration running'})

    @register_bp.route('/api/register/pause', methods=['POST'])
    def pause():
        if _state:
            _state.pause()
            return jsonify({'success': True, 'data': None, 'message': 'Paused'})
        return jsonify({'success': False, 'data': None, 'message': 'No registration running'})

    @register_bp.route('/api/register/resume', methods=['POST'])
    def resume():
        if _state:
            _state.resume()
            return jsonify({'success': True, 'data': None, 'message': 'Resumed'})
        return jsonify({'success': False, 'data': None, 'message': 'No registration running'})

    @register_bp.route('/api/register/status', methods=['GET'])
    def status():
        if _state:
            snapshot = _state.get_snapshot()
            snapshot['mode'] = _mode or 'register'
            return jsonify({'success': True, 'data': snapshot, 'message': ''})
        return jsonify({
            'success': True,
            'data': {
                'status': 'stopped',
                'current_round': 0,
                'current_email': '',
                'completed': 0,
                'success': 0,
                'failed': 0,
                'mode': None,
            },
            'message': '',
        })

    return register_bp
