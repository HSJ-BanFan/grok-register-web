from flask import Blueprint, request, jsonify
from core.database import DEFAULT_SETTINGS
from core.registration.turnstile import probe_turnstile_solver
from services import solver_manager

settings_bp = Blueprint('settings', __name__)


def init_settings_api(db):
    @settings_bp.route('/api/settings', methods=['GET'])
    def get_settings():
        settings = db.get_settings()
        return jsonify({'success': True, 'data': settings, 'message': ''})

    @settings_bp.route('/api/settings', methods=['PUT'])
    def update_settings():
        data = request.get_json() or {}
        if not data:
            return jsonify({'success': False, 'data': None, 'message': 'No settings provided'})

        if data.get('_reset'):
            db.reset_settings()
            return jsonify({'success': True, 'data': db.get_settings(), 'message': 'Settings reset to defaults'})

        valid_keys = set(DEFAULT_SETTINGS.keys())
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        if not filtered:
            return jsonify({'success': False, 'data': None, 'message': 'No valid settings keys provided'})

        try:
            db.update_settings(filtered)
        except ValueError as exc:
            return jsonify({
                'success': False,
                'data': None,
                'message': str(exc),
            }), 400
        return jsonify({'success': True, 'data': db.get_settings(), 'message': 'Settings updated'})

    @settings_bp.route('/api/settings/turnstile-solver/test', methods=['POST'])
    def test_turnstile_solver():
        data = request.get_json(silent=True) or {}
        url = str(data.get('url') or '').strip()
        if not url:
            url = str(
                db.get_settings().get('turnstile_solver_url', '') or ''
            ).strip()
        result = probe_turnstile_solver(url)
        return jsonify({
            'success': True,
            'data': result,
            'message': '',
        })

    @settings_bp.route('/api/settings/turnstile-solver/status', methods=['GET'])
    def turnstile_solver_status():
        settings = db.get_settings()
        url = str(settings.get('turnstile_solver_url', '') or '').strip()
        status = solver_manager.get_status(url or None)
        probe = probe_turnstile_solver(url or solver_manager.DEFAULT_SOLVER_URL)
        status = {
            **status,
            'online': bool(probe.get('online')),
            'probe': probe,
            'auto_start': solver_manager.should_auto_start(settings),
        }
        return jsonify({'success': True, 'data': status, 'message': ''})

    @settings_bp.route('/api/settings/turnstile-solver/start', methods=['POST'])
    def turnstile_solver_start():
        data = request.get_json(silent=True) or {}
        settings = dict(db.get_settings())
        # Allow the unsaved URL from the form to win for this start attempt.
        form_url = str(data.get('url') or '').strip()
        if form_url:
            settings['turnstile_solver_url'] = form_url
        status = solver_manager.start(settings, force=True)
        online = solver_manager.is_running(status.get('url'))
        message = (
            '本地 Solver 已启动'
            if online
            else (status.get('last_error') or status.get('message') or '启动失败')
        )
        return jsonify({
            'success': online,
            'data': {**status, 'online': online},
            'message': message,
        }), (200 if online else 503)

    @settings_bp.route('/api/settings/turnstile-solver/stop', methods=['POST'])
    def turnstile_solver_stop():
        status = solver_manager.stop(kill_orphans=True)
        return jsonify({
            'success': True,
            'data': status,
            'message': '已请求停止本地 Solver',
        })

    @settings_bp.route('/api/settings/turnstile-solver/restart', methods=['POST'])
    def turnstile_solver_restart():
        data = request.get_json(silent=True) or {}
        settings = dict(db.get_settings())
        form_url = str(data.get('url') or '').strip()
        if form_url:
            settings['turnstile_solver_url'] = form_url
        status = solver_manager.restart(settings)
        online = solver_manager.is_running(status.get('url'))
        return jsonify({
            'success': online,
            'data': {**status, 'online': online},
            'message': '本地 Solver 已重启' if online else (
                status.get('last_error') or '重启失败'
            ),
        }), (200 if online else 503)

    return settings_bp
