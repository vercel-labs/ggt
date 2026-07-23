# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

"""The pytest-compatible fixture engine.

Discovers ``@pytest.fixture`` definitions (by duck-typing the marker
attributes pytest attaches, so pytest itself is never imported here),
resolves fixture dependency graphs, maintains per-scope value caches,
and runs teardown finalizers in LIFO order.

Execution model: unlike stock pytest (single process), ggt runs tests
in worker processes.  All fixtures execute lazily in the process that
runs the requesting test; caches are per-process, so session- and
module-scoped fixtures run at most once *per worker* (the same
semantics as pytest-xdist).

Scope-to-teardown mapping:

- function: at the end of each synthesized test method;
- class: from the synthesized class's ``tearDownClass``;
- module: from the injected ``tearDownModule`` (runs on module
  transitions within a worker);
- session: at worker shutdown (``runner.teardown_suite``).
"""

from __future__ import annotations

import dataclasses
import functools
import inspect
import json
import os
import pathlib
import sys
import traceback
import types
import unittest
from typing import TYPE_CHECKING, Any, Self, cast

from ..decorators import LOCAL_FIXTURE_ATTR
from . import builtin_fixtures, discovery

# Imported for their side effect of contributing to the builtin
# fixture registry.
from . import anyio_bridge  # noqa: F401

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

SCOPE_ORDER = {
    "function": 0,
    "class": 1,
    "module": 2,
    "session": 3,
}

_NAMED_KINDS = frozenset(
    {
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.KEYWORD_ONLY,
    }
)


def fixture_params(
    func: Callable[..., object],
    *,
    skip_first: bool = False,
) -> tuple[str, ...]:
    """Named parameters without defaults (pytest fixture requests)."""
    try:
        # Signature inspection is slow and the same functions are
        # inspected many times during synthesis (per parametrize
        # case, and again in workers), so memoize.
        return _fixture_params(func, skip_first=skip_first)
    except TypeError:
        # Unhashable callable.
        return _fixture_params.__wrapped__(func, skip_first=skip_first)


def _num_mock_patch_args(func: Callable[..., object]) -> int:
    """Arguments consumed by ``unittest.mock.patch`` decorators.

    Mirrors pytest's ``num_mock_patch_args``: each ``@patch`` (or
    ``@patch.object``) without an explicit replacement passes a mock
    to the test as a positional argument.  Those bind to the test's
    leading parameters (fixtures are passed by keyword and so must
    come after them in the signature); they are not fixture requests.
    """
    patchings = getattr(func, "patchings", None)
    if not patchings:
        return 0
    mock_sentinel = getattr(sys.modules.get("mock"), "DEFAULT", object())
    ut_mock_sentinel = getattr(
        sys.modules.get("unittest.mock"), "DEFAULT", object()
    )
    return len(
        [
            p
            for p in patchings
            if not p.attribute_name
            and (p.new is mock_sentinel or p.new is ut_mock_sentinel)
        ]
    )


@functools.cache
def _fixture_params(
    func: Callable[..., object],
    *,
    skip_first: bool,
) -> tuple[str, ...]:
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        return ()
    params = [p for p in sig.parameters.values() if p.kind in _NAMED_KINDS]
    if skip_first and params:
        params = params[1:]
    if num_mock := _num_mock_patch_args(func):
        # inspect.signature followed __wrapped__ to the original
        # function, whose leading parameters receive the mocks.
        params = params[num_mock:]
    return tuple(
        p.name for p in params if p.default is inspect.Parameter.empty
    )


class FixtureError(Exception):
    pass


class FixtureLookupError(FixtureError):
    pass


_MISSING: Any = object()


@dataclasses.dataclass(eq=False)
class FixtureDef:
    name: str
    scope: str
    autouse: bool
    params: Sequence[object] | None
    func: Callable[..., object]
    needs_instance: bool
    argnames: tuple[str, ...]
    source: str
    is_async: bool = False
    ids: object = None
    local: bool = False

    def __hash__(self) -> int:
        return id(self)


def param_payload(item: object) -> object:
    """The actual value of a fixture params entry.

    Entries may be plain values or ``pytest.param(...)`` ParameterSets
    (duck-typed to avoid importing pytest).
    """
    if (
        type(item).__name__ == "ParameterSet"
        and hasattr(item, "values")
        and hasattr(item, "marks")
        and hasattr(item, "id")
    ):
        values = tuple(cast("Sequence[object]", getattr(item, "values", ())))
        return values[0] if len(values) == 1 else values
    return item


