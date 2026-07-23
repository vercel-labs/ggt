# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

import os

import pytest

_module_events = []
_autouse_runs = []
_finalized = []


@pytest.fixture(scope="module")
def module_fixture():
    _module_events.append("setup")
    yield "modval"
    _module_events.append("teardown")


@pytest.fixture
def function_fixture(module_fixture):
    return f"{module_fixture}+func"


@pytest.fixture(autouse=True)
def autouse_fixture(request):
    _autouse_runs.append(request.node.name)
    yield


@pytest.fixture
def overridden_fixture():
    return "inner"


def test_monkeypatch_a_sets(function_fixture, tmp_path, monkeypatch):
    assert function_fixture == "modval+func"
    assert tmp_path.is_dir()
    monkeypatch.setenv("GGT_PYTEST_MP_CHECK", "on")
    assert os.environ["GGT_PYTEST_MP_CHECK"] == "on"


def test_monkeypatch_b_undone():
    # The monkeypatch from test_monkeypatch_a_sets (which sorts
    # before this test) must have been undone.
    assert "GGT_PYTEST_MP_CHECK" not in os.environ


def test_module_fixture_cached(module_fixture):
    assert module_fixture == "modval"
    # The fixture either ran in the runner process (shared fixture
    # transport, in which case this process never ran it) or at most
    # once in this process.
    assert _module_events.count("setup") <= 1


def test_autouse_ran():
    assert "test_autouse_ran" in _autouse_runs


def test_override_and_conftest(overridden_fixture, conftest_fixture):
    assert overridden_fixture == "inner"
    assert conftest_fixture == "from-conftest"


def test_monkeypatch_unknown_import_path(monkeypatch):
    with pytest.raises(ImportError):
        monkeypatch.setattr("ggt_no_such_module_exists.attr", None)


def test_capsys_has_real_encoding(capsys):
    import sys

    print("capsys encoding check".encode(sys.stdout.encoding, "replace"))
    assert capsys.readouterr().out.startswith("b'capsys")


def test_request_features(request, tmp_path_factory):
    assert request.node.name == "test_request_features"
    assert request.getfixturevalue("conftest_fixture") == "from-conftest"
    request.addfinalizer(lambda: _finalized.append("fin"))
    assert tmp_path_factory.getbasetemp().is_dir()


class TestClassFixtures:
    @pytest.fixture
    def class_fixture(self, conftest_fixture):
        return f"{conftest_fixture}+class"

    def test_class_fixture(self, class_fixture):
        assert class_fixture == "from-conftest+class"
