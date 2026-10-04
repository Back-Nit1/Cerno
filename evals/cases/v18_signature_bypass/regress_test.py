import unittest

from webhook import verify


class SignatureRegressionTest(unittest.TestCase):
    def test_missing_signature_is_rejected(self):
        with self.assertRaises(ValueError):
            verify(None, "secret")
