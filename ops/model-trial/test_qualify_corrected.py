"""Exact carry partition and corrected finite cohort; all model transport mocked."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import sqlite3
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

import qualify_corrected as driver
import test_qualify_trial as original_tests


class CorrectedCohort(unittest.TestCase):
    def setUp(self):
        self.fixture = original_tests.FiniteRunnerTests(methodName='test_exact_one_primary_attempt_and_real_effective_caps_per_case')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        fixture = self.fixture
        self.prior_root = fixture.root
        self.root = self.prior_root / 'new-corrected-root'
        self.root.mkdir()
        self.prior_run = self.prior_root / 'original-candidate-run'
        self.prior_run.mkdir()
        self.source = fixture.source
        self.source_hashes = {p.name: driver.sha(p.read_bytes()) for p in self.source.glob('*.py')}
        self.old_env = fixture.candidate_env
        self.new_env = self.old_env + b'LLM_PROFILES_JSON={"converse_warm":{"primary":"openai-gpt-6-1-sol","max_tokens":640}}\n'
        self.original_profiles = fixture.profiles

        def profiles(cfg):
            table = self.original_profiles(cfg)
            table['converse_warm']['max_tokens'] = cfg.cap
            table['converse_warm']['primary'] = getattr(cfg, 'converse_primary', 'anthropic-claude-sonnet-5.5')
            return table

        self.profiles = profiles
        sys.modules['common'].load_config = lambda env: SimpleNamespace(
            db_path=fixture.database, cohort=env['DO_CHAT_MODEL'], task_cost_limit_usd=.15,
            cap=json.loads(env.get('LLM_PROFILES_JSON', '{}')).get('converse_warm', {}).get('max_tokens', 320),
            converse_primary=json.loads(env.get('LLM_PROFILES_JSON', '{}')).get('converse_warm', {}).get('primary', 'anthropic-claude-sonnet-5.5'))
        fixture.llm.profiles = profiles
        self.old_profiles = profiles(SimpleNamespace(cohort='candidate', cap=320))
        self.new_profiles = profiles(SimpleNamespace(cohort='candidate', cap=640, converse_primary='openai-gpt-6-1-sol'))
        non_converse = [name for name in original_tests.PROFILES if name != 'converse_warm']
        ids = list(driver.CONVERSE_IDS) + ['unchanged-' + str(n) for n in range(21)]
        cases, results = [], []
        connection = sqlite3.connect(fixture.database)
        for index, identity in enumerate(ids):
            profile_name = 'converse_warm' if index < 3 else non_converse[(index - 3) % 7]
            profile = self.old_profiles[profile_name]
            messages = [{'role': 'user', 'content': 'Synthetic immutable request ' + identity}]
            raw = '{}'
            case = {'id': identity, 'profile': profile_name, 'runtime_profile': deepcopy(profile),
                'max_tokens': profile['max_tokens'], 'messages': messages,
                'input_sha256': driver.sha(driver.canonical(messages)), 'synthetic': True,
                'caller_kwargs': {'profile': profile_name}}
            cursor = connection.execute('INSERT INTO llm_usage(kind,model,tokens_in,tokens_out,seconds,cost_usd,skill) VALUES(?,?,?,?,?,?,?)',
                ('chat', profile['primary'], 10, 3, None, .001, driver.SKILL))
            usage = {'id': cursor.lastrowid, 'kind': 'chat', 'model': profile['primary'],
                     'tokens_in': 10, 'tokens_out': 3, 'seconds': None, 'cost_usd': .001}
            result = {'id': identity, 'profile': profile_name, 'model': profile['primary'],
                'input_sha256': case['input_sha256'], 'raw': raw, 'raw_sha256': driver.sha(raw.encode()),
                'usage': usage, 'status': 'failed' if index < 2 else 'functional_passed_pending_manual',
                'functional': {'status': 'passed', 'id': identity}}
            cases.append(case)
            results.append(result)
        connection.commit()
        connection.close()
        self.prepared = {'cases': cases, 'source_python_hashes': self.source_hashes}
        (self.prior_run / 'cases.json').write_bytes(driver.canonical(self.prepared))
        self.old_raw = {'schema': 'cara.model-trial-raw/v1', 'cohort': 'candidate',
            'candidate_env_sha256': fixture.initial['candidate_env_sha256'],
            'source_revision': fixture.initial['identity']['source_revision'],
            'runtime_hashes': fixture.initial['runtime_hashes'], 'source_python_hashes': self.source_hashes,
            'prepared_sha256': driver.sha((self.prior_run / 'cases.json').read_bytes()),
            'cases': results, 'cases_expected': 24}
        (self.prior_run / 'raw.json').write_bytes(driver.canonical(self.old_raw))
        new_initial = deepcopy(fixture.initial)
        new_initial['candidate_env_sha256'] = driver.sha(self.new_env)
        (self.root / 'prepared.json').write_bytes(driver.canonical(new_initial))
        (self.root / 'original.env').write_bytes(fixture.original_env)
        (self.root / 'candidate.env').write_bytes(self.new_env)
        self.repair_all = False
        self.replay_calls = []
        repair = ModuleType('repair_cases')

        def replay(source, case, responses, overrides):
            self.replay_calls.append((case['id'], len(responses)))
            if self.repair_all and len(responses) == 1:
                return {'status': 'needs_next', 'needs_next': {'messages': [
                    {'role': 'user', 'content': 'Actual synthetic corrective request ' + case['id']}],
                    'profile': 'converse_warm', 'kwargs': {'profile': 'converse_warm'}}}
            return {'status': 'functional_passed_pending_manual', 'final_text': 'Synthetic final text',
                    'requests': [], 'effects_preserved': True}

        repair.replay_with_responses = replay
        self.repair_patch = mock.patch.dict(sys.modules, {'repair_cases': repair})
        self.repair_patch.start()
        self.addCleanup(self.repair_patch.stop)
        self.carried_replay = mock.Mock(return_value={'status': 'passed'})
        sys.modules['validate_cases'].validate_response = self.carried_replay

    def run_driver(self, suffix='result'):
        with redirect_stdout(io.StringIO()):
            return driver.run(self.root, self.prior_root, self.prior_run, self.root / suffix)

    def partition(self, prepared=None, raw=None, old_profiles=None, new_profiles=None, hashes=None):
        return driver.selected_cases(prepared or self.prepared, raw or self.old_raw,
            old_profiles or self.old_profiles, new_profiles or self.new_profiles, hashes or self.source_hashes)

    def test_exact21_carries_and3_fresh640_inputs(self):
        updated, carried, fresh = self.partition()
        self.assertEqual(len(carried), 21)
        self.assertEqual(len(fresh), 3)
        self.assertEqual({c['id'] for c in fresh}, set(driver.CONVERSE_IDS))
        for case in updated['cases']:
            old = next(c for c in self.prepared['cases'] if c['id'] == case['id'])
            self.assertEqual(case['messages'], old['messages'])
            self.assertEqual(case['input_sha256'], old['input_sha256'])
            self.assertEqual(case['max_tokens'], 640 if case['id'] in driver.CONVERSE_IDS else old['max_tokens'])
            if case['id'] in driver.CONVERSE_IDS:
                self.assertEqual(case['runtime_profile']['primary'], 'openai-gpt-6-1-sol')
        self.assertEqual(self.prepared['cases'][0]['max_tokens'], 320)

    def test_unchanged_source_profile_input_and_raw_are_required_for_carry(self):
        for kind in ('source', 'profile', 'input', 'raw'):
            with self.subTest(kind=kind):
                prepared, raw, profiles, hashes = deepcopy(self.prepared), deepcopy(self.old_raw), deepcopy(self.new_profiles), dict(self.source_hashes)
                if kind == 'source':
                    hashes['llm.py'] = '0' * 64
                elif kind == 'profile':
                    profiles['router_fast']['timeout'] += 1
                elif kind == 'input':
                    prepared['cases'][-1]['messages'][0]['content'] += ' changed'
                else:
                    raw['cases'][-1]['raw'] = 'altered raw'
                with self.assertRaises(ValueError):
                    self.partition(prepared=prepared, raw=raw, new_profiles=profiles, hashes=hashes)

    def test_failed_nonconversation_result_cannot_be_carried(self):
        raw = deepcopy(self.old_raw)
        raw['cases'][-1]['status'] = 'failed'
        with self.assertRaisesRegex(ValueError, 'nonconversation prior functional'):
            self.partition(raw=raw)

    def test_additional_conversation_control_change_is_refused(self):
        profiles = deepcopy(self.new_profiles)
        profiles['converse_warm']['temperature'] = .8
        with self.assertRaisesRegex(ValueError, 'only approved conversation Sonnet320toSol640'):
            self.partition(new_profiles=profiles)

    def test_other_conversation_model_is_refused(self):
        profiles = deepcopy(self.new_profiles)
        profiles['converse_warm']['primary'] = 'unapproved-model'
        with self.assertRaisesRegex(ValueError, 'Sonnet320toSol640'):
            self.partition(new_profiles=profiles)

    def test_driver_revalidates21_without_models_and_pays_only3(self):
        summary = self.run_driver()
        self.assertEqual(self.carried_replay.call_count, 21)
        self.assertEqual(len(self.fixture.transport_calls), 3)
        self.assertEqual(summary['new_paid_calls'], 3)
        self.assertEqual(summary['carried_cases'], 21)
        self.assertEqual(summary['functional_cases_passed'], 24)
        self.assertEqual(len(summary['cases']), 24)
        self.assertAlmostEqual(summary['accounted_cost_usd'], .024 + 3 * self.fixture.final_cost)
        self.assertTrue(all(r['functional_revalidated_without_model_calls'] for r in summary['cases'] if r['provenance'] == 'carried_exact_unchanged'))
        self.assertTrue(all(c['max_tokens'] == 640 for c in self.fixture.prepared_calls))
        self.assertFalse(summary['manual_quality_passed'])

    def test_one_original_corrective_request_per_fresh_case_is_accounted(self):
        self.repair_all = True
        summary = self.run_driver()
        self.assertEqual(len(self.fixture.transport_calls), 6)
        self.assertEqual(summary['new_paid_calls'], 6)
        self.assertEqual(summary['functional_cases_passed'], 24)
        self.assertEqual(sorted(n for _, n in self.replay_calls), [1, 1, 1, 2, 2, 2])

    def test_reservation_failure_has_zero_paid_requests_and_cannot_be_replayed(self):
        self.fixture.reserve_cost = .150001
        summary = self.run_driver()
        self.assertEqual(summary['new_paid_calls'], 0)
        self.assertEqual(len(self.fixture.transport_calls), 0)
        self.assertEqual(len(self.fixture.usage()), 24)
        with self.assertRaises(FileExistsError):
            self.run_driver(suffix='forbidden-second-output')
        self.assertEqual(len(self.fixture.transport_calls), 0)

    def test_unknown_stops_at_first_fresh_request_and_preserves_charge(self):
        self.fixture.mode = 'unknown'
        summary = self.run_driver()
        self.assertEqual(summary['new_paid_calls'], 1)
        self.assertEqual(len(self.fixture.transport_calls), 1)
        self.assertEqual(len(summary['cases']), 22)
        self.assertEqual(len(summary['unknown_usage_ids']), 1)
        self.assertAlmostEqual(summary['accounted_cost_usd'], .024 + self.fixture.reserve_cost)
        self.assertTrue((self.root / 'result' / 'STOPPED-UNKNOWN.json').is_file())

    def test_unknown_prior_history_refuses_before_marker_and_transport(self):
        connection = sqlite3.connect(self.fixture.database)
        connection.execute("UPDATE llm_usage SET kind='unknown_chat' WHERE id=1")
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(ValueError, 'unknown prior'):
            self.run_driver()
        self.assertEqual(self.fixture.transport_calls, [])
        self.assertFalse((self.root / 'qualification-corrected.STARTED').exists())

    def test_changed_original_ledger_row_cannot_be_carried(self):
        connection = sqlite3.connect(self.fixture.database)
        connection.execute('UPDATE llm_usage SET cost_usd=.002 WHERE id=4')
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(ValueError, 'original paid case ledger'):
            self.run_driver()
        self.assertEqual(self.fixture.transport_calls, [])

    def test_failed_corrected_config_functional_replay_blocks_all_spend(self):
        self.carried_replay.return_value = {'status': 'failed'}
        with self.assertRaisesRegex(ValueError, 'corrected-config functional replay'):
            self.run_driver()
        self.assertEqual(self.fixture.transport_calls, [])


if __name__ == '__main__':
    unittest.main()
