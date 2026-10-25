"""Prepare a no-paid-call proof for seven changed active Cara profiles only.

Conversation keeps its exact original effective DeepSeek profile, including cap,
fallbacks and all controls. Failed conversation experiments are excluded rather
than approved. The21 unchanged qualified nonconversation raws are applied again
through their original functional fixtures under the partial configuration.
"""
import argparse
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sqlite3
import sys

sys.dont_write_bytecode = True
from qualify_trial import CEILING, PER_CALL, canonical, guard, publish, require, sha
from qualify_corrected import CONVERSE_IDS, read_json, usage_rows

CHANGED_ACTIVE = tuple(sorted(('router_fast', 'ingest_balanced', 'ask_grounded',
    'memory_curator', 'memory_consolidate', 'task_planner', 'task_synthesis')))
ACTIVE = frozenset((*CHANGED_ACTIVE, 'converse_warm'))
ALL_PROFILES = ACTIVE | {'improvement_evaluator'}


def partition(prepared, old_raw, original_profiles, prior_candidate_profiles, partial_profiles, python_hashes):
    require(prepared['source_python_hashes'] == old_raw['source_python_hashes'] == python_hashes,
            'source Python bytes changed')
    require(set(original_profiles) == set(prior_candidate_profiles) == set(partial_profiles) == ALL_PROFILES,
            'exact active/dormant profile catalogue required')
    require(partial_profiles['converse_warm'] == original_profiles['converse_warm']
            and original_profiles['converse_warm']['max_tokens'] == 320
            and original_profiles['converse_warm']['primary'] == 'deepseek-4-flash',
            'conversation must retain exact original DeepSeek320 effective profile')
    require(sorted(name for name in ACTIVE if partial_profiles[name] != original_profiles[name]) == list(CHANGED_ACTIVE),
            'exactly seven changed active profiles required')
    for name in ALL_PROFILES - {'converse_warm'}:
        require(partial_profiles[name] == prior_candidate_profiles[name], 'changed role differs from qualified original candidate: ' + name)
    require(len(prepared['cases']) == len(old_raw['cases']) == old_raw['cases_expected'] == 24,
            'complete original24 evidence required')
    cases = {c['id']: c for c in prepared['cases']}
    results = {r['id']: r for r in old_raw['cases']}
    require(len(cases) == len(results) == 24 and set(cases) == set(results), 'closed original case IDs required')
    require({c['id'] for c in cases.values() if c['profile'] == 'converse_warm'} == set(CONVERSE_IDS),
            'exact three excluded conversation cases required')
    selected, carried = [], []
    for original in prepared['cases']:
        case = deepcopy(original)
        previous = results[case['id']]
        require(case['synthetic'] is True and case['input_sha256'] == sha(canonical(case['messages'])), 'retained synthetic input differs')
        require(case['runtime_profile'] == prior_candidate_profiles[case['profile']], 'retained effective candidate profile differs')
        if case['id'] in CONVERSE_IDS:
            continue
        require(case['profile'] in CHANGED_ACTIVE and case['runtime_profile'] == partial_profiles[case['profile']],
                'carried profile/control mapping changed')
        require(previous['profile'] == case['profile'] and previous['model'] == partial_profiles[case['profile']]['primary']
                and previous['input_sha256'] == case['input_sha256'], 'retained paid request binding differs')
        require(previous['status'] == 'functional_passed_pending_manual' and previous['functional']['status'] == 'passed',
                'only passing original changed-role evidence may be carried')
        require(isinstance(previous.get('raw'), str) and sha(previous['raw'].encode()) == previous['raw_sha256']
                and previous['usage'] is not None and previous['usage']['kind'] == 'chat'
                and previous['usage']['model'] == previous['model'], 'known original raw/accounting binding required')
        item = deepcopy(previous)
        item.update(provenance='carried_exact_changed_role', carried_input_sha256=case['input_sha256'],
                    carried_raw_sha256=previous['raw_sha256'], new_paid_calls=0)
        selected.append(case)
        carried.append(item)
    require(len(selected) == len(carried) == 21 and sorted({c['profile'] for c in selected}) == list(CHANGED_ACTIVE),
            'strict21 cases/seven changed active roles coverage required')
    unchanged = {'converse_warm': sha(canonical(original_profiles['converse_warm']))}
    return selected, carried, unchanged


