# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

import pytest


@pytest.fixture(params=[1, 2])
def parametrized_fixture(request):
    return request.param


def test_dynamic_parametrized(request):
    # Parametrized fixtures must be statically reachable; dynamic
    # requests cannot be expanded at collection time.
    request.getfixturevalue("parametrized_fixture")
