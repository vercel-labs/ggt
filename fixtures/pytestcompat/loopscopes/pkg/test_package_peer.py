import asyncio

import pytest


@pytest.mark.asyncio(loop_scope="package")
async def test_package_loop_peer(package_loop):
    assert asyncio.get_running_loop() is package_loop
