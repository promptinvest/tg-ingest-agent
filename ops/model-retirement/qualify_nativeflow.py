"""Distinct one-shot evidence for the unchanged native first-plus-repair flow.

The failed qualification2 stays failed. Its one paid first answer is carried
exactly, then the original remaining42 requests are used. Only the three cases
already supported by frozen repair_cases may issue one native corrective call.
This operator companion never changes the frozen application or original gate.
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
MODE = 'full_native_existing_correction'
SOURCE_COMMIT = 'c25038e8efd4c466d5ad557faed565ebdfd7ff74'
ARCHIVE_SHA = '2fa941e8ce655e2a4cf040304a82728ed383bb1582875bfc208172ffb1b5ecc1'
Q_SHA = '9fb65aac6d22786e1101cf956f6445821808432d5c19cc047bc3081d3f14993c'
ORIGINAL_FILES = {
    'plan.json': 'e26eea17d1986c047e7953da6adbbc9a6607900b7a44eac3071048b76b1544ab',
    'raw.json': '78e5406bd610cb849cfb34228b0675d8bb3b3fe65b38fbb146d5aa62f9e2885b',
    'case-001.json': '133dd6924cfa907614e4dc6e96694f1b3c0a54dca1180ac07fdf901c234993b9',
    'inputs.json': 'f744593f946ab9c03a52ed6b29d85302fb424760d65b1ea87615fb7b1b688b86'}
ORIGINAL_NAMES = {'plan.json': 'original-plan.json', 'raw.json': 'original-raw.json',
                  'case-001.json': 'original-case.json', 'inputs.json': 'inputs.json'}
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
        require(sha(raw) == expected, 'original qualification2 bytes changed: ' + name)
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
    plan, raw, first, inputs = (value[name] for name in ('plan.json', 'raw.json', 'case-001.json', 'inputs.json'))
    require(plan['source_commit'] == raw['source_commit'] == SOURCE_COMMIT
            and plan['archive_sha256'] == raw['archive_sha256'] == ARCHIVE_SHA, 'application lineage differs')
    require(raw['status'] == first['status'] == 'failed' and raw['cases_completed'] == 1
            and raw['cases'] == [first] and raw['cases_expected'] == plan['cases_expected'] == len(inputs['cases']) == 43,
            'exact failed first case and untouched42 partition required')
    require(first['id'] == inputs['cases'][0]['id']
            == 'assistant-current-work-en__deepseek-v4.1-flash-1017'
            and first['reservation_id'] == 24976 and first['finish_reason'] == 'stop'
            and first['provider_model'] == first['model'] == inputs['cases'][0]['model']
            and first['input_sha256'] == inputs['cases'][0]['input_sha256']
            and sha(first['raw'].encode()) == first['raw_sha256'], 'retained first response binding differs')
    require(raw['usage'] == first['ledger'] and len(raw['usage']) == 1
            and not raw['migration_unknown_usage_ids'], 'known original single paid outcome required')
    require(plan['qualification_inputs_sha256'] == raw['qualification_inputs_sha256'] == ORIGINAL_FILES['inputs.json']
            and raw['plan_sha256'] == ORIGINAL_FILES['plan.json'], 'original input or plan lineage differs')
    return plan, first, inputs


def evidence_hashes():
    return dict(zip(('plan', 'raw', 'case', 'inputs'), ORIGINAL_FILES.values()))


def request(case):
    return {key: case[key] for key in ('messages', 'input_sha256', 'max_tokens', 'temperature', 'timeout')}


def first_phase(case, retained, paid):
    return {'phase': 'first', 'origin': 'retained_qualification2' if retained is not None else 'new',
            'request': request(case), 'result': retained if retained is not None else paid(case, 'first')}


def correction_case(case, captured):
    require(case['kind'] == 'text' and case['case']['id'] in SUPPORTED
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
    require(len(retained['ledger']) == 1 and retained['reservation_id'] == retained['ledger'][0]['id'] == 24976,
            'exact single retained paid row required')
    expected = dict(retained['ledger'][0], skill=retained_skill)
    require(expected['kind'] == 'chat' and [row for row in rows if row['id'] == 24976] == [expected],
            'retained paid ledger outcome changed')


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
        proof = repair.replay_with_responses(source, inputs['cases'][0]['case'], [retained['raw']],
                                             inputs['prepared']['config_overrides'])
    require(proof['status'] == 'needs_next', 'retained failure must require its existing native correction')
    correction_case(inputs['cases'][0], proof['needs_next'])
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    q.publish(output / 'PREPARING.json', {'at': q.now(), 'attempt': attempt, 'qualification_mode': MODE})
    for name in ORIGINAL_FILES:
        q.publish(output / ORIGINAL_NAMES[name], read(prior / name))
    for name in ('candidate.env', 'book.png', 'invoice.png', 'blank.png'):
        q.publish(output / name, read(prior / name))
    q.publish(output / 'retained-replay.json', proof)
    plan = dict(original, schema='cara.model-retirement-nativeflow-prepared/v1', at=q.now(),
                attempt=attempt, skill=q.MIGRATION_PREFIX + attempt, retained_skill=original['skill'],
                qualification_mode=MODE, original_first_pass_status='failed', original_evidence=evidence_hashes(),
                retained_replay_sha256=sha(read(output / 'retained-replay.json')),
                operator_source_hashes={'qualify_nativeflow.py': sha(read(__file__))},
                new_first_calls_expected=42, max_native_corrections=6, paid_calls=0)
    require(plan['skill'] != plan['retained_skill'], 'new nativeflow skill required')
    q.publish(output / 'plan.json', plan)
    return {'status': 'prepared', 'qualification_mode': MODE,
            'plan_sha256': sha(read(output / 'plan.json')), 'cases': 43, 'paid_calls': 0}


def run_case(q, repair, validator, fixtures, cfg, source, output, overrides, case, first, paid, result):
    """Pure replay chooses at most one correction; paid is an explicit callback."""
    result.update(id=case['id'], profile=case['profile'], model=case['model'], input_sha256=case['input_sha256'])
    phase = first_phase(case, first, paid)
    result['phases'] = [phase]
    raw = phase['result']['raw']
    if case['kind'] == 'text' and case['case']['id'] in SUPPORTED:
        proof = repair.replay_with_responses(source, case['case'], [raw], overrides)
        result['replay_first'] = proof
        if proof['status'] == 'needs_next':
            corrected = correction_case(case, proof['needs_next'])
            phase = {'phase': 'correction', 'origin': 'new', 'request': request(corrected),
                     'result': paid(corrected, 'correction')}
            result['phases'].append(phase)
            proof = repair.replay_with_responses(source, case['case'], [raw, phase['result']['raw']], overrides)
        result['functional'] = proof
        final = require_terminal(proof, len(result['phases']))
    else:
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
    require(plan['schema'] == 'cara.model-retirement-nativeflow-prepared/v1' and plan['qualification_mode'] == MODE
            and plan['original_first_pass_status'] == 'failed' and plan['original_evidence'] == evidence_hashes(),
            'distinct nativeflow contract required')
    require(plan['operator_source_hashes'] == {'qualify_nativeflow.py': sha(read(__file__))}, 'companion source changed')
    require(not (output / 'STARTED.json').exists(), 'nativeflow already started; never replay')
    original, retained, inputs = check_original(original_evidence(output, copied=True))
    q, fixtures, validator, env, repair = load_frozen(plan['source'], original)
    for key in ('source', 'source_commit', 'source_hashes', 'archive', 'archive_sha256', 'environment',
                'original_environment_sha256', 'candidate_environment_sha256', 'qualification_inputs_sha256',
                'coverage', 'cases_expected', 'aggregate_ceiling_usd', 'per_call_ceiling_usd', 'migration_skill_prefix'):
        require(plan[key] == original[key], 'original qualification field changed: ' + key)
    require(plan['aggregate_ceiling_usd'] == q.CEILING == .25 and plan['per_call_ceiling_usd'] == q.PER_CALL == .15
            and plan['migration_skill_prefix'] == q.MIGRATION_PREFIX
            and plan['skill'] == q.MIGRATION_PREFIX + plan['attempt'] and plan['skill'] != original['skill']
            and plan['retained_skill'] == original['skill'] and plan['new_first_calls_expected'] == 42
            and plan['max_native_corrections'] == 6, 'nativeflow limits/accounting changed')
    require(sha(read(plan['environment'])) == plan['original_environment_sha256'], 'live environment changed')
    require(sha(read(output / 'candidate.env')) == plan['candidate_environment_sha256'], 'candidate environment changed')
    require(sha(read(output / 'retained-replay.json')) == plan['retained_replay_sha256'], 'retained replay changed')
    validate_inputs(q, output, plan, inputs)
    with redirect_stdout(io.StringIO()):
        replay = repair.replay_with_responses(Path(plan['source']), inputs['cases'][0]['case'],
                                             [retained['raw']], inputs['prepared']['config_overrides'])
    require(sha(canonical(replay)) == plan['retained_replay_sha256'] and replay['status'] == 'needs_next',
            'retained correction prompt changed before execution')
    correction_case(inputs['cases'][0], replay['needs_next'])
    import common
    cfg = common.load_config(env.parse_env_bytes(read(output / 'candidate.env')))
    require(str(cfg.db_path) == '/var/lib/tg-ingest-agent/ingest.db'
            and cfg.do_base_url.rstrip('/') == 'https://inference.do-ai.run/v1', 'native ledger/endpoint required')
    q.publish(output / 'STARTED.json', {'at': q.now(), 'plan_sha256': expected_plan_sha, 'skill': plan['skill'], 'qualification_mode': MODE})
    conn = sqlite3.connect('file:' + str(cfg.db_path) + '?mode=rw', uri=True, timeout=3, cached_statements=0)
    conn.row_factory = sqlite3.Row
    results = []
    try:
        baseline_rows = q.migration_rows(conn)
        verify_carried_ledger(baseline_rows, retained, original['skill'])
        require(baseline_rows == json.loads(read(output / 'original-raw.json'))['migration_usage'],
                'another migration attempt already spent; nativeflow cannot silently rebaseline')
        q.guard(baseline_rows, 0)
        with q.ledger_only(conn, plan['skill']):
            for index, case in enumerate(inputs['cases']):
                prefix = 'case-' + str(index + 1).zfill(3)
                result = {'id': case['id'], 'profile': case['profile'], 'model': case['model'],
                          'input_sha256': case['input_sha256'], 'phases': []}
                called = set()

                def paid(selected, phase):
                    require(phase in {'first', 'correction'} and phase not in called, 'repeated phase forbidden')
                    require(not (index == 0 and phase == 'first'), 'retained paid first answer cannot be rerun')
                    if index == 0:
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
                    if index == 0:
                        q.publish(output / (prefix + '-phase-first.json'), first_phase(case, retained, paid))
                    with redirect_stdout(io.StringIO()):
                        run_case(q, repair, validator, fixtures, cfg, Path(plan['source']), output,
                                 inputs['prepared']['config_overrides'], case, retained if index == 0 else None, paid, result)
                    if index == 0:
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
    summary.update(schema='cara.model-retirement-nativeflow-raw/v1', at=q.now(),
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
