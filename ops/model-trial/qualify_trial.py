"""Fresh finite paired qualification through the accepted gateway and real fixture replay."""
import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import sqlite3
import sys
import time

sys.dont_write_bytecode = True
CEILING = 5.0
PER_CALL = 0.15


def require(value, message):
    if not value:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def publish(path, value):
    with path.open('xb') as stream:
        stream.write(canonical(value))
        stream.flush()
        import os
        os.fsync(stream.fileno())


def guard(rows, cost):
    require(math.isfinite(cost) and 0 <= cost <= PER_CALL, 'per-call conservative reservation exceeds0.15')
    require(all(row['kind'] in ('chat', 'rejected_chat') for row in rows), 'unknown prior charge; no continuation/replay')
    require(all(type(row['cost_usd']) in (int, float) and math.isfinite(row['cost_usd'])
                and row['cost_usd'] >= 0 and (row['kind'] != 'rejected_chat' or row['cost_usd'] == 0)
                for row in rows), 'invalid prior charge')
    spent = sum(row['cost_usd'] for row in rows)
    require(spent + cost <= CEILING + 1e-12, 'aggregate5 qualification budget would be exceeded')
    return spent


def run(root, output, cohort):
    initial = json.loads((root / 'prepared.json').read_text())
    require(not output.exists(), 'new exclusive qualification output required')
    require(sha((root / 'original.env').read_bytes()) == initial['before_env_sha256']
            and sha((root / 'candidate.env').read_bytes()) == initial['candidate_env_sha256'],
            'recorded qualification environments changed')
    source = Path(initial['source'])
    for name, expected in initial['runtime_hashes'].items():
        counterpart = source / ('tg_ingest_agent.py' if name == 'agent.py' else name)
        require(counterpart.is_file() and sha(counterpart.read_bytes()) == expected,
                'qualification runtime source changed: ' + name)
    publish(root / ('qualification-' + cohort + '.STARTED'),
            {'at': datetime.now(timezone.utc).isoformat(), 'cohort': cohort, 'output': str(output)})
    output.mkdir(mode=0o700)
    publish(output / 'STARTED.json', {'at': datetime.now(timezone.utc).isoformat(), 'cohort': cohort,
            'before_env_sha256': initial['before_env_sha256'], 'candidate_env_sha256': initial['candidate_env_sha256']})
    sys.path.insert(0, str(source))
    import common, llm
    import prepare_cases, validate_cases
    from trial_config import parse_env_bytes, MODEL_KEYS
    original = parse_env_bytes((root / 'original.env').read_bytes())
    candidate = parse_env_bytes((root / 'candidate.env').read_bytes())
    selected = candidate if cohort == 'candidate' else original
    require(sha(Path('/etc/tg-ingest-agent.env').read_bytes()) == initial['before_env_sha256'],
            'production configuration changed before finite qualification')
    cfg = common.load_config(selected)
    overrides = {key: selected[key] for key in MODEL_KEYS if key in selected}
    with redirect_stdout(io.StringIO()):
        prepared = prepare_cases.prepare(source, overrides)
    publish(output / 'cases.json', prepared)
    require(len(prepared['cases']) >= 24 and len(prepared['coverage']['active_profiles']) == 8,
            'all active profile cases required')
    conn = sqlite3.connect('file:' + str(cfg.db_path) + '?mode=rw', uri=True, timeout=3)
    conn.row_factory = sqlite3.Row
    skill = 'model_trial_20261025'
    original_reserve = llm.call_budget.reserve
    last_row = [None]

    def rows():
        return [dict(row) for row in conn.execute('SELECT id,kind,model,tokens_in,tokens_out,seconds,cost_usd FROM llm_usage WHERE skill=? ORDER BY id', (skill,))]

    def reserve(*args, **kwargs):
        cost = args[5]
        conn.execute('BEGIN IMMEDIATE')
        try:
            guard(rows(), cost)
            row = original_reserve(*args, **kwargs)
        except BaseException:
            conn.rollback()
            raise
        last_row[0] = row
        return row

    llm.call_budget.reserve = reserve
    results = []
    raw_responses = {}
    try:
        for index, case in enumerate(prepared['cases']):
            last_row[0] = None
            profile = llm.profiles(cfg)[case['profile']]
            require(case['runtime_profile'] == profile, 'prepared profile differs from actual selected profile')
            started = time.monotonic()
            result = {'id': case['id'], 'cohort': cohort, 'profile': case['profile'],
                      'model': profile['primary'], 'input_sha256': case['input_sha256']}
            try:
                with redirect_stdout(io.StringIO()):
                    prepared_request = llm.prepare_chat(cfg, conn, skill, case['messages'],
                        max_tokens=case['max_tokens'], model=profile['primary'],
                        temperature=profile.get('temperature', 0), timeout=profile['timeout'])
                    try:
                        data = llm.transport_chat(prepared_request)
                    except Exception as error:
                        llm.finish_chat(cfg, conn, prepared_request, error=error)
                    else:
                        raw = llm.finish_chat(cfg, conn, prepared_request, data=data)
                raw_responses[case['id']] = raw
                result['raw'] = raw
                result['raw_sha256'] = sha(raw.encode())
                result['transport'] = 'completed'
                result['finish_reason'] = (data.get('choices') or [{}])[0].get('finish_reason')
                result['provider_usage'] = data.get('usage', {})
                with redirect_stdout(io.StringIO()):
                    replay = validate_cases.validate_response(source, case, raw, overrides)
                result['functional'] = replay
                result['status'] = 'functional_passed_pending_manual'
            except Exception as error:
                result['status'] = 'failed'
                result['error'] = type(error).__name__ + ': ' + str(error)[:1500]
            result['elapsed_seconds'] = round(time.monotonic() - started, 3)
            result['usage'] = next((row for row in rows() if row['id'] == last_row[0]), None)
            publish(output / ('case-' + str(index + 1).zfill(2) + '.json'), result)
            results.append(result)
            print(json.dumps({'case': case['id'], 'cohort': cohort, 'model': profile['primary'],
                              'status': result['status'], 'seconds': result['elapsed_seconds']}), flush=True)
            if any(row['kind'].startswith('unknown_') for row in rows()):
                publish(output / 'STOPPED-UNKNOWN.json', {'at': datetime.now(timezone.utc).isoformat(),
                        'usage': rows(), 'automatic_replay': False})
                break
        summary = {'schema': 'cara.model-trial-raw/v1', 'at': datetime.now(timezone.utc).isoformat(),
                   'cohort': cohort, 'candidate_env_sha256': initial['candidate_env_sha256'],
                   'prepared_sha256': sha((output / 'cases.json').read_bytes()),
                   'source_revision': initial['identity']['source_revision'],
                   'runtime_hashes': initial['runtime_hashes'], 'qualification_ceiling_usd': CEILING,
                   'source_python_hashes': prepared['source_python_hashes'],
                   'cases': results, 'cases_expected': len(prepared['cases']), 'usage': rows(),
                   'functional_cases_passed': sum(result['status'] == 'functional_passed_pending_manual' for result in results),
                   'unknown_usage_ids': [row['id'] for row in rows() if row['kind'].startswith('unknown_')],
                   'accounted_cost_usd': sum(row['cost_usd'] for row in rows()),
                   'manual_quality_passed': False, 'operator_messages_sent': 0, 'live_business_data_changed': False}
        publish(output / 'raw.json', summary)
        publish(output / 'responses.json', raw_responses)
        print(json.dumps({'cohort': cohort, 'completed': len(results), 'passed': summary['functional_cases_passed'],
                          'known_charge_usd': summary['accounted_cost_usd'], 'unknown': summary['unknown_usage_ids']}))
    finally:
        llm.call_budget.reserve = original_reserve
        conn.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cohort', choices=('candidate', 'baseline'), required=True)
    args = parser.parse_args()
    run(args.root, args.output, args.cohort)
