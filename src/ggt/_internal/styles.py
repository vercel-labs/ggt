# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.


from __future__ import annotations

import contextlib
import sys
from collections.abc import Callable, Iterator
from typing import Any, TextIO

from typing_extensions import TypeAliasType

from . import console

Style = TypeAliasType("Style", Callable[[str], str])


def marker_passed(t: str) -> str:
    return t


def marker_errored(t: str) -> str:
    return console.style(t, fg="red", bold=True, file=sys.stderr)


def marker_skipped(t: str) -> str:
    return console.style(t, fg="yellow", file=sys.stderr)


def marker_failed(t: str) -> str:
    return console.style(t, fg="red", bold=True, file=sys.stderr)


def marker_xfailed(t: str) -> str:
    return t


def marker_not_implemented(t: str) -> str:
    return t


def marker_upassed(t: str) -> str:
    return console.style(t, fg="yellow", file=sys.stderr)


def status(t: str) -> str:
    return console.style(t, fg="white", bold=True, file=sys.stderr)


def warning(t: str) -> str:
    return console.style(t, fg="yellow", file=sys.stderr)


class ConsoleUI:
    def __init__(
        self, verbosity: int = 1, stream: TextIO = sys.stderr
    ) -> None:
        self._verbosity = verbosity
        self._stream = stream

    def _echo(self, msg: str = "", **kwargs: Any) -> None:
        if self._verbosity > 0:
            kwargs.setdefault("nl", False)
            console.secho(msg, file=self._stream, **kwargs)

    def text(self, msg: str) -> None:
        if self._verbosity > 1:
            self._echo(msg)

    def info(self, msg: str) -> None:
        self._echo(msg, fg="white")

    @contextlib.contextmanager
    def stage(self, msg: str) -> Iterator[None]:
        """Show a status line for the duration of a run stage.

        On a terminal the line is transient: shown while the stage
        runs and erased once it completes.  Elsewhere (pipes, CI logs)
        the message is printed permanently, exactly as given.
        """
        if self._verbosity <= 0:
            yield
            return
        is_tty = getattr(self._stream, "isatty", lambda: False)()
        if not is_tty:
            self._echo(msg, fg="white")
            yield
            return
        shown = msg.strip("\n")
        self._echo(shown, fg="white")
        try:
            yield
        finally:
            # Erase just the status text (cursor left over its width,
            # then clear to end of line), so that a partial line
            # written before the stage is preserved.
            if shown:
                self._echo(f"\033[{len(shown)}D\033[K")

    def warning(self, msg: str) -> None:
        self._echo(msg, fg="yellow")

    def error(self, msg: str) -> None:
        self._echo(msg, fg="red")
