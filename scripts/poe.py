#!/usr/bin/env python3
from __future__ import annotations

from typing import Any

import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
QUIET_FAILURE_DETAIL = "ggt.failure.detail"
FAILURE_TAIL_LINES = 20
PYTHON_MATRIX = ("3.11", "3.12", "3.13", "3.14")


@dataclass(frozen=True)
class Command:
    label: str
    argv: tuple[str, ...]
    category: str
    parser: str | None = None
    quiet: bool = True

    @property
    def display_label(self) -> str:
        return self.label


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        raise SystemExit(
            "usage: scripts/poe.py "
            "<lint|typecheck|test|test-python-matrix|qa|pre-commit|pre-push>"
        )
    command = args.pop(0)
    verbose = parse_verbose(args)
    if command == "lint":
        return run_group(lint_commands(), verbose=verbose)
    if command == "typecheck":
        return run_group(typecheck_commands(), verbose=verbose)
    if command == "test":
        return run_group(test_commands(verbose=verbose), verbose=verbose)
    if command == "test-python-matrix":
        return run_group(
            test_python_matrix_commands(verbose=verbose), verbose=verbose
        )
    if command == "qa":
        return run_group(
            (
                *lint_commands(),
                *typecheck_commands(),
                *test_commands(verbose=verbose),
            ),
            verbose=verbose,
        )
    if command == "pre-commit":
        return run_group((*lint_commands(), *typecheck_commands()))
    if command == "pre-push":
        return run_group((
            *lint_commands(),
            *typecheck_commands(),
            *test_commands(),
        ))
    raise SystemExit(f"unknown poe command: {command}")


def parse_verbose(args: Sequence[str]) -> bool:
    verbose = poe_verbose_enabled()
    remaining = list(args)
    while remaining:
        arg = remaining.pop(0)
        if arg == "--poe-verbose":
            if not remaining:
                raise SystemExit("--poe-verbose requires a boolean value")
            verbose = parse_bool(remaining.pop(0))
            continue
        raise SystemExit(f"unexpected scripts/poe.py argument: {arg}")
    return verbose


def parse_bool(value: str) -> bool:
    return value.lower() in {"1", "true", "yes"}


def poe_verbose_enabled() -> bool:
    value = os.environ.get("POE_VERBOSITY")
    if value is None:
        return False
    try:
        return int(value) >= 0
    except ValueError:
        return False


def lint_commands() -> tuple[Command, ...]:
    return (
        Command(
            "ruff check", ("ruff", "check", "src/ggt", "pyproject.toml"), "lint"
        ),
        Command(
            "ruff format",
            ("ruff", "format", "--check", "src", "tests"),
            "lint",
        ),
        Command(
            "zizmor",
            ("zizmor", "--offline", ".github/workflows", "action.yml"),
            "lint",
        ),
        Command(
            "readme", ("python", "scripts/check-readme.py"), "lint"
        ),
    )


def typecheck_commands() -> tuple[Command, ...]:
    return (
        Command("mypy", ("mypy",), "typecheck"),
        Command("ty", ("ty", "check"), "typecheck"),
    )


def fancy_output_expected() -> bool:
    """Return whether lograil will render a live fancy display."""
    mode = os.environ.get("LOGRAIL_OUTPUT", "").strip().lower()
    if mode:
        return mode == "fancy"
    return sys.stderr.isatty()


def ggt_output_format(*, verbose: bool) -> str:
    # ggt emits lograil-native NDJSON entries, so no output parser is
    # needed: the default remaps adopt them and drive the progress bar.
    # Without a live display (and not in verbose mode), the NDJSON
    # stream would render as one plain line per test; fall back to
    # ggt's own compact simple output instead.
    if verbose or fancy_output_expected():
        return "json"
    return "simple"


