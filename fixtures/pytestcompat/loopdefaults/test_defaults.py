import asyncio


test_loop = None


async def test_default_scopes_a(configured_fixture_loop):
    global test_loop
    test_loop = asyncio.get_running_loop()
    assert test_loop is not configured_fixture_loop


async def test_default_scopes_b(configured_fixture_loop):
    assert asyncio.get_running_loop() is test_loop
    assert asyncio.get_running_loop() is not configured_fixture_loop
