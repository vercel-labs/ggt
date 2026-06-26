# mypy: ignore-errors
# ruff: noqa: FBT003

import unittest


class FastStop(unittest.TestCase):
    def test_1_fail(self):
        self.fail("stop")

    def test_2_later(self):
        self.assertTrue(True)
