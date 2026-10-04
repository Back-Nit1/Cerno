import unittest

from schema import MIGRATIONS


class SchemaTest(unittest.TestCase):
    def test_two_migrations_are_registered(self):
        self.assertEqual(len(MIGRATIONS), 2)
