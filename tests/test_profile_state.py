import json
import os
import tempfile
import unittest

from core.registration.profile import (
    ProfileSubmitSnapshot,
    ProfileSubmitStage,
    classify_profile_submit,
    save_profile_diagnostics,
)


class ProfileSubmitStateTest(unittest.TestCase):
    def test_classifies_in_flight_and_timeout(self):
        snapshot = ProfileSubmitSnapshot(loading=True, primary_disabled=True)
        self.assertEqual(
            classify_profile_submit(snapshot),
            ProfileSubmitStage.IN_FLIGHT,
        )
        self.assertEqual(
            classify_profile_submit(snapshot, timed_out=True),
            ProfileSubmitStage.TIMED_OUT,
        )

    def test_success_and_error_take_precedence(self):
        error = ProfileSubmitSnapshot(error_text='account exists')
        self.assertEqual(
            classify_profile_submit(error), ProfileSubmitStage.FAILED,
        )
        self.assertEqual(
            classify_profile_submit(error, has_sso=True),
            ProfileSubmitStage.SUCCEEDED,
        )

    def test_idle_timeout_is_stalled(self):
        self.assertEqual(
            classify_profile_submit(ProfileSubmitSnapshot(), timed_out=True),
            ProfileSubmitStage.STALLED,
        )

    def test_diagnostics_write_json_and_screenshot(self):
        class Page:
            title = 'Sign up'
            url = 'https://accounts.x.ai/sign-up'

            def get_screenshot(self, path=None, name=None, full_page=False):
                result = os.path.join(path, name)
                with open(result, 'wb') as handle:
                    handle.write(b'png')
                return result

        with tempfile.TemporaryDirectory() as directory:
            result = save_profile_diagnostics(
                Page(),
                ProfileSubmitStage.STALLED,
                ProfileSubmitSnapshot(primary_text='Complete sign up'),
                reason='no redirect',
                directory=directory,
            )
            self.assertTrue(os.path.exists(result['json']))
            self.assertTrue(os.path.exists(result['screenshot']))
            with open(result['json'], encoding='utf-8') as handle:
                payload = json.load(handle)
            self.assertEqual(payload['stage'], 'stalled')
            self.assertEqual(payload['reason'], 'no redirect')


if __name__ == '__main__':
    unittest.main()
