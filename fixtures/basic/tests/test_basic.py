# mypy: ignore-errors
# ruff: noqa: FBT003

import unittest


class Alpha(unittest.TestCase):
    def test_pass_a(self):
        self.assertTrue(True)

    def test_pass_b(self):
        self.assertEqual(2 + 2, 4)

    @unittest.skip("skip marker")
    def test_skipped(self):
        raise AssertionError("should not run")


class Beta(unittest.TestCase):
    def test_select_me(self):
        self.assertTrue(True)

    def test_exclude_me(self):
        self.assertTrue(True)