def _extract_marker(obj: object) -> tuple[Any, Callable[..., object]] | None:
    """Duck-type pytest's fixture markers.

    Returns (marker, fixture function) or None if *obj* is not a
    fixture definition.  Supports both the pytest >= 8.4 shape
    (``FixtureFunctionDefinition`` with ``_fixture_function_marker``
    and ``_fixture_function``) and the older shape (pytest 7 - 8.3: a
    function with a ``_pytestfixturefunction`` attribute).
    """
    marker = getattr(obj, "_fixture_function_marker", None)
    if marker is not None:
        func = getattr(obj, "_fixture_function", None)
        if callable(func):
            return marker, func
        return None

    marker = getattr(obj, "_pytestfixturefunction", None)
    if marker is not None and callable(obj):
        # pytest < 8.4 wraps the fixture function in a guard that
        # fails on direct calls; the original is stashed in
        # ``__pytest_wrapped__.obj``.
        wrapped = getattr(obj, "__pytest_wrapped__", None)
        func = getattr(wrapped, "obj", None)
        if callable(func):
            return marker, func
        return marker, obj

    return None


def _make_fixture_def(
    attr_name: str,
    obj: object,
    *,
    source: str,
    needs_instance: bool,
) -> FixtureDef | None:
    extracted = _extract_marker(obj)
    if extracted is None:
        return None

    marker, func = extracted
    name = getattr(marker, "name", None) or attr_name
    scope = getattr(marker, "scope", "function")

    if callable(scope):
        raise FixtureError(
            f"dynamic fixture scopes are not supported by ggt pytest "
            f"compatibility: fixture {name!r} in {source}"
        )
    if scope == "package":
        raise FixtureError(
            f"package-scoped fixtures are not supported by ggt pytest "
            f"compatibility: fixture {name!r} in {source}"
        )
    if scope not in SCOPE_ORDER:
        raise FixtureError(
            f"unknown fixture scope {scope!r}: fixture {name!r} in {source}"
        )

    is_async = inspect.iscoroutinefunction(func) or (
        inspect.isasyncgenfunction(func)
    )

    params = getattr(marker, "params", None)

    return FixtureDef(
        name=name,
        scope=scope,
        autouse=bool(getattr(marker, "autouse", False)),
        params=[*params] if params is not None else None,
        func=func,
        needs_instance=needs_instance,
        argnames=fixture_params(func, skip_first=needs_instance),
        source=source,
        is_async=is_async,
        ids=getattr(marker, "ids", None),
        local=bool(
            getattr(obj, LOCAL_FIXTURE_ATTR, False)
            or getattr(func, LOCAL_FIXTURE_ATTR, False)
        ),
    )


_module_fixture_cache: dict[str, dict[str, FixtureDef]] = {}


def _collect_module_fixtures(mod: types.ModuleType) -> dict[str, FixtureDef]:
    # FixtureDef identity is part of the value-cache key, so a module's
    # definitions must be collected exactly once per process — otherwise
    # two test modules sharing a conftest would get distinct FixtureDef
    # objects for the same fixture and defeat session/module caching.
    cached = _module_fixture_cache.get(mod.__name__)
    if cached is not None:
        return cached

    # NOTE: the source doubles as a cross-process identity (it is part
    # of the shared-fixture seed key), so it must be deterministic.
    # Mangled top-level conftest module names are deterministic too,
    # but the file path is friendlier in error messages.
    source = mod.__name__
    if source.startswith("__ggt_conftest_"):
        source = getattr(mod, "__file__", None) or source

    result: dict[str, FixtureDef] = {}
    for attr_name, obj in [*vars(mod).items()]:
        fdef = _make_fixture_def(
            attr_name,
            obj,
            source=source,
            needs_instance=False,
        )
        if fdef is not None:
            result[fdef.name] = fdef

    _module_fixture_cache[mod.__name__] = result
    return result


def _collect_class_fixtures(cls: type) -> dict[str, FixtureDef]:
    result: dict[str, FixtureDef] = {}
    for attr_name in dir(cls):
        obj = inspect.getattr_static(cls, attr_name, None)
        fdef = _make_fixture_def(
            attr_name,
            obj,
            source=f"{cls.__module__}.{cls.__qualname__}",
            needs_instance=True,
        )
        if fdef is not None:
            result[fdef.name] = fdef
    return result


