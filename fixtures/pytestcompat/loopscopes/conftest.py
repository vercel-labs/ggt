import asyncio
import contextvars
import os

import pytest_asyncio

pytest_plugins = ("loop_plugin",)


events = []
ctx = contextvars.ContextVar("ctx", default="unset")
loops = {}


def record(event):
    path = os.environ.get("GGT_LOOP_EVENTS")
    if path:
        # Multiple workers appending to one file can overwrite each other's
        # records on Windows, where append-mode writes are not atomic across
        # processes.  Keep each worker's lifecycle log independent.
        with open(f"{path}.{os.getpid()}", "a", encoding="utf-8") as stream:
            stream.write(event + "\n")


def _fixture(scope, loop_scope=None):
    @pytest_asyncio.fixture(scope=scope, loop_scope=loop_scope)
    async def value():
        loop = asyncio.get_running_loop()
        events.append(("setup", scope, id(loop), os.getpid()))
        record(f"setup:{scope}:{os.getpid()}:{id(loop)}")
        loops[scope] = loop
        yield loop
        assert asyncio.get_running_loop() is loop
        assert not loop.is_closed()
        events.append(("teardown", scope, id(loop), os.getpid()))
        record(f"teardown:{scope}:{os.getpid()}:{id(loop)}")

    return value


function_loop = _fixture("function")
class_loop = _fixture("class")
module_loop = _fixture("module")
package_loop = _fixture("package")
session_loop = _fixture("session")


@pytest_asyncio.fixture(scope="session")
async def session_context():
    ctx.set("fixture")
    return asyncio.get_running_loop(), ctx
