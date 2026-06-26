# mypy: ignore-errors

import asyncio
import unittest

from ggt._internal.cli import async_timeout


class ContextError(RuntimeError):
    def get_server_context(self):
        return "server-side context"


class ErrorContextCase(unittest.IsolatedAsyncioTestCase):
    def test_server_context_error(self):
        raise ContextError("client-side context")

    @async_timeout(1)
    async def test_async_cancelled_timeout_path(self):
        raise asyncio.CancelledError
