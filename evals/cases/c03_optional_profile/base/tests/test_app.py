import unittest

from app import public_profile


class ProfileTest(unittest.TestCase):
    def test_public_fields(self):
        self.assertEqual(public_profile({"id": 1, "name": "Ana"}), {"id": 1, "name": "Ana"})
