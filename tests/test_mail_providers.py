import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import core.database as database_module
from core.database import Database
from core.email_manager import EmailManager
from core.mail_providers import (
    MailProviderError,
    ProvisionedMailbox,
    TemporaryMailboxProviders,
    extract_verification_code,
)


def response(data):
    item = Mock()
    item.json.return_value = data
    item.raise_for_status.return_value = None
    item.text = ''
    return item


class TemporaryMailboxProviderInterfaceTest(unittest.TestCase):
    def test_extracts_xai_code_and_normalizes_hyphen(self):
        self.assertEqual(
            extract_verification_code('Use ABC-123 as your confirmation code'),
            'ABC123',
        )

    def test_extracts_spacexai_subject_code(self):
        """Cloud Mail / xAI currently titles mails like SpaceXAI confirmation code: WKT-B4B."""
        self.assertEqual(
            extract_verification_code(
                '<html><style>.per100{width:100%}</style><body>ignore PER100</body></html>',
                subject='SpaceXAI confirmation code: WKT-B4B',
            ),
            'WKTB4B',
        )

    def test_html_body_does_not_steal_css_token_over_subject_code(self):
        self.assertEqual(
            extract_verification_code(
                'font-size:PER100; color:red',
                subject='SpaceXAI confirmation code: YKS-YHX',
            ),
            'YKSYHX',
        )

    def test_extracts_numeric_code_from_non_latin_content(self):
        self.assertEqual(
            extract_verification_code(
                '请使用验证码 739204 完成账号验证。',
                subject='使用 739204 完成验证',
            ),
            '739204',
        )

    def test_generic_alphanumeric_fallback_requires_a_digit(self):
        self.assertEqual(
            extract_verification_code('Please enter ABC123 to continue'),
            'ABC123',
        )
        self.assertIsNone(extract_verification_code('Please enter safely'))

    def test_duckmail_provisions_isolated_mailbox(self):
        http = Mock()
        http.request.side_effect = [
            response({'hydra:member': [
                {'domain': 'duck.test', 'isVerified': True},
            ]}),
            response({'id': 'account-1'}),
            response({'token': 'mailbox-token'}),
        ]
        providers = TemporaryMailboxProviders(http=http, sleep=lambda _: None)

        mailbox = providers.provision('duckmail', {
            'duckmail_api_base': 'https://duck.example',
            'duckmail_api_key': 'api-key',
        })

        self.assertEqual(mailbox.provider, 'duckmail')
        self.assertTrue(mailbox.address.endswith('@duck.test'))
        self.assertEqual(mailbox.credential, 'mailbox-token')
        self.assertEqual(http.request.call_count, 3)
        create_call = http.request.call_args_list[1]
        self.assertEqual(create_call.args[:2], ('POST', 'https://duck.example/accounts'))
        self.assertEqual(
            create_call.kwargs['headers']['Authorization'],
            'Bearer api-key',
        )

    def test_duckmail_reads_code_through_same_interface(self):
        http = Mock()
        http.request.side_effect = [
            response({'hydra:member': [{
                'id': 'message-1',
                'to': [{'address': 'new@duck.test'}],
                'subject': 'ABC-123 xAI verification',
            }]}),
            response({
                'id': 'message-1',
                'subject': 'ABC-123 xAI verification',
                'text': 'Use ABC-123 as your confirmation code',
            }),
        ]
        providers = TemporaryMailboxProviders(http=http, sleep=lambda _: None)

        code = providers.get_verification_code(
            'duckmail',
            'new@duck.test',
            'mailbox-token',
            {'duckmail_api_base': 'https://duck.example'},
            max_retries=1,
        )

        self.assertEqual(code, 'ABC123')

    def test_detail_fetch_failure_is_retried_without_marking_message_seen(self):
        listing = response({'hydra:member': [{
            'id': 'message-1',
            'to': [{'address': 'new@duck.test'}],
            'subject': 'xAI verification',
        }]})
        failed_detail = response({})
        failed_detail.raise_for_status.side_effect = RuntimeError('temporary')
        successful_detail = response({
            'id': 'message-1',
            'subject': 'ABC-123 xAI verification',
            'text': 'Use ABC-123 as your confirmation code',
        })
        http = Mock()
        http.request.side_effect = [
            listing, failed_detail, listing, successful_detail,
        ]
        providers = TemporaryMailboxProviders(http=http, sleep=lambda _: None)

        code = providers.get_verification_code(
            'duckmail',
            'new@duck.test',
            'mailbox-token',
            {'duckmail_api_base': 'https://duck.example'},
            max_retries=2,
        )

        self.assertEqual(code, 'ABC123')
        self.assertEqual(http.request.call_count, 4)

    def test_rejects_unknown_provider(self):
        providers = TemporaryMailboxProviders(http=Mock(), sleep=lambda _: None)
        with self.assertRaisesRegex(MailProviderError, 'Unsupported'):
            providers.provision('unknown', {})

    def test_cloud_mail_requires_each_admin_credential_without_api_key(self):
        providers = TemporaryMailboxProviders(http=Mock(), sleep=lambda _: None)
        with self.assertRaisesRegex(MailProviderError, 'cloud_mail_admin_email'):
            providers.provision('cloud_mail', {
                'cloud_mail_api_base': 'https://mail.example',
                'cloud_mail_admin_password': 'secret',
            })
        with self.assertRaisesRegex(MailProviderError, 'cloud_mail_admin_password'):
            providers.provision('cloud_mail', {
                'cloud_mail_api_base': 'https://mail.example',
                'cloud_mail_admin_email': 'admin@example.com',
            })

    def test_cloudflare_mail_read_requires_api_base(self):
        providers = TemporaryMailboxProviders(http=Mock(), sleep=lambda _: None)
        with self.assertRaisesRegex(MailProviderError, 'cloudflare_api_base'):
            providers.get_verification_code(
                'cloudflare',
                'user@example.com',
                'mailbox-token',
                {},
                max_retries=1,
            )


