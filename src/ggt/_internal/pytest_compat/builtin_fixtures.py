# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

"""Built-in fixtures for the pytest compatibility layer.

Self-contained implementations of the most commonly used pytest
builtin fixtures (``tmp_path``, ``tmp_path_factory``, ``monkeypatch``).
Deliberately free of any pytest imports so that importing ggt does not
pull in pytest.
"""

from __future__ import annotations

import contextlib
import dataclasses
import importlib
import io
import logging
import os
import pathlib
import re
import sys
import tempfile
import warnings
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


@dataclasses.dataclass(frozen=True)
class BuiltinFixtureSpec:
    name: str
    scope: str
    argnames: tuple[str, ...]
    func: Callable[..., object]
    params: tuple[object, ...] | None = None


_registry: dict[str, BuiltinFixtureSpec] = {}


def register(
    name: str,
    *,
    func: Callable[..., object],
    scope: str = "function",
    argnames: tuple[str, ...] = (),
    params: tuple[object, ...] | None = None,
) -> None:
    """Register a built-in fixture.

    This module registers the core pytest builtins below; feature
    modules (e.g. anyio_bridge) contribute theirs as an import side
    effect.
    """
    if name in _registry:
        raise ValueError(f"builtin fixture {name!r} is already registered")
    _registry[name] = BuiltinFixtureSpec(
        name=name,
        scope=scope,
        argnames=argnames,
        func=func,
        params=params,
    )


def registered() -> tuple[BuiltinFixtureSpec, ...]:
    return tuple(_registry.values())


class TempPathFactory:
    """A minimal stand-in for pytest's TempPathFactory."""

    def __init__(self) -> None:
        self._basetemp: pathlib.Path | None = None

    def getbasetemp(self) -> pathlib.Path:
        if self._basetemp is None:
            # resolve() so that paths compare equal to their resolved
            # forms (macOS /var -> /private/var), matching pytest.
            self._basetemp = pathlib.Path(
                tempfile.mkdtemp(prefix="ggt-pytest-")
            ).resolve()
        return self._basetemp

    def mktemp(self, basename: str, numbered: bool = True) -> pathlib.Path:  # noqa: FBT001, FBT002
        base = self.getbasetemp()
        if not numbered:
            path = base / basename
            path.mkdir()
            return path

        for i in range(10000):
            path = base / f"{basename}{i}"
            try:
                path.mkdir()
            except FileExistsError:
                continue
            return path

        raise RuntimeError(f"could not create numbered dir {basename}")


_notset: Any = object()


