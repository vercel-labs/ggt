# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

"""Synthesis of ``unittest.TestCase`` classes from pytest-style tests.

``synthesize_module`` is idempotent and deterministic: given the same
imported module it always builds identical classes with identical
names.  This property is load-bearing — the parent process synthesizes
at collection time, while workers (which re-import test modules under
the spawn/forkserver start methods) re-run the synthesis when
restoring pickled test cases, and both must resolve to the same
``module.ClassName`` locations.
"""

from __future__ import annotations

import dataclasses
import functools
import inspect
import itertools
import sys
import traceback
import unittest
from typing import TYPE_CHECKING, Any, cast

from .. import marks as ggt_marks
from . import anyio_bridge, collect, hypothesis_bridge, inicfg, marks, shared
from . import fixtures as fixture_engine

if TYPE_CHECKING:
    import types
    from collections.abc import Callable, Mapping, Sequence

    _TestMethod = Callable[[unittest.TestCase], object]

MODULE_MARKER = collect.MODULE_MARKER
CLASS_MARKER = collect.CLASS_MARKER
FUNCTIONS_CLASS_NAME = "_GGTPytestFunctions"
CLASS_NAME_PREFIX = "_GGTPytest_"

_POSITIONAL_KINDS = frozenset(
    {
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    }
)


def _call_with_optional_arg(
    hook: Callable[..., object],
    arg: object,
) -> None:
    """Call an xunit hook, passing its optional argument if accepted.

    pytest's xunit hooks come in two flavors, e.g. both
    ``setup_method(self, method)`` and ``setup_method(self)`` are
    valid.  Dispatch on the hook's signature.
    """
    try:
        sig = inspect.signature(hook)
    except (TypeError, ValueError):
        hook(arg)
        return

    accepts_arg = any(
        p.kind in _POSITIONAL_KINDS
        or p.kind is inspect.Parameter.VAR_POSITIONAL
        for p in sig.parameters.values()
    )
    if accepts_arg:
        _call_hook(hook, arg)
    else:
        _call_hook(hook)


def _call_class_hook(orig_cls: type, hook_name: str) -> None:
    hook = getattr(orig_cls, hook_name)
    if inspect.ismethod(hook):
        # A classmethod, already bound to the class.
        _call_hook(hook)
    else:
        # A plain function accessed through the class.
        _call_hook(hook, orig_cls)


def _test_name_of(test: unittest.TestCase) -> str:
    return test.id().rpartition(".")[2]


def _translate_outcome(e: BaseException) -> BaseException | None:
    """Translate pytest's imperative outcomes into unittest ones.

    ``pytest.skip()`` and ``pytest.fail()`` raise ``OutcomeException``
    subclasses; duck-type them (pytest masks their ``__module__`` as
    ``builtins``, so identify them by name plus the ``msg``/``pytrace``
    attributes every OutcomeException carries) so that pytest is never
    imported here.
    """
    cls = type(e)
    if not (hasattr(e, "msg") and hasattr(e, "pytrace")):
        return None
    if cls.__name__ == "Skipped":
        return unittest.SkipTest(str(e) or "skipped")
    if cls.__name__ == "Failed":
        return AssertionError(str(e) or "failed")
    return None


def _call_hook(hook: Callable[..., object], *args: object) -> None:
    """Call an xunit hook, translating pytest outcomes.

    ``pytest.skip()``/``pytest.importorskip()`` raise ``Skipped``, a
    ``BaseException`` subclass that would otherwise sail through
    unittest's class/module setup handling and kill the worker
    process.
    """
    try:
        hook(*args)
    except BaseException as e:
        translated = _translate_outcome(e)
        if translated is not None:
            raise translated from e
        raise


def _call_test(fn: Callable[..., object], kwargs: dict[str, object]) -> None:
    try:
        fn(**kwargs)
    except BaseException as e:
        translated = _translate_outcome(e)
        if translated is not None:
            raise translated from e
        raise


