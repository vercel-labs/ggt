# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

import pytest


@pytest.fixture(scope="session")
async def async_session_fixture():
    return 1


@pytest.fixture
async def plain_async_fixture():
    return 2


async def test_async_session_scope(async_session_fixture):
    raise AssertionError("must not run")


def test_sync_wants_async_fixture(plain_async_fixture):
    raise AssertionError("must not run")
