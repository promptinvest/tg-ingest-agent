"""One new source-bound continuation of the actual qualification3 prefix.

The seven passed first responses and failed eighth response remain exact.
Only case eight may use its one existing native correction; the remaining35
requests keep the original first-pass checks. No paid sampling is repeated.
"""
import argparse
from contextlib import redirect_stdout
import hashlib
import importlib
import io
import json
from pathlib import Path
import re
import sqlite3
import sys

sys.dont_write_bytecode = True
MODE = 'full_native_retirement'
SOURCE_COMMIT = '93bf1a6042af5b1651a747c230533e7c008de52a'
ARCHIVE_SHA = '165723ab9872ff227f944ea8b461acbe3a06442f4de12bb0a52f5ce0ec1c2e9b'
Q_SHA = '9fb65aac6d22786e1101cf956f6445821808432d5c19cc047bc3081d3f14993c'
ORIGINAL_FILES = {
    'plan.json': '96d615f140c8fa2b9a0be054b2d42a6556a458cd49af59c259c57636313be910',
    'raw.json': 'c51793ab20186fdc60c0afa0844d13c38b0a6a33abe17e2bb53b22485c0a6372',
    'inputs.json': '295045461d2965733a7d6b880f8ffc355bec29b29902a1fd4c125014a5fe6b74',
    'case-001.json': 'd99fb3c1abbae5bfe9894d7977cb24c389d260a909c813f5c0c80210acca3293',
    'case-002.json': 'd7557f09d33d080237ebf8768980f72ce638f3443649801ae7fb6c08ca5b6de1',
    'case-003.json': '12bc50bacacced43f21b6306c753056e861a211a73d0fa15bfc3df6ef8152c0f',
    'case-004.json': '6c1c5b3b88c7d8f2d8c11bcc22f05fb2f2ffec543d504c848128397e3da51c26',
    'case-005.json': 'e8db85f0e45cf08f716463072595c423fb3cd64c507c21e580c4fdecf2722363',
    'case-006.json': '7a7201fbbb95228fa45e80d1b87d5e1d78696ecc14169eb2d9456425d790e017',
    'case-007.json': '95cf91c7a53331b4845fabf36a9085d5ed866f2fc5b9be609356142554a7e7ef',
    'case-008.json': 'cf3f72e520cb9080ba851dca40b1527c76441ea6b4851a7b76b3ffdf67a2ae91',
}
ORIGINAL_NAMES = {name: 'original-' + name if name != 'inputs.json' else name
                  for name in ORIGINAL_FILES}
RETAINED_COUNT = 8
FAIL_INDEX = 7
FAIL_CASE = 'assistant-uncertain-delivery-en__openai-gpt-oss-120b'
FIRST_FAILURE = 'unexpected second model request: first response did not satisfy caller'
SUPPORTED = frozenset({'assistant-current-work-en', 'assistant-requested-structure-en',
                       'assistant-uncertain-delivery-en'})


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), 'regular retained input required')
    return path.read_bytes()


def original_evidence(root, copied=False):
    result = {}
    for name, expected in ORIGINAL_FILES.items():
        raw = read(Path(root) / (ORIGINAL_NAMES[name] if copied else name))
        require(sha(raw) == expected, 'original qualification3 bytes changed: ' + name)
        result[name] = json.loads(raw)
    return result


def load_frozen(source, original_plan):
    source = Path(source).resolve()
    require(original_plan['source_commit'] == SOURCE_COMMIT
            and original_plan['archive_sha256'] == ARCHIVE_SHA, 'fixed application candidate required')
    operator = source / 'ops' / 'model-retirement'
    require(sha(read(operator / 'qualify_models.py')) == Q_SHA, 'original qualifier changed')
    sys.path.insert(0, str(operator))
    q = importlib.import_module('qualify_models')
    require(Path(q.__file__).resolve() == operator / 'qualify_models.py', 'nonfrozen qualifier import')
    require(q.source_hashes(source) == original_plan['source_hashes'], 'frozen application files changed')
    archive = read(original_plan['archive'])
    require(sha(archive) == ARCHIVE_SHA, 'application archive changed')
    q.verify_archive(archive, original_plan['source_hashes'], SOURCE_COMMIT)
    fixtures, validator, env = q.libraries(source)
    repair = importlib.import_module('repair_cases')
    require(Path(repair.__file__).resolve() == source / 'ops' / 'model-trial' / 'repair_cases.py'
            and repair.SUPPORTED == SUPPORTED, 'frozen native repair library required')
    return q, fixtures, validator, env, repair


