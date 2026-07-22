# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

"""Interpretation of pytest marks.

Duck-types the ``Mark`` objects pytest attaches (``pytestmark``
attributes on modules, classes and functions), so pytest is never
imported here.  Supported marks: ``skip``, ``skipif``, ``xfail``,
``parametrize`` and ``usefixtures``.  Unknown (custom) marks are
ignored, matching pytest's default behavior.
"""

from __future__ import annotations

import dataclasses
import itertools
import os
import platform
import re
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import types
    from collections.abc import Callable, Sequence


class MarkError(Exception):
    pass


def _as_mark(obj: object) -> Any | None:
    # A MarkDecorator carries the Mark in its .mark attribute.
    mark = getattr(obj, "mark", None)
    if mark is not None:
        obj = mark
    if (
        isinstance(getattr(obj, "name", None), str)
        and hasattr(obj, "args")
        and hasattr(obj, "kwargs")
    ):
        return obj
    return None


def _marks_of(holder: object) -> list[Any]:
    pytestmark = getattr(holder, "pytestmark", None)
    if pytestmark is None:
        return []
    if not isinstance(pytestmark, (list, tuple)):
        pytestmark = [pytestmark]
    result = []
    for item in pytestmark:
        mark = _as_mark(item)
        if mark is not None:
            result.append(mark)
    return result


def effective_marks(
    mod: types.ModuleType,
    cls: type | None,
    func: Callable[..., object],
) -> list[Any]:
    """Marks that apply to *func*: module-level, then class, then own.

    Note: for functions/methods ``pytestmark`` holds the function's
    own marks (bottom-most decorator first).
    """
    result: list[Any] = _marks_of(mod)
    if cls is not None:
        result.extend(_marks_of(cls))
    result.extend(_marks_of(func))
    return result


def _evaluate_condition(condition: object, mod: types.ModuleType) -> bool:
    if isinstance(condition, str):
        namespace: dict[str, object] = {
            "os": os,
            "sys": sys,
            "platform": platform,
            **vars(mod),
        }
        return bool(eval(condition, namespace))  # noqa: S307
    return bool(condition)


def skip_reason(marks: Sequence[Any], mod: types.ModuleType) -> str | None:
    """The skip reason if any skip/skipif mark is active, else None."""
    for mark in marks:
        if mark.name == "skip":
            reason = mark.kwargs.get("reason")
            if reason is None and mark.args:
                reason = mark.args[0]
            return str(reason) if reason is not None else "unconditional skip"
        elif mark.name == "skipif":
            conditions = mark.args or (True,)
            if any(_evaluate_condition(c, mod) for c in conditions):
                reason = mark.kwargs.get("reason")
                return (
                    str(reason)
                    if reason is not None
                    else f"condition: {conditions!r}"
                )
    return None


@dataclasses.dataclass(frozen=True)
class XFailInfo:
    reason: str
    strict: bool
    raises: tuple[type[BaseException], ...] | None


def xfail_info(
    marks: Sequence[Any],
    mod: types.ModuleType,
) -> XFailInfo | None:
    for mark in marks:
        if mark.name != "xfail":
            continue

        conditions = list(mark.args)
        if "condition" in mark.kwargs:
            conditions.append(mark.kwargs["condition"])
        if conditions and not any(
            _evaluate_condition(c, mod) for c in conditions
        ):
            continue

        raises = mark.kwargs.get("raises")
        raises_tuple: tuple[type[BaseException], ...] | None
        if raises is None:
            raises_tuple = None
        elif isinstance(raises, tuple):
            raises_tuple = raises
        else:
            raises_tuple = (raises,)

        reason = mark.kwargs.get("reason")
        return XFailInfo(
            reason=str(reason) if reason is not None else "expected failure",
            strict=bool(mark.kwargs.get("strict", False)),
            raises=raises_tuple,
        )
    return None


def usefixtures_names(marks: Sequence[Any]) -> tuple[str, ...]:
    names: list[str] = []
    for mark in marks:
        if mark.name == "usefixtures":
            names.extend(str(arg) for arg in mark.args)
    return tuple(names)


@dataclasses.dataclass(frozen=True)
class ParamCase:
    pretty_id: str
    values: dict[str, object]
    marks: tuple[Any, ...]


