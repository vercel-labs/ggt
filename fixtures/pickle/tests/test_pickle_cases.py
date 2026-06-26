# mypy: ignore-errors

import unittest


class NewArgsCase(unittest.TestCase):
    def __getnewargs__(self):
        return ()

    def test_uses_getnewargs(self):
        self.assertEqual(self._testMethodName, "test_uses_getnewargs")


class NewArgsExCase(unittest.TestCase):
    def __getnewargs_ex__(self):
        return (), {}

    def test_uses_getnewargs_ex(self):
        self.assertEqual(self._testMethodName, "test_uses_getnewargs_ex")


class StateCase(unittest.TestCase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.unpickleable = lambda: None
        self.keep = "kept"

    def __getstate__(self):
        state = self.__dict__.copy()
        state["unpickleable"] = getattr(self, "unpickleable", lambda: None)
        return state

    def test_unpickleable_state_is_dropped(self):
        self.assertEqual(self.keep, "kept")


class SetStateCase(unittest.TestCase):
    def __getstate__(self):
        state = self.__dict__.copy()
        state["restored"] = "yes"
        return state

    def __setstate__(self, state):
        self.__dict__.update(state)

    def test_setstate_runs(self):
        self.assertEqual(self.restored, "yes")


class AsyncPickleCase(unittest.IsolatedAsyncioTestCase):
    async def test_async_case_restores_context(self):
        self.assertTrue(True)
