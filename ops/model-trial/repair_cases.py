"""Replay a retained first answer through Cara's existing bounded repair path.

No model call occurs here. A missing second answer stops at the exact original
corrective request and returns its immutable input for separately accounted use.
Earlier first-pass failures remain failures; this produces distinct full-flow
evidence. No application prompt, guard, token cap or fallback is changed.
"""
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
from pathlib import Path
import re
import sys
from unittest import mock

sys.dont_write_bytecode = True
import prepare_cases as fixtures

SUPPORTED = frozenset({'assistant-current-work-en', 'assistant-requested-structure-en',
                       'assistant-uncertain-delivery-en'})
TABLES = ('reminders', 'assistant_tasks', 'assistant_task_steps', 'task_approvals')


class NeedsNext(BaseException):
    pass


def _snapshot(agent):
    return {name: [tuple(row) for row in agent.conn.execute('SELECT * FROM ' + name + ' ORDER BY id')]
            for name in TABLES}


def _clock_from_case(case, overrides):
    """Recover only fixture wall time, never replace real model/guard content."""
    from testlib import make_config
    cfg = make_config(**overrides)
    systems = '\n'.join(str(m.get('content', '')) for m in case['messages'] if m.get('role') == 'system')
    found = re.findall(r"Right now it's (\d{2}:\d{2}) on [A-Za-z]+, (\d{4}-\d{2}-\d{2}) for the boss", systems)
    fixtures.require(len(found) == 1, 'exact retained fixture wall time required')
    hour, day = found[0]
    return datetime.fromisoformat(day + 'T' + hour + ':00+00:00') - timedelta(hours=cfg.timezone_offset)


