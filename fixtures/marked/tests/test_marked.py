# mypy: ignore-errors

import unittest

import ggt


@ggt.mark.suite_a
class MarkedAlpha(unittest.TestCase):
    @ggt.mark.slow
    def test_slow_one(self):
        self.assertTrue(True)

    @ggt.mark("slow", "integration")
    def test_slow_integration(self):
        self.assertTrue(True)

    def test_plain(self):
        self.assertTrue(True)


class MarkedBeta(unittest.TestCase):
    @ggt.mark.integration
    def test_integration_only(self):
        self.assertTrue(True)

    def test_unmarked(self):
        self.assertTrue(True)