class EmailManagerProviderSeamTest(unittest.TestCase):
    def test_claim_provisions_only_when_provider_has_no_ready_mailbox(self):
        db = Mock()
        alias = {
            'id': 9,
            'provider': 'duckmail',
            'alias_email': 'new@duck.test',
        }
        db.claim_next_alias.side_effect = [None, alias]
        manager = EmailManager(db)
        manager.providers.provision = Mock(return_value=ProvisionedMailbox(
            'duckmail', 'new@duck.test', 'credential',
        ))

        claimed = manager.claim_registration_alias(
            {'email_provider': 'duckmail'},
            max_retries=3,
            lease_owner='worker-1',
            lease_seconds=600,
        )

        self.assertEqual(claimed, alias)
        db.create_temporary_account.assert_called_once_with(
            'new@duck.test', 'duckmail', 'credential',
        )
        self.assertEqual(db.claim_next_alias.call_count, 2)
        self.assertEqual(
            db.claim_next_alias.call_args.kwargs['provider'],
            'duckmail',
        )


class TemporaryMailboxDatabaseTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(
            database_module,
            'DB_PATH',
            os.path.join(self.temp_dir.name, 'test.db'),
        )
        self.db_patch.start()
        self.previous_instance = Database._instance
        Database._instance = None
        self.db = Database()
        self.db.init_database()

    def tearDown(self):
        self.db.conn.close()
        Database._instance = self.previous_instance
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_provider_filter_does_not_consume_microsoft_accounts(self):
        self.db.upsert_account(
            'main@example.com', '', 'client-id', 'refresh-token',
        )
        self.db.create_temporary_account(
            'temp@duck.test', 'duckmail', 'mailbox-token',
        )

        claimed = self.db.claim_next_alias(
            3, 'worker-1', lease_seconds=60, provider='duckmail',
        )

        self.assertEqual(claimed['alias_email'], 'temp@duck.test')
        self.assertEqual(claimed['provider'], 'duckmail')
        self.assertEqual(claimed['account_max_aliases'], 1)

    def test_temporary_account_rejects_unknown_provider(self):
        with self.assertRaisesRegex(ValueError, 'Unsupported email provider'):
            self.db.create_temporary_account(
                'temp@example.com', 'typo-provider', 'mailbox-token',
            )

    def test_temporary_account_rejects_provider_mismatch(self):
        self.db.create_temporary_account(
            'temp@example.com', 'duckmail', 'mailbox-token',
        )
        with self.assertRaisesRegex(ValueError, 'already belongs to provider duckmail'):
            self.db.create_temporary_account(
                'temp@example.com', 'yyds', 'other-token',
            )

    def test_temporary_account_never_generates_plus_alias(self):
        account_id = self.db.create_temporary_account(
            'temp@duck.test', 'duckmail', 'mailbox-token',
        )
        alias = self.db.claim_next_alias(
            1, 'worker-1', lease_seconds=60, provider='duckmail',
        )
        reg_id = self.db.create_registration(
            alias['id'], alias['alias_email'], 'password', 1,
            lease_owner='worker-1',
        )
        outcome = self.db.finish_registration_attempt(
            reg_id,
            alias['id'],
            'worker-1',
            'DuckMail failed to get verification code after 1 attempts',
            duration=1,
            max_retries=1,
        )

        self.assertTrue(outcome['terminal'])
        self.assertTrue(outcome['account_disabled'])
        self.assertEqual(self.db.get_account(account_id)['status'], 'disabled')
        self.assertIsNone(self.db.claim_next_alias(
            1, 'worker-2', lease_seconds=60, provider='duckmail',
        ))
        aliases = self.db.conn.execute(
            'SELECT alias_email FROM aliases WHERE account_id=?',
            (account_id,),
        ).fetchall()
        self.assertEqual([row['alias_email'] for row in aliases], ['temp@duck.test'])

if __name__ == '__main__':
    unittest.main()