def check_original(value):
    plan, raw, inputs = (value[name] for name in ('plan.json', 'raw.json', 'inputs.json'))
    retained = [value['case-' + str(index + 1).zfill(3) + '.json'] for index in range(RETAINED_COUNT)]
    require(plan['source_commit'] == raw['source_commit'] == SOURCE_COMMIT
            and plan['archive_sha256'] == raw['archive_sha256'] == ARCHIVE_SHA, 'application lineage differs')
    require(raw['status'] == 'failed' and raw['cases_completed'] == RETAINED_COUNT
            and raw['cases'] == retained
            and raw['cases_expected'] == plan['cases_expected'] == len(inputs['cases']) == 43,
            'exact retained eight-case prefix and untouched35 partition required')
    require(len(raw['usage']) == RETAINED_COUNT
            and [row['id'] for row in raw['usage']] == list(range(25062, 25070)),
            'eight exact original reservations required')
    for index, first in enumerate(retained):
        case = inputs['cases'][index]
        require(first['id'] == case['id'] and first['provider_model'] == first['model'] == case['model']
                and first['profile'] == case['profile'] and first['finish_reason'] == 'stop'
                and first['input_sha256'] == case['input_sha256']
                and sha(first['raw'].encode()) == first['raw_sha256']
                and first['reservation_id'] == raw['usage'][index]['id']
                and first['ledger'] == raw['usage'][:index + 1], 'retained response/ledger prefix differs')
        if index < FAIL_INDEX:
            require(first['status'] == 'functional_passed_pending_semantic'
                    and first['functional']['status'] == 'passed', 'passed original prefix changed')
        else:
            require(first['id'] == FAIL_CASE and first['status'] == 'failed'
                    and first['error_type'] == 'AssertionError' and first['error_detail'] == FIRST_FAILURE,
                    'the original eighth failure must remain exact and failed')
    qualifying = [dict(row, skill=plan['skill']) for row in raw['usage']]
    historical = [row for row in raw['migration_usage'] if row['id'] not in range(25062, 25070)]
    require([row['id'] for row in historical] == [24976, 24980]
            and raw['migration_usage'] == sorted(historical + qualifying, key=lambda row: row['id'])
            and all(row['kind'] == 'chat' for row in raw['migration_usage'])
            and raw['unknown_usage_ids'] == raw['migration_unknown_usage_ids'] == [],
            'original ten settled migration rows required; old failures are spend only')
    require(plan['qualification_inputs_sha256'] == raw['qualification_inputs_sha256'] == ORIGINAL_FILES['inputs.json']
            and raw['plan_sha256'] == ORIGINAL_FILES['plan.json'], 'original input or plan lineage differs')
    require(all(case.get('case', {}).get('id') not in SUPPORTED for case in inputs['cases'][RETAINED_COUNT:]),
            'remaining35 must retain first-pass-only checks')
    return plan, retained, inputs


def evidence_hashes():
    return {'plan': ORIGINAL_FILES['plan.json'], 'raw': ORIGINAL_FILES['raw.json'],
            'inputs': ORIGINAL_FILES['inputs.json'],
            'cases': {name: pin for name, pin in ORIGINAL_FILES.items() if name.startswith('case-')}}


def request(case):
    return {key: case[key] for key in ('messages', 'input_sha256', 'max_tokens', 'temperature', 'timeout')}


def first_phase(case, retained, paid):
    return {'phase': 'first', 'origin': 'retained_qualification3' if retained is not None else 'new',
            'request': request(case), 'result': retained if retained is not None else paid(case, 'first')}


