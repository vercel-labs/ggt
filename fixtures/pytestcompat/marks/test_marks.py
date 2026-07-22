# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

import sys

import pytest


@pytest.mark.skip(reason="always skipped")
def test_skipped():
    raise AssertionError("must not run")


@pytest.mark.skipif(sys.platform == "the-moon", reason="never true")
def test_not_skipped():
    assert True


@pytest.mark.skipif("sys.platform != 'the-moon'", reason="string cond")
def test_skipped_by_string_condition():
    raise AssertionError("must not run")


def test_imperative_skip():
    pytest.skip("imperative skip")


@pytest.mark.xfail(reason="known bad")
def test_xfail_assertion():
    assert False  # noqa: B011


@pytest.mark.xfail(reason="known bad error")
def test_xfail_error():
    raise RuntimeError("boom")


@pytest.mark.xfail(raises=KeyError, reason="known key error")
def test_xfail_raises_match():
    raise KeyError("missing")


@pytest.mark.xfail(reason="fixed but not unmarked", strict=True)
def test_xfail_strict_passes():
    assert True


@pytest.mark.parametrize(
    "value,doubled",
    [
        (1, 2),
        (2, 4),
        pytest.param(3, 6, id="three"),
        pytest.param(0, 1, marks=pytest.mark.xfail(reason="zero is bad")),
    ],
)
def test_parametrized(value, doubled):
    assert value * 2 == doubled


@pytest.mark.parametrize("x", [1, 2])
@pytest.mark.parametrize("y", ["a", "b"])
def test_parametrized_stacked(x, y):
    assert isinstance(x, int)
    assert isinstance(y, str)


_use_fixture_ran = []


@pytest.fixture
def use_me():
    _use_fixture_ran.append(1)


@pytest.mark.usefixtures("use_me")
def test_usefixtures():
    assert _use_fixture_ran


@pytest.fixture
def per_param_fixture():
    return 10


@pytest.mark.parametrize("offset", [0, 1])
def test_parametrize_with_fixture(offset, per_param_fixture):
    assert per_param_fixture + offset >= 10


class TestMarkedClass:
    @pytest.mark.parametrize("n", [1, 2])
    def test_method_parametrized(self, n):
        assert n in (1, 2)

    @pytest.mark.skip(reason="class method skip")
    def test_method_skipped(self):
        raise AssertionError("must not run")
