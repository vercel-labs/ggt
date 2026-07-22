# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

"""pytest-style assertion introspection.

Installs a thin ggt-owned meta-path finder that applies pytest's
assertion-rewriting AST pass (``_pytest.assertion.rewrite``) to test
files and conftest.py files as they are imported.  Unlike pytest's own
``AssertionRewritingHook`` this does not require a pytest ``Config``.

Rewritten bytecode is cached in ``__pycache__`` under a ggt-specific
tag (``<cache_tag>-ggt-<pytest version>.pyc``), so it can never clash
with regular pyc files or with pytest's own rewrite cache over the
same tree, while repeat runs — and every worker process — skip the
AST-rewrite and compile steps entirely.  Cache entries are validated
against the source's mtime and size (the same scheme CPython and
pytest use) and written atomically so concurrent workers cannot
corrupt them.

The hook must be installed before test modules are imported: the
parent process installs it right after enabling pytest compatibility
(before discovery), and workers install it from ``worker_init`` before
the first task is unpickled.

If pytest is not importable the hook degrades to plain asserts.
"""

from __future__ import annotations

import ast
import importlib.abc
import importlib.machinery
import importlib.metadata
import importlib.util
import marshal
import os
import pathlib
import sys
import types
from typing import TYPE_CHECKING, Any

from . import collect

if TYPE_CHECKING:
    from collections.abc import Sequence

CACHE_ENV_DISABLE = "GGT_PYTEST_REWRITE_CACHE"


def _rewrite_cache_enabled() -> bool:
    return (
        not sys.dont_write_bytecode
        and os.environ.get(CACHE_ENV_DISABLE) != "0"
    )


_pyc_tail: str | None = None


def _get_pyc_tail() -> str:
    """The cache filename suffix, e.g. ``cpython-314-ggt-8.4.2.pyc``.

    Includes the pytest version because the rewritten code is a
    product of pytest's rewriter.
    """
    global _pyc_tail  # noqa: PLW0603
    if _pyc_tail is None:
        try:
            pytest_version = importlib.metadata.version("pytest")
        except importlib.metadata.PackageNotFoundError:
            pytest_version = "unknown"
        _pyc_tail = f"{sys.implementation.cache_tag}-ggt-{pytest_version}.pyc"
    return _pyc_tail


def _pyc_path(source_path: pathlib.Path) -> pathlib.Path:
    return (
        source_path.parent
        / "__pycache__"
        / f"{source_path.stem}.{_get_pyc_tail()}"
    )


def _read_pyc(
    source_stat: os.stat_result,
    pyc_path: pathlib.Path,
) -> types.CodeType | None:
    """Read cached code if it is up to date with the source."""
    try:
        data = pyc_path.read_bytes()
    except OSError:
        return None

    if len(data) <= 16 or data[:4] != importlib.util.MAGIC_NUMBER:
        return None
    if int.from_bytes(data[4:8], "little") != 0:
        return None
    mtime = int.from_bytes(data[8:12], "little")
    size = int.from_bytes(data[12:16], "little")
    if (
        mtime != int(source_stat.st_mtime) & 0xFFFFFFFF
        or size != source_stat.st_size & 0xFFFFFFFF
    ):
        return None

    try:
        code = marshal.loads(data[16:])  # noqa: S302
    except Exception:
        return None

    return code if isinstance(code, types.CodeType) else None


def _write_pyc(
    code: types.CodeType,
    source_stat: os.stat_result,
    pyc_path: pathlib.Path,
) -> None:
    """Atomically write a cache entry; failures are non-fatal."""
    blob = b"".join(
        (
            importlib.util.MAGIC_NUMBER,
            (0).to_bytes(4, "little"),
            (int(source_stat.st_mtime) & 0xFFFFFFFF).to_bytes(4, "little"),
            (source_stat.st_size & 0xFFFFFFFF).to_bytes(4, "little"),
            marshal.dumps(code),
        )
    )
    tmp_path = pyc_path.with_name(f"{pyc_path.name}.{os.getpid()}~")
    try:
        pyc_path.parent.mkdir(exist_ok=True)
        tmp_path.write_bytes(blob)
        os.replace(tmp_path, pyc_path)
    except OSError:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def _pytest_rewrite_modules() -> tuple[Any, Any] | None:
    try:
        # Imported lazily so that ggt does not pull in pytest unless
        # assertion rewriting is actually enabled.
        from _pytest.assertion import rewrite  # noqa: PLC0415, PLC2701
        from _pytest.assertion import util  # noqa: PLC0415, PLC2701
    except ImportError:
        return None
    return rewrite, util


