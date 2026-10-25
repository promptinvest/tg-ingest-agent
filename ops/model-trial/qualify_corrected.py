"""Fresh Sol640 conversation compatibility cohort; unchanged results are carried.

Uses the same existing qualification ledger, USD5 aggregate and USD0.15 per-call
ceiling. Old candidate/baseline results stay immutable. Each of the three changed
conversation cases gets one fresh primary request and at most its original single
corrective request. This module never drives profile retries or fallbacks.
"""
import argparse
from contextlib import contextmanager, redirect_stdout
from copy import deepcopy
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sqlite3
import sys
import time

sys.dont_write_bytecode = True
from qualify_trial import CEILING, PER_CALL, canonical, guard, publish, require, sha

SKILL = 'model_trial_20261025'
CONVERSE_IDS = ('assistant-current-work-en', 'assistant-requested-structure-en',
                'assistant-uncertain-delivery-en')
FIELDS = 'id,kind,model,tokens_in,tokens_out,seconds,cost_usd'


def now():
    return datetime.now(timezone.utc).isoformat()


def usage_rows(conn):
    return [dict(row) for row in conn.execute('SELECT ' + FIELDS + ' FROM llm_usage WHERE skill=? ORDER BY id', (SKILL,))]


def read_json(path):
    require(path.is_file() and not path.is_symlink(), 'regular retained input required: ' + path.name)
    return json.loads(path.read_text(encoding='utf-8'))


def selected_cases(prepared, old_raw, old_profiles, new_profiles, current_python_hashes):
    """Pure provenance partition: exactly21 identical carries and3 fresh cases."""
    require(prepared['source_python_hashes'] == old_raw['source_python_hashes'] == current_python_hashes,
            'source Python bytes changed; prior results cannot be carried')
    require(len(prepared['cases']) == len(old_raw['cases']) == old_raw['cases_expected'] == 24,
            'complete original24 candidate cases required')
    cases = {case['id']: case for case in prepared['cases']}
    results = {result['id']: result for result in old_raw['cases']}
    require(len(cases) == len(results) == 24 and set(cases) == set(results), 'duplicate or mismatched prior case IDs')
    require({case['id'] for case in cases.values() if case['profile'] == 'converse_warm'} == set(CONVERSE_IDS),
            'exactly the three original conversation cases required')
    require(set(old_profiles) == set(new_profiles), 'profile catalogue changed')
    for name, old in old_profiles.items():
        if name == 'converse_warm':
            expected = dict(old, primary='openai-gpt-6-1-sol', max_tokens=640)
            require(old['primary'] == 'anthropic-claude-sonnet-5.5'
                    and old['max_tokens'] == 320 and new_profiles[name] == expected,
                    'only approved conversation Sonnet320toSol640 may change')
        else:
            require(old == new_profiles[name], 'unchanged profile controls differ: ' + name)
    updated = deepcopy(prepared)
    carried = []
    fresh = []
    for case in updated['cases']:
        old_case = cases[case['id']]
        previous = results[case['id']]
        require(case['input_sha256'] == sha(canonical(case['messages'])) and case['synthetic'] is True,
                'retained synthetic case input changed')
        require(case['runtime_profile'] == old_profiles[case['profile']], 'retained effective profile mismatch')
        require(previous['profile'] == case['profile'] and previous['model'] == old_profiles[case['profile']]['primary']
                and previous['input_sha256'] == case['input_sha256'], 'prior raw request/profile binding mismatch')
        require(isinstance(previous.get('raw'), str) and sha(previous['raw'].encode()) == previous['raw_sha256'],
                'retained prior raw response missing or changed')
        require(previous['usage'] is not None and previous['usage']['kind'] == 'chat'
                and previous['usage']['model'] == previous['model'], 'prior known paid outcome required')
        if case['id'] in CONVERSE_IDS:
            require(case['max_tokens'] == 320 and case['caller_kwargs'] == {'profile': 'converse_warm'},
                    'unexpected original conversation caller controls')
            case['runtime_profile'] = deepcopy(new_profiles['converse_warm'])
            case['max_tokens'] = 640
            fresh.append(case)
        else:
            require(previous['status'] == 'functional_passed_pending_manual'
                    and previous['functional']['status'] == 'passed', 'nonconversation prior functional evidence must pass')
            require(case == old_case and case['runtime_profile'] == new_profiles[case['profile']],
                    'carried input/profile/control bytes differ')
            item = deepcopy(previous)
            item.update(provenance='carried_exact_unchanged', carried_raw_sha256=previous['raw_sha256'],
                        carried_input_sha256=case['input_sha256'], new_paid_calls=0)
            carried.append(item)
    require(len(carried) == 21 and len(fresh) == 3, 'closed21carry3fresh partition required')
    return updated, carried, fresh


