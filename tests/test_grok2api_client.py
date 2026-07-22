import json
import unittest
from unittest.mock import Mock, patch

from core.grok2api_client import (
    Grok2APIChatPermissionError,
    Grok2APIClient,
    Grok2APIError,
    upload_registered_sso,
)


class Grok2APIClientTest(unittest.TestCase):
    def test_import_build_credential_logs_in_and_parses_completion_event(self):
        session = Mock()
        login = Mock(status_code=200)
        login.json.return_value = {'data': {'tokens': {'accessToken': 'admin-token'}}}
        imported = Mock(
            status_code=200,
            text='event: progress\ndata: {"completed":1,"total":1}\n\n'
                 'event: complete\ndata: {"created":1,"updated":0,"synced":1,"syncFailed":0}\n\n',
        )
        session.post.side_effect = [login, imported]
        client = Grok2APIClient('http://localhost:21434/', 'admin', 'secret')
        client.session = session

        result = client.import_build_credential({'provider': 'grok_build', 'access_token': 'token'})

        self.assertEqual(result['created'], 1)
        upload_call = session.post.call_args_list[1]
        self.assertEqual(upload_call.kwargs['headers']['Authorization'], 'Bearer admin-token')
        uploaded = json.loads(upload_call.kwargs['files']['file'][1])
        self.assertEqual(uploaded['accounts'][0]['access_token'], 'token')

    def test_import_raises_for_sse_error(self):
        session = Mock()
        login = Mock(status_code=200)
        login.json.return_value = {'data': {'tokens': {'accessToken': 'admin-token'}}}
        imported = Mock(status_code=200, text='event: error\ndata: {"message":"bad credential"}\n\n')
        session.post.side_effect = [login, imported]
        client = Grok2APIClient('http://localhost:21434', 'admin', 'secret')
        client.session = session

        with self.assertRaisesRegex(Grok2APIError, 'bad credential'):
            client.import_build_credential({'access_token': 'bad'})

    def test_web_sso_import_is_followed_by_unlinked_conversion(self):
        session = Mock()
        login_one = Mock(status_code=200)
        login_one.json.return_value = {'data': {'tokens': {'accessToken': 'admin-token'}}}
        imported = Mock(status_code=200, text='event: complete\ndata: {"created":1,"updated":0,"synced":0,"syncFailed":1}\n\n')
        lookup = Mock(status_code=200)
        lookup.json.return_value = {'data': {'items': [{'id': '42', 'name': 'user@example.com'}]}}
        login_two = Mock(status_code=200)
        login_two.json.return_value = {'data': {'tokens': {'accessToken': 'admin-token'}}}
        converted = Mock(status_code=200, text='event: complete\ndata: {"created":1,"linked":0,"skipped":0,"failed":0,"synced":0,"syncFailed":1}\n\n')
        session.post.side_effect = [login_one, imported, login_two, converted]
        session.get.return_value = lookup
        client = Grok2APIClient('http://localhost:21434', 'admin', 'secret')
        client.session = session

        with self.assertLogs('register', level='INFO') as logs:
            result = client.import_web_sso_and_convert('sso-token', email='user@example.com')

        self.assertEqual(result['import']['created'], 1)
        self.assertEqual(result['conversion']['created'], 1)
        output = '\n'.join(logs.output)
        self.assertIn('grok2api Web import started: account=user@example.com', output)
        self.assertIn('grok2api Web import completed: account=user@example.com created=1', output)
        self.assertIn('grok2api Build conversion started: account=user@example.com web_account_id=42', output)
        self.assertIn('grok2api Build conversion completed: account=user@example.com web_account_id=42 created=1', output)
        conversion_call = session.post.call_args_list[3]
        self.assertEqual(conversion_call.kwargs['json'], {'ids': ['42']})

    def test_disabled_auto_upload_is_explicitly_logged(self):
        with self.assertLogs('register', level='INFO') as logs:
            result = upload_registered_sso(
                {
                    'grok2api_auto_upload': 'false',
                    'cpa_auto_export': 'false',
                    'sub2api_auto_upload': 'false',
                },
                'sso-token',
                email='user@example.com',
            )

        self.assertIsNone(result)
        self.assertIn(
            'No delivery backend enabled (cpa_auto_export / grok2api_auto_upload / sub2api_auto_upload)',
            '\n'.join(logs.output),
        )

    def test_sub2api_only_delivery_calls_export(self):
        with patch(
            'core.sub2api_export.export_sso_to_sub2api',
            return_value={'account_id': 99, 'name': 'demo'},
        ) as export:
            result = upload_registered_sso(
                {
                    'grok2api_auto_upload': 'false',
                    'cpa_auto_export': 'false',
                    'sub2api_auto_upload': 'true',
                    'sub2api_url': 'https://example.test',
                    'sub2api_api_key': 'key',
                    'sub2api_group_id': '12',
                },
                'sso-token',
                email='user@example.com',
            )
        self.assertEqual(result['sub2api']['account_id'], 99)
        export.assert_called_once()

    def test_cpa_success_ignores_secondary_grok2api_failure(self):
        client = Mock()
        client.import_web_sso_and_convert.side_effect = RuntimeError('grok2api down')
        with patch('core.grok2api_client.Grok2APIClient', return_value=client):
            with patch(
                'core.cpa_export.export_sso_to_cpa',
                return_value={'path': '/cpa/auths/xai-a.json', 'probe': {'ok': True}},
            ):
                result = upload_registered_sso(
                    {
                        'cpa_auto_export': 'true',
                        'grok2api_auto_upload': 'true',
                        'grok2api_url': 'http://127.0.0.1:21434',
                        'grok2api_username': 'admin',
                        'grok2api_password': 'secret',
                    },
                    'sso-token',
                    email='user@example.com',
                )

        self.assertEqual(result['cpa']['path'], '/cpa/auths/xai-a.json')
        self.assertIn('grok2api down', result['grok2api_error'])

    def test_grok2api_probe_denied_stops_before_import(self):
        client = Mock()
        with patch('core.grok2api_client.Grok2APIClient', return_value=client), patch(
            'core.grok2api_client.sso_to_build_credential',
            return_value={'access_token': 'build-token'},
        ), patch(
            'core.cpa_export.probe_chat_with_retries',
            return_value={'ok': False, 'status': 403, 'error': 'permission_denied'},
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

        self.assertEqual(raised.exception.probe['status'], 403)
        client.import_web_sso_and_convert.assert_not_called()

    def test_grok2api_probe_success_imports_account(self):
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
                'classification': 'chat_allowed_free',
                'body': '{"model":"grok-4.5-build-free"}',
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

        self.assertTrue(result['grok2api']['probe']['ok'])
        self.assertEqual(result['grok2api']['probe']['model'], 'grok-4.5-build-free')
        client.import_web_sso_and_convert.assert_called_once()

    def test_web_sso_conversion_rejects_failed_completion_summary(self):
        session = Mock()
        login_one = Mock(status_code=200)
        login_one.json.return_value = {'data': {'tokens': {'accessToken': 'admin-token'}}}
        imported = Mock(status_code=200, text='event: complete\ndata: {"created":1,"updated":0}\n\n')
        lookup = Mock(status_code=200)
        lookup.json.return_value = {
            'data': {'items': [{'id': '42', 'name': 'user@example.com'}]},
        }
        login_two = Mock(status_code=200)
        login_two.json.return_value = {'data': {'tokens': {'accessToken': 'admin-token'}}}
        converted = Mock(
            status_code=200,
            text='event: complete\ndata: {"created":0,"linked":0,"skipped":0,"failed":1}\n\n',
        )
        login_three = Mock(status_code=200)
        login_three.json.return_value = {'data': {'tokens': {'accessToken': 'admin-token'}}}
        converted_retry = Mock(
            status_code=200,
            text='event: complete\ndata: {"created":0,"linked":0,"skipped":0,"failed":1}\n\n',
        )
        session.post.side_effect = [
            login_one, imported, login_two, converted,
            login_three, converted_retry,
        ]
        session.get.return_value = lookup
        client = Grok2APIClient('http://localhost:21434', 'admin', 'secret')
        client.session = session

        with self.assertRaisesRegex(Grok2APIError, 'failed for Web account 42'):
            client.import_web_sso_and_convert('sso-token', email='user@example.com')

    def test_web_sso_conversion_retries_one_transient_failure(self):
        session = Mock()
        login_one = Mock(status_code=200)
        login_one.json.return_value = {'data': {'tokens': {'accessToken': 'admin-token'}}}
        imported = Mock(status_code=200, text='event: complete\ndata: {"created":1,"updated":0}\n\n')
        lookup = Mock(status_code=200)
        lookup.json.return_value = {
            'data': {'items': [{'id': '42', 'name': 'user@example.com'}]},
        }
        login_two = Mock(status_code=200)
        login_two.json.return_value = {'data': {'tokens': {'accessToken': 'admin-token'}}}
        first_conversion = Mock(
            status_code=200,
            text='event: complete\ndata: {"created":0,"linked":0,"skipped":0,"failed":1}\n\n',
        )
        login_three = Mock(status_code=200)
        login_three.json.return_value = {'data': {'tokens': {'accessToken': 'admin-token'}}}
        successful_retry = Mock(
            status_code=200,
            text='event: complete\ndata: {"created":0,"linked":1,"skipped":0,"failed":0}\n\n',
        )
        session.post.side_effect = [
            login_one, imported, login_two, first_conversion,
            login_three, successful_retry,
        ]
        session.get.return_value = lookup
        client = Grok2APIClient('http://localhost:21434', 'admin', 'secret')
        client.session = session

        result = client.import_web_sso_and_convert('sso-token', email='user@example.com')

        self.assertEqual(result['conversion']['linked'], 1)
        self.assertEqual(session.post.call_count, 6)


if __name__ == '__main__':
    unittest.main()
