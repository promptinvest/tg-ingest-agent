"""Partial scope cannot count failed conversation or omit a changed role."""
from copy import deepcopy
import unittest
import trial_operator as operator


class ScopeTests(unittest.TestCase):
    def setUp(self):
        hashes = {'converse_warm': 'a' * 64}
        self.initial = {'validation': {'unchanged_profiles': ['converse_warm'],
            'changed_active_profiles': operator.PARTIAL_PROFILES,
            'unchanged_profile_sha256s': hashes, 'profile_control_exceptions': {}}}
        self.proof = {'qualification_scope': 'changed_profiles_only',
            'functional_cases_passed': 21, 'functional_cases_total': 21,
            'case_ids': sorted(operator.PARTIAL_CASE_IDS),
            'changed_active_profiles': operator.PARTIAL_PROFILES,
            'unchanged_profile_sha256s': hashes}

    def test_all_changed_cases_with_bound_unchanged_conversation_pass(self):
        operator.qualification_coverage(self.initial, self.proof)

    def test_wrong_counts_missing_or_duplicate_cases_fail(self):
        for change in ({'functional_cases_passed': 20}, {'functional_cases_total': 24},
                       {'case_ids': self.proof['case_ids'][:-1]},
                       {'case_ids': self.proof['case_ids'][:-1] + [self.proof['case_ids'][0]]}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                operator.qualification_coverage(self.initial, dict(self.proof, **change))

    def test_failed_conversation_cannot_replace_changed_role_case(self):
        ids = self.proof['case_ids'][:-1] + ['assistant-current-work-en']
        with self.assertRaises(ValueError):
            operator.qualification_coverage(self.initial, dict(self.proof, case_ids=ids))

    def test_unchanged_profile_hash_and_roles_are_required(self):
        for change in ({'unchanged_profile_sha256s': {}},
                       {'unchanged_profile_sha256s': {'converse_warm': 'b' * 64}},
                       {'changed_active_profiles': operator.PARTIAL_PROFILES[:-1]},
                       {'qualification_scope': 'full'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                operator.qualification_coverage(self.initial, dict(self.proof, **change))

    def test_conversation_control_exception_blocks_partial_scope(self):
        initial = deepcopy(self.initial)
        initial['validation']['profile_control_exceptions'] = {'converse_warm': {'max_tokens_after': 640}}
        with self.assertRaises(ValueError):
            operator.qualification_coverage(initial, self.proof)

    def test_historical_full_scope_cannot_accept_partial_counts(self):
        with self.assertRaises(ValueError):
            operator.qualification_coverage({}, self.proof)
        operator.qualification_coverage({}, {'functional_cases_passed': 24, 'functional_cases_total': 24})


if __name__ == '__main__':
    unittest.main()
