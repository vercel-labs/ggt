# mypy: ignore-errors

import unittest


class SubTestCase(unittest.TestCase):
    def test_subtests_fail_as_error(self):
        for value in [1, 2]:
            with self.subTest(value=value):
                self.assertEqual(value, 1)


class PlainCase(unittest.TestCase):
    def test_pass(self):
        self.assertEqual("ok", "ok")
