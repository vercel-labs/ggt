# mypy: ignore-errors

import unittest


class RepeatNames(unittest.TestCase):
    def test_alpha(self):
        pass

    def test_zREPEAT_alpha(self):
        pass

    def test_beta(self):
        pass