def correction_case(case, captured):
    require(case['kind'] == 'text' and case['id'] == FAIL_CASE
            and captured['profile'] == case['profile'] == 'converse_warm'
            and captured['skill'] == 'converse'
            and captured['kwargs'] == {'profile': 'converse_warm'}, 'only the unchanged native conversation repair is allowed')
    require(sha(canonical(captured['messages'])) == captured['input_sha256'], 'corrective request changed')
    return dict(case, messages=captured['messages'], input_sha256=captured['input_sha256'])


def require_terminal(proof, count):
    require(proof.get('status') == 'functional_passed_pending_manual'
            and proof.get('deterministic_fallback') is False and proof.get('effects_preserved') is True
            and proof.get('responses_consumed') == count and len(proof.get('requests', [])) == count
            and isinstance(proof.get('final_text'), str) and proof['final_text'],
            'native flow failed, requested another call, or used deterministic containment')
    return proof['final_text']


def verify_carried_ledger(rows, retained, retained_skill):
    require(len(retained) == RETAINED_COUNT, 'all eight retained outcomes required')
    expected = []
    for index, result in enumerate(retained):
        require(len(result['ledger']) == index + 1
                and result['reservation_id'] == result['ledger'][-1]['id'] == 25062 + index,
                'original cumulative ledger prefix differs')
        expected.append(dict(result['ledger'][-1], skill=retained_skill))
    require([row for row in rows if row['id'] in range(25062, 25070)] == expected,
            'retained paid ledger outcomes changed')


def validate_inputs(q, output, plan, inputs):
    require(len(inputs['cases']) == 43 and q.coverage(inputs['cases']) == plan['coverage'], 'original43 coverage changed')
    for case in inputs['cases']:
        require(sha(canonical(case['messages'])) == case['input_sha256'], 'original native prompt changed')
        if case['kind'] == 'vision':
            require(case['image'] in {'book.png', 'invoice.png', 'blank.png'}, 'unselected image')
            require(sha(read(output / case['image'])) == case['image_sha256'], 'image bytes changed')


def prepare(prior, output, attempt):
    prior, output = Path(prior).resolve(), Path(output).resolve()
    require(re.fullmatch('[a-z0-9][a-z0-9-]{0,45}', attempt), 'bounded unique attempt required')
    value = original_evidence(prior)
    original, retained, inputs = check_original(value)
    source = Path(original['source']).resolve()
    require(not output.is_relative_to(source) and output != prior, 'new evidence outside original/source required')
    q, _, _, env, repair = load_frozen(source, original)
    require(sha(read(original['environment'])) == original['original_environment_sha256'], 'live environment changed')
    candidate = read(prior / 'candidate.env')
    require(sha(candidate) == original['candidate_environment_sha256'], 'candidate environment changed')
    validate_inputs(q, prior, original, inputs)
    # Capture and validate the actual existing correction before authorizing a
    # paid continuation. Frozen clock and seed order reproduce every first byte.
    with redirect_stdout(io.StringIO()):
        proof = repair.replay_with_responses(source, inputs['cases'][FAIL_INDEX]['case'], [retained[FAIL_INDEX]['raw']],
                                             inputs['prepared']['config_overrides'])
    require(proof['status'] == 'needs_next', 'retained failure must require its existing native correction')
    correction_case(inputs['cases'][FAIL_INDEX], proof['needs_next'])
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    q.publish(output / 'PREPARING.json', {'at': q.now(), 'attempt': attempt, 'qualification_mode': MODE})
    for name in ORIGINAL_FILES:
        q.publish(output / ORIGINAL_NAMES[name], read(prior / name))
    for name in ('candidate.env', 'book.png', 'invoice.png', 'blank.png'):
        q.publish(output / name, read(prior / name))
    q.publish(output / 'retained-replay.json', proof)
    plan = dict(original, schema='cara.model-retirement-retained-prefix-prepared/v1', at=q.now(),
                attempt=attempt, skill=q.MIGRATION_PREFIX + attempt, retained_skill=original['skill'],
                qualification_mode=MODE, original_first_pass_status='failed', original_evidence=evidence_hashes(),
                retained_replay_sha256=sha(read(output / 'retained-replay.json')),
                operator_source_hashes={'qualify_retirement93.py': sha(read(__file__))},
                retained_cases_expected=8, retained_passed_expected=7,
                new_first_calls_expected=35, max_native_corrections=1, correction_case_index=7, paid_calls=0)
    require(plan['skill'] != plan['retained_skill'], 'new nativeflow skill required')
    q.publish(output / 'plan.json', plan)
    return {'status': 'prepared', 'qualification_mode': MODE,
            'plan_sha256': sha(read(output / 'plan.json')), 'cases': 43, 'paid_calls': 0}


