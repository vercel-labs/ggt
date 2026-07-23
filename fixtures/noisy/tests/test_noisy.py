# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

import logging
import sys
import unittest

# A handler holding a direct reference to the stream, as libraries
# commonly configure at import time: only fd-level capture catches it.
_handler = logging.StreamHandler(sys.stderr)
_log = logging.getLogger("ggt-noisy")
_log.addHandler(_handler)
_log.setLevel(logging.INFO)


class NoisyTests(unittest.TestCase):
    def test_noisy_pass(self):
        print("NOISY-PASS-STDOUT-MARKER")
        print("NOISY-PASS-STDERR-MARKER", file=sys.stderr)
        _log.info("NOISY-PASS-LOG-MARKER")

    def test_noisy_fail(self):
        print("NOISY-FAIL-STDOUT-MARKER")
        print("NOISY-FAIL-STDERR-MARKER", file=sys.stderr)
        raise AssertionError("noisy failure")
