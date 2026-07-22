# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

import pytest


@pytest.fixture
def checker():
    def check(a, b):
        assert a == b

    return check
