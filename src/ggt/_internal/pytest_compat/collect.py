# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

"""Scanning of imported modules for pytest-style test items.

Produces a deterministic :class:`ModulePlan` describing what
``synth.synthesize_module`` should build.  The scan must be a pure
function of the module contents: workers re-import test modules and
re-run the scan, and both processes must arrive at identical TestCase
classes and method names.
"""

from __future__ import annotations

import dataclasses
import fnmatch
import inspect
import unittest
import warnings
from typing import TYPE_CHECKING

from . import inicfg

if TYPE_CHECKING:
    import types
    from collections.abc import Sequence

TEST_FUNCTION_PREFIX = "test_"
TEST_CLASS_PREFIX = "Test"

# Default test file patterns: unittest's test*.py convention (a
# superset of pytest's test_*.py) plus pytest's *_test.py.  Overridden
# by the python_files ini option.
TEST_FILE_PATTERNS = ("test*.py", "*_test.py")


def is_test_file(name: str) -> bool:
    patterns = inicfg.current().python_files or TEST_FILE_PATTERNS
    return any(fnmatch.fnmatch(name, pat) for pat in patterns)


def _name_matches(
    name: str,
    patterns: Sequence[str],
    default_prefix: str,
) -> bool:
    """pytest-style name matching: prefixes, or globs when present."""
    if not patterns:
        return name.startswith(default_prefix)
    for pattern in patterns:
        if any(ch in pattern for ch in "*?["):
            if fnmatch.fnmatch(name, pattern):
                return True
        elif name.startswith(pattern):
            return True
    return False


def is_test_function_name(name: str) -> bool:
    return _name_matches(
        name, inicfg.current().python_functions, TEST_FUNCTION_PREFIX
    )


def is_test_class_name(name: str) -> bool:
    return _name_matches(
        name, inicfg.current().python_classes, TEST_CLASS_PREFIX
    )


# Marker attribute set on synthesized modules and TestCase classes.
MODULE_MARKER = "__ggt_pytest_synthesized__"
CLASS_MARKER = "__ggt_pytest_synthesized__"


@dataclasses.dataclass(frozen=True)
class ClassPlan:
    name: str
    cls: type
    method_names: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class ModulePlan:
    function_names: tuple[str, ...]
    classes: tuple[ClassPlan, ...]

    @property
    def empty(self) -> bool:
        return not self.function_names and not self.classes


def _is_collectable(obj: object) -> bool:
    return bool(getattr(obj, "__test__", True))


def _is_test_function(name: str, obj: object, modname: str) -> bool:
    if not is_test_function_name(name):
        return False
    if not inspect.isfunction(obj):
        return False
    if getattr(obj, "__module__", None) != modname:
        # Skip functions imported from other modules to avoid
        # collecting the same test more than once.
        return False
    return _is_collectable(obj)


def _is_test_class(name: str, obj: object, modname: str) -> bool:
    if not is_test_class_name(name):
        return False
    if not inspect.isclass(obj):
        return False
    if issubclass(obj, unittest.TestCase):
        # Real unittest classes are collected by the stock loader.
        return False
    if getattr(obj, "__module__", None) != modname:
        return False
    if not _is_collectable(obj):
        return False
    if getattr(obj, CLASS_MARKER, False):
        return False
    # Use getattr_static for identity checks: it returns the raw
    # __dict__ entries, making the comparison against object's own
    # slots reliable (and keeping mypy happy about overloaded slots).
    if inspect.getattr_static(obj, "__init__", None) is not (
        inspect.getattr_static(object, "__init__", None)
    ) or inspect.getattr_static(obj, "__new__", None) is not (
        inspect.getattr_static(object, "__new__", None)
    ):
        warnings.warn(
            f"cannot collect test class {modname}.{name} because it "
            f"has a custom __init__ or __new__ constructor",
            stacklevel=3,
        )
        return False
    return True


def _scan_class(cls: type) -> tuple[str, ...]:
    methods: list[str] = []
    for name in dir(cls):
        if not is_test_function_name(name):
            continue
        obj = inspect.getattr_static(cls, name, None)
        if not inspect.isfunction(obj):
            continue
        methods.append(name)
    return tuple(methods)


def scan_module(mod: types.ModuleType) -> ModulePlan:
    """Find pytest-style test items in an imported module."""
    modname = mod.__name__
    functions: list[str] = []
    classes: list[ClassPlan] = []

    # vars() preserves definition order, which is identical for the
    # same source file across processes.
    for name, obj in [*vars(mod).items()]:
        if _is_test_function(name, obj, modname):
            functions.append(name)
        elif _is_test_class(name, obj, modname):
            method_names = _scan_class(obj)
            if method_names:
                classes.append(
                    ClassPlan(name=name, cls=obj, method_names=method_names)
                )

    return ModulePlan(
        function_names=tuple(functions),
        classes=tuple(classes),
    )
