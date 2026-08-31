# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

"""A supported subset of pytest's ini-file options.

Reads ``[tool.pytest.ini_options]`` from ``pyproject.toml`` or the
``[pytest]`` section of ``pytest.ini``, walking up from the current
directory.  Collection-affecting options are honored:
``python_files``, ``python_classes``, ``python_functions``,
``testpaths``, ``pythonpath`` and ``usefixtures``.  ``addopts`` is also
exposed to the CLI so that ggt-compatible command-line defaults can be
configured.
Everything else is ignored.

The parent process loads the config once and exports it through the
``GGT_PYTEST_INI`` environment variable so that workers — which re-run
collection during test restore — apply identical patterns.
"""

from __future__ import annotations

import configparser
import dataclasses
import functools
import importlib
import json
import os
import pathlib
import shlex
import sys
import tomllib

ENV_KEY = "GGT_PYTEST_INI"

_SUPPORTED = (
    "python_files",
    "python_classes",
    "python_functions",
    "testpaths",
    "pythonpath",
    "usefixtures",
    "asyncio_default_test_loop_scope",
    "asyncio_default_fixture_loop_scope",
)


@dataclasses.dataclass(frozen=True)
class IniConfig:
    python_files: tuple[str, ...] = ()
    python_classes: tuple[str, ...] = ()
    python_functions: tuple[str, ...] = ()
    testpaths: tuple[str, ...] = ()
    pythonpath: tuple[str, ...] = ()
    usefixtures: tuple[str, ...] = ()
    asyncio_default_test_loop_scope: str = ""
    asyncio_default_fixture_loop_scope: str = ""
    addopts: tuple[str, ...] = ()
    # The directory containing the configuration file (or the initial
    # lookup directory when none was found).  Bounds the conftest.py
    # search, like pytest's rootdir.
    rootdir: str = ""

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            key: list(value)
            for key in _SUPPORTED
            if key
            not in {
                "asyncio_default_test_loop_scope",
                "asyncio_default_fixture_loop_scope",
            }
            if (value := getattr(self, key))
        }
        for key in (
            "asyncio_default_test_loop_scope",
            "asyncio_default_fixture_loop_scope",
        ):
            if value := getattr(self, key):
                result[key] = value
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


def _translate_addopts(args: tuple[str, ...]) -> tuple[str, ...]:
    """Translate pytest presentation options to ggt equivalents."""
    result: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--no-header":
            # ggt has no pytest-style header to suppress.
            index += 1
            continue

        capture_mode: str | None = None
        if arg.startswith("--capture="):
            capture_mode = arg.partition("=")[2]
        elif arg == "--capture" and index + 1 < len(args):
            capture_mode = args[index + 1]
            index += 1

        if capture_mode is not None:
            if capture_mode in {"fd", "sys", "tee-sys"}:
                result.append("--capture")
            elif capture_mode == "no":
                result.append("--no-capture")
            else:
                raise ValueError(
                    f"unsupported pytest capture mode: {capture_mode!r}"
                )
            index += 1
            continue

        result.append(arg)
        index += 1

    return tuple(result)


@functools.cache
def _pytest_option_nargs() -> dict[str, object]:
    """Argument counts for pytest core and installed-plugin options."""
    try:
        from _pytest.config import get_config  # noqa: PLC0415, PLC2701

        config = get_config()
        config.pluginmanager.load_setuptools_entrypoints("pytest11")
    except Exception:
        return {}

    result: dict[str, object] = {}
    for group in config._parser._groups:
        for option in group.options:
            attrs = option.attrs()
            for name in option.names():
                result[name] = attrs.get("nargs")
    return result


def filter_addopts(
    args: tuple[str, ...],
    *,
    supported_options: set[str],
) -> tuple[str, ...]:
    """Drop pytest options that ggt's command line does not support."""
    pytest_nargs = _pytest_option_nargs()
    result: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        option = arg.partition("=")[0] if arg.startswith("-") else None
        if option is None or option in supported_options:
            result.append(arg)
            index += 1
            continue

        nargs = pytest_nargs.get(option, 0)
        index += 1
        if "=" in arg or nargs == 0:
            continue
        if nargs is None or nargs == "?":
            if index < len(args) and not args[index].startswith("-"):
                index += 1
        elif isinstance(nargs, int):
            index = min(index + nargs, len(args))
        elif nargs in {"+", "*"}:
            while index < len(args) and not args[index].startswith("-"):
                index += 1

    return tuple(result)


def _normalize_addopts(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return _translate_addopts(tuple(shlex.split(value)))
    if isinstance(value, (list, tuple)):
        return _translate_addopts(tuple(str(item) for item in value))
    return ()


def _from_mapping(data: dict[str, object]) -> IniConfig:
    rootdir = data.get("rootdir")
    return IniConfig(
        rootdir=str(rootdir) if isinstance(rootdir, str) else "",
        addopts=_normalize_addopts(data.get("addopts")),
        asyncio_default_test_loop_scope=str(
            data.get("asyncio_default_test_loop_scope") or ""
        ),
        asyncio_default_fixture_loop_scope=str(
            data.get("asyncio_default_fixture_loop_scope") or ""
        ),
        **{
            key: _normalize(data.get(key))
            for key in _SUPPORTED
            if key
            not in {
                "asyncio_default_test_loop_scope",
                "asyncio_default_fixture_loop_scope",
            }
        },
    )


def _with_rootdir(config: IniConfig, rootdir: pathlib.Path) -> IniConfig:
    """Resolve path-valued options relative to the configuration file."""
    return dataclasses.replace(
        config,
        rootdir=str(rootdir),
        pythonpath=tuple(
            str((rootdir / entry).resolve()) for entry in config.pythonpath
        ),
    )


def apply_pythonpath(config: IniConfig) -> None:
    """Prepend configured import paths with pytest-compatible ordering."""
    for entry in reversed(config.pythonpath):
        while entry in sys.path:
            sys.path.remove(entry)
        sys.path.insert(0, entry)
    if config.pythonpath:
        importlib.invalidate_caches()


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
                return _with_rootdir(
                    _from_mapping(dict(parser.items("pytest"))), directory
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
                return _with_rootdir(_from_mapping(ini), directory)

    return IniConfig(rootdir=str(start))


def initialize(start: pathlib.Path) -> IniConfig:
    """Load the config and export it to workers (parent process)."""
    global _current  # noqa: PLW0603
    _current = load_from(start)
    apply_pythonpath(_current)
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
