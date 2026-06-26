# mypy: ignore-errors
# ruff: noqa: B028, PLC2701

import asyncio
import unittest
import warnings

from ggt._internal.cli import async_timeout, not_implemented
from ggt._internal.cli import skip, xerror, xfail


class Outcomes(unittest.IsolatedAsyncioTestCase):
    def test_failure(self):
        self.fail("planned failure")

    def test_error(self):
        raise RuntimeError("planned error")

    @skip("planned skip")
    def test_skip(self):
        pass

    @xfail("known failure")
    def test_expected_failure(self):
        self.fail("expected")

    @xfail("unexpected pass")
    def test_unexpected_success(self):
        pass

    @xerror("known error")
    def test_expected_error(self):
        raise RuntimeError("expected")

    @not_implemented("todo")
    def test_not_implemented(self):
        self.fail("todo")

    @async_timeout(1)
    async def test_async_timeout(self):
        await asyncio.sleep(2)

    def test_warning(self):
        warnings.warn("careful", UserWarning)
