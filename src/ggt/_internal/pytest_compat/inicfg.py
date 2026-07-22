# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

"""A supported subset of pytest's ini-file options.

Reads ``[tool.pytest.ini_options]`` from ``pyproject.toml`` or the
``[pytest]`` section of ``pytest.ini``, walking up from the current
directory.  Only collection-affecting options are honored:
``python_files``, ``python_classes``, ``python_functions``,
``testpaths`` and ``usefixtures``; everything else (notably
``addopts``) is ignored.

The parent process loads the config once and exports it through the
``GGT_PYTEST_INI`` environment variable so that workers — which re-run
collection during test restore — apply identical patterns.
"""

from __future__ import annotations

import configparser
import dataclasses
import json
import os
import pathlib
import tomllib

ENV_KEY = "GGT_PYTEST_INI"

_SUPPORTED = (
    "python_files",
    "python_classes",
    "python_functions",
    "testpaths",
    "usefixtures",
)


@dataclasses.dataclass(frozen=True)
class IniConfig:
    python_files: tuple[str, ...] = ()
    python_classes: tuple[str, ...] = ()
    python_functions: tuple[str, ...] = ()
    testpaths: tuple[str, ...] = ()
    usefixtures: tuple[str, ...] = ()
    # The directory containing the configuration file (or the initial
    # lookup directory when none was found).  Bounds the conftest.py
    # search, like pytest's rootdir.
    rootdir: str = ""

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            key: list(value)
            for key in _SUPPORTED
            if (value := getattr(self, key))
        }
        if self.rootdir:
            result["rootdir"] = self.rootdir
        return result


_current: IniConfig | None = None


def _normalize(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(value.split())
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return ()


def _from_mapping(data: dict[str, object]) -> IniConfig:
    rootdir = data.get("rootdir")
    return IniConfig(
        rootdir=str(rootdir) if isinstance(rootdir, str) else "",
        **{key: _normalize(data.get(key)) for key in _SUPPORTED},
    )


def load_from(start: pathlib.Path) -> IniConfig:
    """Locate and parse the pytest configuration for *start*.

    Mirrors pytest's precedence: a ``pytest.ini`` with a ``[pytest]``
    section wins; otherwise the first ``pyproject.toml`` containing
    ``[tool.pytest.ini_options]``.
    """
    start = start.resolve()
    for directory in [start, *start.parents]:
        pytest_ini = directory / "pytest.ini"
        if pytest_ini.is_file():
            parser = configparser.ConfigParser()
            try:
                parser.read(pytest_ini, encoding="utf-8")
            except configparser.Error:
                return IniConfig(rootdir=str(directory))
            if parser.has_section("pytest"):
                return dataclasses.replace(
                    _from_mapping(dict(parser.items("pytest"))),
                    rootdir=str(directory),
                )
            return IniConfig(rootdir=str(directory))

        pyproject = directory / "pyproject.toml"
        if pyproject.is_file():
            try:
                with open(pyproject, "rb") as f:
                    data = tomllib.load(f)
            except (OSError, tomllib.TOMLDecodeError):
                continue
            ini = data.get("tool", {}).get("pytest", {}).get("ini_options")
            if isinstance(ini, dict):
                return dataclasses.replace(
                    _from_mapping(ini), rootdir=str(directory)
                )

    return IniConfig(rootdir=str(start))


def initialize(start: pathlib.Path) -> IniConfig:
    """Load the config and export it to workers (parent process)."""
    global _current  # noqa: PLW0603
    _current = load_from(start)
    os.environ[ENV_KEY] = json.dumps(_current.as_dict())
    return _current


def current() -> IniConfig:
    global _current  # noqa: PLW0603
    if _current is None:
        raw = os.environ.get(ENV_KEY)
        if raw is not None:
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                _current = _from_mapping(parsed)
            else:
                _current = IniConfig()
        else:
            _current = load_from(pathlib.Path.cwd())
    return _current
