# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.


from __future__ import annotations

import argparse
import contextlib
import dataclasses
import importlib.util
import multiprocessing
import os
import pathlib
import shutil
import sys
import tempfile
from typing import TYPE_CHECKING, Any, TextIO

if TYPE_CHECKING:
    coverage: Any | None
else:
    try:
        import coverage
    except ImportError:
        coverage = None

from . import (
    console,
    cov,
    loader,
    marks,
    mproc_fixes,
    pytest_compat,
    results,
    runner,
    styles,
)
from .ndjson import NDJSONEmitter
from . import preload as preload_mod
from .decorators import (
    _xfail,
    async_timeout,
    not_implemented,
    skip,
    xerror,
    xfail,
)
from .marks import mark

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence


__all__ = (
    "_xfail",
    "async_timeout",
    "mark",
    "not_implemented",
    "skip",
    "xerror",
    "xfail",
)


_COVERAGE_EXTRA = "ggt[coverage]"


class _KeyValueAction(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        item = values
        if not isinstance(item, str):
            raise argparse.ArgumentError(self, "expected key=value")
        if "=" not in item:
            raise argparse.ArgumentError(
                self, f"Expected format key=value, got: {item}"
            )

        result = getattr(namespace, self.dest, None)
        if result is None:
            result = {}
            setattr(namespace, self.dest, result)
        key, val = item.split("=", 1)
        result[key] = val


def _open_running_times_log(path: str | None) -> TextIO | None:
    if path is None:
        # Default to a cache file: historical timings feed shard
        # balancing and the parallel scheduler's task weighting.
        cache_dir = preload_mod.ensure_cache_dir()
        if cache_dir is None:
            return None
        try:
            return open(
                cache_dir / "running_times.csv",
                "a+",
                encoding="utf-8",
                newline="",
            )
        except OSError:
            return None
    return open(path, "a+", encoding="utf-8", newline="")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ggt",
        description=(
            "Run a test suite. Discovers and runs tests in the specified "
            "files or directories. If no files or directories are specified, "
            "current directory is assumed."
        ),
    )
    parser.add_argument("files", nargs="*", metavar="[file or directory]")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="increase verbosity"
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true", help="decrease verbosity"
    )
    parser.add_argument(
        "--debug", action="store_true", help="output internal debug logs"
    )
    parser.add_argument(
        "--output-format",
        choices=[fmt.value for fmt in runner.OutputFormat],
        default=runner.OutputFormat.auto.value,
        help="test progress output style",
    )
    parser.add_argument(
        "--warnings",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="enable warning capture and reporting (default: enabled)",
    )
    parser.add_argument(
        "--capture",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "capture test stdout/stderr; captured output of failing "
            "tests is shown in their failure report (default: enabled; "
            "use --no-capture to let test output pass through to the "
            "terminal)"
        ),
    )
    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=0,
        help=(
            "number of parallel processes to use, default is 0, which "
            "means choose automatically based on the number of "
            "available CPU cores"
        ),
    )
    parser.add_argument(
        "-s",
        "--shard",
        type=str,
        default="1/1",
        help="run tests in shards (current/total)",
    )
    parser.add_argument(
        "-k",
        "--include",
        type=str,
        action="append",
        default=[],
        metavar="REGEXP",
        help="only run tests which match the given regular expression",
    )
    parser.add_argument(
        "-e",
        "--exclude",
        type=str,
        action="append",
        default=[],
        metavar="REGEXP",
        help="do not run tests which match the given regular expression",
    )
    parser.add_argument(
        "-m",
        "--mark",
        dest="mark_expr",
        type=str,
        default=None,
        metavar="MARKEXPR",
        help=(
            "only run tests matching the given mark expression, "
            "e.g. -m 'slow and not integration'; marks are attached "
            "with the ggt.mark decorator, and non-identifier terms "
            "are treated as regular expressions over mark names"
        ),
    )
    parser.add_argument(
        "-x",
        "--failfast",
        action="store_true",
        help="stop tests after a first failure/error",
    )
    parser.add_argument(
        "--shuffle",
        action="store_true",
        help="shuffle the order in which tests are run",
    )
    parser.add_argument(
        "--preload",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "warm up the worker fork server by preloading the test "
            "suite's dependency graph (cached from the previous run) "
            "in parallel with test discovery (default: enabled; use "
            "--no-preload if a dependency is not fork-safe at import "
            "time)"
        ),
    )
    parser.add_argument(
        "--distribute",
        choices=["module", "test"],
        default="module",
        help=(
            'parallel work distribution granularity: "module" keeps '
            "each test module's tests in a single worker (avoiding "
            'repeated imports and module fixture setup); "test" '
            "distributes each test individually (default: module, "
            "with an automatic fallback to per-test distribution when "
            "there are too few modules to balance the workers)"
        ),
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="repeat tests N times or until first unsuccessful run",
    )
    cov_help = (
        "package name to measure code coverage for, "
        "can be specified multiple times "
        "(e.g --cov myproject --cov myproject.submodule)"
    )
    if coverage is None:
        cov_help += (
            f" (WARNING: coverage support is not enabled; use `uv add --dev "
            f"{_COVERAGE_EXTRA}` or `python -m pip install "
            f"'{_COVERAGE_EXTRA}'`)"
        )

    parser.add_argument(
        "--cov",
        type=str,
        action="append",
        default=[],
        help=cov_help,
    )
    parser.add_argument(
        "--running-times-log",
        dest="running_times_log_file",
        metavar="FILEPATH",
        help=(
            "maintain a running time log file at FILEPATH "
            "(default: .ggt_cache/running_times.csv; the timings feed "
            "shard balancing and parallel task scheduling)"
        ),
    )
    parser.add_argument(
        "--result-log",
        metavar="FILEPATH",
        help=(
            "write the test result to a log file. If the path contains "
            "%%TIMESTAMP%%, it will be replaced by ISO8601 date and time. "
            "Empty string means not to write the log at all."
        ),
    )
    parser.add_argument(
        "--include-unsuccessful",
        action="store_true",
        help="include the tests that were not successful in the last run",
    )
    parser.add_argument(
        "--list",
        dest="list_tests",
        action="store_true",
        help="list all the tests and exit",
    )
    parser.add_argument(
        "--pytest",
        dest="use_pytest",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "enable pytest-compatible test collection (bare test_* "
            "functions, Test* classes, pytest-style discovery); "
            "enabled by default when pytest is installed, "
            "e.g. via the ggt[pytest] extra"
        ),
    )
    parser.add_argument(
        "-X",
        "--option",
        action=_KeyValueAction,
        default=None,
        metavar="OPTION",
        help=(
            "test suite specific option in key=value format, "
            "e.g `test-db-cache=on` or `data-dir=/some/path`, "
            "be specified multiple times"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    # This process is a test runner, not a ggt worker: discard any
    # worker marker inherited from the environment (e.g. when ggt is
    # itself invoked by a test running inside a ggt worker).
    os.environ.pop("GGT_PARALLEL", None)

    parser = build_parser()
    args = parser.parse_args(argv)
    args.output_format = runner.OutputFormat(args.output_format)
    args.running_times_log_file = _open_running_times_log(
        args.running_times_log_file
    )
    if args.option is None:
        args.option = {}

    try:
        test(**vars(args))
    finally:
        if args.running_times_log_file is not None:
            args.running_times_log_file.close()


def test(
    *,
    files: list[str],
    jobs: int,
    shard: str,
    include: list[str],
    exclude: list[str],
    verbose: bool,
    quiet: bool,
    debug: bool,
    output_format: runner.OutputFormat,
    warnings: bool,
    capture: bool,
    failfast: bool,
    shuffle: bool,
    cov: list[str],
    repeat: int,
    running_times_log_file: TextIO | None,
    list_tests: bool,
    result_log: str | None,
    include_unsuccessful: bool,
    option: dict[str, str],
    distribute: str = "module",
    preload: bool = True,
    use_pytest: bool | None = None,
    mark_expr: str | None = None,
) -> None:
    """Run a test suite.

    Discovers and runs tests in the specified files or directories.
    If no files or directories are specified, current directory is assumed.
    """
    if quiet:
        if verbose:
            console.secho(
                "Warning: both --quiet and --verbose are "
                "specified, assuming --quiet.",
                fg="yellow",
                err=True,
            )
        verbosity = 0
    elif verbose:
        verbosity = 2
    else:
        verbosity = 1

    if jobs == 0:
        jobs = os.cpu_count() or 1

    for pkg in cov:
        if "\\" in pkg or "/" in pkg or pkg.endswith(".py"):
            console.secho(
                f"Error: --cov argument {pkg!r} looks like a path, "
                f"expected a Python package name",
                fg="red",
                err=True,
            )
            sys.exit(1)

    if cov and coverage is None:
        console.secho(
            "Error: --cov requires coverage support.\n"
            f"Enable it with: uv add --dev {_COVERAGE_EXTRA}\n"
            f"Or install it with: python -m pip install '{_COVERAGE_EXTRA}'",
            fg="red",
            err=True,
        )
        sys.exit(1)

    if use_pytest is None:
        use_pytest = pytest_compat.pytest_available()
    elif use_pytest and not pytest_compat.pytest_available():
        console.secho(
            "Error: --pytest requires pytest to be installed.\n"
            "Enable it with: uv add --dev ggt[pytest]\n"
            "Or install it with: python -m pip install 'ggt[pytest]'",
            fg="red",
            err=True,
        )
        sys.exit(1)

    pytest_compat.set_enabled(enabled=use_pytest)
    ini = None
    if use_pytest:
        # Must precede test discovery so that test modules are
        # imported through the assertion-rewriting hook.
        pytest_compat.install_assertion_rewriting()
        pytest_compat.export_options(option)
        ini = pytest_compat.load_ini_config()

    mark_filter = None
    if mark_expr:
        try:
            mark_filter = marks.compile_mark_expression(mark_expr)
        except marks.MarkError as e:
            console.secho(f"Error: {e}", fg="red", err=True)
            sys.exit(1)

    mproc_fixes.patch_multiprocessing(debug=debug)

    if preload and "forkserver" in multiprocessing.get_all_start_methods():
        # Start the fork server immediately: it warms up on the module
        # list cached by the previous run while this process performs
        # test discovery.
        preload_mod.start_forkserver(preload_mod.load_module_cache())

    if verbosity > 1 and output_format is runner.OutputFormat.stacked:
        console.secho(
            "Error: cannot use stacked output format in verbose mode.",
            fg="red",
            err=True,
        )
        sys.exit(1)

    ndjson: NDJSONEmitter | None = None
    if output_format is runner.OutputFormat.json:
        if not capture:
            # With -j1 uncaptured test output writes to the real fd 1
            # and would corrupt the NDJSON stream.
            console.secho(
                "Error: cannot use --no-capture with the json output format.",
                fg="red",
                err=True,
            )
            sys.exit(1)
        # Bind the real stdout now: capture redirects fd 1 later (in
        # this very process for sequential runs).
        ndjson = NDJSONEmitter.for_stdout()

    if repeat < 1:
        console.secho(
            "Error: --repeat must be a positive non-zero number.",
            fg="red",
            err=True,
        )
        sys.exit(1)

    if not files:
        cwd = os.path.abspath(os.getcwd())
        if ini is not None and ini.testpaths:
            files = [
                path
                for path in ini.testpaths
                if os.path.exists(os.path.join(cwd, path))
            ]
        if not files:
            if os.path.exists(os.path.join(cwd, "tests")):
                files = ["tests"]
            else:
                console.secho(
                    "Error: no test path specified and no "
                    '"tests" directory found',
                    fg="red",
                    err=True,
                )
                sys.exit(1)

    for file in files:
        if not os.path.exists(file):
            console.secho(
                f"Error: test path {file!r} does not exist", fg="red", err=True
            )
            sys.exit(1)

    try:
        selected_shard, total_shards = map(int, shard.split("/"))
    except ValueError:
        console.secho(
            f"Error: --shard {shard} must match format e.g. 2/5", err=True
        )
        sys.exit(1)

    if selected_shard < 1 or selected_shard > total_shards:
        console.secho(f"Error: --shard {shard} is out of bound", err=True)
        sys.exit(1)

    def run() -> int:
        return _run(
            include=include,
            exclude=exclude,
            mark_filter=mark_filter,
            verbosity=verbosity,
            files=files,
            jobs=jobs,
            output_format=output_format,
            warnings=warnings,
            capture=capture,
            failfast=failfast,
            shuffle=shuffle,
            distribute=distribute,
            preload=preload,
            repeat=repeat,
            selected_shard=selected_shard,
            total_shards=total_shards,
            running_times_log_file=running_times_log_file,
            list_tests=list_tests,
            result_log=result_log,
            include_unsuccessful=include_unsuccessful,
            options=option,
            ndjson=ndjson,
        )

    try:
        if cov:
            with _coverage_wrapper(cov):
                result = run()
        else:
            result = run()
    except KeyboardInterrupt:
        if ndjson is not None:
            ndjson.event(
                "interrupted",
                level="ERROR",
                stage="summary",
                status="failed",
                clear_label=True,
            )
        raise
    finally:
        if ndjson is not None:
            ndjson.close()

    sys.exit(result)


def _modules_parent_path(modnames: list[str]) -> pathlib.Path:
    origins: set[str] = set()
    for modname in modnames:
        spec = importlib.util.find_spec(modname)
        if spec is not None and spec.origin and spec.has_location:
            origins.add(spec.origin)

    return pathlib.Path(os.path.commonpath(origins))


def _find_pyproject_toml(path: pathlib.Path) -> pathlib.Path:
    for modpath in [path, *path.parents]:
        cov_rc = modpath / "pyproject.toml"
        if cov_rc.exists():
            return cov_rc

    raise RuntimeError("cannot locate the pyproject.toml file")


@contextlib.contextmanager
def _coverage_wrapper(paths: list[str]) -> Iterator[None]:
    assert coverage is not None
    cov_rc = _find_pyproject_toml(_modules_parent_path(paths))

    with tempfile.TemporaryDirectory() as td:
        cov_config = cov.CoverageConfig(
            paths=list(paths), config=str(cov_rc), datadir=td
        )
        cov_config.save_to_environ()

        main_cov = cov_config.new_coverage_object()
        main_cov.start()

        try:
            yield
        finally:
            main_cov.stop()
            main_cov.save()

            covfile = str(pathlib.Path(td) / ".coverage")
            data = coverage.CoverageData(covfile)

            with os.scandir(td) as it:
                for entry in it:
                    new_data = coverage.CoverageData(entry.path)
                    new_data.read()
                    data.update(new_data)

            data.write()
            report_cov = cov_config.new_custom_coverage_object(
                config_file=str(cov_rc),
                data_file=covfile,
            )
            report_cov.load()
            console.secho("Coverage:")
            report_cov.report()
            # store the coverage file in cwd, so it can be used to produce
            # additional reports with coverage cli
            shutil.copy(covfile, ".")


def _emit_result_events(
    ndjson: NDJSONEmitter, result: results.TestResult
) -> None:
    """Emit the end-of-run result as NDJSON events.

    Per-case detail events carry the full captured traceback/output
    under ``ggt.detail`` and deliberately have no ``message``: the
    concise real-time ERROR event already reported each failure, so
    log renderers skip these while raw NDJSON consumers get the data.
    """
    detail_kinds = (
        ("failure", result.failures),
        ("error", result.errors),
        ("unexpected_success", result.unexpected_successes),
    )
    for kind, cases in detail_kinds:
        for case in cases:
            ndjson.event(
                level="ERROR",
                stage="summary",
                extra={
                    "ggt.detail": {
                        "kind": kind,
                        **dataclasses.asdict(case),
                    }
                },
            )

    counts = {
        "failures": len(result.failures),
        "errors": len(result.errors),
        "unexpected_successes": len(result.unexpected_successes),
        "not_implemented": len(result.not_implemented),
        "skipped": len(result.skipped),
        "expected_failures": len(result.expected_failures),
        "warnings": len(result.warnings),
    }
    problems = ", ".join(
        f"{count} {name.replace('_', ' ')}"
        for name, count in counts.items()
        if count and name in {"failures", "errors", "unexpected_successes"}
    )
    time_taken = results._format_time(
        result.setup_time_taken + result.tests_time_taken
    )
    outcome = "SUCCESS" if result.was_successful else "FAILURE"
    message = f"{outcome}: {result.testsRun} tests"
    if problems:
        message += f", {problems}"
    message += f" in {time_taken}"
    ndjson.event(
        message,
        level="INFO" if result.was_successful else "ERROR",
        stage="summary",
        status="finished",
        description=message,
        completed=result.testsRun,
        total=result.testsRun,
        clear_label=True,
        extra={
            "ggt.summary": {
                "was_successful": result.was_successful,
                "tests_run": result.testsRun,
                "setup_time_taken": result.setup_time_taken,
                "tests_time_taken": result.tests_time_taken,
                **counts,
            }
        },
    )


def _run(
    *,
    include: list[str],
    exclude: list[str],
    mark_filter: Callable[[frozenset[str]], bool] | None,
    verbosity: int,
    files: list[str],
    jobs: int,
    output_format: runner.OutputFormat,
    warnings: bool,
    capture: bool,
    failfast: bool,
    shuffle: bool,
    distribute: str,
    preload: bool,
    repeat: int,
    selected_shard: int,
    total_shards: int,
    running_times_log_file: TextIO | None,
    list_tests: bool,
    result_log: str | None,
    include_unsuccessful: bool,
    options: dict[str, str],
    ndjson: NDJSONEmitter | None = None,
) -> int:
    total = 0
    total_unfiltered = 0

    def progress_callback(n: int, unfiltered_n: int) -> None:
        nonlocal total, total_unfiltered
        total += n
        total_unfiltered += unfiltered_n
        if ndjson is not None:
            # No total: collection progress is indeterminate until
            # discovery completes.
            ndjson.event(
                description=f"collected {total}/{total_unfiltered} tests",
                completed=total,
            )
        elif verbosity > 0:
            console.echo(
                styles.status(
                    f"Collected {total}/{total_unfiltered} tests.\r"
                ),
                nl=False,
                err=list_tests,
            )

    update_progress: Callable[[int, int], None] | None = progress_callback

    if include_unsuccessful and result_log:
        unsuccessful = results.read_unsuccessful(result_log)
        include = list(include) + unsuccessful + ["a_non_existing_test"]

    for file in files:
        if not os.path.exists(file) and verbosity > 0:
            console.echo(
                styles.warning(f"Warning: {file}: no such file or directory.")
            )

    # Warnings emitted during collection (e.g. conftest compatibility
    # notices) go to the end-of-run warning summary; in --list mode
    # there is no summary, so let them print through.
    with (
        ndjson.stage("collect")
        if ndjson is not None
        else contextlib.nullcontext()
    ):
        with runner.recorded_warnings(
            enabled=warnings and not list_tests
        ) as collection_warnings:
            suite = loader.discover(
                files,
                verbosity=verbosity,
                include=include,
                exclude=exclude,
                progress_cb=update_progress,
                mark_filter=mark_filter,
            )
        if ndjson is not None:
            ndjson.event(
                f"collected {total_unfiltered} tests, selected {total}",
                description=(
                    f"collected {total_unfiltered} tests, selected {total}"
                ),
                completed=total,
            )

    if preload:
        # Record the post-discovery module set to warm up the next
        # run's fork server (see ggt._internal.preload).
        preload_mod.save_module_cache()

    if list_tests:
        cases = loader.get_test_cases([suite])
        if ndjson is not None:
            for test_group in cases.values():
                for test in test_group:
                    ndjson.event(
                        str(test),
                        stage="collect",
                        extra={"ggt.test": test.id()},
                    )
        else:
            console.echo(err=True)
            for test_group in cases.values():
                for test in test_group:
                    console.echo(str(test))
        return 0

    jobs = max(min(total, jobs), 1)

    if ndjson is not None:
        if jobs > 1:
            ndjson.event(f"using up to {jobs} processes to run tests")
    elif verbosity > 0:
        console.echo()
        if jobs > 1:
            console.echo(
                styles.status(f"Using up to {jobs} processes to run tests.")
            )

    result = None
    for rnum in range(repeat):
        if repeat > 1:
            if ndjson is not None:
                ndjson.event(
                    f"repeat #{rnum + 1} out of {repeat}",
                    extra={"ggt.repeat": f"{rnum + 1}/{repeat}"},
                )
            else:
                console.echo(
                    styles.status(f"Repeat #{rnum + 1} out of {repeat}.")
                )

        test_runner = runner.ParallelTextTestRunner(
            verbosity=verbosity,
            output_format=runner.OutputFormat(output_format),
            warnings=warnings,
            capture_output=capture,
            num_workers=jobs,
            failfast=failfast,
            shuffle=shuffle,
            distribute=distribute,
            options=options,
            ndjson=ndjson,
        )

        result = test_runner.run(
            suite,
            selected_shard,
            total_shards,
            running_times_log_file,
            collection_warnings=collection_warnings,
        )

        if ndjson is not None:
            _emit_result_events(ndjson, result)
        elif verbosity > 0:
            results.render_result(test_runner.stream, result)

        if not result.was_successful:
            break

    assert result is not None
    if result_log:
        results.write_result(result_log, result)

    return 0 if result.was_successful else 1
