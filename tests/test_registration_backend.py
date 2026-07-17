import base64
import json
import unittest
from unittest.mock import Mock, patch

import requests

from core.registration.backend import (
    BrowserRegistrationBackend,
    ProtocolEnvironmentError,
    ProtocolRegistrationBackend,
    SignupParameterDiscovery,
    SignupParameters,
    apply_sso_cookies,
    build_protocol_session,
    build_registration_backend,
    build_signup_payload,
    expand_set_cookie_chain,
    follow_sso_http,
    redact_sensitive_text,
    resolve_protocol_proxy,
)
from core.registration.protocol_worker import ProtocolRegistrationWorker
from core.registration.state import (
    DuplicateSSOError,
    ExistingAccountError,
    VerificationRequestError,
)
from core.registration.turnstile import (
    ExternalTurnstileProvider,
    TurnstileSolveError,
    parse_proxy_for_yescaptcha,
    resolve_turnstile_settings,
)
from core.runtime import resolve_registration_backend


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode('ascii').rstrip('=')


def _jwt_with_success_url(success_url: str) -> str:
    header = _b64url(json.dumps({'alg': 'none'}, separators=(',', ':')).encode())
    body = _b64url(
        json.dumps(
            {'config': {'success_url': success_url}},
            separators=(',', ':'),
        ).encode()
    )
    return f'{header}.{body}.sig'


