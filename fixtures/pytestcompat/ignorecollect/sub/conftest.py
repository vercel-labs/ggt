# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

from pathlib import Path


def pytest_ignore_collect(collection_path: Path) -> bool | None:
    # The nearest conftest gets the first say: returning False keeps
    # a file the outer conftest would ignore.
    if collection_path.name.startswith("test_ignored_but_kept"):
        return False
    return None
