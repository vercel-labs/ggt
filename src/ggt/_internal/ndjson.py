# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

"""Machine-readable NDJSON output (``--output-format=json``).

Every run event is emitted as one JSON object per line on the *real*
stdout, shaped as a lograil log entry: ``message``/``levelname`` plus
the standard ``lograil.stage`` / ``lograil.stage.status`` and
``lograil.progress.*`` metadata keys, so ``ggt ... --output-format=json
| lograil --source=fd`` renders live progress bars.  Tool-specific
details ride along under ``ggt.*`` keys.
"""

from __future__ import annotations
from typing import TYPE_CHECKING, Any, TextIO

import contextlib
import io
import json
import os
import sys
import threading
import time

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

STAGE = "lograil.stage"
STAGE_STATUS = "lograil.stage.status"
PROGRESS_DESCRIPTION = "lograil.progress.description"
PROGRESS_COMPLETED = "lograil.progress.completed"
PROGRESS_TOTAL = "lograil.progress.total"
PROGRESS_PROCESS = "lograil.progress.process"
PROGRESS_SUBJECT = "lograil.progress.subject"
PROGRESS_CLEAR_LABEL = "lograil.progress.clear_label"
STATUS_ONLY = "lograil.status_only"

_PROCESS_NAME = "ggt"


class NDJSONEmitter:
    """Emit run events as NDJSON lines on a single stream.

    Events arrive concurrently from the main thread (stages, UI
    messages), the result-monitor thread (per-test reports), and the
    status thread (still-running heartbeats); the lock makes each line
    atomic and keeps the current-stage bookkeeping consistent.
    """

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream
        self._lock = threading.RLock()
        self._stage: str | None = None
        self._stage_total: int | None = None
        self._stage_completed = 0
        self._stage_description: str | None = None

    @classmethod
    def for_stdout(cls) -> NDJSONEmitter:
        """Create an emitter bound to the real stdout.

        The descriptor is duplicated up front because test output
        capture redirects fd 1 later (in-process for sequential runs);
        machine output must keep flowing to the original destination
        regardless.
        """
        try:
            stdout_fd = sys.stdout.fileno()
        except (AttributeError, OSError, io.UnsupportedOperation):
            return cls(sys.stdout)
        stream = os.fdopen(
            os.dup(stdout_fd),
            "w",
            buffering=1,
            encoding="utf-8",
            errors="replace",
            newline="\n",
        )
        return cls(stream)

    def close(self) -> None:
        with self._lock:
            if self._stream not in {sys.stdout, sys.__stdout__}:
                with contextlib.suppress(Exception):
                    self._stream.close()

    def emit(self, entry: Mapping[str, Any]) -> None:
        """Write one entry as a single NDJSON line."""
        line = json.dumps(entry, sort_keys=True, default=str)
        with self._lock:
            self._stream.write(line + "\n")
            self._stream.flush()

    def event(
        self,
        message: str | None = None,
        *,
        level: str = "INFO",
        stage: str | None = None,
        status: str | None = None,
        description: str | None = None,
        completed: int | None = None,
        total: int | None = None,
        clear_label: bool = False,
        status_only: bool = False,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        """Emit one run event.

        Progress metadata is attached when ``description`` is given (or
        implied by ``clear_label``); ``completed``/``total`` default to
        the current stage's values so mid-stage messages carry stable
        progress.  Every event names a stage — ``session`` outside any
        explicit one — so each line self-identifies as a lograil entry.
        """
        with self._lock:
            if completed is not None:
                self._stage_completed = completed
            if description is not None:
                self._stage_description = description
            stage_name = stage or self._stage or "session"
            entry: dict[str, Any] = {
                "name": _PROCESS_NAME,
                "created": time.time(),
                "levelname": level,
                STAGE: stage_name,
                STAGE_STATUS: status or "running",
            }
            if message is not None:
                entry["message"] = message
            if description is None and clear_label:
                description = self._stage_description or stage_name
            if description is not None:
                entry[PROGRESS_DESCRIPTION] = description
                entry[PROGRESS_COMPLETED] = (
                    completed
                    if completed is not None
                    else self._stage_completed
                )
                effective_total = (
                    total if total is not None else self._stage_total
                )
                if effective_total is not None:
                    entry[PROGRESS_TOTAL] = effective_total
                entry[PROGRESS_PROCESS] = _PROCESS_NAME
                entry[PROGRESS_SUBJECT] = stage_name
            if clear_label:
                entry[PROGRESS_CLEAR_LABEL] = True
            if status_only:
                entry[STATUS_ONLY] = True
            if extra:
                entry.update(extra)
            self.emit(entry)

    @contextlib.contextmanager
    def stage(
        self,
        name: str,
        *,
        total: int | None = None,
        message: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> Iterator[None]:
        """Delimit a run stage with started/finished (or failed) events.

        The final event sets ``lograil.progress.clear_label`` so
        consumers tear the stage's progress bar down before the next
        stage begins.
        """
        with self._lock:
            self._stage = name
            self._stage_total = total
            self._stage_completed = 0
            self._stage_description = None
        self.event(
            message or f"{name} started",
            status="started",
            description=message or name,
            completed=0,
            extra=extra,
        )
        try:
            yield
        except BaseException:
            self.event(
                f"{name} failed",
                level="ERROR",
                status="failed",
                clear_label=True,
            )
            raise
        else:
            self.event(status="finished", clear_label=True)
        finally:
            with self._lock:
                self._stage = None
                self._stage_total = None
                self._stage_completed = 0
                self._stage_description = None


class JSONUI:
    """A :class:`ggt._internal.loader.UI` that emits NDJSON events.

    Replaces :class:`ggt._internal.styles.ConsoleUI` in json mode so
    fixture setup/teardown messages become structured events instead of
    styled stderr text.  Whitespace-only cosmetic messages are dropped.
    """

    def __init__(self, emitter: NDJSONEmitter) -> None:
        self._emitter = emitter

    def text(self, msg: str) -> None:
        self._event(msg)

    def info(self, msg: str) -> None:
        self._event(msg)

    def warning(self, msg: str) -> None:
        self._event(msg, level="WARNING")

    def error(self, msg: str) -> None:
        self._event(msg, level="ERROR")

    @contextlib.contextmanager
    def stage(self, msg: str) -> Iterator[None]:
        stripped = msg.strip()
        if stripped:
            self._emitter.event(stripped, description=stripped)
        yield

    def _event(self, msg: str, *, level: str = "INFO") -> None:
        stripped = msg.strip()
        if stripped:
            self._emitter.event(stripped, level=level)
