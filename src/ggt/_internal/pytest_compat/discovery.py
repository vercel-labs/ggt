# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

"""pytest-aligned test file discovery.

Follows pytest's collection conventions:

- test files match ``test*.py`` (the unittest convention, a superset
  of pytest's ``test_*.py``) or ``*_test.py``;
- test directories do not need ``__init__.py`` files — module names
  are derived with pytest's default "prepend" import-mode semantics
  (the first non-package ancestor directory is put on ``sys.path``
  and the module name is the dotted path relative to it);
- directories matching pytest's default ``norecursedirs`` patterns
  are skipped;
- ``conftest.py`` files are imported top-down along the path from the
  ``sys.path`` root down to each test file's directory.

The path → module-name derivation is shared with the worker-side
restore logic: workers re-import test modules by name, so both
processes must agree on it.
"""

from __future__ import annotations

import fnmatch
import hashlib
import importlib
import importlib.util
import pathlib
import sys
import unittest
import warnings
from typing import TYPE_CHECKING

from .. import imputil
from . import collect, inicfg, rewrite

if TYPE_CHECKING:
    import types
    from collections.abc import Iterator

TEST_FILE_PATTERNS = collect.TEST_FILE_PATTERNS

# pytest's default norecursedirs, plus __pycache__.
NORECURSE_PATTERNS = (
    "*.egg",
    ".*",
    "_darcs",
    "build",
    "CVS",
    "dist",
    "node_modules",
    "venv",
    "{arch}",
    "__pycache__",
)

_conftest_cache: dict[pathlib.Path, types.ModuleType] = {}


def is_test_file(name: str) -> bool:
    return collect.is_test_file(name)


def _should_recurse(name: str) -> bool:
    return not any(fnmatch.fnmatch(name, pat) for pat in NORECURSE_PATTERNS)


def iter_test_files(root: pathlib.Path) -> Iterator[pathlib.Path]:
    """Yield test files under *root* in deterministic (sorted) order."""
    if root.is_file():
        # Explicitly named files are collected unconditionally.
        yield root
        return

    subdirs: list[pathlib.Path] = []
    for entry in sorted(root.iterdir()):
        if entry.is_dir():
            if _should_recurse(entry.name):
                subdirs.append(entry)
        elif is_test_file(entry.name):
            yield entry

    for subdir in subdirs:
        yield from iter_test_files(subdir)


def module_name_for(file: pathlib.Path) -> tuple[pathlib.Path, str]:
    """Derive (sys.path root, dotted module name) for a file.

    Implements pytest's default "prepend" import mode: walk up from
    the file while ``__init__.py`` exists, then join the remaining
    path parts with dots.
    """
    basedir = file.parent
    parts = [file.stem]
    while (basedir / "__init__.py").exists():
        parts.append(basedir.name)
        basedir = basedir.parent
    return basedir, ".".join(reversed(parts))


def _module_origin(mod: types.ModuleType) -> pathlib.Path | None:
    origin = getattr(mod, "__file__", None)
    if origin is None:
        return None
    return pathlib.Path(origin).resolve()


def _check_import_match(
    mod: types.ModuleType,
    modname: str,
    file: pathlib.Path,
) -> None:
    origin = _module_origin(mod)
    if origin != file.resolve():
        raise ImportError(
            f"import file mismatch: module {modname!r} resolved to "
            f"{origin} instead of {file}.  Two test files with the "
            f"same basename ended up with the same module name; add "
            f"__init__.py files to their directories or rename one "
            f"of the files."
        )


def import_test_file(file: pathlib.Path) -> types.ModuleType:
    """Import a test file using prepend import-mode semantics."""
    file = file.absolute()
    basedir, modname = module_name_for(file)

    existing = sys.modules.get(modname)
    if existing is not None:
        _check_import_match(existing, modname, file)
        return existing

    with imputil.sys_path(str(basedir)):
        mod = importlib.import_module(modname)

    _check_import_match(mod, modname, file)
    return mod


def _warn_ignored_hooks(mod: types.ModuleType, path: pathlib.Path) -> None:
    hooks = sorted(
        name
        for name, value in vars(mod).items()
        if name.startswith("pytest_") and callable(value)
    )
    if hooks:
        warnings.warn(
            f"{path} defines pytest plugin hooks, which ggt pytest "
            f"compatibility does not implement; ignoring: "
            f"{', '.join(hooks)}",
            stacklevel=2,
        )


def import_conftest(path: pathlib.Path) -> types.ModuleType:
    path = path.resolve()
    cached = _conftest_cache.get(path)
    if cached is not None:
        return cached

    basedir, modname = module_name_for(path)
    if "." in modname:
        # The conftest lives in a package: import it normally so that
        # relative imports within it keep working.
        mod = import_test_file(path)
    else:
        # A top-level conftest.py: plain "conftest" module names from
        # different directories would collide in sys.modules, so use
        # a name derived from the file location instead.
        digest = hashlib.blake2s(
            str(path).encode("utf-8"), digest_size=6
        ).hexdigest()
        unique_name = f"__ggt_conftest_{digest}"
        cached = sys.modules.get(unique_name)
        if cached is not None:
            mod = cached
        else:
            spec = importlib.util.spec_from_file_location(
                unique_name,
                path,
                loader=rewrite.loader_for_file(unique_name, str(path)),
            )
            if spec is None or spec.loader is None:
                raise ImportError(f"cannot load conftest file: {path}")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[unique_name] = mod
            with imputil.sys_path(str(basedir)):
                spec.loader.exec_module(mod)

    _warn_ignored_hooks(mod, path)
    _conftest_cache[path] = mod
    return mod


def conftest_directories(file: pathlib.Path) -> list[pathlib.Path]:
    """Directories whose conftest.py applies to *file*, nearest first.

    Walks up from the file's directory to the rootdir (where the
    pytest configuration file lives), matching pytest's conftest
    collection — package boundaries do not stop the walk.  Files
    outside the rootdir are bounded by their sys.path root instead.
    """
    file = file.absolute()
    basedir, _ = module_name_for(file)

    resolved = file.resolve()
    rootdir = pathlib.Path(inicfg.current().rootdir or ".").resolve()
    stop = rootdir if resolved.is_relative_to(rootdir) else basedir.resolve()

    directories: list[pathlib.Path] = []
    current = resolved.parent
    while True:
        directories.append(current)
        if current in {stop, current.parent}:
            break
        current = current.parent

    return directories


def load_conftests(file: pathlib.Path) -> None:
    """Import conftest.py files that apply to *file*, top-down."""
    for directory in reversed(conftest_directories(file)):
        conftest = directory / "conftest.py"
        if conftest.is_file():
            import_conftest(conftest)


def discover(
    entry: str,
    test_loader: unittest.TestLoader,
) -> unittest.TestSuite:
    """Discover tests under *entry* with pytest-style collection."""
    suite = unittest.TestSuite()
    root = pathlib.Path(entry).absolute()
    for file in iter_test_files(root):
        load_conftests(file)
        mod = import_test_file(file)
        suite.addTest(test_loader.loadTestsFromModule(mod))

    return suite
