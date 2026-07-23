# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

import os
import pathlib

import ggt
import pytest


def record(event):
    path = os.environ.get("GGT_FUNCTIONAL_EVENTS")
    if path:
        p = pathlib.Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(event + "\n")


@pytest.fixture(scope="session")
def shared_session():
    record("session-setup")
    yield {"token": 42, "runner_pid": os.getpid()}
    record("session-teardown")


@pytest.fixture(scope="session")
def unpickleable_session():
    record("unpickleable-setup")
    return lambda: 17


@ggt.local_fixture
@pytest.fixture(scope="session")
def local_unpickleable_session():
    fixture_pid = os.getpid()
    return lambda: (19, fixture_pid)


@pytest.fixture(scope="session")
def local_dependent(local_unpickleable_session):
    return local_unpickleable_session
