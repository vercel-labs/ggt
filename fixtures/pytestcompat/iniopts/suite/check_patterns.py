# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

import os


def check_usefixtures_applied():
    assert os.environ.get("GGT_INI_USEFIXTURES") == "on"


def check_asserts_are_rewritten():
    assert True


def test_not_collected():
    # python_functions = check_* excludes the default test_ prefix.
    raise AssertionError("must not be collected")


class CheckGroup:
    def check_method(self):
        assert True


class TestOldNaming:
    def check_never(self):
        raise AssertionError("must not be collected")
