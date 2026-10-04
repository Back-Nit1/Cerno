import unittest

from documents import list_documents


class RecordingDB:
    def __init__(self):
        self.statement = ""
        self.parameters = ()

    def query(self, statement, parameters=()):
        self.statement = statement
        self.parameters = parameters
        return []


class TenantRegressionTest(unittest.TestCase):
    def test_query_is_scoped_to_request_tenant(self):
        database = RecordingDB()
        list_documents(database, "tenant-a")
        self.assertIn("tenant_id", database.statement)
        self.assertEqual(database.parameters, ("tenant-a",))
