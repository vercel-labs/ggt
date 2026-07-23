# mypy: ignore-errors
"""A plugin pulled in transitively by queue_plugin."""

import pytest


@pytest.fixture
def extra_value():
    return "extra"
