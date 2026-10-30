"""Pure gate for one native correction after the exact qualification3 prefix.

The original first-pass failure and all ten paid rows remain immutable. Eight
answers are reused, one existing native corrective request is completed, and the
35 untouched first requests run once. No application or provider work occurs here.
"""
import re

from fullflow_gate import (PREFIX, TEXT_FLASH, VISION, FIRST_FAILURE, SUPPORTED,
    REQUIRED_FROZEN_FILES, require, sha, digest, coverage, _number, _request,
    _provider, _replay)

MODE = 'full_native_retirement'
APP_SOURCE = '93bf1a6042af5b1651a747c230533e7c008de52a'
APP_ARCHIVE = '165723ab9872ff227f944ea8b461acbe3a06442f4de12bb0a52f5ce0ec1c2e9b'
Q_SHA = '9fb65aac6d22786e1101cf956f6445821808432d5c19cc047bc3081d3f14993c'
RETAINED_IDS = (
    'assistant-current-work-en__deepseek-v4.1-flash-1017',
    'assistant-current-work-en__openai-gpt-oss-120b',
    'assistant-data-only-ru__openai-gpt-oss-120b',
    'assistant-data-only-ru__deepseek-v4.1-flash-1017',
    'assistant-requested-structure-en__deepseek-v4.1-flash-1017',
    'assistant-requested-structure-en__openai-gpt-oss-120b',
    'assistant-uncertain-delivery-en__deepseek-v4.1-flash-1017',
    'assistant-uncertain-delivery-en__openai-gpt-oss-120b')
ORIGINAL_PINS = {
    'original_plan': '96d615f140c8fa2b9a0be054b2d42a6556a458cd49af59c259c57636313be910',
    'original_raw': 'c51793ab20186fdc60c0afa0844d13c38b0a6a33abe17e2bb53b22485c0a6372',
    'inputs': '295045461d2965733a7d6b880f8ffc355bec29b29902a1fd4c125014a5fe6b74',
    'original_case_001': 'd99fb3c1abbae5bfe9894d7977cb24c389d260a909c813f5c0c80210acca3293',
    'original_case_002': 'd7557f09d33d080237ebf8768980f72ce638f3443649801ae7fb6c08ca5b6de1',
    'original_case_003': '12bc50bacacced43f21b6306c753056e861a211a73d0fa15bfc3df6ef8152c0f',
    'original_case_004': '6c1c5b3b88c7d8f2d8c11bcc22f05fb2f2ffec543d504c848128397e3da51c26',
    'original_case_005': 'e8db85f0e45cf08f716463072595c423fb3cd64c507c21e580c4fdecf2722363',
    'original_case_006': '7a7201fbbb95228fa45e80d1b87d5e1d78696ecc14169eb2d9456425d790e017',
    'original_case_007': '95cf91c7a53331b4845fabf36a9085d5ed866f2fc5b9be609356142554a7e7ef',
    'original_case_008': 'cf3f72e520cb9080ba851dca40b1527c76441ea6b4851a7b76b3ffdf67a2ae91'}


def original_evidence():
    return {'plan': ORIGINAL_PINS['original_plan'], 'raw': ORIGINAL_PINS['original_raw'],
            'inputs': ORIGINAL_PINS['inputs'],
            'cases': {'case-' + str(i).zfill(3) + '.json': ORIGINAL_PINS['original_case_' + str(i).zfill(3)]
                      for i in range(1, 9)}}


