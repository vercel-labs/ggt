# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

import os
import pathlib

import pytest

_events = os.environ.get("GGT_FUNCTIONAL_EVENTS")
if _events:
    _path = pathlib.Path(_events)
    _path.parent.mkdir(parents=True, exist_ok=True)
    with open(_path, "a", encoding="utf-8") as _f:
        _f.write("conftest-imported\n")


@pytest.fixture
def conftest_fixture():
    return "from-conftest"


@pytest.fixture
def overridden_fixture():
    return "outer"