class RegistrationBackendTest(unittest.TestCase):
    def test_backend_defaults_to_browser(self):
        self.assertEqual(resolve_registration_backend({}), 'browser')
        self.assertIsInstance(build_registration_backend('browser'), BrowserRegistrationBackend)

    def test_invalid_backend_falls_back_to_browser(self):
        self.assertEqual(resolve_registration_backend({'registration_backend': 'bogus'}), 'browser')

    def test_discovers_parameters_and_action_id(self):
        session = Mock()
        page = Mock(status_code=200)
        page.text = (
            '<script>"sitekey":"0x4AAAAAAAabc" '
            '"next-router-state-tree":"tree-value"</script>'
            '<script src="/_next/static/chunks/app.js"></script>'
        )
        bundle = Mock(status_code=200)
        bundle.text = 'action token 7f' + 'a' * 40
        page.raise_for_status.return_value = None
        bundle.raise_for_status.return_value = None
        session.get.side_effect = [page, bundle]

        params = SignupParameterDiscovery(session).discover('https://accounts.x.ai/sign-up')
        self.assertEqual(params.site_key, '0x4AAAAAAAabc')
        self.assertEqual(params.state_tree, 'tree-value')
        self.assertEqual(params.action_id, '7f' + 'a' * 40)

    def test_discovery_blocked_raises_environment_error(self):
        session = Mock()
        page = Mock(status_code=403, text='<!DOCTYPE html> Just a moment...')
        session.get.return_value = page
        with self.assertRaises(ProtocolEnvironmentError) as ctx:
            SignupParameterDiscovery(session).discover('https://accounts.x.ai/sign-up')
        self.assertEqual(ctx.exception.reason, 'blocked')

    def test_extracts_escaped_rsc_sitekey_and_default_tree(self):
        html = r'12:["$","$L1b",null,{\"sitekey\":\"0x4AAAAAAAhr9JGVDZbrZOo0\",\"nonce\":\"x\"}]'
        scripts = [
            'createServerReference)("7f0a91ba5242676db585f47da85cf4b6088e8920ae", callServer, void 0)'
        ]
        params = SignupParameterDiscovery(Mock()).extract_from_sources(html, scripts)
        self.assertEqual(params.site_key, '0x4AAAAAAAhr9JGVDZbrZOo0')
        self.assertEqual(params.action_id, '7f0a91ba5242676db585f47da85cf4b6088e8920ae')
        self.assertIn('sign-up', params.state_tree)
        self.assertIn('(app)', params.state_tree)

    def test_extracts_sso_from_session_cookie(self):
        session = Mock()
        session.cookies.get.return_value = 'sso-token'
        backend = ProtocolRegistrationBackend(
            session, SignupParameters('site', 'tree', 'action'),
        )
        response = Mock(url='https://accounts.x.ai/sign-up', text='')
        result = backend.extract_sso(response)
        self.assertEqual(result.sso, 'sso-token')
        self.assertEqual(backend.last_sso_follow, 'cookie')
        session.get.assert_not_called()

    def test_extracts_sso_from_set_cookie_follow(self):
        session = Mock()
        values = {'sso': ''}

        def cookie_get(name, default=None):
            return values.get(name, default)

        session.cookies.get.side_effect = cookie_get

        def after_get(*args, **kwargs):
            values['sso'] = 'followed-sso'
            return Mock(status_code=200, text='', url='https://auth.x.ai/done')

        session.get.side_effect = after_get
        backend = ProtocolRegistrationBackend(
            session, SignupParameters('site', 'tree', 'action'),
        )
        response = Mock(
            url='https://accounts.x.ai/sign-up',
            text='1:{"url":"https://auth.x.ai/set-cookie?q=abc.def.ghi"}',
        )
        result = backend.extract_sso(response)
        self.assertEqual(result.sso, 'followed-sso')
        self.assertEqual(backend.last_sso_follow, 'http')
        session.get.assert_called()

    def test_build_signup_payload(self):
        payload = build_signup_payload(
            email='a@b.com', password='x', given_name='Ann', family_name='Lee',
            email_validation_code='ABC123', turnstile_token='tok',
            castle_request_token='castle', conversion_id='cid',
            include_conversion_id=True,
        )
        self.assertEqual(payload['emailValidationCode'], 'ABC123')
        self.assertEqual(payload['createUserAndSessionRequest']['email'], 'a@b.com')
        self.assertEqual(payload['createUserAndSessionRequest']['givenName'], 'Ann')
        self.assertEqual(payload['createUserAndSessionRequest']['familyName'], 'Lee')
        self.assertEqual(payload['createUserAndSessionRequest']['clearTextPassword'], 'x')
        self.assertEqual(
            payload['createUserAndSessionRequest']['tosAcceptedVersion'],
            '$undefined',
        )
        self.assertEqual(payload['turnstileToken'], 'tok')
        self.assertEqual(payload['castleRequestToken'], 'castle')
        self.assertEqual(payload['conversionId'], 'cid')
        self.assertTrue(payload['promptOnDuplicateEmail'])

    def test_build_signup_payload_omits_optional_castle(self):
        payload = build_signup_payload(
            email='a@b.com', password='x', given_name='Ann', family_name='Lee',
            email_validation_code='ABC123', turnstile_token='tok',
        )
        self.assertNotIn('castleRequestToken', payload)
        self.assertNotIn('conversionId', payload)
        self.assertEqual(
            payload['createUserAndSessionRequest']['tosAcceptedVersion'],
            '$undefined',
        )

    def test_build_protocol_session_sets_proxy(self):
        session = build_protocol_session({'browser_proxy': 'http://127.0.0.1:7897'})
        self.assertIn('http', session.proxies)
        self.assertEqual(session.proxies['http'], 'http://127.0.0.1:7897')

    def test_build_protocol_session_prefers_legacy_impersonation(self):
        session = build_protocol_session({})
        profile = getattr(session, '_protocol_impersonate', '')
        if profile:
            self.assertIn(profile, {
                'chrome110', 'chrome119', 'chrome116', 'chrome104', 'chrome101',
                'edge101', 'chrome120', 'chrome124', 'chrome131', 'chrome', 'default',
            })

    def test_resolve_protocol_proxy_from_env(self):
        with patch.dict('os.environ', {'GROK_PROXY': 'http://proxy.example:8080'}, clear=False):
            self.assertEqual(resolve_protocol_proxy({}), 'http://proxy.example:8080')
        self.assertEqual(
            resolve_protocol_proxy({'browser_proxy': 'http://127.0.0.1:1'}),
            'http://127.0.0.1:1',
        )


