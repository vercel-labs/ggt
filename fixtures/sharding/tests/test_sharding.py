# mypy: ignore-errors

import unittest


class SetupShardA(unittest.TestCase):
    @classmethod
    def get_setup_script(cls):
        return "setup-a"

    @classmethod
    def get_database_name(cls):
        return "db-a"

    def test_a_1(self):
        pass

    def test_a_2(self):
        pass

    def test_a_3(self):
        pass

    def test_zREPEAT_a_1(self):
        pass


class SetupShardB(unittest.TestCase):
    @classmethod
    def get_setup_script(cls):
        return "setup-b"

    def test_b_1(self):
        pass

    def test_b_2(self):
        pass

    def test_b_3(self):
        pass

    def test_b_4(self):
        pass


class PlainShard(unittest.TestCase):
    def test_plain_1(self):
        pass

    def test_plain_2(self):
        pass

    def test_plain_3(self):
        pass

    def test_plain_4(self):
        pass

    def test_plain_5(self):
        pass
