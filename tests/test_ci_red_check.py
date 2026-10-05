"""Throwaway: proves the CI check goes red when a test fails. Never merge this file (SIC-81)."""
import unittest


class CiRedCheck(unittest.TestCase):
    def test_this_fails_on_purpose(self):
        self.fail("deliberate failure to prove that the required check blocks a red pull request")
