# mypy: ignore-errors

import os
import pathlib
import unittest


EVENTS = pathlib.Path(os.environ["GGT_FUNCTIONAL_EVENTS"])


def event(name):
    with EVENTS.open("a", encoding="utf-8") as f:
        f.write(name + "\n")


class SetupError(unittest.TestCase):
    @classmethod
    def set_options(cls, options):
        event("options:" + options.get("mode", "missing"))

    @classmethod
    async def set_up_class_once(cls, ui):
        event("setup")
        raise RuntimeError("setup broke")

    @classmethod
    async def tear_down_class_once(cls, ui):
        event("teardown")

    @classmethod
    def get_shared_data(cls):
        return {}

    @classmethod
    def update_shared_data(cls, **data):
        event("update")

    def test_never_runs(self):
        self.fail("should not run")
