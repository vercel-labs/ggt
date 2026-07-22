# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

import sys


def test_rich_assertion_message():
    assert [1, 2] == [1, 3]


def test_conftest_assert_rewritten(checker):
    checker("left", "right")


def test_capsys(capsys):
    print("hello out")
    print("hello err", file=sys.stderr)
    captured = capsys.readouterr()
    assert captured.out == "hello out\n"
    assert captured.err == "hello err\n"
    print("second")
    assert capsys.readouterr().out == "second\n"