@functools.cache
def _builtin_fixture_source() -> dict[str, FixtureDef]:
    result: dict[str, FixtureDef] = {}
    for spec in builtin_fixtures.registered():
        result[spec.name] = FixtureDef(
            name=spec.name,
            scope=spec.scope,
            autouse=False,
            params=[*spec.params] if spec.params is not None else None,
            func=spec.func,
            needs_instance=False,
            argnames=spec.argnames,
            source="ggt builtins",
        )
    return result


_conftest_chain_cache: dict[str, list[types.ModuleType]] = {}


def _conftest_modules(mod: types.ModuleType) -> list[types.ModuleType]:
    """conftest.py modules that apply to *mod*, nearest first.

    Fixture-contributing plugin modules declared via a conftest's
    ``pytest_plugins`` follow the conftest chain, giving them lower
    lookup priority than any conftest (as in pytest, where conftest
    fixtures override plugin fixtures of the same name).

    The chain depends only on the module's directory, so it is
    computed (and the conftest files stat'ed) once per directory.
    """
    origin = getattr(mod, "__file__", None)
    if origin is None:
        return []

    dirname = os.path.dirname(origin)
    cached = _conftest_chain_cache.get(dirname)
    if cached is not None:
        return cached

    result: list[types.ModuleType] = []
    for directory in discovery.conftest_directories(pathlib.Path(origin)):
        conftest = directory / "conftest.py"
        if conftest.is_file():
            result.append(discovery.import_conftest(conftest))

    plugins: list[types.ModuleType] = []
    seen: set[str] = set()
    for conftest_mod in result:
        for plugin in discovery.plugin_modules(conftest_mod):
            if plugin.__name__ not in seen:
                seen.add(plugin.__name__)
                plugins.append(plugin)
    result.extend(plugins)

    _conftest_chain_cache[dirname] = result
    return result


@dataclasses.dataclass
class Registry:
    """Fixture sources in lookup-priority order (nearest first)."""

    sources: tuple[dict[str, FixtureDef], ...]

    def lookup(
        self,
        name: str,
        start: int = 0,
    ) -> tuple[FixtureDef, int] | None:
        for index in range(start, len(self.sources)):
            fdef = self.sources[index].get(name)
            if fdef is not None:
                return fdef, index
        return None

    def autouse_defs(self) -> list[tuple[FixtureDef, int]]:
        """Autouse fixtures, outermost source first."""
        result: list[tuple[FixtureDef, int]] = []
        for index in range(len(self.sources) - 1, -1, -1):
            result.extend(
                (fdef, index)
                for fdef in self.sources[index].values()
                if fdef.autouse
            )
        return result

    def known_names(self) -> list[str]:
        names: set[str] = {"request"}
        for source in self.sources:
            names.update(source)
        return sorted(names)

    def index_of(self, fdef: FixtureDef) -> int | None:
        for index, source in enumerate(self.sources):
            if source.get(fdef.name) is fdef:
                return index
        return None


def walk_fixture_defs(
    registry: Registry,
    roots: Sequence[tuple[str, int]],
) -> list[tuple[FixtureDef, int]]:
    """Fixture defs transitively reachable from *roots*.

    Deterministic first-reached (DFS) order; the same walk runs at
    collection time in the parent and during re-synthesis in workers,
    and both must agree (e.g. for parametrized-fixture expansion).
    """
    ordered: list[tuple[FixtureDef, int]] = []
    visited: set[tuple[FixtureDef, int]] = set()

    def visit(fdef: FixtureDef, index: int) -> None:
        if (fdef, index) in visited:
            return
        visited.add((fdef, index))
        ordered.append((fdef, index))

        for argname in fdef.argnames:
            if argname == "request":
                continue
            start = index + 1 if argname == fdef.name else 0
            found = registry.lookup(argname, start)
            if found is not None:
                visit(*found)

    for name, start in roots:
        if name == "request":
            continue
        found = registry.lookup(name, start)
        if found is not None:
            visit(*found)

    return ordered


_registry_cache: dict[tuple[str, type | None], Registry] = {}


