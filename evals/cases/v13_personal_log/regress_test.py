import unittest
from profile import load_user
from unittest.mock import Mock

from audit import record_login


class PersonalDataRegressionTest(unittest.TestCase):
    def test_contact_is_not_logged(self):
        logger = Mock()
        record_login(logger, load_user())
        rendered = logger.info.call_args.args[0] % logger.info.call_args.args[1:]
        self.assertNotIn("ana@example.test", rendered)
