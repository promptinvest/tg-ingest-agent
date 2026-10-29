"""Pure evidence rejection tests; no model, database, subprocess or service work."""
import copy
import unittest
from unittest.mock import patch

import fullflow_gate as gate
import native_backend as native


class FullFlowGateTests(unittest.TestCase):
    def fixture(self):
        source = {name: '1' * 64 for name in gate.REQUIRED_FROZEN_FILES}
        source['agent.py'] = '2' * 64
        binding = {'source_commit': gate.APP_SOURCE, 'archive_sha256': gate.APP_ARCHIVE,
                   'candidate_environment_sha256': '3' * 64}
        cases = []
        for index in range(43):
            base = 'assistant-current-work-en' if index == 0 else 'synthetic-' + str(index)
            model = gate.TEXT_FLASH if index < 37 else gate.VISION
            identity = base + '__' + model if index < 37 else base
            profile = 'converse_warm' if index == 0 else 'router_fast' if index < 37 else 'vision'
            messages = [{'role': 'user', 'content': 'Synthetic ' + str(index)}]
            cases.append({'id': identity, 'kind': 'text' if index < 37 else 'vision',
                'profile': profile, 'model': model, 'messages': messages,
                'input_sha256': gate.digest(messages), 'max_tokens': 320,
                'temperature': .7, 'timeout': 60,
                'case': {'id': base, 'profile': profile, 'caller_kwargs': {'profile': profile},
                         'runtime_profile': {'max_tokens': 320, 'temperature': .7, 'timeout': 60}}})
        inputs = {'cases': cases}
        coverage = gate.coverage(cases)
        original_plan = {'schema': 'cara.model-retirement-prepared/v1', **binding,
            'source_hashes': copy.deepcopy(source), 'coverage': coverage, 'cases_expected': 43,
            'skill': gate.PREFIX + 'attempt2', 'migration_skill_prefix': gate.PREFIX,
            'qualification_inputs_sha256': gate.digest(inputs), 'aggregate_ceiling_usd': .25,
            'per_call_ceiling_usd': .15}
        def result(case, reservation, raw, input_hash=None):
            response = {'model': case['model'], 'choices': [{'message': {'content': raw}, 'finish_reason': 'stop'}],
                        'usage': {'prompt_tokens': 10, 'completion_tokens': 5}}
            return {'id': case['id'], 'profile': case['profile'], 'model': case['model'],
                'input_sha256': input_hash or case['input_sha256'], 'reservation_id': reservation,
                'raw': raw, 'raw_sha256': gate.sha(raw.encode()), 'provider_response': response,
                'provider_usage': response['usage'], 'provider_model': case['model'], 'finish_reason': 'stop'}
        first = result(cases[0], 24976, 'Retained first raw')
        first.update(status='failed', error_type='AssertionError', error_detail=gate.FIRST_FAILURE)
        old_row = {'id': 24976, 'skill': original_plan['skill'], 'kind': 'chat', 'model': cases[0]['model'],
                   'tokens_in': 10, 'tokens_out': 5, 'seconds': .1, 'cost_usd': .0012636}
        first['ledger'] = [{key: value for key, value in old_row.items() if key != 'skill'}]
        original_raw = {'schema': 'cara.model-retirement-raw/v1', **binding, 'status': 'failed',
            'cases': [first], 'cases_completed': 1, 'cases_expected': 43,
            'usage': first['ledger'], 'migration_usage': [old_row], 'unknown_usage_ids': [],
            'migration_unknown_usage_ids': [], 'plan_sha256': gate.digest(original_plan),
            'qualification_inputs_sha256': gate.digest(inputs)}
        pins = {'original_plan': gate.digest(original_plan), 'inputs': gate.digest(inputs),
                'original_raw': gate.digest(original_raw), 'original_case': gate.digest(first)}
        evidence = {'plan': pins['original_plan'], 'inputs': pins['inputs'],
                    'raw': pins['original_raw'], 'case': pins['original_case']}
        prepared = {'schema': 'cara.model-retirement-nativeflow-prepared/v1', **binding,
            'qualification_mode': gate.MODE, 'source_hashes': copy.deepcopy(source),
            'qualification_inputs_sha256': pins['inputs'], 'coverage': coverage, 'cases_expected': 43,
            'original_evidence': evidence, 'original_first_pass_status': 'failed',
            'skill': gate.PREFIX + 'nativeflow1', 'migration_skill_prefix': gate.PREFIX,
            'retained_skill': original_plan['skill'], 'new_first_calls_expected': 42, 'max_native_corrections': 6,
            'operator_source_hashes': {'qualify_nativeflow.py': '4' * 64},
            'aggregate_ceiling_usd': .25, 'per_call_ceiling_usd': .15}
        release = {'identity': {key: binding[key] for key in ('source_commit', 'archive_sha256')},
            'candidate_environment_sha256': binding['candidate_environment_sha256'],
            'qualification_mode': gate.MODE, 'package_sha256s': {'qualify_nativeflow.py': '4' * 64}}
        outputs, fresh = [], []
        for index, case in enumerate(cases):
            first_result = copy.deepcopy(first) if index == 0 else result(case, 25000 + index, 'Fresh raw ' + str(index))
            request = {key: case[key] for key in ('messages', 'input_sha256', 'max_tokens', 'temperature', 'timeout')}
            phases = [{'phase': 'first', 'origin': 'retained_qualification2' if index == 0 else 'new',
                       'request': request, 'result': first_result}]
            if index:
                fresh.append({'id': 25000 + index, 'skill': prepared['skill'], 'kind': 'chat',
                              'model': case['model'], 'tokens_in': 10, 'tokens_out': 5, 'seconds': .1, 'cost_usd': .0001})
            first_replay = None
            if index == 0:
                correction_messages = request['messages'] + [{'role': 'user', 'content': 'Native corrective prompt'}]
                second_request = {**request, 'messages': correction_messages, 'input_sha256': gate.digest(correction_messages)}
                second = result(case, 26000, 'Final safe answer', second_request['input_sha256'])
                phases.append({'phase': 'correction', 'origin': 'new', 'request': second_request, 'result': second})
                fresh.append({'id': 26000, 'skill': prepared['skill'], 'kind': 'chat', 'model': case['model'],
                              'tokens_in': 10, 'tokens_out': 5, 'seconds': .1, 'cost_usd': .0001})
                requests = [{'index': n, 'messages': phase['request']['messages'], 'profile': case['profile'],
                    'skill': 'converse', 'kwargs': {'profile': case['profile']},
                    'input_sha256': phase['request']['input_sha256'],
                    'retained_response_sha256': phase['result']['raw_sha256']} for n, phase in enumerate(phases)]
                functional = {'schema': 'cara-model-switch-repair-replay/v1', 'case_id': case['case']['id'],
                    'status': 'functional_passed_pending_manual', 'first_request_sha256': case['input_sha256'],
                    'requests': requests, 'retained_response_sha256s': [phase['result']['raw_sha256'] for phase in phases],
                    'responses_consumed': 2, 'source_python_hashes': {name: value for name, value in source.items() if '/' not in name and name.endswith('.py')},
                    'effects_preserved': True, 'paid_calls': 0, 'telegram_messages': 0,
                    'earlier_first_pass_verdict_unchanged': True, 'manual_quality_passed': False,
                    'qualification_passed': False, 'retained_seed_order_reconstructed': True,
                    'frozen_fixture_utc': '2026-10-29T00:00:00+00:00',
                    'deterministic_fallback': False, 'final_text': second['raw'],
                    'replay_result': {'reminders_unchanged': True, 'answer': second['raw']}, 'protected_before_sha256': '5' * 64}
                first_replay = copy.deepcopy(functional)
                first_replay.update(status='needs_next', final_text=None, qualification_passed=False,
                    responses_consumed=1, retained_response_sha256s=[first['raw_sha256']])
                first_replay['requests'][1].pop('retained_response_sha256')
                first_replay['needs_next'] = copy.deepcopy(first_replay['requests'][1])
            elif case['kind'] == 'text':
                functional = {'id': case['case']['id'], 'status': 'passed', 'profile': case['profile'],
                    'raw_sha256': first_result['raw_sha256'], 'prepared_input_sha256': case['input_sha256'],
                    'replay_boundary': 'synthetic', 'replay_result': {'passed': True},
                    'manual_quality_required': True, 'external_effects': 0, 'paid_calls': 0}
            else:
                functional = {'status': 'passed', 'manual_quality_required': True, 'native_result': ['synthetic']}
            final = functional['final_text'] if index == 0 else first_result['raw']
            outputs.append({'id': case['id'], 'profile': case['profile'], 'model': case['model'],
                'input_sha256': case['input_sha256'], 'phases': phases, 'replay_first': first_replay,
                'functional': functional, 'final_text': final, 'final_text_sha256': gate.sha(final.encode()),
                'status': 'functional_passed_pending_semantic'})
        fresh.sort(key=lambda row: row['id'])
        migration = [old_row, *copy.deepcopy(fresh)]
        fresh = [{key: value for key, value in row.items() if key != 'skill'} for row in fresh]
        prepared['retained_replay_sha256'] = gate.digest(outputs[0]['replay_first'])
        raw = {'schema': 'cara.model-retirement-nativeflow-raw/v1', **binding,
            'qualification_mode': gate.MODE, 'status': 'functional_passed_pending_semantic',
            'original_first_pass_status': 'failed', 'original_evidence': evidence,
            'plan_sha256': gate.digest(prepared), 'qualification_inputs_sha256': pins['inputs'],
            'coverage': coverage, 'expected_coverage': coverage, 'cases_expected': 43, 'cases_completed': 43,
            'cases': outputs, 'usage': fresh, 'new_usage': fresh, 'migration_usage': migration,
            'migration_skill_prefix': gate.PREFIX, 'aggregate_ceiling_usd': .25, 'per_call_ceiling_usd': .15,
            'accounted_cost_usd': sum(row['cost_usd'] for row in fresh),
            'migration_accounted_cost_usd': sum(row['cost_usd'] for row in [old_row, *fresh]),
            'unknown_usage_ids': [], 'migration_unknown_usage_ids': [], 'unknown_reserve_usd': 0,
            'migration_unknown_reserve_usd': 0, 'messages_sent': 0, 'live_business_data_changed': False}
        hashes = {**pins, 'plan': gate.digest(prepared), 'raw': gate.digest(raw)}
        semantic = {'schema': 'cara.model-retirement-nativeflow-semantic/v1', **binding,
            'qualification_mode': gate.MODE, 'status': 'passed', 'plan_sha256': hashes['plan'],
            'raw_sha256': hashes['raw'], 'inputs_sha256': hashes['inputs'],
            'cases': [{'id': case['id'], 'status': 'passed', 'reason': 'Reviewed synthetic outcome',
                'final_text_sha256': case['final_text_sha256'], 'functional_sha256': gate.digest(case['functional']),
                'phase_raw_sha256s': [phase['result']['raw_sha256'] for phase in case['phases']]} for case in outputs]}
        return [release, prepared, inputs, raw, semantic, hashes, original_plan, original_raw, first, source], pins

    def verify(self, data, pins):
        with patch.object(gate, 'ORIGINAL_PINS', pins):
            return gate.verify_fullflow(*data)

    def test_full_native_correction_accounts_carried_first_and_every_new_call(self):
        data, pins = self.fixture()
        result = self.verify(data, pins)
        self.assertEqual(result['cases'], 43)
        self.assertEqual(result['carried_calls'], 1)
        self.assertEqual(result['new_calls'], 43)
        self.assertEqual(result['corrective_calls'], 1)

    def make_one_response(self, result):
        result['phases'] = result['phases'][:1]
        first = result['phases'][0]['result']
        terminal = result['functional']
        terminal.update(responses_consumed=1, requests=terminal['requests'][:1],
                        retained_response_sha256s=[first['raw_sha256']], final_text=first['raw'])
        terminal['replay_result']['answer'] = first['raw']
        result.update(final_text=first['raw'], final_text_sha256=first['raw_sha256'],
                      replay_first=copy.deepcopy(terminal))

    def test_untouched_supported_case_can_pass_without_a_correction(self):
        data, _ = self.fixture()
        case, result = copy.deepcopy(data[2]['cases'][0]), copy.deepcopy(data[3]['cases'][0])
        self.make_one_response(result)
        case['case']['id'] = 'assistant-uncertain-delivery-en'
        for proof in (result['functional'], result['replay_first']):
            proof['case_id'] = case['case']['id']
        gate._replay(case, result, result['functional']['source_python_hashes'])

    def test_retained_failure_cannot_become_a_self_consistent_firstpass_success(self):
        data, pins = self.fixture()
        prepared, raw, semantic, hashes = data[1], data[3], data[4], data[5]
        self.make_one_response(raw['cases'][0])
        prepared['retained_replay_sha256'] = gate.digest(raw['cases'][0]['replay_first'])
        raw['usage'] = raw['new_usage'] = [row for row in raw['usage'] if row['id'] != 26000]
        raw['migration_usage'] = [row for row in raw['migration_usage'] if row['id'] != 26000]
        raw['accounted_cost_usd'] = sum(row['cost_usd'] for row in raw['usage'])
        raw['migration_accounted_cost_usd'] = sum(row['cost_usd'] for row in raw['migration_usage'])
        final = raw['cases'][0]
        semantic['cases'][0].update(final_text_sha256=final['final_text_sha256'],
            phase_raw_sha256s=[final['phases'][0]['result']['raw_sha256']],
            functional_sha256=gate.digest(final['functional']))
        raw['plan_sha256'] = semantic['plan_sha256'] = hashes['plan'] = gate.digest(prepared)
        semantic['raw_sha256'] = hashes['raw'] = gate.digest(raw)
        with self.assertRaisesRegex(ValueError, 'retained failed first answer'):
            self.verify(data, pins)

    def test_fail_closed_evidence_mutations(self):
        mutations = {
            'mode': lambda d: d[0].update(qualification_mode='first_pass'),
            'old failure reclassified': lambda d: d[8].update(status='passed'),
            'retained first changed': lambda d: d[3]['cases'][0]['phases'][0]['result'].update(raw='changed'),
            'fresh original rerun': lambda d: d[3]['cases'][0]['phases'][0].update(origin='new'),
            'third call': lambda d: d[3]['cases'][0]['phases'].append(copy.deepcopy(d[3]['cases'][0]['phases'][1])),
            'replacement prompt': lambda d: d[3]['cases'][0]['phases'][1]['request'].update(messages=[]),
            'raised cap': lambda d: d[3]['cases'][0]['phases'][1]['request'].update(max_tokens=640),
            'deterministic containment': lambda d: d[3]['cases'][0]['functional'].update(deterministic_fallback=True),
            'changed native builder': lambda d: d[1]['source_hashes'].update({'converse_svc.py': 'changed'}),
            'missing case': lambda d: d[3]['cases'].pop(),
            'unknown charge': lambda d: d[3]['migration_usage'][-1].update(kind='unknown_chat'),
            'unbound usage': lambda d: d[3]['new_usage'].append(copy.deepcopy(d[3]['new_usage'][0])),
            'double debit': lambda d: d[3]['cases'][1]['phases'][0]['result'].update(reservation_id=24976),
            'semantic final mismatch': lambda d: d[4]['cases'][0].update(final_text_sha256='different'),
            'semantic functional mismatch': lambda d: d[4]['cases'][0].update(functional_sha256='different'),
            'missing final semantic': lambda d: d[4]['cases'].pop(),
            'changed retained replay': lambda d: d[1].update(retained_replay_sha256='changed'),
            'provider usage omitted': lambda d: d[3]['cases'][2]['phases'][0]['result']['provider_usage'].pop('completion_tokens'),
            'provider raw changed': lambda d: d[3]['cases'][2]['phases'][0]['result']['provider_response']['choices'][0]['message'].update(content='changed'),
            'current unknown reserve': lambda d: d[3].update(unknown_reserve_usd=.01),
            'call ceiling': lambda d: d[1].update(per_call_ceiling_usd=.20),
            'migration ceiling': lambda d: d[1].update(aggregate_ceiling_usd=.50),
            'reordered cases': lambda d: d[3]['cases'].reverse(),
            'effects changed': lambda d: d[3]['cases'][0]['functional'].update(effects_preserved=False),
            'source replay changed': lambda d: d[3]['cases'][0]['functional']['source_python_hashes'].update({'llm.py': '0' * 64}),
            'vision native result omitted': lambda d: d[3]['cases'][-1]['functional'].pop('native_result'),
        }
        for name, mutation in mutations.items():
            data, pins = self.fixture()
            mutation(data)
            with self.subTest(name=name), self.assertRaises((ValueError, KeyError)):
                self.verify(data, pins)


