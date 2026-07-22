# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.


def helper() -> int:
    return 3


def test_simple_pass():
    assert 1 + 1 == 2


def test_with_helper():
    assert helper() == 3


def test_not_collected():
    raise AssertionError("must not be collected")


test_not_collected.__test__ = False


async def test_async_collected():
    assert helper() == 3


class TestGroup:
    def setup_method(self, method):
        self.prepared = method.__name__

    def test_group_one(self):
        assert self.prepared == "test_group_one"
        assert "leftover" not in self.__dict__
        self.leftover = True

    def test_group_two(self):
        assert self.prepared == "test_group_two"
        assert "leftover" not in self.__dict__
        self.leftover = True


class TestWithInit:
    def __init__(self):
        self.x = 1

    def test_with_init_not_collected(self):
        raise AssertionError("must not be collected")