class _TerminalWriterStub:
    def _highlight(
        self,
        source: str,
        lexer: str = "python",
    ) -> str:
        return source


class _ConfigStub:
    """The minimal Config surface used by assertrepr_compare."""

    def get_verbosity(self, *args: Any) -> int:
        return 0

    def getoption(self, name: str, default: object = None) -> object:
        return default

    def get_terminal_writer(self) -> _TerminalWriterStub:
        return _TerminalWriterStub()


def _install_reprcompare(util_mod: Any) -> None:
    config = _ConfigStub()

    def reprcompare(op: str, left: object, right: object) -> str | None:
        try:
            expl: Sequence[str] | None = util_mod.assertrepr_compare(
                config, op, left, right
            )
        except Exception:
            return None
        if not expl:
            return None
        return "\n~".join(expl).replace("%", "%%")

    util_mod._reprcompare = reprcompare


def should_rewrite(filename: str) -> bool:
    return filename == "conftest.py" or collect.is_test_file(filename)


class RewriteLoader(importlib.machinery.SourceFileLoader):
    """Compiles matching sources through pytest's assertion rewriter.

    The regular pyc mechanism is not used (a stale pyc compiled
    without rewriting must never be loaded, and rewritten code must
    not pollute the normal cache); instead, rewritten bytecode lives
    in a ggt-tagged sidecar cache validated against the source.
    """

    def get_code(self, fullname: str) -> types.CodeType:
        cache_enabled = _rewrite_cache_enabled()
        source_stat: os.stat_result | None = None
        pyc_path: pathlib.Path | None = None

        if cache_enabled:
            try:
                source_stat = os.stat(self.path)
            except OSError:
                source_stat = None
            if source_stat is not None:
                pyc_path = _pyc_path(pathlib.Path(self.path))
                cached = _read_pyc(source_stat, pyc_path)
                if cached is not None:
                    return cached

        source = self.get_data(self.path)
        assert isinstance(source, bytes)
        code = self.source_to_code(source, self.path)
        assert isinstance(code, types.CodeType)

        if source_stat is not None and pyc_path is not None:
            _write_pyc(code, source_stat, pyc_path)

        return code

    def source_to_code(  # type: ignore [override]  # ty: ignore[invalid-method-override]
        self,
        data: bytes,
        path: str,
        *,
        _optimize: int = -1,
    ) -> types.CodeType:
        mods = _pytest_rewrite_modules()
        if mods is not None:
            rewrite_mod, _ = mods
            try:
                tree = ast.parse(data, filename=path)
                rewrite_mod.rewrite_asserts(tree, data, path, None)
                return compile(
                    tree, path, "exec", dont_inherit=True, optimize=_optimize
                )
            except SyntaxError:
                raise
            except Exception:  # noqa: S110
                # Fall through to a plain compile below.
                pass
        code = super().source_to_code(data, path, _optimize=_optimize)
        assert isinstance(code, types.CodeType)
        return code


class RewriteFinder(importlib.abc.MetaPathFinder):
    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,
        target: types.ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or spec.origin is None:
            return None
        origin = pathlib.Path(spec.origin)
        if origin.suffix != ".py" or not should_rewrite(origin.name):
            return None
        if type(spec.loader) is not importlib.machinery.SourceFileLoader:
            return None
        spec.loader = RewriteLoader(spec.loader.name, spec.loader.path)
        spec.cached = None
        return spec


_installed = False


def install() -> None:
    """Install the assertion-rewriting import hook (idempotent)."""
    global _installed  # noqa: PLW0603
    if _installed:
        return
    _installed = True

    mods = _pytest_rewrite_modules()
    if mods is None:
        return

    _, util_mod = mods
    _install_reprcompare(util_mod)
    sys.meta_path.insert(0, RewriteFinder())


def loader_for_file(
    name: str,
    path: str,
) -> importlib.machinery.SourceFileLoader:
    """A source loader for manually-constructed module specs.

    Returns the rewriting loader when the hook is installed and the
    file qualifies (used for conftest.py files, which are imported via
    spec_from_file_location and bypass the meta path).
    """
    if (
        _installed
        and should_rewrite(pathlib.Path(path).name)
        and _pytest_rewrite_modules() is not None
    ):
        return RewriteLoader(name, path)
    return importlib.machinery.SourceFileLoader(name, path)
