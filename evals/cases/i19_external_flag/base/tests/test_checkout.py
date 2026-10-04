import unittest

from checkout import checkout_mode


class CheckoutTest(unittest.TestCase):
    def test_unconfigured_mode_is_legacy(self):
        self.assertEqual(checkout_mode({}), "legacy")
