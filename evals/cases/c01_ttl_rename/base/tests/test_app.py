import unittest

from app import expires_at


class CacheTest(unittest.TestCase):
    def test_expiry_is_five_minutes(self):
        self.assertEqual(expires_at(1_000), 1_300)
