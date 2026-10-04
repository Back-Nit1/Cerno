import unittest
from unittest.mock import Mock

from audit import record_login


class AuditTest(unittest.TestCase):
    def test_emits_one_event(self):
        logger = Mock()
        record_login(logger, {"id": 7, "contact": "ana@example.test"})
        logger.info.assert_called_once()