class LedgerGateway:
    """A single primary request with committed reservations and no replay."""
    def __init__(self, llm, cfg, conn, output):
        self.llm, self.cfg, self.conn, self.output = llm, cfg, conn, output
        self.original_reserve = llm.call_budget.reserve
        self.last_reservation = None
        self.calls = []

    def reserve(self, *args, **kwargs):
        require(args[1] is self.conn and args[2] == SKILL, 'unbound qualification reservation')
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            guard(usage_rows(self.conn), args[5])
            reserved = self.original_reserve(*args, **kwargs)
        except BaseException:
            self.conn.rollback()
            raise
        self.last_reservation = reserved
        return reserved

    @contextmanager
    def installed(self):
        self.llm.call_budget.reserve = self.reserve
        try:
            yield self
        finally:
            self.llm.call_budget.reserve = self.original_reserve

    def paid(self, case, phase, messages, profile_name, kwargs):
        require(phase in ('first', 'corrective') and profile_name == 'converse_warm', 'closed conversation phase required')
        require(len(self.calls) < 6 and not any(call['case_id'] == case['id'] and call['phase'] == phase for call in self.calls),
                'duplicate phase or more than six requests refused')
        require(not any(row['kind'].startswith('unknown_') for row in usage_rows(self.conn)), 'unknown prior outcome; stop')
        profile = self.llm.profiles(self.cfg)[profile_name]
        require(profile == case['runtime_profile'] and profile['max_tokens'] == case['max_tokens'] == 640,
                'exact effective640 conversation profile required')
        require(kwargs == {'profile': 'converse_warm'}, 'actual conversation caller kwargs changed')
        label = case['id'] + '-' + phase
        prefix = self.output / label
        record = {'case_id': case['id'], 'phase': phase, 'at': now(), 'profile': profile_name,
                  'model': profile['primary'], 'messages': messages, 'input_sha256': sha(canonical(messages)),
                  'max_tokens': 640, 'caller_kwargs': kwargs, 'transport_dispatched': False}
        publish(prefix.with_suffix('.prepared.json'), record)
        self.calls.append(record)
        self.last_reservation = None
        start = time.monotonic()
        try:
            request = self.llm.prepare_chat(self.cfg, self.conn, SKILL, messages,
                max_tokens=640, model=profile['primary'], temperature=profile.get('temperature', 0), timeout=profile['timeout'])
            publish(prefix.with_suffix('.reserved.json'), {'reservation_id': self.last_reservation,
                'usage': next(row for row in usage_rows(self.conn) if row['id'] == self.last_reservation)})
            try:
                record['transport_dispatched'] = True
                data = self.llm.transport_chat(request)
            except Exception as error:
                self.llm.finish_chat(self.cfg, self.conn, request, error=error)
                raise RuntimeError('gateway did not propagate a transport failure')
            try:
                publish(prefix.with_suffix('.returned.json'), {'at': now(), 'data': data,
                    'input_sha256': record['input_sha256'], 'reservation_id': self.last_reservation})
            except BaseException:
                self.llm.finish_chat(self.cfg, self.conn, request, data=data)
                raise
            raw = self.llm.finish_chat(self.cfg, self.conn, request, data=data)
            record.update(status='completed', raw=raw, raw_sha256=sha(raw.encode()),
                          finish_reason=(data.get('choices') or [{}])[0].get('finish_reason'),
                          provider_usage=data.get('usage', {}))
        except Exception as error:
            record.update(status='failed', error=type(error).__name__ + ': ' + str(error)[:1500])
        record['elapsed_seconds'] = round(time.monotonic() - start, 3)
        record['usage'] = next((row for row in usage_rows(self.conn) if row['id'] == self.last_reservation), None)
        publish(prefix.with_suffix('.result.json'), record)
        return record