def registry_for(mod: types.ModuleType, orig_cls: type | None) -> Registry:
    key = (mod.__name__, orig_cls)
    cached = _registry_cache.get(key)
    if cached is not None:
        return cached

    sources: list[dict[str, FixtureDef]] = []
    if orig_cls is not None:
        sources.append(_collect_class_fixtures(orig_cls))
    sources.append(_collect_module_fixtures(mod))
    sources.extend(
        _collect_module_fixtures(conftest)
        for conftest in _conftest_modules(mod)
    )
    sources.append(_builtin_fixture_source())

    registry = Registry(sources=tuple(sources))
    _registry_cache[key] = registry
    return registry


class FixtureEngine:
    def __init__(self) -> None:
        # Values of instantiated fixtures, keyed by (scope, scope key,
        # fixture definition, param index).
        self._cache: dict[
            tuple[str, object, FixtureDef, int | None], object
        ] = {}
        # LIFO finalizer stacks, keyed by (scope, scope key).
        self._finalizers: dict[tuple[str, object], list[Callable[[], None]]]
        self._finalizers = {}

    def add_finalizer(
        self,
        scope: str,
        scope_key: object,
        finalizer: Callable[[], None],
    ) -> None:
        self._finalizers.setdefault((scope, scope_key), []).append(finalizer)

    def _teardown(self, scope: str, scope_key: object) -> None:
        finalizers = self._finalizers.pop((scope, scope_key), [])
        for cache_key in [*self._cache]:
            if cache_key[0] == scope and cache_key[1] == scope_key:
                del self._cache[cache_key]

        first_error: BaseException | None = None
        for finalizer in reversed(finalizers):
            try:
                finalizer()
            except BaseException as e:
                if first_error is None:
                    first_error = e

        if first_error is not None:
            raise first_error

    def teardown_function(self, token: object) -> None:
        self._teardown("function", token)

    def teardown_class(self, cls: type) -> None:
        self._teardown("class", cls)

    def teardown_module(self, modname: str) -> None:
        self._teardown("module", modname)

    def teardown_session(self) -> None:
        # Tear down anything that is still standing, narrowest scope
        # first.  Under normal operation only session-scoped
        # finalizers remain by this point.
        pending = sorted(
            self._finalizers,
            key=lambda key: SCOPE_ORDER.get(key[0], 0),
        )
        first_error: BaseException | None = None
        for scope, scope_key in pending:
            try:
                self._teardown(scope, scope_key)
            except BaseException as e:
                if first_error is None:
                    first_error = e

        if first_error is not None:
            raise first_error


_engine = FixtureEngine()

# Values of fixtures that were executed in the parent (runner) process
# and shipped to this process, keyed by
# (scope, scope key, defining module name, fixture name).  Consulted
# by _value_of before executing a fixture.
_seeded_values: dict[tuple[str, object, str, str], object] = {}


def seed_value(
    *,
    scope: str,
    scope_key: object,
    source: str,
    name: str,
    value: object,
) -> None:
    _seeded_values[scope, scope_key, source, name] = value


def teardown_class(cls: type) -> None:
    _engine.teardown_class(cls)


def teardown_module(modname: str) -> None:
    _engine.teardown_module(modname)


def teardown_session() -> None:
    _engine.teardown_session()


class ConfigLite:
    """A minimal stand-in for pytest's ``request.config``.

    Backed by ggt's ``-X key=value`` options (exported to workers via
    the ``GGT_PYTEST_OPTIONS`` environment variable); unknown options
    resolve to the caller-provided default, which is what most
    defensive ``getoption`` call sites expect.
    """

    def __init__(self, options: Mapping[str, str]) -> None:
        self._options = dict(options)

    def _lookup(self, name: str) -> str | None:
        stripped = name.lstrip("-")
        candidates = [
            name,
            stripped,
            stripped.replace("-", "_"),
            stripped.replace("_", "-"),
        ]
        for candidate in candidates:
            if candidate in self._options:
                return self._options[candidate]
        return None

    def getoption(
        self,
        name: str,
        default: object = None,
        skip: bool = False,  # noqa: FBT001, FBT002
    ) -> object:
        value = self._lookup(name)
        if value is not None:
            return value
        if skip:
            raise unittest.SkipTest(f"no option named {name!r}")
        return default

    # The legacy spelling of getoption.
    getvalue = getoption

    def getini(self, name: str) -> object:
        raise ValueError(f"unknown configuration value: {name!r}")

    @property
    def rootpath(self) -> pathlib.Path:
        return pathlib.Path.cwd()

    @property
    def option(self) -> types.SimpleNamespace:
        return types.SimpleNamespace(
            **{
                key.replace("-", "_"): value
                for key, value in self._options.items()
            }
        )


