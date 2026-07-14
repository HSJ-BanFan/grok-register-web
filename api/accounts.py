from flask import Blueprint, request, jsonify

accounts_bp = Blueprint('accounts', __name__)


def init_accounts_api(db, oauth_mgr):
    @accounts_bp.route('/api/oauth/start', methods=['POST'])
    def oauth_start():
        data = request.get_json() or {}
        client_id = data.get('client_id', '').strip()
        if not client_id:
            return jsonify({'success': False, 'data': None, 'message': 'Client ID is required', 'code': 'MISSING_CLIENT_ID'})
        try:
            auth_url = oauth_mgr.start_authorization(client_id)
            return jsonify({'success': True, 'data': {'auth_url': auth_url}, 'message': ''})
        except Exception as e:
            code = 'PORT_IN_USE' if 'Port' in str(e) or 'port' in str(e) else 'OAUTH_ERROR'
            status = 409 if 'progress' in str(e).lower() else 400
            return jsonify({'success': False, 'data': None, 'message': str(e), 'code': code}), status

    @accounts_bp.route('/api/oauth/status', methods=['GET'])
    def oauth_status():
        status = oauth_mgr.get_status()
        return jsonify({'success': True, 'data': status, 'message': ''})

    @accounts_bp.route('/api/accounts', methods=['GET'])
    def get_accounts():
        status_filter = request.args.get('status', 'all')
        accounts = db.get_accounts(status_filter)
        return jsonify({'success': True, 'data': accounts, 'message': ''})

    @accounts_bp.route('/api/accounts/import', methods=['POST'])
    def import_accounts():
        data = request.get_json() or {}
        lines = data.get('lines', [])
        if not lines:
            return jsonify({'success': False, 'data': None, 'message': 'No account data provided'})

        # 修复 N+1 查询：在循环外查询一次，构建 email 集合
        existing_accounts = db.get_accounts('all')
        existing_emails = {a['email'] for a in existing_accounts}

        results = {'total': 0, 'new': 0, 'updated': 0, 'invalid': 0, 'details': []}
        for line in lines:
            line = line.strip()
            if not line:
                continue
            results['total'] += 1
            parts = line.split('----')
            if len(parts) < 4:
                results['invalid'] += 1
                results['details'].append({'line': line, 'status': 'invalid', 'reason': 'Format error: expected 4 fields separated by ----'})
                continue

            email_addr, password, client_id, refresh_token = parts[0].strip(), parts[1].strip(), parts[2].strip(), parts[3].strip()
            if not all([email_addr, client_id, refresh_token]):
                results['invalid'] += 1
                results['details'].append({'line': line, 'status': 'invalid', 'reason': 'Missing required fields'})
                continue

            is_new = email_addr not in existing_emails
            db.upsert_account(email_addr, password, client_id, refresh_token)
            if is_new:
                results['new'] += 1
                existing_emails.add(email_addr)  # 添加到集合，避免同批次重复
            else:
                results['updated'] += 1
            results['details'].append({'line': email_addr, 'status': 'new' if is_new else 'updated'})

        return jsonify({'success': True, 'data': results, 'message': f"Imported {results['new']} new, {results['updated']} updated, {results['invalid']} invalid"})

    @accounts_bp.route('/api/accounts/<int:account_id>', methods=['DELETE'])
    def delete_account(account_id):
        db.delete_accounts([account_id])
        return jsonify({'success': True, 'data': None, 'message': 'Account deleted'})

    @accounts_bp.route('/api/accounts', methods=['DELETE'])
    def delete_accounts():
        data = request.get_json() or {}
        ids = data.get('ids', [])
        if not ids:
            return jsonify({'success': False, 'data': None, 'message': 'No account IDs provided'})
        db.delete_accounts(ids)
        return jsonify({'success': True, 'data': None, 'message': f'{len(ids)} account(s) deleted'})

    @accounts_bp.route('/api/accounts/<int:account_id>/reset', methods=['POST'])
    def reset_account(account_id):
        db.reset_account(account_id)
        return jsonify({'success': True, 'data': None, 'message': 'Account reset to ready'})

    @accounts_bp.route('/api/accounts/stats', methods=['GET'])
    def account_stats():
        stats = db.get_account_stats()
        return jsonify({'success': True, 'data': stats, 'message': ''})

    return accounts_bp