class SensitiveTextAndSsoFollowTest(unittest.TestCase):
    def test_redact_sensitive_text_strips_jwt_and_sso(self):
        raw = (
            'redirect https://auth.x.ai/set-cookie?q=eyJhbGciOiJIUzI1NiJ9.'
            'eyJhIjoiYiJ9.signature and sso=super-secret-token-value end '
            'standalone eyJhbGciOiJIUzI1NiJ9.eyJhIjoiYiJ9.signature'
        )
        redacted = redact_sensitive_text(raw, limit=400)
        self.assertNotIn('super-secret-token-value', redacted)
        self.assertNotIn('signature', redacted)
        self.assertIn('[REDACTED]', redacted)
        self.assertIn('[JWT]', redacted)

    def test_expand_set_cookie_chain_walks_jwt_success_url(self):
        inner = 'https://auth.x.ai/set-cookie?q=inner-token'
        outer = 'https://auth.x.ai/set-cookie?q=' + _jwt_with_success_url(inner)
        hops = expand_set_cookie_chain(outer)
        self.assertEqual(hops[0], outer)
        self.assertIn(inner, hops)

    def test_follow_sso_http_multi_hop_without_browser(self):
        session = requests.Session()
        terminal = 'https://auth.x.ai/set-cookie?q=terminal'
        outer = 'https://auth.x.ai/set-cookie?q=' + _jwt_with_success_url(terminal)
        calls = []

        def fake_get(url, **kwargs):
            calls.append(url)
            if 'terminal' in url or url.endswith('terminal'):
                session.cookies.set('sso', 'http-hop-sso', domain='.x.ai')
            return Mock(status_code=200, text='', url=url)

        session.get = fake_get  # type: ignore[method-assign]
        sso = follow_sso_http(session, outer)
        self.assertEqual(sso, 'http-hop-sso')
        self.assertGreaterEqual(len(calls), 2)
        self.assertTrue(any('terminal' in c for c in calls))

    def test_backend_prefers_http_follow_before_browser_navigate(self):
        session = requests.Session()
        values = {'sso': ''}

        def cookie_get(name, default=None):
            return values.get(name, default)

        session.cookies.get = cookie_get  # type: ignore[method-assign]

        def after_get(url, **kwargs):
            values['sso'] = 'http-sso'
            return Mock(status_code=200, text='', url=url)

        session.get = after_get  # type: ignore[method-assign]
        backend = ProtocolRegistrationBackend(
            session, SignupParameters('site', 'tree', 'action'),
        )
        navigate = Mock(return_value='browser-sso')
        backend._navigate_for_sso = navigate
        response = Mock(
            url='https://accounts.x.ai/sign-up',
            text='1:{"url":"https://auth.x.ai/set-cookie?q=abc.def.ghi"}',
        )
        result = backend.extract_sso(response)
        self.assertEqual(result.sso, 'http-sso')
        self.assertEqual(backend.last_sso_follow, 'http')
        navigate.assert_not_called()

    def test_apply_sso_cookies_sets_grok_and_xai_domains(self):
        session = requests.Session()
        cookie_dict = apply_sso_cookies(session, 'tok-123')
        self.assertEqual(cookie_dict['sso'], 'tok-123')
        self.assertEqual(cookie_dict['sso-rw'], 'tok-123')
        domains = {
            getattr(c, 'domain', '')
            for c in session.cookies
            if getattr(c, 'name', '') == 'sso'
        }
        # At least one of the intended domains should be stamped.
        self.assertTrue(domains.intersection({'.x.ai', 'x.ai', '.grok.com', 'grok.com', ''}))


