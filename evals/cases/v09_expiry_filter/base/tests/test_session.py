import unittest

from session import active_sessions


class SessionTest(unittest.TestCase):
    def test_empty_store(self):
        self.assertEqual(active_sessions([], 100), [])
