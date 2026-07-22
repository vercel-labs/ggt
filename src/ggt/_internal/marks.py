# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

"""Test marks and ``-m``/``--mark`` expression filtering.

Marks are free-form labels attached to tests with the ``ggt.mark``
decorator; the ``-m`` command line option selects tests by evaluating
a boolean expression over those labels.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable

MARKS_ATTR = "__ggt_marks__"

_TObj = TypeVar("_TObj")


class MarkError(Exception):
    pass


class _MarkFactory:
    """Attach mark names to a test function, method, or class.

    Marks are inert labels; their only effect is ``-m`` selection.
    The two spellings are equivalent::

        @ggt.mark.slow
        @ggt.mark("slow", "integration")

    Marks on a class apply to every test in it (subclasses
    included); class and method marks are combined.
    """

    def __call__(self, *names: str) -> Callable[[_TObj], _TObj]:
        for name in names:
            if not name.isidentifier():
                raise MarkError(
                    f"invalid mark name {name!r}: mark names must be "
                    f"valid Python identifiers"
                )

        def decorator(obj: _TObj) -> _TObj:
            merged = getattr(obj, MARKS_ATTR, frozenset()) | set(names)
            setattr(obj, MARKS_ATTR, merged)
            return obj

        return decorator

    def __getattr__(self, name: str) -> Callable[[_TObj], _TObj]:
        if name.startswith("_"):
            raise AttributeError(name)
        return self(name)


mark = _MarkFactory()


def get_marks(*objs: object) -> frozenset[str]:
    """The union of the mark names attached to *objs*.

    None entries are permitted and contribute nothing.
    """
    result: frozenset[str] = frozenset()
    for obj in objs:
        if obj is not None:
            result |= getattr(obj, MARKS_ATTR, frozenset())
    return result


_TOKEN_RE = re.compile(r"[()]|[^()\s]+")

_RESERVED = frozenset({"and", "or", "not", "(", ")"})


class _ExprParser:
    """Recursive-descent parser for ``-m`` expressions.

    Grammar (standard boolean precedence: ``not`` > ``and`` > ``or``)::

        expr : term (('and' | 'or') term)* | 'not' term | '(' expr ')'

    A term that is a plain identifier matches that mark name exactly;
    any other term is a regular expression, matched in full against
    each of the test's mark names.
    """

    def __init__(self, expr: str) -> None:
        self._expr = expr
        self._tokens: list[str] = _TOKEN_RE.findall(expr)
        self._pos = 0

    def _error(self, why: str) -> MarkError:
        return MarkError(f"invalid mark expression {self._expr!r}: {why}")

    def _peek(self) -> str | None:
        if self._pos < len(self._tokens):
            return self._tokens[self._pos]
        return None

    def _next(self) -> str | None:
        tok = self._peek()
        if tok is not None:
            self._pos += 1
        return tok

    def parse(self) -> Callable[[frozenset[str]], bool]:
        if not self._tokens:
            raise self._error("empty expression")
        matcher = self._or()
        if self._peek() is not None:
            raise self._error(f"unexpected {self._peek()!r}")
        return matcher

    def _or(self) -> Callable[[frozenset[str]], bool]:
        matchers = [self._and()]
        while self._peek() == "or":
            self._next()
            matchers.append(self._and())
        if len(matchers) == 1:
            return matchers[0]
        return lambda names: any(m(names) for m in matchers)

    def _and(self) -> Callable[[frozenset[str]], bool]:
        matchers = [self._not()]
        while self._peek() == "and":
            self._next()
            matchers.append(self._not())
        if len(matchers) == 1:
            return matchers[0]
        return lambda names: all(m(names) for m in matchers)

    def _not(self) -> Callable[[frozenset[str]], bool]:
        if self._peek() == "not":
            self._next()
            inner = self._not()
            return lambda names: not inner(names)
        return self._atom()

    def _atom(self) -> Callable[[frozenset[str]], bool]:
        tok = self._next()
        if tok == "(":
            matcher = self._or()
            if self._next() != ")":
                raise self._error("missing closing parenthesis")
            return matcher
        if tok is None or tok in _RESERVED:
            raise self._error(f"expected a mark pattern, got {tok!r}")
        if tok.isidentifier():
            name = tok
            return lambda names: name in names
        try:
            pattern = re.compile(tok)
        except re.error as e:
            raise self._error(f"bad mark pattern {tok!r}: {e}") from e
        return lambda names: any(map(pattern.fullmatch, names))


def compile_mark_expression(
    expr: str,
) -> Callable[[frozenset[str]], bool]:
    """Compile a ``-m`` expression into a mark-set predicate.

    Supports mark terms combined with ``and``, ``or``, ``not`` and
    parentheses.  Terms that are plain identifiers match a mark name
    exactly; any other term is treated as a regular expression and
    matched in full against each mark name.
    """
    return _ExprParser(expr).parse()
