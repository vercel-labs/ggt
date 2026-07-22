# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

import pytest
import sniffio


@pytest.mark.anyio
async def test_default_backend(anyio_backend_name):
    # Without a user anyio_backend fixture, the builtin default
    # applies: parametrized over every installed backend, like
    # anyio's own plugin.
    assert sniffio.current_async_library() == anyio_backend_name
