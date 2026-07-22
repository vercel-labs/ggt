# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.


def test_fixture_from_ancestor_conftest(outer_fixture):
    # This directory has no __init__.py, so the module's sys.path
    # root is inner/ — but conftest.py discovery must still walk up
    # to the rootdir, like pytest.
    assert outer_fixture == "outer-value"
