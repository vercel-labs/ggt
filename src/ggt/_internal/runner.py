# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

# ruff: noqa: PLW0603

from __future__ import annotations
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    NamedTuple,
    Optional,
    TextIO,
    TypeGuard,
)
from collections.abc import Callable
from typing_extensions import TypeAliasType

import asyncio
import collections
import contextlib
import csv
import gc
import dataclasses
import enum
import faulthandler
import io
import itertools
import multiprocessing
import operator
import os
import random
import re
import shutil
import statistics
import sys
import threading
import time
import types
import unittest.case
import unittest.result
import unittest.signals
import warnings

from . import capture
from . import cov
from . import cpython_state
from . import console
from . import fixtures
from . import loader
from .ndjson import JSONUI, NDJSONEmitter
from . import mproc_fixes
from . import pytest_compat
from . import styles
from . import results
from .pytest_compat import fixtures as pytest_fixtures

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Iterator
    from collections.abc import Callable, Mapping, Sequence
    from contextlib import AbstractContextManager

_ConnArgs = TypeAliasType("_ConnArgs", dict[str, Any])

_WorkerParam = TypeAliasType(
    "_WorkerParam",
    tuple[_ConnArgs | None, str | None, str | None, dict[str, str]],
)

_ResultCall = TypeAliasType(
    "_ResultCall", tuple[str, tuple[Any, ...], dict[str, Any]]
)

result: ParallelTextTestResult | ChannelingTestResult | None = None
coverage_run: Optional[Any] = None
py_hash_secret: bytes = cpython_state.get_py_hash_secret()
py_random_seed: bytes = random.SystemRandom().randbytes(8)

faulthandler.enable(file=sys.stderr, all_threads=True)


@contextlib.contextmanager
def recorded_warnings(
    *,
    enabled: bool = True,
) -> Iterator[list[warnings.WarningMessage]]:
    """Record warnings emitted by a runner phase.

    Warnings emitted during collection and parent-side fixture
    setup/teardown are recorded (when warning capture is enabled) and
    added to the result's warning summary instead of interleaving
    with the progress output.
    """
    if not enabled:
        yield []
        return
    with warnings.catch_warnings(record=True) as ww:
        warnings.resetwarnings()
        warnings.simplefilter("default")
        yield ww


