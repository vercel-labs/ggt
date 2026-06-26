# mypy: ignore-errors
# ruff: noqa: FBT003

import unittest


class PackageCase(unittest.TestCase):
    def test_from_package(self):
        self.assertTrue(True)
