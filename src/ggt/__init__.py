# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

import importlib.metadata

from ._internal.decorators import (
    async_timeout,
    local_fixture,
    not_implemented,
    skip,
    xerror,
    xfail,
)
from ._internal.marks import mark

__all__ = (
    "async_timeout",
    "local_fixture",
    "mark",
    "not_implemented",
    "skip",
    "xerror",
    "xfail",
)

# The version is derived from the git tag at build time
# (uv-dynamic-versioning); releases are cut by pushing a tag, with no
# version-bump commit.
try:
    __version__ = importlib.metadata.version("ggt")
except importlib.metadata.PackageNotFoundError:  # uninstalled source tree
    __version__ = "0+unknown"
