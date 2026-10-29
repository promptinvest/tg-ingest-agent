"""Pure evidence gate for the distinct, unchanged native bounded repair flow.

No model, application import, database, subprocess or service operation occurs.
The reviewed companion supplies native replay evidence; immutable original input
pins and the selected archive authenticate the builders, prompts and failed past.
The original first-pass validator remains a separate, unchanged contract.
"""
import hashlib
import json
import math
import re

MODE = 'full_native_existing_correction'
APP_SOURCE = 'c25038e8efd4c466d5ad557faed565ebdfd7ff74'
APP_ARCHIVE = '2fa941e8ce655e2a4cf040304a82728ed383bb1582875bfc208172ffb1b5ecc1'
PREFIX = 'model_retirement_20261029_'
TEXT_FLASH = 'deepseek-v4.1-flash-1017'
VISION = 'deepseek-v4.1-flash'
SUPPORTED = frozenset({'assistant-current-work-en', 'assistant-requested-structure-en',
                       'assistant-uncertain-delivery-en'})
FIRST_FAILURE = 'unexpected second model request: first response did not satisfy caller'
ORIGINAL_PINS = {
    'original_plan': 'e26eea17d1986c047e7953da6adbbc9a6607900b7a44eac3071048b76b1544ab',
    'original_raw': '78e5406bd610cb849cfb34228b0675d8bb3b3fe65b38fbb146d5aa62f9e2885b',
    'original_case': '133dd6924cfa907614e4dc6e96694f1b3c0a54dca1180ac07fdf901c234993b9',
    'inputs': 'f744593f946ab9c03a52ed6b29d85302fb424760d65b1ea87615fb7b1b688b86'}
REQUIRED_FROZEN_FILES = frozenset({'common.py', 'llm.py', 'cache_usage.py',
    'call_budget.py', 'inference_io.py', 'converse_svc.py', 'action_truth.py',
    'ops/model-trial/prepare_cases.py', 'ops/model-trial/validate_cases.py',
    'ops/model-trial/repair_cases.py', 'ops/model-retirement/qualify_models.py'})


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def digest(value):
    return sha(json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=False).encode())


def coverage(cases):
    result = {}
    for case in cases:
        result.setdefault(case['profile'], {}).setdefault(case['model'], []).append(case['id'])
    return result


def _number(value, maximum=None):
    return (type(value) in (int, float) and math.isfinite(value) and value >= 0
            and (maximum is None or value <= maximum))


def _request(case):
    return {key: case[key] for key in ('messages', 'input_sha256', 'max_tokens', 'temperature', 'timeout')}


def _original(prepared, inputs, original_plan, original_raw, original_case, hashes):
    require(all(hashes.get(key) == value for key, value in ORIGINAL_PINS.items()),
            'only the exact failed qualification2 lineage is selected')
    evidence = {key: ORIGINAL_PINS['original_' + key] if key != 'inputs' else ORIGINAL_PINS[key]
                for key in ('plan', 'raw', 'case', 'inputs')}
    require(prepared['original_evidence'] == evidence and prepared['original_first_pass_status'] == 'failed',
            'earlier first-pass failure must remain failed')
    require(original_plan['schema'] == 'cara.model-retirement-prepared/v1'
            and original_raw['schema'] == 'cara.model-retirement-raw/v1'
            and original_raw['status'] == original_case['status'] == 'failed'
            and original_case['error_type'] == 'AssertionError'
            and original_case['error_detail'] == FIRST_FAILURE
            and original_raw['cases'] == [original_case] and original_raw['cases_completed'] == 1,
            'exact original failed first answer and untouched remainder required')
    require(original_raw['plan_sha256'] == hashes['original_plan']
            and original_plan['qualification_inputs_sha256'] == original_raw['qualification_inputs_sha256']
            == hashes['inputs'], 'original input/plan lineage changed')
    cases = inputs['cases']
    require(len(cases) == original_plan['cases_expected'] == original_raw['cases_expected'] == 43
            and len({case['id'] for case in cases}) == 43
            and cases[0]['id'] == original_case['id']
            == 'assistant-current-work-en__deepseek-v4.1-flash-1017'
            and original_case['reservation_id'] == 24976, 'original43 ordered native cases required')
    require(original_raw['usage'] == original_case['ledger'] and len(original_case['ledger']) == 1
            and original_raw['migration_usage'] == [dict(original_case['ledger'][0], skill=original_plan['skill'])]
            and original_case['ledger'][0]['id'] == 24976
            and original_case['ledger'][0]['kind'] == 'chat'
            and original_case['ledger'][0]['cost_usd'] == .0012636
            and original_raw['unknown_usage_ids'] == original_raw['migration_unknown_usage_ids'] == [],
            'original settled reservation differs')
    return evidence


