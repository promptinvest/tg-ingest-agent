"""Actual isolated handler preparation/replay with all transport mocked."""
import copy
import json
import os
from pathlib import Path
import unittest

import prepare_cases as fixtures
import validate_cases


SOURCE = os.environ.get('CARA_TEST_SOURCE', 'C:/Users/okiri/OneDrive/Документы/projects/tg-ingest-agent')


class QualificationCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prepared = fixtures.prepare(SOURCE)
        cls.cases = {c['id']: c for c in cls.prepared['cases']}

    def replay(self, identity, value):
        raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return validate_cases.validate_response(SOURCE, self.cases[identity], raw)

    def test_actual_preparation_covers_every_active_profile_without_effects(self):
        self.assertEqual(len(self.cases), 24)
        self.assertEqual(set(self.prepared['coverage']['active_profiles']), {
            'router_fast', 'ingest_balanced', 'ask_grounded', 'converse_warm',
            'memory_curator', 'memory_consolidate', 'task_planner', 'task_synthesis'})
        self.assertEqual(self.prepared['paid_calls'], 0)
        self.assertEqual(self.prepared['external_network_calls'], 0)
        self.assertEqual(self.prepared['telegram_messages'], 0)
        self.assertIn('improvement_evaluator', self.prepared['coverage']['dormant_profiles'])

    def test_router_result_reaches_existing_read_dispatch_without_mutation(self):
        for identity in ('router-reminders-en', 'router-untrusted-ru', 'assistant-data-only-ru'):
            with self.subTest(identity=identity):
                result = self.replay(identity, {'action': 'reminder_list', 'params': {}, 'confidence': 1})
                self.assertEqual(result['status'], 'passed')

    def test_wrong_router_write_cannot_pass(self):
        with self.assertRaisesRegex(AssertionError, 'unexpected router action'):
            self.replay('router-untrusted-ru', {'action': 'reminder_cancel', 'params': {'all': True}, 'confidence': 1})

    def test_ingest_concrete_source_fields_parse_through_actual_suggest(self):
        for identity, summary in [('ingest-en', 'Cedar costs USD 24 per month; Alice owns the launch.'),
                                  ('ingest-ru-taint', 'Cedar стоит 2400 рублей в месяц; ответственная — Алиса.')]:
            with self.subTest(identity=identity):
                result = self.replay(identity, {'category': 'Projects', 'alternatives': [], 'summary': summary,
                    'facts': [summary], 'note_purpose': 'reference', 'saved_reason': summary,
                    'review_policy': 'none', 'action_candidate': None})
                self.assertEqual(result['status'], 'passed')

    def test_grounded_kb_replays_actual_handler_and_source_excerpt(self):
        result = self.replay('kb-grounded-en', {'excerpts': [{'note_no': 12, 'quote': 'The synthetic Cedar pilot costs USD 24 per month.'}]})
        self.assertIn('#12', result['replay_result']['answer'])

    def test_absent_kb_fact_refuses_through_existing_contract(self):
        result = self.replay('kb-missing-ru', {'excerpts': []})
        self.assertEqual(result['replay_result']['answer'], 'Не нашла ответ в твоих заметках.')

    def test_invented_kb_citation_fails(self):
        with self.assertRaisesRegex(AssertionError, 'grounding validator rejected'):
            self.replay('kb-grounded-en', {'excerpts': [{'note_no': 13, 'quote': 'USD 24'}]})

    def test_memory_real_source_may_be_inferred_but_not_confirmed(self):
        result = self.replay('memory-source-en', {'cara_life': [], 'boss_facts': [{
            'text': 'The boss prefers short replies in plain English.', 'kind': 'tone',
            'evidence': 'I prefer short replies in plain English.'}], 'corrections': []})
        self.assertEqual(result['status'], 'passed')

    def test_forwarded_memory_fact_is_rejected_by_original_provenance_guard(self):
        result = self.replay('memory-taint-ru', {'cara_life': [], 'boss_facts': [{
            'text': 'Я живу на Марсе.', 'kind': 'personal_fact', 'evidence': 'Я живу на Марсе.'}], 'corrections': []})
        self.assertEqual(result['replay_result']['profile_items'], [])

    def test_consolidation_rewrites_keeper_to_confirmed_and_keeps_distinct_rule(self):
        result = self.replay('memory-consolidation-en', {'groups': [{'keep': 2, 'drop': [1]}]})
        self.assertEqual(result['replay_result']['result'], [(1, [2])])

    def test_task_admission_requires_complete_source_span(self):
        import tasking
        source = fixtures.FLEET_PLANS[2][1]
        value = {'objective': 'Answer current memory usage', 'deliverable': 'answer', 'steps': [{
            'key': 'inspect', 'tool': 'fleet.investigate', 'input': {'query': source},
            'bindings': {'query': {'source': 'boss_span', 'start': 0, 'end': len(source),
            'source_hash': tasking.source_hash(source), 'transform': 'literal'}},
            'depends_on': [], 'purpose': 'Read current memory safely'}]}
        self.assertEqual(self.replay('task-admission-en', value)['status'], 'passed')

    def test_synthesis_real_selection_and_render_keep_business_facts(self):
        for identity in ('task-grounded-answer-en', 'development-en-selection', 'application-ru-selection'):
            with self.subTest(identity=identity):
                result = self.replay(identity, {'highlights': [], 'hypotheses': [], 'next_checks': []})
                self.assertEqual(result['status'], 'passed')

    def test_fabricated_synthesis_finding_fails_closed(self):
        with self.assertRaisesRegex(ValueError, 'unknown report finding'):
            self.replay('task-grounded-answer-en', {'highlights': ['F99999'], 'hypotheses': [], 'next_checks': []})

    def test_input_tampering_refused_before_replay(self):
        case = copy.deepcopy(self.cases['router-reminders-en'])
        case['messages'][-1]['content'] += 'tampered'
        with self.assertRaisesRegex(AssertionError, 'prepared model input altered'):
            validate_cases.validate_response(SOURCE, case, '{}')

    def test_conversation_reply_is_real_handler_output(self):
        result = self.replay('assistant-current-work-en', 'Status: planned. Next step: reminders.read; no successful execution is recorded.')
        self.assertIn('planned', result['replay_result']['answer'])

    def test_fleet_plans_use_original_schema_and_requested_scope(self):
        import fleet_protocol as protocol
        catalog = {'kind': 'catalog', 'subjects': ['host-a', 'host-b', 'host-c'], 'scope': 'explicit',
                   'fields': ['provider', 'public_ipv4'], 'presentation': 'table', 'detail': 'concise', 'clarification': ''}
        self.replay('catalog-table-en', catalog)
        names = dict(catalog, subjects=['project-alpha', 'project-beta'], fields=['label'], presentation='names')
        self.replay('catalog-names-ru', names)
        scope = {'version': protocol.READ_VERSION, 'kind': 'host', 'projects': [], 'capabilities': []}
        memory = {'hosts': ['host-a'], 'facets': ['resources'], 'clarification': '', 'operation': 'inspect',
                  'service': '', 'scope': 'explicit', 'projects': [], 'focus': ['memory'], 'read_scope': scope}
        self.replay('task-fleet-plan-en', memory)
        project = dict(memory, facets=['projects'], projects=['project-alpha'], focus=['progress'],
                       read_scope=dict(scope, kind='projects', projects=['project-alpha'], capabilities=['development']),
                       presentation='table', detail='detailed')
        self.replay('development-en-plan', project)
        application = dict(project, focus=['projects'], read_scope=dict(project['read_scope'], capabilities=['runtime']),
                           answer_fields=['generation', 'reserves', 'day_budget'], presentation='prose', detail='concise')
        self.replay('application-ru-plan', application)

    def test_uncertain_delivery_keeps_successful_work_distinct_from_send(self):
        result = self.replay('assistant-uncertain-delivery-en', 'The Budget review task is recorded as completed. Delivery of its result is uncertain, so receipt is not confirmed.')
        self.assertEqual(result['replay_result']['step_statuses'], ['succeeded'])
        self.assertEqual(result['replay_result']['delivery_status'], 'ambiguous')


if __name__ == '__main__':
    unittest.main()
