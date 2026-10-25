"""Independent networkless result replay for the fresh Cara model-switch corpus.

This verifies functional outcomes, schemas, source scope and effect guards. It
does not pretend a regex verifies tone or semantic answer quality: a separate
raw-bound human/model-review judgment must approve those before promotion.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
import prepare_cases as fixtures


def validate_response(source, case, raw, overrides=None):
    source = fixtures.load_source(source)
    fixtures.OVERRIDES = dict(overrides or {})
    fixtures.require(isinstance(raw, str) and 0 < len(raw) <= 100_000, 'nonempty bounded raw model response required')
    fixtures.require(case['synthetic'] is True and case['recipe']['id'] == case['id'], 'case recipe binding invalid')
    fixtures.require(fixtures.digest(case['messages']) == case['input_sha256'], 'prepared model input altered')
    with fixtures.isolation():
        captured, result = fixtures.exercise(case['id'], raw)
    fixtures.require(captured['profile'] == case['profile'], 'replay profile differs from prepared case')
    return {'id': case['id'], 'status': 'passed', 'profile': case['profile'],
            'raw_sha256': hashlib.sha256(raw.encode()).hexdigest(),
            'prepared_input_sha256': case['input_sha256'],
            'replay_boundary': case['kind'], 'replay_result': result,
            'manual_quality_required': True, 'external_effects': 0, 'paid_calls': 0}


def validate_bundle(source, prepared, responses):
    source = fixtures.load_source(source)
    fixtures.require(prepared['schema'] == 'cara-model-switch-prepared/v1'
                     and prepared['synthetic_only'] is True, 'closed fresh synthetic prepared schema required')
    actual = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(source.glob('*.py'))}
    fixtures.require(actual == prepared['source_python_hashes'], 'source changed since preparation')
    fixtures.require(prepared['preparer_sha256'] == hashlib.sha256(Path(fixtures.__file__).read_bytes()).hexdigest(),
                     'preparer changed since preparation')
    expected = {case['id'] for case in prepared['cases']}
    fixtures.require(isinstance(responses, dict) and set(responses) == expected, 'exactly one retained raw response per fixed case required')
    results = []
    for case in prepared['cases']:
        try:
            results.append(validate_response(source, case, responses[case['id']], prepared['config_overrides']))
        except Exception as error:
            results.append({'id': case['id'], 'status': 'failed', 'error': type(error).__name__ + ': ' + str(error),
                'raw_sha256': hashlib.sha256(str(responses[case['id']]).encode()).hexdigest(),
                'manual_quality_required': True, 'external_effects': 0, 'paid_calls': 0})
    return {'schema': 'cara-model-switch-replay/v1',
            'status': 'functional_passed_pending_manual' if all(r['status'] == 'passed' for r in results) else 'failed',
            'cases': results, 'prepared_sha256': fixtures.digest(prepared),
            'validator_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'semantic_quality_approved': False, 'paid_calls': 0, 'external_effects': 0}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', required=True)
    p.add_argument('--prepared', required=True)
    p.add_argument('--responses', required=True, help='JSON object mapping fixed case_id to retained raw response string')
    p.add_argument('--out', required=True)
    a = p.parse_args()
    result = validate_bundle(a.source, json.loads(Path(a.prepared).read_text(encoding='utf-8')),
        json.loads(Path(a.responses).read_text(encoding='utf-8')))
    with Path(a.out).open('xb') as stream:
        stream.write(fixtures.canonical(result))
    print(json.dumps({'status': result['status'], 'cases': len(result['cases']),
                      'failed': [c['id'] for c in result['cases'] if c['status'] != 'passed']}))
    if result['status'] == 'failed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
