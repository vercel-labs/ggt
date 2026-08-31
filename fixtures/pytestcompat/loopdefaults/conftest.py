import asyncio

import pytest_asyncio


@pytest_asyncio.fixture(scope="module")
async def configured_fixture_loop():
    return asyncio.get_running_loop()
