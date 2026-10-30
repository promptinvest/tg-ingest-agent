"""Offline qualification safety checks; every provider transport is mocked."""
import copy
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

import qualify_models as q

SOURCE = Path(os.environ.get('CARA_TEST_SOURCE', Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(SOURCE))
import llm
import call_budget
import store
from testlib import make_config


class SafetyTests(unittest.TestCase):
    def archive(self, members, comment=None):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w', format=tarfile.PAX_FORMAT,
                          pax_headers={'comment': comment} if comment else {}) as archive:
            for name, content, kind in members:
                info = tarfile.TarInfo(name)
                info.type = kind
                if kind == tarfile.REGTYPE:
                    info.size = len(content)
                    archive.addfile(info, io.BytesIO(content))
                else:
                    info.linkname = 'elsewhere'
                    archive.addfile(info)
        return stream.getvalue()

    def test_archive_source_binding_and_git_commit_comment(self):
        raw = self.archive([('one.py', b'one', tarfile.REGTYPE)], 'a' * 40)
        self.assertEqual(q.verify_archive(raw, {'one.py': q.sha(b'one')}, 'a' * 40),
                         {'one.py': q.sha(b'one')})
        for expected, commit in (({'one.py': q.sha(b'changed')}, 'a' * 40),
                                  ({'one.py': q.sha(b'one')}, 'b' * 40),
                                  ({}, 'a' * 40)):
            with self.assertRaises(ValueError):
                q.verify_archive(raw, expected, commit)

    def test_archive_rejects_links_traversal_and_duplicate_paths(self):
        for members in ([('../one.py', b'one', tarfile.REGTYPE)],
                        [('/one.py', b'one', tarfile.REGTYPE)],
                        [('one.py', b'', tarfile.SYMTYPE)],
                        [('one.py', b'', tarfile.LNKTYPE)],
                        [('one.py', b'one', tarfile.REGTYPE), ('./one.py', b'one', tarfile.REGTYPE)]):
            with self.subTest(members=members), self.assertRaises(ValueError):
                q.verify_archive(self.archive(members), {'one.py': q.sha(b'one')}, 'a' * 40)

    def test_guard_enforces_both_limits_and_unknown_outcome(self):
        self.assertEqual(q.guard([], .15), 0)
        self.assertAlmostEqual(q.guard([{'kind': 'chat', 'cost_usd': .10}], .15), .10)
        for rows, cost in (([], .151), ([], float('nan')), ([], -1), ([], True),
                           ([{'kind': 'chat', 'cost_usd': .101}], .15),
                           ([{'kind': 'unknown_chat', 'cost_usd': .001}], .001),
                           ([{'kind': 'rejected_chat', 'cost_usd': .001}], .001)):
            with self.subTest(rows=rows, cost=cost), self.assertRaises(ValueError):
                q.guard(rows, cost)

    def test_publication_cannot_overwrite_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'STARTED.json'
            q.publish(path, {'attempt': 1})
            before = path.read_bytes()
            with self.assertRaises(FileExistsError):
                q.publish(path, {'attempt': 2})
            self.assertEqual(path.read_bytes(), before)

    def test_images_are_deterministic_distinct_and_not_external_assets(self):
        data = {kind: q.image_bytes(kind) for kind in ('book', 'invoice', 'blank')}
        self.assertEqual(len({q.sha(raw) for raw in data.values()}), 3)
        for kind, raw in data.items():
            self.assertEqual(raw, q.image_bytes(kind))
            self.assertTrue(raw.startswith(b'\x89PNG\r\n\x1a\n'))
            self.assertIn(b'IHDR', raw)

    def test_plan_hash_rejection_precedes_source_import_and_any_started_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            q.publish(root / 'plan.json', {'schema': 'wrong'})
            with mock.patch.object(q, 'libraries', side_effect=AssertionError('must not import')):
                with self.assertRaisesRegex(ValueError, 'exact plan hash'):
                    q.execute(root, '0' * 64)
            self.assertFalse((root / 'STARTED.json').exists())

    def test_tampered_vision_image_stops_before_import_start_marker_or_paid_call(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            source.mkdir()
            q.publish(root / 'source.tar', b'archive')
            q.publish(root / 'original.env', b'environment')
            q.publish(root / 'candidate.env', b'candidate')
            q.publish(root / 'book.png', b'tampered')
            case = {'id': 'vision', 'kind': 'vision', 'profile': 'vision', 'model': q.VISION_MODEL,
                    'messages': [], 'input_sha256': q.sha(q.canonical([])),
                    'image': 'book.png', 'image_sha256': q.sha(b'original')}
            q.publish(root / 'inputs.json', {'cases': [case]})
            plan = {'schema': 'cara.model-retirement-prepared/v1', 'source': str(source),
                'source_hashes': {}, 'archive': str(root / 'source.tar'),
                'archive_sha256': q.sha(b'archive'), 'environment': str(root / 'original.env'),
                'original_environment_sha256': q.sha(b'environment'),
                'candidate_environment_sha256': q.sha(b'candidate'),
                'qualification_inputs_sha256': q.sha((root / 'inputs.json').read_bytes()),
                'cases_expected': 1, 'coverage': q.coverage([case]),
                'attempt': 'fixture', 'skill': q.MIGRATION_PREFIX + 'fixture',
                'migration_skill_prefix': q.MIGRATION_PREFIX,
                'aggregate_ceiling_usd': q.CEILING, 'per_call_ceiling_usd': q.PER_CALL}
            q.publish(root / 'plan.json', plan)
            with mock.patch.object(q, 'libraries', side_effect=AssertionError('must not import')):
                with self.assertRaisesRegex(ValueError, 'vision fixture changed'):
                    q.execute(root, q.sha((root / 'plan.json').read_bytes()))
            self.assertFalse((root / 'STARTED.json').exists())

    def test_expansion_uses_only_actual_replacement_fallbacks_never_primary(self):
        cfg = make_config()
        profiles = llm.profiles(cfg)
        # Eight active roles, three synthetic originals each; enough to exercise
        # expansion/coverage without pretending these are the actual 24 fixtures.
        prepared = {'cases': []}
        for profile in sorted(q.ACTIVE_PROFILES):
            profiles[profile]['primary'] = 'preserved-primary'
            profiles[profile]['fallbacks'] = ['unchanged-fallback', *sorted(q.TEXT_MODELS)]
            for number in range(3):
                prepared['cases'].append({'id': profile + str(number), 'profile': profile,
                    'runtime_profile': profiles[profile], 'messages': [], 'max_tokens': 20,
                    'input_sha256': q.sha(q.canonical([])), 'rubric': ['synthetic test']})
        cases = q.expand_cases(prepared, profiles)
        self.assertEqual(len(cases), 48)
        self.assertEqual({case['model'] for case in cases}, q.TEXT_MODELS)
        broken = copy.deepcopy(prepared)
        broken['cases'].pop()
        with self.assertRaises(ValueError):
            q.expand_cases(broken, profiles)
        profiles['ask_grounded']['fallbacks'] = []
        with self.assertRaises(ValueError):
            q.expand_cases(prepared, profiles)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(Path(self.tmp.name) / 'ledger.db', cached_statements=0)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript('''
            CREATE TABLE llm_usage(id INTEGER PRIMARY KEY,ts TEXT,day TEXT,month TEXT,
              skill TEXT,kind TEXT,model TEXT,tokens_in INTEGER,tokens_out INTEGER,
              seconds REAL,cost_usd REAL,trace_id TEXT,cache_usage_json TEXT);
            CREATE TABLE preferences(key TEXT PRIMARY KEY,value TEXT,updated_at TEXT);
            CREATE TABLE kv(key TEXT PRIMARY KEY,value TEXT);
            INSERT INTO kv VALUES('preserved','original');
            INSERT INTO preferences VALUES('budget_daily_usd','2','original');
            INSERT INTO preferences VALUES('budget_monthly_usd','12','original');
        ''')
        self.cfg = make_config()
        self.skill = q.MIGRATION_PREFIX + 'fixture'
        self.old = store.usage_add(self.conn, 'ordinary', 'chat', 'old-model', 17, 5, cost_usd=.01)
        self.old_row = tuple(self.conn.execute('SELECT * FROM llm_usage WHERE id=?', (self.old,)).fetchone())

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def reserve(self, cost=.01):
        return call_budget.reserve(self.cfg, self.conn, self.skill, 'chat',
            'deepseek-v4.1-flash-1017', cost, llm.budget_limits, llm.BudgetExceeded)

    def test_only_new_owned_usage_rows_can_change(self):
        with q.ledger_only(self.conn, self.skill):
            row = self.reserve()
            with self.assertRaises(ValueError):
                call_budget.settle(self.conn, self.old, 'chat', cost_usd=0)
            with self.assertRaises(sqlite3.DatabaseError):
                self.conn.execute('DELETE FROM llm_usage')
            with self.assertRaises(sqlite3.DatabaseError):
                self.conn.execute("UPDATE kv SET value='wrong'")
            call_budget.settle(self.conn, row, 'chat', 10, 2, cost_usd=.001)
            with self.assertRaises(ValueError):
                call_budget.settle(self.conn, row, 'chat', 10, 2, cost_usd=0)
        self.assertEqual(tuple(self.conn.execute('SELECT * FROM llm_usage WHERE id=?', (self.old,)).fetchone()), self.old_row)
        self.assertEqual(self.conn.execute('SELECT value FROM kv').fetchone()[0], 'original')

    def test_existing_attempt_or_unknown_outcome_stops_before_next_reservation(self):
        with q.ledger_only(self.conn, self.skill):
            self.reserve()
            with self.assertRaisesRegex(ValueError, 'unknown prior'):
                self.reserve()
        with self.assertRaisesRegex(ValueError, 'already has usage'):
            with q.ledger_only(self.conn, self.skill):
                self.fail('must not enter reused attempt')

    def test_native_daily_limit_remains_authoritative(self):
        self.conn.execute("UPDATE preferences SET value='0.011' WHERE key='budget_daily_usd'")
        self.conn.commit()
        with q.ledger_only(self.conn, self.skill), self.assertRaises(llm.BudgetExceeded):
            self.reserve()
        self.assertEqual(q.ledger_rows(self.conn, self.skill), [])

    def test_another_attempt_cannot_reset_migration_aggregate_ceiling(self):
        store.usage_add(self.conn, q.MIGRATION_PREFIX + 'prior', 'chat',
                        'deepseek-v4.1-flash-1017', 10, 10, cost_usd=.20)
        with q.ledger_only(self.conn, self.skill):
            with self.assertRaisesRegex(ValueError, 'aggregate qualification'):
                self.reserve(.10)
        self.assertEqual(q.ledger_rows(self.conn, self.skill), [])
        self.assertAlmostEqual(sum(row['cost_usd'] for row in q.migration_rows(self.conn)), .20)

    def test_prior_attempt_unknown_charge_blocks_every_new_attempt(self):
        store.usage_add(self.conn, q.MIGRATION_PREFIX + 'prior', 'unknown_chat',
                        'deepseek-v4.1-flash-1017', cost_usd=.001)
        with q.ledger_only(self.conn, self.skill), self.assertRaisesRegex(ValueError, 'unknown prior'):
            self.reserve()
        self.assertEqual(q.ledger_rows(self.conn, self.skill), [])

    def test_accounting_prefix_is_literal_and_cannot_be_selected_per_attempt(self):
        store.usage_add(self.conn, 'modelXretirementX20261029Xother', 'chat',
                        'unrelated', cost_usd=.50)
        self.assertEqual(q.migration_rows(self.conn), [])
        with self.assertRaisesRegex(ValueError, 'fixed migration'):
            with q.ledger_only(self.conn, 'some_other_allowance'):
                self.fail('must not enter arbitrary accounting scope')

    def test_native_gateway_retains_truncated_raw_before_failure(self):
        model = 'deepseek-v4.1-flash-1017'
        messages = [{'role': 'user', 'content': 'synthetic'}]
        case = {'id': 'fixture', 'profile': 'router_fast', 'model': model,
                'messages': messages, 'input_sha256': q.sha(q.canonical(messages)),
                'max_tokens': 20, 'temperature': 0, 'timeout': 30}
        data = {'model': model, 'choices': [{'message': {'content': 'retained partial'},
                'finish_reason': 'length'}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 2}}
        result = {}
        with q.ledger_only(self.conn, self.skill), \
             mock.patch.object(llm, 'transport_chat', return_value=data) as transport:
            with self.assertRaisesRegex(ValueError, 'truncated'):
                q.execute_case(self.cfg, self.conn, self.skill, case, result)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(result['raw'], 'retained partial')
        self.assertEqual(result['provider_response'], data)
        self.assertEqual(q.ledger_rows(self.conn, self.skill)[0]['kind'], 'chat')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM kv').fetchone()[0], 1)

    def test_transport_failure_keeps_unknown_reserve_and_never_retries(self):
        messages = [{'role': 'user', 'content': 'synthetic'}]
        case = {'id': 'fixture', 'profile': 'router_fast', 'model': 'deepseek-v4.1-flash-1017',
                'messages': messages, 'input_sha256': q.sha(q.canonical(messages)),
                'max_tokens': 20, 'temperature': 0, 'timeout': 30}
        with q.ledger_only(self.conn, self.skill), \
             mock.patch.object(llm, 'transport_chat', side_effect=TimeoutError('fixture')) as transport:
            with self.assertRaises(llm.LLMError):
                q.execute_case(self.cfg, self.conn, self.skill, case)
        self.assertEqual(transport.call_count, 1)
        rows = q.ledger_rows(self.conn, self.skill)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['kind'], 'unknown_chat')


class NativeVisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures, _, _ = q.libraries(SOURCE)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = make_config(VISION_MODEL=q.VISION_MODEL)
        self.cases = q.capture_vision(self.fixtures, self.cfg, self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_all_six_cases_capture_native_limits_and_actual_image_messages(self):
        self.assertEqual(len(self.cases), 6)
        self.assertEqual([case['max_tokens'] for case in self.cases], [400, 400, 160, 160, 1600, 1600])
        for case in self.cases:
            self.assertEqual(case['model'], q.VISION_MODEL)
            self.assertTrue(case['messages'][0]['content'][1]['image_url']['url'].startswith('data:image/png;base64,'))

    def test_blank_must_be_valid_json_and_cannot_invent_titles(self):
        case = self.cases[-1]
        result = q.replay_vision(self.fixtures, self.cfg, self.root, case, '{"layout":"single","entries":[]}')
        self.assertEqual(result['native_result'], [])
        for raw in ('not JSON', '{"entries":[{"title":"invented","kind":"book"}]}'):
            with self.assertRaises(ValueError):
                q.replay_vision(self.fixtures, self.cfg, self.root, case, raw)

    def test_book_extraction_preserves_visible_exact_fields(self):
        raw = json.dumps({'layout': 'single', 'entries': [{'title': 'CEDAR NOTES',
            'kind': 'book', 'creator': 'ALICE', 'year': '2099', 'aliases': []}]})
        result = q.replay_vision(self.fixtures, self.cfg, self.root, self.cases[-2], raw)
        self.assertEqual(result['status'], 'passed')


if __name__ == '__main__':
    unittest.main()
