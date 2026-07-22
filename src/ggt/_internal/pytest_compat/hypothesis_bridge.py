# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

"""Bridging of hypothesis-wrapped async tests.

hypothesis's ``@given`` produces a synchronous wrapper whose inner
test may be a coroutine function; each generated example must then
run on the test's event loop, the way the pytest-asyncio/anyio
plugins arrange it.  The bridge replaces the inner test with a sync
trampoline, installed *permanently* and picking the backend up
dynamically from a context variable set around the wrapper call:
hypothesis's ``differing_executors`` health check fires if the
executor object changes between runs of the same test (e.g. across
anyio backend variants), so the trampoline must stay stable.

hypothesis itself is only imported when a wrapped test actually
runs; wrappers are recognized by the ``.hypothesis.inner_test``
attributes ``@given`` attaches.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import functools
import inspect
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

BRIDGE_MARKER = "__ggt_hypothesis_bridge__"

# The anyio backend for the currently executing hypothesis-wrapped
# async test; None means run examples with asyncio.run.
_backend: contextvars.ContextVar[tuple[str, dict[str, Any]] | None] = (
    contextvars.ContextVar("ggt_hypothesis_backend", default=None)
)


def current_inner(
    fn: Callable[..., object],
) -> Callable[..., object] | None:
    """The inner test of *fn* if it is a hypothesis @given wrapper."""
    return getattr(getattr(fn, "hypothesis", None), "inner_test", None)


def async_inner(
    fn: Callable[..., object],
) -> Callable[..., object] | None:
    """The inner test of *fn* if it is a wrapped coroutine function."""
    inner = current_inner(fn)
    if inner is not None and inspect.iscoroutinefunction(inner):
        return inner
    return None


def is_bridged(fn: Callable[..., object]) -> bool:
    return bool(getattr(fn, BRIDGE_MARKER, False))


def install_bridge(
    fn: Callable[..., object],
    inner_test: Callable[..., object],
) -> None:
    """Permanently bridge an async hypothesis inner test."""

    @functools.wraps(inner_test)
    def bridged(*args: Any, **kw: Any) -> None:
        backend = _backend.get()
        if backend is None:
            coro = inner_test(*args, **kw)
            assert inspect.isawaitable(coro)
            asyncio.run(cast("Any", coro))
        else:
            import anyio  # noqa: PLC0415

            example = cast(
                "Callable[[], Any]",
                functools.partial(inner_test, *args, **kw),
            )
            anyio.run(example, backend=backend[0], backend_options=backend[1])

    setattr(bridged, BRIDGE_MARKER, True)
    fn.hypothesis.inner_test = bridged  # type: ignore [attr-defined]  # ty: ignore[unresolved-attribute]


@contextlib.contextmanager
def backend_context(
    backend: tuple[str, dict[str, Any]] | None,
) -> Iterator[None]:
    """Expose the anyio backend to bridged inner tests."""
    token = _backend.set(backend)
    try:
        yield
    finally:
        _backend.reset(token)


def suppress_differing_executors(fn: Callable[..., object]) -> None:
    """Disable hypothesis's differing_executors check for *fn*.

    Parametrized variants of the same hypothesis-wrapped test trip
    the check as a false alarm; hypothesis's own pytest plugin
    disables it for parametrized tests for the same reason
    (hypothesis issue #3733).
    """
    try:
        from hypothesis import HealthCheck  # noqa: PLC0415
        from hypothesis import settings as hyp_settings  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        return

    target = getattr(fn, "__func__", fn)
    current = (
        getattr(target, "_hypothesis_internal_use_settings", None)
        or hyp_settings()
    )
    suppressed = set(current.suppress_health_check)
    if HealthCheck.differing_executors in suppressed:
        return
    setattr(  # noqa: B010
        target,
        "_hypothesis_internal_use_settings",
        hyp_settings(
            parent=current,
            suppress_health_check=(
                {HealthCheck.differing_executors} | suppressed
            ),
        ),
    )
