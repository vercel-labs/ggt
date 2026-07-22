# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

import anyio
import pytest
import sniffio

pytestmark = pytest.mark.anyio


@pytest.fixture
async def async_resource():
    await anyio.sleep(0)
    return 5


async def test_backend_matches(anyio_backend_name):
    # The test must actually run on the parametrized backend.
    assert sniffio.current_async_library() == anyio_backend_name


async def test_anyio_sleep():
    await anyio.sleep(0)


async def test_async_fixture_on_backend(async_resource, anyio_backend):
    assert async_resource == 5
    assert anyio_backend in ("asyncio", "trio")


def test_sync_not_duplicated():
    # Sync tests are not expanded per backend even under a
    # module-level anyio mark.
    assert True
