# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

import pytest

_mod_setups = []


@pytest.fixture(params=[1, 2, 3])
def number(request):
    return request.param


def test_number_is_positive(number):
    assert number > 0


def test_number_small(number):
    assert number <= 3


@pytest.fixture(
    params=[
        "a",
        pytest.param("b", id="bee"),
        pytest.param("c", marks=pytest.mark.skip(reason="no c")),
    ]
)
def letter(request):
    return request.param


def test_letter(letter):
    assert letter in ("a", "b")


@pytest.fixture(params=[10, 20], ids=["ten", "twenty"])
def named(request):
    return request.param


@pytest.mark.parametrize("mult", [1, 2])
def test_cross(named, mult):
    assert named * mult in (10, 20, 40)


@pytest.fixture(scope="module", params=["x", "y"])
def mod_param(request):
    _mod_setups.append(request.param)
    return request.param


def test_mod_param_one(mod_param):
    assert mod_param in ("x", "y")
    # Module-scoped parametrized fixtures are cached per param.
    assert _mod_setups.count(mod_param) <= 1


def test_mod_param_two(mod_param):
    assert _mod_setups.count(mod_param) <= 1


@pytest.fixture
def doubled(number):
    return number * 2


def test_doubled_through_chain(doubled, number):
    # ``number`` is reached both directly and via ``doubled``; both
    # must resolve to the same parametrized value.
    assert doubled == number * 2
