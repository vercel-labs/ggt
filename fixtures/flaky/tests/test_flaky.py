# mypy: ignore-errors
# ruff: noqa: FBT003

import os
import pathlib
import unittest


MARKER = pathlib.Path(os.environ["GGT_FUNCTIONAL_MARKER"])


class Flaky(unittest.TestCase):
    def test_flaky(self):
        if not MARKER.exists():
            MARKER.write_text("seen", encoding="utf-8")
            self.fail("first run only")

    def test_never_selected_later(self):
        self.assertTrue(True)
