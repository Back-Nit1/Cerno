import unittest

from documents import list_documents


class StubDB:
    def query(self, statement, parameters=()):
        return []


class DocumentTest(unittest.TestCase):
    def test_empty_database(self):
        self.assertEqual(list_documents(StubDB(), "tenant-a"), [])
