import os
import json
from datetime import datetime
from flask import Blueprint, request, jsonify, send_file

results_bp = Blueprint('results', __name__)


def init_results_api(db):
    @results_bp.route('/api/results/sso', methods=['GET'])
    def get_sso():
        rows = db.get_registrations('sso')
        return jsonify({'success': True, 'data': rows, 'message': ''})

    @results_bp.route('/api/results/sso/export', methods=['POST'])
    def export_sso():
        data = request.get_json() or {}
        fmt = data.get('format') or db.get_settings().get('export_format', 'txt')
        export_dir = db.get_settings().get('export_dir', './data')
        os.makedirs(export_dir, exist_ok=True)

        rows = db.get_registrations('sso')
        if fmt == 'json':
            path = os.path.join(export_dir, 'sso.json')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump([{'email': r['email'], 'sso': r['sso_value'], 'created_at': r['created_at']} for r in rows], f, indent=2, ensure_ascii=False)
        else:
            path = os.path.join(export_dir, 'sso.txt')
            with open(path, 'w', encoding='utf-8') as f:
                for r in rows:
                    f.write(r['sso_value'] + '\n')

        return send_file(os.path.abspath(path), as_attachment=True)

    @results_bp.route('/api/results/sso/<int:reg_id>', methods=['DELETE'])
    def delete_sso(reg_id):
        db.delete_registrations([reg_id])
        return jsonify({'success': True, 'data': None, 'message': 'Deleted'})

    @results_bp.route('/api/results/sso', methods=['DELETE'])
    def clear_sso():
        db.delete_registrations(reg_type='sso')
        return jsonify({'success': True, 'data': None, 'message': 'All SSO records cleared'})

    @results_bp.route('/api/results/accounts', methods=['GET'])
    def get_accounts():
        rows = db.get_registrations('accounts')
        return jsonify({'success': True, 'data': rows, 'message': ''})

    @results_bp.route('/api/results/accounts/export', methods=['POST'])
    def export_accounts():
        data = request.get_json() or {}
        fmt = data.get('format') or db.get_settings().get('export_format', 'txt')
        export_dir = db.get_settings().get('export_dir', './data')
        os.makedirs(export_dir, exist_ok=True)

        rows = db.get_registrations('accounts')
        if fmt == 'json':
            path = os.path.join(export_dir, 'registered_accounts.json')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump([{'email': r['email'], 'password': r['account_password'], 'created_at': r['created_at']} for r in rows], f, indent=2, ensure_ascii=False)
        else:
            path = os.path.join(export_dir, 'registered_accounts.txt')
            with open(path, 'w', encoding='utf-8') as f:
                for r in rows:
                    f.write(f"{r['email']}----{r['account_password']}\n")

        return send_file(os.path.abspath(path), as_attachment=True)

    @results_bp.route('/api/results/accounts/<int:reg_id>', methods=['DELETE'])
    def delete_account(reg_id):
        db.delete_registrations([reg_id])
        return jsonify({'success': True, 'data': None, 'message': 'Deleted'})

    @results_bp.route('/api/results/accounts', methods=['DELETE'])
    def clear_accounts():
        db.delete_registrations(reg_type='accounts')
        return jsonify({'success': True, 'data': None, 'message': 'All account records cleared'})

    return results_bp
