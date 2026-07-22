# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

import pytest


@pytest.fixture(params=["asyncio", "trio"])
def anyio_backend(request):
    return request.param
