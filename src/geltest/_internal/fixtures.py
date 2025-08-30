from __future__ import annotations
from typing import TYPE_CHECKING, Protocol
from typing_extensions import TypeAliasType, TypedDict
from typing import Required

import asyncio
import functools
import inspect
import json
import os
import sys
import time
import traceback

from . import loader

if TYPE_CHECKING:
    import unittest
    from collections.abc import Iterable, Iterator, Mapping, Sequence


StatsEntry = TypedDict(
    "StatsEntry",
    {"running-time": Required[float], "cached": bool},
    total=False,
)


Stats = TypeAliasType("Stats", list[tuple[str, StatsEntry]])


class _PhaseCallback(Protocol):
    @staticmethod
    async def __call__(
        *,
        test_case: type[loader.DatabaseTestCaseProto],
        stats: Stats,
        options: Mapping[str, str] | None = None,
        ui: loader.UI,
    ) -> None: ...


async def setup_test_cases(
    cases: Sequence[type[unittest.TestCase | loader.DatabaseTestCaseProto]],
    *,
    options: Mapping[str, str] | None = None,
    num_jobs: int = 1,
    ui: loader.UI,
) -> tuple[Stats, dict[str, object]]:
    fixture_data: dict[str, object] = {}
    fixtures: list[loader.Fixture] = []

    ui.info("Setting up global test prerequisites... ")
    for case, attr, fixture in _collect_global_fixtures(cases):
        if options is not None:
            fixture.set_options(options)
        await fixture.set_up(ui)
        data = fixture.get_shared_data()
        if data is not None:
            key = f"{case.__module__}.{case.__qualname__}:{attr}"
            fixture_data[key] = data
            fixtures.append(fixture)

    ui.info("\nSetting up test classes ")
    stats = await _call_session_phase(
        callback=_setup_test_case,
        cases=cases,
        num_jobs=num_jobs,
        options=options,
        ui=ui,
    )

    for fixture in fixtures:
        await fixture.post_session_set_up(cases, ui=ui)

    return stats, fixture_data


async def tear_down_test_cases(
    cases: Sequence[type[unittest.TestCase | loader.DatabaseTestCaseProto]],
    *,
    options: Mapping[str, str] | None = None,
    num_jobs: int = 1,
    ui: loader.UI,
) -> Stats:
    stats = []

    ui.info("Tearing down test classes ")
    try:
        stats.extend(
            await _call_session_phase(
                callback=_tear_down_test_case,
                cases=cases,
                num_jobs=num_jobs,
                options=options,
                ui=ui,
            )
        )
    except Exception:
        err = traceback.format_exc()
        ui.warning(f"exception in test class teardown:\n{err}\n")

    ui.info("Tearing down global test prerequisites... ")
    try:
        for _, _, fixture in _collect_global_fixtures(cases):
            await fixture.tear_down(ui)
    except Exception:
        err = traceback.format_exc()
        ui.warning(f"exception in test fixture teardown:\n{err}\n")

    return stats


@functools.cache
def _find_all_global_fixture_data() -> dict[tuple[str, str, str], str]:
    result: dict[tuple[str, str, str], str] = {}

    for env_var, serialized_data in os.environ.items():
        _, pfx, key = env_var.partition("GEL_TEST_GLOBAL_DATA_")
        if not pfx:
            # Not a global fixture data entry
            continue

        clsfqname, sep, attr = key.partition(":")
        if not sep:
            # Improperly formatted key?
            # XXX: log a warning
            continue

        modname, dot, clsname = clsfqname.rpartition(".")
        if not dot:
            # Improperly formatted key?
            # XXX: log a warning
            continue

        result[modname, clsname, attr] = serialized_data

    return result


def import_global_fixture_data() -> None:
    fixture_data = _find_all_global_fixture_data()
    for (modname, clsname, attr), serialized_data in fixture_data.items():
        if (
            # the module containing the class was imported
            (mod := sys.modules.get(modname)) is not None
            # and the class is actually in that module
            and (cls := getattr(mod, clsname, None)) is not None
            # and the attribute is a fixture
            and isinstance(
                (fixture := inspect.getattr_static(cls, attr, None)),
                loader.Fixture,
            )
        ):
            try:
                data = json.loads(serialized_data)
            except ValueError:
                # XXX: log a warning
                pass
            else:
                fixture.set_shared_data(data)


