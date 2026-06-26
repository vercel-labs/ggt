# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.


from __future__ import annotations

import sys
from collections.abc import Callable
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
            console.secho(
                msg, file=self._stream, **({"nl": False} | kwargs)
            )

    def text(self, msg: str) -> None:
        if self._verbosity > 1:
            self._echo(msg)

    def info(self, msg: str) -> None:
        self._echo(msg, fg="white")

    def warning(self, msg: str) -> None:
        self._echo(msg, fg="yellow")

    def error(self, msg: str) -> None:
        self._echo(msg, fg="red")
