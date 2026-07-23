# mypy: ignore-errors

import queue_plugin


def test_plugin_fixture(plug_value):
    assert plug_value == "srv://plug/value"


def test_transitive_plugin_fixture(extra_value):
    assert extra_value == "extra"


def test_conftest_overrides_plugin(overridable):
    assert overridable == "conftest"


def test_plugin_autouse_ran():
    assert queue_plugin._autouse_calls
