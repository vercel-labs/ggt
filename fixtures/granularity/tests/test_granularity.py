# mypy: ignore-errors

import os
import pathlib
import unittest
import uuid


EVENTS = pathlib.Path(os.environ["GGT_FUNCTIONAL_EVENTS"])


def event(name):
    EVENTS.mkdir(parents=True, exist_ok=True)
    event_file = EVENTS / f"{os.getpid()}-{uuid.uuid4().hex}.txt"
    event_file.write_text(name, encoding="utf-8")


class SuiteGranularity(unittest.TestCase):
    @classmethod
    def get_parallelism_granularity(cls):
        return "suite"

    def test_suite_a(self):
        event("suite-a")

    def test_suite_b(self):
        event("suite-b")


class SystemGranularity(unittest.TestCase):
    @classmethod
    def get_parallelism_granularity(cls):
        return "system"

    def test_system_a(self):
        event("system-a")

    def test_system_b(self):
        event("system-b")


class DefaultGranularity(unittest.TestCase):
    def test_default_a(self):
        event("default-a")

    def test_default_b(self):
        event("default-b")
