# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.


from __future__ import annotations

import os
import sys
from typing import Any, TextIO

_ANSI_COLORS = {
    "black": 30,
    "red": 31,
    "green": 32,
    "yellow": 33,
    "blue": 34,
    "magenta": 35,
    "cyan": 36,
    "white": 37,
}


def _should_color(file: TextIO) -> bool:
    return (
        os.environ.get("NO_COLOR") is None
        and os.environ.get("TERM") != "dumb"
        and hasattr(file, "isatty")
        and file.isatty()
    )


def style(
    text: str,
    *,
    fg: str | None = None,
    bold: bool = False,
    color: bool | None = None,
    file: TextIO | None = None,
    **_: Any,
) -> str:
    file = file or sys.stdout
    if color is None:
        color = _should_color(file)
    if not color:
        return text

    attrs: list[str] = []
    if bold:
        attrs.append("1")
    if fg is not None and fg in _ANSI_COLORS:
        attrs.append(str(_ANSI_COLORS[fg]))

    if not attrs:
        return text

    return f"\033[{';'.join(attrs)}m{text}\033[0m"


def echo(
    message: Any = "",
    *,
    file: TextIO | None = None,
    nl: bool = True,
    err: bool = False,
    **_: Any,
) -> None:
    if file is None:
        file = sys.stderr if err else sys.stdout

    end = "\n" if nl else ""
    print("" if message is None else message, file=file, end=end)


def secho(
    message: Any = "",
    *,
    file: TextIO | None = None,
    nl: bool = True,
    err: bool = False,
    fg: str | None = None,
    bold: bool = False,
    **kwargs: Any,
) -> None:
    if file is None:
        file = sys.stderr if err else sys.stdout

    echo(
        style(
            "" if message is None else str(message),
            fg=fg,
            bold=bold,
            file=file,
            **kwargs,
        ),
        file=file,
        nl=nl,
    )
