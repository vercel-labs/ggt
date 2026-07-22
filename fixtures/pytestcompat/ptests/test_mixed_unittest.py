# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

import unittest


class MixedUnittest(unittest.TestCase):
    def test_plain_unittest(self):
        self.assertEqual(2 * 2, 4)