OPTIONS_ENV = "GGT_PYTEST_OPTIONS"

_config: ConfigLite | None = None


def get_config() -> ConfigLite:
    global _config  # noqa: PLW0603
    if _config is None:
        options: dict[str, str] = {}
        raw = os.environ.get(OPTIONS_ENV)
        if raw:
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                options = {str(k): str(v) for k, v in parsed.items()}
        _config = ConfigLite(options)
    return _config


class FixtureRequest:
    """A minimal implementation of pytest's ``request`` fixture."""

    def __init__(
        self,
        execution: TestExecution,
        fixturedef: FixtureDef | None,
    ) -> None:
        self._execution = execution
        self._fixturedef = fixturedef

    @property
    def fixturename(self) -> str | None:
        return self._fixturedef.name if self._fixturedef else None

    @property
    def scope(self) -> str:
        return self._fixturedef.scope if self._fixturedef else "function"

    @property
    def node(self) -> types.SimpleNamespace:
        ctx = self._execution

        def get_closest_marker(
            name: str,
            default: object = None,
        ) -> object:
            # item_marks is ordered outermost (module) to innermost
            # (function, then per-parameter marks).
            for mark in reversed(ctx.item_marks):
                if getattr(mark, "name", None) == name:
                    return mark
            return default

        return types.SimpleNamespace(
            name=ctx.test_name,
            nodeid=f"{ctx.mod.__name__}::{ctx.test_name}",
            module=ctx.mod,
            cls=ctx.orig_cls,
            instance=ctx.instance,
            get_closest_marker=get_closest_marker,
        )

    @property
    def instance(self) -> object | None:
        return self._execution.instance

    @property
    def module(self) -> types.ModuleType:
        return self._execution.mod

    @property
    def param(self) -> object:
        if self._fixturedef is not None:
            return self._execution.param_value(self._fixturedef)
        raise AttributeError(
            "request.param is only available inside parametrized fixtures"
        )

    @property
    def config(self) -> ConfigLite:
        return get_config()

    def getfixturevalue(self, name: str) -> object:
        return self._execution.get(name)

    def addfinalizer(self, finalizer: Callable[[], None]) -> None:
        if self._fixturedef is None:
            scope = "function"
            scope_key: object = self._execution.token
        else:
            scope = self._fixturedef.scope
            scope_key = self._execution.scope_key(scope)
        _engine.add_finalizer(scope, scope_key, finalizer)


