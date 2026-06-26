# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

from __future__ import annotations

import asyncio
import csv
import importlib.util
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
            sys.executable,
            "-m",
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
            (["missing.py"], "does not exist"),
            (["--cov", "pkg/path", "tests"], "looks like a path"),
        ]

        for args, expected in cases:
            with self.subTest(args=args):
                result = await self.run_ggt(*args)
                await self.assert_failure(result)
                self.assertIn(expected, result.output)

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
        self.assertEqual(data["testsRun"], 9)
        self.assertEqual(len(data["failures"]), 2)
        self.assertEqual(len(data["errors"]), 1)
        self.assertEqual(len(data["skipped"]), 1)
        self.assertEqual(len(data["expected_failures"]), 2)
        self.assertEqual(len(data["not_implemented"]), 1)
        self.assertEqual(len(data["unexpected_successes"]), 1)
        self.assertEqual(len(data["warnings"]), 1)

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

    async def test_class_hooks_fixtures_options_and_parallel_shared_data(
        self,
    ) -> None:
        events = self.project / "events.jsonl"
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
            json.loads(line)
            for line in events.read_text(encoding="utf-8").splitlines()
        ]
        names = [entry["event"] for entry in entries]
        self.assertEqual(names.count("fixture_setup"), 1)
        self.assertEqual(names.count("fixture_teardown"), 1)
        self.assertEqual(names.count("class_setup"), 1)
        self.assertEqual(names.count("class_teardown"), 1)
        self.assertIn("fixture_post", names)
        self.assertIn("fixture_import", names)
        self.assertIn("class_import", names)

    async def test_coverage_success_and_missing_coverage_message(self) -> None:
        self.use_fixture("samplepkg")

        if importlib.util.find_spec("coverage") is None:
            result = await self.run_ggt(
                "tests/test_samplepkg.py",
                "-j1",
                "--cov",
                "samplepkg",
                "--output-format",
                "simple",
            )
            self.assertIn("--cov requires coverage support", result.output)
        else:
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
