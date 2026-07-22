# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

_state = {"module": False}


def setup_module(module):
    _state["module"] = True


def teardown_module():
    # The zero-argument flavor of the hook.
    _state["module"] = False


def test_module_hook_ran():
    assert _state["module"]


class TestLifecycle:
    class_ready = False

    @classmethod
    def setup_class(cls):
        cls.class_ready = True

    @classmethod
    def teardown_class(cls):
        cls.class_ready = False

    def setup_method(self, method):
        self.prepared = method.__name__

    def teardown_method(self):
        # The zero-argument flavor of the hook.
        self.prepared = None

    def test_lifecycle_one(self):
        assert self.class_ready
        assert self.prepared == "test_lifecycle_one"

    def test_lifecycle_two(self):
        assert self.class_ready
        assert self.prepared == "test_lifecycle_two"