def _original(prepared, inputs, original, previous, retained, hashes):
    require(all(hashes.get(key) == value for key, value in ORIGINAL_PINS.items()),
            'only the exact failed qualification3 lineage is selected')
    evidence = original_evidence()
    require(prepared['original_evidence'] == evidence and prepared['original_first_pass_status'] == 'failed',
            'earlier first-pass failure must remain failed')
    cases = inputs['cases']
    require(original['schema'] == 'cara.model-retirement-prepared/v1'
            and previous['schema'] == 'cara.model-retirement-raw/v1' and previous['status'] == 'failed'
            and len(retained) == previous['cases_completed'] == 8 and previous['cases'] == retained
            and len(cases) == original['cases_expected'] == previous['cases_expected'] == 43
            and len({case['id'] for case in cases}) == 43
            and tuple(case['id'] for case in cases[:8]) == RETAINED_IDS,
            'exact eight retained responses and untouched35 partition required')
    require(previous['plan_sha256'] == hashes['original_plan']
            and original['qualification_inputs_sha256'] == previous['qualification_inputs_sha256'] == hashes['inputs'],
            'original input/plan lineage changed')
    usage = previous['usage']
    require(len(usage) == 8 and [row['id'] for row in usage] == list(range(25062, 25070)),
            'exact original eight reservation identities required')
    for index, (result, row) in enumerate(zip(retained, usage)):
        require(all(result[key] == cases[index][key] for key in ('id', 'profile', 'model', 'input_sha256'))
                and result['reservation_id'] == row['id'] and result['ledger'] == usage[:index + 1],
                'retained response identity or cumulative ledger prefix changed')
        if index < 7:
            require(result['status'] == 'functional_passed_pending_semantic' and result['functional']['status'] == 'passed'
                    and 'error_type' not in result, 'original seven native passes must remain exact')
        else:
            require(result['status'] == 'failed' and result['error_type'] == 'AssertionError'
                    and result['error_detail'] == FIRST_FAILURE,
                    'original eighth first-pass failure must remain failed')
    aggregate = previous['migration_usage']
    require(len(aggregate) == 10
            and [row['id'] for row in aggregate] == [24976, 24980, *range(25062, 25070)]
            and aggregate[2:] == [dict(row, skill=original['skill']) for row in usage]
            and all(row['skill'].startswith(PREFIX) and len(row['skill']) > len(PREFIX) for row in aggregate)
            and all(row['kind'] == 'chat' and _number(row['cost_usd'], .15) for row in aggregate)
            and abs(sum(row['cost_usd'] for row in aggregate) - .0135208) < 1e-12
            and abs(sum(row['cost_usd'] for row in usage) - .0106594) < 1e-12
            and [row['cost_usd'] for row in aggregate[:2]] == [.0012636, .0015978],
            'exact ten original paid rows must remain accounted; older two are spend only')
    require(previous['coverage'] == coverage(cases[:8]) and previous['expected_coverage'] == coverage(cases)
            and previous['accounted_cost_usd'] == previous['known_cost_usd'] == sum(row['cost_usd'] for row in usage)
            and previous['migration_accounted_cost_usd'] == previous['migration_known_cost_usd']
                == sum(row['cost_usd'] for row in aggregate), 'original coverage or accounting totals differ')
    require(previous['unknown_usage_ids'] == previous['migration_unknown_usage_ids'] == []
            and previous['unknown_reserve_usd'] == previous['migration_unknown_reserve_usd'] == 0
            and previous['messages_sent'] == 0 and previous['live_business_data_changed'] is False,
            'original unknown outcome or live effect')
    require(sum(case['kind'] == 'text' and case['case']['id'] in SUPPORTED for case in cases[:8]) == 6
            and not any(case['kind'] == 'text' and case['case']['id'] in SUPPORTED for case in cases[8:]),
            'all six conversation cases must stay in the retained prefix')
    return evidence


def _first_pass(case, result):
    functional, phase = result['functional'], result['phases'][0]['result']
    require(result.get('replay_first') is None and functional['status'] == 'passed'
            and functional['manual_quality_required'] is True
            and result['final_text'] == phase['raw'], 'original native first-pass outcome differs')
    if case['kind'] == 'text':
        require(functional['id'] == case['case']['id'] and functional['profile'] == case['profile']
                and functional['raw_sha256'] == phase['raw_sha256']
                and functional['prepared_input_sha256'] == case['input_sha256']
                and functional['paid_calls'] == functional['external_effects'] == 0
                and 'replay_boundary' in functional and 'replay_result' in functional,
                'native first-pass functional replay or effects differ')
    else:
        require(case['kind'] == 'vision' and 'native_result' in functional, 'native vision replay outcome absent')


