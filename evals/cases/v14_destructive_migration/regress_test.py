import sqlite3
import unittest

from schema import MIGRATIONS


class MigrationRegressionTest(unittest.TestCase):
    def test_accounts_survive_migrations(self):
        connection = sqlite3.connect(":memory:")
        for statement in MIGRATIONS:
            connection.execute(statement)
        tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        self.assertIn(("accounts",), tables)