def _lock_file(file: TextIO, *, exclusive: bool) -> bool:
    """Take a non-blocking advisory lock on ``file``; True on success.

    Concurrent ggt runs in the same directory share the running-times
    log: readers take a shared lock, the end-of-run rewrite takes an
    exclusive one and is simply skipped when a competing run holds the
    file.  Acquisition is retried briefly (a writer holds the lock only
    for one in-memory rewrite) but never blocks indefinitely — timing
    data is a performance optimization, not worth wedging a run over.

    On platforms without ``flock`` (Windows) locking degrades to a
    no-op "success".
    """
    try:
        import fcntl  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        return True
    op = (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB
    for _ in range(5):
        try:
            fcntl.flock(file.fileno(), op)
        except OSError:
            time.sleep(0.02)
        else:
            return True
    return False


def _unlock_file(file: TextIO) -> None:
    try:
        import fcntl  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        return
    try:
        fcntl.flock(file.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


class _SessionPhaseHolder:
    """Stands in for a test when attributing session-phase warnings."""

    def __init__(self, description: str) -> None:
        self._description = description

    def id(self) -> str:
        return self._description

    def __str__(self) -> str:
        return self._description


def teardown_suite() -> None:
    # The TestSuite methods are mutating the *result* object,
    # and the suite itself does not hold any state whatsoever,
    # and, in our case specifically, it doesn't even hold
    # references to tests being run, so we can think of
    # its methods as static.
    suite = StreamingTestSuite()
    suite._tearDownPreviousClass(None, result)  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    suite._handleModuleTearDown(result)  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]

    if pytest_compat.is_enabled():
        # Tear down any remaining pytest-style fixtures (session
        # scope in particular).
        pytest_fixtures.teardown_session()


def init_worker(
    status_queue: multiprocessing.SimpleQueue[bool],
    param_queue: multiprocessing.SimpleQueue[_WorkerParam],
    result_queue: multiprocessing.SimpleQueue[_ResultCall],
    additional_init: Callable[[], None] | None,
) -> None:
    global result, coverage_run, py_hash_secret, py_random_seed

    # The fork server (and therefore this worker) may have inherited
    # a disabled collector from the preload warm-up; see
    # ggt._internal.preload.
    gc.enable()

    faulthandler.enable(file=sys.stderr, all_threads=True)

    # The fork server's environment snapshot predates test discovery,
    # so runner state (shared fixture data in particular) travels
    # through the parameter queue instead of the environment.
    _conn_args, _server_ver, _backend_dsn, ggt_env = param_queue.get()
    os.environ.update(ggt_env)

    if additional_init:
        additional_init()

    # Make sure the generator is re-seeded, as we have inherited
    # the seed from the parent process.
    py_random_seed = random.SystemRandom().randbytes(8)
    random.seed(py_random_seed)

    result = ChannelingTestResult(result_queue)

    os.environ["GGT_PARALLEL"] = "1"

    coverage_run = cov.CoverageConfig.start_coverage_if_requested()
    py_hash_secret = cpython_state.get_py_hash_secret()
    status_queue.put(True)  # noqa: FBT003


def shutdown_worker() -> None:
    global coverage_run  # noqa: PLW0602

    teardown_suite()
    if coverage_run is not None:
        coverage_run.stop()
        coverage_run.save()


class StreamingTestSuite(unittest.TestSuite):
    _cleanup = False

    def run(  # type: ignore [override]  # ty: ignore[invalid-method-override]
        self,
        test: unittest.TestCase,
        result: ParallelTextTestResult | ChannelingTestResult,
    ) -> ParallelTextTestResult | ChannelingTestResult:
        with warnings.catch_warnings(record=True) as ww:
            warnings.resetwarnings()
            warnings.simplefilter("default")

            # This is temporary, until we implement `subtransaction`
            # functionality of RFC1004
            warnings.filterwarnings(
                "ignore",
                message=r'The "transaction\(\)" method is deprecated'
                r" and is scheduled to be removed",
                category=DeprecationWarning,
            )

            cap = capture.instance()
            if cap is not None:
                cap.start()
            try:
                result = self._run(test, result)
            finally:
                if cap is not None:
                    out, err = cap.stop()
                    if out or err:
                        result.record_test_output(test, out, err)

            if ww:
                for wmsg in ww:
                    if wmsg.source is not None:
                        wmsg.source = str(wmsg.source)
                    result.addWarning(test, wmsg)

        return result

    def _run(
        self,
        test: unittest.TestCase,
        result: ParallelTextTestResult | ChannelingTestResult,
    ) -> ParallelTextTestResult | ChannelingTestResult:
        result._testRunEntered = True  # type: ignore [union-attr]  # ty: ignore[invalid-assignment]
        self._tearDownPreviousClass(test, result)  # type: ignore [attr-defined]  # ty: ignore[unresolved-attribute]
        self._handleModuleFixture(test, result)  # type: ignore [attr-defined]  # ty: ignore[unresolved-attribute]
        previousClass = getattr(result, "_previousTestClass", None)
        currentClass = test.__class__
        if previousClass != currentClass:
            fixtures.import_global_fixture_data()
            fixtures.import_class_fixture_data(currentClass)
        self._handleClassSetUp(test, result)  # type: ignore [attr-defined]  # ty: ignore[unresolved-attribute]
        result._previousTestClass = currentClass  # type: ignore [union-attr]  # ty: ignore[invalid-assignment]

        if getattr(currentClass, "_classSetupFailed", False) or getattr(
            result, "_moduleSetUpFailed", False
        ):
            return result

        result.annotate_test(
            test,
            {
                "py-hash-secret": py_hash_secret,
                "py-random-seed": py_random_seed,
                "runner-pid": os.getpid(),
            },
        )

        start = time.monotonic()
        test.run(result)
        elapsed = time.monotonic() - start

        result.record_test_stats(test, {"running-time": elapsed})

        result._testRunEntered = False  # type: ignore [union-attr]  # ty: ignore[invalid-assignment]
        return result


def _unroll_suite(suite: unittest.TestSuite) -> Iterator[unittest.TestCase]:
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from _unroll_suite(test)
        else:
            yield test


def _unroll_suites(
    tests: Iterable[unittest.TestSuite | unittest.TestCase],
) -> Iterator[unittest.TestCase]:
    for test in tests:
        if isinstance(test, unittest.TestSuite):
            yield from _unroll_suite(test)
        else:
            yield test


def _run_test(test: unittest.TestCase) -> None:
    suite = StreamingTestSuite()
    assert result is not None
    suite.run(test, result)


def _run_test_group(tests: Sequence[unittest.TestCase]) -> None:
    # A group of tests (typically one test module's worth) dispatched
    # to a single worker so that the module is imported — and its
    # module-scoped fixtures are set up — in one process only.
    for test in tests:
        _run_test(test)


def _is_exc_info(args: Any) -> TypeGuard[results.ExcInfo]:
    return (
        isinstance(args, tuple)
        and len(args) == 3
        and issubclass(args[0], BaseException)
        and (args[2] is None or isinstance(args[2], types.TracebackType))
    )


def _is_assert_failure(args: Any) -> bool:
    if _is_exc_info(args):
        return issubclass(args[0], AssertionError)
    elif isinstance(args, str):
        # HACK: If we serialized the error on the client side... just
        # detect it in the string.
        return "\nAssertionError" in args
    else:
        return False


class _ExpectedFailure(NamedTuple):
    reason: str | None
    not_implemented: bool
    expect_failure: bool
    expect_error: bool


def _is_expecting_failure(test: object) -> _ExpectedFailure | None:
    reason = getattr(test, "__ggt_xfail_reason__", None)
    is_ggt_xfail = hasattr(test, "__ggt_xfail_reason__")
    ggt_not_impl = getattr(test, "__ggt_xfail_not_implemented__", False)
    ggt_xfail = getattr(test, "__ggt_xfail_allow_failure__", False)
    ggt_xerror = getattr(test, "__ggt_xfail_allow_error__", False)

    unittest_xfail = getattr(test, "__unittest_expecting_failure__", False)

    if unittest_xfail or ggt_not_impl or ggt_xfail or ggt_xerror:
        return _ExpectedFailure(
            reason=reason,
            not_implemented=ggt_not_impl,
            expect_failure=ggt_xfail or unittest_xfail,
            expect_error=ggt_xerror or not is_ggt_xfail,
        )
    else:
        return None


@dataclasses.dataclass
class SerializedServerError:
    test_error: str
    server_error: str


class ChannelingTestResultMeta(type):
    @staticmethod
    def get_wrapper(meth: str) -> Callable[..., None]:
        def _wrapper(
            self: ChannelingTestResult,
            *args: Any,
            **kwargs: Any,
        ) -> None:
            try:
                new_args: list[Any] = []
                for arg in args:
                    if isinstance(arg, unittest.TestCase):
                        new_args.append(loader.TestCasePickleWrapper(arg))
                    elif _is_exc_info(arg):
                        test = (
                            args[0]
                            if isinstance(args[0], unittest.TestCase)
                            else None
                        )
                        formatted = results.exc_info_to_string(self, arg, test)
                        new_args.append(formatted)
                    else:
                        new_args.append(arg)
                args = tuple(new_args)
                self._queue.put((meth, args, kwargs))
            except Exception:
                raise

        return _wrapper

    def __new__(
        mcls,
        name: str,
        bases: tuple[type[Any]],
        dct: dict[str, Any],
    ) -> ChannelingTestResultMeta:
        for meth in (
            "startTest",
            "addSuccess",
            "addError",
            "addFailure",
            "addSkip",
            "addExpectedFailure",
            "addUnexpectedSuccess",
            "addSubTest",
            "addWarning",
            "record_test_stats",
            "record_test_output",
            "annotate_test",
        ):
            dct[meth] = mcls.get_wrapper(meth)

        return super().__new__(mcls, name, bases, dct)


class ChannelingTestResult(
    unittest.result.TestResult,
    metaclass=ChannelingTestResultMeta,
):
    def __init__(
        self, queue: multiprocessing.SimpleQueue[_ResultCall]
    ) -> None:
        super().__init__(io.StringIO(), False, 1)  # noqa: FBT003
        self._queue = queue

    def _setupStdout(self) -> None:
        pass

    def _restoreStdout(self) -> None:
        pass

    def printErrors(self) -> None:
        pass

    def printErrorList(self, flavour: Any, errors: Any) -> None:
        pass

    def __getstate__(self) -> dict[str, Any]:
        state = self.__dict__.copy()
        state.pop("_queue")
        state.pop("_original_stdout")
        state.pop("_original_stderr")
        return state

    if TYPE_CHECKING:

        def record_test_stats(
            self, test: unittest.TestCase, stats: fixtures.StatsEntry
        ) -> None: ...

        def record_test_output(
            self, test: unittest.TestCase, out: str, err: str
        ) -> None: ...

        def annotate_test(
            self, test: unittest.TestCase, annotations: dict[str, Any]
        ) -> None: ...

        def addWarning(
            self,
            test: unittest.TestCase,
            wmsg: warnings.WarningMessage,
        ) -> None: ...


def _monitor_thread(
    queue: multiprocessing.Queue[_ResultCall | None],
    result: ParallelTextTestResult,
) -> None:
    while True:
        entry = queue.get()
        if entry is None:
            # This must be the last message in the queue, injected
            # when all tests are completed and the pool is about
            # to be closed.
            break

        methname, args, kwargs = entry
        method = result
        for part in methname.split("."):
            method = getattr(method, part)
        assert callable(method)
        method(*args, **kwargs)  # ty: ignore[call-top-callable]


def status_thread_func(
    result: ParallelTextTestResult,
    stop_event: threading.Event,
) -> None:
    while not stop_event.is_set():
        result.report_still_running()
        stop_event.wait(1)


class ParallelTestSuite(unittest.TestSuite):
    def __init__(
        self,
        tests: Iterable[unittest.TestCase | unittest.TestSuite],
        *,
        server_conn_args: dict[str, Any] | None = None,
        server_ver: str | None = None,
        num_workers: int = 1,
        backend_dsn: str | None = None,
        worker_init: Callable[[], None] | None = None,
        distribute: str = "module",
        test_stats: Mapping[str, tuple[float, int]] | None = None,
    ) -> None:
        self.tests = [*_unroll_suites(tests)]
        self.server_conn_args = server_conn_args
        self.server_ver = server_ver
        self.backend_dsn = backend_dsn
        self.num_workers = num_workers
        self.stop_requested = False
        self.worker_init = worker_init
        self.distribute = distribute
        self.test_stats = test_stats if test_stats is not None else {}

    def _make_task_groups(
        self,
        tests: list[unittest.TestCase],
    ) -> list[list[unittest.TestCase]] | None:
        """Group tests by module for worker dispatch.

        Keeping a module's tests in one worker avoids re-importing
        the module (and re-running its module-scoped fixtures) in
        every worker.  To keep the load balanced, oversized modules
        are split into capped chunks and the resulting tasks are
        dispatched heaviest-first (LPT scheduling), so stragglers are
        small.  Tasks are weighted by historical per-test durations
        from the running-times log when available (unknown tests are
        assumed to cost the median of the known ones); without any
        history every test weighs the same and the weights degrade to
        test counts.  Returns None when grouping would hurt load
        balancing (too few modules relative to the worker count).
        """
        if self.distribute != "module":
            return None

        groups: dict[str, list[unittest.TestCase]] = {}
        for test in tests:
            groups.setdefault(test.__class__.__module__, []).append(test)

        if len(groups) < self.num_workers * 2:
            return None

        stats = self.test_stats
        known = [
            avg
            for name in map(str, tests)
            if (rec := stats.get(name)) and (avg := rec[0]) > 0
        ]
        default = statistics.median(known) if known else 1.0

        def weight(test: unittest.TestCase) -> float:
            rec = stats.get(str(test))
            avg = rec[0] if rec else default
            # Clamp: zero-cost records still occupy scheduling slots,
            # and one outlier record must not dwarf the cap.
            return max(avg, default / 100)

        weights = {id(test): weight(test) for test in tests}

        # Cap task weight so that a single expensive module cannot
        # gate the end of the run while other workers sit idle.
        cap = sum(weights.values()) / (self.num_workers * 4)

        tasks: list[tuple[float, list[unittest.TestCase]]] = []
        for group in groups.values():
            chunk: list[unittest.TestCase] = []
            acc = 0.0
            for test in group:
                w = weights[id(test)]
                if chunk and acc + w > cap:
                    tasks.append((acc, chunk))
                    chunk = []
                    acc = 0.0
                chunk.append(test)
                acc += w
            if chunk:
                tasks.append((acc, chunk))

        # Heaviest tasks first: with dynamic dispatch this
        # approximates LPT scheduling and minimizes the idle tail.
        tasks.sort(key=operator.itemgetter(0), reverse=True)
        return [chunk for _, chunk in tasks]

    def run(self, result: ParallelTextTestResult) -> ParallelTextTestResult:  # type: ignore [override]  # ty: ignore[invalid-method-override]
        # We use SimpleQueues because they are more predictable.
        # They do the necessary IO directly, without using a
        # helper thread.
        result_queue: multiprocessing.SimpleQueue[_ResultCall | None] = (
            multiprocessing.SimpleQueue()
        )
        status_queue: multiprocessing.SimpleQueue[bool] = (
            multiprocessing.SimpleQueue()
        )
        worker_param_queue: multiprocessing.SimpleQueue[_WorkerParam] = (
            multiprocessing.SimpleQueue()
        )

        result_thread = threading.Thread(
            name="test-monitor",
            target=_monitor_thread,
            args=(result_queue, result),
            daemon=True,
        )
        result_thread.start()

        status_thread_stop_event = threading.Event()
        status_thread = threading.Thread(
            name="test-status",
            target=status_thread_func,
            args=(result, status_thread_stop_event),
            daemon=True,
        )
        status_thread.start()

        initargs = (
            status_queue,
            worker_param_queue,
            result_queue,
            self.worker_init,
        )

        pool = multiprocessing.Pool(
            self.num_workers,
            initializer=mproc_fixes.WorkerScope(init_worker, shutdown_worker),
            initargs=initargs,
        )

        # Feed the workers their parameters: server connection
        # information and the runner's environment (the fork server's
        # own environment snapshot predates discovery, so shared
        # fixture data must travel through the queue).  This happens
        # after pool creation because the payload can exceed the pipe
        # buffer: the workers must already be draining the queue.
        ggt_env = {
            key: value
            for key, value in os.environ.items()
            if key.startswith("GGT_")
        }
        for _ in range(self.num_workers):
            worker_param_queue.put(
                (
                    self.server_conn_args,
                    self.server_ver,
                    self.backend_dsn,
                    ggt_env,
                )
            )

        # Wait for all workers to initialize.
        for _ in range(self.num_workers):
            status_queue.get()

        with pool:
            for is_repeat in (False, True):
                if self.stop_requested:
                    break

                phase_tests = [
                    test
                    for test in self.tests
                    if ("test_zREPEAT" in str(test)) == is_repeat
                ]

                groups = self._make_task_groups(phase_tests)
                if groups is not None:
                    group_items = [
                        [loader.TestCasePickleWrapper(test) for test in group]
                        for group in groups
                    ]
                    ar = pool.map_async(
                        _run_test_group,  # type: ignore [arg-type]  # ty: ignore[invalid-argument-type]
                        group_items,
                        chunksize=1,
                    )
                else:
                    items = [
                        loader.TestCasePickleWrapper(test)
                        for test in phase_tests
                    ]
                    ar = pool.map_async(_run_test, items, chunksize=1)  # type: ignore [arg-type]  # ty: ignore[invalid-argument-type]

                while True:
                    try:
                        ar.get(timeout=0.1)
                    except multiprocessing.TimeoutError:
                        # multiprocessing doesn't handle processes
                        # crashing very well, so we check ourselves
                        # (having disabled its own child pruning in
                        # mproc_fixes)
                        #
                        # TODO: Should we look into using
                        # concurrent.futures.ProcessPoolExecutor
                        # instead?
                        for p in pool._pool:  # type: ignore [attr-defined]  # ty: ignore[unresolved-attribute]
                            if p.exitcode:
                                if isinstance(result, ParallelTextTestResult):
                                    result.ren.report_worker_crash(
                                        p.pid,
                                        result.current_pids.get(p.pid),
                                    )
                                sys.stderr.flush()
                                os._exit(1)

                        if self.stop_requested:
                            break
                        else:
                            continue
                    else:
                        break

        # Wait for pool to shutdown, this includes test teardowns.
        pool.join()

        # Post the terminal message to the queue so that
        # test-monitor can stop.
        result_queue.put(None)
        status_thread_stop_event.set()

        # Give the test-monitor and test-status threads some time to process
        # the
        # queue messages.  If something goes wrong, the thread will be forcibly
        # joined by a timeout.
        result_thread.join(timeout=3)
        status_thread.join(timeout=3)

        return result


class SequentialTestSuite(unittest.TestSuite):
    def __init__(
        self,
        tests: Sequence[unittest.TestCase | unittest.TestSuite],
        *,
        worker_init: Callable[[], None] | None,
    ) -> None:
        self.tests = _unroll_suites(tests)
        self.stop_requested = False
        self.worker_init = worker_init

    def run(self, result_: ParallelTextTestResult) -> ParallelTextTestResult:  # type: ignore [override]  # ty: ignore[invalid-method-override]
        global result
        result = result_

        if self.worker_init:
            self.worker_init()

        random.seed(py_random_seed)

        for test in self.tests:
            _run_test(test)
            if self.stop_requested:
                break

        # Make sure the class and the module teardown methods are
        # executed for the trailing test, _run_test() does not do
        # this for us.
        teardown_suite()

        return result


class Markers(enum.Enum):
    passed = "."
    errored = "E"
    skipped = "s"
    failed = "F"
    xfailed = "x"  # expected fail
    not_implemented = "-"
    upassed = "U"  # unexpected success


class OutputFormat(enum.Enum):
    auto = "auto"
    simple = "simple"
    stacked = "stacked"
    verbose = "verbose"
    silent = "silent"
    # Machine-readable NDJSON on stdout.  Never chosen by ``auto``
    # resolution: it must be requested explicitly.
    json = "json"


class BaseRenderer:
    def __init__(
        self,
        *,
        tests: Sequence[unittest.TestCase | unittest.TestSuite],
        stream: TextIO,
    ) -> None:
        self.stream = stream
        self.styles_map: dict[str, styles.Style] = {
            marker.value: getattr(styles, f"marker_{marker.name}")
            for marker in Markers
        }

    def format_test(self, test: unittest.TestCase) -> str:
        if isinstance(test, unittest.case._SubTest):  # type: ignore [attr-defined]  # ty: ignore[unresolved-attribute]
            if hasattr(test, "params") and getattr(test, "params", None):
                params = ", ".join(
                    f"{k}={v!r}"
                    for k, v in getattr(test, "params", {}).items()
                )
            else:
                params = "<subtest>"
            return f"{getattr(test, 'test_case', test)} {{{params}}}"
        else:
            if hasattr(test, "fail_notes") and getattr(
                test, "fail_notes", None
            ):
                fail_notes = ", ".join(
                    f"{k}={v!r}"
                    for k, v in getattr(test, "fail_notes", {}).items()
                )
                return f"{test} {{{fail_notes}}}"
            else:
                return str(test)

    def report(
        self,
        test: unittest.TestCase,
        marker: Markers,
        description: str | None = None,
        *,
        currently_running: Sequence[unittest.TestCase],
        excinfo: str | None = None,
    ) -> None:
        raise NotImplementedError

    def report_start(
        self,
        test: unittest.TestCase,
        *,
        currently_running: Sequence[unittest.TestCase],
    ) -> None:
        return

    def report_still_running(self, still_running: dict[str, float]) -> None:
        return

    def report_worker_crash(
        self, pid: int | None, test: unittest.TestCase | None
    ) -> None:
        return


class SimpleRenderer(BaseRenderer):
    def report(
        self,
        test: unittest.TestCase,
        marker: Markers,
        description: str | None = None,
        *,
        currently_running: Sequence[unittest.TestCase],
        excinfo: str | None = None,
    ) -> None:
        console.echo(
            self.styles_map[marker.value](marker.value),
            nl=False,
            file=self.stream,
        )


class SilentRenderer(BaseRenderer):
    def report(
        self,
        test: unittest.TestCase,
        marker: Markers,
        description: str | None = None,
        *,
        currently_running: Sequence[unittest.TestCase],
        excinfo: str | None = None,
    ) -> None:
        pass


class VerboseRenderer(BaseRenderer):
    fullnames: ClassVar[dict[Markers, str]] = {
        Markers.passed: "OK",
        Markers.errored: "ERROR",
        Markers.skipped: "SKIPPED",
        Markers.failed: "FAILED",
        Markers.xfailed: "expected failure",
        Markers.not_implemented: "not implemented",
        Markers.upassed: "unexpected success",
    }

    def _render_test(
        self, test: unittest.TestCase, marker: Markers, description: str | None
    ) -> str:
        test_title = self.format_test(test)
        if description:
            return f"{test_title}: {self.fullnames[marker]}: {description}"
        else:
            return f"{test_title}: {self.fullnames[marker]}"

    def report(
        self,
        test: unittest.TestCase,
        marker: Markers,
        description: str | None = None,
        *,
        currently_running: Sequence[unittest.TestCase],
        excinfo: str | None = None,
    ) -> None:
        style = self.styles_map[marker.value]
        console.echo(
            style(self._render_test(test, marker, description)),
            file=self.stream,
        )

    def report_still_running(self, still_running: dict[str, float]) -> None:
        items = [f"{t} for {d:.02f}s" for t, d in still_running.items()]
        newline_join = "\n   "
        console.echo(
            f"still running:\n  {newline_join.join(items)}",
            file=self.stream,
        )


class MultiLineRenderer(BaseRenderer):
    FT_LABEL = "First few failed: "
    FT_MAX_LINES = 6

    R_LABEL = "Running: "
    R_MAX_LINES = 6

    def __init__(
        self,
        *,
        tests: Sequence[unittest.TestSuite | unittest.TestCase],
        stream: TextIO,
    ) -> None:
        super().__init__(tests=tests, stream=stream)

        self.total_tests = len(tests)
        self.completed_tests = 0

        test_modules = {test.__class__.__module__ for test in tests}
        max_test_module_len = max(
            (len(self._render_modname(name)) for name in test_modules),
            default=0,
        )
        self.first_col_width = max_test_module_len + 1  # 1 == len(' ')

        self.failed_tests: set[str] = set()

        self.buffer: collections.defaultdict[str, str] = (
            collections.defaultdict(str)
        )
        self.last_lines = -1
        self.max_label_lines_rendered: collections.defaultdict[str, int] = (
            collections.defaultdict(int)
        )

    def report(
        self,
        test: unittest.TestCase,
        marker: Markers,
        description: str | None = None,
        *,
        currently_running: Sequence[unittest.TestCase],
        excinfo: str | None = None,
    ) -> None:
        if marker in {Markers.failed, Markers.errored}:
            test_name = test.id().rpartition(".")[2]
            if " " in test_name:
                test_name = test_name.split(" ")[0]
            self.failed_tests.add(test_name)

        self.buffer[test.__class__.__module__] += marker.value
        self.completed_tests += 1
        self._render(currently_running)

    def report_start(
        self,
        test: unittest.TestCase,
        *,
        currently_running: Sequence[unittest.TestCase],
    ) -> None:
        self._render(currently_running)

    def report_still_running(self, still_running: dict[str, float]) -> None:
        # Still-running tests are already reported in normal repert
        return

    def _render_modname(self, name: str) -> str:
        return name.replace(".", "/") + ".py"

    def _color_second_column(
        self, line: str, style: Callable[[str], str]
    ) -> str:
        return line[: self.first_col_width] + style(
            line[self.first_col_width :]
        )

    def _render(self, currently_running: Sequence[unittest.TestCase]) -> None:
        def print_line(line: str) -> None:
            if len(line) < cols:
                line += " " * (cols - len(line))
            lines.append(line)

        def print_empty_line() -> None:
            print_line(" ")

        last_render = self.completed_tests == self.total_tests
        cols, rows = shutil.get_terminal_size()
        second_col_width = cols - self.first_col_width

        def _render_test_list(
            label: str,
            max_lines: int,
            tests: Collection[str],
            style: styles.Style,
        ) -> None:
            if (
                len(label) > self.first_col_width
                or cols - self.first_col_width <= 40
            ):
                return

            print_empty_line()

            line = f"{label}{' ' * (self.first_col_width - len(label))}"
            tests_lines = 1
            for testi, test in enumerate(tests, 1):
                last = testi == len(tests)
                test_str = test
                if not last:
                    test_str += ", "

                test_name_len = len(test_str)

                if len(line) + test_name_len < cols:
                    line += test_str

                else:
                    if tests_lines == max_lines:
                        if len(line) + 3 < cols:
                            line += "..."
                        break

                    else:
                        line += (cols - len(line)) * " "
                        line = self._color_second_column(line, style)
                        lines.append(line)

                        tests_lines += 1
                        line = self.first_col_width * " "

                        if len(line) + test_name_len > cols:
                            continue

                        line += test

            line += (cols - len(line)) * " "
            line = self._color_second_column(line, style)
            lines.append(line)

            # Prevent the rendered output from "jumping" up/down when we
            # render 2 lines worth of running tests just after we rendered
            # 3 lines.
            lkey = label.split(":", maxsplit=1)[0]
            # ^- We can't just use `label`, as we append extra information
            # to the "Running: (..)" label, so strip that
            for _ in range(self.max_label_lines_rendered[lkey] - tests_lines):
                lines.append(" " * cols)
            self.max_label_lines_rendered[lkey] = max(
                self.max_label_lines_rendered[lkey], tests_lines
            )

        clear_cmd = ""
        if self.last_lines > 0:
            # Move cursor up `last_lines` times.
            clear_cmd = f"\r\033[{self.last_lines}A"

        lines = []
        for mod, progress in self.buffer.items():
            line = self._render_modname(mod).ljust(self.first_col_width, " ")
            progress_str = progress
            while progress_str:
                second_col = progress_str[:second_col_width]
                second_col = second_col.ljust(second_col_width, " ")

                progress_str = progress_str[second_col_width:]

                # Apply styles *after* slicing and padding the string
                # (otherwise ANSI codes could be sliced in half).
                def style_char(match: re.Match[str]) -> str:
                    char = match.group(0)
                    return self.styles_map[char](char)

                second_col = re.sub(r"\S", style_char, second_col)

                lines.append(f"{line}{second_col}")

                if line[0] != " ":
                    line = " " * self.first_col_width

        if not last_render:
            if self.failed_tests:
                _render_test_list(
                    self.FT_LABEL,
                    self.FT_MAX_LINES,
                    self.failed_tests,
                    styles.marker_errored,
                )

            running_tests = []
            for test in currently_running:
                test_name = test.id().rpartition(".")[2]
                if " " in test_name:
                    test_name = test_name.split(" ")[0]
                running_tests.append(test_name)

            if not running_tests:
                running_tests.append("...")

            _render_test_list(
                self.R_LABEL + f"({len(currently_running)})",
                self.R_MAX_LINES,
                running_tests,
                styles.marker_passed,
            )

        print_empty_line()
        print_line(
            f"Progress: {self.completed_tests}/{self.total_tests} tests."
        )

        if not last_render:
            # If it's not the last test, check if our render buffer
            # requires more rows than currently visible.
            if len(lines) + 1 > rows:
                # Scroll the render buffer to the bottom and
                # cut the lines from the beginning, so that it
                # will fit the screen.
                #
                # We need to do this because we can't move the
                # cursor past the visible screen area, so if we
                # render more data than the screen can fit, we
                # will have lot's of garbage output.
                lines = lines[len(lines) + 1 - rows :]
                lines[0] = "^" * cols

        # When this frame is shorter than the previous one (the final
        # render drops the failed/running lists), erase the leftover
        # lines below instead of padding the frame, which would bake a
        # permanent blank block into the scrollback.
        erase_below = "\033[0J" if len(lines) < self.last_lines else ""

        # Hide cursor.
        print("\033[?25l", end="", flush=True, file=self.stream)
        try:
            # Use `print` because we want to
            # precisely control when the output is flushed.
            print(
                clear_cmd + "\n".join(lines) + erase_below,
                flush=False,
                file=self.stream,
            )
        finally:
            # Show cursor.
            print("\033[?25h", end="", flush=True, file=self.stream)

        self.last_lines = len(lines)


class JSONRenderer(BaseRenderer):
    """Report per-test events as NDJSON entries (json output format).

    Test outcomes that constitute a run failure are emitted at ERROR
    level so consumers surface them immediately; everything else stays
    at INFO and only advances the progress counters.
    """

    marker_levels: ClassVar[dict[Markers, str]] = {
        Markers.passed: "INFO",
        Markers.errored: "ERROR",
        Markers.skipped: "INFO",
        Markers.failed: "ERROR",
        Markers.xfailed: "INFO",
        Markers.not_implemented: "INFO",
        Markers.upassed: "WARNING",
    }

    marker_labels: ClassVar[dict[Markers, str]] = {
        Markers.passed: "PASSED",
        Markers.errored: "ERROR",
        Markers.skipped: "SKIPPED",
        Markers.failed: "FAILED",
        Markers.xfailed: "XFAIL",
        Markers.not_implemented: "NOT IMPLEMENTED",
        Markers.upassed: "UNEXPECTED SUCCESS",
    }

    def __init__(
        self,
        *,
        tests: Sequence[unittest.TestSuite | unittest.TestCase],
        stream: TextIO,
        ndjson: NDJSONEmitter,
    ) -> None:
        super().__init__(tests=tests, stream=stream)
        self.ndjson = ndjson
        self.total_tests = len(tests)
        self.completed_tests = 0

    def report(
        self,
        test: unittest.TestCase,
        marker: Markers,
        description: str | None = None,
        *,
        currently_running: Sequence[unittest.TestCase],
        excinfo: str | None = None,
    ) -> None:
        # Subtest failures report without a corresponding startTest, so
        # clamp instead of letting the counter run past the total (the
        # stacked renderer tolerates the same skew).
        self.completed_tests = min(self.completed_tests + 1, self.total_tests)
        test_id = test.id()
        message = f"{self.marker_labels[marker]} {test_id}"
        if excinfo is not None and (concise := self._concise_error(excinfo)):
            message = f"{message}: {concise}"
        elif description:
            message = f"{message}: {description}"
        extra: dict[str, Any] = {
            "ggt.test": test_id,
            "ggt.marker": marker.name,
        }
        if excinfo is not None:
            extra["ggt.traceback"] = excinfo
        if description:
            extra["ggt.reason"] = description
        self.ndjson.event(
            message,
            level=self.marker_levels[marker],
            description=test_id,
            completed=self.completed_tests,
            total=self.total_tests,
            extra=extra,
        )

    def report_still_running(self, still_running: dict[str, float]) -> None:
        items = ", ".join(
            f"{test} ({duration:.1f}s)"
            for test, duration in still_running.items()
        )
        self.ndjson.event(f"still running: {items}", status_only=True)

    def report_worker_crash(
        self, pid: int | None, test: unittest.TestCase | None
    ) -> None:
        detail = f" while running {test.id()}" if test is not None else ""
        self.ndjson.event(
            f"worker process crashed (pid={pid}){detail}",
            level="ERROR",
            extra={"ggt.worker_pid": pid}
            | ({"ggt.test": test.id()} if test is not None else {}),
        )

    @staticmethod
    def _concise_error(excinfo: str) -> str | None:
        lines = [line for line in excinfo.strip().splitlines() if line.strip()]
        return lines[-1].strip() if lines else None


class ParallelTextTestResult(unittest.result.TestResult):
    def __init__(
        self,
        *,
        stream: TextIO,
        verbosity: int,
        catch_warnings: bool,
        tests: Sequence[unittest.TestCase],
        output_format: OutputFormat = OutputFormat.auto,
        failfast: bool = False,
        suite: ParallelTestSuite | SequentialTestSuite,
        ndjson: NDJSONEmitter | None = None,
    ) -> None:
        super().__init__(stream, descriptions=False, verbosity=verbosity)
        self.verbosity = verbosity
        self.catch_warnings = catch_warnings
        self.failfast = failfast
        self.test_stats: list[
            tuple[unittest.TestCase, fixtures.StatsEntry]
        ] = []
        self.test_annotations: collections.defaultdict[
            unittest.TestCase, dict[str, Any]
        ] = collections.defaultdict(dict)
        self.errors: list[tuple[unittest.TestCase, results.OptExcInfo]] = []  # type: ignore [assignment]
        self.warnings: list[tuple[unittest.TestCase, str]] = []
        self.test_output: dict[str, tuple[str, str]] = {}
        self.notImplemented: list[
            tuple[unittest.TestCase, results.OptExcInfo]
        ] = []
        self.currently_running: dict[unittest.TestCase, float] = {}
        self.current_pids: dict[int, unittest.TestCase] = {}
        # An index of all seen warnings to keep track
        # of repeated warnings.
        self._warnings: dict[
            tuple[str, str, int], warnings.WarningMessage
        ] = {}
        self.suite = suite
        self.ren: BaseRenderer

        if output_format is OutputFormat.json:
            assert ndjson is not None
            self.ren = JSONRenderer(tests=tests, stream=stream, ndjson=ndjson)
        elif output_format is OutputFormat.verbose or (
            output_format is OutputFormat.auto and self.verbosity > 1
        ):
            self.ren = VerboseRenderer(tests=tests, stream=stream)
        elif output_format is OutputFormat.stacked or (
            output_format is OutputFormat.auto
            and stream.isatty()
            and shutil.get_terminal_size()[0] > 60
            and os.name != "nt"
        ):
            self.ren = MultiLineRenderer(tests=tests, stream=stream)
        elif output_format is OutputFormat.silent:
            self.ren = SilentRenderer(tests=tests, stream=stream)
        else:
            self.ren = SimpleRenderer(tests=tests, stream=stream)

    def report_progress(
        self,
        test: unittest.TestCase,
        marker: Markers,
        description: str | None = None,
        *,
        err: Any = None,
    ) -> None:
        self.currently_running.pop(test, None)
        self.ren.report(
            test,
            marker,
            description,
            currently_running=list(self.currently_running),
            excinfo=self._format_excinfo(test, err),
        )

    def _format_excinfo(self, test: unittest.TestCase, err: Any) -> str | None:
        """Normalize a test error to its formatted-traceback string.

        Parallel workers serialize errors to strings before channeling
        them; sequential runs deliver live ``exc_info`` tuples, and
        server errors arrive as :class:`SerializedServerError`.
        """
        if err is None:
            return None
        if _is_exc_info(err):
            return results.exc_info_to_string(self, err, test)
        if isinstance(err, SerializedServerError):
            return err.test_error
        if isinstance(err, str):
            return err
        return None

    def report_still_running(self) -> None:
        now = time.monotonic()
        still_running = {}
        for test, start in self.currently_running.items():
            running_for = now - start
            if running_for > 5.0:
                key = str(test)
                if test in self.test_annotations and (
                    pid := self.test_annotations[test].get("runner-pid")
                ):
                    key = f"{key} (pid={pid})"

                still_running[key] = running_for
        if still_running:
            self.ren.report_still_running(still_running)

    def record_test_stats(
        self, test: unittest.TestCase, stats: fixtures.StatsEntry
    ) -> None:
        self.test_stats.append((test, stats))

    def record_test_output(
        self, test: unittest.TestCase, out: str, err: str
    ) -> None:
        self.test_output[test.id()] = (out, err)

    def get_test_output(
        self, test: unittest.TestCase
    ) -> tuple[str, str] | None:
        return self.test_output.get(test.id())

    def annotate_test(
        self, test: unittest.TestCase, annotations: dict[str, Any]
    ) -> None:
        self.test_annotations[test].update(annotations)

    def get_test_annotations(
        self, test: unittest.TestCase
    ) -> dict[str, Any] | None:
        return self.test_annotations.get(test)

    def _exc_info_to_string(
        self, err: results.OptExcInfo, test: unittest.TestCase
    ) -> results.OptExcInfo:
        # Errors are serialized in the worker.
        return err

    def getDescription(self, test: unittest.TestCase) -> str:
        return self.ren.format_test(test)

    def startTest(self, test: unittest.TestCase) -> None:
        super().startTest(test)
        self.currently_running[test] = time.monotonic()
        self.ren.report_start(
            test, currently_running=list(self.currently_running)
        )
        if test in self.test_annotations and (
            pid := self.test_annotations[test].get("runner-pid")
        ):
            self.current_pids[pid] = test

    def addSuccess(self, test: unittest.TestCase) -> None:
        super().addSuccess(test)
        self.report_progress(test, Markers.passed)

    def addError(
        self, test: unittest.TestCase, err: results.OptExcInfo
    ) -> None:
        super().addError(test, err)
        self.report_progress(test, Markers.errored, err=err)
        if self.failfast:
            self.suite.stop_requested = True

    def addFailure(
        self, test: unittest.TestCase, err: results.OptExcInfo
    ) -> None:
        super().addFailure(test, err)
        self.report_progress(test, Markers.failed, err=err)
        if self.failfast:
            self.suite.stop_requested = True

    def addSubTest(
        self,
        test: unittest.TestCase,
        subtest: unittest.TestCase,
        err: results.OptExcInfo | None,
    ) -> None:
        if err is not None:
            self.errors.append((subtest, self._exc_info_to_string(err, test)))  # ty: ignore[invalid-argument-type]
            self._mirrorOutput = True

            self.ren.report(
                subtest,
                Markers.errored,
                currently_running=list(self.currently_running),
                excinfo=self._format_excinfo(subtest, err),
            )
            if self.failfast:
                self.suite.stop_requested = True

    def addSkip(self, test: unittest.TestCase, reason: str) -> None:
        super().addSkip(test, reason)
        self.report_progress(test, Markers.skipped)

    def addExpectedFailure(
        self,
        test: unittest.TestCase,
        err: results.OptExcInfo,
    ) -> None:
        method = getattr(test, test._testMethodName)
        xfail = _is_expecting_failure(method) or _is_expecting_failure(test)
        if xfail is None:
            # This should not happen, at the very least
            # __unittest_expecting_failure__ should have been set
            # report it as error
            super().addError(test, err)
            marker = Markers.errored
            reason = None
        elif xfail.not_implemented:
            marker = Markers.not_implemented
            reason = xfail.reason
            self.notImplemented.append(
                (test, self._exc_info_to_string(err, test))
            )
        else:
            reason = xfail.reason
            is_fail = _is_assert_failure(err)
            if (xfail.expect_failure and is_fail) or (
                xfail.expect_error and not is_fail
            ):
                marker = Markers.xfailed
                super().addExpectedFailure(test, err)
            elif is_fail:
                marker = Markers.failed
                super().addFailure(test, err)
            else:
                marker = Markers.errored
                super().addError(test, err)

        self.report_progress(
            test,
            marker,
            reason,
            err=err if marker in {Markers.failed, Markers.errored} else None,
        )

    def addUnexpectedSuccess(self, test: unittest.TestCase) -> None:
        method = getattr(test, test._testMethodName, None)
        if getattr(method, "__ggt_xfail_strict__", False):
            # pytest.mark.xfail(strict=True): an unexpected pass is a
            # hard failure.
            reason = getattr(method, "__ggt_xfail_reason__", None) or ""
            self.addFailure(
                test,
                f"AssertionError: [XPASS(strict)] {reason}".rstrip(),  # type: ignore [arg-type]  # ty: ignore[invalid-argument-type]
            )
            return
        super().addUnexpectedSuccess(test)
        self.report_progress(test, Markers.upassed)

    def addWarning(
        self,
        test: unittest.TestCase,
        wmsg: warnings.WarningMessage,
    ) -> None:
        if not self.catch_warnings:
            return

        key = str(wmsg.message), wmsg.filename, wmsg.lineno

        if key not in self._warnings:
            self._warnings[key] = wmsg
            self.warnings.append(
                (
                    test,
                    warnings.formatwarning(
                        wmsg.message,
                        wmsg.category,
                        wmsg.filename,
                        wmsg.lineno,
                        wmsg.line,
                    ),
                )
            )

    def wasSuccessful(self) -> bool:
        # Overload TestResult.wasSuccessful to ignore unexpected successes
        return len(self.failures) == len(self.errors) == 0


class ParallelTextTestRunner:
    def __init__(
        self,
        *,
        stream: TextIO | None = None,
        num_workers: int = 1,
        verbosity: int = 1,
        output_format: OutputFormat = OutputFormat.auto,
        warnings: bool = True,
        failfast: bool = False,
        shuffle: bool = False,
        options: Mapping[str, str] | None = None,
        distribute: str = "module",
        capture_output: bool = True,
        ndjson: NDJSONEmitter | None = None,
    ) -> None:
        self.stream = stream if stream is not None else sys.stderr
        self.distribute = distribute
        self.num_workers = num_workers
        self.verbosity = verbosity
        self.warnings = warnings
        self.capture_output = capture_output
        self.failfast = failfast
        self.shuffle = shuffle
        self.output_format = output_format
        self.ndjson = ndjson
        self.ui: loader.UI
        if ndjson is not None:
            self.ui = JSONUI(ndjson)
        else:
            self.ui = styles.ConsoleUI(verbosity=verbosity, stream=self.stream)
        self.options = {**options} if options is not None else {}

    def run(
        self,
        test: Any,
        selected_shard: int,
        total_shards: int,
        running_times_log_file: TextIO | None,
        collection_warnings: (Sequence[warnings.WarningMessage] | None) = None,
    ) -> results.TestResult:
        session_start = time.monotonic()
        cases = loader.get_test_cases([test])
        stats = {}
        if running_times_log_file and _lock_file(
            running_times_log_file, exclusive=False
        ):
            try:
                running_times_log_file.seek(0)
                for row in csv.reader(running_times_log_file):
                    try:
                        rec_name, rec_avg, rec_count = row
                        stats[rec_name] = (float(rec_avg), int(rec_count))
                    except ValueError:
                        # A malformed row (e.g. left over from a
                        # pre-locking torn rewrite) costs one timing
                        # entry, not the run.
                        continue
            finally:
                _unlock_file(running_times_log_file)
        cases = loader.get_cases_by_shard(
            cases,
            selected_shard,
            total_shards,
            self.verbosity,
            stats,
        )
        worker_init = (
            pytest_compat.worker_init if pytest_compat.is_enabled() else None
        )
        setup_time_taken = 0.0
        tests_time_taken = 0.0
        result: ParallelTextTestResult | None = None
        setup_stats: fixtures.Stats = []
        teardown_stats = []
        dup_stream: TextIO | None = None

        try:
            with (
                self._ndjson_stage("setup"),
                self._recorded_warnings() as setup_warnings,
            ):
                setup_stats = asyncio.run(
                    fixtures.setup_test_cases(
                        [*cases],
                        num_jobs=self.num_workers,
                        ui=self.ui,
                        options=self.options,
                    )
                )
            setup_time_taken = time.monotonic() - session_start

            os.environ["GGT_TEST_SETUP_RESPONSIBLE"] = "runner"
            # Workers receive this through the parameter queue's
            # environment snapshot; the sequential suite reads it
            # in-process.
            os.environ[capture.ENV_VAR] = "1" if self.capture_output else "0"

            start = time.monotonic()

            all_tests = list(
                itertools.chain.from_iterable(
                    tests for tests in cases.values()
                )
            )

            suite: ParallelTestSuite | SequentialTestSuite

            if self.num_workers > 1:
                suite = ParallelTestSuite(
                    self._sort_tests(cases),
                    num_workers=self.num_workers,
                    worker_init=worker_init,
                    distribute=self.distribute,
                    test_stats=stats,
                )
            else:
                suite = SequentialTestSuite(
                    self._sort_tests(cases),
                    worker_init=worker_init,
                )

            result_stream = self.stream
            if self.capture_output and self.num_workers <= 1:
                # The sequential suite captures fds 1/2 in this very
                # process while each test runs; the progress renderer
                # must keep writing to the real terminal, so give it a
                # duplicate of the stream's descriptor made before any
                # redirection.
                try:
                    stream_fd = self.stream.fileno()
                except (AttributeError, OSError, io.UnsupportedOperation):
                    pass
                else:
                    dup_stream = os.fdopen(
                        os.dup(stream_fd),
                        "w",
                        buffering=1,
                        encoding=getattr(self.stream, "encoding", None)
                        or "utf-8",
                        errors="replace",
                    )
                    result_stream = dup_stream

            result = ParallelTextTestResult(
                stream=result_stream,
                verbosity=self.verbosity,
                catch_warnings=self.warnings,
                failfast=self.failfast,
                output_format=self.output_format,
                tests=all_tests,
                suite=suite,
                ndjson=self.ndjson,
            )
            unittest.signals.registerResult(result)

            collection_holder = _SessionPhaseHolder("test collection")
            for wmsg in collection_warnings or ():
                result.addWarning(collection_holder, wmsg)  # type: ignore [arg-type]  # ty: ignore[invalid-argument-type]

            setup_holder = _SessionPhaseHolder("test session setup")
            for wmsg in setup_warnings:
                result.addWarning(setup_holder, wmsg)  # type: ignore [arg-type]  # ty: ignore[invalid-argument-type]

            if self.ndjson is None:
                self.ui.info("\nRunning tests\n\n")
            with self._ndjson_stage(
                "run",
                total=len(all_tests),
                message=f"running {len(all_tests)} tests",
                extra={"ggt.shard": f"{selected_shard}/{total_shards}"},
            ):
                suite.run(result)
            if self.ndjson is None and not isinstance(
                result.ren, MultiLineRenderer
            ):
                # Terminate the progress output (e.g. the simple
                # renderer's dot line); the stacked renderer's frame
                # is already newline-terminated.
                self.ui.info("\n")

            with (
                self._ndjson_stage("teardown"),
                self._recorded_warnings() as teardown_warnings,
            ):
                teardown_stats = asyncio.run(
                    fixtures.tear_down_test_cases(
                        [*cases],
                        num_jobs=self.num_workers,
                        ui=self.ui,
                    )
                )
            teardown_holder = _SessionPhaseHolder("test session teardown")
            for wmsg in teardown_warnings:
                result.addWarning(teardown_holder, wmsg)  # type: ignore [arg-type]  # ty: ignore[invalid-argument-type]

            if running_times_log_file and _lock_file(
                running_times_log_file, exclusive=True
            ):
                # When a competing run holds the lock, the rewrite is
                # skipped: that run's timings win and this run's are
                # dropped, which is cheaper than corrupting the file
                # and only dampens the running averages.
                try:
                    for test_obj, stat in (
                        result.test_stats + setup_stats + teardown_stats
                    ):
                        name = str(test_obj)
                        t = stat.get("running-time", 0)
                        at, c = stats.get(name, (0, 0))
                        stats[name] = (at + (t - at) / (c + 1), c + 1)
                    running_times_log_file.seek(0)
                    running_times_log_file.truncate()
                    writer = csv.writer(running_times_log_file)
                    for k, v in stats.items():
                        writer.writerow((k, *v))
                finally:
                    _unlock_file(running_times_log_file)

            tests_time_taken = time.monotonic() - start

        except KeyboardInterrupt:
            raise

        finally:
            # Release capture resources (the sequential suite creates
            # them in this process) before interpreter shutdown would
            # report them as unclosed files.
            capture.close()
            if dup_stream is not None:
                with contextlib.suppress(Exception):
                    dup_stream.close()
            if (
                self.ndjson is None
                and self.verbosity == 1
                and not (
                    result is not None
                    and isinstance(result.ren, MultiLineRenderer)
                )
            ):
                self._echo()

        return results.collect_result_data(
            result, setup_time_taken, tests_time_taken
        )

    def _echo(self, s: str = "", **kwargs: Any) -> None:
        if self.verbosity > 0:
            console.secho(s, file=self.stream, **kwargs)

    def _ndjson_stage(
        self,
        name: str,
        *,
        total: int | None = None,
        message: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> AbstractContextManager[None]:
        if self.ndjson is None:
            return contextlib.nullcontext()
        return self.ndjson.stage(
            name, total=total, message=message, extra=extra
        )

    def _recorded_warnings(
        self,
    ) -> AbstractContextManager[list[warnings.WarningMessage]]:
        return recorded_warnings(enabled=self.warnings)

    def _sort_tests(
        self,
        cases: Mapping[type[unittest.TestCase], Sequence[unittest.TestCase]],
    ) -> list[unittest.TestCase | unittest.TestSuite]:
        serialized_suites: dict[
            type[unittest.TestCase], unittest.TestSuite
        ] = {}
        exclusive_suites: set[type[unittest.TestCase]] = set()
        exclusive_tests: list[unittest.TestCase] = []

        for casecls, tests in cases.items():
            gg = getattr(casecls, "get_parallelism_granularity", None)
            granularity = gg() if gg is not None else "default"

            if granularity == "suite":
                serialized_suites[casecls] = unittest.TestSuite(tests)
            elif granularity == "system":
                exclusive_tests.extend(tests)
                exclusive_suites.add(casecls)

        test_list: list[unittest.TestCase | unittest.TestSuite] = list(
            itertools.chain(
                serialized_suites.values(),
                itertools.chain.from_iterable(
                    tests
                    for casecls, tests in cases.items()
                    if (
                        casecls not in serialized_suites
                        and casecls not in exclusive_suites
                    )
                ),
                [unittest.TestSuite(exclusive_tests)],
            )
        )

        if self.shuffle:
            random.shuffle(test_list)

        return test_list
