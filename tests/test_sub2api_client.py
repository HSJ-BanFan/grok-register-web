"""Tests for core.sub2api_client — SSO delivery into Sub2API admin endpoint."""
from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from core.sub2api_client import (
    Sub2APIError,
    export_sso_to_sub2api,
    parse_group_ids,
    test_sub2api_connection as probe_sub2api_connection,
    normalize_base_url,
)


def _ok_response(payload=None):
    if payload is None:
        payload = {}
    resp = Mock(status_code=200, text='{}')
    resp.ok = True
    resp.json.return_value = payload
    return resp


def _make_session(*, login_payload=None, post_payload=None, get_payload=None):
    """Build a mocked requests.Session for Sub2APIClient.

    login / post / get are returned in the order they are consumed by the client.
    The default payloads are wrapped in the Sub2API ``{code, message, data}``
    envelope so ``_unwrap_envelope`` strips them correctly.
    """
    session = Mock()
    default_login = {
        'code': 0,
        'message': 'ok',
        'data': {'access_token': 'admin-token', 'expires_in': 3600},
    }
    default_post = {
        'code': 0,
        'message': 'ok',
        'data': {'created': [], 'failed': []},
    }
    default_get = []
    session.post.return_value = _mock_resp(login_payload if login_payload is not None else default_login)
    post_resp = _mock_resp(post_payload if post_payload is not None else default_post)
    get_resp = _mock_resp(get_payload if get_payload is not None else default_get)
    # Sub2APIClient uses session.post for login, then session.request for the rest.
    session.request.side_effect = lambda method, url, **kwargs: (
        post_resp if method == 'POST' else get_resp
    )
    return session


def _mock_resp(payload):
    resp = Mock(status_code=200, text='{}')
    resp.ok = True
    resp.json.return_value = payload
    return resp


class NormalizeBaseUrlTest(unittest.TestCase):
    def test_strips_trailing_slash(self):
        self.assertEqual(normalize_base_url('http://localhost:8080/'), 'http://localhost:8080')

    def test_strips_api_v1_suffix(self):
        self.assertEqual(
            normalize_base_url('http://localhost:8080/api/v1'),
            'http://localhost:8080',
        )
    def test_strips_api_suffix(self):
        self.assertEqual(
            normalize_base_url('http://localhost:8080/api/'),
            'http://localhost:8080',
        )

    def test_keeps_path(self):
        self.assertEqual(
            normalize_base_url('http://localhost:8080/sub2api'),
            'http://localhost:8080/sub2api',
        )

    def test_empty_input(self):
        self.assertEqual(normalize_base_url(''), '')
        self.assertEqual(normalize_base_url('   '), '')


class ParseGroupIdsTest(unittest.TestCase):
    def test_csv(self):
        self.assertEqual(parse_group_ids('1,2,3'), [1, 2, 3])

    def test_json_like(self):
        self.assertEqual(parse_group_ids('[1, 2, 3]'), [1, 2, 3])

    def test_space_separated(self):
        self.assertEqual(parse_group_ids('1 2 3'), [1, 2, 3])

    def test_empty_or_zero_filtered(self):
        self.assertEqual(parse_group_ids(''), [])
        self.assertEqual(parse_group_ids('0,0,-1,2'), [2])
        self.assertEqual(parse_group_ids(None), [])

    def test_list_or_int(self):
        self.assertEqual(parse_group_ids([1, 2, '3', 'foo']), [1, 2, 3])
        self.assertEqual(parse_group_ids(4), [4])
        self.assertEqual(parse_group_ids(0), [])


