# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

"""Forkserver warm-up.

Test-suite dependency graphs are often expensive to import, and every
forked worker pays that price again because the forkserver process is
deliberately clean.  This module removes that cost from the hot path:

- after discovery, the parent saves the list of loaded modules to
  ``preload-<env fingerprint>.json`` in the cache directory (a
  format-versioned subdirectory of ``.ggt_cache``);
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

The warm-up must never take the fork server down with it: the server
only tolerates ``ImportError`` from preload imports, and a server
that dies after workers began connecting surfaces as an opaque
``EOFError`` at pool startup.  Three layers guard against that:

- cache filenames carry a digest of an environment fingerprint
  (interpreter, prefix, ``PYTHONPATH``, this module's origin), so a
  cache written by a different environment is never even read — it
  can neither crash the server nor warm it with modules resolved
  from the wrong tree; the full fingerprint is also recorded in the
  payload and verified at load time;
- the origin-validation pass never evicts *this* module, whose import
  is still executing — removing a mid-import module from
  ``sys.modules`` violates an importlib invariant and raises
  ``KeyError`` inside the import machinery;
- ``_preload()`` is invoked under a catch-all, because a cold server
  is always preferable to a dead one.
"""

from __future__ import annotations

import gc
import hashlib
import importlib
import json
import multiprocessing
import multiprocessing.forkserver
import os
import pathlib
import sys
import warnings

ENV_MODULES = "GGT_PRELOAD_MODULES"
CACHE_DIR = ".ggt_cache"

# Format version of the cache contents.  All cache files live in a
# subdirectory named after it, so bumping it when a recorded structure
# changes incompatibly makes every older cache invisible (rather than
# merely rejected).  Deliberately independent of the ggt version:
# upgrading ggt in place keeps module paths (and therefore the
# environment fingerprint) stable, so only an explicit version bump
# reliably invalidates old caches.
CACHE_VERSION = 1
_CACHE_SUBDIR = f"v{CACHE_VERSION}"

_CACHEDIR_TAG_FILE = "CACHEDIR.TAG"
# https://bford.info/cachedir/ — the signature line must be the first
# 43 bytes of the file for backup and sync tools to recognize it.
_CACHEDIR_TAG_CONTENT = (
    "Signature: 8a477f597d28d172789f06886806bc55\n"
    "# This file is a cache directory tag created by ggt.\n"
    "# For information about cache directory tags see:\n"
    "#   https://bford.info/cachedir/\n"
)


def _env_fingerprint() -> dict[str, str]:
    """Identify the environment a preload cache was recorded in.

    A cache recorded by a different interpreter, virtualenv, or module
    search path must not be replayed: the recorded ``sys.path`` would
    make the fork server import modules from trees the current run did
    not select, and a mismatched origin for this very module would
    poison the server's warm-up import (see module docstring).
    """
    return {
        "executable": sys.executable,
        "prefix": sys.prefix,
        "pythonpath": os.environ.get("PYTHONPATH", ""),
        "preload_origin": __file__,
    }


