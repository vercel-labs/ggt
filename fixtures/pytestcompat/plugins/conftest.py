# mypy: ignore-errors

import pytest

pytest_plugins = ["queue_plugin"]


@pytest.fixture
def overridable():
    return "conftest"
