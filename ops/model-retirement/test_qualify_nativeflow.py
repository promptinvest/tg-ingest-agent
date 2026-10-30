"""Offline tests: native correction is bounded; earlier failures stay immutable."""
import copy
from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import qualify_nativeflow as n


class NativeFlowTests(unittest.TestCase):
    def case(self, identity='assistant-current-work-en'):
        messages = [{'role': 'user', 'content': 'status?'}]
        return {'id': identity + '__deepseek-v4.1-flash-1017', 'kind': 'text',
                'case': {'id': identity, 'profile': 'converse_warm'},
                'model': 'deepseek-v4.1-flash-1017', 'profile': 'converse_warm',
                'messages': messages, 'input_sha256': n.sha(n.canonical(messages)),
                'max_tokens': 320, 'temperature': .7, 'timeout': 60}

    def next_request(self):
        messages = [{'role': 'user', 'content': 'native repair'}]
        return {'profile': 'converse_warm', 'skill': 'converse',
                'kwargs': {'profile': 'converse_warm'}, 'messages': messages,
                'input_sha256': n.sha(n.canonical(messages))}

    def test_correction_preserves_candidate_and_every_native_control(self):
        first = self.case()
        corrected = n.correction_case(first, self.next_request())
        self.assertEqual({key: corrected[key] for key in ('id', 'model', 'profile', 'max_tokens', 'temperature', 'timeout')},
                         {key: first[key] for key in ('id', 'model', 'profile', 'max_tokens', 'temperature', 'timeout')})
        self.assertEqual(corrected['messages'], self.next_request()['messages'])

    def test_correction_rejects_unknown_profile_kwargs_prompt_and_unsupported_case(self):
        for key, value in (('profile', 'router_fast'), ('skill', 'router'),
                           ('kwargs', {'profile': 'converse_warm', 'max_tokens': 640}),
                           ('input_sha256', '0' * 64)):
            request = self.next_request()
            request[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                n.correction_case(self.case(), request)
        with self.assertRaises(ValueError):
            n.correction_case(self.case('assistant-data-only-ru'), self.next_request())

    def test_retained_first_never_calls_provider_and_is_exact(self):
        retained = {'id': self.case()['id'], 'raw': 'original failure', 'status': 'failed', 'reservation_id': 24976}
        paid = mock.Mock()
        phase = n.first_phase(self.case(), retained, paid)
        paid.assert_not_called()
        self.assertEqual(phase['result'], retained)
        self.assertEqual(phase['origin'], 'retained_qualification2')
        self.assertEqual(retained['status'], 'failed')

    def test_new_first_calls_once_with_original_case(self):
        paid = mock.Mock(return_value={'raw': 'new'})
        phase = n.first_phase(self.case(), None, paid)
        paid.assert_called_once_with(self.case(), 'first')
        self.assertEqual(phase['origin'], 'new')

    def test_only_one_corrective_request_and_no_fallback_pass(self):
        for terminal in ({'status': 'needs_next'}, {'status': 'failed', 'deterministic_fallback': True},
                         {'status': 'functional_passed_pending_manual', 'deterministic_fallback': True},
                         {'status': 'functional_passed_pending_manual', 'deterministic_fallback': False,
                          'effects_preserved': False, 'final_text': 'answer', 'responses_consumed': 2}):
            with self.subTest(terminal=terminal), self.assertRaises(ValueError):
                n.require_terminal(terminal, 2)
        good = {'status': 'functional_passed_pending_manual', 'deterministic_fallback': False,
                'effects_preserved': True, 'final_text': 'answer', 'responses_consumed': 2,
                'requests': [{}, {}]}
        self.assertEqual(n.require_terminal(good, 2), 'answer')

    def test_original_ledger_row_cannot_be_rebased_or_double_charged(self):
        row = {'id': 24976, 'kind': 'chat', 'model': 'deepseek-v4.1-flash-1017',
               'tokens_in': 3540, 'tokens_out': 168, 'seconds': 1.38478078879416,
               'cost_usd': .0012636}
        retained = {'reservation_id': 24976, 'ledger': [row]}
        actual = dict(row, skill='model_retirement_20261029_qualification2')
        n.verify_carried_ledger([actual], retained, actual['skill'])
        for change in ({'id': 24977}, {'cost_usd': 0}, {'kind': 'unknown_chat'}, {'skill': 'other'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                n.verify_carried_ledger([dict(actual, **change)], retained, actual['skill'])

    def test_original_evidence_digest_refuses_tampering(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in n.ORIGINAL_FILES:
                (root / name).write_bytes(b'changed')
            with self.assertRaises(ValueError):
                n.original_evidence(root)

    def terminal(self, text='native final', count=2):
        return {'status': 'functional_passed_pending_manual', 'deterministic_fallback': False,
                'effects_preserved': True, 'final_text': text, 'responses_consumed': count,
                'requests': [{}] * count}

    def run_flow(self, case, responses, retained=None):
        repair = mock.Mock()
        repair.replay_with_responses.side_effect = responses
        paid = mock.Mock(side_effect=lambda selected, phase: {'raw': phase + ' response'})
        result = {}
        n.run_case(mock.Mock(), repair, mock.Mock(), None, None, Path('/frozen'),
                   Path('/output'), {}, case, retained, paid, result)
        return result, paid, repair

    def test_retained_failure_runs_only_exact_native_corrective_request(self):
        result, paid, repair = self.run_flow(self.case(),
            [{'status': 'needs_next', 'needs_next': self.next_request()}, self.terminal()],
            {'raw': 'retained first', 'status': 'failed'})
        paid.assert_called_once_with(n.correction_case(self.case(), self.next_request()), 'correction')
        self.assertEqual(repair.replay_with_responses.call_args_list[1].args[2],
                         ['retained first', 'correction response'])
        self.assertEqual(result['phases'][0]['result']['status'], 'failed')
        self.assertEqual(result['final_text'], 'native final')

    def test_native_first_pass_success_does_not_add_a_correction(self):
        result, paid, repair = self.run_flow(self.case(), [self.terminal(count=1)])
        paid.assert_called_once_with(self.case(), 'first')
        self.assertEqual(len(result['phases']), 1)
        self.assertEqual(result['status'], 'functional_passed_pending_semantic')

    def test_native_second_request_or_containment_cannot_start_third_call(self):
        for terminal in ({'status': 'needs_next'}, {'status': 'failed', 'deterministic_fallback': True}):
            repair = mock.Mock()
            repair.replay_with_responses.side_effect = [
                {'status': 'needs_next', 'needs_next': self.next_request()}, terminal]
            paid = mock.Mock(return_value={'raw': 'candidate'})
            with self.subTest(terminal=terminal), self.assertRaises(ValueError):
                n.run_case(None, repair, None, None, None, Path('/frozen'), Path('/output'), {},
                           self.case(), {'raw': 'retained'}, paid, {})
            self.assertEqual(paid.call_count, 1)

    def test_unsupported_case_uses_original_validator_without_repair(self):
        validator, repair, paid = mock.Mock(), mock.Mock(), mock.Mock(return_value={'raw': 'candidate'})
        validator.validate_response.side_effect = AssertionError('original second request refused')
        with self.assertRaisesRegex(AssertionError, 'original second request refused'):
            n.run_case(None, repair, validator, None, None, Path('/frozen'), Path('/output'), {},
                       self.case('assistant-data-only-ru'), None, paid, {})
        repair.replay_with_responses.assert_not_called()
        self.assertEqual(paid.call_count, 1)


@unittest.skipUnless(os.environ.get('CARA_NATIVEFLOW_ORIGINAL'), 'retained Linux evidence root not selected')
class RetainedNativeReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        original = n.original_evidence(Path(os.environ['CARA_NATIVEFLOW_ORIGINAL']))
        cls.plan, cls.retained, cls.inputs = n.check_original(original)
        cls.source = Path(cls.plan['source'])
        cls.q, cls.fixtures, cls.validator, _, cls.repair = n.load_frozen(cls.source, cls.plan)

    def test_actual_retained_failure_reconstructs_identical_native_repair_without_dispatch(self):
        case = self.inputs['cases'][0]
        overrides = self.inputs['prepared']['config_overrides']
        with redirect_stdout(io.StringIO()):
            first = self.repair.replay_with_responses(self.source, case['case'], [self.retained['raw']], overrides)
            repeated = self.repair.replay_with_responses(self.source, case['case'], [self.retained['raw']], overrides)
        self.assertEqual(first, repeated)
        self.assertEqual(first['status'], 'needs_next')
        self.assertEqual(first['responses_consumed'], 1)
        self.assertEqual(first['paid_calls'], 0)
        corrected = n.correction_case(case, first['needs_next'])
        self.assertEqual((corrected['max_tokens'], corrected['temperature'], corrected['timeout']), (320, .7, 60))

    def test_actual_native_guard_accepts_safe_fixture_and_rejects_containment(self):
        case = self.inputs['cases'][0]
        safe = 'The Budget review is planned. Next step: reminders.read, still pending. No successful execution is recorded.'
        for second, passes in ((safe, True), ('I saved a new note for you.', False)):
            result = {}
            paid = mock.Mock(return_value={'raw': second})
            with self.subTest(passes=passes), redirect_stdout(io.StringIO()):
                if passes:
                    n.run_case(self.q, self.repair, self.validator, self.fixtures, None, self.source,
                               Path('/unused'), self.inputs['prepared']['config_overrides'], case,
                               self.retained, paid, result)
                    self.assertEqual(result['final_text'], safe)
                else:
                    with self.assertRaisesRegex(ValueError, 'deterministic containment'):
                        n.run_case(self.q, self.repair, self.validator, self.fixtures, None, self.source,
                                   Path('/unused'), self.inputs['prepared']['config_overrides'], case,
                                   self.retained, paid, result)
            self.assertEqual(paid.call_count, 1)
        self.assertEqual(self.retained['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
