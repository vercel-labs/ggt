# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.


from __future__ import annotations

import contextlib
import importlib
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator


def set_sys_path(entries: list[str]) -> None:
    sys.path[:] = entries
    importlib.invalidate_caches()


@contextlib.contextmanager
def sys_path(*paths: str) -> Iterator[None]:
    """Temporarily prepend paths without discarding import-time additions."""
    orig_sys_path = sys.path[:]
    paths_set = {*paths}
    scoped_sys_path = [
        *paths,
        *(path for path in orig_sys_path if path not in paths_set),
    ]
    set_sys_path(scoped_sys_path)
    try:
        yield
    finally:
        scoped_paths = set(scoped_sys_path)
        additions = [path for path in sys.path if path not in scoped_paths]
        set_sys_path([*additions, *orig_sys_path])
