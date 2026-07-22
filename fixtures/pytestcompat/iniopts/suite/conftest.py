# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

import pytest


@pytest.fixture
def global_marker(monkeypatch):
    # Applied to every test via the usefixtures ini option.
    monkeypatch.setenv("GGT_INI_USEFIXTURES", "on")