def verify_retirement93(plan, prepared, inputs, raw, semantic, hashes,
                        original_plan, original_raw, original_cases, source_hashes):
    """Validate all43 outcomes, immutable history, the one correction and spend."""
    require(plan['qualification_mode'] == MODE and plan['identity']['source_commit'] == APP_SOURCE
            and plan['identity']['archive_sha256'] == APP_ARCHIVE, 'explicit frozen retirement application required')
    for value in (prepared, raw, semantic, original_plan, original_raw):
        require(value['source_commit'] == APP_SOURCE and value['archive_sha256'] == APP_ARCHIVE
                and value['candidate_environment_sha256'] == plan['candidate_environment_sha256'],
                'application/environment lineage differs')
    require(all(value['qualification_mode'] == MODE for value in (prepared, raw, semantic))
            and prepared['schema'] == 'cara.model-retirement-retained-prefix-prepared/v1'
            and raw['schema'] == 'cara.model-retirement-retained-prefix-raw/v1'
            and semantic['schema'] == 'cara.model-retirement-retained-prefix-semantic/v1'
            and raw['status'] == 'functional_passed_pending_semantic' and semantic['status'] == 'passed',
            'distinct complete retirement and independent semantic evidence required')
    evidence = _original(prepared, inputs, original_plan, original_raw, original_cases, hashes)
    require(raw['original_evidence'] == evidence and raw['original_first_pass_status'] == 'failed',
            'original failure cannot be reclassified')
    require(prepared['source_hashes'] == original_plan['source_hashes'] == source_hashes
            and REQUIRED_FROZEN_FILES <= set(source_hashes)
            and source_hashes['ops/model-retirement/qualify_models.py'] == Q_SHA
            and all(re.fullmatch('[0-9a-f]{64}', value) for value in source_hashes.values()),
            'exact frozen application and original qualifier required')
    require(prepared['operator_source_hashes'] == {'qualify_retirement93.py': plan['package_sha256s']['qualify_retirement93.py']},
            'companion source differs from qualified operator package')
    require(raw['plan_sha256'] == semantic['plan_sha256'] == hashes['plan']
            and prepared['qualification_inputs_sha256'] == raw['qualification_inputs_sha256']
            == semantic['inputs_sha256'] == hashes['inputs'] and semantic['raw_sha256'] == hashes['raw'],
            'raw/input/semantic byte lineage differs')
    for key in ('source', 'archive', 'environment', 'original_environment_sha256', 'transform'):
        require(prepared[key] == original_plan[key], 'original preparation field changed: ' + key)
    cases = inputs['cases']
    expected = coverage(cases)
    require(original_plan['coverage'] == prepared['coverage'] == raw['coverage'] == raw['expected_coverage'] == expected
            and prepared['cases_expected'] == raw['cases_expected'] == raw['cases_completed'] == 43
            and [result['id'] for result in raw['cases']] == [case['id'] for case in cases],
            'native43 completion/order differs')
    require(prepared['retained_cases_expected'] == 8 and prepared['retained_passed_expected'] == 7
            and prepared['new_first_calls_expected'] == 35 and prepared['max_native_corrections'] == 1
            and prepared['correction_case_index'] == 7 and prepared['retained_skill'] == original_plan['skill']
            and prepared['skill'].startswith(PREFIX) and len(prepared['skill']) > len(PREFIX)
            and prepared['skill'] not in {row['skill'] for row in original_raw['migration_usage']},
            'retained partition, one correction or new accounting scope differs')
    require(prepared['retained_replay_sha256'] == digest(raw['cases'][7]['replay_first']),
            'retained corrective replay differs from prepared native request')
    require(all(value['aggregate_ceiling_usd'] == .25 and value['per_call_ceiling_usd'] == .15
                and value['migration_skill_prefix'] == PREFIX for value in (prepared, raw, original_plan, original_raw)),
            'original migration budget limits differ')
    usage = raw['usage']
    require(raw['new_usage'] == usage and len(usage) == 36
            and len({row['id'] for row in usage}) == 36 and all('skill' not in row for row in usage)
            and [row['id'] for row in usage] == sorted(row['id'] for row in usage),
            'exact36 new reservations in accounting order required')
    aggregate = original_raw['migration_usage'] + [dict(row, skill=prepared['skill']) for row in usage]
    require(raw['migration_usage'] == sorted(aggregate, key=lambda row: row['id'])
            and len({row['id'] for row in aggregate}) == 46,
            'migration accounting must retain all original ten plus all and only new reservations')
    by_id = {row['id']: row for row in aggregate}
    reviews = semantic['cases']
    require(len(reviews) == 43 and {item['id'] for item in reviews} == {case['id'] for case in cases},
            'every final outcome requires one independent semantic review')
    reviewed = {item['id']: item for item in reviews}
    used, used_new = [], []
    source_python = {name: value for name, value in source_hashes.items() if '/' not in name and name.endswith('.py')}
    for index, (case, result) in enumerate(zip(cases, raw['cases'])):
        require(digest(case['messages']) == case['input_sha256'] and result['status'] == 'functional_passed_pending_semantic'
                and all(result[key] == case[key] for key in ('id', 'profile', 'model', 'input_sha256')),
                'native case/result identity differs')
        phases = result['phases']
        require(len(phases) == (2 if index == 7 else 1), 'only the eighth case may have its one correction')
        for phase_index, phase in enumerate(phases):
            carried = index < 8 and phase_index == 0
            require(phase['phase'] == ('first' if phase_index == 0 else 'correction')
                    and phase['origin'] == ('retained_qualification3' if carried else 'new'),
                    'phase order/origin changed or retained answer rerun')
            request = phase['request']
            require(set(request) == set(_request(case)) and digest(request['messages']) == request['input_sha256']
                    and all(request[key] == case[key] for key in ('max_tokens', 'temperature', 'timeout')),
                    'native token/temperature/timeout control or prompt hash changed')
            if phase_index == 0:
                require(request == _request(case), 'original first request changed')
            response = phase['result']
            if carried:
                require(response == original_cases[index], 'each retained paid response and ledger prefix must stay exact')
            else:
                require(response.get('status') != 'failed' and 'error_type' not in response,
                        'new failed phase cannot qualify')
            reservation = response['reservation_id']
            require(type(reservation) is int and reservation not in used and reservation in by_id,
                    'reservation reused or unaccounted')
            used.append(reservation)
            if not carried:
                used_new.append(reservation)
                require(reservation == usage[len(used_new) - 1]['id'], 'new response used historical spend or changed paid order')
            _provider(response, case, request, by_id[reservation])
        require(result['ledger'] == usage[:len(used_new)], 'new cumulative wrapper ledger prefix changed')
        if index == 7:
            _replay(case, result, source_python)
            require(result['replay_first']['status'] == 'needs_next', 'retained failed first answer requires native correction')
        else:
            _first_pass(case, result)
            if index < 7:
                require(result['functional'] == original_cases[index]['functional'],
                        'retained seven functional results cannot be revalidated or changed')
        review = reviewed[case['id']]
        require(sha(result['final_text'].encode()) == result['final_text_sha256'] == review['final_text_sha256']
                and review['functional_sha256'] == digest(result['functional'])
                and review['phase_raw_sha256s'] == [phase['result']['raw_sha256'] for phase in phases]
                and review['status'] == 'passed' and isinstance(review.get('reason'), str) and bool(review['reason'].strip()),
                'independent semantic review does not bind every phase and native final outcome')
    require(set(used) == set(by_id) - {24976, 24980} and len(used) == 44
            and used_new == [row['id'] for row in usage], 'historical spend used as coverage or unbound phase')
    require(all(row['kind'] == 'chat' and _number(row['cost_usd'], .15) for row in aggregate),
            'unknown or invalid phase charge')
    total = sum(row['cost_usd'] for row in aggregate)
    require(total <= .25 + 1e-12 and raw['migration_accounted_cost_usd'] == total
            and raw['accounted_cost_usd'] == raw['known_cost_usd'] == sum(row['cost_usd'] for row in usage)
            and raw['migration_known_cost_usd'] == total, 'migration budget accounting differs')
    require(raw['unknown_usage_ids'] == raw['migration_unknown_usage_ids'] == []
            and raw['unknown_reserve_usd'] == raw['migration_unknown_reserve_usd'] == 0
            and raw['messages_sent'] == 0 and raw['live_business_data_changed'] is False,
            'unknown charge or live business effect')
    return {'qualification_mode': MODE, 'cases': 43, 'carried_calls': 8, 'new_calls': 36,
            'corrective_calls': 1, 'spend_only_calls': 2, 'migration_accounted_cost_usd': total,
            'new_unknowns': 0, 'semantic_cases': 43, 'raw_sha256': hashes['raw']}
