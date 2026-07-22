# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

"""pytest compatibility layer.

Allows ggt to discover and run pytest-style test suites (bare ``test_*``
functions, non-unittest ``Test*`` classes, xunit-style setup hooks) by
synthesizing ``unittest.TestCase`` classes at collection time.

The layer is enabled by default when pytest is importable (i.e. ggt was
installed with the ``[pytest]`` extra) and can be turned off with the
``--no-pytest`` command line flag.  The effective setting is exported to
worker processes via the ``GGT_PYTEST_COMPAT`` environment variable,
because workers re-import test modules and must re-synthesize the same
TestCase classes deterministically.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .inicfg import IniConfig

ENV_FLAG = "GGT_PYTEST_COMPAT"

_enabled: bool | None = None


def pytest_available() -> bool:
    return importlib.util.find_spec("pytest") is not None


def set_enabled(*, enabled: bool) -> None:
    """Set the pytest compatibility mode for this process and its workers."""
    global _enabled  # noqa: PLW0603
    _enabled = enabled
    # Workers inherit the environment, letting them know they must
    # re-synthesize TestCase classes when restoring pickled tests.
    os.environ[ENV_FLAG] = "1" if enabled else "0"


def is_enabled() -> bool:
    global _enabled  # noqa: PLW0603
    if _enabled is None:
        env = os.environ.get(ENV_FLAG)
        _enabled = env == "1" if env is not None else pytest_available()
    return _enabled


def install_assertion_rewriting() -> None:
    """Install pytest's assertion introspection (idempotent).

    Must run before test modules are imported.
    """
    # Imported lazily: the rewrite module (and pytest underneath it)
    # only load when compatibility mode is actually on.
    from . import rewrite  # noqa: PLC0415

    rewrite.install()


def load_ini_config() -> IniConfig:
    """Load the pytest ini-option subset and export it to workers."""
    from . import inicfg  # noqa: PLC0415

    return inicfg.initialize(pathlib.Path.cwd())


def export_options(options: Mapping[str, str]) -> None:
    """Expose ggt's -X options to request.config in all processes."""
    os.environ["GGT_PYTEST_OPTIONS"] = json.dumps(dict(options))


def worker_init() -> None:
    """Per-worker initialization for pytest compatibility support.

    Runs in every worker before the first task (and therefore the
    first test module import) so that assertion rewriting applies to
    the modules the worker re-imports.
    """
    if is_enabled():
        install_assertion_rewriting()
