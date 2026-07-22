# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

import anyio
import pytest
import sniffio
from hypothesis import given, settings
from hypothesis import strategies as st

pytestmark = pytest.mark.anyio


@settings(max_examples=5, deadline=None)
@given(n=st.integers(min_value=0, max_value=10))
async def test_hypothesis_async_bridge(anyio_backend_name, n):
    # Each generated example runs on the selected anyio backend.
    await anyio.sleep(0)
    assert sniffio.current_async_library() == anyio_backend_name
    assert 0 <= n <= 10
