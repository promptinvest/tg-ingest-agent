"""Bounded existing corrective path, using retained synthetic model strings only."""
import copy
import os
import unittest
from unittest import mock

import prepare_cases as fixtures
import repair_cases

SOURCE = os.environ.get('CARA_TEST_SOURCE', 'C:/Users/okiri/OneDrive/Документы/projects/tg-ingest-agent')
RAW_ONE = ('Budget review is recorded as planned, and the next step, reading the saved reminders, is still pending. '
           'Nothing on it is marked done, and I can’t say it’s been done.\n\n'
           'The one reminder on file, Call Alice, isn’t recorded as linked to the Budget review, so')
RAW_THREE = ('1. Status: planned. The current step is still pending, and delivery is pending too.\n'
             '2. Next step: read the saved reminders. This hasn’t been done yet.')
# RAW_THREE is truthful after the approved attributive-phrase fix. Repair tests
# need a genuine false completion claim; preserve the retained answer verbatim.
UNSAFE_STRUCTURE_DRAFT = RAW_THREE + ' I saved a new reminder.'


class ExistingRepairPath(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prepared = fixtures.prepare(SOURCE)
        cls.cases = {c['id']: c for c in cls.prepared['cases']}

    def test_retained_first_answer_captures_actual_existing_corrective_prompt(self):
        case = self.cases['assistant-current-work-en']
        result = repair_cases.replay_with_responses(SOURCE, case, [RAW_ONE])
        self.assertEqual(result['status'], 'needs_next')
        self.assertEqual(result['responses_consumed'], 1)
        self.assertEqual(len(result['requests']), 2)
        self.assertEqual(result['first_request_sha256'], case['input_sha256'])
        self.assertTrue(result['effects_preserved'])
        self.assertEqual(result['paid_calls'], 0)
        self.assertEqual(result['needs_next']['profile'], 'converse_warm')
        corrective = result['needs_next']['messages'][-1]['content']
        self.assertIn('Your rejected draft', corrective)
        import common
        self.assertIn(common.neutralize_untrusted(RAW_ONE), corrective)

    def test_second_retained_answer_flows_through_original_guard_and_reply(self):
        case = self.cases['assistant-current-work-en']
        safe = 'The Budget review is planned. Next step: reminders.read, still pending. No successful execution is recorded.'
        result = repair_cases.replay_with_responses(SOURCE, case, [RAW_ONE, safe])
        self.assertEqual(result['status'], 'functional_passed_pending_manual')
        self.assertEqual(result['final_text'], safe)
        self.assertEqual(result['responses_consumed'], 2)
        self.assertEqual(result['replay_result']['task_status'], 'planned')
        self.assertEqual(result['replay_result']['step_statuses'], ['pending'])
        self.assertFalse(result['manual_quality_passed'])
        self.assertTrue(result['earlier_first_pass_verdict_unchanged'])

    def test_requested_two_item_structure_survives_original_repair(self):
        case = self.cases['assistant-requested-structure-en']
        safe = '1. Status: planned.\n2. Next step: reminders.read (pending).'
        result = repair_cases.replay_with_responses(SOURCE, case, [UNSAFE_STRUCTURE_DRAFT, safe])
        self.assertEqual(result['status'], 'functional_passed_pending_manual')
        self.assertEqual(result['final_text'], safe)

    def test_truthful_structure_answer_passes_native_first_reply(self):
        case = self.cases['assistant-requested-structure-en']
        result = repair_cases.replay_with_responses(SOURCE, case, [RAW_THREE])
        self.assertEqual(result['status'], 'functional_passed_pending_manual')
        self.assertEqual(result['final_text'], RAW_THREE)
        self.assertEqual(result['responses_consumed'], 1)
        self.assertEqual(len(result['requests']), 1)
        self.assertTrue(result['effects_preserved'])
        self.assertEqual(result['replay_result']['task_status'], 'planned')
        self.assertEqual(result['replay_result']['step_statuses'], ['pending'])
        self.assertEqual(result['paid_calls'], 0)
        self.assertEqual(result['telegram_messages'], 0)
        self.assertTrue(result['earlier_first_pass_verdict_unchanged'])

    def test_unsafe_structure_fixture_requires_actual_native_correction(self):
        case = self.cases['assistant-requested-structure-en']
        result = repair_cases.replay_with_responses(SOURCE, case, [UNSAFE_STRUCTURE_DRAFT])
        self.assertEqual(result['status'], 'needs_next')
        self.assertEqual(result['responses_consumed'], 1)
        self.assertEqual(len(result['requests']), 2)
        self.assertEqual(result['first_request_sha256'], case['input_sha256'])
        self.assertEqual(result['needs_next']['profile'], 'converse_warm')
        self.assertTrue(result['effects_preserved'])
        self.assertEqual(result['paid_calls'], 0)
        self.assertEqual(result['telegram_messages'], 0)
        corrective = result['needs_next']['messages'][-1]['content']
        self.assertIn('Your rejected draft', corrective)
        import common
        self.assertIn(common.neutralize_untrusted(UNSAFE_STRUCTURE_DRAFT), corrective)

    def test_completed_work_and_ambiguous_delivery_remain_distinct_in_third_case(self):
        case = self.cases['assistant-uncertain-delivery-en']
        safe = 'The Budget review task is recorded as completed. Delivery of its result is uncertain, so receipt is not confirmed.'
        result = repair_cases.replay_with_responses(SOURCE, case, [safe])
        self.assertEqual(result['status'], 'functional_passed_pending_manual')
        self.assertEqual(result['replay_result']['task_status'], 'completed')
        self.assertEqual(result['replay_result']['step_statuses'], ['succeeded'])
        self.assertEqual(result['replay_result']['delivery_status'], 'ambiguous')

    def test_final_containment_fallback_does_not_qualify_as_task_status(self):
        case = self.cases['assistant-current-work-en']
        result = repair_cases.replay_with_responses(SOURCE, case, [RAW_ONE, 'I saved a new note for you.'])
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(result['deterministic_fallback'])
        self.assertFalse(result['qualification_passed'])
        self.assertIn('does not answer', result['semantic_failure'])
        self.assertTrue(result['effects_preserved'])

    def test_repair_may_not_drop_requested_structure(self):
        case = self.cases['assistant-requested-structure-en']
        with self.assertRaisesRegex(AssertionError, 'exactly two requested numbered items'):
            repair_cases.replay_with_responses(SOURCE, case, [UNSAFE_STRUCTURE_DRAFT, 'Status: planned. Next step: reminders.read, pending.'])

    def test_third_response_is_refused_without_replay(self):
        case = self.cases['assistant-current-work-en']
        with self.assertRaisesRegex(AssertionError, 'one or two bounded'):
            repair_cases.replay_with_responses(SOURCE, case, [RAW_ONE, 'safe', 'third'])

    def test_a_third_actual_gateway_request_is_refused(self):
        import llm, tg_ingest_agent
        case = self.cases['assistant-current-work-en']
        original = tg_ingest_agent.Agent._honest_action_reply

        def unexpected_extra(agent, *args, **kwargs):
            original(agent, *args, **kwargs)
            return llm.chat_profile(agent.cfg, agent.conn, 'converse',
                [{'role': 'user', 'content': 'Forbidden third request'}], profile='converse_warm')

        with mock.patch.object(tg_ingest_agent.Agent, '_honest_action_reply', unexpected_extra):
            with self.assertRaisesRegex(AssertionError, 'third model request forbidden'):
                repair_cases.replay_with_responses(SOURCE, case, [RAW_ONE, 'Status: planned. Next step: reminders.read (pending).'])

    def test_frozen_seed_order_cannot_introduce_new_life_facts(self):
        case = copy.deepcopy(self.cases['assistant-current-work-en'])
        case['messages'][0]['content'] = case['messages'][0]['content'].replace(
            '- По выходным гуляешь по блошиным рынкам и старым книжным.', '- Invented fixture life claim.')
        case['input_sha256'] = fixtures.digest(case['messages'])
        with self.assertRaisesRegex(AssertionError, 'exact synthetic seed facts'):
            repair_cases.replay_with_responses(SOURCE, case, [RAW_ONE])

    def test_retained_first_input_tampering_is_rejected(self):
        case = copy.deepcopy(self.cases['assistant-current-work-en'])
        case['messages'][-1]['content'] += ' altered'
        with self.assertRaisesRegex(AssertionError, 'retained first request changed'):
            repair_cases.replay_with_responses(SOURCE, case, [RAW_ONE])

    def test_changed_case_binding_is_rejected(self):
        case = copy.deepcopy(self.cases['assistant-current-work-en'])
        case['recipe']['id'] = 'assistant-requested-structure-en'
        with self.assertRaisesRegex(AssertionError, 'case/profile binding'):
            repair_cases.replay_with_responses(SOURCE, case, [RAW_ONE])

    def test_same_second_prompt_is_rebuilt_without_replaying_paid_operation(self):
        case = self.cases['assistant-current-work-en']
        first = repair_cases.replay_with_responses(SOURCE, case, [RAW_ONE])
        second = repair_cases.replay_with_responses(SOURCE, case, [RAW_ONE])
        self.assertEqual(first['needs_next']['messages'], second['needs_next']['messages'])
        self.assertEqual(first['needs_next']['input_sha256'], second['needs_next']['input_sha256'])
        self.assertEqual(second['paid_calls'], 0)


if __name__ == '__main__':
    unittest.main()
