"""Offline coverage for the exact qualification3 continuation boundary."""
import ast
import copy
from contextlib import closing
from datetime import datetime, timezone
import sqlite3
import sys
import types
from pathlib import Path
import unittest
from unittest import mock

import qualify_retirement93 as n
import native_backend as native
import prepare_selection as selection


class ContinuationTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(n, 'replay_retained',
            side_effect=lambda repair, *args: repair.replay_with_responses(*args))
        patcher.start()
        self.addCleanup(patcher.stop)

    def case(self, identity=n.FAIL_CASE):
        messages = [{'role': 'user', 'content': 'delivery status?'}]
        return {'id': identity, 'kind': 'text', 'case': {'id': identity.split('__')[0]},
                'model': 'openai-gpt-oss-120b', 'profile': 'converse_warm',
                'messages': messages, 'input_sha256': n.sha(n.canonical(messages)),
                'max_tokens': 320, 'temperature': .7, 'timeout': 60}

    def captured(self):
        messages = [{'role': 'user', 'content': 'unchanged native correction'}]
        return {'profile': 'converse_warm', 'skill': 'converse',
                'kwargs': {'profile': 'converse_warm'}, 'messages': messages,
                'input_sha256': n.sha(n.canonical(messages))}

    def retained(self, failed=False):
        return {'raw': 'retained answer', 'status': 'failed' if failed else 'functional_passed_pending_semantic',
                'functional': {'status': 'passed', 'original': 'evidence'}}

    def run_flow(self, index, first, case=None, proofs=()):
        q, repair, validator, paid = (mock.Mock() for _ in range(4))
        repair.replay_with_responses.side_effect = proofs
        paid.return_value = {'raw': 'new answer'}
        validator.validate_response.return_value = {'status': 'passed'}
        result = {}
        n.run_case(q, repair, validator, None, None, Path('/source'), Path('/output'), {},
                   index, case or self.case(), first, paid, result)
        return result, paid, repair, validator

    def terminal(self, **changes):
        return dict({'status': 'functional_passed_pending_manual', 'effects_preserved': True,
                     'deterministic_fallback': False, 'responses_consumed': 2,
                     'requests': [{}, {}], 'final_text': 'native final'}, **changes)

    def test_all_seven_passes_reuse_exact_response_and_functional_evidence_without_calls(self):
        for index in range(7):
            first = self.retained()
            original = copy.deepcopy(first)
            result, paid, repair, validator = self.run_flow(index, first)
            self.assertEqual(first, original)
            self.assertEqual(result['phases'][0]['result'], original)
            self.assertEqual(result['functional'], original['functional'])
            self.assertEqual(result['final_text'], original['raw'])
            self.assertEqual(result['phases'][0]['origin'], 'retained_qualification3')
            paid.assert_not_called(); repair.assert_not_called(); validator.assert_not_called()

    def test_case_eight_gets_only_exact_existing_correction(self):
        first = self.retained(True)
        result, paid, repair, validator = self.run_flow(7, first, proofs=[
            {'status': 'needs_next', 'needs_next': self.captured()}, self.terminal()])
        paid.assert_called_once_with(n.correction_case(self.case(), self.captured()), 'correction')
        self.assertEqual(repair.replay_with_responses.call_args_list[1].args[2], ['retained answer', 'new answer'])
        self.assertEqual(result['phases'][0]['result']['status'], 'failed')
        self.assertEqual(result['final_text'], 'native final')
        validator.assert_not_called()

    def test_remaining35_keep_single_original_firstpass(self):
        case = self.case('assistant-data-only-ru__openai-gpt-oss-120b')
        for index in range(8, 43):
            result, paid, repair, validator = self.run_flow(index, None, case)
            paid.assert_called_once_with(case, 'first')
            validator.validate_response.assert_called_once_with(Path('/source'), case['case'], 'new answer', {})
            repair.assert_not_called()
            self.assertEqual(len(result['phases']), 1)
            self.assertEqual(result['final_text'], 'new answer')

    def test_invalid_partition_is_rejected_before_any_provider_call(self):
        for index, first, case in ((0, None, self.case()), (7, None, self.case()),
                                  (8, self.retained(), self.case()), (8, None, self.case()),
                                  (-1, None, self.case()), (43, None, self.case('other'))):
            paid = mock.Mock(return_value={'raw': 'must not call'})
            with self.subTest(index=index, first=first), self.assertRaises(ValueError):
                n.run_case(mock.Mock(), mock.Mock(), mock.Mock(), None, None, Path('/source'),
                           Path('/output'), {}, index, case, first, paid, {})
            paid.assert_not_called()

    def test_third_request_and_deterministic_containment_never_get_another_call(self):
        for terminal in ({'status': 'needs_next'}, self.terminal(deterministic_fallback=True),
                         self.terminal(effects_preserved=False), self.terminal(responses_consumed=3)):
            paid, repair = mock.Mock(return_value={'raw': 'new answer'}), mock.Mock()
            repair.replay_with_responses.side_effect = [
                {'status': 'needs_next', 'needs_next': self.captured()}, terminal]
            with self.subTest(terminal=terminal), self.assertRaises(ValueError):
                n.run_case(mock.Mock(), repair, mock.Mock(), None, None, Path('/source'), Path('/output'), {},
                           7, self.case(), self.retained(True), paid, {})
            self.assertEqual(paid.call_count, 1)

    def test_no_correction_for_unselected_case_or_modified_controls(self):
        original = self.case()
        corrected = n.correction_case(original, self.captured())
        for key in ('id', 'model', 'profile', 'max_tokens', 'temperature', 'timeout'):
            self.assertEqual(corrected[key], original[key])
        for change in ({'profile': 'router_fast'}, {'skill': 'router'},
                       {'kwargs': {'profile': 'converse_warm', 'max_tokens': 640}}, {'input_sha256': '0'*64}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                n.correction_case(original, dict(self.captured(), **change))
        with self.assertRaises(ValueError):
            n.correction_case(self.case('other'), self.captured())

    def test_all_original_ledger_prefixes_must_stay_exact(self):
        rows = [{'id': 25062 + i, 'kind': 'chat', 'cost_usd': .001} for i in range(8)]
        retained = [{'reservation_id': row['id'], 'ledger': copy.deepcopy(rows[:i+1])} for i, row in enumerate(rows)]
        actual = [dict(row, skill='old') for row in rows]
        n.verify_carried_ledger(actual, retained, 'old')
        for index in range(8):
            changed = copy.deepcopy(actual); changed[index]['cost_usd'] = 0
            with self.subTest(index=index), self.assertRaises(ValueError):
                n.verify_carried_ledger(changed, retained, 'old')
        with self.assertRaises(ValueError):
            n.verify_carried_ledger(actual, retained[:7], 'old')


class RetainedTaskClockTests(unittest.TestCase):
    def module(self, clock):
        source = Path(__file__).resolve().parents[2]
        filename = source / 'tasks_svc.py'
        method = next(node for node in ast.walk(ast.parse(filename.read_text(encoding='utf-8')))
                      if isinstance(node, ast.FunctionDef) and node.name == '_mark_task_delivery_ambiguous')
        module = types.ModuleType('tasks_svc')
        module.__file__ = str(filename)
        module.datetime, module.timezone = clock, timezone
        # Execute the exact application method over synthetic in-memory rows.
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(filename), 'exec'), module.__dict__)
        return source, module

    def test_actual_delivery_timestamp_and_whole_proof_stable_across_distinct_base_clocks(self):
        frozen = datetime(2026, 10, 30, 5, 17, tzinfo=timezone.utc)
        case = {'id': 'assistant-uncertain-delivery-en', 'messages': [{'role': 'user', 'content': 'same request'}]}
        proofs, baseline_timestamps = [], []
        for hour in (6, 9):
            base = datetime(2026, 10, 30, hour, 2, 3, tzinfo=timezone.utc)
            class DifferentBaseClock(datetime):
                @classmethod
                def now(cls, tz=None):
                    return base.astimezone(tz) if tz is not None else base.replace(tzinfo=None)
            source, module = self.module(DifferentBaseClock)
            def invoke(_source, selected, responses, overrides):
                with closing(sqlite3.connect(':memory:')) as conn:
                    conn.execute('CREATE TABLE assistant_tasks(id INTEGER, delivery_status TEXT, next_action_at TEXT, updated_at TEXT)')
                    conn.execute("INSERT INTO assistant_tasks VALUES(1,'sending','due','old')")
                    module._mark_task_delivery_ambiguous(types.SimpleNamespace(conn=conn), 1)
                    rows = conn.execute('SELECT * FROM assistant_tasks').fetchall()
                return {'requests': selected['messages'], 'responses': responses,
                        'protected_before_sha256': n.sha(n.canonical(rows)), 'state': rows}
            baseline_timestamps.append(invoke(source, case, ['retained'], {})['state'][0][3])
            repair = types.SimpleNamespace(_clock_from_case=mock.Mock(return_value=frozen), replay_with_responses=mock.Mock(side_effect=invoke))
            with mock.patch.dict(sys.modules, {'tasks_svc': module}):
                proofs.append(n.replay_retained(repair, source, case, ['retained'], {}))
            self.assertIs(module.datetime, DifferentBaseClock)
            repair._clock_from_case.assert_called_once_with(case, {})
            repair.replay_with_responses.assert_called_once_with(source, case, ['retained'], {})
        self.assertNotEqual(*baseline_timestamps)
        self.assertEqual(n.canonical(proofs[0]), n.canonical(proofs[1]))
        self.assertEqual(proofs[0]['requests'], case['messages'])
        self.assertEqual(proofs[0]['state'], [(1, 'ambiguous', None, frozen.isoformat())])

    def test_foreign_module_cannot_reach_replay(self):
        source, module = self.module(datetime)
        module.__file__ = str(source / 'foreign' / 'tasks_svc.py')
        repair = mock.Mock()
        with mock.patch.dict(sys.modules, {'tasks_svc': module}), self.assertRaisesRegex(ValueError, 'frozen source'):
            n.replay_retained(repair, source, {}, ['retained'], {})
        repair.replay_with_responses.assert_not_called()

    def test_original_task_clock_is_restored_when_replay_fails(self):
        source, module = self.module(datetime)
        repair = mock.Mock()
        repair._clock_from_case.return_value = datetime(2026, 10, 30, tzinfo=timezone.utc)
        repair.replay_with_responses.side_effect = AssertionError('protected state changed')
        with mock.patch.dict(sys.modules, {'tasks_svc': module}), self.assertRaisesRegex(AssertionError, 'protected state changed'):
            n.replay_retained(repair, source, {}, ['retained'], {})
        self.assertIs(module.datetime, datetime)


