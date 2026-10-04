import unittest

from session import active_sessions


class ExpiryRegressionTest(unittest.TestCase):
    def test_expired_sessions_are_excluded(self):
        rows = [{"id": "expired", "expires_at": 99}, {"id": "active", "expires_at": 101}]
        self.assertEqual([row["id"] for row in active_sessions(rows, 100)], ["active"])