async def _call_async_test(
    fn: Callable[..., object],
    kwargs: dict[str, object],
) -> None:
    try:
        result = fn(**kwargs)
        assert inspect.isawaitable(result)
        await result
    except BaseException as e:
        translated = _translate_outcome(e)
        if translated is not None:
            raise translated from e
        raise


def _is_async_test(func: Callable[..., object]) -> bool:
    """True for coroutine functions, including decorated ones.

    Wrappers like hypothesis's ``@given`` produce a sync callable
    around an async test; follow the ``__wrapped__`` chain (and
    hypothesis's ``.hypothesis.inner_test``) so such tests still
    count as async for collection purposes (e.g. anyio backend
    expansion).
    """
    if inspect.iscoroutinefunction(func):
        return True

    if hypothesis_bridge.async_inner(func) is not None:
        return True

    try:
        unwrapped = inspect.unwrap(func)
    except ValueError:
        return False
    return inspect.iscoroutinefunction(unwrapped)


def _call_sync_test(
    execution: fixture_engine.TestExecution,
    fn: Callable[..., object],
    kwargs: dict[str, object],
    *,
    params: Mapping[str, object],
    use_anyio: bool,
) -> None:
    """Call a synchronous test callable, bridging hypothesis wrappers.

    A hypothesis ``@given`` around an async test produces a sync
    wrapper whose inner test is a coroutine function; run each
    generated example on the appropriate backend, the way the
    pytest-asyncio/anyio plugins do.
    """
    current_inner = hypothesis_bridge.current_inner(fn)
    if current_inner is not None and (params or execution.param_bindings):
        hypothesis_bridge.suppress_differing_executors(fn)
    if current_inner is None:
        _call_test(fn, kwargs)
        return

    if inspect.iscoroutinefunction(current_inner):
        hypothesis_bridge.install_bridge(fn, current_inner)
    elif not hypothesis_bridge.is_bridged(current_inner):
        # A plain sync hypothesis test.
        _call_test(fn, kwargs)
        return

    backend: tuple[str, dict[str, Any]] | None = None
    if use_anyio:
        backend = anyio_bridge.resolve_backend(execution, params)

    with hypothesis_bridge.backend_context(backend):
        _call_test(fn, kwargs)


def _run_anyio_test(
    execution: fixture_engine.TestExecution,
    fn: Callable[..., object],
    *,
    params: Mapping[str, object],
    usefixtures: Sequence[str],
    argnames: Sequence[str],
    setup: Callable[..., object] | None = None,
    teardown: Callable[..., object] | None = None,
    hook_arg: object = None,
) -> None:
    """Run a pytest.mark.anyio test on its anyio_backend."""
    anyio_bridge.run_anyio_test(
        execution,
        functools.partial(
            _run_async_body,
            execution,
            fn,
            params=params,
            usefixtures=usefixtures,
            argnames=argnames,
            setup=setup,
            teardown=teardown,
            hook_arg=hook_arg,
        ),
        params=params,
    )


async def _run_async_body(
    execution: fixture_engine.TestExecution,
    fn: Callable[..., object],
    *,
    params: Mapping[str, object],
    usefixtures: Sequence[str],
    argnames: Sequence[str],
    setup: Callable[..., object] | None = None,
    teardown: Callable[..., object] | None = None,
    hook_arg: object = None,
) -> None:
    """Fixture resolution, test call, and async teardown in one loop.

    Async fixtures are bound to the test's event loop, so their
    finalizers must be awaited here, before the loop is closed —
    unlike sync finalizers, which run in TestExecution.__exit__.
    """
    try:
        await execution.resolve_autouse_async()
        for fixture_name in usefixtures:
            await execution.get_async(fixture_name)
        kwargs: dict[str, object] = {**params}
        for name in argnames:
            kwargs[name] = await execution.get_async(name)

        if setup is not None:
            _call_with_optional_arg(setup, hook_arg)
        try:
            await _call_async_test(fn, kwargs)
        finally:
            if teardown is not None:
                _call_with_optional_arg(teardown, hook_arg)
    except BaseException:
        try:
            await execution.run_async_finalizers()
        except BaseException:
            # The test already failed; report the teardown error
            # without masking the original failure.
            sys.stderr.write(
                f"error during async fixture teardown of "
                f"{execution.test_name}:\n{traceback.format_exc()}\n"
            )
        raise
    else:
        await execution.run_async_finalizers()