class ExportSSOTest(unittest.TestCase):
    def _settings(self, **overrides):
        base = {
            'sub2api_auto_upload': 'true',
            'sub2api_url': 'http://localhost:8080',
            'sub2api_email': 'admin@example.com',
            'sub2api_password': 'secret',
            'sub2api_api_token': '',
            'sub2api_group_ids': '2',
            'sub2api_proxy_id': '',
            'sub2api_concurrency': '1',
            'sub2api_priority': '1',
            'sub2api_name_prefix': '',
            'sub2api_auto_pause_on_expired': 'true',
            'sub2api_timeout_sec': '180',
        }
        base.update(overrides)
        return base

    def test_disabled_raises(self):
        with self.assertRaisesRegex(Sub2APIError, 'disabled'):
            export_sso_to_sub2api(self._settings(sub2api_auto_upload='false'), 'sso')

    def test_url_or_auth_missing_raises(self):
        with self.assertRaisesRegex(Sub2APIError, 'URL is empty'):
            export_sso_to_sub2api(self._settings(sub2api_url=''), 'sso')
        with self.assertRaisesRegex(Sub2APIError, 'incomplete'):
            export_sso_to_sub2api(
                self._settings(sub2api_email='', sub2api_password=''),
                'sso',
            )

    def test_login_and_import_ok(self):
        session = _make_session(
            post_payload={
                'created': [
                    {
                        'name': 'user@example.com',
                        'email': 'user@example.com',
                        'account': {'id': 17},
                    }
                ],
                'failed': [],
            },
        )
        with patch('core.sub2api_client.requests.Session', return_value=session):
            result = export_sso_to_sub2api(
                self._settings(),
                'sso-cookie-value',
                email='user@example.com',
            )

        self.assertTrue(result['ok'])
        self.assertEqual(result['account_id'], 17)
        self.assertEqual(result['email'], 'user@example.com')

        # Verify the request body shape sent to Sub2API.
        post_call = next(
            call for call in session.request.call_args_list
            if call.args and call.args[0] == 'POST'
        )
        body = post_call.kwargs['json']
        self.assertEqual(body['sso_tokens'], ['sso-cookie-value'])
        self.assertEqual(body['concurrency'], 1)
        self.assertEqual(body['priority'], 1)
        self.assertTrue(body['auto_pause_on_expired'])
        self.assertEqual(body['group_ids'], [2])
        self.assertEqual(body['name'], 'user@example.com')
        # No proxy_id when not configured.
        self.assertNotIn('proxy_id', body)

    def test_api_token_skips_login(self):
        settings = self._settings(
            sub2api_api_token='long-lived-token',
            sub2api_email='',
            sub2api_password='',
        )
        session = _make_session(
            post_payload={
                'created': [{'name': 'u@e.com', 'email': 'u@e.com', 'account': {'id': 1}}],
                'failed': [],
            },
        )
        with patch('core.sub2api_client.requests.Session', return_value=session):
            result = export_sso_to_sub2api(settings, 'sso')

        self.assertTrue(result['ok'])
        # When api_token is set, login should not have been called.
        session.post.assert_not_called()

    def test_partial_failure_raises(self):
        session = _make_session(
            post_payload={
                'created': [],
                'failed': [{'error': 'invalid_sso_token'}],
            },
        )
        with patch('core.sub2api_client.requests.Session', return_value=session):
            with self.assertRaisesRegex(Sub2APIError, 'invalid_sso_token'):
                export_sso_to_sub2api(self._settings(), 'bad-sso')

    def test_empty_created_raises(self):
        session = _make_session(post_payload={'created': [], 'failed': []})
        with patch('core.sub2api_client.requests.Session', return_value=session):
            with self.assertRaisesRegex(Sub2APIError, 'no created accounts'):
                export_sso_to_sub2api(self._settings(), 'sso')

    def test_proxy_id_only_when_positive(self):
        session = _make_session(
            post_payload={
                'created': [{'name': 'u@e.com', 'email': 'u@e.com', 'account': {'id': 1}}],
                'failed': [],
            },
        )
        with patch('core.sub2api_client.requests.Session', return_value=session):
            export_sso_to_sub2api(
                self._settings(sub2api_proxy_id='5'),
                'sso', email='u@e.com',
            )
        post_call = next(
            call for call in session.request.call_args_list
            if call.args and call.args[0] == 'POST'
        )
        self.assertEqual(post_call.kwargs['json']['proxy_id'], 5)

    def test_proxy_id_zero_omits_field(self):
        session = _make_session(
            post_payload={
                'created': [{'name': 'u@e.com', 'email': 'u@e.com', 'account': {'id': 1}}],
                'failed': [],
            },
        )
        with patch('core.sub2api_client.requests.Session', return_value=session):
            export_sso_to_sub2api(
                self._settings(sub2api_proxy_id='0'),
                'sso', email='u@e.com',
            )
        post_call = next(
            call for call in session.request.call_args_list
            if call.args and call.args[0] == 'POST'
        )
        self.assertNotIn('proxy_id', post_call.kwargs['json'])

    def test_uses_email_for_name(self):
        session = _make_session(
            post_payload={
                'created': [{'name': 'user@example.com', 'email': 'user@example.com', 'account': {'id': 9}}],
                'failed': [],
            },
        )
        with patch('core.sub2api_client.requests.Session', return_value=session):
            export_sso_to_sub2api(self._settings(), 'sso', email='user@example.com')
        post_call = next(
            call for call in session.request.call_args_list
            if call.args and call.args[0] == 'POST'
        )
        self.assertEqual(post_call.kwargs['json']['name'], 'user@example.com')

    def test_name_prefix_fallback(self):
        session = _make_session(
            post_payload={
                'created': [{'name': 'pool-x', 'email': '', 'account': {'id': 1}}],
                'failed': [],
            },
        )
        with patch('core.sub2api_client.requests.Session', return_value=session):
            export_sso_to_sub2api(
                self._settings(sub2api_name_prefix='pool-x'),
                'sso',
            )
        post_call = next(
            call for call in session.request.call_args_list
            if call.args and call.args[0] == 'POST'
        )
        self.assertEqual(post_call.kwargs['json']['name'], 'pool-x')


