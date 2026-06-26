# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

from __future__ import annotations

import asyncio
import csv
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from dataclasses import dataclass


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
FIXTURES = REPO_ROOT / "fixtures"


@dataclass(frozen=True)
class RunResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        return self.stdout + self.stderr


class FunctionalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.project = pathlib.Path(self._td.name)
        self.tests_dir = self.project / "tests"
        self.tests_dir.mkdir()
        self.write(self.tests_dir / "__init__.py")
        self.write(self.project / "pyproject.toml", "[project]\nname='x'\n")

    def tearDown(self) -> None:
        self._td.cleanup()

    def write(self, path: pathlib.Path, content: str = "") -> pathlib.Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def use_fixture(self, name: str) -> None:
        shutil.copytree(FIXTURES / name, self.project, dirs_exist_ok=True)

    def env(self, **extra: str) -> dict[str, str]:
        paths = [str(SRC), str(self.project)]
        if old := os.environ.get("PYTHONPATH"):
            paths.append(old)
        env = {
            **os.environ,
            "PYTHONPATH": os.pathsep.join(paths),
            "NO_COLOR": "1",
        }
        env.update(extra)
        return env

    async def run_ggt(
        self,
        *args: str,
        cwd: pathlib.Path | None = None,
        env: dict[str, str] | None = None,
    ) -> RunResult:
        proc = await asyncio.create_subprocess_exec(
            "ggt",
            *args,
            cwd=cwd or self.project,
            env=env or self.env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        assert proc.returncode is not None
        return RunResult(
            proc.returncode,
            stdout.decode("utf-8", "replace"),
            stderr.decode("utf-8", "replace"),
        )

    async def assert_success(self, result: RunResult) -> None:
        self.assertEqual(result.returncode, 0, result.output)

    async def assert_failure(self, result: RunResult) -> None:
        self.assertNotEqual(result.returncode, 0, result.output)

    def skip_if_multiprocessing_blocked(self, result: RunResult) -> None:
        sandbox_error = "PermissionError: [Errno 1] Operation not permitted"
        if sandbox_error in result.output:
            self.skipTest("multiprocessing forkserver is blocked by sandbox")

    async def test_help_lists_options_and_coverage_hint(self) -> None:
        result = await self.run_ggt("--help")
        await self.assert_success(result)
        self.assertIn("--cov", result.stdout)
        self.assertIn("--result-log", result.stdout)
        self.assertIn("-X", result.stdout)

    async def test_validation_errors_are_friendly(self) -> None:
        cases = [
            (["-X", "missing-equals"], "Expected format key=value"),
            (["--repeat", "0"], "--repeat must be"),
            (["--shard", "bad"], "must match format"),
            (["--shard", "2/1"], "is out of bound"),
            (["missing.py"], "does not exist"),
            (["--cov", "pkg/path", "tests"], "looks like a path"),
        ]

        for args, expected in cases:
            with self.subTest(args=args):
                result = await self.run_ggt(*args)
                await self.assert_failure(result)
                self.assertIn(expected, result.output)

    async def test_no_path_without_tests_directory_fails(self) -> None:
        empty = self.project / "empty"
        empty.mkdir()
        result = await self.run_ggt(cwd=empty)
        await self.assert_failure(result)
        self.assertIn('no "tests" directory found', result.output)

    async def test_verbose_stacked_is_rejected(self) -> None:
        self.use_fixture("basic")
        result = await self.run_ggt(
            "tests/test_basic.py",
            "-v",
            "--output-format",
            "stacked",
        )
        await self.assert_failure(result)
        self.assertIn("cannot use stacked output format", result.output)

    async def test_no_path_defaults_to_tests_directory(self) -> None:
        self.use_fixture("basic")
        result = await self.run_ggt("-j1", "--output-format", "simple")
        await self.assert_success(result)
        self.assertIn("tests ran: 5", result.output)
        self.assertIn("skipped: 1", result.output)

    async def test_discovery_selection_and_listing(self) -> None:
        self.use_fixture("basic")
        self.use_fixture("pkg")

        listed = await self.run_ggt("tests", "--list")
        await self.assert_success(listed)
        self.assertIn("test_pass_a", listed.stdout)
        self.assertIn("test_from_package", listed.stdout)
        self.assertIn("test_skipped", listed.stdout)

        by_file = await self.run_ggt(
            "tests/test_basic.py",
            "-k",
            "select_me",
            "-e",
            "exclude_me",
            "-j1",
            "--output-format",
            "simple",
        )
        await self.assert_success(by_file)
        self.assertIn("tests ran: 1", by_file.output)

        by_package = await self.run_ggt(
            "tests/pkg",
            "-j1",
            "--output-format",
            "simple",
        )
        await self.assert_success(by_package)
        self.assertIn("tests ran: 1", by_package.output)

    async def test_repeated_include_and_exclude_filters(self) -> None:
        self.use_fixture("basic")
        result = await self.run_ggt(
            "tests/test_basic.py",
            "-k",
            "pass_a",
            "-k",
            "select_me",
            "-e",
            "pass_b",
            "-e",
            "exclude_me",
            "-j1",
            "--output-format",
            "simple",
        )
        await self.assert_success(result)
        self.assertIn("tests ran: 2", result.output)

    async def test_fixture_projects_are_directly_runnable(self) -> None:
        cases = [
            ("fixtures/basic/tests", "tests ran: 5"),
            ("fixtures/modes/tests", "tests ran: 4"),
            ("fixtures/pkg/tests", "tests ran: 1"),
        ]

        for path, expected in cases:
            with self.subTest(path=path):
                result = await self.run_ggt(
                    path,
                    "-j1",
                    "--output-format",
                    "simple",
                    cwd=REPO_ROOT,
                    env=self.env(),
                )
                await self.assert_success(result)
                self.assertIn(expected, result.output)

    async def test_result_log_classifies_outcomes(self) -> None:
        log = self.project / "result.json"
        self.use_fixture("outcomes")

        result = await self.run_ggt(
            "tests/test_outcomes.py",
            "-j1",
            "--result-log",
            str(log),
            "--output-format",
            "simple",
        )
        await self.assert_failure(result)

        data = json.loads(log.read_text(encoding="utf-8"))
        self.assertFalse(data["was_successful"])
        self.assertEqual(data["testsRun"], 12)
        self.assertEqual(len(data["failures"]), 2)
        self.assertEqual(len(data["errors"]), 2)
        self.assertEqual(len(data["skipped"]), 1)
        self.assertEqual(len(data["expected_failures"]), 3)
        self.assertEqual(len(data["not_implemented"]), 1)
        self.assertEqual(len(data["unexpected_successes"]), 1)
        self.assertEqual(len(data["warnings"]), 1)

    async def test_parallel_result_log_classifies_outcomes(self) -> None:
        log = self.project / "parallel-result.json"
        self.use_fixture("outcomes")

        result = await self.run_ggt(
            "tests/test_outcomes.py",
            "-j2",
            "--result-log",
            str(log),
            "--output-format",
            "simple",
        )
        self.skip_if_multiprocessing_blocked(result)
        await self.assert_failure(result)

        data = json.loads(log.read_text(encoding="utf-8"))
        self.assertEqual(data["testsRun"], 12)
        self.assertEqual(len(data["failures"]), 2)
        self.assertEqual(len(data["errors"]), 2)
        self.assertEqual(len(data["expected_failures"]), 3)
        self.assertEqual(len(data["warnings"]), 1)

    async def test_result_log_timestamp_and_no_warnings(self) -> None:
        self.use_fixture("outcomes")
        log_template = str(self.project / "logs" / "result-%%TIMESTAMP%%.json")
        result = await self.run_ggt(
            "tests/test_outcomes.py",
            "-j1",
            "--no-warnings",
            "--result-log",
            log_template,
            "--output-format",
            "silent",
        )
        await self.assert_failure(result)

        logs = sorted((self.project / "logs").glob("result-*.json"))
        self.assertEqual(len(logs), 1)
        data = json.loads(logs[0].read_text(encoding="utf-8"))
        self.assertEqual(data["warnings"], [])

    async def test_include_unsuccessful_uses_previous_result_log(self) -> None:
        log = self.project / "logs" / "result.json"
        marker = self.project / "marker"
        self.use_fixture("flaky")

        env = self.env(GGT_FUNCTIONAL_MARKER=str(marker))
        first = await self.run_ggt(
            "tests/test_flaky.py",
            "-j1",
            "--result-log",
            str(log),
            "--output-format",
            "simple",
            env=env,
        )
        await self.assert_failure(first)

        second = await self.run_ggt(
            "tests/test_flaky.py",
            "-j1",
            "--result-log",
            str(log),
            "--include-unsuccessful",
            "--output-format",
            "simple",
            env=env,
        )
        await self.assert_success(second)
        self.assertIn("tests ran: 1", second.output)

    async def test_execution_modes_repeat_shard_failfast_and_timings(
        self,
    ) -> None:
        times = self.project / "times.csv"
        self.use_fixture("modes")

        for jobs in ["1", "2"]:
            with self.subTest(jobs=jobs):
                result = await self.run_ggt(
                    "tests/test_modes.py",
                    "-j",
                    jobs,
                    "--output-format",
                    "simple",
                )
                self.skip_if_multiprocessing_blocked(result)
                await self.assert_success(result)
                self.assertIn("tests ran: 4", result.output)

        repeated = await self.run_ggt(
            "tests/test_modes.py",
            "-j1",
            "--repeat",
            "2",
            "--output-format",
            "simple",
        )
        await self.assert_success(repeated)
        self.assertIn("Repeat #2 out of 2", repeated.output)

        shard = await self.run_ggt(
            "tests/test_modes.py",
            "-j1",
            "--shard",
            "1/2",
            "--running-times-log",
            str(times),
            "--output-format",
            "simple",
        )
        await self.assert_success(shard)
        self.assertTrue(times.exists())
        rows = list(csv.reader(times.read_text(encoding="utf-8").splitlines()))
        self.assertTrue(rows)

        self.use_fixture("failfast")
        stopped = await self.run_ggt(
            "tests/test_failfast.py",
            "-j1",
            "--failfast",
            "--output-format",
            "simple",
        )
        await self.assert_failure(stopped)
        self.assertIn("tests ran: 1", stopped.output)

    async def test_repeat_named_tests_are_ordered_after_first_runs(
        self,
    ) -> None:
        self.use_fixture("edgecases")
        result = await self.run_ggt(
            "tests/test_repeat_names.py",
            "-j1",
            "--list",
        )
        await self.assert_success(result)
        lines = [
            line for line in result.stdout.splitlines() if "test_" in line
        ]
        self.assertEqual(
            [line.split()[0] for line in lines],
            ["test_alpha", "test_beta", "test_zREPEAT_alpha"],
        )

    async def test_sharding_with_setup_scripts_and_timing_stats(self) -> None:
        self.use_fixture("sharding")
        times = self.project / "shard-times.csv"
        times.write_text(
            "setup::db-a,0.01,1\n"
            "setup::unknown,0.02,1\n"
            "test_a_1 (tests.test_sharding.SetupShardA.test_a_1),0.01,1\n",
            encoding="utf-8",
        )
        counts: list[int] = []

        for shard in ["1/3", "2/3", "3/3"]:
            result = await self.run_ggt(
                "tests/test_sharding.py",
                "-j1",
                "--shard",
                shard,
                "--running-times-log",
                str(times),
                "--output-format",
                "simple",
            )
            await self.assert_success(result)
            counts.extend(
                int(line.rpartition(" ")[2])
                for line in result.output.splitlines()
                if "tests ran:" in line
            )

        self.assertEqual(len(counts), 3)
        self.assertTrue(all(count > 0 for count in counts))
        timing_text = times.read_text(encoding="utf-8")
        self.assertIn("setup::db-a", timing_text)
        self.assertIn("test_a_1", timing_text)

        cached = await self.run_ggt(
            "tests/test_sharding.py",
            "-j1",
            "--shard",
            "2/3",
            "--running-times-log",
            str(times),
            "--output-format",
            "simple",
        )
        await self.assert_success(cached)

    async def test_subtest_failure_is_reported_as_error(self) -> None:
        self.use_fixture("edgecases")
        log = self.project / "subtests.json"
        result = await self.run_ggt(
            "tests/test_subtests.py",
            "-j1",
            "--result-log",
            str(log),
            "--output-format",
            "verbose",
        )
        await self.assert_failure(result)
        self.assertIn("test_subtests_fail_as_error", result.output)

        data = json.loads(log.read_text(encoding="utf-8"))
        self.assertEqual(data["testsRun"], 2)
        self.assertEqual(len(data["errors"]), 1)
        self.assertIn("value=2", data["errors"][0]["id"])

    async def test_server_context_and_cancelled_timeout_are_reported(
        self,
    ) -> None:
        self.use_fixture("edgecases")
        log = self.project / "context.json"
        result = await self.run_ggt(
            "tests/test_error_context.py",
            "-j1",
            "--result-log",
            str(log),
            "--output-format",
            "simple",
        )
        await self.assert_failure(result)
        self.assertIn("server-side context", result.output)
        self.assertIn("timeout after 1", result.output)

        data = json.loads(log.read_text(encoding="utf-8"))
        self.assertEqual(len(data["errors"]), 1)
        self.assertEqual(len(data["failures"]), 1)
        self.assertTrue(
            any(case["server_traceback"] for case in data["errors"])
        )

    async def test_class_setup_error_and_teardown_warning(self) -> None:
        self.use_fixture("edgecases")
        events = self.project / "setup-events.txt"
        result = await self.run_ggt(
            "tests/test_setup_error.py",
            "-j1",
            "-X",
            "mode=broken",
            "--output-format",
            "simple",
            env=self.env(GGT_FUNCTIONAL_EVENTS=str(events)),
        )
        await self.assert_failure(result)
        self.assertIn("setup broke", result.output)

        event_lines = events.read_text(encoding="utf-8").splitlines()
        self.assertEqual(event_lines[:2], ["options:broken", "setup"])

    async def test_output_formats_smoke(self) -> None:
        self.use_fixture("basic")
        for fmt in ["simple", "verbose", "stacked", "silent"]:
            with self.subTest(fmt=fmt):
                result = await self.run_ggt(
                    "tests/test_basic.py",
                    "-j1",
                    "--output-format",
                    fmt,
                )
                await self.assert_success(result)
                self.assertIn("SUCCESS", result.output)
                if fmt == "verbose":
                    self.assertIn("test_pass_a", result.output)

    async def test_quiet_verbose_and_shuffle_smoke(self) -> None:
        self.use_fixture("modes")
        result = await self.run_ggt(
            "tests/test_modes.py",
            "--quiet",
            "--verbose",
            "--shuffle",
            "-j1",
            "--output-format",
            "simple",
        )
        await self.assert_success(result)
        self.assertIn("both --quiet and --verbose", result.output)
        self.assertNotIn("SUCCESS", result.output)

    async def test_class_hooks_fixtures_options_and_parallel_shared_data(
        self,
    ) -> None:
        events = self.project / "events"
        self.use_fixture("hooks")
        result = await self.run_ggt(
            "tests/test_hooks.py",
            "-j2",
            "-X",
            "color=blue",
            "--output-format",
            "simple",
            env=self.env(GGT_FUNCTIONAL_EVENTS=str(events)),
        )
        self.skip_if_multiprocessing_blocked(result)
        await self.assert_success(result)

        entries = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in events.glob("*.json")
        ]
        names = [entry["event"] for entry in entries]
        self.assertEqual(names.count("fixture_setup"), 1)
        self.assertEqual(names.count("fixture_teardown"), 1)
        self.assertEqual(names.count("class_setup"), 1)
        self.assertEqual(names.count("class_teardown"), 1)
        self.assertIn("fixture_post", names)
        self.assertIn("fixture_import", names)
        self.assertIn("class_import", names)

    async def test_parallel_pickling_restores_testcase_variants(self) -> None:
        self.use_fixture("pickle")
        result = await self.run_ggt(
            "tests/test_pickle_cases.py",
            "-j2",
            "--output-format",
            "simple",
        )
        self.skip_if_multiprocessing_blocked(result)
        await self.assert_success(result)
        self.assertIn("tests ran: 5", result.output)

    async def test_parallel_granularity_sorting_modes(self) -> None:
        self.use_fixture("granularity")
        events = self.project / "granularity-events"
        result = await self.run_ggt(
            "tests/test_granularity.py",
            "-j2",
            "--output-format",
            "simple",
            env=self.env(GGT_FUNCTIONAL_EVENTS=str(events)),
        )
        self.skip_if_multiprocessing_blocked(result)
        await self.assert_success(result)
        self.assertIn("tests ran: 6", result.output)
        event_names = [
            path.read_text(encoding="utf-8") for path in events.glob("*.txt")
        ]
        self.assertEqual(
            sorted(event_names),
            [
                "default-a",
                "default-b",
                "suite-a",
                "suite-b",
                "system-a",
                "system-b",
            ],
        )

    async def test_still_running_status_is_reported(self) -> None:
        self.use_fixture("slow")
        result = await self.run_ggt(
            "tests/test_slow.py",
            "-j2",
            "--output-format",
            "verbose",
        )
        self.skip_if_multiprocessing_blocked(result)
        await self.assert_success(result)
        self.assertIn("still running", result.output)

    async def test_parallel_failfast_stops_after_first_failure(self) -> None:
        self.use_fixture("failfast")
        result = await self.run_ggt(
            "tests/test_failfast.py",
            "-j2",
            "--failfast",
            "--output-format",
            "simple",
        )
        self.skip_if_multiprocessing_blocked(result)
        await self.assert_failure(result)
        self.assertIn("test_1_fail", result.output)
        self.assertIn("failures: 1", result.output)

    async def test_results_module_renders_combined_logs(self) -> None:
        self.use_fixture("outcomes")
        logs = self.project / "logs"
        result = await self.run_ggt(
            "tests/test_outcomes.py",
            "-j1",
            "--result-log",
            str(logs / "a.json"),
            "--output-format",
            "silent",
        )
        await self.assert_failure(result)

        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "ggt._internal.results",
            str(logs / "*.json"),
            cwd=self.project,
            env=self.env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        self.assertEqual(
            proc.returncode,
            1,
            stdout.decode("utf-8", "replace")
            + stderr.decode("utf-8", "replace"),
        )
        self.assertIn("FAILURE", stdout.decode("utf-8", "replace"))

    async def test_coverage_success_and_missing_coverage_message(self) -> None:
        self.use_fixture("samplepkg")

        result = await self.run_ggt(
            "tests/test_samplepkg.py",
            "-j1",
            "--cov",
            "samplepkg",
            "--output-format",
            "simple",
        )
        await self.assert_success(result)
        self.assertIn("Coverage:", result.output)
        self.assertTrue((self.project / ".coverage").exists())

        blocker = self.project / "block_coverage"
        self.write(
            blocker / "coverage.py",
            "raise ImportError('coverage intentionally hidden')\n",
        )
        hidden_env = self.env(
            PYTHONPATH=os.pathsep.join(
                [str(blocker), str(SRC), str(self.project)]
            )
        )
        hidden_env.pop("GGT_COVERAGE", None)
        help_result = await self.run_ggt("--help", env=hidden_env)
        await self.assert_success(help_result)
        self.assertIn("coverage support is not", help_result.stdout)
        self.assertIn("enabled", help_result.stdout)

        cov_result = await self.run_ggt(
            "tests/test_samplepkg.py",
            "--cov",
            "samplepkg",
            env=hidden_env,
        )
        await self.assert_failure(cov_result)
        self.assertIn("--cov requires coverage support", cov_result.output)
        self.assertIn("uv add --dev ggt[coverage]", cov_result.output)


if __name__ == "__main__":
    unittest.main()
