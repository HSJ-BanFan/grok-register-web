from flask import Blueprint, request, jsonify
from core.database import DEFAULT_SETTINGS

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

        db.update_settings(filtered)
        return jsonify({'success': True, 'data': db.get_settings(), 'message': 'Settings updated'})

    return settings_bp
