import pytest


@pytest.fixture(scope="package")
def sync_package_loop_state():
    value = []
    yield value