def test_commands(*, verbose: bool = False) -> tuple[Command, ...]:
    return (
        Command(
            "ggt tests",
            (
                "ggt",
                "tests",
                "--output-format",
                ggt_output_format(verbose=verbose),
            ),
            "test",
            quiet=False,
        ),
    )


def test_python_matrix_commands(
    *, verbose: bool = False
) -> tuple[Command, ...]:
    return tuple(
        Command(
            f"py{version}",
            (
                "uv",
                "run",
                "--no-sync",
                "tox",
                "run",
                "-e",
                f"py{version.replace('.', '')}",
                "--",
                "--output-format",
                ggt_output_format(verbose=verbose),
            ),
            "test matrix",
            quiet=False,
        )
        for version in PYTHON_MATRIX
    )


def run_group(commands: Sequence[Command], *, verbose: bool = False) -> int:
    if not commands:
        return 0
    from lograil import (  # ruff:ignore[import-outside-top-level]
        DEFAULT_REMAPS,
        ProcessSpec,
        configure_logging,
        run_process_group,
    )

    if verbose:
        os.environ.setdefault("LOGRAIL_OUTPUT", "plain")
    configure_logging()
    env = base_env()
    specs = [
        ProcessSpec(
            command.argv,
            cwd=str(ROOT),
            env=env,
            process=command.display_label,
            subject=command.label,
            category=command.category,
            stream="combined",
            parser=command.parser,
            remaps=(
                (*DEFAULT_REMAPS, quiet_entry)
                if command.quiet and not verbose
                else None
            ),
        )
        for command in commands
    ]
    result = run_process_group(specs)
    if not result.success:
        print_failure_summary(result.processes)
    return 0 if result.success else 1


def base_env() -> dict[str, str]:
    env = os.environ.copy()
    pythonpath = str(SRC)
    if existing := env.get("PYTHONPATH"):
        pythonpath = f"{pythonpath}{os.pathsep}{existing}"
    env["PYTHONPATH"] = pythonpath
    if color_supported():
        env.setdefault("FORCE_COLOR", "1")
        env.setdefault("CLICOLOR_FORCE", "1")
        env.setdefault("PY_COLORS", "1")
    return env


def color_supported() -> bool:
    return sys.stderr.isatty() or bool(os.environ.get("FORCE_COLOR"))


def quiet_entry(entry: dict[str, Any]) -> dict[str, Any]:
    message = entry.get("message")
    if isinstance(message, str) and message:
        entry[QUIET_FAILURE_DETAIL] = message
    entry["message"] = ""
    entry.pop("lograil.status.detail", None)
    entry["lograil.status_only"] = True
    return entry


def print_failure_summary(processes: Sequence[Any]) -> None:
    failed = [process for process in processes if not process.success]
    if not failed:
        return
    print("\nFailures:", file=sys.stderr)
    for process in failed:
        spec = process.spec
        heading = spec.subject or spec.process or spec.name or "unknown"
        print(f"\n==> {heading}", file=sys.stderr)
        lines = failure_tail_lines(process.tail)
        if lines:
            for line in lines:
                print(line, file=sys.stderr)
        elif process.last_message:
            print(process.last_message, file=sys.stderr)
        else:
            print(f"exited with status {process.exit_code}", file=sys.stderr)


def failure_tail_lines(entries: Sequence[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for entry in entries:
        message = entry.get("message") or entry.get(QUIET_FAILURE_DETAIL)
        if not isinstance(message, str):
            continue
        message = message.rstrip()
        if not message or message in lines:
            continue
        if is_low_signal_failure_tail_line(message):
            continue
        lines.append(message)
    return lines[-FAILURE_TAIL_LINES:]


def is_low_signal_failure_tail_line(message: str) -> bool:
    stripped = message.strip()
    if not stripped:
        return True
    # Per-test success events from ggt's json output format.
    return stripped.startswith(("PASSED ", "SKIPPED ", "XFAIL "))


if __name__ == "__main__":
    raise SystemExit(main())
