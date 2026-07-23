# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

import logging
import os
import warnings
from unittest import mock

import pytest


def test_caplog(caplog):
    logging.getLogger("x").warning("hello %s", "world")
    assert "hello world" in caplog.text
    assert caplog.messages == ["hello world"]
    assert caplog.record_tuples == [("x", logging.WARNING, "hello world")]
    caplog.clear()
    assert not caplog.records


def test_caplog_set_level(caplog):
    caplog.set_level(logging.INFO, logger="lowlevel")
    logging.getLogger("lowlevel").info("quiet message")
    assert "quiet message" in caplog.messages


def test_caplog_at_level(caplog):
    with caplog.at_level(logging.DEBUG, logger="chatty"):
        logging.getLogger("chatty").debug("dbg")
    logging.getLogger("chatty").debug("not captured")
    assert caplog.messages == ["dbg"]


def test_recwarn(recwarn):
    warnings.warn("boom", UserWarning)
    assert len(recwarn) == 1
    caught = recwarn.pop(UserWarning)
    assert "boom" in str(caught.message)
    assert len(recwarn) == 0


def test_monkeypatch_context(monkeypatch):
    with monkeypatch.context() as patcher:
        patcher.setenv("GGT_CTX_CHECK", "yes")
        assert os.environ["GGT_CTX_CHECK"] == "yes"
    assert "GGT_CTX_CHECK" not in os.environ


def test_capsys_disabled(capsys):
    print("captured")
    with capsys.disabled():
        print("GGT-DISABLED-MARKER")
    print("more")
    assert capsys.readouterr().out == "captured\nmore\n"


@pytest.mark.speed("fast")
def test_marker_lookup(marker_probe):
    assert marker_probe == "fast"


def test_marker_absent(marker_probe):
    assert marker_probe == "unmarked"


def test_config_getoption(request):
    assert request.config.getoption("--missing", "fallback") == "fallback"
    # ggt's -X options back request.config.
    assert request.config.getoption("color") == "blue"
    assert request.config.getoption("--color") == "blue"


def _patch_me():
    return "real"


def _patch_me_too():
    return "real too"


@mock.patch(f"{__name__}._patch_me")
def test_mock_patch_arg(mock_helper):
    mock_helper.return_value = "mocked"
    assert _patch_me() == "mocked"


@mock.patch(f"{__name__}._patch_me")
@mock.patch(f"{__name__}._patch_me_too")
def test_mock_patch_stacked(mock_too, mock_helper):
    mock_helper.return_value = "mocked"
    mock_too.return_value = "mocked too"
    assert _patch_me() == "mocked"
    assert _patch_me_too() == "mocked too"


@mock.patch(f"{__name__}._patch_me")
def test_mock_patch_with_fixture(mock_helper, monkeypatch):
    monkeypatch.setenv("GGT_MOCK_FIXTURE_CHECK", "yes")
    mock_helper.return_value = "mocked"
    assert _patch_me() == "mocked"
    assert os.environ["GGT_MOCK_FIXTURE_CHECK"] == "yes"


@mock.patch(f"{__name__}._patch_me", new=lambda: "swapped")
def test_mock_patch_explicit_new(monkeypatch):
    # An explicit replacement consumes no test argument; the
    # remaining parameter is a real fixture request.
    monkeypatch.setenv("GGT_MOCK_NEW_CHECK", "yes")
    assert _patch_me() == "swapped"
    assert os.environ["GGT_MOCK_NEW_CHECK"] == "yes"


class TestMockPatchMethods:
    @mock.patch(f"{__name__}._patch_me")
    @mock.patch(f"{__name__}._patch_me_too")
    def test_mock_patch_method(self, mock_too, mock_helper):
        mock_helper.return_value = "mocked"
        mock_too.return_value = "mocked too"
        assert _patch_me() == "mocked"
        assert _patch_me_too() == "mocked too"


@pytest.mark.slow
def test_marked_slow():
    assert True


@pytest.mark.slow
@pytest.mark.integration
def test_marked_slow_integration():
    assert True


def test_unmarked():
    assert True