class ExternalTurnstileProviderTest(unittest.TestCase):
    def test_resolve_turnstile_settings_from_env(self):
        with patch.dict(
            'os.environ',
            {
                'YESCAPTCHA_KEY': 'abc',
                'TURNSTILE_SOLVER_URL': 'http://127.0.0.1:9',
            },
            clear=False,
        ):
            cfg = resolve_turnstile_settings({})
            self.assertEqual(cfg['yescaptcha_key'], 'abc')
            self.assertEqual(cfg['solver_url'], 'http://127.0.0.1:9')

    def test_external_mode_disables_browser_fallback(self):
        cfg = resolve_turnstile_settings({'turnstile_provider': 'external'})
        self.assertEqual(cfg['allow_browser_fallback'], 'false')
        cfg_auto = resolve_turnstile_settings({'turnstile_provider': 'auto'})
        self.assertEqual(cfg_auto['allow_browser_fallback'], 'true')
        cfg_strict = resolve_turnstile_settings({'turnstile_provider': 'strict_external'})
        self.assertEqual(cfg_strict['allow_browser_fallback'], 'false')
        cfg_override = resolve_turnstile_settings({
            'turnstile_provider': 'external',
            'allow_browser_fallback': 'true',
        })
        self.assertEqual(cfg_override['allow_browser_fallback'], 'true')

    def test_parse_proxy_for_yescaptcha(self):
        fields = parse_proxy_for_yescaptcha('http://user:pass@127.0.0.1:7897')
        self.assertEqual(fields['proxyType'], 'http')
        self.assertEqual(fields['proxyAddress'], '127.0.0.1')
        self.assertEqual(fields['proxyPort'], '7897')
        self.assertEqual(fields['proxyLogin'], 'user')
        self.assertEqual(fields['proxyPassword'], 'pass')

    def test_yescaptcha_solve_polls_until_ready(self):
        provider = ExternalTurnstileProvider(yescaptcha_key='k', timeout=10, poll_interval=0)
        create = Mock()
        create.raise_for_status.return_value = None
        create.json.return_value = {'errorId': 0, 'taskId': 't1'}
        ready = Mock()
        ready.raise_for_status.return_value = None
        ready.json.return_value = {
            'errorId': 0,
            'status': 'ready',
            'solution': {'token': 'cf-token'},
        }
        provider._http = Mock()
        provider._http.post.side_effect = [create, ready]
        with patch('core.registration.turnstile.time.sleep', return_value=None):
            token = provider.solve(
                url='https://accounts.x.ai',
                site_key='0x4AAAA',
                session=Mock(),
            )
        self.assertEqual(token, 'cf-token')
        task = provider._http.post.call_args_list[0].kwargs['json']['task']
        self.assertEqual(task['type'], 'TurnstileTaskProxyless')

    def test_yescaptcha_binds_registration_proxy(self):
        provider = ExternalTurnstileProvider(
            yescaptcha_key='k',
            proxy='http://user:pass@10.0.0.2:7897',
            timeout=10,
            poll_interval=0,
        )
        create = Mock()
        create.raise_for_status.return_value = None
        create.json.return_value = {'errorId': 0, 'taskId': 't1'}
        ready = Mock()
        ready.raise_for_status.return_value = None
        ready.json.return_value = {
            'errorId': 0,
            'status': 'ready',
            'solution': {'token': 'cf-token'},
        }
        provider._http = Mock()
        provider._http.post.side_effect = [create, ready]
        with patch('core.registration.turnstile.time.sleep', return_value=None):
            token = provider.solve(
                url='https://accounts.x.ai',
                site_key='0x4AAAA',
                session=Mock(),
            )
        self.assertEqual(token, 'cf-token')
        task = provider._http.post.call_args_list[0].kwargs['json']['task']
        self.assertEqual(task['type'], 'TurnstileTask')
        self.assertEqual(task['proxyAddress'], '10.0.0.2')
        self.assertEqual(task['proxyPort'], '7897')
        self.assertEqual(task['proxyLogin'], 'user')

    def test_local_solver_appends_proxy_query(self):
        provider = ExternalTurnstileProvider(
            solver_url='http://127.0.0.1:5072',
            proxy='http://127.0.0.1:7897',
            timeout=10,
            poll_interval=0,
        )
        create = Mock()
        create.raise_for_status.return_value = None
        create.json.return_value = {'taskId': 'local-1'}
        ready = Mock()
        ready.raise_for_status.return_value = None
        ready.json.return_value = {'solution': {'token': 'local-token'}}
        provider._http = Mock()
        provider._http.get.side_effect = [create, ready]
        with patch('core.registration.turnstile.time.sleep', return_value=None):
            token = provider.solve(
                url='https://accounts.x.ai',
                site_key='0x4AAAA',
                session=Mock(),
            )
        self.assertEqual(token, 'local-token')
        create_url = provider._http.get.call_args_list[0].args[0]
        self.assertIn('proxy=', create_url)