def _provider(result, case, request, row):
    require((result['id'], result['profile'], result['model'], result['input_sha256'])
            == (case['id'], case['profile'], case['model'], request['input_sha256']),
            'phase identity or prompt binding differs')
    require(isinstance(result['raw'], str) and bool(result['raw'])
            and sha(result['raw'].encode()) == result['raw_sha256']
            and result['provider_model'] == case['model']
            and result['finish_reason'] not in (None, 'length'), 'missing, truncated or wrong-model answer')
    data = result['provider_response']
    require(data['model'] == result['provider_model'] and data['usage'] == result['provider_usage']
            and len(data['choices']) == 1 and data['choices'][0]['finish_reason'] == result['finish_reason']
            and data['choices'][0]['message']['content'] == result['raw'], 'provider response/raw binding differs')
    require(row['id'] == result['reservation_id'] and row['model'] == case['model'] and row['kind'] == 'chat'
            and _number(row['tokens_in']) and _number(row['tokens_out']) and _number(row['seconds']),
            'phase is not settled as its native reservation')
    for token, measured in (('prompt_tokens', 'tokens_in'), ('completion_tokens', 'tokens_out')):
        require(_number(result['provider_usage'].get(token)) and result['provider_usage'][token] > 0
                and result['provider_usage'][token] == row[measured],
                'provider and ledger token counts differ or require unknown accounting')


def _replay_base(proof, case, source_python):
    require(proof['schema'] == 'cara-model-switch-repair-replay/v1'
            and proof['case_id'] == case['case']['id'] and proof['first_request_sha256'] == case['input_sha256']
            and proof['source_python_hashes'] == source_python and proof['effects_preserved'] is True
            and proof['paid_calls'] == proof['telegram_messages'] == 0
            and proof['earlier_first_pass_verdict_unchanged'] is True
            and proof['manual_quality_passed'] is False and proof['qualification_passed'] is False
            and proof['retained_seed_order_reconstructed'] is True
            and isinstance(proof['frozen_fixture_utc'], str) and bool(proof['frozen_fixture_utc'])
            and bool(re.fullmatch('[0-9a-f]{64}', proof['protected_before_sha256'])),
            'native frozen source, protected state or replay contract differs')


def _replay(case, result, source_python):
    phases, first, terminal = result['phases'], result['replay_first'], result['functional']
    for proof in (first, terminal):
        _replay_base(proof, case, source_python)
    require(terminal['status'] == 'functional_passed_pending_manual'
            and terminal['deterministic_fallback'] is False
            and terminal['replay_result']['reminders_unchanged'] is True
            and terminal['replay_result']['answer'] == terminal['final_text']
            and terminal['responses_consumed'] == len(phases)
            and len(terminal['requests']) == len(phases)
            and terminal['retained_response_sha256s'] == [phase['result']['raw_sha256'] for phase in phases],
            'complete successful native replay required; containment cannot pass')
    for index, (phase, captured) in enumerate(zip(phases, terminal['requests'])):
        require(captured['index'] == index and captured['messages'] == phase['request']['messages']
                and captured['input_sha256'] == phase['request']['input_sha256']
                and captured['retained_response_sha256'] == phase['result']['raw_sha256']
                and captured['profile'] == case['profile'] == 'converse_warm' and captured['skill'] == 'converse'
                and captured['kwargs'] == case['case']['caller_kwargs'] == {'profile': 'converse_warm'},
                'native captured request/caller differs from paid phase')
    if len(phases) == 1:
        require(first == terminal, 'successful first response replay changed')
    else:
        require(first['status'] == 'needs_next' and first['final_text'] is None
                and first['responses_consumed'] == 1 and len(first['requests']) == 2
                and first['retained_response_sha256s'] == [phases[0]['result']['raw_sha256']]
                and first['requests'][0] == terminal['requests'][0]
                and first['requests'][1] == first['needs_next']
                and first['needs_next'] == {key: value for key, value in terminal['requests'][1].items()
                                             if key != 'retained_response_sha256'},
                'correction was not the one exact request produced by the native first replay')
        for key in ('protected_before_sha256', 'frozen_fixture_utc', 'source_python_hashes'):
            require(first[key] == terminal[key], 'synthetic native replay state changed')
    require(isinstance(terminal['final_text'], str) and bool(terminal['final_text'])
            and result['final_text'] == terminal['final_text'], 'delivered native final answer differs')
    if case['case']['id'] == 'assistant-requested-structure-en':
        lines = [line.strip() for line in result['final_text'].splitlines() if line.strip()]
        require(len(lines) == 2 and re.match(r'^1[.)]\s+', lines[0]) and re.match(r'^2[.)]\s+', lines[1]),
                'original requested two-item format failed')


