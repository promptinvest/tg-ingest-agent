"""Synthetic mutation checks for retained-prefix qualification; no paid operations."""
import copy
import unittest
from unittest.mock import patch

import retirement93_gate as gate
import test_fullflow_gate as fixtures


class Retirement93GateTests(unittest.TestCase):
    def fixture(self):
        # Reuse the existing synthetic provider/native replay builder, then create
        # this contract's independent eight-response and ten-spend-row lineage.
        data, _ = fixtures.FullFlowGateTests().fixture()
        release, prepared, inputs, raw, semantic, hashes, original, previous, _, source = data
        cases, outputs = inputs['cases'], raw['cases']
        cases[0], cases[7] = cases[7], cases[0]
        outputs[0], outputs[7] = outputs[7], outputs[0]
        binding = {'source_commit': gate.APP_SOURCE, 'archive_sha256': gate.APP_ARCHIVE,
                   'candidate_environment_sha256': '3' * 64}
        for value in (prepared, raw, semantic, original, previous):
            value.update(binding)
        release['identity'].update({k: binding[k] for k in ('source_commit', 'archive_sha256')})
        release['qualification_mode'] = gate.MODE
        release['package_sha256s'] = {'qualify_retirement93.py': '4' * 64}
        source['ops/model-retirement/qualify_models.py'] = gate.Q_SHA
        prepared['source_hashes'] = original['source_hashes'] = copy.deepcopy(source)
        source_python = {k: v for k, v in source.items() if '/' not in k and k.endswith('.py')}
        old_usage, retained = [], []
        new_usage = []
        for index, (case, result) in enumerate(zip(cases, outputs)):
            if index < 8:
                identity = gate.RETAINED_IDS[index]
                base, model = identity.split('__')
                profile = 'router_fast' if base == 'assistant-data-only-ru' else 'converse_warm'
                case.update(id=identity, profile=profile, model=model)
                case['case'].update(id=base, profile=profile, caller_kwargs={'profile': profile})
            for field in ('id', 'model', 'profile', 'input_sha256'):
                result[field] = case[field]
            for phase in result['phases']:
                response = phase['result']
                for field in ('id', 'model', 'profile'):
                    response[field] = case[field]
                response['provider_model'] = response['provider_response']['model'] = case['model']
            first = result['phases'][0]['result']
            if index < 8:
                first['reservation_id'] = 25062 + index
                row = {'id': first['reservation_id'], 'kind': 'chat', 'model': case['model'],
                       'tokens_in': 10, 'tokens_out': 5, 'seconds': .1, 'cost_usd': .0106594 / 8}
                old_usage.append(row)
                first['ledger'] = copy.deepcopy(old_usage)
                result['phases'][0]['origin'] = 'retained_qualification3'
                if index < 7:
                    first['status'] = 'functional_passed_pending_semantic'
                    first['functional'] = result['functional']
                else:
                    first.update(status='failed', error_type='AssertionError', error_detail=gate.FIRST_FAILURE)
                retained.append(copy.deepcopy(first))
            if index == 7:
                for proof in (result['replay_first'], result['functional']):
                    proof['case_id'] = case['case']['id']
                    proof['source_python_hashes'] = source_python
                    proof['requests'][0]['profile'] = proof['requests'][1]['profile'] = case['profile']
                    proof['requests'][0]['kwargs'] = proof['requests'][1]['kwargs'] = {'profile': case['profile']}
                result['replay_first']['needs_next'] = copy.deepcopy(result['replay_first']['requests'][1])
            elif case['kind'] == 'text':
                result['functional'].update(id=case['case']['id'], profile=case['profile'])
                if index < 7:
                    retained[index]['functional'] = copy.deepcopy(result['functional'])
                    result['phases'][0]['result']['functional'] = copy.deepcopy(result['functional'])
            for phase in result['phases']:
                if phase['origin'] == 'new':
                    response = phase['result']
                    response['reservation_id'] = 26000 + len(new_usage)
                    new_usage.append({'id': response['reservation_id'], 'kind': 'chat', 'model': case['model'],
                                      'tokens_in': 10, 'tokens_out': 5, 'seconds': .1, 'cost_usd': .0001})
            result['ledger'] = copy.deepcopy(new_usage)
        older = [{'id': identity, 'skill': gate.PREFIX + label, 'kind': 'chat', 'model': gate.TEXT_FLASH,
                  'tokens_in': 10, 'tokens_out': 5, 'seconds': .1, 'cost_usd': cost}
                 for identity, label, cost in ((24976, 'attempt2', .0012636), (24980, 'nativeflow1', .0015978))]
        original['skill'] = gate.PREFIX + 'qualification3'
        for key, value in {'source': '/frozen/source', 'archive': '/frozen/source.tar',
                           'environment': '/etc/synthetic.env', 'original_environment_sha256': '6'*64,
                           'transform': {'synthetic': True}}.items():
            prepared[key] = original[key] = value
        original.update(coverage=gate.coverage(cases), qualification_inputs_sha256=gate.digest(inputs))
        previous.update(cases=copy.deepcopy(retained), cases_completed=8, usage=copy.deepcopy(old_usage),
            migration_usage=older + [dict(row, skill=original['skill']) for row in old_usage],
            coverage=gate.coverage(cases[:8]), expected_coverage=gate.coverage(cases),
            plan_sha256=gate.digest(original), qualification_inputs_sha256=gate.digest(inputs),
            accounted_cost_usd=sum(row['cost_usd'] for row in old_usage),
            migration_accounted_cost_usd=sum(row['cost_usd'] for row in older + old_usage),
            unknown_reserve_usd=0, migration_unknown_reserve_usd=0,
            messages_sent=0, live_business_data_changed=False)
        previous.update(known_cost_usd=previous['accounted_cost_usd'],
                        migration_known_cost_usd=previous['migration_accounted_cost_usd'],
                        aggregate_ceiling_usd=.25, per_call_ceiling_usd=.15, migration_skill_prefix=gate.PREFIX)
        pins = {'original_plan': gate.digest(original), 'original_raw': gate.digest(previous),
                'inputs': gate.digest(inputs), **{'original_case_' + str(i+1).zfill(3): gate.digest(v)
                                                 for i, v in enumerate(retained)}}
        evidence = {'plan': pins['original_plan'], 'raw': pins['original_raw'], 'inputs': pins['inputs'],
                    'cases': {'case-' + str(i+1).zfill(3) + '.json': pins['original_case_' + str(i+1).zfill(3)]
                              for i in range(8)}}
        prepared.update(schema='cara.model-retirement-retained-prefix-prepared/v1', qualification_mode=gate.MODE,
            original_evidence=evidence, qualification_inputs_sha256=pins['inputs'], coverage=gate.coverage(cases),
            skill=gate.PREFIX + 'retirement93', retained_skill=original['skill'], retained_cases_expected=8, retained_passed_expected=7,
            new_first_calls_expected=35, max_native_corrections=1, correction_case_index=7,
            retained_replay_sha256=gate.digest(outputs[7]['replay_first']),
            operator_source_hashes={'qualify_retirement93.py': '4' * 64})
        aggregate = previous['migration_usage'] + [dict(row, skill=prepared['skill']) for row in new_usage]
        raw.update(schema='cara.model-retirement-retained-prefix-raw/v1', qualification_mode=gate.MODE,
            original_evidence=evidence, qualification_inputs_sha256=pins['inputs'],
            coverage=gate.coverage(cases), expected_coverage=gate.coverage(cases),
            usage=new_usage, new_usage=copy.deepcopy(new_usage), migration_usage=aggregate,
            accounted_cost_usd=sum(row['cost_usd'] for row in new_usage),
            migration_accounted_cost_usd=sum(row['cost_usd'] for row in aggregate))
        raw.update(known_cost_usd=raw['accounted_cost_usd'],
                   migration_known_cost_usd=raw['migration_accounted_cost_usd'])
        hashes = {**pins, 'plan': gate.digest(prepared), 'raw': gate.digest(raw)}
        semantic.update(schema='cara.model-retirement-retained-prefix-semantic/v1', qualification_mode=gate.MODE,
            plan_sha256=hashes['plan'], inputs_sha256=hashes['inputs'], raw_sha256=hashes['raw'],
            cases=[{'id': result['id'], 'status': 'passed', 'reason': 'Synthetic independent review',
                    'final_text_sha256': result['final_text_sha256'],
                    'functional_sha256': gate.digest(result['functional']),
                    'phase_raw_sha256s': [phase['result']['raw_sha256'] for phase in result['phases']]}
                   for result in outputs])
        raw['plan_sha256'] = hashes['plan']
        semantic['raw_sha256'] = hashes['raw'] = gate.digest(raw)
        return [release, prepared, inputs, raw, semantic, hashes, original, previous, retained, source], pins

    def verify(self, data, pins):
        with patch.object(gate, 'ORIGINAL_PINS', pins):
            return gate.verify_retirement93(*data)

    def test_exact_prefix_and_single_correction_pass(self):
        data, pins = self.fixture()
        result = self.verify(data, pins)
        self.assertEqual((result['cases'], result['carried_calls'], result['new_calls'],
                          result['corrective_calls'], result['spend_only_calls']), (43, 8, 36, 1, 2))
        self.assertEqual(result['new_unknowns'], 0)

    def test_unselected_original_pins_fail(self):
        data, _ = self.fixture()
        with self.assertRaisesRegex(ValueError, 'exact failed qualification3'):
            gate.verify_retirement93(*data)

    def test_lineage_mutations_fail(self):
        mutations = {
            'old_application': lambda d: d[0]['identity'].update(source_commit='c' * 40),
            'wrong_archive': lambda d: d[1].update(archive_sha256='f' * 64),
            'environment': lambda d: d[3].update(candidate_environment_sha256='e' * 64),
            'original_qualifier': lambda d: d[9].update({'ops/model-retirement/qualify_models.py': 'f' * 64}),
            'operator': lambda d: d[1]['operator_source_hashes'].update({'qualify_retirement93.py': 'e' * 64}),
            'plan_hash': lambda d: d[3].update(plan_sha256='e' * 64),
            'original_status': lambda d: d[7].update(status='passed'),
            'raw_status': lambda d: d[3].update(status='failed'),
            'coverage': lambda d: d[3].update(coverage={}),
            'resample_count': lambda d: d[1].update(new_first_calls_expected=43),
            'correction_cap': lambda d: d[1].update(max_native_corrections=6),
            'same_skill': lambda d: d[1].update(skill=d[6]['skill']),
            'raise_budget': lambda d: d[3].update(aggregate_ceiling_usd=.26),
            'semantic_status': lambda d: d[4].update(status='failed'),
            'semantic_missing': lambda d: d[4]['cases'].pop(),
            'semantic_duplicate': lambda d: d[4]['cases'].__setitem__(1, copy.deepcopy(d[4]['cases'][0])),
        }
        self.reject_mutations(mutations)

    def test_each_retained_result_and_ledger_prefix_cannot_change(self):
        for index in range(8):
            for field, value in (('raw', 'Edited retained answer'), ('ledger', []), ('status', 'passed')):
                with self.subTest(index=index, field=field):
                    data, pins = self.fixture()
                    data[3]['cases'][index]['phases'][0]['result'][field] = value
                    with self.assertRaises(ValueError):
                        self.verify(data, pins)
        for index in range(8):
            with self.subTest(original_prefix=index):
                data, pins = self.fixture()
                data[8][index]['ledger'] = data[8][-1]['ledger']
                if index == 7:
                    data[8][index]['ledger'] = []
                with self.assertRaises(ValueError):
                    self.verify(data, pins)

    def test_phase_native_effect_and_review_mutations_fail(self):
        mutations = {
            'resampled_first': lambda d: d[3]['cases'][0]['phases'][0].update(origin='new'),
            'missing_correction': lambda d: d[3]['cases'][7]['phases'].pop(),
            'third_call': lambda d: d[3]['cases'][7]['phases'].append(copy.deepcopy(d[3]['cases'][7]['phases'][1])),
            'unselected_correction': lambda d: d[3]['cases'][8]['phases'].append(copy.deepcopy(d[3]['cases'][7]['phases'][1])),
            'edited_prompt': lambda d: d[3]['cases'][7]['phases'][1]['request']['messages'].append({'role': 'user', 'content': 'Different'}),
            'max_tokens': lambda d: d[3]['cases'][8]['phases'][0]['request'].update(max_tokens=9000),
            'temperature': lambda d: d[3]['cases'][8]['phases'][0]['request'].update(temperature=1.0),
            'timeout': lambda d: d[3]['cases'][8]['phases'][0]['request'].update(timeout=99),
            'provider_model': lambda d: d[3]['cases'][8]['phases'][0]['result'].update(provider_model='other'),
            'truncation': lambda d: d[3]['cases'][8]['phases'][0]['result'].update(finish_reason='length'),
            'provider_raw': lambda d: d[3]['cases'][8]['phases'][0]['result']['provider_response']['choices'][0]['message'].update(content='Other'),
            'provider_tokens': lambda d: d[3]['cases'][8]['phases'][0]['result']['provider_usage'].update(prompt_tokens=999),
            'replay_fallback': lambda d: d[3]['cases'][7]['functional'].update(deterministic_fallback=True),
            'replay_effect': lambda d: d[3]['cases'][7]['functional'].update(effects_preserved=False),
            'replay_source': lambda d: d[3]['cases'][7]['functional']['source_python_hashes'].update({'agent.py': 'e'*64}),
            'replay_call': lambda d: d[3]['cases'][7]['functional'].update(paid_calls=1),
            'replay_message': lambda d: d[3]['cases'][7]['functional'].update(telegram_messages=1),
            'original_failed_reclassified': lambda d: d[3]['cases'][7]['functional'].update(earlier_first_pass_verdict_unchanged=False),
            'native_firstpass_effect': lambda d: d[3]['cases'][8]['functional'].update(external_effects=1),
            'vision_native_missing': lambda d: d[3]['cases'][42]['functional'].pop('native_result'),
            'review_hash': lambda d: d[4]['cases'][7].update(final_text_sha256='e'*64),
            'review_functional': lambda d: d[4]['cases'][0].update(functional_sha256='e'*64),
            'review_phase': lambda d: d[4]['cases'][7].update(phase_raw_sha256s=[]),
            'review_reason': lambda d: d[4]['cases'][7].update(reason=''),
        }
        self.reject_mutations(mutations)

    def test_accounting_and_effect_mutations_fail(self):
        mutations = {
            'drop_older_spend': lambda d: d[3]['migration_usage'].pop(0),
            'reprice_older': lambda d: d[3]['migration_usage'][0].update(cost_usd=0),
            'new_unknown': lambda d: d[3]['usage'][0].update(kind='unknown_chat'),
            'unknown_flag': lambda d: d[3].update(migration_unknown_usage_ids=[999]),
            'new_usage_alias': lambda d: d[3]['new_usage'].pop(),
            'extra_new_row': lambda d: d[3]['usage'].append(copy.deepcopy(d[3]['usage'][-1])),
            'reused_reservation': lambda d: d[3]['cases'][8]['phases'][0]['result'].update(reservation_id=26000),
            'old_spend_as_coverage': lambda d: d[3]['cases'][8]['phases'][0]['result'].update(reservation_id=24976),
            'wrapper_prefix': lambda d: d[3]['cases'][8].update(ledger=[]),
            'retained_wrapper_spend': lambda d: d[3]['cases'][0].update(ledger=[d[3]['usage'][0]]),
            'known_sum': lambda d: d[3].update(known_cost_usd=0),
            'global_known_sum': lambda d: d[3].update(migration_known_cost_usd=0),
            'original_known_sum': lambda d: d[7].update(known_cost_usd=0),
            'new_sum': lambda d: d[3].update(accounted_cost_usd=0),
            'global_sum': lambda d: d[3].update(migration_accounted_cost_usd=0),
            'message': lambda d: d[3].update(messages_sent=1),
            'live_effect': lambda d: d[3].update(live_business_data_changed=True),
        }
        self.reject_mutations(mutations)

    def reject_mutations(self, mutations):
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                data, pins = self.fixture()
                mutate(data)
                with self.assertRaises((ValueError, KeyError)):
                    self.verify(data, pins)


if __name__ == '__main__':
    unittest.main()
