# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

"""Translation of pytest's imperative outcomes to unittest outcomes."""

from __future__ import annotations

import unittest
from typing import Never


def translate(e: BaseException) -> BaseException | None:
    """Translate a duck-typed pytest OutcomeException, if applicable."""
    cls = type(e)
    if not (hasattr(e, "msg") and hasattr(e, "pytrace")):
        return None
    if cls.__name__ == "Skipped":
        return unittest.SkipTest(str(e) or "skipped")
    if cls.__name__ == "Failed":
        return AssertionError(str(e) or "failed")
    return None


def raise_translated(e: BaseException) -> Never:
    translated = translate(e)
    if translated is not None:
        raise translated from e
    raise e