def run_case(q, repair, validator, fixtures, cfg, source, output, overrides, index, case, first, paid, result):
    """Retain prefix7, permit only case8's one correction, then first-pass35."""
    require(isinstance(index, int) and 0 <= index < 43, 'original case index required')
    if index < RETAINED_COUNT:
        require(first is not None and first['status'] == ('failed' if index == FAIL_INDEX
                else 'functional_passed_pending_semantic'), 'exact retained prefix required before dispatch')
        if index == FAIL_INDEX:
            require(case['id'] == FAIL_CASE, 'only the eighth retained case may be corrected')
    else:
        require(first is None and case.get('case', {}).get('id') not in SUPPORTED,
                'remaining35 use only original first-pass checks')
    result.update(id=case['id'], profile=case['profile'], model=case['model'], input_sha256=case['input_sha256'])
    phase = first_phase(case, first, paid)
    result['phases'] = [phase]
    raw = phase['result']['raw']
    if index < FAIL_INDEX:
        require(first is not None and first['status'] == 'functional_passed_pending_semantic',
                'passed retained prefix cannot be resampled')
        result['functional'] = first['functional']
        require(result['functional']['status'] == 'passed', 'original passed functional evidence required')
        final = raw
    elif index == FAIL_INDEX:
        require(first is not None and case['id'] == FAIL_CASE and first['status'] == 'failed',
                'exact eighth retained failure required')
        proof = repair.replay_with_responses(source, case['case'], [raw], overrides)
        result['replay_first'] = proof
        require(proof['status'] == 'needs_next', 'only the existing eighth corrective request is authorized')
        corrected = correction_case(case, proof['needs_next'])
        phase = {'phase': 'correction', 'origin': 'new', 'request': request(corrected),
                 'result': paid(corrected, 'correction')}
        result['phases'].append(phase)
        proof = repair.replay_with_responses(source, case['case'], [raw, phase['result']['raw']], overrides)
        result['functional'] = proof
        final = require_terminal(proof, 2)
    else:
        require(first is None and case.get('case', {}).get('id') not in SUPPORTED,
                'remaining35 use only original first-pass checks')
        result['functional'] = (validator.validate_response(source, case['case'], raw, overrides)
            if case['kind'] == 'text' else q.replay_vision(fixtures, cfg, output, case, raw))
        require(result['functional']['status'] == 'passed', 'native functional validation failed')
        final = raw
    result.update(final_text=final, final_text_sha256=sha(final.encode()), status='functional_passed_pending_semantic')