def _make_function_method(
    func: types.FunctionType,
    owner_name: str,
    mod: types.ModuleType,
    *,
    params: Mapping[str, object],
    usefixtures: Sequence[str],
    param_bindings: Mapping[fixture_engine.FixtureDef, int],
    exec_marks: Sequence[Any],
) -> _TestMethod:
    argnames = tuple(
        name
        for name in fixture_engine.fixture_params(func)
        if name not in params
    )

    method: _TestMethod

    if inspect.iscoroutinefunction(func) and anyio_bridge.has_anyio_mark(
        exec_marks
    ):

        @functools.wraps(func)
        def anyio_method(self: unittest.TestCase) -> None:
            with fixture_engine.test_execution(
                mod=mod,
                synth_cls=type(self),
                test_name=_test_name_of(self),
                param_bindings=param_bindings,
                item_marks=exec_marks,
            ) as execution:
                _run_anyio_test(
                    execution,
                    func,
                    params=params,
                    usefixtures=usefixtures,
                    argnames=argnames,
                )

        method = anyio_method
    elif inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_method(self: unittest.TestCase) -> None:
            with fixture_engine.test_execution(
                mod=mod,
                synth_cls=type(self),
                test_name=_test_name_of(self),
                param_bindings=param_bindings,
                item_marks=exec_marks,
            ) as execution:
                await _run_async_body(
                    execution,
                    func,
                    params=params,
                    usefixtures=usefixtures,
                    argnames=argnames,
                )

        method = async_method
    else:

        @functools.wraps(func)
        def sync_method(self: unittest.TestCase) -> None:
            with fixture_engine.test_execution(
                mod=mod,
                synth_cls=type(self),
                test_name=_test_name_of(self),
                param_bindings=param_bindings,
                item_marks=exec_marks,
            ) as execution:
                execution.resolve_autouse()
                for fixture_name in usefixtures:
                    execution.get(fixture_name)
                kwargs: dict[str, object] = {**params}
                kwargs.update((name, execution.get(name)) for name in argnames)
                _call_sync_test(
                    execution,
                    func,
                    kwargs,
                    params=params,
                    use_anyio=anyio_bridge.has_anyio_mark(exec_marks),
                )

        method = sync_method

    method.__qualname__ = f"{owner_name}.{func.__name__}"
    return method


