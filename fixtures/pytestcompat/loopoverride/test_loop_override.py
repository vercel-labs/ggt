import pytest


@pytest.fixture
def event_loop():
    return None


def test_loop_override(event_loop):
    raise AssertionError("must not run")
