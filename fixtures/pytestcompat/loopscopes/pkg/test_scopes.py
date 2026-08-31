import asyncio

import pytest


@pytest.mark.asyncio(loop_scope="module")
async def test_module_loop_a(module_loop):
    assert asyncio.get_running_loop() is module_loop


@pytest.mark.asyncio(scope="module")
async def test_module_loop_b(module_loop):
    assert asyncio.get_running_loop() is module_loop


@pytest.mark.asyncio(loop_scope="package")
async def test_package_loop(package_loop):
    assert asyncio.get_running_loop() is package_loop


@pytest.mark.asyncio(loop_scope="session")
async def test_session_loop(session_loop, session_context):
    context_loop, context_var = session_context
    assert asyncio.get_running_loop() is session_loop is context_loop
    assert context_var.get() == "fixture"


async def test_function_loop(function_loop):
    assert asyncio.get_running_loop() is function_loop


class TestClassLoop:
    @pytest.mark.asyncio(loop_scope="class")
    async def test_a(self, class_loop):
        assert asyncio.get_running_loop() is class_loop

    @pytest.mark.asyncio(loop_scope="class")
    async def test_b(self, class_loop):
        assert asyncio.get_running_loop() is class_loop


def test_sync_consumer(session_loop):
    assert not session_loop.is_closed()


def test_sync_package_fixture(sync_package_loop_state):
    sync_package_loop_state.append("used")
