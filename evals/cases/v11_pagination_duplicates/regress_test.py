import unittest

from page import page


class PageRegressionTest(unittest.TestCase):
    def test_adjacent_pages_do_not_overlap(self):
        first = page([1, 2, 3, 4], 0, 2)
        second = page([1, 2, 3, 4], 1, 2)
        self.assertTrue(set(first).isdisjoint(second))
