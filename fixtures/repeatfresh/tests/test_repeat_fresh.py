import os
import pathlib
import unittest


COLLECTIONS = pathlib.Path(os.environ["GGT_COLLECTION_COUNT"])
COLLECTIONS.write_text(
    str(int(COLLECTIONS.read_text(encoding="utf-8") or "0") + 1),
    encoding="utf-8",
)


class FreshAsyncCase(unittest.IsolatedAsyncioTestCase):
    def __init__(self, methodName="runTest"):
        super().__init__(methodName)
        self.pristine = True

    async def test_fresh_instance(self):
        self.assertTrue(self.pristine)
        self.pristine = False
