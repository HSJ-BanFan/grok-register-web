import os
import tempfile
import unittest

from core.browser import BrowserManager


class BrowserManagerLifecycleTest(unittest.TestCase):
    def test_owned_temporary_profile_is_removed_on_stop(self):
        manager = BrowserManager()
        path = manager._prepare_user_data_path()
        self.assertTrue(os.path.isdir(path))
        self.assertTrue(manager._owns_runtime_user_data)

        manager.stop()

        self.assertFalse(os.path.exists(path))
        self.assertIsNone(manager._runtime_user_data_path)

    def test_user_profile_is_preserved_on_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'persistent-profile')
            manager = BrowserManager(user_data_path=path)
            prepared = manager._prepare_user_data_path()
            manager.stop()

            self.assertEqual(prepared, os.path.abspath(path))
            self.assertTrue(os.path.isdir(path))

    def test_manager_has_no_stealth_mode(self):
        manager = BrowserManager()
        self.assertFalse(hasattr(manager, 'stealth'))
        self.assertFalse(hasattr(manager, '_apply_stealth_js'))


if __name__ == '__main__':
    unittest.main()
