import unittest

from core.web_security import is_loopback_host, origin_matches_host


class WebSecurityTest(unittest.TestCase):
    def test_loopback_detection(self):
        self.assertTrue(is_loopback_host('localhost'))
        self.assertTrue(is_loopback_host('127.0.0.1'))
        self.assertTrue(is_loopback_host('::1'))
        self.assertFalse(is_loopback_host('0.0.0.0'))
        self.assertFalse(is_loopback_host('192.168.1.10'))

    def test_origin_must_match_request_host(self):
        self.assertTrue(origin_matches_host('', 'localhost:5000'))
        self.assertTrue(origin_matches_host(
            'http://localhost:5000', 'localhost:5000',
        ))
        self.assertFalse(origin_matches_host(
            'http://evil.example', 'localhost:5000',
        ))


if __name__ == '__main__':
    unittest.main()
