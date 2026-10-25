"""No-paid/no-network finite runner accounting and immutable-input regressions.

The runner uses actual scratch SQLite. Provider/gateway modules and case replay
are explicit synthetic transport boundaries, never an actual model request.
"""
from contextlib import redirect_stdout
import copy
import io
import json
import math
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

import qualify_trial as runner


SKILL = 'model_trial_20261025'
PROFILES = ('router_fast', 'ingest_balanced', 'ask_grounded', 'converse_warm',
            'memory_curator', 'memory_consolidate', 'task_planner', 'task_synthesis')


class BudgetGuardTests(unittest.TestCase):
    def test_all_prior_candidate_baseline_and_rejected_rows_count_together(self):
        rows = [{'kind': 'chat', 'cost_usd': 2.46}, {'kind': 'chat', 'cost_usd': 2.4},
                {'kind': 'rejected_chat', 'cost_usd': 0}]
        self.assertAlmostEqual(runner.guard(rows, .14), 4.86)
        with self.assertRaisesRegex(ValueError, 'aggregate'):
            runner.guard(rows, .15)

    def test_unknown_and_nonchat_rows_never_allow_another_reservation(self):
        for kind in ('unknown_chat', 'unknown_stt', 'stt', 'arbitrary'):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'unknown prior'):
                runner.guard([{'kind': kind, 'cost_usd': .01}], .01)

    def test_negative_nonfinite_and_nonzero_rejected_prior_costs_are_rejected(self):
        for kind, value in [('chat', -1), ('chat', float('nan')), ('chat', float('inf')),
                            ('chat', True), ('rejected_chat', .01)]:
            with self.subTest(kind=kind, value=value), self.assertRaisesRegex(ValueError, 'invalid prior'):
                runner.guard([{'kind': kind, 'cost_usd': value}], .01)

    def test_each_call_respects_unchanged_point_fifteen_cap(self):
        self.assertEqual(runner.guard([], .15), 0)
        for value in (.1500001, -1, float('nan'), float('inf')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                runner.guard([], value)


class FiniteRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='cara-runner-netless-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        for name in ('tg_ingest_agent.py', 'llm.py', 'common.py'):
            (self.source / name).write_text('# synthetic immutable source: ' + name, encoding='utf-8')
        self.database = self.root / 'scratch.db'
        connection = sqlite3.connect(self.database)
        connection.executescript('''CREATE TABLE llm_usage(
            id INTEGER PRIMARY KEY, kind TEXT, model TEXT, tokens_in INTEGER DEFAULT 0,
            tokens_out INTEGER DEFAULT 0, seconds REAL, cost_usd REAL, skill TEXT);''')
        connection.close()
        self.original_env = b'DO_CHAT_MODEL=baseline\n'
        self.candidate_env = b'DO_CHAT_MODEL=candidate\n'
        (self.root / 'original.env').write_bytes(self.original_env)
        (self.root / 'candidate.env').write_bytes(self.candidate_env)
        self.initial = {'source': str(self.source),
            'before_env_sha256': runner.sha(self.original_env),
            'candidate_env_sha256': runner.sha(self.candidate_env),
            'identity': {'source_revision': 'a' * 40},
            'runtime_hashes': {('agent.py' if p.name == 'tg_ingest_agent.py' else p.name):
                runner.sha(p.read_bytes()) for p in self.source.glob('*.py')}}
        (self.root / 'prepared.json').write_bytes(runner.canonical(self.initial))
        self.prepared_calls, self.transport_calls, self.original_reservations = [], [], []
        self.mode, self.reserve_cost, self.final_cost = 'success', .04, .014
        self.schema_failed = False

        common = ModuleType('common')
        common.__file__ = str(self.source / 'common.py')
        common.load_config = lambda env: SimpleNamespace(db_path=self.database,
                                                        cohort=env['DO_CHAT_MODEL'])
        self.llm = ModuleType('llm')
        self.llm.__file__ = str(self.source / 'llm.py')
        self.llm.call_budget = SimpleNamespace(reserve=self.original_reserve)
        self.llm.profiles = self.profiles
        self.llm.prepare_chat = self.prepare_chat
        self.llm.transport_chat = self.transport_chat
        self.llm.finish_chat = self.finish_chat
        self.llm.chat_profile = lambda *args, **kwargs: self.fail('automatic profile retry/fallback forbidden in finite trial')
        preparer = ModuleType('prepare_cases')
        preparer.prepare = self.prepare_cases
        validator = ModuleType('validate_cases')
        validator.validate_response = self.validate_response
        self.module_patch = mock.patch.dict(sys.modules, {'common': common, 'llm': self.llm,
            'prepare_cases': preparer, 'validate_cases': validator})
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)
        original_read = Path.read_bytes
        self.production_env = self.original_env

        def read(path):
            if str(path).replace('\\', '/') == '/etc/tg-ingest-agent.env':
                return self.production_env
            return original_read(path)

        self.read_patch = mock.patch.object(Path, 'read_bytes', read)
        self.read_patch.start()
        self.addCleanup(self.read_patch.stop)

    def profiles(self, cfg):
        return {name: {'primary': cfg.cohort + '-' + name, 'fallbacks': ['must-not-be-called'],
            'max_tokens': 400 + index * 10, 'json_required': name != 'converse_warm',
            'temperature': .7 if name == 'converse_warm' else 0, 'timeout': 30 + index}
            for index, name in enumerate(PROFILES)}

    def prepare_cases(self, source, overrides):
        self.assertEqual(source, self.source)
        cfg = SimpleNamespace(cohort=overrides['DO_CHAT_MODEL'])
        profiles = self.profiles(cfg)
        cases = []
        for index in range(24):
            name = PROFILES[index % 8]
            messages = [{'role': 'user', 'content': 'Synthetic case ' + str(index)}]
            cases.append({'id': 'fixed-' + str(index), 'profile': name,
                'runtime_profile': profiles[name], 'messages': messages,
                'input_sha256': runner.sha(runner.canonical(messages)),
                'max_tokens': profiles[name]['max_tokens']})
        return {'cases': cases, 'coverage': {'active_profiles': list(PROFILES)},
            'source_python_hashes': {p.name: runner.sha(p.read_bytes()) for p in self.source.glob('*.py')}}

    def original_reserve(self, cfg, conn, skill, kind, model, cost, limits, error):
        self.assertTrue(conn.in_transaction, 'aggregate check and reservation must hold one SQLite write transaction')
        cursor = conn.execute('INSERT INTO llm_usage(kind,model,cost_usd,skill) VALUES(?,?,?,?)',
                              ('unknown_chat', model, cost, skill))
        conn.commit()
        self.original_reservations.append(cursor.lastrowid)
        return cursor.lastrowid

    def prepare_chat(self, cfg, conn, skill, messages, **options):
        self.prepared_calls.append(dict(options))
        reservation = self.llm.call_budget.reserve(cfg, conn, skill, 'chat', options['model'],
            self.reserve_cost, lambda *args: (2, 12), ValueError)
        return {'cfg': cfg, 'conn': conn, 'reserved': reservation, 'messages': messages, 'options': options}

    def transport_chat(self, prepared):
        self.assertFalse(prepared['conn'].in_transaction, 'unknown reservation must be committed before transport')
        check = sqlite3.connect(self.database)
        try:
            self.assertEqual(check.execute('SELECT kind FROM llm_usage WHERE id=?', (prepared['reserved'],)).fetchone()[0], 'unknown_chat')
        finally:
            check.close()
        self.transport_calls.append(prepared['options']['model'])
        if self.mode in ('unknown', 'rejected'):
            raise RuntimeError('synthetic ' + self.mode)
        return {'choices': [{'message': {'content': '{}'}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 12, 'completion_tokens': 4}}

    def finish_chat(self, cfg, conn, prepared, data=None, error=None):
        if error is not None:
            if self.mode == 'rejected':
                conn.execute("UPDATE llm_usage SET kind='rejected_chat',cost_usd=0 WHERE id=?", (prepared['reserved'],))
                conn.commit()
            raise error
        conn.execute("UPDATE llm_usage SET kind='chat',tokens_in=12,tokens_out=4,cost_usd=? WHERE id=?",
                     (self.final_cost, prepared['reserved']))
        conn.commit()
        return '{}'

    def validate_response(self, source, case, raw, overrides):
        if self.schema_failed:
            raise ValueError('synthetic schema failure')
        return {'status': 'passed', 'id': case['id'], 'manual_quality_required': True}

    def usage(self):
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in connection.execute('SELECT * FROM llm_usage ORDER BY id')]
        finally:
            connection.close()

    def run_trial(self, cohort='candidate', output_name=None):
        output = self.root / (output_name or (cohort + '-output'))
        with redirect_stdout(io.StringIO()):
            runner.run(self.root, output, cohort)
        return json.loads((output / 'raw.json').read_text(encoding='utf-8'))

    def test_exact_one_primary_attempt_and_real_effective_caps_per_case(self):
        summary = self.run_trial()
        self.assertEqual(len(self.transport_calls), 24)
        self.assertEqual(len(self.usage()), 24)
        self.assertEqual(summary['functional_cases_passed'], 24)
        for index, options in enumerate(self.prepared_calls):
            profile = self.profiles(SimpleNamespace(cohort='candidate'))[PROFILES[index % 8]]
            self.assertEqual(options, {'model': profile['primary'], 'max_tokens': profile['max_tokens'],
                'temperature': profile['temperature'], 'timeout': profile['timeout']})
        self.assertTrue(all('must-not' not in model for model in self.transport_calls))
        self.assertFalse(summary['manual_quality_passed'])

    def test_both_cohorts_share_existing_ledger_without_reset_or_double_count(self):
        first = self.run_trial('candidate')
        second = self.run_trial('baseline')
        self.assertEqual(len(self.usage()), 48)
        self.assertAlmostEqual(first['accounted_cost_usd'], 24 * self.final_cost)
        self.assertAlmostEqual(second['accounted_cost_usd'], 48 * self.final_cost)
        self.assertEqual(len(second['usage']), 48)

    def test_unknown_transport_stops_after_one_and_keeps_committed_reservation(self):
        self.mode = 'unknown'
        summary = self.run_trial()
        self.assertEqual(len(self.transport_calls), 1)
        self.assertEqual(len(summary['cases']), 1)
        self.assertEqual([row['kind'] for row in self.usage()], ['unknown_chat'])
        self.assertEqual(summary['unknown_usage_ids'], [self.usage()[0]['id']])
        self.assertAlmostEqual(summary['accounted_cost_usd'], self.reserve_cost)
        self.assertTrue((self.root / 'candidate-output' / 'STOPPED-UNKNOWN.json').is_file())

    def test_unknown_candidate_blocks_baseline_without_a_new_transport(self):
        self.mode = 'unknown'
        self.run_trial('candidate')
        count = len(self.transport_calls)
        summary = self.run_trial('baseline')
        self.assertEqual(len(self.transport_calls), count)
        self.assertEqual(len(self.usage()), 1)
        self.assertEqual(summary['unknown_usage_ids'], [1])

    def test_definite_rejected_rows_release_only_own_charge_and_allow_next_case(self):
        self.mode = 'rejected'
        summary = self.run_trial()
        self.assertEqual(len(self.transport_calls), 24)
        self.assertEqual(len(self.usage()), 24)
        self.assertTrue(all(r['kind'] == 'rejected_chat' and r['cost_usd'] == 0 for r in self.usage()))
        self.assertEqual(summary['unknown_usage_ids'], [])
        self.assertEqual(summary['functional_cases_passed'], 0)

    def test_quality_failure_is_billed_and_never_drives_retry_or_fallback(self):
        self.schema_failed = True
        summary = self.run_trial()
        self.assertEqual(len(self.transport_calls), 24)
        self.assertEqual(summary['functional_cases_passed'], 0)
        self.assertEqual(summary['unknown_usage_ids'], [])
        self.assertAlmostEqual(summary['accounted_cost_usd'], 24 * self.final_cost)

    def test_existing_near_cap_qualification_rows_block_spend_across_cohorts(self):
        connection = sqlite3.connect(self.database)
        connection.execute('INSERT INTO llm_usage(kind,model,cost_usd,skill) VALUES(?,?,?,?)', ('chat', 'earlier-trial', 4.99, SKILL))
        connection.commit()
        connection.close()
        self.run_trial()
        self.assertEqual(self.transport_calls, [])
        self.assertEqual(self.original_reservations, [])
        self.assertEqual(len(self.usage()), 1)

    def test_per_call_cap_refuses_before_transport_and_unknown_row(self):
        self.reserve_cost = .150001
        self.run_trial()
        self.assertEqual(self.transport_calls, [])
        self.assertEqual(self.usage(), [])

    def test_same_cohort_cannot_be_replayed_with_a_new_output_directory(self):
        self.run_trial()
        count = len(self.transport_calls)
        with self.assertRaises(FileExistsError):
            self.run_trial(output_name='attempted-replay-new-directory')
        self.assertEqual(len(self.transport_calls), count)
        self.assertEqual(len(self.usage()), 24)

    def test_existing_output_refuses_without_new_charge(self):
        self.run_trial()
        with self.assertRaisesRegex(ValueError, 'exclusive'):
            self.run_trial()
        self.assertEqual(len(self.transport_calls), 24)

    def test_changed_source_refuses_before_marker_or_transport(self):
        (self.source / 'llm.py').write_text('# changed after pin', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'runtime source changed'):
            self.run_trial()
        self.assertEqual(self.transport_calls, [])
        self.assertFalse((self.root / 'qualification-candidate.STARTED').exists())

    def test_changed_staged_env_refuses_before_marker_or_transport(self):
        for name in ('original.env', 'candidate.env'):
            with self.subTest(name=name):
                path = self.root / name
                previous = path.read_bytes()
                path.write_bytes(previous + b'UNAPPROVED=change\n')
                try:
                    with self.assertRaisesRegex(ValueError, 'environments changed'):
                        self.run_trial()
                finally:
                    path.write_bytes(previous)
        self.assertEqual(self.transport_calls, [])
        self.assertFalse((self.root / 'qualification-candidate.STARTED').exists())

    def test_changed_live_env_refuses_before_paid_request(self):
        self.production_env += b'UNAPPROVED=change\n'
        with self.assertRaisesRegex(ValueError, 'production configuration changed'):
            self.run_trial()
        self.assertEqual(self.transport_calls, [])
        self.assertEqual(self.usage(), [])


if __name__ == '__main__':
    unittest.main()
