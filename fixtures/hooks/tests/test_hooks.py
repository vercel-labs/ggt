# mypy: ignore-errors
# ruff: noqa: RUF012

import json
import os
import pathlib
import uuid
import unittest


EVENTS = pathlib.Path(os.environ["GGT_FUNCTIONAL_EVENTS"])


def event(name, **data):
    EVENTS.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"event": name, **data}, sort_keys=True)
    event_file = EVENTS / f"{os.getpid()}-{uuid.uuid4().hex}.json"
    event_file.write_text(payload, encoding="utf-8")


class SharedFixture:
    def __init__(self):
        self.value = None
        self.options = {}

    def __get__(self, instance, owner=None):
        return self

    def set_options(self, options):
        self.options = dict(options)
        event("fixture_options", color=self.options.get("color"))

    async def set_up(self, ui):
        self.value = "fixture-" + self.options.get("color", "none")
        event("fixture_setup", value=self.value)

    async def tear_down(self, ui):
        event("fixture_teardown")

    async def post_session_set_up(self, cases, *, ui):
        event("fixture_post", cases=len(cases))

    def get_shared_data(self):
        return {"value": self.value}

    def set_shared_data(self, data):
        self.value = data["value"]
        event("fixture_import", value=self.value)


class Hooked(unittest.TestCase):
    shared = SharedFixture()
    options = {}
    data = {}

    @classmethod
    def set_options(cls, options):
        cls.options = dict(options)
        event("class_options", color=cls.options.get("color"))

    @classmethod
    async def set_up_class_once(cls, ui):
        cls.data = {"class_value": "class-" + cls.options["color"]}
        event("class_setup", value=cls.data["class_value"])

    @classmethod
    async def tear_down_class_once(cls, ui):
        event("class_teardown")

    @classmethod
    def get_shared_data(cls):
        return cls.data

    @classmethod
    def update_shared_data(cls, **data):
        cls.data.update(data)
        event("class_import", value=cls.data.get("class_value"))

    def test_one(self):
        self.assertEqual(self.shared.value, "fixture-blue")
        self.assertEqual(self.data["class_value"], "class-blue")

    def test_two(self):
        self.assertEqual(self.shared.value, "fixture-blue")
        self.assertEqual(self.data["class_value"], "class-blue")
