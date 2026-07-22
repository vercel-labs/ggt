# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

import asyncio
import os
import pathlib

import pytest


def record(event):
    path = os.environ.get("GGT_FUNCTIONAL_EVENTS")
    if path:
        p = pathlib.Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(event + "\n")


@pytest.fixture
async def async_value():
    await asyncio.sleep(0)
    return 7


@pytest.fixture
async def async_gen_fixture():
    await asyncio.sleep(0)
    loop = asyncio.get_running_loop()
    yield ("agen", loop)
    # Teardown runs in the same event loop the fixture was created in.
    assert asyncio.get_running_loop() is loop
    record("agen-teardown")
