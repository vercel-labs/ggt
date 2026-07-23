# mypy: ignore-errors
"""A fixture-only pytest plugin, as libraries commonly ship them."""

import pytest

pytest_plugins = ["queue_plugin_extra"]

_autouse_calls = []


@pytest.fixture(scope="session")
def plug_server():
    return {"url": "srv://plug"}


@pytest.fixture
def plug_value(plug_server):
    return plug_server["url"] + "/value"


@pytest.fixture
def overridable():
    return "plugin"


@pytest.fixture(autouse=True)
def plug_autouse():
    _autouse_calls.append(1)
    yield
