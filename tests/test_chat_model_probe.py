import json
import unittest
from unittest.mock import Mock, patch

from core.cpa_export import (
    evaluate_chat_probe_response,
    is_accepted_chat_model,
    probe_chat,
    probe_chat_with_retries,
)
from core.grok2api_client import Grok2APIChatPermissionError, upload_registered_sso


class ChatModelGateTest(unittest.TestCase):
    def test_accepts_grok_45_build_free(self):
        self.assertTrue(is_accepted_chat_model('grok-4.5-build-free'))
        self.assertTrue(is_accepted_chat_model('Grok-4.5-Build-Free'))
        self.assertTrue(is_accepted_chat_model('grok-4.5-preview-build-free'))

    def test_rejects_non_free_or_empty_models(self):
        self.assertFalse(is_accepted_chat_model(''))
        self.assertFalse(is_accepted_chat_model(None))
        self.assertFalse(is_accepted_chat_model('grok-4.5'))
        self.assertFalse(is_accepted_chat_model('grok-4.20-0309-non-reasoning'))
        self.assertFalse(is_accepted_chat_model('claude-sonnet'))

    def test_evaluate_success_with_free_model(self):
        body = json.dumps({
            'model': 'grok-4.5-build-free',
            'choices': [{'message': {'content': 'OK'}}],
        })
        result = evaluate_chat_probe_response(200, body)
        self.assertTrue(result['ok'])
        self.assertTrue(result['model_ok'])
        self.assertEqual(result['model'], 'grok-4.5-build-free')
        self.assertEqual(result['classification'], 'chat_allowed_free')
        self.assertIsNone(result['error'])

    def test_evaluate_permission_denied(self):
        result = evaluate_chat_probe_response(403, '{"error":"permission-denied"}')
        self.assertFalse(result['ok'])
        self.assertFalse(result['model_ok'])
        self.assertEqual(result['classification'], 'chat_permission_denied')

    def test_evaluate_unexpected_model_is_soft_fail(self):
        body = json.dumps({'model': 'grok-4.5', 'choices': []})
        result = evaluate_chat_probe_response(200, body)
        # Chat permission works → upload should still be allowed.
        self.assertTrue(result['ok'])
        self.assertFalse(result['model_ok'])
        self.assertEqual(result['classification'], 'unexpected_model')
        self.assertIn('unexpected model', result['error'])

    def test_evaluate_missing_model_field(self):
        result = evaluate_chat_probe_response(200, '{"choices":[]}')
        self.assertTrue(result['ok'])
        self.assertFalse(result['model_ok'])
        self.assertEqual(result['classification'], 'unexpected_model')

    def test_evaluate_non_json_body(self):
        result = evaluate_chat_probe_response(200, 'not-json')
        # HTTP 200 still means chat works; body parse is soft.
        self.assertTrue(result['ok'])
        self.assertFalse(result['model_ok'])
        self.assertEqual(result['classification'], 'invalid_response')

    def test_probe_chat_parses_model_from_upstream(self):
        response = Mock(status_code=200, text=json.dumps({
            'model': 'grok-4.5-build-free',
            'choices': [{'message': {'content': 'ping'}}],
        }))
        with patch('curl_cffi.requests.post', return_value=response) as post:
            result = probe_chat('token-abc', proxy=None)
        self.assertTrue(result['ok'])
        self.assertTrue(result['model_ok'])
        self.assertEqual(result['model'], 'grok-4.5-build-free')
        self.assertEqual(result['classification'], 'chat_allowed_free')
        post.assert_called_once()
        kwargs = post.call_args.kwargs
        self.assertEqual(kwargs['json']['model'], 'grok-4.5')

    def test_probe_chat_keeps_ok_on_wrong_model(self):
        response = Mock(status_code=200, text=json.dumps({
            'model': 'grok-4.5',
            'choices': [{'message': {'content': 'ok'}}],
        }))
        with patch('curl_cffi.requests.post', return_value=response):
            result = probe_chat('token-abc', proxy=None)
        self.assertTrue(result['ok'])
        self.assertFalse(result['model_ok'])
        self.assertEqual(result['classification'], 'unexpected_model')
        self.assertEqual(result['model'], 'grok-4.5')

    def test_probe_retries_stop_on_permission_denied(self):
        calls = {'n': 0}

        def fake_probe(*_args, **_kwargs):
            calls['n'] += 1
            return {
                'ok': False,
                'status': 403,
                'model': '',
                'model_ok': False,
                'classification': 'chat_permission_denied',
                'error': 'permission denied',
            }

        with patch('core.cpa_export.probe_chat', side_effect=fake_probe):
            result = probe_chat_with_retries(
                'token',
                delay_sec=0,
                retries=3,
                retry_gap_sec=0,
            )
        self.assertEqual(calls['n'], 1)
        self.assertEqual(result['classification'], 'chat_permission_denied')
        self.assertFalse(result['ok'])

    def test_upload_allows_unexpected_model_and_imports(self):
        client = Mock()
        client.import_web_sso_and_convert.return_value = {
            'import': {'created': 1}, 'conversion': {'created': 1},
        }
        with patch('core.grok2api_client.Grok2APIClient', return_value=client), patch(
            'core.grok2api_client.sso_to_build_credential',
            return_value={'access_token': 'build-token'},
        ), patch(
            'core.cpa_export.probe_chat_with_retries',
            return_value={
                'ok': True,
                'status': 200,
                'model': 'grok-4.5',
                'model_ok': False,
                'classification': 'unexpected_model',
                'error': 'unexpected model: grok-4.5',
            },
        ):
            result = upload_registered_sso(
                {
                    'grok2api_auto_upload': 'true',
                    'grok2api_probe_chat': 'true',
                    'grok2api_probe_delay_sec': '0',
                    'grok2api_probe_retries': '0',
                    'grok2api_url': 'http://127.0.0.1:21434',
                    'grok2api_username': 'admin',
                    'grok2api_password': 'secret',
                },
                'sso-token',
                email='wrong-model@example.com',
            )

        self.assertTrue(result['grok2api']['probe']['ok'])
        self.assertFalse(result['grok2api']['probe']['model_ok'])
        self.assertEqual(result['grok2api']['probe']['classification'], 'unexpected_model')
        client.import_web_sso_and_convert.assert_called_once()

    def test_upload_blocks_only_on_permission_denied(self):
        client = Mock()
        with patch('core.grok2api_client.Grok2APIClient', return_value=client), patch(
            'core.grok2api_client.sso_to_build_credential',
            return_value={'access_token': 'build-token'},
        ), patch(
            'core.cpa_export.probe_chat_with_retries',
            return_value={
                'ok': False,
                'status': 403,
                'model': '',
                'model_ok': False,
                'classification': 'chat_permission_denied',
                'error': 'permission_denied',
            },
        ):
            with self.assertRaises(Grok2APIChatPermissionError) as raised:
                upload_registered_sso(
                    {
                        'grok2api_auto_upload': 'true',
                        'grok2api_probe_chat': 'true',
                        'grok2api_probe_delay_sec': '0',
                        'grok2api_probe_retries': '0',
                        'grok2api_url': 'http://127.0.0.1:21434',
                        'grok2api_username': 'admin',
                        'grok2api_password': 'secret',
                    },
                    'sso-token',
                    email='denied@example.com',
                )

        self.assertEqual(raised.exception.probe['classification'], 'chat_permission_denied')
        client.import_web_sso_and_convert.assert_not_called()

    def test_upload_passes_when_model_is_build_free(self):
        client = Mock()
        client.import_web_sso_and_convert.return_value = {
            'import': {'created': 1}, 'conversion': {'created': 1},
        }
        with patch('core.grok2api_client.Grok2APIClient', return_value=client), patch(
            'core.grok2api_client.sso_to_build_credential',
            return_value={'access_token': 'build-token'},
        ), patch(
            'core.cpa_export.probe_chat_with_retries',
            return_value={
                'ok': True,
                'status': 200,
                'model': 'grok-4.5-build-free',
                'model_ok': True,
                'classification': 'chat_allowed_free',
            },
        ):
            result = upload_registered_sso(
                {
                    'grok2api_auto_upload': 'true',
                    'grok2api_probe_chat': 'true',
                    'grok2api_probe_delay_sec': '0',
                    'grok2api_probe_retries': '0',
                    'grok2api_url': 'http://127.0.0.1:21434',
                    'grok2api_username': 'admin',
                    'grok2api_password': 'secret',
                },
                'sso-token',
                email='ok@example.com',
            )

        self.assertEqual(result['grok2api']['probe']['model'], 'grok-4.5-build-free')
        self.assertTrue(result['grok2api']['probe']['model_ok'])
        client.import_web_sso_and_convert.assert_called_once()


if __name__ == '__main__':
    unittest.main()
