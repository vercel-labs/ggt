# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

"""Parent-process execution of session/module-scoped pytest fixtures.

ggt's native fixture protocol runs expensive setup once in the runner
process and ships the resulting state to workers.  This module bridges
pytest fixtures into that model: for every session- and module-scoped
fixture (transitively) used by a module's tests, a
:class:`SharedFixtureAdapter` — structurally implementing the
``loader.Fixture`` protocol — is attached to the module's synthesized
TestCase classes.  The ggt runner then:

- executes the fixture once in the parent via ``set_up()``;
- ships the pickled value to workers through the standard
  ``GGT_TEST_GLOBAL_DATA`` channel (``get_shared_data()`` returns a
  JSON-safe envelope: inline base64 for small values, a temp-file
  reference for large ones);
- workers hydrate the value in ``set_shared_data()``, seeding the
  fixture engine so the fixture never executes there;
- teardown (the post-``yield`` part) runs in the parent via
  ``tear_down()``.

Values that cannot be pickled fall back to lazy per-worker execution
(pytest-xdist semantics) with a one-time warning.  The whole mechanism
can be disabled by setting ``GGT_PYTEST_SHARED_FIXTURES=0``.
"""

from __future__ import annotations

import base64
import hashlib
import inspect
import os
import pathlib
import pickle  # noqa: S403
import tempfile
import warnings
from typing import TYPE_CHECKING, Any

from . import collect
from . import fixtures as fixture_engine

if TYPE_CHECKING:
    import types
    import unittest
    from collections.abc import Mapping, Sequence

ENV_DISABLE = "GGT_PYTEST_SHARED_FIXTURES"

# Envelopes larger than this are spilled to a temporary file to avoid
# blowing past OS environment-size limits.
SPILL_THRESHOLD = 16 * 1024

PICKLE_KEY = "__ggt_pickle__"
PICKLE_FILE_KEY = "__ggt_pickle_file__"


def shared_fixtures_enabled() -> bool:
    return os.environ.get(ENV_DISABLE) != "0"


class SharedFixtureAdapter:
    """Adapts a pytest fixture to ggt's ``loader.Fixture`` protocol."""

    def __init__(
        self,
        fdef: fixture_engine.FixtureDef,
        mod: types.ModuleType,
    ) -> None:
        self._fdef = fdef
        self._mod = mod
        self._value: object = None
        self._ran = False
        self._spill_path: pathlib.Path | None = None
        self._last_imported: object = None

    def _scope_key(self) -> object:
        return self._mod.__name__ if self._fdef.scope == "module" else None

    # -- the loader.Fixture protocol --------------------------------

    def __get__(
        self,
        instance: Any | None,
        owner: type[Any] | None = None,
        /,
    ) -> Any:
        return self

    def set_options(self, options: Mapping[str, str]) -> None:
        pass

    async def set_up(self, ui: Any) -> None:
        execution = fixture_engine.test_execution(
            mod=self._mod,
            synth_cls=SharedFixtureAdapter,
            test_name=f"<shared setup of {self._fdef.name!r}>",
        )
        index = execution.registry.index_of(self._fdef)
        if index is None:
            return

        try:
            self._value = execution.resolve_def(self._fdef, index)
        except Exception as e:
            # Let the tests run the fixture themselves so that the
            # error is attributed to each affected test.
            ui.warning(
                f"\npytest fixture {self._fdef.name!r} "
                f"(from {self._fdef.source}) failed during shared setup "
                f"and will be retried in test workers: {e}\n"
            )
        else:
            self._ran = True

    async def tear_down(self, ui: Any) -> None:
        if self._spill_path is not None:
            try:
                self._spill_path.unlink()
            except OSError:
                pass
            self._spill_path = None

        if self._ran:
            self._ran = False
            if self._fdef.scope == "module":
                fixture_engine.teardown_module(self._mod.__name__)
            else:
                fixture_engine.teardown_session()

    async def post_session_set_up(
        self,
        cases: Sequence[type[Any]],
        *,
        ui: Any,
    ) -> None:
        pass

    def get_shared_data(self) -> object:
        if not self._ran:
            return None

        try:
            blob = pickle.dumps(self._value)
        except Exception as e:
            warnings.warn(
                f"the value of pytest fixture {self._fdef.name!r} "
                f"(from {self._fdef.source}) could not be pickled and "
                f"will be re-created in every test worker: {e}",
                stacklevel=2,
            )
            return None

        encoded = base64.b64encode(blob).decode("ascii")
        if len(encoded) <= SPILL_THRESHOLD:
            return {PICKLE_KEY: encoded}

        fd, spill_name = tempfile.mkstemp(prefix="ggt-fixture-")
        with os.fdopen(fd, "wb") as f:
            f.write(blob)
        self._spill_path = pathlib.Path(spill_name)
        return {PICKLE_FILE_KEY: spill_name}

    def set_shared_data(self, data: object) -> None:
        if data is self._last_imported:
            # import_global_fixture_data() re-runs on every class
            # transition with the same parsed payload; only the first
            # application matters.
            return
        self._last_imported = data

        if not isinstance(data, dict):
            return

        inline = data.get(PICKLE_KEY)
        spill = data.get(PICKLE_FILE_KEY)
        if isinstance(inline, str):
            blob = base64.b64decode(inline)
        elif isinstance(spill, str):
            blob = pathlib.Path(spill).read_bytes()
        else:
            return

        value = pickle.loads(blob)  # noqa: S301
        fixture_engine.seed_value(
            scope=self._fdef.scope,
            scope_key=self._scope_key(),
            source=self._fdef.source,
            name=self._fdef.name,
            value=value,
        )


