# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

from pathlib import Path


def pytest_ignore_collect(collection_path: Path, config) -> bool | None:
    assert hasattr(config, "getoption")
    if collection_path.name == "skipdir":
        return True
    if collection_path.name.startswith("test_ignored"):
        return True
    return None