def import_class_fixture_data(cls: type[unittest.TestCase]) -> None:
    if not issubclass(cls, loader.DatabaseTestCaseProto):
        return
    cls_key = f"{cls.__module__}.{cls.__qualname__}"
    env_key = f"GEL_TEST_CLASS_DATA_{cls_key}"
    data_string = os.environ.get(env_key, "")
    if data_string:
        class_data = json.loads(data_string)
        if not isinstance(class_data, dict):
            raise RuntimeError(f"expected data in {env_key} to be a dict")
        cls.update_shared_data(**class_data)


def export_global_fixture_data(
    global_fixture_data: Mapping[str, object],
) -> None:
    for key, data in global_fixture_data.items():
        os.environ[f"GEL_TEST_GLOBAL_DATA_{key}"] = json.dumps(data)


def export_class_fixture_data(
    class_fixture_data: Mapping[str, Mapping[str, object]],
) -> None:
    for key, data in class_fixture_data.items():
        os.environ[f"GEL_TEST_CLASS_DATA_{key}"] = json.dumps(data)


def _collect_global_fixtures(
    cases: Iterable[type[unittest.TestCase | loader.DatabaseTestCaseProto]],
) -> Iterator[
    tuple[
        type[unittest.TestCase | loader.DatabaseTestCaseProto],
        str,
        loader.Fixture,
    ]
]:
    seen: set[loader.Fixture] = set()
    for case in cases:
        for name in dir(case):
            attr = inspect.getattr_static(case, name, None)
            if isinstance(attr, loader.Fixture) and attr not in seen:
                seen.add(attr)
                origin = next(c for c in case.__mro__ if name in c.__dict__)
                yield origin, name, attr


async def _call_session_phase(
    *,
    callback: _PhaseCallback,
    cases: Iterable[type[unittest.TestCase | loader.DatabaseTestCaseProto]],
    num_jobs: int = 1,
    options: Mapping[str, str] | None = None,
    ui: loader.UI,
) -> Stats:
    eligible = [
        case
        for case in cases
        if issubclass(case, loader.DatabaseTestCaseProto)
    ]

    stats: Stats = []
    if num_jobs == 1:
        # Special case for --jobs=1
        for case in eligible:
            await callback(test_case=case, stats=stats, options=options, ui=ui)
    else:
        async with asyncio.TaskGroup() as g:
            # Use a semaphore to limit the concurrency of bootstrap
            # tasks to the number of jobs (bootstrap is heavy, having
            # more tasks than `--jobs` won't necessarily make
            # things faster.)
            sem = asyncio.BoundedSemaphore(num_jobs)

            async def controller(
                cb: _PhaseCallback,
                test_case: type[loader.DatabaseTestCaseProto],
            ) -> None:
                async with sem:
                    await cb(
                        test_case=test_case,
                        stats=stats,
                        options=options,
                        ui=ui,
                    )

            for case in eligible:
                g.create_task(controller(callback, case))

    ui.text("\n")

    return stats


async def _setup_test_case(
    *,
    test_case: type[loader.DatabaseTestCaseProto],
    stats: Stats,
    options: Mapping[str, str] | None = None,
    ui: loader.UI,
) -> None:
    start_time = time.monotonic()
    if options is not None:
        test_case.set_options(options)
    await test_case.set_up_class_once(ui)
    elapsed = time.monotonic() - start_time
    clsname = f"{test_case.__module__}.{test_case.__qualname__}"
    stats.append(("setup::" + clsname, {"running-time": elapsed}))
    ui.text(f"\n -> {clsname}")


async def _tear_down_test_case(
    *,
    test_case: type[loader.DatabaseTestCaseProto],
    stats: Stats,
    options: Mapping[str, str] | None = None,
    ui: loader.UI,
) -> None:
    start_time = time.monotonic()
    await test_case.tear_down_class_once(ui)
    elapsed = time.monotonic() - start_time
    clsname = f"{test_case.__module__}.{test_case.__qualname__}"
    stats.append(("teardown::" + clsname, {"running-time": elapsed}))
    ui.text(f"\n -> {clsname}")
