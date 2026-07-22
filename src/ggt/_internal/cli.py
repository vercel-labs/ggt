# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.


from __future__ import annotations

import argparse
import contextlib
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

from . import console, cov, loader, marks, mproc_fixes, results, runner, styles
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
        dest="warnings",
        action="store_true",
        default=True,
        help="enable warnings (enabled by default)",
    )
    parser.add_argument(
        "--no-warnings",
        dest="warnings",
        action="store_false",
        help="disable warnings",
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
            )
            sys.exit(1)

    if cov and coverage is None:
        console.secho(
            "Error: --cov requires coverage support.\n"
            f"Enable it with: uv add --dev {_COVERAGE_EXTRA}\n"
            f"Or install it with: python -m pip install '{_COVERAGE_EXTRA}'",
            fg="red",
        )
        sys.exit(1)

    mark_filter = None
    if mark_expr:
        try:
            mark_filter = marks.compile_mark_expression(mark_expr)
        except marks.MarkError as e:
            console.secho(f"Error: {e}", fg="red")
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
        )
        sys.exit(1)

    if repeat < 1:
        console.secho(
            "Error: --repeat must be a positive non-zero number.", fg="red"
        )
        sys.exit(1)

    if not files:
        cwd = os.path.abspath(os.getcwd())
        if os.path.exists(os.path.join(cwd, "tests")):
            files = ["tests"]
        else:
            console.secho(
                'Error: no test path specified and no "tests" directory found',
                fg="red",
            )
            sys.exit(1)

    for file in files:
        if not os.path.exists(file):
            console.secho(
                f"Error: test path {file!r} does not exist", fg="red"
            )
            sys.exit(1)

    try:
        selected_shard, total_shards = map(int, shard.split("/"))
    except ValueError:
        console.secho(f"Error: --shard {shard} must match format e.g. 2/5")
        sys.exit(1)

    if selected_shard < 1 or selected_shard > total_shards:
        console.secho(f"Error: --shard {shard} is out of bound")
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
        )

    if cov:
        with _coverage_wrapper(cov):
            result = run()
    else:
        result = run()

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
) -> int:
    total = 0
    total_unfiltered = 0

    def progress_callback(n: int, unfiltered_n: int) -> None:
        nonlocal total, total_unfiltered
        total += n
        total_unfiltered += unfiltered_n
        if verbosity > 0:
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

    suite = loader.discover(
        files,
        verbosity=verbosity,
        include=include,
        exclude=exclude,
        progress_cb=update_progress,
        mark_filter=mark_filter,
    )

    if preload:
        # Record the post-discovery module set to warm up the next
        # run's fork server (see ggt._internal.preload).
        preload_mod.save_module_cache()

    if list_tests:
        console.echo(err=True)
        cases = loader.get_test_cases([suite])
        for test_group in cases.values():
            for test in test_group:
                console.echo(str(test))
        return 0

    jobs = max(min(total, jobs), 1)

    if verbosity > 0:
        console.echo()
        if jobs > 1:
            console.echo(
                styles.status(f"Using up to {jobs} processes to run tests.")
            )

    result = None
    for rnum in range(repeat):
        if repeat > 1:
            console.echo(styles.status(f"Repeat #{rnum + 1} out of {repeat}."))

        test_runner = runner.ParallelTextTestRunner(
            verbosity=verbosity,
            output_format=runner.OutputFormat(output_format),
            warnings=warnings,
            num_workers=jobs,
            failfast=failfast,
            shuffle=shuffle,
            distribute=distribute,
            options=options,
        )

        result = test_runner.run(
            suite,
            selected_shard,
            total_shards,
            running_times_log_file,
        )

        if verbosity > 0:
            results.render_result(test_runner.stream, result)

        if not result.was_successful:
            break

    assert result is not None
    if result_log:
        results.write_result(result_log, result)

    return 0 if result.was_successful else 1