def env_fingerprint() -> str:
    """Stable filename-safe digest of the environment fingerprint.

    Cache filenames carry this digest, so caches written by different
    environments never read — or clobber — each other; a mismatched
    environment simply resolves to a file that does not exist.
    """
    payload = json.dumps(_env_fingerprint(), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def compute_preload_state() -> dict[str, object]:
    """The importable, non-stdlib modules currently loaded.

    Records each module's origin file so the fork server can verify
    that a name resolves to the same file in its own context, and the
    parent's sys.path so the server resolves names the same way.
    """
    stdlib = sys.stdlib_module_names
    modules: list[list[str]] = []
    # Snapshot: reading __file__ below may trigger a module __getattr__
    # that lazily imports and mutates sys.modules mid-iteration.
    for name, mod in list(sys.modules.items()):
        top = name.partition(".")[0]
        if top in stdlib:
            continue
        if top.startswith("__"):
            # __main__, __mp_main__, mangled conftest modules.
            continue
        origin = getattr(mod, "__file__", None) if mod is not None else None
        if origin is None:
            continue
        modules.append([name, origin])
    modules.sort()
    return {
        "env": _env_fingerprint(),
        "sys_path": list(sys.path),
        "modules": modules,
    }


def _cache_path() -> pathlib.Path:
    return (
        pathlib.Path(CACHE_DIR)
        / _CACHE_SUBDIR
        / f"preload-{env_fingerprint()}.json"
    )


def ensure_cache_dir() -> pathlib.Path | None:
    """Create (if needed) and return the ggt cache directory.

    The returned directory is the format-versioned subdirectory all
    cache files are written to; the root holds only the markers that
    apply to every version (``.gitignore`` and the ``CACHEDIR.TAG``
    backup-exclusion tag).
    """
    try:
        root = pathlib.Path(CACHE_DIR)
        root.mkdir(exist_ok=True)
        gitignore = root / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("*\n", encoding="utf-8")
        cachedir_tag = root / _CACHEDIR_TAG_FILE
        if not cachedir_tag.exists():
            cachedir_tag.write_text(_CACHEDIR_TAG_CONTENT, encoding="utf-8")
        cache_dir = root / _CACHE_SUBDIR
        cache_dir.mkdir(exist_ok=True)
    except OSError:
        return None
    return cache_dir


def save_module_cache() -> None:
    """Record the post-discovery module set for the next run."""
    if ensure_cache_dir() is None:
        return
    path = _cache_path()
    # Write-and-rename so a concurrent ggt run in the same directory
    # (same environment, hence same filename) never reads torn JSON.
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        tmp.write_text(json.dumps(compute_preload_state()), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def load_module_cache() -> dict[str, object] | None:
    try:
        data = json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("modules"):
        return None
    if data.get("env") != _env_fingerprint():
        # Recorded by a different environment; replaying it would warm
        # the fork server with modules from the wrong tree.  Treat as
        # absent — this run rewrites the cache after discovery.
        return None
    return data


def start_forkserver(state: dict[str, object] | None) -> None:
    """Arm the fork server's preload and start it right away.

    Called from the CLI before test discovery; the server imports the
    cached module list in parallel with the parent's discovery work.
    Without a cached list only the preload hook is armed (the server
    then starts lazily, exactly as before).
    """
    multiprocessing.set_forkserver_preload(["ggt._internal.preload"])

    if not state:
        return

    os.environ[ENV_MODULES] = json.dumps(state)
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
        state = json.loads(raw)
    except ValueError:
        return
    if not isinstance(state, dict):
        return
    modules = state.get("modules")
    if not isinstance(modules, list):
        return

    # Resolve names exactly like the run that recorded them: the
    # recorded sys.path takes precedence (in its original order) over
    # whatever this process inherited.
    recorded_path = state.get("sys_path")
    if isinstance(recorded_path, list):
        recorded = [str(entry) for entry in recorded_path]
        known = set(recorded)
        sys.path[:] = recorded + [
            entry for entry in sys.path if entry not in known
        ]
        importlib.invalidate_caches()

    # Avoid creating freed holes in memory pages while building the
    # long-lived object graph (see gc.freeze docs).
    gc.disable()

    # Test modules in the list must be imported through the
    # assertion-rewriting hook, exactly as workers would import them.
    from . import pytest_compat  # noqa: PLC0415

    if pytest_compat.is_enabled():
        pytest_compat.install_assertion_rewriting()

    # These imports are speculative warm-up work, not part of the test run.
    # Do not leak dependency warnings directly from the fork server; modules
    # imported normally by a worker retain the process's usual warning policy.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for entry in modules:
            try:
                name, _origin = entry
                importlib.import_module(str(name))
            except BaseException:  # noqa: S112
                continue

    # Validate origins in a post-pass: a mismatched module must be
    # evicted so workers import the right file from scratch instead
    # of inheriting a poisoned sys.modules entry.  This runs after
    # the import loop because importing a submodule re-imports its
    # parent packages as a side effect, potentially re-poisoning an
    # entry that was checked earlier.
    own_package = __name__.partition(".")[0]
    for entry in modules:
        try:
            name, origin = entry
        except ValueError:
            continue
        if name == own_package or name.startswith(f"{own_package}."):
            # ggt's own tree is exempt from eviction, for two reasons.
            # This very module's import is executing right now:
            # importlib pops and re-inserts sys.modules[name] once
            # exec_module returns, and binds the child attribute via
            # an unguarded sys.modules[parent] lookup (Python <=
            # 3.13), so evicting it or its parent packages raises
            # KeyError inside the import machinery and kills the fork
            # server.  And evicting any *other* ggt module splits
            # ggt into two generations of module objects — the
            # server's machinery keeps references to the old one
            # while workers re-import a new one, breaking pickling's
            # identity checks.  Neither is ever necessary: ggt
            # modules resolve through the already-imported package
            # objects of the current tree, so the in-memory copy is
            # the correct one regardless of what a stale cache
            # recorded.
            continue
        mod = sys.modules.get(str(name))
        if mod is not None and getattr(mod, "__file__", None) != origin:
            sys.modules.pop(str(name), None)

    # Move everything to the permanent generation so that worker GC
    # runs never touch the inherited objects (maximizing COW page
    # sharing).  Workers re-enable the collector in init_worker.
    gc.freeze()


try:
    _preload()
except Exception:  # noqa: S110
    # Warm-up is strictly best-effort.  An exception escaping this
    # module kills the fork server (forkserver.main only tolerates
    # ImportError from preload imports), and a server that dies while
    # workers are connecting fails the whole run with an opaque
    # EOFError at pool startup.  A cold server beats a dead one.
    pass
