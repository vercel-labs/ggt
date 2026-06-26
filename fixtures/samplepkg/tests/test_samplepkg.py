# mypy: ignore-errors
import unittest

import samplepkg


class CoverageCase(unittest.TestCase):
    def test_answer(self):
        self.assertEqual(samplepkg.answer(), 42)
