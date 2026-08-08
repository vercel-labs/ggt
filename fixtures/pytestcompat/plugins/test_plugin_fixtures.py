# mypy: ignore-errors

import queue_plugin

pytest_plugins = ["test_module_plugin"]


def test_plugin_fixture(plug_value):
    assert plug_value == "srv://plug/value"


def test_transitive_plugin_fixture(extra_value):
    assert extra_value == "extra"


def test_conftest_overrides_plugin(overridable):
    assert overridable == "conftest"


def test_plugin_autouse_ran():
    assert queue_plugin._autouse_calls


def test_test_module_plugin_fixture(module_plugin_value):
    assert module_plugin_value == "test-module-plugin"