class TestExecution:
    """Per-test fixture resolution context."""

    def __init__(
        self,
        *,
        mod: types.ModuleType,
        synth_cls: type,
        orig_cls: type | None,
        instance: object | None,
        test_name: str,
        param_bindings: Mapping[FixtureDef, int] | None = None,
        item_marks: Sequence[Any] = (),
    ) -> None:
        self.mod = mod
        self.synth_cls = synth_cls
        self.orig_cls = orig_cls
        self.instance = instance
        self.test_name = test_name
        self.token = object()
        self.registry = registry_for(mod, orig_cls)
        # Parameter choices for parametrized fixtures, decided when
        # the test was expanded at collection time.
        self.param_bindings = dict(param_bindings or {})
        # Marks applying to this test, outermost (module) first.
        self.item_marks = tuple(item_marks)
        self._resolving: list[FixtureDef] = []
        self._request = FixtureRequest(self, None)
        # Finalizers of async fixtures; they must be awaited inside
        # the test's event loop (see run_async_finalizers).
        self._async_finalizers: list[Callable[[], Awaitable[None]]] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: types.TracebackType | None,
    ) -> None:
        try:
            _engine.teardown_function(self.token)
        except BaseException:
            if exc is None:
                raise
            # The test already failed; report the teardown error
            # without masking the original failure.
            sys.stderr.write(
                f"error during fixture teardown of {self.test_name}:\n"
                f"{traceback.format_exc()}\n"
            )

    def scope_key(self, scope: str) -> object:
        keys: dict[str, object] = {
            "function": self.token,
            "class": self.synth_cls,
            "module": self.mod.__name__,
        }
        return keys.get(scope)

    def resolve_autouse(self) -> None:
        for fdef, index in self.registry.autouse_defs():
            self._value_of(fdef, index)

    async def resolve_autouse_async(self) -> None:
        for fdef, index in self.registry.autouse_defs():
            await self._value_of_async(fdef, index)

    def get(self, name: str) -> object:
        if name == "request":
            return self._request

        found = self.registry.lookup(name)
        if found is None:
            raise FixtureLookupError(self._lookup_error_message(name))

        fdef, index = found
        return self._value_of(fdef, index)

    async def get_async(self, name: str) -> object:
        if name == "request":
            return self._request

        found = self.registry.lookup(name)
        if found is None:
            raise FixtureLookupError(self._lookup_error_message(name))

        fdef, index = found
        return await self._value_of_async(fdef, index)

    async def run_async_finalizers(self) -> None:
        """Run teardown of async fixtures, LIFO, in the test's loop."""
        first_error: BaseException | None = None
        while self._async_finalizers:
            finalizer = self._async_finalizers.pop()
            try:
                await finalizer()
            except BaseException as e:
                if first_error is None:
                    first_error = e
        if first_error is not None:
            raise first_error

    def _lookup_error_message(self, name: str) -> str:
        available = ", ".join(self.registry.known_names())
        return (
            f"fixture {name!r} not found while setting up "
            f"{self.mod.__name__}::{self.test_name}\n"
            f"available fixtures: {available}"
        )

    def resolve_def(self, fdef: FixtureDef, index: int) -> object:
        """Resolve a specific fixture definition (used by shared.py)."""
        return self._value_of(fdef, index)

    def param_value(self, fdef: FixtureDef) -> object:
        param_index = self.param_bindings.get(fdef)
        if param_index is None or fdef.params is None:
            raise AttributeError(
                f"request.param is not available: fixture {fdef.name!r} "
                f"is not parametrized for this test"
            )
        return param_payload(fdef.params[param_index])

    def _lookup_cached(
        self,
        fdef: FixtureDef,
    ) -> tuple[object, tuple[str, object, FixtureDef, int | None], object]:
        scope_key = self.scope_key(fdef.scope)
        # The chosen parameter is part of a parametrized fixture's
        # identity: each param set gets its own cached value.
        cache_key = (
            fdef.scope,
            scope_key,
            fdef,
            self.param_bindings.get(fdef),
        )
        if cache_key in _engine._cache:
            return scope_key, cache_key, _engine._cache[cache_key]

        seeded_key = (fdef.scope, scope_key, fdef.source, fdef.name)
        if seeded_key in _seeded_values:
            # The value was computed in the runner process and shipped
            # to this worker; no local execution (and no finalizer).
            value = _seeded_values[seeded_key]
            _engine._cache[cache_key] = value
            return scope_key, cache_key, value

        return scope_key, cache_key, _MISSING

    def _check_resolvable(self, fdef: FixtureDef) -> None:
        if fdef in self._resolving:
            chain = " -> ".join(d.name for d in [*self._resolving, fdef])
            raise FixtureError(f"recursive fixture dependency: {chain}")

        if fdef.params is not None and fdef not in self.param_bindings:
            raise FixtureError(
                f"parametrized fixture {fdef.name!r} (from "
                f"{fdef.source}) was requested dynamically; "
                f"parametrized fixtures must be reachable from the "
                f"test's signature (or autouse/usefixtures) so that "
                f"ggt can expand them at collection time"
            )

    def _value_of(self, fdef: FixtureDef, index: int) -> object:
        scope_key, cache_key, value = self._lookup_cached(fdef)
        if value is not _MISSING:
            return value

        if fdef.is_async:
            raise FixtureError(
                f"async fixture {fdef.name!r} (from {fdef.source}) can "
                f"only be requested by async tests and async fixtures"
            )

        self._check_resolvable(fdef)

        self._resolving.append(fdef)
        try:
            kwargs = self._resolve_args(fdef, index)
        finally:
            self._resolving.pop()

        value = self._instantiate(fdef, scope_key, kwargs)
        _engine._cache[cache_key] = value
        return value

    async def _value_of_async(self, fdef: FixtureDef, index: int) -> object:
        scope_key, cache_key, value = self._lookup_cached(fdef)
        if value is not _MISSING:
            return value

        if fdef.is_async and fdef.scope != "function":
            raise FixtureError(
                f"async fixtures are only supported with function "
                f"scope: fixture {fdef.name!r} in {fdef.source} has "
                f"scope {fdef.scope!r} (its value would be bound to a "
                f"single test's event loop)"
            )

        self._check_resolvable(fdef)

        self._resolving.append(fdef)
        try:
            kwargs = await self._resolve_args_async(fdef, index)
        finally:
            self._resolving.pop()

        value = await self._instantiate_async(fdef, scope_key, kwargs)
        _engine._cache[cache_key] = value
        return value

    def _dep_of(
        self,
        fdef: FixtureDef,
        index: int,
        argname: str,
    ) -> tuple[FixtureDef, int]:
        # A fixture may request a same-named fixture from an outer
        # (lower priority) source; continue the search past its own
        # definition.
        start = index + 1 if argname == fdef.name else 0
        found = self.registry.lookup(argname, start)
        if found is None:
            raise FixtureLookupError(self._lookup_error_message(argname))

        dep, dep_index = found
        if SCOPE_ORDER[dep.scope] < SCOPE_ORDER[fdef.scope]:
            raise FixtureError(
                f"ScopeMismatch: {fdef.scope}-scoped fixture "
                f"{fdef.name!r} cannot use {dep.scope}-scoped "
                f"fixture {dep.name!r}"
            )

        return dep, dep_index

    def _resolve_args(
        self,
        fdef: FixtureDef,
        index: int,
    ) -> dict[str, object]:
        kwargs: dict[str, object] = {}
        for argname in fdef.argnames:
            if argname == "request":
                kwargs[argname] = FixtureRequest(self, fdef)
                continue

            dep, dep_index = self._dep_of(fdef, index, argname)
            kwargs[argname] = self._value_of(dep, dep_index)

        return kwargs

    async def _resolve_args_async(
        self,
        fdef: FixtureDef,
        index: int,
    ) -> dict[str, object]:
        kwargs: dict[str, object] = {}
        for argname in fdef.argnames:
            if argname == "request":
                kwargs[argname] = FixtureRequest(self, fdef)
                continue

            dep, dep_index = self._dep_of(fdef, index, argname)
            kwargs[argname] = await self._value_of_async(dep, dep_index)

        return kwargs

    def _instantiate(
        self,
        fdef: FixtureDef,
        scope_key: object,
        kwargs: dict[str, object],
    ) -> object:
        func = fdef.func
        args: tuple[object, ...] = ()
        if fdef.needs_instance:
            args = (self.instance,)

        if inspect.isgeneratorfunction(func):
            gen = func(*args, **kwargs)
            assert isinstance(gen, types.GeneratorType)
            value = next(gen)

            def finalizer() -> None:
                try:
                    next(gen)
                except StopIteration:
                    pass
                else:
                    raise FixtureError(
                        f"fixture {fdef.name!r} in {fdef.source} yielded "
                        f"more than once"
                    )

            _engine.add_finalizer(fdef.scope, scope_key, finalizer)
            return value

        return func(*args, **kwargs)

    async def _instantiate_async(
        self,
        fdef: FixtureDef,
        scope_key: object,
        kwargs: dict[str, object],
    ) -> object:
        func = fdef.func
        args: tuple[object, ...] = ()
        if fdef.needs_instance:
            args = (self.instance,)

        if inspect.isasyncgenfunction(func):
            agen = func(*args, **kwargs)
            assert isinstance(agen, types.AsyncGeneratorType)
            value = await anext(agen)

            async def finalizer() -> None:
                try:
                    await anext(agen)
                except StopAsyncIteration:
                    pass
                else:
                    raise FixtureError(
                        f"fixture {fdef.name!r} in {fdef.source} yielded "
                        f"more than once"
                    )

            self._async_finalizers.append(finalizer)
            return value

        if inspect.iscoroutinefunction(func):
            coro = func(*args, **kwargs)
            assert inspect.isawaitable(coro)
            return await coro

        # A synchronous fixture requested from an async context.
        return self._instantiate(fdef, scope_key, kwargs)


def test_execution(
    *,
    mod: types.ModuleType,
    synth_cls: type,
    orig_cls: type | None = None,
    instance: object | None = None,
    test_name: str,
    param_bindings: Mapping[FixtureDef, int] | None = None,
    item_marks: Sequence[Any] = (),
) -> TestExecution:
    return TestExecution(
        mod=mod,
        synth_cls=synth_cls,
        orig_cls=orig_cls,
        instance=instance,
        test_name=test_name,
        param_bindings=param_bindings,
        item_marks=item_marks,
    )