class MonkeyPatch:
    """A minimal stand-in for pytest's MonkeyPatch."""

    def __init__(self) -> None:
        self._setattr: list[tuple[object, str, object]] = []
        self._setitem: list[tuple[Any, object, object]] = []
        self._cwd: str | None = None
        self._syspath: list[str] = []

    def _resolve_target(self, target: str) -> tuple[object, str]:
        modpath, dot, attr = target.rpartition(".")
        if not dot:
            raise TypeError(
                f"monkeypatch string target must be in the form "
                f"'module.attr': {target!r}"
            )
        obj: object
        try:
            obj = importlib.import_module(modpath)
        except ImportError:
            # The prefix may itself be a "module.Class" path.
            parent, klass = modpath.rsplit(".", 1)
            obj = getattr(importlib.import_module(parent), klass)
        return obj, attr

    def setattr(
        self,
        target: object,
        name: object = _notset,
        value: object = _notset,
        *,
        raising: bool = True,
    ) -> None:
        if isinstance(target, str) and value is _notset:
            # setattr("module.attr", value) form.
            value = name
            target, attr = self._resolve_target(target)
        else:
            if not isinstance(name, str):
                raise TypeError(
                    "monkeypatch.setattr() expects an attribute name string"
                )
            attr = name

        old = getattr(target, attr, _notset)
        if old is _notset and raising:
            raise AttributeError(f"{target!r} has no attribute {attr!r}")

        self._setattr.append((target, attr, old))
        setattr(target, attr, value)

    def delattr(
        self,
        target: object,
        name: object = _notset,
        *,
        raising: bool = True,
    ) -> None:
        if isinstance(target, str) and name is _notset:
            target, attr = self._resolve_target(target)
        else:
            if not isinstance(name, str):
                raise TypeError(
                    "monkeypatch.delattr() expects an attribute name string"
                )
            attr = name

        if not hasattr(target, attr):
            if raising:
                raise AttributeError(f"{target!r} has no attribute {attr!r}")
            return

        self._setattr.append((target, attr, getattr(target, attr)))
        delattr(target, attr)

    def setitem(self, mapping: Any, name: object, value: object) -> None:
        old = mapping.get(name, _notset)
        self._setitem.append((mapping, name, old))
        mapping[name] = value

    def delitem(
        self,
        mapping: Any,
        name: object,
        *,
        raising: bool = True,
    ) -> None:
        if name not in mapping:
            if raising:
                raise KeyError(name)
            return
        self._setitem.append((mapping, name, mapping[name]))
        del mapping[name]

    def setenv(
        self,
        name: str,
        value: str,
        *,
        prepend: str | None = None,
    ) -> None:
        if prepend is not None and name in os.environ:
            value = f"{value}{prepend}{os.environ[name]}"
        self.setitem(os.environ, name, value)

    def delenv(self, name: str, *, raising: bool = True) -> None:
        self.delitem(os.environ, name, raising=raising)

    def syspath_prepend(self, path: object) -> None:
        entry = str(path)
        self._syspath.append(entry)
        sys.path.insert(0, entry)
        importlib.invalidate_caches()

    def chdir(self, path: object) -> None:
        if self._cwd is None:
            self._cwd = os.getcwd()
        os.chdir(str(path))

    def undo(self) -> None:
        for obj, attr, old in reversed(self._setattr):
            if old is _notset:
                delattr(obj, attr)
            else:
                setattr(obj, attr, old)
        self._setattr.clear()

        for mapping, name, old in reversed(self._setitem):
            if old is _notset:
                mapping.pop(name, None)
            else:
                mapping[name] = old
        self._setitem.clear()

        for entry in self._syspath:
            try:
                sys.path.remove(entry)
            except ValueError:
                pass
        self._syspath.clear()

        if self._cwd is not None:
            os.chdir(self._cwd)
            self._cwd = None

    @classmethod
    @contextlib.contextmanager
    def context(cls) -> Iterator[MonkeyPatch]:
        patcher = cls()
        try:
            yield patcher
        finally:
            patcher.undo()


class CaptureResult(NamedTuple):
    out: str
    err: str


class CaptureFixture:
    """A minimal stand-in for pytest's capsys."""

    def __init__(self) -> None:
        self._old: tuple[Any, Any] | None = None
        self._out = io.StringIO()
        self._err = io.StringIO()

    def _start(self) -> None:
        self._old = (sys.stdout, sys.stderr)
        sys.stdout = self._out
        sys.stderr = self._err

    def _stop(self) -> None:
        if self._old is not None:
            sys.stdout, sys.stderr = self._old
            self._old = None

    def readouterr(self) -> CaptureResult:
        out = self._out.getvalue()
        err = self._err.getvalue()
        self._out.seek(0)
        self._out.truncate(0)
        self._err.seek(0)
        self._err.truncate(0)
        return CaptureResult(out=out, err=err)

    @contextlib.contextmanager
    def disabled(self) -> Iterator[None]:
        """Temporarily restore the real stdout/stderr."""
        if self._old is None:
            yield
            return
        current = (sys.stdout, sys.stderr)
        sys.stdout, sys.stderr = self._old
        try:
            yield
        finally:
            sys.stdout, sys.stderr = current


def _capsys() -> Iterator[CaptureFixture]:
    cap = CaptureFixture()
    cap._start()
    try:
        yield cap
    finally:
        cap._stop()


class _LogCaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=0)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # Formatting populates record.message, which tests
            # inspect directly (pytest's handler formats on emit).
            self.format(record)
        except Exception:  # noqa: S110
            pass
        self.records.append(record)


