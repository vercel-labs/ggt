# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.


def pytest_ignore_collect(path, config):
    # The legacy py.path signature is not supported: the hook must be
    # reported as ignored and never called.
    raise AssertionError("must never be called")