def run(root, prior_root, prior_run, output):
    initial, prior_initial = read_json(root / 'prepared.json'), read_json(prior_root / 'prepared.json')
    require(not output.exists(), 'exclusive partial proof output required')
    from trial_config import parse_env_bytes, MODEL_KEYS, changed_model_keys
    original_raw = (root / 'original.env').read_bytes()
    partial_raw = (root / 'candidate.env').read_bytes()
    prior_candidate_raw = (prior_root / 'candidate.env').read_bytes()
    require(sha(original_raw) == initial['before_env_sha256'] == prior_initial['before_env_sha256']
            == sha((prior_root / 'original.env').read_bytes()) == sha(Path('/etc/tg-ingest-agent.env').read_bytes()),
            'original/live production environment changed')
    require(sha(partial_raw) == initial['candidate_env_sha256']
            and sha(prior_candidate_raw) == prior_initial['candidate_env_sha256'], 'candidate environment pin changed')
    require(initial['identity']['source_revision'] == prior_initial['identity']['source_revision']
            and initial['runtime_hashes'] == prior_initial['runtime_hashes'], 'runtime source identity changed')
    source = Path(initial['source'])
    for name, expected in initial['runtime_hashes'].items():
        path = source / ('tg_ingest_agent.py' if name == 'agent.py' else name)
        require(path.is_file() and sha(path.read_bytes()) == expected, 'runtime source changed: ' + name)
    sys.path.insert(0, str(source))
    import common, llm, validate_cases
    original_env, partial_env = parse_env_bytes(original_raw), parse_env_bytes(partial_raw)
    changed_model_keys(original_env, partial_env)
    original_cfg = common.load_config(original_env)
    cfg = common.load_config(partial_env)
    prior_cfg = common.load_config(parse_env_bytes(prior_candidate_raw))
    require(original_cfg.db_path == prior_cfg.db_path == cfg.db_path
            and original_cfg.task_cost_limit_usd == cfg.task_cost_limit_usd == PER_CALL,
            'database or native task limit changed')
    prepared, prior_raw = read_json(prior_run / 'cases.json'), read_json(prior_run / 'raw.json')
    require(prior_raw['schema'] == 'cara.model-trial-raw/v1' and prior_raw['cohort'] == 'candidate'
            and prior_raw['candidate_env_sha256'] == prior_initial['candidate_env_sha256']
            and prior_raw['prepared_sha256'] == sha((prior_run / 'cases.json').read_bytes())
            and prior_raw['source_revision'] == initial['identity']['source_revision']
            and prior_raw['runtime_hashes'] == initial['runtime_hashes'], 'original candidate proof pins differ')
    python_hashes = {p.name: sha(p.read_bytes()) for p in sorted(source.glob('*.py'))}
    original_profiles, partial_profiles = llm.profiles(original_cfg), llm.profiles(cfg)
    selected, carried, unchanged = partition(prepared, prior_raw, original_profiles,
        llm.profiles(prior_cfg), partial_profiles, python_hashes)
    overrides = {key: partial_env[key] for key in MODEL_KEYS if key in partial_env}
    conn = sqlite3.connect('file:' + str(cfg.db_path) + '?mode=ro', uri=True, timeout=3)
    conn.row_factory = sqlite3.Row
    try:
        before_changes = conn.total_changes
        usage = usage_rows(conn)
        guard(usage, 0)
        require(tuple(llm.budget_limits(original_cfg, conn)) == tuple(llm.budget_limits(cfg, conn)) == (2.0, 12.0),
                'effective daily/monthly budgets must remain2/12')
        indexed = {row['id']: row for row in usage}
        for item in carried:
            require(item['usage'] == indexed.get(item['usage']['id']), 'retained paid ledger row changed')
        with redirect_stdout(io.StringIO()):
            for case, item in zip(selected, carried):
                result = validate_cases.validate_response(source, case, item['raw'], overrides)
                require(result['status'] == 'passed', 'changed-role functional replay failed under partial config')
                item.update(prior_functional_sha256=sha(canonical(item['functional'])), functional=result,
                            functional_revalidated_without_model_calls=True)
        require(conn.total_changes == before_changes and usage_rows(conn) == usage, 'partial qualification wrote accounting')
    finally:
        conn.close()
    profile_proof = {'schema': 'cara-model-trial-unchanged-profile/v1', 'profile': 'converse_warm',
        'effective_original': original_profiles['converse_warm'], 'effective_candidate': partial_profiles['converse_warm'],
        'effective_profile_sha256': unchanged['converse_warm'], 'source_python_hashes': python_hashes,
        'source_revision': initial['identity']['source_revision'], 'original_env_sha256': initial['before_env_sha256'],
        'candidate_env_sha256': initial['candidate_env_sha256'], 'excluded_case_ids': list(CONVERSE_IDS),
        'failed_conversation_trials_not_promoted': True, 'paid_calls': 0}
    publish(root / 'qualification-partial.STARTED', {'at': datetime.now(timezone.utc).isoformat(),
        'output': str(output), 'candidate_env_sha256': initial['candidate_env_sha256'],
        'prior_raw_sha256': sha((prior_run / 'raw.json').read_bytes()), 'paid_calls': 0})
    output.mkdir(mode=0o700)
    publish(output / 'unchanged-conversation-proof.json', profile_proof)
    partial_prepared = deepcopy(prepared)
    partial_prepared.update(cases=selected, config_overrides=overrides,
        qualification_scope='changed_profiles_only', changed_active_profiles=list(CHANGED_ACTIVE),
        unchanged_profile_sha256s=unchanged, excluded_case_ids=list(CONVERSE_IDS),
        dormant_excluded_profiles=['improvement_evaluator'])
    publish(output / 'cases.json', partial_prepared)
    ids = [case['id'] for case in selected]
    summary = {'schema': 'cara-model-trial-partial/v1', 'status': 'functional_passed_pending_manual',
        'at': datetime.now(timezone.utc).isoformat(), 'qualification_scope': 'changed_profiles_only',
        'changed_active_profiles': list(CHANGED_ACTIVE), 'unchanged_profile_sha256s': unchanged,
        'dormant_excluded_profiles': ['improvement_evaluator'], 'excluded_case_ids': list(CONVERSE_IDS),
        'case_ids': ids, 'cases': carried, 'cases_expected': 21, 'functional_cases_total': 21,
        'functional_cases_passed': 21, 'missing_case_ids': [], 'new_paid_calls': 0,
        'source_revision': initial['identity']['source_revision'], 'runtime_hashes': initial['runtime_hashes'],
        'source_python_hashes': python_hashes, 'candidate_env_sha256': initial['candidate_env_sha256'],
        'before_env_sha256': initial['before_env_sha256'], 'prepared_sha256': sha((output / 'cases.json').read_bytes()),
        'unchanged_conversation_proof_sha256': sha((output / 'unchanged-conversation-proof.json').read_bytes()),
        'prior_raw_sha256': sha((prior_run / 'raw.json').read_bytes()), 'qualification_ceiling_usd': CEILING,
        'per_call_ceiling_usd': PER_CALL, 'effective_daily_budget_usd': 2.0, 'effective_monthly_budget_usd': 12.0,
        'usage': usage, 'unknown_usage_ids': [], 'accounted_cost_usd': sum(row['cost_usd'] for row in usage),
        'manual_quality_passed': False, 'operator_messages_sent': 0, 'live_business_data_changed': False,
        'failed_conversation_trials_not_promoted': True}
    publish(output / 'raw.json', summary)
    publish(output / 'responses.json', {item['id']: item['raw'] for item in carried})
    print(json.dumps({'status': summary['status'], 'functional_cases_total': 21,
        'functional_cases_passed': 21, 'changed_active_profiles': list(CHANGED_ACTIVE),
        'new_paid_calls': 0, 'aggregate_cost_usd': summary['accounted_cost_usd']}))
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--prior-root', type=Path, required=True)
    parser.add_argument('--prior-run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.root, args.prior_root, args.prior_run, args.output)
