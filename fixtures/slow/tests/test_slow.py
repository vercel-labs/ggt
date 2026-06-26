# mypy: ignore-errors

import time
import unittest

import time_machine

from ggt._internal import runner as ggt_runner


_traveller = time_machine.travel(0, tick=False).start()
_start_test = ggt_runner.ParallelTextTestResult.startTest

# The runner measures long-running tests with time.monotonic(). time-machine
# controls wall-clock time, so make this fixture's runner process measure from
# the faked clock while still exercising the production reporting path.
ggt_runner.time.monotonic = time.time


def deterministic_start_test(self, test):
    _start_test(self, test)
    _traveller.shift(6)
    self.report_still_running()


ggt_runner.ParallelTextTestResult.startTest = deterministic_start_test


class SlowCase(unittest.TestCase):
    def test_slow_enough_for_status(self):
        time.sleep(0.2)
