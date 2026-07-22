# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

"""Emulation of anyio's pytest plugin contract.

``pytest.mark.anyio`` tests run on the backend supplied by the
``anyio_backend`` fixture — parametrized over the installed backends
by default, overridable from user conftests, and shadowable by a
direct ``@pytest.mark.parametrize("anyio_backend", ...)`` — exactly
like anyio's own pytest plugin arranges it.  anyio itself is only
imported when a marked test actually runs.
"""

from __future__ import annotations

import importlib.util
from typing import TYPE_CHECKING, Any, cast

from . import builtin_fixtures

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from . import fixtures as fixture_engine


def has_anyio_mark(marks_seq: Sequence[Any]) -> bool:
    return any(getattr(m, "name", None) == "anyio" for m in marks_seq)


def available_backends() -> tuple[str, ...]:
    # Mirrors anyio's pytest plugin: the default anyio_backend fixture
    # is parametrized over every installed backend.
    backends = ["asyncio"]
    if importlib.util.find_spec("trio") is not None:
        backends.append("trio")
    return tuple(backends)


def split_backend(backend: object) -> tuple[str, dict[str, Any]]:
    """Normalize an anyio_backend value to (name, options)."""
    if isinstance(backend, str):
        return backend, {}
    name, options = cast("tuple[object, Any]", backend)
    return str(name), dict(options)


_NOTSET: Any = object()


def resolve_backend(
    execution: fixture_engine.TestExecution,
    params: Mapping[str, object],
) -> tuple[str, dict[str, Any]]:
    # A direct @parametrize("anyio_backend", ...) supplies the value
    # through params (shadowing the fixture, as in pytest).
    backend = params.get("anyio_backend", _NOTSET)
    if backend is _NOTSET:
        backend = execution.get("anyio_backend")
    return split_backend(backend)


def run_anyio_test(
    execution: fixture_engine.TestExecution,
    body: Callable[[], Awaitable[None]],
    *,
    params: Mapping[str, object],
) -> None:
    """Run a pytest.mark.anyio test body on its anyio_backend."""
    try:
        import anyio  # noqa: PLC0415
    except ImportError as e:
        raise RuntimeError(
            f"{execution.test_name} is marked with pytest.mark.anyio "
            f"but the anyio package is not installed"
        ) from e

    backend_name, backend_options = resolve_backend(execution, params)
    anyio.run(body, backend=backend_name, backend_options=backend_options)


def _anyio_backend(request: Any) -> object:
    return request.param


def _anyio_backend_name(anyio_backend: object) -> str:
    return split_backend(anyio_backend)[0]


def _anyio_backend_options(anyio_backend: object) -> dict[str, Any]:
    return split_backend(anyio_backend)[1]


builtin_fixtures.register(
    "anyio_backend",
    scope="module",
    argnames=("request",),
    func=_anyio_backend,
    params=available_backends(),
)
builtin_fixtures.register(
    "anyio_backend_name",
    argnames=("anyio_backend",),
    func=_anyio_backend_name,
)
builtin_fixtures.register(
    "anyio_backend_options",
    argnames=("anyio_backend",),
    func=_anyio_backend_options,
)
