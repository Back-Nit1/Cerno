import unittest

from endpoint import service_url


class EndpointTest(unittest.TestCase):
    def test_configured_url(self):
        environment = {
            "BILLING_URL": "https://billing.test",
            "BILLING_V2_URL": "https://billing.test",
        }
        self.assertEqual(service_url(environment), "https://billing.test")
