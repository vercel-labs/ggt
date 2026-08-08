# mypy: ignore-errors

import pytest


@pytest.fixture
def module_plugin_value():
    return "test-module-plugin"