# One adapter per (fixture definition, module scope key); FixtureDef
# identities are stable per process (see _collect_module_fixtures).
_adapters: dict[
    tuple[fixture_engine.FixtureDef, str | None],
    SharedFixtureAdapter,
] = {}


def _walk_shared_defs(
    registry: fixture_engine.Registry,
    roots: list[tuple[str, int]],
) -> set[fixture_engine.FixtureDef]:
    """Transitively reachable session/module-scope fixture defs."""
    reached = fixture_engine.walk_fixture_defs(registry, roots)
    dependencies: dict[
        fixture_engine.FixtureDef, set[fixture_engine.FixtureDef]
    ] = {}
    for fdef, index in reached:
        dependencies[fdef] = set()
        for argname in fdef.argnames:
            if argname == "request":
                continue
            start = index + 1 if argname == fdef.name else 0
            found = registry.lookup(argname, start)
            if found is not None:
                dependencies[fdef].add(found[0])

    # Propagate locality to dependents. Otherwise a local fixture could
    # still execute in the parent while setting up a shared fixture that
    # depends on it. Iterate to a fixed point because a fixture definition
    # may have been reached earlier through a different root.
    local = {fdef for fdef, _index in reached if fdef.local}
    while True:
        dependents = {
            fdef
            for fdef, deps in dependencies.items()
            if fdef not in local and not deps.isdisjoint(local)
        }
        if not dependents:
            break
        local.update(dependents)

    return {
        fdef
        for fdef, _index in reached
        if (
            fdef not in local
            and fdef.scope in {"session", "module"}
            and not fdef.needs_instance
            and not fdef.is_async
            and fdef.params is None
        )
    }


def _module_roots(
    mod: types.ModuleType,
    plan: collect.ModulePlan,
    registry: fixture_engine.Registry,
) -> list[tuple[str, int]]:
    roots: list[tuple[str, int]] = []
    for func_name in plan.function_names:
        func = getattr(mod, func_name, None)
        if callable(func):
            roots.extend(
                (name, 0) for name in fixture_engine.fixture_params(func)
            )
    for _fdef, index in registry.autouse_defs():
        roots.append((_fdef.name, index))
    return roots


def _class_roots(
    class_plan: collect.ClassPlan,
    registry: fixture_engine.Registry,
) -> list[tuple[str, int]]:
    roots: list[tuple[str, int]] = []
    for meth_name in class_plan.method_names:
        func = inspect.getattr_static(class_plan.cls, meth_name, None)
        if func is not None and callable(func):
            roots.extend(
                (name, 0)
                for name in fixture_engine.fixture_params(
                    func, skip_first=True
                )
            )
    for _fdef, index in registry.autouse_defs():
        roots.append((_fdef.name, index))
    return roots


def _attr_name_for(fdef: fixture_engine.FixtureDef) -> str:
    digest = hashlib.blake2s(
        f"{fdef.source}:{fdef.name}:{fdef.scope}".encode(),
        digest_size=4,
    ).hexdigest()
    return f"__ggt_shared_{fdef.name}_{digest}"


def attach_shared_fixtures(
    mod: types.ModuleType,
    plan: collect.ModulePlan,
) -> None:
    """Attach SharedFixtureAdapters to a module's synthesized classes.

    Deterministic and idempotent: workers re-run this during synthesis
    and must produce identically-named attributes so that
    ``import_global_fixture_data()`` can find them.
    """
    if plan.empty or not shared_fixtures_enabled():
        return

    reached: set[fixture_engine.FixtureDef] = set()

    if plan.function_names:
        registry = fixture_engine.registry_for(mod, None)
        reached |= _walk_shared_defs(
            registry, _module_roots(mod, plan, registry)
        )

    for class_plan in plan.classes:
        registry = fixture_engine.registry_for(mod, class_plan.cls)
        reached |= _walk_shared_defs(
            registry, _class_roots(class_plan, registry)
        )

    if not reached:
        return

    synth_classes: list[type[unittest.TestCase]] = [
        obj
        for obj in vars(mod).values()
        if isinstance(obj, type) and getattr(obj, collect.CLASS_MARKER, False)
    ]

    for fdef in sorted(reached, key=lambda d: (d.source, d.name)):
        scope_key = mod.__name__ if fdef.scope == "module" else None
        adapter = _adapters.get((fdef, scope_key))
        if adapter is None:
            adapter = SharedFixtureAdapter(fdef, mod)
            _adapters[fdef, scope_key] = adapter

        attr_name = _attr_name_for(fdef)
        for cls in synth_classes:
            setattr(cls, attr_name, adapter)
