import unittest
from unittest.mock import Mock

from app import audit_login


class AuditTest(unittest.TestCase):
    def test_logs_identifier(self):
        logger = Mock()
        audit_login(logger, {"id": 7, "email": "a@example.test"})
        logger.info.assert_called_once_with("login user_id=%s", 7)
