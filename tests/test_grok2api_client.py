import json
import unittest
from unittest.mock import Mock

from core.grok2api_client import Grok2APIClient, Grok2APIError


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

        result = client.import_web_sso_and_convert('sso-token', email='user@example.com')

        self.assertEqual(result['import']['created'], 1)
        self.assertEqual(result['conversion']['created'], 1)
        conversion_call = session.post.call_args_list[3]
        self.assertEqual(conversion_call.kwargs['json'], {'ids': ['42']})


if __name__ == '__main__':
    unittest.main()