def run(root, prior_root, prior_run, output):
    initial, prior_initial = read_json(root / 'prepared.json'), read_json(prior_root / 'prepared.json')
    require(not output.exists(), 'exclusive corrected output required')
    from trial_config import parse_env_bytes, MODEL_KEYS
    old_env_raw, new_env_raw = (prior_root / 'candidate.env').read_bytes(), (root / 'candidate.env').read_bytes()
    require(sha(old_env_raw) == prior_initial['candidate_env_sha256']
            and sha(new_env_raw) == initial['candidate_env_sha256'], 'candidate environment pins changed')
    require(initial['before_env_sha256'] == prior_initial['before_env_sha256']
            == sha((root / 'original.env').read_bytes()) == sha((prior_root / 'original.env').read_bytes())
            == sha(Path('/etc/tg-ingest-agent.env').read_bytes()), 'original/live production environment changed')
    require(initial['identity']['source_revision'] == prior_initial['identity']['source_revision']
            and initial['runtime_hashes'] == prior_initial['runtime_hashes'], 'source identity changed')
    source = Path(initial['source'])
    for name, expected in initial['runtime_hashes'].items():
        path = source / ('tg_ingest_agent.py' if name == 'agent.py' else name)
        require(path.is_file() and sha(path.read_bytes()) == expected, 'runtime source changed: ' + name)
    sys.path.insert(0, str(source))
    import common, llm, repair_cases, validate_cases
    old_cfg, cfg = common.load_config(parse_env_bytes(old_env_raw)), common.load_config(parse_env_bytes(new_env_raw))
    require(old_cfg.db_path == cfg.db_path and cfg.task_cost_limit_usd == PER_CALL, 'database/task cap changed')
    old_prepared, old_raw = read_json(prior_run / 'cases.json'), read_json(prior_run / 'raw.json')
    require(old_raw['schema'] == 'cara.model-trial-raw/v1' and old_raw['cohort'] == 'candidate'
            and old_raw['candidate_env_sha256'] == prior_initial['candidate_env_sha256']
            and old_raw['prepared_sha256'] == sha((prior_run / 'cases.json').read_bytes())
            and old_raw['source_revision'] == initial['identity']['source_revision']
            and old_raw['runtime_hashes'] == initial['runtime_hashes'], 'original candidate raw proof pins changed')
    source_hashes = {p.name: sha(p.read_bytes()) for p in sorted(source.glob('*.py'))}
    prepared, carried, fresh = selected_cases(old_prepared, old_raw, llm.profiles(old_cfg), llm.profiles(cfg), source_hashes)
    selected_env = parse_env_bytes(new_env_raw)
    overrides = {key: selected_env[key] for key in MODEL_KEYS if key in selected_env}
    retained_cases = {case['id']: case for case in prepared['cases']}
    # These21 requests need no new paid inference: unchanged raw answers are
    # re-applied to the unchanged real handlers under the corrected config.
    with redirect_stdout(io.StringIO()):
        for item in carried:
            prior_functional_sha = sha(canonical(item['functional']))
            rechecked = validate_cases.validate_response(source, retained_cases[item['id']], item['raw'], overrides)
            require(rechecked['status'] == 'passed', 'carried result failed corrected-config functional replay')
            item.update(functional=rechecked, prior_functional_sha256=prior_functional_sha,
                        functional_revalidated_without_model_calls=True)
    prepared['config_overrides'] = overrides
    prepared['preparation_policy'] = 'retained exact24 inputs; only three conversation primaries/caps Sonnet320toSol640 updated'
    conn = sqlite3.connect('file:' + str(cfg.db_path) + '?mode=rw', uri=True, timeout=3)
    conn.row_factory = sqlite3.Row
    try:
        rows = usage_rows(conn)
        guard(rows, 0)
        by_id = {row['id']: row for row in rows}
        for previous in old_raw['cases']:
            require(previous['usage'] == by_id.get(previous['usage']['id']), 'original paid case ledger row differs')
        publish(root / 'qualification-corrected.STARTED', {'at': now(), 'output': str(output),
            'prior_raw_sha256': sha((prior_run / 'raw.json').read_bytes()),
            'candidate_env_sha256': initial['candidate_env_sha256']})
        output.mkdir(mode=0o700)
        publish(output / 'cases.json', prepared)
        publish(output / 'carry.json', {'status': 'exact_unchanged', 'case_ids': [c['id'] for c in carried],
            'prior_raw_sha256': sha((prior_run / 'raw.json').read_bytes()), 'source_python_hashes': source_hashes,
            'paid_calls': 0, 'old_first_pass_verdicts_preserved': True})
        results = list(carried)
        responses = {r['id']: r['raw'] for r in carried}
        gateway = LedgerGateway(llm, cfg, conn, output)
        with gateway.installed():
            for case in fresh:
                entry = {'id': case['id'], 'provenance': 'fresh_changed_conversation_cap',
                    'profile': case['profile'], 'model': case['runtime_profile']['primary'],
                    'input_sha256': case['input_sha256'], 'first_pass_old_verdict_preserved': True}
                first = gateway.paid(case, 'first', case['messages'], case['profile'], case['caller_kwargs'])
                entry['attempts'] = [first]
                try:
                    require(first['status'] == 'completed', 'fresh primary request did not complete')
                    retained = [first['raw']]
                    with redirect_stdout(io.StringIO()):
                        replay = repair_cases.replay_with_responses(source, case, retained, overrides)
                    if replay['status'] == 'needs_next':
                        next_request = replay['needs_next']
                        publish(output / (case['id'] + '-repair-needed.json'), replay)
                        second = gateway.paid(case, 'corrective', next_request['messages'],
                                              next_request['profile'], next_request['kwargs'])
                        entry['attempts'].append(second)
                        require(second['status'] == 'completed', 'corrective request did not complete')
                        retained.append(second['raw'])
                        with redirect_stdout(io.StringIO()):
                            replay = repair_cases.replay_with_responses(source, case, retained, overrides)
                    require(replay['status'] != 'needs_next', 'third request forbidden')
                    entry.update(status=replay['status'], functional=replay, raw=first['raw'],
                        raw_sha256=first['raw_sha256'], final_text=replay['final_text'])
                    responses[case['id']] = {'first_raw': first['raw'],
                        'corrective_raw': retained[1] if len(retained) == 2 else None,
                        'final_text': replay['final_text'], 'repair_trace': replay['requests']}
                except Exception as error:
                    entry.update(status='failed', error=type(error).__name__ + ': ' + str(error)[:1500])
                publish(output / (case['id'] + '-merged.json'), entry)
                results.append(entry)
                if any(row['kind'].startswith('unknown_') for row in usage_rows(conn)):
                    publish(output / 'STOPPED-UNKNOWN.json', {'at': now(), 'usage': usage_rows(conn), 'automatic_replay': False})
                    break
        ordered = {r['id']: r for r in results}
        results = [ordered[c['id']] for c in prepared['cases'] if c['id'] in ordered]
        all_usage = usage_rows(conn)
        summary = {'schema': 'cara.model-trial-corrected/v1', 'at': now(), 'source_revision': initial['identity']['source_revision'],
            'runtime_hashes': initial['runtime_hashes'], 'source_python_hashes': source_hashes,
            'candidate_env_sha256': initial['candidate_env_sha256'], 'prepared_sha256': sha((output / 'cases.json').read_bytes()),
            'prior_raw_sha256': sha((prior_run / 'raw.json').read_bytes()), 'cases': results, 'cases_expected': 24,
            'carried_cases': 21, 'fresh_cases_completed': len(results) - 21,
            'new_prepared_requests': len(gateway.calls),
            'new_paid_calls': sum(call['transport_dispatched'] for call in gateway.calls),
            'functional_cases_passed': sum(r['status'] == 'functional_passed_pending_manual' for r in results),
            'qualification_ceiling_usd': CEILING, 'per_call_ceiling_usd': PER_CALL,
            'usage': all_usage, 'accounted_cost_usd': sum(r['cost_usd'] for r in all_usage),
            'unknown_usage_ids': [r['id'] for r in all_usage if r['kind'].startswith('unknown_')],
            'manual_quality_passed': False, 'operator_messages_sent': 0, 'live_business_data_changed': False}
        publish(output / 'raw.json', summary)
        publish(output / 'responses.json', responses)
        print(json.dumps({'completed': len(results), 'carried': 21, 'new_paid_calls': summary['new_paid_calls'],
            'passed': summary['functional_cases_passed'], 'aggregate_cost_usd': summary['accounted_cost_usd'],
            'unknown': summary['unknown_usage_ids']}))
        return summary
    finally:
        conn.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--prior-root', type=Path, required=True)
    parser.add_argument('--prior-run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.root, args.prior_root, args.prior_run, args.output)