def verify_fullflow(plan, prepared, inputs, raw, semantic, hashes,
                    original_plan, original_raw, original_case, source_hashes):
    """Return a redacted summary only after every bound phase and review passes."""
    require(plan['qualification_mode'] == MODE and plan['identity']['source_commit'] == APP_SOURCE
            and plan['identity']['archive_sha256'] == APP_ARCHIVE, 'explicit nativeflow on frozen application required')
    for value in (prepared, raw, semantic, original_plan, original_raw):
        require(value['source_commit'] == APP_SOURCE and value['archive_sha256'] == APP_ARCHIVE
                and value['candidate_environment_sha256'] == plan['candidate_environment_sha256'],
                'application/environment lineage differs')
    require(all(value['qualification_mode'] == MODE for value in (prepared, raw, semantic))
            and prepared['schema'] == 'cara.model-retirement-nativeflow-prepared/v1'
            and raw['schema'] == 'cara.model-retirement-nativeflow-raw/v1'
            and semantic['schema'] == 'cara.model-retirement-nativeflow-semantic/v1'
            and raw['status'] == 'functional_passed_pending_semantic' and semantic['status'] == 'passed',
            'distinct complete nativeflow and independent semantic evidence required')
    evidence = _original(prepared, inputs, original_plan, original_raw, original_case, hashes)
    require(raw['original_evidence'] == evidence and raw['original_first_pass_status'] == 'failed',
            'original failure cannot be reclassified')
    require(prepared['source_hashes'] == original_plan['source_hashes'] == source_hashes
            and REQUIRED_FROZEN_FILES <= set(source_hashes)
            and all(re.fullmatch('[0-9a-f]{64}', value) for value in source_hashes.values()),
            'exact frozen application and native builders required')
    require(prepared['operator_source_hashes'] == {'qualify_nativeflow.py': plan['package_sha256s']['qualify_nativeflow.py']},
            'companion source differs from qualified operator package')
    require(raw['plan_sha256'] == semantic['plan_sha256'] == hashes['plan']
            and prepared['qualification_inputs_sha256'] == raw['qualification_inputs_sha256']
            == semantic['inputs_sha256'] == hashes['inputs'] and semantic['raw_sha256'] == hashes['raw'],
            'raw/input/semantic byte lineage differs')
    cases = inputs['cases']
    expected_coverage = coverage(cases)
    require(original_plan['coverage'] == prepared['coverage'] == raw['coverage'] == raw['expected_coverage'] == expected_coverage
            and prepared['cases_expected'] == raw['cases_expected'] == raw['cases_completed'] == 43
            and [case['id'] for case in raw['cases']] == [case['id'] for case in cases], 'native43 completion/order differs')
    require(prepared['new_first_calls_expected'] == 42 and prepared['max_native_corrections'] == 6
            and prepared['retained_skill'] == original_plan['skill']
            and prepared['skill'].startswith(PREFIX) and prepared['skill'] != original_plan['skill']
            and len(prepared['skill']) > len(PREFIX), 'new attempt/retained accounting partition differs')
    require(prepared['retained_replay_sha256'] == digest(raw['cases'][0]['replay_first']),
            'retained corrective replay differs from prepared native request')
    require(all(value['aggregate_ceiling_usd'] == .25 and value['per_call_ceiling_usd'] == .15
                and value['migration_skill_prefix'] == PREFIX for value in (prepared, raw, original_plan)),
            'original migration budget limits differ')
    usage = raw['usage']
    require(raw['new_usage'] == usage and len({row['id'] for row in usage}) == len(usage),
            'new usage alias differs or reservations duplicate')
    require(all('skill' not in row for row in usage), 'native usage row shape changed')
    aggregate = original_raw['migration_usage'] + [dict(row, skill=prepared['skill']) for row in usage]
    require(raw['migration_usage'] == sorted(aggregate, key=lambda row: row['id'])
            and len({row['id'] for row in aggregate}) == len(aggregate),
            'migration accounting must be exact original plus all and only new reservations')
    by_id = {row['id']: row for row in aggregate}
    reviews = semantic['cases']
    require(len(reviews) == 43 and {item['id'] for item in reviews} == {case['id'] for case in cases},
            'every final outcome requires one independent semantic review')
    reviewed = {item['id']: item for item in reviews}
    used, corrections = [], 0
    source_python = {name: value for name, value in source_hashes.items() if '/' not in name and name.endswith('.py')}
    for index, (case, result) in enumerate(zip(cases, raw['cases'])):
        require(digest(case['messages']) == case['input_sha256'] and result['status'] == 'functional_passed_pending_semantic'
                and all(result[key] == case[key] for key in ('id', 'profile', 'model', 'input_sha256')),
                'native case/result identity differs')
        phases = result['phases']
        supported = case['kind'] == 'text' and case['case']['id'] in SUPPORTED
        require(1 <= len(phases) <= (2 if supported else 1), 'third or unsupported corrective call')
        if index == 0:
            require(len(phases) == 2 and result['replay_first']['status'] == 'needs_next',
                    'the retained failed first answer requires its one native correction')
        for phase_index, phase in enumerate(phases):
            require(phase['phase'] == ('first' if phase_index == 0 else 'correction')
                    and phase['origin'] == ('retained_qualification2' if index == phase_index == 0 else 'new'),
                    'phase order/origin changed or original answer rerun')
            request = phase['request']
            require(set(request) == set(_request(case)) and digest(request['messages']) == request['input_sha256']
                    and all(request[key] == case[key] for key in ('max_tokens', 'temperature', 'timeout')),
                    'native token/temperature/timeout control or prompt hash changed')
            if phase_index == 0:
                require(request == _request(case), 'original first request changed')
            else:
                corrections += 1
            response = phase['result']
            if index == phase_index == 0:
                require(response == original_case, 'retained paid first outcome must remain exact and failed')
            else:
                require(response.get('status') != 'failed' and 'error_type' not in response,
                        'new failed phase cannot qualify')
            reservation = response['reservation_id']
            require(type(reservation) is int and reservation not in used and reservation in by_id,
                    'reservation reused or unaccounted')
            used.append(reservation)
            _provider(response, case, request, by_id[reservation])
        if supported:
            _replay(case, result, source_python)
        else:
            functional = result['functional']
            require(result.get('replay_first') is None and functional['status'] == 'passed'
                    and functional['manual_quality_required'] is True
                    and result['final_text'] == phases[0]['result']['raw'], 'original native functional outcome differs')
            if case['kind'] == 'text':
                require(functional['id'] == case['case']['id'] and functional['profile'] == case['profile']
                        and functional['raw_sha256'] == phases[0]['result']['raw_sha256']
                        and functional['prepared_input_sha256'] == case['input_sha256']
                        and functional['paid_calls'] == functional['external_effects'] == 0,
                        'original first-pass functional evidence differs')
                require('replay_boundary' in functional and 'replay_result' in functional,
                        'original native functional replay result absent')
            else:
                require('native_result' in functional, 'native vision replay outcome absent')
        review = reviewed[case['id']]
        require(sha(result['final_text'].encode()) == result['final_text_sha256'] == review['final_text_sha256']
                and review['functional_sha256'] == digest(result['functional'])
                and review['phase_raw_sha256s'] == [phase['result']['raw_sha256'] for phase in phases]
                and review['status'] == 'passed' and isinstance(review.get('reason'), str) and bool(review['reason'].strip()),
                'independent semantic review does not bind every phase and native final outcome')
    require(set(used) == set(by_id) and len(usage) == 42 + corrections and corrections <= 6,
            'unbound usage or native correction count differs')
    for row in aggregate:
        require(row['kind'] == 'chat' and _number(row['cost_usd'], .15), 'unknown or invalid phase charge')
    total = sum(row['cost_usd'] for row in aggregate)
    require(total <= .25 + 1e-12 and raw['migration_accounted_cost_usd'] == total
            and raw['accounted_cost_usd'] == sum(row['cost_usd'] for row in usage), 'migration budget accounting differs')
    require(raw['unknown_usage_ids'] == raw['migration_unknown_usage_ids'] == []
            and raw['unknown_reserve_usd'] == raw['migration_unknown_reserve_usd'] == 0
            and raw['messages_sent'] == 0 and raw['live_business_data_changed'] is False,
            'unknown charge or live business effect')
    return {'qualification_mode': MODE, 'cases': 43, 'carried_calls': 1, 'new_calls': len(usage),
            'corrective_calls': corrections, 'migration_accounted_cost_usd': total,
            'new_unknowns': 0, 'semantic_cases': len(reviews), 'raw_sha256': hashes['raw']}
