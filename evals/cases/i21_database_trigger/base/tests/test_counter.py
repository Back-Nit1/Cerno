import unittest

from counter import add_event


class Database:
    def insert_event(self, account_id):
        self.inserted = account_id

    def increment_count(self, account_id):
        self.incremented = account_id


class CounterTest(unittest.TestCase):
    def test_event_is_inserted(self):
        database = Database()
        add_event(database, 4)
        self.assertEqual(database.inserted, 4)
