# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

import logging
import os
import warnings

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


@pytest.mark.slow
def test_marked_slow():
    assert True


@pytest.mark.slow
@pytest.mark.integration
def test_marked_slow_integration():
    assert True


def test_unmarked():
    assert True