def execute(output, expected_plan_sha):
    output = Path(output).resolve()
    plan_raw = read(output / 'plan.json')
    require(sha(plan_raw) == expected_plan_sha, 'exact nativeflow plan hash required')
    plan = json.loads(plan_raw)
    require(plan['schema'] == 'cara.model-retirement-retained-prefix-prepared/v1' and plan['qualification_mode'] == MODE
            and plan['original_first_pass_status'] == 'failed' and plan['original_evidence'] == evidence_hashes(),
            'distinct nativeflow contract required')
    require(plan['operator_source_hashes'] == {'qualify_retirement93.py': sha(read(__file__))}, 'companion source changed')
    require(not (output / 'STARTED.json').exists(), 'nativeflow already started; never replay')
    original, retained, inputs = check_original(original_evidence(output, copied=True))
    q, fixtures, validator, env, repair = load_frozen(plan['source'], original)
    for key in ('source', 'source_commit', 'source_hashes', 'archive', 'archive_sha256', 'environment',
                'original_environment_sha256', 'candidate_environment_sha256', 'qualification_inputs_sha256',
                'coverage', 'cases_expected', 'aggregate_ceiling_usd', 'per_call_ceiling_usd', 'migration_skill_prefix', 'transform'):
        require(plan[key] == original[key], 'original qualification field changed: ' + key)
    require(plan['aggregate_ceiling_usd'] == q.CEILING == .25 and plan['per_call_ceiling_usd'] == q.PER_CALL == .15
            and plan['migration_skill_prefix'] == q.MIGRATION_PREFIX
            and plan['skill'] == q.MIGRATION_PREFIX + plan['attempt'] and plan['skill'] != original['skill']
            and plan['retained_skill'] == original['skill'] and plan['new_first_calls_expected'] == 35
            and plan['max_native_corrections'] == 1 and plan['correction_case_index'] == FAIL_INDEX
            and plan['retained_cases_expected'] == RETAINED_COUNT and plan['retained_passed_expected'] == 7, 'nativeflow limits/accounting changed')
    require(sha(read(plan['environment'])) == plan['original_environment_sha256'], 'live environment changed')
    require(sha(read(output / 'candidate.env')) == plan['candidate_environment_sha256'], 'candidate environment changed')
    require(sha(read(output / 'retained-replay.json')) == plan['retained_replay_sha256'], 'retained replay changed')
    validate_inputs(q, output, plan, inputs)
    with redirect_stdout(io.StringIO()):
        replay = repair.replay_with_responses(Path(plan['source']), inputs['cases'][FAIL_INDEX]['case'],
                                             [retained[FAIL_INDEX]['raw']], inputs['prepared']['config_overrides'])
    require(sha(canonical(replay)) == plan['retained_replay_sha256'] and replay['status'] == 'needs_next',
            'retained correction prompt changed before execution')
    correction_case(inputs['cases'][FAIL_INDEX], replay['needs_next'])
    import common
    cfg = common.load_config(env.parse_env_bytes(read(output / 'candidate.env')))
    require(str(cfg.db_path) == '/var/lib/tg-ingest-agent/ingest.db'
            and cfg.do_base_url.rstrip('/') == 'https://inference.do-ai.run/v1', 'native ledger/endpoint required')
    conn = sqlite3.connect('file:' + str(cfg.db_path) + '?mode=rw', uri=True, timeout=3, cached_statements=0)
    conn.row_factory = sqlite3.Row
    results = []
    try:
        baseline_rows = q.migration_rows(conn)
        verify_carried_ledger(baseline_rows, retained, original['skill'])
        require(baseline_rows == json.loads(read(output / 'original-raw.json'))['migration_usage'],
                'another migration attempt already spent; nativeflow cannot silently rebaseline')
        q.guard(baseline_rows, 0)
        q.publish(output / 'STARTED.json', {'at': q.now(), 'plan_sha256': expected_plan_sha, 'skill': plan['skill'], 'qualification_mode': MODE})
        with q.ledger_only(conn, plan['skill']):
            for index, case in enumerate(inputs['cases']):
                prefix = 'case-' + str(index + 1).zfill(3)
                result = {'id': case['id'], 'profile': case['profile'], 'model': case['model'],
                          'input_sha256': case['input_sha256'], 'phases': []}
                called = set()

                def paid(selected, phase):
                    require(phase in {'first', 'correction'} and phase not in called, 'repeated phase forbidden')
                    require((index == FAIL_INDEX and phase == 'correction')
                            or (index >= RETAINED_COUNT and phase == 'first'),
                            'retained first answers cannot be rerun; only eighth correction is authorized')
                    if index == FAIL_INDEX:
                        require(phase == 'correction'
                                and sha(canonical(result['replay_first'])) == plan['retained_replay_sha256'],
                                'retained native correction changed before dispatch')
                    called.add(phase)
                    q.publish(output / (prefix + '-phase-' + phase + '-request.json'), request(selected))
                    response = {}
                    record = {'phase': phase, 'origin': 'new', 'request': request(selected), 'result': response}
                    try:
                        q.execute_case(cfg, conn, plan['skill'], selected, response)
                        q.guard(q.migration_rows(conn), 0)
                    finally:
                        record['usage'] = [row for row in q.ledger_rows(conn, plan['skill'])
                                           if row['id'] == response.get('reservation_id')]
                        q.publish(output / (prefix + '-phase-' + phase + '.json'), record)
                    return response

                try:
                    if index < RETAINED_COUNT:
                        q.publish(output / (prefix + '-phase-first.json'), first_phase(case, retained[index], paid))
                    with redirect_stdout(io.StringIO()):
                        run_case(q, repair, validator, fixtures, cfg, Path(plan['source']), output,
                                 inputs['prepared']['config_overrides'], index, case,
                                 retained[index] if index < RETAINED_COUNT else None, paid, result)
                    if index == FAIL_INDEX:
                        require(sha(canonical(result['replay_first'])) == plan['retained_replay_sha256'], 'retained native replay changed')
                except Exception as error:
                    result.update(status='failed', error_type=type(error).__name__,
                                  error_detail=str(error).replace(cfg.do_key, '<redacted>')[:2000])
                result['ledger'] = q.ledger_rows(conn, plan['skill'])
                q.publish(output / (prefix + '.json'), result)
                results.append(result)
                print(json.dumps({'case': case['id'], 'status': result['status'], 'qualification_mode': MODE}), flush=True)
                if result['status'] == 'failed':
                    break
        rows, aggregate = q.ledger_rows(conn, plan['skill']), q.migration_rows(conn)
        verify_carried_ledger(aggregate, retained, original['skill'])
    finally:
        conn.close()
    passed = len(results) == 43 and all(row['status'] == 'functional_passed_pending_semantic' for row in results)
    summary = {key: plan[key] for key in ('source_commit', 'archive_sha256', 'candidate_environment_sha256',
        'qualification_inputs_sha256', 'coverage', 'cases_expected', 'migration_skill_prefix',
        'aggregate_ceiling_usd', 'per_call_ceiling_usd', 'original_evidence', 'original_first_pass_status', 'qualification_mode')}
    summary.update(schema='cara.model-retirement-retained-prefix-raw/v1', at=q.now(),
        status='functional_passed_pending_semantic' if passed else 'failed', plan_sha256=expected_plan_sha,
        cases_completed=len(results), cases=results, usage=rows, new_usage=rows, migration_usage=aggregate,
        expected_coverage=plan['coverage'],
        accounted_cost_usd=sum(row['cost_usd'] for row in rows),
        known_cost_usd=sum(row['cost_usd'] for row in rows if row['kind'] == 'chat'),
        unknown_reserve_usd=sum(row['cost_usd'] for row in rows if row['kind'].startswith('unknown_')),
        unknown_usage_ids=[row['id'] for row in rows if row['kind'].startswith('unknown_')],
        migration_accounted_cost_usd=sum(row['cost_usd'] for row in aggregate),
        migration_known_cost_usd=sum(row['cost_usd'] for row in aggregate if row['kind'] == 'chat'),
        migration_unknown_reserve_usd=sum(row['cost_usd'] for row in aggregate if row['kind'].startswith('unknown_')),
        migration_unknown_usage_ids=[row['id'] for row in aggregate if row['kind'].startswith('unknown_')],
        semantic_quality_approved=False, messages_sent=0, live_business_data_changed=False)
    q.publish(output / 'raw.json', summary)
    return {'status': summary['status'], 'qualification_mode': MODE, 'raw_sha256': sha(read(output / 'raw.json')),
            'cases_completed': len(results), 'migration_accounted_cost_usd': summary['migration_accounted_cost_usd'],
            'unknown_count': len(summary['migration_unknown_usage_ids'])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prep = commands.add_parser('prepare')
    for name in ('prior', 'output', 'attempt'):
        prep.add_argument('--' + name, required=True)
    run = commands.add_parser('execute')
    run.add_argument('--output', required=True)
    run.add_argument('--plan-sha256', required=True)
    args = parser.parse_args()
    try:
        result = (prepare(args.prior, args.output, args.attempt) if args.command == 'prepare'
                  else execute(args.output, args.plan_sha256))
    except Exception as error:
        print(json.dumps({'status': 'failed', 'error_type': type(error).__name__, 'qualification_mode': MODE}), flush=True)
        return 1
    print(json.dumps(result), flush=True)
    return 1 if result['status'] == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