def _is_parameter_set(value: object) -> bool:
    return (
        type(value).__name__ == "ParameterSet"
        and hasattr(value, "values")
        and hasattr(value, "marks")
        and hasattr(value, "id")
    )


def _value_id(value: object, argname: str, index: int) -> str:
    if isinstance(value, bool) or value is None:
        return str(value)
    if isinstance(value, (int, float, complex)):
        return str(value)
    if isinstance(value, str):
        if value and all(c.isprintable() and not c.isspace() for c in value):
            return value
        return f"{argname}{index}"
    if isinstance(value, bytes):
        try:
            decoded = value.decode("ascii")
        except UnicodeDecodeError:
            return f"{argname}{index}"
        if decoded and all(
            c.isprintable() and not c.isspace() for c in decoded
        ):
            return decoded
        return f"{argname}{index}"
    return f"{argname}{index}"


def _parse_argnames(argnames: object) -> list[str]:
    if isinstance(argnames, str):
        return [name.strip() for name in argnames.split(",") if name.strip()]
    if isinstance(argnames, (list, tuple)):
        return [str(name) for name in argnames]
    raise MarkError(f"invalid parametrize argnames: {argnames!r}")


def _expand_one_mark(
    mark: Any,
    func_name: str,
) -> list[ParamCase]:
    if mark.kwargs.get("indirect"):
        raise MarkError(
            f"indirect parametrization is not supported by ggt pytest "
            f"compatibility: {func_name}"
        )

    if len(mark.args) < 2:
        raise MarkError(
            f"parametrize needs argnames and argvalues: {func_name}"
        )

    argnames = _parse_argnames(mark.args[0])
    argvalues = mark.args[1]
    ids = mark.kwargs.get("ids")

    cases: list[ParamCase] = []
    for index, item in enumerate(argvalues):
        param_marks: tuple[Any, ...] = ()
        param_id: str | None = None

        if _is_parameter_set(item):
            values = tuple(item.values)
            param_marks = tuple(
                m for m in (_as_mark(mm) for mm in item.marks) if m is not None
            )
            param_id = item.id
        elif len(argnames) == 1:
            values = (item,)
        else:
            values = tuple(item)

        if len(values) != len(argnames):
            raise MarkError(
                f"parametrize value #{index} of {func_name} has "
                f"{len(values)} elements, expected {len(argnames)}"
            )

        if param_id is None and ids is not None:
            candidate: object = None
            if callable(ids):
                candidate = ids(values[0] if len(values) == 1 else values)
            else:
                try:
                    candidate = ids[index]
                except IndexError:
                    candidate = None
            if candidate is not None:
                param_id = str(candidate)

        if param_id is None:
            param_id = "-".join(
                _value_id(value, argname, index)
                for argname, value in zip(argnames, values, strict=True)
            )

        cases.append(
            ParamCase(
                pretty_id=param_id,
                values=dict(zip(argnames, values, strict=True)),
                marks=param_marks,
            )
        )

    return cases


def parametrize_cases(
    marks: Sequence[Any],
    func_name: str,
) -> list[ParamCase] | None:
    """Expand (possibly stacked) parametrize marks into cases.

    Stacked marks produce a cross product; the mark listed first
    (the bottom-most decorator) varies slowest and its id component
    comes first, matching pytest's ordering.
    """
    pmarks = [m for m in marks if m.name == "parametrize"]
    if not pmarks:
        return None

    per_mark = [_expand_one_mark(mark, func_name) for mark in pmarks]

    combined: list[ParamCase] = []
    for combo in itertools.product(*per_mark):
        values: dict[str, object] = {}
        combo_marks: list[Any] = []
        for case in combo:
            values.update(case.values)
            combo_marks.extend(case.marks)
        pretty = "-".join(case.pretty_id for case in combo)
        combined.append(
            ParamCase(
                pretty_id=pretty,
                values=values,
                marks=tuple(combo_marks),
            )
        )

    return combined


def sanitize_identifier(text: str) -> str:
    return re.sub(r"\W", "_", text)


# Public aliases for use by the synthesis machinery.
def value_id(value: object, argname: str, index: int) -> str:
    return _value_id(value, argname, index)


def as_mark(obj: object) -> Any | None:
    return _as_mark(obj)


def is_parameter_set(value: object) -> bool:
    return _is_parameter_set(value)
