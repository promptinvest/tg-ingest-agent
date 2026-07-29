#!/usr/bin/env python3
"""Dedicated landing file for quarantined Mentor candidate regression tests.

The deployed file intentionally contains only this sentinel. Mentor candidates
may modify it inside an isolated source copy; they never modify the installed
copy, merge themselves, push, or deploy.
"""
import unittest


class MentorCandidateSentinelTests(unittest.TestCase):
    def test_candidate_test_surface_is_explicit(self):
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
