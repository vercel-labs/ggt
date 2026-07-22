# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

import asyncio

import pytest


@pytest.fixture
def sync_uses_async(async_value):
    # A sync fixture may depend on an async fixture when the
    # requesting test is async.
    return async_value + 1


async def test_async_fixture(async_value):
    await asyncio.sleep(0)
    assert async_value == 7


async def test_async_gen_fixture(async_gen_fixture):
    value, loop = async_gen_fixture
    assert value == "agen"
    assert asyncio.get_running_loop() is loop


async def test_sync_fixture_with_async_dep(sync_uses_async):
    assert sync_uses_async == 8


def test_sync_in_async_module(tmp_path):
    assert tmp_path.is_dir()


@pytest.mark.parametrize("n", [1, 2])
async def test_async_parametrized(n):
    await asyncio.sleep(0)
    assert n in (1, 2)


@pytest.mark.xfail(reason="known bad")
async def test_async_xfail():
    await asyncio.sleep(0)
    assert False  # noqa: B011


async def test_async_imperative_skip():
    pytest.skip("not today")


class TestAsyncMethods:
    def setup_method(self, method):
        self.prepared = method.__name__

    async def test_async_method(self, async_value):
        await asyncio.sleep(0)
        assert async_value == 7
        assert self.prepared == "test_async_method"

    def test_sync_method_same_class(self):
        assert self.prepared == "test_sync_method_same_class"
