"""Partial changed-role proof: real scratch accounting, no model/remote I/O."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import sqlite3
from types import SimpleNamespace
import unittest

import qualify_partial as partial
import test_qualify_corrected as corrected_tests


class PartialScopeProof(unittest.TestCase):
    def setUp(self):
        self.fixture = corrected_tests.CorrectedCohort(methodName='test_exact21_carries_and3_fresh640_inputs')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        fixture = self.fixture
        self.root, self.prior_root, self.prior_run = fixture.root, fixture.prior_root, fixture.prior_run
        base_profiles = fixture.profiles

        def profiles(cfg):
            table = base_profiles(cfg)
            if cfg.cohort == 'baseline':
                table['converse_warm']['primary'] = 'deepseek-4-flash'
            table['improvement_evaluator'] = {'primary': cfg.cohort + '-improvement-evaluator',
                'fallbacks': ['unchanged-backup'], 'max_tokens': 1000, 'json_required': True, 'timeout': 90}
            return table

        fixture.fixture.llm.profiles = profiles
        fixture.fixture.llm.budget_limits = lambda cfg, conn: (2.0, 12.0)
        self.profiles = profiles
        self.original = profiles(SimpleNamespace(cohort='baseline', cap=320))
        self.candidate = profiles(SimpleNamespace(cohort='candidate', cap=320))
        self.selected = profiles(SimpleNamespace(cohort='candidate', cap=320, converse_primary='deepseek-4-flash'))
        data = fixture.old_env + b'LLM_PROFILES_JSON={"converse_warm":{"primary":"deepseek-4-flash","max_tokens":320}}\n'
        (self.root / 'candidate.env').write_bytes(data)
        initial = json.loads((self.root / 'prepared.json').read_text())
        initial['candidate_env_sha256'] = partial.sha(data)
        (self.root / 'prepared.json').write_bytes(partial.canonical(initial))

    def run_proof(self, suffix='partial-output'):
        with redirect_stdout(io.StringIO()):
            return partial.run(self.root, self.prior_root, self.prior_run, self.root / suffix)

    def partition(self, selected=None, raw=None, prepared=None):
        return partial.partition(prepared or self.fixture.prepared, raw or self.fixture.old_raw,
            self.original, self.candidate, selected or self.selected, self.fixture.source_hashes)

    def test_exact21_covers_seven_changed_active_roles_with_no_paid_call(self):
        before = self.fixture.fixture.usage()
        summary = self.run_proof()
        self.assertEqual(summary['schema'], 'cara-model-trial-partial/v1')
        self.assertEqual(summary['qualification_scope'], 'changed_profiles_only')
        self.assertEqual(summary['functional_cases_total'], 21)
        self.assertEqual(summary['functional_cases_passed'], 21)
        self.assertEqual(summary['missing_case_ids'], [])
        self.assertEqual(summary['unknown_usage_ids'], [])
        self.assertEqual(summary['new_paid_calls'], 0)
        self.assertEqual(summary['changed_active_profiles'], list(partial.CHANGED_ACTIVE))
        self.assertEqual(self.fixture.carried_replay.call_count, 21)
        self.assertEqual(self.fixture.fixture.transport_calls, [])
        self.assertEqual(self.fixture.fixture.original_reservations, [])
        self.assertEqual(self.fixture.fixture.usage(), before)
        self.assertAlmostEqual(summary['accounted_cost_usd'], .024)
        self.assertFalse(summary['manual_quality_passed'])

    def test_unchanged_profile_hash_comes_from_original_deepseek_not_failed_sonnet(self):
        summary = self.run_proof()
        expected = partial.sha(partial.canonical(self.original['converse_warm']))
        self.assertEqual(summary['unchanged_profile_sha256s'], {'converse_warm': expected})
        proof = json.loads((self.root / 'partial-output' / 'unchanged-conversation-proof.json').read_text())
        self.assertEqual(proof['effective_original'], proof['effective_candidate'])
        self.assertEqual(proof['effective_candidate']['primary'], 'deepseek-4-flash')
        self.assertEqual(proof['effective_candidate']['max_tokens'], 320)
        self.assertNotEqual(expected, partial.sha(partial.canonical(self.candidate['converse_warm'])))
        self.assertTrue(proof['failed_conversation_trials_not_promoted'])

    def test_conversation_and_dormant_evaluator_are_explicitly_excluded(self):
        summary = self.run_proof()
        self.assertEqual(set(summary['excluded_case_ids']), set(partial.CONVERSE_IDS))
        self.assertTrue(set(summary['case_ids']).isdisjoint(partial.CONVERSE_IDS))
        self.assertEqual(summary['dormant_excluded_profiles'], ['improvement_evaluator'])
        self.assertTrue(all(case['profile'] in partial.CHANGED_ACTIVE for case in summary['cases']))

    def test_retained_conversation_controls_must_be_exact_original_profile(self):
        for field, value in [('primary', 'anthropic-claude-sonnet-5.5'), ('max_tokens', 640),
                             ('fallbacks', ['different-backup']), ('temperature', .8)]:
            with self.subTest(field=field):
                selected = deepcopy(self.selected)
                selected['converse_warm'][field] = value
                with self.assertRaisesRegex(ValueError, 'exact original DeepSeek320'):
                    self.partition(selected=selected)

    def test_any_other_changed_role_control_delta_is_rejected(self):
        selected = deepcopy(self.selected)
        selected['router_fast']['timeout'] += 1
        with self.assertRaisesRegex(ValueError, 'qualified original candidate'):
            self.partition(selected=selected)

    def test_failed_or_modified_original_changed_role_evidence_is_rejected(self):
        for kind in ('verdict', 'raw', 'input'):
            with self.subTest(kind=kind):
                raw, prepared = deepcopy(self.fixture.old_raw), deepcopy(self.fixture.prepared)
                if kind == 'verdict':
                    raw['cases'][-1]['status'] = 'failed'
                elif kind == 'raw':
                    raw['cases'][-1]['raw'] = 'changed'
                else:
                    prepared['cases'][-1]['messages'][0]['content'] += 'changed'
                with self.assertRaises(ValueError):
                    self.partition(raw=raw, prepared=prepared)

    def test_unknown_charge_even_from_excluded_conversation_refuses_proof(self):
        connection = sqlite3.connect(self.fixture.fixture.database)
        connection.execute("UPDATE llm_usage SET kind='unknown_chat' WHERE id=1")
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(ValueError, 'unknown prior'):
            self.run_proof()
        self.assertFalse((self.root / 'qualification-partial.STARTED').exists())
        self.assertEqual(self.fixture.fixture.transport_calls, [])

    def test_altered_carried_paid_row_refuses_proof(self):
        connection = sqlite3.connect(self.fixture.fixture.database)
        connection.execute('UPDATE llm_usage SET cost_usd=.002 WHERE id=4')
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(ValueError, 'retained paid ledger row'):
            self.run_proof()

    def test_native_daily_monthly_budget_change_refuses_proof(self):
        self.fixture.fixture.llm.budget_limits = lambda cfg, conn: (1.0, 12.0)
        with self.assertRaisesRegex(ValueError, 'budgets must remain2/12'):
            self.run_proof()

    def test_new_functional_failure_blocks_proof_without_spend(self):
        self.fixture.carried_replay.return_value = {'status': 'failed'}
        with self.assertRaisesRegex(ValueError, 'functional replay failed'):
            self.run_proof()
        self.assertEqual(self.fixture.fixture.transport_calls, [])
        self.assertFalse((self.root / 'qualification-partial.STARTED').exists())

    def test_partial_proof_cannot_be_replayed_to_a_new_directory(self):
        self.run_proof()
        with self.assertRaises(FileExistsError):
            self.run_proof(suffix='forbidden-second-output')
        self.assertEqual(self.fixture.fixture.transport_calls, [])


if __name__ == '__main__':
    unittest.main()