def replay_with_responses(source, case, responses, overrides=None):
    source = fixtures.load_source(source)
    fixtures.require(case['id'] in SUPPORTED, 'only the three retained conversation cases are authorized')
    fixtures.require(isinstance(responses, list) and 1 <= len(responses) <= 2
        and all(isinstance(raw, str) and 0 < len(raw) <= 100_000 for raw in responses),
        'one or two bounded retained response strings required; no automatic third request')
    fixtures.require(fixtures.digest(case['messages']) == case['input_sha256'], 'retained first request changed')
    fixtures.require(case['profile'] == 'converse_warm' and case['recipe']['id'] == case['id'], 'retained case/profile binding differs')
    overrides = dict(overrides or {})
    fixtures.OVERRIDES = overrides
    import converse_svc, llm, relationship, store, texts, tg_ingest_agent
    frozen_utc = _clock_from_case(case, overrides)

    class FixtureClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen_utc.astimezone(tz) if tz is not None else frozen_utc.replace(tzinfo=None)

    requests = []
    next_request = [None]
    protected_before = [None]
    effects_preserved = [False]

    def invoke(call, response=None):
        agent = inspect.getclosurevars(call).nonlocals.get('agent')
        fixtures.require(agent is not None, 'original fixture handler/agent closure required')
        before = _snapshot(agent)
        protected_before[0] = fixtures.digest(before)
        system = case['messages'][0]['content']
        life_block = re.search(r'Your life right now \(stay consistent with it; you may add to it naturally as you live\):\n(.*?)(?:\n\n|$)', system, re.S)
        fixtures.require(life_block is not None, 'retained synthetic seeded life context required')
        life_order = [line[2:] for line in life_block.group(1).splitlines() if line.startswith('- ')]
        life_rows = {row['text']: row for row in store.life_facts(agent.conn, limit=40)}
        fixtures.require(len(life_order) == len(set(life_order)) and set(life_order) == set(life_rows),
                         'retained life context differs from exact synthetic seed facts')

        def retained_life(conn, limit=40):
            fixtures.require(conn is agent.conn, 'cross-fixture life read forbidden')
            return [life_rows[text] for text in life_order[:limit]]

        def retained_gateway(cfg, conn, skill, messages, **kwargs):
            fixtures.require(len(requests) < 2, 'third model request forbidden by bounded repair qualification')
            record = {'index': len(requests), 'messages': fixtures.canonical(messages).decode('utf-8'),
                      'profile': kwargs['profile'], 'skill': skill, 'kwargs': dict(kwargs),
                      'input_sha256': fixtures.digest(messages)}
            record['messages'] = __import__('json').loads(record['messages'])
            if not requests:
                fixtures.require(record['input_sha256'] == case['input_sha256'] and messages == case['messages'],
                                 'rebuilt first request differs from immutable retained request')
                fixtures.require(kwargs == case['caller_kwargs'], 'first caller kwargs changed')
            else:
                fixtures.require(kwargs['profile'] == case['profile'] and skill == 'converse', 'corrective profile or skill changed')
            index = len(requests)
            requests.append(record)
            if index >= len(responses):
                next_request[0] = dict(record)
                raise NeedsNext()
            record['retained_response_sha256'] = hashlib.sha256(responses[index].encode()).hexdigest()
            return responses[index]

        try:
            # ORDER BY RANDOM is ordinary production behavior. Reconstruct this
            # fixture's already-retained ordering without changing its facts.
            with mock.patch.object(llm, 'chat_profile', side_effect=retained_gateway), \
                 mock.patch.object(store, 'life_facts', side_effect=retained_life):
                result = call()
        finally:
            fixtures.require(before == _snapshot(agent), 'repair path changed reminders/tasks/steps/approvals')
            effects_preserved[0] = True
        return requests[0], result

    result = None
    with fixtures.isolation(), ExitStack() as stack:
        # Time is synthetic fixture state and is reconstructed from the retained
        # request. The unchanged original builders must reproduce every byte.
        for module in (converse_svc, tg_ingest_agent, relationship):
            stack.enter_context(mock.patch.object(module, 'datetime', FixtureClock))
        stack.enter_context(mock.patch.object(store, '_now', return_value=frozen_utc.isoformat()))
        stack.enter_context(mock.patch.object(fixtures, 'invoke', side_effect=invoke))
        try:
            _captured, result = fixtures.exercise(case['id'], responses[0])
        except NeedsNext:
            pass
    fixtures.require(effects_preserved[0], 'original protected effect checks did not run')
    base = {'schema': 'cara-model-switch-repair-replay/v1', 'case_id': case['id'],
            'first_request_sha256': case['input_sha256'], 'requests': requests,
            'retained_response_sha256s': [hashlib.sha256(raw.encode()).hexdigest() for raw in responses],
            'responses_consumed': sum('retained_response_sha256' in request for request in requests),
            'source_python_hashes': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(source.glob('*.py'))},
            'frozen_fixture_utc': frozen_utc.isoformat(), 'retained_seed_order_reconstructed': True,
            'protected_before_sha256': protected_before[0],
            'effects_preserved': True, 'paid_calls': 0, 'telegram_messages': 0,
            'earlier_first_pass_verdict_unchanged': True, 'manual_quality_passed': False}
    if next_request[0] is not None:
        base.update(status='needs_next', needs_next=next_request[0], final_text=None,
                    qualification_passed=False)
        return base
    fixtures.require(result is not None and result['reminders_unchanged'], 'original fixture result missing')
    final = result['answer']
    fallback = final == texts.T('en', 'action_not_done')
    base.update(status='failed' if fallback else 'functional_passed_pending_manual',
                final_text=final, replay_result=result, deterministic_fallback=fallback,
                qualification_passed=False)
    if fallback:
        base['semantic_failure'] = 'Existing deterministic containment fallback does not answer the requested task status and next step.'
    elif case['id'] == 'assistant-requested-structure-en':
        lines = [line.strip() for line in final.splitlines() if line.strip()]
        fixtures.require(len(lines) == 2 and re.match(r'^1[.)]\s+', lines[0])
                         and re.match(r'^2[.)]\s+', lines[1]), 'final answer does not preserve exactly two requested numbered items')
    return base