class TestConnectionTest(unittest.TestCase):
    def _settings(self, **overrides):
        base = {
            'sub2api_url': 'http://localhost:8080',
            'sub2api_email': 'admin@example.com',
            'sub2api_password': 'secret',
            'sub2api_api_token': '',
            'sub2api_timeout_sec': '180',
        }
        base.update(overrides)
        return base

    def test_missing_url(self):
        result = probe_sub2api_connection(self._settings(sub2api_url=''))
        self.assertFalse(result['ok'])
        self.assertIn('empty', result['error'])

    def test_missing_auth(self):
        result = probe_sub2api_connection(
            self._settings(sub2api_email='', sub2api_password=''),
        )
        self.assertFalse(result['ok'])
        self.assertIn('provide', result['error'])

    def test_login_ok_and_groups_returned(self):
        session = _make_session(get_payload=[
            {'id': 1, 'name': 'free', 'platform': 'grok'},
            {'id': 2, 'name': 'pro', 'platform': 'grok'},
        ])
        with patch('core.sub2api_client.requests.Session', return_value=session):
            result = probe_sub2api_connection(self._settings())

        self.assertTrue(result['ok'])
        self.assertEqual(result['group_count'], 2)
        self.assertEqual(result['grok_group_count'], 2)
        self.assertEqual(result['auth'], 'login')
        self.assertEqual(result['base_url'], 'http://localhost:8080')

    def test_dict_envelope_with_items(self):
        session = _make_session(get_payload={
            'items': [{'id': 1, 'platform': 'grok'}],
        })
        with patch('core.sub2api_client.requests.Session', return_value=session):
            result = probe_sub2api_connection(self._settings())

        self.assertTrue(result['ok'])
        self.assertEqual(result['group_count'], 1)
        self.assertEqual(result['grok_group_count'], 1)

    def test_http_error_returns_failure(self):
        session = Mock()
        login_resp = Mock(status_code=200, text='{}')
        login_resp.ok = True
        login_resp.json.return_value = {
            'code': 0,
            'message': 'ok',
            'data': {'access_token': 'tok'},
        }
        bad = Mock(status_code=500, text='server error')
        bad.ok = False
        session.post.return_value = login_resp
        session.request.return_value = bad

        with patch('core.sub2api_client.requests.Session', return_value=session):
            result = probe_sub2api_connection(self._settings())

        self.assertFalse(result['ok'])
        self.assertIn('HTTP 500', result['error'])