class LogCaptureFixture:
    """A minimal stand-in for pytest's caplog."""

    def __init__(self) -> None:
        self.handler = _LogCaptureHandler()
        self.handler.setFormatter(
            logging.Formatter(
                "%(levelname)-8s %(name)s:%(filename)s:%(lineno)s %(message)s"
            )
        )
        self._saved_levels: dict[str | None, int] = {}

    @property
    def records(self) -> list[logging.LogRecord]:
        return self.handler.records

    @property
    def messages(self) -> list[str]:
        return [record.getMessage() for record in self.records]

    @property
    def text(self) -> str:
        return "".join(
            self.handler.format(record) + "\n" for record in self.records
        )

    @property
    def record_tuples(self) -> list[tuple[str, int, str]]:
        return [
            (record.name, record.levelno, record.getMessage())
            for record in self.records
        ]

    def clear(self) -> None:
        self.handler.records.clear()

    def set_level(self, level: int | str, logger: str | None = None) -> None:
        logger_obj = logging.getLogger(logger)
        if logger not in self._saved_levels:
            self._saved_levels[logger] = logger_obj.level
        logger_obj.setLevel(level)
        self.handler.setLevel(level)

    @contextlib.contextmanager
    def at_level(
        self,
        level: int | str,
        logger: str | None = None,
    ) -> Iterator[None]:
        logger_obj = logging.getLogger(logger)
        orig_level = logger_obj.level
        orig_handler_level = self.handler.level
        logger_obj.setLevel(level)
        self.handler.setLevel(level)
        try:
            yield
        finally:
            logger_obj.setLevel(orig_level)
            self.handler.setLevel(orig_handler_level)

    def _finalize(self) -> None:
        for logger, level in self._saved_levels.items():
            logging.getLogger(logger).setLevel(level)
        self._saved_levels.clear()


def _caplog() -> Iterator[LogCaptureFixture]:
    cap = LogCaptureFixture()
    root = logging.getLogger()
    root.addHandler(cap.handler)
    try:
        yield cap
    finally:
        root.removeHandler(cap.handler)
        cap._finalize()


_WarningList = list[warnings.WarningMessage]


class WarningsRecorder:
    """A minimal stand-in for pytest's recwarn value."""

    def __init__(self) -> None:
        self._list: _WarningList = []

    @property
    def list(self) -> _WarningList:
        return self._list

    def pop(self, cls: type[Warning] = Warning) -> warnings.WarningMessage:
        for index, message in enumerate(self._list):
            if issubclass(message.category, cls):
                return self._list.pop(index)
        raise AssertionError(f"{cls!r} not found in warning list")

    def clear(self) -> None:
        self._list.clear()

    def __len__(self) -> int:
        return len(self._list)

    def __iter__(self) -> Iterator[warnings.WarningMessage]:
        return iter(self._list)

    def __getitem__(self, index: int) -> warnings.WarningMessage:
        return self._list[index]


def _recwarn() -> Iterator[WarningsRecorder]:
    recorder = WarningsRecorder()
    with warnings.catch_warnings(record=True) as captured:
        assert captured is not None
        recorder._list = captured
        warnings.simplefilter("always")
        yield recorder


def _tmp_path_factory() -> TempPathFactory:
    return TempPathFactory()


def _tmp_path(tmp_path_factory: TempPathFactory, request: Any) -> pathlib.Path:
    name = re.sub(r"[\W]", "_", str(request.node.name))[:30]
    return tmp_path_factory.mktemp(name, numbered=True)


def _monkeypatch() -> Iterator[MonkeyPatch]:
    mp = MonkeyPatch()
    try:
        yield mp
    finally:
        mp.undo()


register("tmp_path_factory", scope="session", func=_tmp_path_factory)
register(
    "tmp_path",
    argnames=("tmp_path_factory", "request"),
    func=_tmp_path,
)
register("monkeypatch", func=_monkeypatch)
register("capsys", func=_capsys)
register("caplog", func=_caplog)
register("recwarn", func=_recwarn)