def _make_class_method(
    orig_cls: type,
    meth_name: str,
    owner_name: str,
    mod: types.ModuleType,
    *,
    params: Mapping[str, object],
    usefixtures: Sequence[str],
    param_bindings: Mapping[fixture_engine.FixtureDef, int],
    exec_marks: Sequence[Any],
) -> _TestMethod:
    orig_func = inspect.getattr_static(orig_cls, meth_name)
    assert inspect.isfunction(orig_func)
    argnames = tuple(
        name
        for name in fixture_engine.fixture_params(orig_func, skip_first=True)
        if name not in params
    )

    method: _TestMethod

    if inspect.iscoroutinefunction(orig_func) and anyio_bridge.has_anyio_mark(
        exec_marks
    ):

        @functools.wraps(orig_func)
        def anyio_class_method(self: unittest.TestCase) -> None:
            # pytest instantiates the test class once per test method.
            instance = orig_cls()

            with fixture_engine.test_execution(
                mod=mod,
                synth_cls=type(self),
                orig_cls=orig_cls,
                instance=instance,
                test_name=_test_name_of(self),
                param_bindings=param_bindings,
                item_marks=exec_marks,
            ) as execution:
                _run_anyio_test(
                    execution,
                    getattr(instance, meth_name),
                    params=params,
                    usefixtures=usefixtures,
                    argnames=argnames,
                    setup=getattr(instance, "setup_method", None),
                    teardown=getattr(instance, "teardown_method", None),
                    hook_arg=orig_func,
                )

        method = anyio_class_method
    elif inspect.iscoroutinefunction(orig_func):

        @functools.wraps(orig_func)
        async def async_method(self: unittest.TestCase) -> None:
            # pytest instantiates the test class once per test method.
            instance = orig_cls()

            with fixture_engine.test_execution(
                mod=mod,
                synth_cls=type(self),
                orig_cls=orig_cls,
                instance=instance,
                test_name=_test_name_of(self),
                param_bindings=param_bindings,
                item_marks=exec_marks,
            ) as execution:
                await _run_async_body(
                    execution,
                    getattr(instance, meth_name),
                    params=params,
                    usefixtures=usefixtures,
                    argnames=argnames,
                    setup=getattr(instance, "setup_method", None),
                    teardown=getattr(instance, "teardown_method", None),
                    hook_arg=orig_func,
                )

        method = async_method
    else:

        @functools.wraps(orig_func)
        def sync_method(self: unittest.TestCase) -> None:
            # pytest instantiates the test class once per test method.
            instance = orig_cls()

            with fixture_engine.test_execution(
                mod=mod,
                synth_cls=type(self),
                orig_cls=orig_cls,
                instance=instance,
                test_name=_test_name_of(self),
                param_bindings=param_bindings,
                item_marks=exec_marks,
            ) as execution:
                execution.resolve_autouse()
                for fixture_name in usefixtures:
                    execution.get(fixture_name)
                kwargs: dict[str, object] = {**params}
                kwargs.update((name, execution.get(name)) for name in argnames)

                setup = getattr(instance, "setup_method", None)
                if setup is not None:
                    _call_with_optional_arg(setup, orig_func)
                try:
                    _call_sync_test(
                        execution,
                        getattr(instance, meth_name),
                        kwargs,
                        params=params,
                        use_anyio=anyio_bridge.has_anyio_mark(exec_marks),
                    )
                finally:
                    teardown = getattr(instance, "teardown_method", None)
                    if teardown is not None:
                        _call_with_optional_arg(teardown, orig_func)

        method = sync_method

    method.__qualname__ = f"{owner_name}.{meth_name}"
    return method


def _make_str_method(prefix: str) -> Callable[[unittest.TestCase], str]:
    def __str__(self: unittest.TestCase) -> str:
        # NOTE: str(test) is a load-bearing identity: it keys the
        # running-times log and sharding estimates, so it must be
        # deterministic across runs and processes.
        method_name = _test_name_of(self)
        ids = getattr(type(self), "__ggt_pytest_ids__", None)
        display = ids.get(method_name, method_name) if ids else method_name
        return f"{display} ({prefix}::{display})"

    return __str__


def _wrap_raises(
    method: _TestMethod,
    raises: tuple[type[BaseException], ...],
) -> _TestMethod:
    if inspect.iscoroutinefunction(method):
        async_method = method

        @functools.wraps(method)
        async def async_wrapper(self: unittest.TestCase) -> None:
            try:
                await async_method(self)
            except unittest.SkipTest:
                raise
            except raises as e:
                # ggt classifies AssertionErrors as "expected
                # failures" (as opposed to unexpected errors) — see
                # runner._is_expecting_failure.
                raise AssertionError(f"expected failure: {e!r}") from e

        return async_wrapper

    @functools.wraps(method)
    def wrapper(self: unittest.TestCase) -> None:
        try:
            method(self)
        except unittest.SkipTest:
            raise
        except raises as e:
            # ggt classifies AssertionErrors as "expected failures"
            # (as opposed to unexpected errors) — see
            # runner._is_expecting_failure.
            raise AssertionError(f"expected failure: {e!r}") from e

    return wrapper


