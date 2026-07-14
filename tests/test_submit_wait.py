import unittest

from core.register import submit_is_in_flight


class SubmitWaitTest(unittest.TestCase):
    def test_disabled_submit_button_is_in_flight_even_when_label_remains(self):
        self.assertTrue(submit_is_in_flight({
            'loading': False,
            'primaryDisabled': True,
            'primaryText': '完成注册',
        }))

    def test_enabled_button_without_spinner_is_not_in_flight(self):
        self.assertFalse(submit_is_in_flight({
            'loading': False,
            'primaryDisabled': False,
            'primaryText': '完成注册',
        }))


if __name__ == '__main__':
    unittest.main()
