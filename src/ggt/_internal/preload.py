# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

"""Forkserver warm-up.

Test-suite dependency graphs are often expensive to import, and every
forked worker pays that price again because the forkserver process is
deliberately clean.  This module removes that cost from the hot path:

- after discovery, the parent saves the list of loaded modules to
  ``.ggt_cache/preload.json``;
- on the *next* run, the fork server is started immediately at CLI
  startup, armed (via ``multiprocessing.set_forkserver_preload``) to
  import that cached list — concurrently with the parent's own test
  discovery, so the warm-up costs no wall-clock time;
- workers then fork from the warm server and inherit the imported
  modules through copy-on-write.

To keep those inherited pages actually shared, the server disables
the garbage collector before importing and calls ``gc.freeze()``
afterwards, moving the whole object graph into the permanent
generation; worker collections then never touch the inherited
objects' GC headers (see the ``gc.freeze`` documentation).  Workers
re-enable the collector in ``runner.init_worker``.

Import failures during warm-up are ignored — a stale cache entry is
simply imported later by whichever worker needs it.  ``--no-preload``
disables the mechanism for suites whose dependencies are not
fork-safe at import time (e.g. modules that start background threads
when imported).
"""

from __future__ import annotations

import gc
import importlib
import json
import multiprocessing
import multiprocessing.forkserver
import os
import pathlib
import sys

ENV_MODULES = "GGT_PRELOAD_MODULES"
CACHE_DIR = ".ggt_cache"
CACHE_FILE = "preload.json"


def compute_preload_modules() -> list[str]:
    """The importable, non-stdlib modules currently loaded."""
    stdlib = sys.stdlib_module_names
    names: list[str] = []
    for name, mod in sys.modules.items():
        top = name.partition(".")[0]
        if top in stdlib:
            continue
        if top.startswith("__"):
            # __main__, __mp_main__, mangled conftest modules.
            continue
        if mod is None or getattr(mod, "__file__", None) is None:
            continue
        names.append(name)
    return sorted(names)


def _cache_path() -> pathlib.Path:
    return pathlib.Path(CACHE_DIR) / CACHE_FILE


def save_module_cache() -> None:
    """Record the post-discovery module set for the next run."""
    try:
        cache_dir = pathlib.Path(CACHE_DIR)
        cache_dir.mkdir(exist_ok=True)
        gitignore = cache_dir / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("*\n", encoding="utf-8")
        _cache_path().write_text(
            json.dumps(compute_preload_modules()), encoding="utf-8"
        )
    except OSError:
        pass


def load_module_cache() -> list[str]:
    try:
        data = json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [str(name) for name in data]


def start_forkserver(modules: list[str]) -> None:
    """Arm the fork server's preload and start it right away.

    Called from the CLI before test discovery; the server imports the
    cached module list in parallel with the parent's discovery work.
    Without a cached list only the preload hook is armed (the server
    then starts lazily, exactly as before).
    """
    multiprocessing.set_forkserver_preload(["ggt._internal.preload"])

    if not modules:
        return

    os.environ[ENV_MODULES] = json.dumps(modules)
    try:
        # Semi-private but stable; the same call popen_forkserver
        # makes for every worker fork.
        multiprocessing.forkserver.ensure_running()
    except Exception:  # noqa: S110
        # The pool will start the server on demand instead.
        pass
    finally:
        # Only the just-started server must see the list: the parent
        # and any worker re-importing this module treat it as absent.
        os.environ.pop(ENV_MODULES, None)


def _preload() -> None:
    """Executed on import inside the fork server."""
    raw = os.environ.pop(ENV_MODULES, None)
    if not raw:
        return

    try:
        names = json.loads(raw)
    except ValueError:
        return
    if not isinstance(names, list):
        return

    # Avoid creating freed holes in memory pages while building the
    # long-lived object graph (see gc.freeze docs).
    gc.disable()

    for name in names:
        try:
            importlib.import_module(str(name))
        except BaseException:  # noqa: S112
            continue

    # Move everything to the permanent generation so that worker GC
    # runs never touch the inherited objects (maximizing COW page
    # sharing).  Workers re-enable the collector in init_worker.
    gc.freeze()


_preload()