def _apply_outcome_marks(
    method: _TestMethod,
    all_marks: Sequence[Any],
    mod: types.ModuleType,
) -> _TestMethod:
    reason = marks.skip_reason(all_marks, mod)
    if reason is not None:
        return unittest.skip(reason)(method)

    xf = marks.xfail_info(all_marks, mod)
    if xf is not None:
        if xf.raises is not None:
            method = _wrap_raises(method, xf.raises)
        method.__ggt_xfail_reason__ = xf.reason  # type: ignore [attr-defined]  # ty: ignore[unresolved-attribute]
        method.__ggt_xfail_allow_failure__ = True  # type: ignore [attr-defined]  # ty: ignore[unresolved-attribute]
        # With raises=..., only the specified exceptions (converted
        # into AssertionErrors by _wrap_raises) count as expected.
        method.__ggt_xfail_allow_error__ = xf.raises is None  # type: ignore [attr-defined]  # ty: ignore[unresolved-attribute]
        method.__ggt_xfail_strict__ = xf.strict  # type: ignore [attr-defined]  # ty: ignore[unresolved-attribute]
        method = unittest.expectedFailure(method)

    return method


def _unique_name(base: str, taken: Mapping[str, Any]) -> str:
    if base not in taken:
        return base
    for i in range(2, 10000):
        candidate = f"{base}_{i}"
        if candidate not in taken:
            return candidate
    raise RuntimeError(f"cannot find a unique method name for {base}")


@dataclasses.dataclass(frozen=True)
class _FixtureCase:
    """One choice of parameters for the reachable parametrized fixtures."""

    pretty_id: str
    bindings: dict[fixture_engine.FixtureDef, int]
    marks: tuple[Any, ...]
    skip_reason: str | None = None


def _fixture_case_ids(fdef: fixture_engine.FixtureDef) -> list[_FixtureCase]:
    params = fdef.params
    assert params is not None

    if not params:
        return [
            _FixtureCase(
                pretty_id=f"{fdef.name}0",
                bindings={},
                marks=(),
                skip_reason=(
                    f"got empty parameter set for fixture {fdef.name!r}"
                ),
            )
        ]

    cases: list[_FixtureCase] = []
    for index, item in enumerate(params):
        param_marks: tuple[Any, ...] = ()
        param_id: str | None = None
        if marks.is_parameter_set(item):
            param_marks = tuple(
                m
                for m in (marks.as_mark(mm) for mm in item.marks)  # type: ignore [attr-defined]  # ty: ignore[unresolved-attribute]
                if m is not None
            )
            raw_id = item.id  # type: ignore [attr-defined]  # ty: ignore[unresolved-attribute]
            param_id = str(raw_id) if raw_id is not None else None

        payload = fixture_engine.param_payload(item)

        if param_id is None:
            ids = fdef.ids
            candidate: object = None
            if callable(ids):
                candidate = cast("Callable[[object], object]", ids)(payload)
            elif isinstance(ids, (list, tuple)):
                try:
                    candidate = ids[index]
                except IndexError:
                    candidate = None
            if candidate is not None:
                param_id = str(candidate)

        if param_id is None:
            param_id = marks.value_id(payload, fdef.name, index)

        cases.append(
            _FixtureCase(
                pretty_id=param_id,
                bindings={fdef: index},
                marks=param_marks,
            )
        )

    return cases


def _fixture_param_cases(
    registry: fixture_engine.Registry,
    roots: Sequence[tuple[str, int]],
) -> list[_FixtureCase] | None:
    """Expand the parametrized fixtures reachable from *roots*.

    Returns None when no parametrized fixtures are involved; otherwise
    the cross product of all parameter choices, in the deterministic
    order of the closure walk.
    """
    pdefs: list[fixture_engine.FixtureDef] = []
    seen: set[fixture_engine.FixtureDef] = set()
    for fdef, _index in fixture_engine.walk_fixture_defs(registry, roots):
        if fdef.params is not None and fdef not in seen:
            seen.add(fdef)
            pdefs.append(fdef)

    if not pdefs:
        return None

    combined: list[_FixtureCase] = []
    for combo in itertools.product(*[_fixture_case_ids(d) for d in pdefs]):
        bindings: dict[fixture_engine.FixtureDef, int] = {}
        combo_marks: list[Any] = []
        skip_reason: str | None = None
        for case in combo:
            bindings.update(case.bindings)
            combo_marks.extend(case.marks)
            if skip_reason is None:
                skip_reason = case.skip_reason
        combined.append(
            _FixtureCase(
                pretty_id="-".join(case.pretty_id for case in combo),
                bindings=bindings,
                marks=tuple(combo_marks),
                skip_reason=skip_reason,
            )
        )

    return combined


