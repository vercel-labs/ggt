# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

import pytest


def pytest_collection_modifyitems(items):
    # An inert plugin hook: ggt must warn that it is ignored.
    raise AssertionError("must never be called")


@pytest.fixture
def marker_probe(request):
    marker = request.node.get_closest_marker("speed")
    return marker.args[0] if marker is not None else "unmarked"
