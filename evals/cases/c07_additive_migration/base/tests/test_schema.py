import sqlite3
import unittest

from schema import MIGRATIONS


class SchemaTest(unittest.TestCase):
    def test_migrations_apply(self):
        connection = sqlite3.connect(":memory:")
        for statement in MIGRATIONS:
            connection.execute(statement)
        self.assertEqual(connection.execute("SELECT count(*) FROM users").fetchone(), (0,))