def _expand_test_item(
    *,
    base_name: str,
    item_marks: list[Any],
    mod: types.ModuleType,
    methods: dict[str, Any],
    ids_map: dict[str, str],
    registry: fixture_engine.Registry,
    base_argnames: Sequence[str],
    is_async: bool,
    maker: Callable[
        [
            Mapping[str, object],
            Sequence[str],
            Mapping[fixture_engine.FixtureDef, int],
            Sequence[Any],
        ],
        _TestMethod,
    ],
) -> None:
    usefixtures = (
        *inicfg.current().usefixtures,
        *marks.usefixtures_names(item_marks),
    )
    mark_cases = marks.parametrize_cases(item_marks, base_name)

    # Direct parametrization of a name shadows a fixture of the same
    # name, so exclude those names from the fixture closure walk.
    excluded = set(mark_cases[0].values) if mark_cases else set()
    roots: list[tuple[str, int]] = [
        (argname, 0) for argname in base_argnames if argname not in excluded
    ]
    roots.extend((fixture_name, 0) for fixture_name in usefixtures)
    roots.extend((fdef.name, index) for fdef, index in registry.autouse_defs())
    if (
        is_async
        and anyio_bridge.has_anyio_mark(item_marks)
        and "anyio_backend" not in excluded
    ):
        # pytest.mark.anyio implicitly pulls in the anyio_backend
        # fixture (like anyio's own plugin does); a parametrized
        # user override expands the test per backend.  A direct
        # @parametrize("anyio_backend", ...) shadows the fixture
        # entirely, hence the exclusion.
        roots.append(("anyio_backend", 0))
    fixture_cases = _fixture_param_cases(registry, roots)

    plain = mark_cases is None and fixture_cases is None

    mark_case_list: list[marks.ParamCase | None] = (
        list(mark_cases) if mark_cases is not None else [None]
    )
    fixture_case_list: list[_FixtureCase | None] = (
        list(fixture_cases) if fixture_cases is not None else [None]
    )

    for mark_case in mark_case_list:
        for fixture_case in fixture_case_list:
            params = mark_case.values if mark_case is not None else {}
            bindings = (
                fixture_case.bindings if fixture_case is not None else {}
            )
            case_marks = [
                *(mark_case.marks if mark_case is not None else ()),
                *(fixture_case.marks if fixture_case is not None else ()),
            ]
            all_marks = [*case_marks, *item_marks]
            exec_marks = (*item_marks, *case_marks)

            method = maker(params, usefixtures, bindings, exec_marks)
            if fixture_case is not None and fixture_case.skip_reason:
                method = unittest.skip(fixture_case.skip_reason)(method)
            else:
                method = _apply_outcome_marks(method, all_marks, mod)

            if plain:
                name = base_name
            else:
                id_parts = [
                    part
                    for part in (
                        mark_case.pretty_id if mark_case is not None else "",
                        (
                            fixture_case.pretty_id
                            if fixture_case is not None
                            else ""
                        ),
                    )
                    if part
                ]
                pretty = "-".join(id_parts)
                suffix = marks.sanitize_identifier(pretty)
                name = _unique_name(f"{base_name}__pv_{suffix}", methods)
                ids_map[name] = f"{base_name}[{pretty}]"

            # Translate pytest marks into ggt marks, consumed by -m
            # mark-expression filtering in the loader.  Every
            # synthesized method carries the attribute (possibly
            # empty): the loader also uses its presence to enumerate
            # test methods of synthesized classes.
            mark_names = frozenset(
                mark.name
                for mark in all_marks
                if isinstance(getattr(mark, "name", None), str)
            )
            setattr(method, ggt_marks.MARKS_ATTR, mark_names)
            methods[name] = method


