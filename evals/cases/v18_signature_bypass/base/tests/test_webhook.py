import unittest

from webhook import verify


class WebhookTest(unittest.TestCase):
    def test_valid_signature(self):
        self.assertTrue(verify("secret", "secret"))
