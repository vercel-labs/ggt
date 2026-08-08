# mypy: ignore-errors

import pytest


@pytest.fixture(scope="session")
def skipped_fixture():
    pytest.skip("skip from fixture")


@pytest.fixture
def failed_fixture():
    pytest.fail("fail from fixture")


def test_fixture_skip_is_reported_as_skip(skipped_fixture):
    raise AssertionError("unreachable")


@pytest.mark.xfail(strict=True)
def test_fixture_fail_is_reported_as_failure(failed_fixture):
    raise AssertionError("unreachable")
