# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.


from __future__ import annotations
from typing import ClassVar

import ctypes


class _Py_HashSecret_t(ctypes.Union):
    _fields_: ClassVar = [
        ("uc", ctypes.c_ubyte * 24),
    ]


def get_py_hash_secret() -> bytes:
    hashsecret = _Py_HashSecret_t.in_dll(ctypes.pythonapi, "_Py_HashSecret")
    return bytes(hashsecret.uc)