class ProtocolWorkerFailureMappingTest(unittest.TestCase):
    def _worker(self):
        db = Mock()
        state = Mock()
        state.should_stop.return_value = False
        socketio = Mock()
        email_mgr = Mock()
        browser = Mock()
        worker = ProtocolRegistrationWorker(db, browser, email_mgr, socketio, state)
        return worker, db, state, socketio

    def test_environment_error_aborts_without_retry(self):
        worker, db, state, socketio = self._worker()
        db.abort_registration_attempt.return_value = True
        alias = {'id': 1, 'alias_email': 'a@b.com', 'main_email': 'a@b.com'}
        worker._handle_round_failure(
            ProtocolEnvironmentError('blocked', reason='blocked', diagnostics='x'),
            'blocked', 1.0, reg_id=9, alias=alias, lease_owner='w1',
            max_retries=2, round_num=1, worker_id='worker-1', alias_email='a@b.com',
        )
        db.abort_registration_attempt.assert_called_once()
        state.stop.assert_called_once()
        db.finish_registration_attempt.assert_not_called()

    def test_existing_account_skips(self):
        worker, db, state, socketio = self._worker()
        db.skip_existing_account_attempt.return_value = {
            'lease_lost': False, 'account_disabled': False,
        }
        alias = {'id': 1, 'alias_email': 'a@b.com', 'main_email': 'a@b.com'}
        worker._handle_round_failure(
            ExistingAccountError('exists'),
            'exists', 1.0, reg_id=9, alias=alias, lease_owner='w1',
            max_retries=2, round_num=1, worker_id='worker-1', alias_email='a@b.com',
        )
        db.skip_existing_account_attempt.assert_called_once()
        state.record_failure.assert_called_once_with('worker-1')

    def test_duplicate_sso_uses_extra_retry_budget(self):
        worker, db, state, socketio = self._worker()
        db.finish_registration_attempt.return_value = {
            'retry_count': 1, 'terminal': False, 'lease_lost': False,
        }
        alias = {'id': 1, 'alias_email': 'a@b.com', 'main_email': 'a@b.com'}
        worker._handle_round_failure(
            DuplicateSSOError('dup'),
            'dup', 1.0, reg_id=9, alias=alias, lease_owner='w1',
            max_retries=1, round_num=1, worker_id='worker-1', alias_email='a@b.com',
        )
        kwargs = db.finish_registration_attempt.call_args.kwargs
        self.assertEqual(kwargs['max_retries'], 2)

    def test_permission_denied_aborts(self):
        worker, db, state, socketio = self._worker()
        db.abort_registration_attempt.return_value = True
        alias = {'id': 1, 'alias_email': 'a@b.com', 'main_email': 'a@b.com'}
        worker._handle_round_failure(
            VerificationRequestError('permission_denied HTTP 403'),
            'permission_denied HTTP 403', 1.0, reg_id=9, alias=alias, lease_owner='w1',
            max_retries=2, round_num=1, worker_id='worker-1', alias_email='a@b.com',
        )
        db.abort_registration_attempt.assert_called_once()
        state.stop.assert_called_once()

    def test_prepare_transport_pure_http_without_browser(self):
        worker, db, state, socketio = self._worker()
        worker.browser = None
        params = SignupParameters('0x4site', 'tree', '7f' + 'b' * 40)
        session = Mock()
        session.headers = {'User-Agent': 'ua'}
        session.cookies = Mock()
        with patch(
            'core.registration.protocol_worker.build_protocol_session',
            return_value=session,
        ), patch.object(
            ProtocolRegistrationWorker,
            '_discover_parameters_http',
            return_value=params,
        ), patch(
            'core.registration.protocol_worker.ExternalTurnstileProvider.from_settings',
            return_value=None,
        ):
            worker._prepare_transport({})
        self.assertTrue(worker._pure_http)
        self.assertEqual(worker._transport_mode, 'http')
        self.assertIsNotNone(worker._backend)
        self.assertIsNone(worker._backend.request_func)

    def test_strict_external_does_not_start_browser_on_turnstile_failure(self):
        worker, db, state, socketio = self._worker()
        worker._allow_browser_fallback = False
        worker._external_provider = Mock()
        worker._external_provider.yescaptcha_key = 'k'
        worker._external_provider.available.return_value = True
        worker._external_provider.solve.side_effect = TurnstileSolveError('boom')
        worker._provider = Mock()
        worker._session = Mock()
        with self.assertRaises(TurnstileSolveError) as ctx:
            worker._solve_turnstile(url='https://accounts.x.ai', site_key='0x4')
        self.assertIn('browser fallback is disabled', str(ctx.exception))
        worker._provider.ensure_started.assert_not_called()
        worker._provider.solve.assert_not_called()

    def test_strict_external_blocks_discovery_browser_fallback(self):
        worker, db, state, socketio = self._worker()
        session = Mock()
        session.headers = {'User-Agent': 'ua'}
        session.cookies = Mock()
        with patch(
            'core.registration.protocol_worker.build_protocol_session',
            return_value=session,
        ), patch.object(
            ProtocolRegistrationWorker,
            '_discover_parameters_http',
            side_effect=ProtocolEnvironmentError('blocked', reason='blocked'),
        ), patch(
            'core.registration.protocol_worker.ExternalTurnstileProvider.from_settings',
            return_value=None,
        ), patch(
            'core.registration.protocol_worker.resolve_turnstile_settings',
            return_value={
                'allow_browser_fallback': 'false',
                'mode': 'external',
                'yescaptcha_key': '',
                'solver_url': '',
                'proxy': '',
            },
        ):
            with self.assertRaises(ProtocolEnvironmentError) as ctx:
                worker._prepare_transport({'turnstile_provider': 'external'})
        self.assertEqual(ctx.exception.reason, 'blocked_no_fallback')
        worker.browser.start.assert_not_called()

    def test_external_turnstile_success_sets_mode_without_browser(self):
        worker, db, state, socketio = self._worker()
        worker._allow_browser_fallback = False
        worker._external_provider = Mock()
        worker._external_provider.yescaptcha_key = 'k'
        worker._external_provider.available.return_value = True
        worker._external_provider.solve.return_value = 'cf-token'
        worker._provider = Mock()
        worker._session = Mock()
        token = worker._solve_turnstile(url='https://accounts.x.ai', site_key='0x4')
        self.assertEqual(token, 'cf-token')
        self.assertEqual(worker._turnstile_mode, 'yescaptcha')
        worker._provider.ensure_started.assert_not_called()

    def test_post_success_init_stamps_grok_domain_cookies(self):
        worker, db, state, socketio = self._worker()
        worker._session = Mock()
        worker._session.headers = {'User-Agent': 'ua'}
        worker._session._protocol_impersonate = 'chrome110'
        fake_session = Mock()
        fake_session.headers = {}
        fake_session.proxies = {}
        fake_session.cookies = Mock()
        fake_resp = Mock(status_code=200)
        fake_session.post = Mock(return_value=fake_resp)

        with patch.dict('sys.modules', {'curl_cffi': None}), \
             patch('core.account_activation._set_tos', return_value=True), \
             patch('core.account_activation._birth_date', return_value='2000-01-01T16:00:00.000Z'), \
             patch('core.registration.protocol_worker.requests.Session', return_value=fake_session):
            worker._post_success_init('sso-token', {'protocol_post_init': 'true'})
        self.assertTrue(fake_session.post.called)
        kwargs = fake_session.post.call_args.kwargs
        self.assertIn('cookies', kwargs)
        self.assertEqual(kwargs['cookies'].get('sso'), 'sso-token')
        self.assertEqual(kwargs['cookies'].get('sso-rw'), 'sso-token')

    def test_mode_summary_splits_fields(self):
        worker, db, state, socketio = self._worker()
        worker._transport_mode = 'http'
        worker._turnstile_mode = 'yescaptcha'
        worker._sso_follow_mode = 'http'
        self.assertEqual(
            worker._mode_summary(),
            'transport=http turnstile=yescaptcha sso_follow=http',
        )

    def test_zero_browser_round_external_turnstile_to_sso(self):
        """Full protocol round without starting Chrome (strict external)."""
        worker, db, state, socketio = self._worker()
        worker.browser = None
        worker._allow_browser_fallback = False
        worker._pure_http = True
        worker._transport_mode = 'http'
        worker._browser_started = False
        worker._provider = None
        worker._params = SignupParameters('0x4site', 'tree', '7f' + 'c' * 40)

        session = requests.Session()
        worker._session = session

        external = Mock()
        external.yescaptcha_key = 'k'
        external.available.return_value = True
        external.solve.return_value = 'cf-turnstile-token'
        worker._external_provider = external

        backend = Mock()
        backend.request_func = None
        backend.last_sso_follow = 'http'
        backend.send_email_code.return_value = None
        backend.verify_email_code.return_value = None
        backend.submit_signup.return_value = Mock(
            status_code=200,
            text='1:{"url":"https://auth.x.ai/set-cookie?q=abc.def"}',
            url='https://accounts.x.ai/sign-up',
        )
        backend.extract_sso.return_value = Mock(sso='zero-browser-sso-token-1234567890')
        worker._backend = backend

        db.create_registration.return_value = 42
        db.find_existing_sso.return_value = None
        db.complete_registration_success.return_value = {'ok': True}
        worker.email_mgr.get_code_for_alias.return_value = '123456'

        alias = {
            'id': 7,
            'alias_email': 'zero@example.com',
            'main_email': 'zero@example.com',
            'account_id': 1,
            'client_id': 'cid',
            'refresh_token': 'rt',
            'provider': 'microsoft',
        }
        settings = {
            'max_code_retries': 10,
            'grok2api_auto_upload': 'false',
            'protocol_post_init': 'false',
            'password_mode': 'manual',
            'manual_password': 'TestPass123!',
            'random_name_enabled': 'false',
            'turnstile_provider': 'external',
            'allow_browser_fallback': 'false',
        }

        with patch.object(worker, '_get_password', return_value='TestPass123!'), \
             patch.object(worker, '_generate_random_name', return_value=('Ann', 'Lee')), \
             patch.object(worker, '_post_success_init') as post_init, \
             patch.object(worker, '_bind_backend'):
            worker._do_one_round(
                alias, round_num=1, max_retries=2, settings=settings,
                lease_owner='w-zero', worker_id='worker-zero',
            )

        external.solve.assert_called_once()
        backend.send_email_code.assert_called_once()
        backend.verify_email_code.assert_called_once()
        backend.submit_signup.assert_called_once()
        backend.extract_sso.assert_called_once()
        db.complete_registration_success.assert_called_once()
        success_sso = db.complete_registration_success.call_args.args[3]
        self.assertEqual(success_sso, 'zero-browser-sso-token-1234567890')
        self.assertEqual(worker._turnstile_mode, 'yescaptcha')
        self.assertEqual(worker._sso_follow_mode, 'http')
        self.assertEqual(worker._transport_mode, 'http')
        self.assertFalse(worker._browser_started)
        self.assertIsNone(worker._provider)
        # Worker always invokes post-init hook; the method itself no-ops when disabled.
        post_init.assert_called_once()
        self.assertEqual(post_init.call_args.args[0], 'zero-browser-sso-token-1234567890')
        self.assertEqual(post_init.call_args.args[1].get('protocol_post_init'), 'false')
        summary = worker._mode_summary()
        self.assertEqual(summary, 'transport=http turnstile=yescaptcha sso_follow=http')


class EngineProtocolBranchTest(unittest.TestCase):
    def test_engine_routes_protocol_to_worker(self):
        from core.register import RegistrationEngine

        db = Mock()
        db.get_settings.return_value = {'registration_backend': 'protocol'}
        browser = Mock()
        email_mgr = Mock()
        socketio = Mock()
        state = Mock()
        engine = RegistrationEngine(db, browser, email_mgr, socketio, state)

        with patch('core.registration.protocol_worker.ProtocolRegistrationWorker') as Worker:
            instance = Worker.return_value
            engine.run(max_rounds=1, max_retries=1, concurrency=1)
            Worker.assert_called_once()
            instance.run.assert_called_once_with(max_rounds=1, max_retries=1, concurrency=1)


if __name__ == '__main__':
    unittest.main()
