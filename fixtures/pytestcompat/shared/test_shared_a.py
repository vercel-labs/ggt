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


@pytest.fixture(scope="module")
def shared_module():
    record("module-a-setup")
    # Large enough to exercise the temp-file spill path.
    return "A" * 100_000


@pytest.fixture(scope="module")
@ggt.local_fixture
def local_module():
    fixture_pid = os.getpid()
    return lambda: (23, fixture_pid)


def test_a_session(shared_session):
    assert shared_session["token"] == 42


def test_a_unpickleable(unpickleable_session):
    assert unpickleable_session() == 17


def test_a_local_dependent(local_dependent, shared_session):
    value, fixture_pid = local_dependent()
    assert value == 19
    if os.environ.get("GGT_PARALLEL") == "1":
        assert fixture_pid != shared_session["runner_pid"]


def test_a_local_module(local_module, shared_session):
    value, fixture_pid = local_module()
    assert value == 23
    if os.environ.get("GGT_PARALLEL") == "1":
        assert fixture_pid != shared_session["runner_pid"]


def test_a_module_one(shared_module):
    assert len(shared_module) == 100_000


def test_a_module_two(shared_module):
    assert shared_module[0] == "A"
