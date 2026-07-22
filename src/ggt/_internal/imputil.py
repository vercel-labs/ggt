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
    """Modify sys.path by temporarily placing the given entry in front"""
    orig_sys_path = sys.path[:]
    paths_set = {*paths}
    set_sys_path([*paths, *(p for p in orig_sys_path if p not in paths_set)])
    try:
        yield
    finally:
        set_sys_path(orig_sys_path)