def _make_set_up_class_method(
    orig_cls: type,
) -> classmethod:  # type: ignore [type-arg]
    def setUpClass(cls: type[unittest.TestCase]) -> None:
        _call_class_hook(orig_cls, "setup_class")

    return classmethod(setUpClass)


def _make_tear_down_class_method(
    orig_cls: type | None,
) -> classmethod:  # type: ignore [type-arg]
    def tearDownClass(cls: type[unittest.TestCase]) -> None:
        # Class-scoped fixtures were set up after (and depend on)
        # xunit setup_class, so tear them down first.
        try:
            fixture_engine.teardown_class(cls)
        finally:
            if orig_cls is not None and hasattr(orig_cls, "teardown_class"):
                _call_class_hook(orig_cls, "teardown_class")

    return classmethod(tearDownClass)


def _build_case(
    mod: types.ModuleType,
    name: str,
    str_prefix: str,
    methods: dict[str, Any],
    *,
    ids_map: dict[str, str] | None = None,
    base: type[unittest.TestCase] = unittest.TestCase,
) -> None:
    existing = vars(mod).get(name)
    if existing is not None and not getattr(existing, CLASS_MARKER, False):
        raise RuntimeError(
            f"cannot synthesize pytest-compatibility test class "
            f"{mod.__name__}.{name}: the module already has an "
            f"attribute with that name"
        )

    attrs: dict[str, Any] = {
        "__module__": mod.__name__,
        "__qualname__": name,
        "__str__": _make_str_method(str_prefix),
        "__ggt_pytest_ids__": ids_map or {},
        CLASS_MARKER: True,
        **methods,
    }
    case = cast(
        "type[unittest.TestCase]",
        type(name, (base,), attrs),
    )
    setattr(mod, name, case)


def _base_for(methods: Mapping[str, Any]) -> type[unittest.TestCase]:
    """The TestCase base for a group of synthesized methods.

    If any synthesized method is a coroutine function, the whole class
    derives from IsolatedAsyncioTestCase (which runs sync methods just
    fine) so that a class is never split in two and xunit class hooks
    run exactly once.  Each async test gets a fresh event loop, the
    same semantics as pytest-asyncio's default loop scope.  Tests
    marked pytest.mark.anyio synthesize into *sync* methods (they run
    their own backend loop via anyio.run), so they do not force the
    async base.
    """
    if any(inspect.iscoroutinefunction(method) for method in methods.values()):
        return unittest.IsolatedAsyncioTestCase
    return unittest.TestCase


def _install_module_hooks(mod: types.ModuleType) -> None:
    setup = getattr(mod, "setup_module", None)
    if callable(setup) and getattr(mod, "setUpModule", None) is None:
        setup_hook = cast("Callable[..., object]", setup)

        def set_up_module() -> None:
            _call_with_optional_arg(setup_hook, mod)

        set_up_module.__name__ = "setUpModule"
        mod.setUpModule = set_up_module  # type: ignore [attr-defined]  # ty: ignore[unresolved-attribute]

    # Always install a tearDownModule so that module-scoped fixtures
    # are torn down when unittest transitions between test modules.
    user_teardown = getattr(mod, "teardown_module", None)
    existing_teardown = getattr(mod, "tearDownModule", None)
    user_teardown_hook = (
        cast("Callable[..., object]", user_teardown)
        if callable(user_teardown)
        else None
    )
    existing_teardown_hook = (
        cast("Callable[[], object]", existing_teardown)
        if callable(existing_teardown)
        else None
    )

    def tear_down_module() -> None:
        # Module-scoped fixtures were set up after xunit
        # setup_module, so tear them down first (LIFO).
        try:
            fixture_engine.teardown_module(mod.__name__)
        finally:
            if existing_teardown_hook is not None:
                existing_teardown_hook()
            elif user_teardown_hook is not None:
                _call_with_optional_arg(user_teardown_hook, mod)

    tear_down_module.__name__ = "tearDownModule"
    mod.tearDownModule = tear_down_module  # type: ignore [attr-defined]  # ty: ignore[unresolved-attribute]