class RetirementDispatchTests(unittest.TestCase):
    def fixture(self):
        roles = ('plan', 'inputs', 'raw', 'original_plan', 'original_raw',
                 *('original_case_' + str(i).zfill(3) for i in range(1, 9)))
        plan = {'qualification_mode': n.MODE, 'identity': {'source_commit': n.SOURCE_COMMIT},
                'operator_source_commit': 'a'*40, 'package_sha256s': {'qualify_retirement93.py': '1'*64}}
        model = {'qualification_mode': n.MODE, 'evidence_roles': {key: key+'.json' for key in roles},
                 'evidence_files': {key+'.json': key+'-sha' for key in roles}}
        semantic = {'qualification_mode': n.MODE, 'evidence_roles': {'review': 'semantic.json'}}
        evidence = {'model_qualification': model, 'semantic_review': semantic}
        for key in ('source_review', 'operator_qualification'):
            evidence[key] = {'operator_source_commit': 'a'*40,
                             'operator_package_sha256': n.sha(n.canonical(plan['package_sha256s']))}
        return plan, evidence, lambda envelope, filename: filename, {'archive': 'source'}

    def test_exact_mode_and_all_eight_originals_reach_independent_gate(self):
        data = self.fixture(); validate = mock.Mock(return_value='retirement')
        self.assertEqual(native.qualification_helpers(data[0]),
                         {'qualify_retirement93.py', 'retirement93_gate.py', 'fullflow_gate.py'})
        self.assertEqual(native.verify_model_gates(*data, fullflow_validator=validate), 'retirement')
        self.assertEqual(validate.call_args.args[-4:], ('original_plan.json', 'original_raw.json',
            ['original_case_'+str(i).zfill(3)+'.json' for i in range(1, 9)], data[3]))

    def test_missing_original_mixed_mode_or_operator_mismatch_cannot_dispatch(self):
        for mutation in (lambda d: d[1]['model_qualification']['evidence_roles'].pop('original_case_008'),
                         lambda d: d[1]['semantic_review'].update(qualification_mode='full_native_existing_correction'),
                         lambda d: d[1]['source_review'].update(operator_source_commit='b'*40)):
            data = self.fixture(); mutation(data); validate = mock.Mock()
            with self.assertRaises(ValueError):
                native.verify_model_gates(*data, fullflow_validator=validate)
            validate.assert_not_called()

    def test_selection_binds_new_mode_to_separately_reviewed_operator(self):
        plan, evidence, _, _ = self.fixture()
        result = selection.operator_revision_binding(evidence,
            {'source_commit': n.SOURCE_COMMIT, 'operator_source_commit': 'a'*40}, plan['package_sha256s'])
        self.assertEqual(result, {'qualification_mode': n.MODE, 'operator_source_commit': 'a'*40})


if __name__ == '__main__':
    unittest.main()