class DispatchTests(unittest.TestCase):
    def fixture(self, full=False):
        roles = ('plan', 'inputs', 'raw', 'original_plan', 'original_raw', 'original_case') if full else ('plan', 'inputs', 'raw')
        model = {'evidence_roles': {name: name + '.json' for name in roles},
                 'evidence_files': {name + '.json': name + '-sha' for name in roles}}
        semantic = {'evidence_roles': {'review': 'semantic.json'}}
        plan = {'identity': {'source_commit': gate.APP_SOURCE}, 'package_sha256s': {'fullflow_gate.py': '1' * 64}}
        evidence = {'model_qualification': model, 'semantic_review': semantic}
        if full:
            plan.update(qualification_mode=gate.MODE, operator_source_commit='a' * 40)
            model['qualification_mode'] = semantic['qualification_mode'] = gate.MODE
            for name in ('source_review', 'operator_qualification'):
                evidence[name] = {'operator_source_commit': 'a' * 40,
                                  'operator_package_sha256': gate.digest(plan['package_sha256s'])}
        return plan, evidence, lambda envelope, filename: filename, {'archive': 'source'}

    def test_existing_firstpass_dispatch_is_unchanged_and_does_not_import_companion(self):
        with patch.object(native, 'verify_qualification', return_value='original') as legacy:
            self.assertEqual(native.verify_model_gates(*self.fixture()), 'original')
        self.assertEqual(legacy.call_args.args[1:5], ('plan.json', 'inputs.json', 'raw.json', 'semantic.json'))
        self.assertEqual(native.qualification_helpers({}), set())

    def test_explicit_fullflow_dispatch_passes_original_lineage_and_archive_map(self):
        data = self.fixture(True)
        calls = []
        result = native.verify_model_gates(*data, fullflow_validator=lambda *args: calls.append(args) or 'full')
        self.assertEqual(result, 'full')
        self.assertEqual(calls[0][-4:], ('original_plan.json', 'original_raw.json', 'original_case.json', data[3]))
        self.assertEqual(native.qualification_helpers(data[0]), {'fullflow_gate.py', 'qualify_nativeflow.py'})

    def test_unknown_mode_or_mixed_mode_rejected(self):
        with self.assertRaises(ValueError):
            native.qualification_helpers({'qualification_mode': 'nativeflow'})
        data = self.fixture(True)
        data[1]['semantic_review'].pop('qualification_mode')
        with self.assertRaisesRegex(ValueError, 'modes differ'):
            native.verify_model_gates(*data, fullflow_validator=lambda *args: self.fail('must not dispatch'))

    def test_fullflow_requires_separate_reviewed_operator_package(self):
        for field in ('operator_source_commit', 'operator_package_sha256'):
            for name in ('source_review', 'operator_qualification'):
                data = self.fixture(True)
                data[1][name][field] = 'changed'
                with self.subTest(field=field, name=name), self.assertRaisesRegex(ValueError, 'package binding'):
                    native.verify_model_gates(*data, fullflow_validator=lambda *args: self.fail('must not dispatch'))
        data = self.fixture(True)
        data[0]['operator_source_commit'] = gate.APP_SOURCE
        with self.assertRaisesRegex(ValueError, 'distinct qualified operator'):
            native.verify_model_gates(*data, fullflow_validator=lambda *args: self.fail('must not dispatch'))

    def test_fullflow_cannot_dispatch_without_original_evidence_or_authenticated_validator(self):
        data = self.fixture(True)
        with self.assertRaisesRegex(ValueError, 'authenticated nativeflow'):
            native.verify_model_gates(*data)
        data[1]['model_qualification']['evidence_roles'].pop('original_case')
        with self.assertRaisesRegex(ValueError, 'role bindings'):
            native.verify_model_gates(*data, fullflow_validator=lambda *args: self.fail('must not dispatch'))


if __name__ == '__main__':
    unittest.main()