def synthesize_module(mod: types.ModuleType) -> None:
    """Inject synthesized TestCase classes for pytest-style tests.

    Idempotent; safe to call on any module, including pure-unittest
    ones (where it is a no-op).
    """
    if getattr(mod, MODULE_MARKER, False):
        return

    plan = collect.scan_module(mod)

    if not plan.empty:
        _install_module_hooks(mod)

    if plan.function_names:
        methods: dict[str, Any] = {}
        ids_map: dict[str, str] = {}
        functions_registry = fixture_engine.registry_for(mod, None)
        for name in plan.function_names:
            func = getattr(mod, name)

            def make_function(
                params: Mapping[str, object],
                usefixtures: Sequence[str],
                param_bindings: Mapping[fixture_engine.FixtureDef, int],
                exec_marks: Sequence[Any],
                func: types.FunctionType = func,
            ) -> _TestMethod:
                return _make_function_method(
                    func,
                    FUNCTIONS_CLASS_NAME,
                    mod,
                    params=params,
                    usefixtures=usefixtures,
                    param_bindings=param_bindings,
                    exec_marks=exec_marks,
                )

            _expand_test_item(
                base_name=name,
                item_marks=marks.effective_marks(mod, None, func),
                mod=mod,
                methods=methods,
                ids_map=ids_map,
                registry=functions_registry,
                base_argnames=fixture_engine.fixture_params(func),
                is_async=_is_async_test(func),
                maker=make_function,
            )

        base = _base_for(methods)
        methods["tearDownClass"] = _make_tear_down_class_method(None)
        _build_case(
            mod,
            FUNCTIONS_CLASS_NAME,
            str_prefix=mod.__name__,
            methods=methods,
            ids_map=ids_map,
            base=base,
        )

    for class_plan in plan.classes:
        name = f"{CLASS_NAME_PREFIX}{class_plan.name}"
        methods = {}
        ids_map = {}
        class_registry = fixture_engine.registry_for(mod, class_plan.cls)
        for meth_name in class_plan.method_names:
            orig_func = inspect.getattr_static(class_plan.cls, meth_name)

            def make_method(
                params: Mapping[str, object],
                usefixtures: Sequence[str],
                param_bindings: Mapping[fixture_engine.FixtureDef, int],
                exec_marks: Sequence[Any],
                *,
                meth_name: str = meth_name,
                owner_name: str = name,
                orig_cls: type = class_plan.cls,
            ) -> _TestMethod:
                return _make_class_method(
                    orig_cls,
                    meth_name,
                    owner_name,
                    mod,
                    params=params,
                    usefixtures=usefixtures,
                    param_bindings=param_bindings,
                    exec_marks=exec_marks,
                )

            _expand_test_item(
                base_name=meth_name,
                item_marks=marks.effective_marks(
                    mod, class_plan.cls, orig_func
                ),
                mod=mod,
                methods=methods,
                ids_map=ids_map,
                registry=class_registry,
                base_argnames=fixture_engine.fixture_params(
                    orig_func, skip_first=True
                ),
                is_async=_is_async_test(orig_func),
                maker=make_method,
            )

        base = _base_for(methods)
        if hasattr(class_plan.cls, "setup_class"):
            methods["setUpClass"] = _make_set_up_class_method(class_plan.cls)
        methods["tearDownClass"] = _make_tear_down_class_method(class_plan.cls)
        _build_case(
            mod,
            name,
            str_prefix=f"{mod.__name__}::{class_plan.name}",
            methods=methods,
            ids_map=ids_map,
            base=base,
        )

    shared.attach_shared_fixtures(mod, plan)

    setattr(mod, MODULE_MARKER, True)