class TokenCacheTest(unittest.TestCase):
    def test_second_call_uses_cached_token(self):
        """Two consecutive logins should only fire POST once thanks to the cache."""
        from core.sub2api_client import _token_cache, _token_cache_lock

        with _token_cache_lock:
            _token_cache.clear()

        session = _make_session(get_payload=[])
        from core.sub2api_client import Sub2APIClient
        client = Sub2APIClient(
            'http://localhost:8080',
            email='admin@example.com',
            password='secret',
            session=session,
        )

        with patch('core.sub2api_client.requests.Session', return_value=session):
            client.resolve_token()
            client.resolve_token()

        # Login POST should have happened exactly once.
        self.assertEqual(session.post.call_count, 1)


class ApiKeyHeaderTest(unittest.TestCase):
    """Sub2API >=0.1.162 admin routes only accept x-api-key for the admin
    api-key (see sub2api backend admin_auth.go:50-57). Bearer is reserved
    for JWTs from /api/v1/auth/login."""

    def _settings(self, **overrides):
        base = {
            'sub2api_url': 'http://localhost:8080',
            'sub2api_email': '',
            'sub2api_password': '',
            'sub2api_api_token': 'sk-admin-xyz',
            'sub2api_timeout_sec': '180',
        }
        base.update(overrides)
        return base

    def setUp(self):
        # Reset module-level caches so each test starts clean.
        from core.sub2api_client import _token_cache, _auth_method_cache
        _token_cache.clear()
        _auth_method_cache.clear()

    def _mock(self, status, payload=None, text=None):
        resp = Mock(status_code=status, text=text or '{}')
        resp.ok = (200 <= status < 300)
        if payload is not None:
            resp.json.return_value = payload
        return resp

    def test_api_key_uses_x_api_key_header(self):
        """When api_token is set and the server accepts it, the client must
        send it via x-api-key — NOT Authorization: Bearer."""
        session = Mock()
        session.post.assert_not_called  # never called yet
        session.request.return_value = self._mock(200, payload=[
            {'id': 1, 'platform': 'grok'},
        ])

        from core.sub2api_client import Sub2APIClient
        with patch('core.sub2api_client.requests.Session', return_value=session):
            result = probe_sub2api_connection(self._settings())

        self.assertTrue(result['ok'])
        self.assertEqual(result['auth'], 'api_key')

        # Login must NOT have been called — api-key bypasses /auth/login.
        session.post.assert_not_called()

        # Inspect the GET headers: must include x-api-key, must NOT include
        # Authorization (which is the path that triggered the original 401).
        call = session.request.call_args
        headers = call.kwargs['headers']
        self.assertEqual(headers.get('x-api-key'), 'sk-admin-xyz')
        self.assertNotIn('Authorization', headers)

    def test_api_key_rejected_falls_back_to_login(self):
        """If x-api-key returns 401, retry once with email/password login
        (Authorization: Bearer) and report auth=login in the result."""
        session = Mock()
        # 1st POST = /auth/login (only used if we fall back).
        login_payload = {
            'code': 0, 'message': 'ok',
            'data': {'access_token': 'jwt-from-login', 'expires_in': 3600},
        }
        session.post.return_value = self._mock(200, payload=login_payload)

        # First request (x-api-key) → 401. Second (bearer) → 200.
        first = self._mock(401, text='{"code":401,"message":"INVALID_ADMIN_KEY"}')
        second = self._mock(200, payload=[{'id': 7, 'platform': 'grok'}])
        session.request.side_effect = [first, second]

        from core.sub2api_client import _auth_method_cache
        settings = self._settings(
            sub2api_email='admin@example.com',
            sub2api_password='secret',
        )
        with patch('core.sub2api_client.requests.Session', return_value=session):
            result = probe_sub2api_connection(settings)

        self.assertTrue(result['ok'])
        self.assertEqual(result['auth'], 'login')

        # Two outbound requests: first with x-api-key, then with bearer.
        self.assertEqual(session.request.call_count, 2)
        first_headers = session.request.call_args_list[0].kwargs['headers']
        second_headers = session.request.call_args_list[1].kwargs['headers']
        self.assertEqual(first_headers.get('x-api-key'), 'sk-admin-xyz')
        self.assertNotIn('Authorization', first_headers)
        self.assertEqual(
            second_headers.get('Authorization'),
            'Bearer jwt-from-login',
        )

        # Cache should reflect that bearer is the working method.
        self.assertEqual(_auth_method_cache.get('http://localhost:8080'), 'bearer')

    def test_api_key_rejected_no_fallback_yields_diagnostic(self):
        """Without email/password, a 401 on x-api-key should NOT raise
        Sub2APIError — it should return ok=False with a diagnostic hint."""
        session = Mock()
        session.post.assert_not_called  # ensure login never fires
        bad = self._mock(401, text='{"code":401,"message":"INVALID_ADMIN_KEY"}')
        session.request.return_value = bad

        with patch('core.sub2api_client.requests.Session', return_value=session):
            result = probe_sub2api_connection(self._settings())

        self.assertFalse(result['ok'])
        self.assertEqual(result['auth'], 'api_key_invalid')
        self.assertIn('rejected', result['error'].lower())
        # Login must NOT have been called.
        session.post.assert_not_called()

    def test_subsequent_request_uses_cached_auth_method(self):
        """After we discover that this base_url wants bearer, subsequent
        requests must go straight to /auth/login (skip the api-key probe)
        — but only when login credentials are actually configured."""
        from core.sub2api_client import Sub2APIClient, _auth_method_cache
        _auth_method_cache['http://localhost:8080'] = 'bearer'

        session = Mock()
        session.post.return_value = self._mock(200, payload={
            'code': 0, 'data': {'access_token': 'cached-jwt', 'expires_in': 3600},
        })
        session.request.return_value = self._mock(200, payload=[])

        client = Sub2APIClient(
            'http://localhost:8080',
            api_token='sk-admin-xyz',
            email='admin@example.com',
            password='secret',
            session=session,
        )
        with patch('core.sub2api_client.requests.Session', return_value=session):
            client._request_json('GET', '/api/v1/admin/groups/all')

        # Login was used (bearer path); request used bearer header.
        session.post.assert_called_once()
        headers = session.request.call_args.kwargs['headers']
        self.assertEqual(headers.get('Authorization'), 'Bearer cached-jwt')
        self.assertNotIn('x-api-key', headers)

    def test_cached_bearer_without_login_credentials_uses_api_key(self):
        """Defensive: a stale 'bearer' cache entry must not cause a crash
        if login credentials are empty — fall back to api-key instead."""
        from core.sub2api_client import Sub2APIClient, _auth_method_cache
        _auth_method_cache['http://localhost:8080'] = 'bearer'

        session = Mock()
        session.request.return_value = self._mock(200, payload=[])

        client = Sub2APIClient(
            'http://localhost:8080',
            api_token='sk-admin-xyz',
            email='', password='',
            session=session,
        )
        with patch('core.sub2api_client.requests.Session', return_value=session):
            client._request_json('GET', '/api/v1/admin/groups/all')

        # Login must NOT be called; api-key header is used instead.
        session.post.assert_not_called()
        headers = session.request.call_args.kwargs['headers']
        self.assertEqual(headers.get('x-api-key'), 'sk-admin-xyz')

    def test_token_cleaned_for_invisible_chars(self):
        """Pasted api-keys with stray invisible characters must be cleaned
        before being sent (BOM, ZWSP, etc.)."""
        session = Mock()
        session.request.return_value = self._mock(200, payload=[])
        # Includes a leading BOM and a trailing newline.
        with patch('core.sub2api_client.requests.Session', return_value=session):
            probe_sub2api_connection(self._settings(sub2api_api_token='﻿sk-clean-me\n'))

        headers = session.request.call_args.kwargs['headers']
        self.assertEqual(headers.get('x-api-key'), 'sk-clean-me')