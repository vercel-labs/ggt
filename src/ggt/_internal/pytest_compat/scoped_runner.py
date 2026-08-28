# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0

"""Per-process asyncio.Runner ownership for pytest-compatible execution."""

from __future__ import annotations

import asyncio
import contextvars
import pathlib
import warnings
from typing import TYPE_CHECKING, Any

from . import inicfg

if TYPE_CHECKING:
    import types
    from collections.abc import Coroutine, Sequence

SCOPES = ("function", "class", "module", "package", "session")
_runners: dict[tuple[str, object], asyncio.Runner] = {}
_contexts: dict[tuple[str, object], contextvars.Context] = {}
_warned_scope_aliases: set[str] = set()


def package_key(mod: types.ModuleType) -> str:
    origin = getattr(mod, "__file__", None)
    if origin is None:
        return mod.__name__.partition(".")[0]
    directory = pathlib.Path(origin).resolve().parent
    package = directory
    while (package.parent / "__init__.py").is_file():
        package = package.parent
    return str(package)


def scope_key(
    scope: str,
    *,
    mod: types.ModuleType,
    synth_cls: type,
    token: object,
) -> object:
    return {
        "function": token,
        "class": synth_cls,
        "module": mod.__name__,
        "package": package_key(mod),
        "session": None,
    }[scope]


def validate_scope(value: object, *, source: str) -> str:
    if not isinstance(value, str) or value not in SCOPES:
        raise ValueError(
            f"invalid asyncio loop scope {value!r} for {source}; "
            f"expected one of {', '.join(SCOPES)}"
        )
    return value


def test_loop_scope(marks: Sequence[Any], *, source: str) -> str:
    selected: object | None = None
    for mark in marks:
        if getattr(mark, "name", None) != "asyncio":
            continue
        kwargs = getattr(mark, "kwargs", {})
        if any(k in kwargs for k in ("loop_factory", "event_loop_policy")):
            raise ValueError(
                f"asyncio loop factories and policy overrides are not "
                f"supported by ggt: {source}"
            )
        loop_scope = kwargs.get("loop_scope")
        old_scope = kwargs.get("scope")
        if loop_scope is not None and old_scope is not None:
            raise ValueError(
                f"asyncio marker for {source} specifies both loop_scope "
                f"and deprecated scope"
            )
        selected = loop_scope if loop_scope is not None else old_scope
        if old_scope is not None and source not in _warned_scope_aliases:
            _warned_scope_aliases.add(source)
            warnings.warn(
                f"asyncio marker scope= is deprecated for {source}; "
                f"use loop_scope= instead",
                DeprecationWarning,
                stacklevel=3,
            )
        if selected is not None:
            break
    if selected is None:
        selected = (
            inicfg.current().asyncio_default_test_loop_scope or "function"
        )
    return validate_scope(selected, source=source)


def fixture_loop_scope(
    declared: object | None, *, cache_scope: str, source: str
) -> str:
    selected = (
        declared
        or inicfg.current().asyncio_default_fixture_loop_scope
        or cache_scope
    )
    selected_scope = validate_scope(selected, source=source)
    if SCOPES.index(selected_scope) < SCOPES.index(cache_scope):
        raise ValueError(
            f"asyncio loop scope {selected_scope!r} for {source} is narrower "
            f"than its fixture cache scope {cache_scope!r}"
        )
    return selected_scope


def run(
    scope: str,
    key: object,
    awaitable: Coroutine[Any, Any, Any],
) -> Any:
    identity = (scope, key)
    runner = _runners.get(identity)
    if runner is None:
        runner = asyncio.Runner()
        _runners[identity] = runner
        _contexts[identity] = contextvars.copy_context()

    context = _contexts[identity]
    for variable, value in contextvars.copy_context().items():
        context.run(variable.set, value)

    async def capture_context() -> tuple[Any, contextvars.Context]:
        value = await awaitable
        return value, contextvars.copy_context()

    value, updated = runner.run(capture_context(), context=context)
    _contexts[identity] = updated
    for variable, item in updated.items():
        variable.set(item)
    return value


def close(scope: str, key: object) -> None:
    identity = (scope, key)
    runner = _runners.pop(identity, None)
    _contexts.pop(identity, None)
    if runner is not None:
        runner.close()


def close_all() -> None:
    first_error: BaseException | None = None
    for scope, key in sorted(
        [*_runners], key=lambda item: SCOPES.index(item[0])
    ):
        try:
            close(scope, key)
        except BaseException as exc:
            if first_error is None:
                first_error = exc
    if first_error is not None:
        raise first_error
