"""One-shot, bounded retirement qualification; no deployment or automatic retries.

Preparation and replay use synthetic state and deny network/process effects.
Execution permits only direct inference and native new-row usage reservation/
settlement. Functional success still needs a separate raw-bound semantic review.
"""
from contextlib import ExitStack, contextmanager, redirect_stdout
from datetime import datetime, timezone
import argparse
import hashlib
import importlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import struct
import sys
import tarfile
import time
from unittest import mock
import zlib

import transform_config
import env_bytes as envlib

sys.dont_write_bytecode = True
CEILING = .25
PER_CALL = .15
MIGRATION_PREFIX = 'model_retirement_20261029_'
TEXT_MODELS = frozenset({'deepseek-v4.1-flash-1017', 'openai-gpt-oss-120b'})
VISION_MODEL = 'deepseek-v4.1-flash'
ACTIVE_PROFILES = frozenset({'router_fast', 'ingest_balanced', 'ask_grounded',
    'converse_warm', 'memory_curator', 'memory_consolidate', 'task_planner', 'task_synthesis'})
MODEL_KEYS = frozenset({'DO_CHAT_MODEL', 'ROUTER_MODEL', 'LLM_PROFILES_JSON', 'PRICING_JSON'})


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def read_file(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), 'regular input required')
    return path.read_bytes()


def publish(path, value):
    raw = value if isinstance(value, bytes) else canonical(value)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def source_hashes(source):
    result = {}
    for path in sorted(Path(source).rglob('*')):
        relative = path.relative_to(source)
        if any(part in {'.git', '__pycache__'} for part in relative.parts):
            continue
        require(not path.is_symlink(), 'source symlinks refused')
        if path.is_file():
            result[relative.as_posix()] = sha(path.read_bytes())
    return result


def verify_archive(archive_raw, expected_hashes, source_commit):
    """Bind the claimed commit/archive to every exact frozen source file."""
    files, seen = {}, set()
    with tarfile.open(fileobj=io.BytesIO(archive_raw), mode='r:*') as archive:
        comment = archive.pax_headers.get('comment')
        require(comment is None or comment.strip() == source_commit,
                'Git archive commit comment differs')
        for member in archive.getmembers():
            name = member.name
            require(name and not name.startswith('/') and '\\' not in name
                    and ':' not in name and '..' not in name.split('/'), 'unsafe archive path')
            path = PurePosixPath(name).as_posix().rstrip('/')
            require(path not in {'', '.'} and path not in seen, 'duplicate or empty archive path')
            seen.add(path)
            require(member.isfile() or member.isdir(), 'archive link or special member refused')
            if member.isdir():
                continue
            require(member.size <= 32 * 1024 * 1024, 'archive member exceeds source bound')
            stream = archive.extractfile(member)
            require(stream is not None, 'archive source file unreadable')
            with stream:
                raw = stream.read(member.size + 1)
            require(len(raw) == member.size, 'archive member size differs')
            files[path] = sha(raw)
    require(files == expected_hashes, 'source files differ from claimed archive')
    return files


def libraries(source):
    source = Path(source).resolve()
    require(Path(transform_config.__file__).resolve().parent == source / 'ops' / 'model-retirement',
            'transform library from another source')
    require(Path(envlib.__file__).resolve().parent == source / 'ops' / 'model-retirement',
            'environment library from another source')
    sys.path.insert(0, str(source / 'ops' / 'model-trial'))
    fixtures = importlib.import_module('prepare_cases')
    validator = importlib.import_module('validate_cases')
    for module in (fixtures, validator):
        require(Path(module.__file__).resolve().parent == source / 'ops' / 'model-trial',
                'qualification library from another source')
    fixtures.load_source(source)
    return fixtures, validator, envlib


def expand_cases(prepared, profiles):
    require(len(prepared['cases']) == 24, 'all 24 original synthetic cases required')
    require({case['profile'] for case in prepared['cases']} == ACTIVE_PROFILES,
            'every active profile must retain original cases')
    result = []
    for case in prepared['cases']:
        profile = profiles[case['profile']]
        require(case['runtime_profile'] == profile, 'prepared profile mismatch')
        for model in dict.fromkeys(profile.get('fallbacks', [])):
            if model not in TEXT_MODELS:
                continue
            require(model != profile['primary'], 'primary model qualification forbidden')
            result.append({'id': case['id'] + '__' + model, 'kind': 'text',
                'case': case, 'profile': case['profile'], 'model': model,
                'messages': case['messages'], 'max_tokens': case['max_tokens'],
                'temperature': profile.get('temperature', 0), 'timeout': profile['timeout'],
                'input_sha256': case['input_sha256'], 'rubric': case['rubric']})
    require({item['profile'] for item in result} == ACTIVE_PROFILES,
            'replacement fallback coverage missing an active profile')
    require({item['model'] for item in result} == TEXT_MODELS, 'both text replacements required')
    return result


# A small fixed bitmap font keeps the fixture self-contained and byte deterministic.
FONT = {
    'A': ('01110','10001','10001','11111','10001','10001','10001'),
    'B': ('11110','10001','10001','11110','10001','10001','11110'),
    'C': ('01111','10000','10000','10000','10000','10000','01111'),
    'D': ('11110','10001','10001','10001','10001','10001','11110'),
    'E': ('11111','10000','10000','11110','10000','10000','11111'),
    'I': ('11111','00100','00100','00100','00100','00100','11111'),
    'K': ('10001','10010','10100','11000','10100','10010','10001'),
    'L': ('10000','10000','10000','10000','10000','10000','11111'),
    'N': ('10001','11001','11001','10101','10011','10011','10001'),
    'O': ('01110','10001','10001','10001','10001','10001','01110'),
    'R': ('11110','10001','10001','11110','10100','10010','10001'),
    'S': ('01111','10000','10000','01110','00001','00001','11110'),
    'T': ('11111','00100','00100','00100','00100','00100','00100'),
    'U': ('10001','10001','10001','10001','10001','10001','01110'),
    'V': ('10001','10001','10001','10001','10001','01010','00100'),
    '0': ('01110','10001','10011','10101','11001','10001','01110'),
    '2': ('01110','10001','00001','00010','00100','01000','11111'),
    '4': ('00010','00110','01010','10010','11111','00010','00010'),
    '9': ('01110','10001','10001','01111','00001','00001','01110'),
}


def image_bytes(kind):
    require(kind in {'book', 'invoice', 'blank'}, 'unknown synthetic image')
    width, height = 720, 480
    pixels = bytearray([255]) * (width * height * 3)

    def rect(x, y, w, h, colour):
        for row in range(y, y + h):
            for column in range(x, x + w):
                offset = (row * width + column) * 3
                pixels[offset:offset + 3] = bytes(colour)

    def text(value, x, y, scale=7):
        for letter in value:
            if letter != ' ':
                for row, pattern in enumerate(FONT[letter]):
                    for column, bit in enumerate(pattern):
                        if bit == '1':
                            rect(x + column * scale, y + row * scale, scale, scale, (10, 20, 30))
            x += 6 * scale

    if kind == 'book':
        rect(24, 20, 672, 440, (180, 215, 245))
        text('CEDAR NOTES', 100, 100)
        text('ALICE', 220, 220)
        text('2099', 240, 310)
        text('BOOK', 250, 395, 5)
    elif kind == 'invoice':
        rect(24, 30, 672, 12, (40, 100, 180))
        text('CEDAR INVOICE', 65, 85)
        text('TOTAL USD 24', 90, 245)
    rows = b''.join(b'\0' + pixels[y * width * 3:(y + 1) * width * 3] for y in range(height))

    def chunk(label, data):
        return struct.pack('>I', len(data)) + label + data + struct.pack('>I', zlib.crc32(label + data))

    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows, 9)) + chunk(b'IEND', b''))


VISION_CASES = (
    ('vision-describe-ru', 'describe', 'book', 'ru'),
    ('vision-describe-en', 'describe', 'invoice', 'en'),
    ('vision-classify-ru', 'classify', 'book', 'ru'),
    ('vision-classify-en', 'classify', 'invoice', 'en'),
    ('vision-extract-book', 'extract', 'book', 'en'),
    ('vision-extract-blank', 'extract', 'blank', 'ru'),
)


def vision_call(cfg, path, operation, lang):
    import llm
    import media
    if operation == 'describe':
        return llm.describe_image(cfg, None, 'media', cfg.vision_model, path, lang=lang)
    if operation == 'classify':
        return media.classify(cfg, None, path, lang=lang)
    return media.extract(cfg, None, path, lang=lang)


def capture_vision(fixtures, cfg, root):
    import llm
    cases = []
    for kind in ('book', 'invoice', 'blank'):
        publish(root / (kind + '.png'), image_bytes(kind))
    for identity, operation, kind, lang in VISION_CASES:
        calls = []

        def capture(config, conn, skill, messages, **kwargs):
            calls.append((messages, kwargs))
            raise fixtures.Captured()

        with fixtures.isolation(), mock.patch.object(llm, 'chat', side_effect=capture):
            try:
                vision_call(cfg, root / (kind + '.png'), operation, lang)
            except fixtures.Captured:
                pass
        require(len(calls) == 1, 'vision fixture must capture one native request')
        messages, options = calls[0]
        require(options['model'] == VISION_MODEL, 'vision candidate mismatch')
        cases.append({'id': identity, 'kind': 'vision', 'operation': operation,
            'image': kind + '.png', 'image_sha256': sha(read_file(root / (kind + '.png'))),
            'lang': lang, 'profile': 'vision', 'model': VISION_MODEL, 'messages': messages,
            'max_tokens': options['max_tokens'], 'temperature': options.get('temperature', 0),
            'timeout': options.get('timeout') or cfg.llm_timeout,
            'input_sha256': sha(canonical(messages)), 'rubric': [
                'Use only visible content; no inferred titles, authors, facts or actions.',
                'Book shows CEDAR NOTES, ALICE, 2099, BOOK; invoice shows CEDAR INVOICE and TOTAL USD 24.',
                'Respond in the requested Russian/English where the native prompt requests it.',
                'Blank image has no readable book/movie titles and must return empty entries.',
                'No provider error, truncation, malformed JSON, wrong script or hallucinated read.']})
    return cases


def replay_vision(fixtures, cfg, root, case, raw):
    import llm
    image = root / case['image']
    require(sha(read_file(image)) == case['image_sha256'], 'vision fixture changed')
    captures = []

    def replay(config, conn, skill, messages, **kwargs):
        captures.append(messages)
        require(len(captures) == 1, 'unexpected second vision call')
        require(sha(canonical(messages)) == case['input_sha256'], 'native vision prompt changed')
        return raw

    with fixtures.isolation(), mock.patch.object(llm, 'chat', side_effect=replay):
        result = vision_call(cfg, image, case['operation'], case['lang'])
    require(len(captures) == 1, 'native vision replay absent')
    if case['operation'] == 'describe':
        require(bool(result), 'native image description rejected')
    elif case['operation'] == 'classify':
        expected = 'media' if case['image'] == 'book.png' else 'document'
        require(result[0] == expected and bool(result[1]), 'native image classification incorrect')
    else:
        parsed = llm.parse_llm_json(raw)
        require(isinstance(parsed, dict) and isinstance(parsed.get('entries'), list),
                'image extraction JSON invalid')
        if case['image'] == 'blank.png':
            require(parsed['entries'] == [] and result == [], 'blank image invented titles')
        else:
            require(len(result) == 1 and result[0]['title'] == 'CEDAR NOTES'
                    and result[0]['kind'] == 'book' and result[0].get('creator') == 'ALICE'
                    and result[0].get('year') == '2099', 'visible book facts not preserved')
    return {'status': 'passed', 'native_result': result, 'manual_quality_required': True}


def prepare(source, archive, source_commit, environment, output, attempt):
    source, archive, environment, output = map(lambda p: Path(p).resolve(),
                                               (source, archive, environment, output))
    require(re.fullmatch('[a-f0-9]{40}', source_commit), 'full source commit required')
    require(re.fullmatch('[a-z0-9][a-z0-9-]{0,45}', attempt), 'bounded unique attempt required')
    require(not output.is_relative_to(source), 'evidence must be outside frozen source')
    hashes = source_hashes(source)
    archive_raw = read_file(archive)
    verify_archive(archive_raw, hashes, source_commit)
    fixtures, _, envlib = libraries(source)
    original_raw = read_file(environment)
    original = envlib.parse_env_bytes(original_raw)
    candidate, transform = transform_config.transform_environment(original)
    candidate_raw = envlib.update_env_bytes(original_raw,
        {key: value for key, value in candidate.items() if original.get(key) != value})
    import common
    import llm
    cfg = common.load_config(candidate)
    require(cfg.do_base_url.rstrip('/') == 'https://inference.do-ai.run/v1', 'fixed DO endpoint required')
    require(cfg.vision_model == VISION_MODEL, 'candidate vision model required')
    overrides = {key: candidate[key] for key in MODEL_KEYS if key in candidate}
    if 'TIMEZONE_OFFSET_HOURS' in candidate:
        overrides['TIMEZONE_OFFSET_HOURS'] = candidate['TIMEZONE_OFFSET_HOURS']
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    publish(output / 'PREPARING.json', {'at': now(), 'attempt': attempt})
    with redirect_stdout(io.StringIO()):
        prepared = fixtures.prepare(source, overrides)
        cases = expand_cases(prepared, llm.profiles(cfg))
        cases.extend(capture_vision(fixtures, cfg, output))
    require(len({case['id'] for case in cases}) == len(cases), 'duplicate qualification case')
    publish(output / 'candidate.env', candidate_raw)
    publish(output / 'inputs.json', {'prepared': prepared, 'cases': cases})
    plan = {'schema': 'cara.model-retirement-prepared/v1', 'at': now(),
        'attempt': attempt, 'skill': MIGRATION_PREFIX + attempt,
        'migration_skill_prefix': MIGRATION_PREFIX,
        'source': str(source), 'source_commit': source_commit, 'source_hashes': hashes,
        'archive': str(archive), 'archive_sha256': sha(archive_raw),
        'environment': str(environment), 'original_environment_sha256': sha(original_raw),
        'candidate_environment_sha256': sha(candidate_raw), 'transform': transform,
        'qualification_inputs_sha256': sha(read_file(output / 'inputs.json')),
        'coverage': coverage(cases), 'cases_expected': len(cases),
        'aggregate_ceiling_usd': CEILING, 'per_call_ceiling_usd': PER_CALL,
        'paid_calls': 0, 'messages_sent': 0, 'semantic_quality_approved': False}
    publish(output / 'plan.json', plan)
    return {'status': 'prepared', 'plan_sha256': sha(read_file(output / 'plan.json')),
            'cases': len(cases), 'coverage': plan['coverage'], 'paid_calls': 0}


def coverage(cases):
    result = {}
    for case in cases:
        result.setdefault(case['profile'], {}).setdefault(case['model'], []).append(case['id'])
    return result


def guard(rows, cost):
    require(type(cost) in (int, float) and math.isfinite(cost) and 0 <= cost <= PER_CALL,
            'per-call qualification ceiling exceeded')
    require(all(row['kind'] in {'chat', 'rejected_chat'} for row in rows),
            'unknown prior qualification outcome forbids continuation')
    require(all(type(row['cost_usd']) in (int, float) and math.isfinite(row['cost_usd'])
                and row['cost_usd'] >= 0 and (row['kind'] != 'rejected_chat' or row['cost_usd'] == 0)
                for row in rows), 'invalid qualification ledger')
    spent = sum(row['cost_usd'] for row in rows)
    require(spent + cost <= CEILING + 1e-12, 'aggregate qualification ceiling exceeded')
    return spent


def ledger_rows(conn, skill):
    return [dict(row) for row in conn.execute(
        'SELECT id,kind,model,tokens_in,tokens_out,seconds,cost_usd FROM llm_usage WHERE skill=? ORDER BY id',
        (skill,))]


def migration_rows(conn):
    """One cumulative allowance across every attempt of this migration."""
    return [dict(row) for row in conn.execute(
        'SELECT id,skill,kind,model,tokens_in,tokens_out,seconds,cost_usd FROM llm_usage '
        'WHERE substr(skill,1,?)=? ORDER BY id', (len(MIGRATION_PREFIX), MIGRATION_PREFIX))]


@contextmanager
def ledger_only(conn, skill):
    """Native accounting only, with new-row ownership and SQL mutation controls."""
    import call_budget
    import common
    import store
    original_reserve, original_settle = call_budget.reserve, call_budget.settle
    original_kv_set = store.kv_set
    owned = set()
    pending = set()
    phase = [None]
    require(skill.startswith(MIGRATION_PREFIX) and len(skill) > len(MIGRATION_PREFIX),
            'fixed migration accounting prefix required')
    require(not ledger_rows(conn, skill), 'attempt skill already has usage; no replay')

    def authorizer(action, table, column, database, trigger):
        if trigger:
            return sqlite3.SQLITE_DENY
        if action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_TRANSACTION}:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ and table in {'llm_usage', 'preferences'}:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_INSERT and table == 'llm_usage' and phase[0] == 'reserve':
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_UPDATE and table == 'llm_usage' and phase[0] == 'settle':
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    def reserve(cfg, database, selected_skill, kind, model, cost, limits, error_type):
        require(database is conn and selected_skill == skill and kind == 'chat', 'foreign reservation refused')
        require(model in TEXT_MODELS | {VISION_MODEL}, 'unselected model refused')
        conn.execute('BEGIN IMMEDIATE')
        try:
            rows = ledger_rows(conn, skill)
            require({row['id'] for row in rows} == owned, 'qualification ledger ownership changed')
            guard(migration_rows(conn), cost)
            phase[0] = 'reserve'
            row = original_reserve(cfg, conn, skill, kind, model, cost, limits, error_type)
            owned.add(row)
            pending.add(row)
            return row
        except BaseException:
            conn.rollback()
            raise
        finally:
            phase[0] = None

    def settle(database, row_id, *args, **kwargs):
        require(database is conn and row_id in owned and row_id in pending,
                'foreign or repeated settlement refused')
        current = conn.execute('SELECT skill FROM llm_usage WHERE id=?', (row_id,)).fetchone()
        require(current and current['skill'] == skill, 'owned reservation changed')
        phase[0] = 'settle'
        try:
            result = original_settle(conn, row_id, *args, **kwargs)
            pending.remove(row_id)
            return result
        finally:
            phase[0] = None

    def health_hint(database, key, value):
        if database is not conn:
            return original_kv_set(database, key, value)
        require(database is conn and key.startswith('model_chat_success:'), 'unexpected nonledger mutation')

    conn.set_authorizer(authorizer)
    try:
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(call_budget, 'reserve', side_effect=reserve))
            stack.enter_context(mock.patch.object(call_budget, 'settle', side_effect=settle))
            stack.enter_context(mock.patch.object(store, 'kv_set', side_effect=health_hint))
            stack.enter_context(mock.patch.object(store, '_trace_id', return_value=None))
            stack.enter_context(mock.patch.object(common, 'current_trace', return_value=None))
            stack.enter_context(mock.patch.object(common, 'watchdog_ping', return_value=False))
            yield owned
    finally:
        conn.set_authorizer(None)


def execute_case(cfg, conn, skill, case, result=None):
    import llm
    result = {} if result is None else result
    result.update(id=case['id'], profile=case['profile'], model=case['model'],
                  input_sha256=case['input_sha256'], at=now())
    require(sha(canonical(case['messages'])) == case['input_sha256'], 'qualification prompt changed')
    prepared = llm.prepare_chat(cfg, conn, skill, case['messages'], max_tokens=case['max_tokens'],
        model=case['model'], temperature=case['temperature'], timeout=case['timeout'])
    result['reservation_id'] = prepared['reserved']
    try:
        data = llm.transport_chat(prepared)
    except Exception as error:
        llm.finish_chat(cfg, conn, prepared, error=error)
        raise AssertionError('gateway failed to raise transport failure')
    result['provider_response'] = data
    raw = llm.finish_chat(cfg, conn, prepared, data=data)
    result.update(raw=raw, raw_sha256=sha(raw.encode()), provider_usage=data.get('usage'),
                  provider_model=data.get('model'),
                  finish_reason=(data.get('choices') or [{}])[0].get('finish_reason'))
    require(result['provider_model'] == case['model'], 'returned model identity differs or is missing')
    require(result['finish_reason'] != 'length', 'truncated model answer')
    return result


def execute(output, expected_plan_sha):
    output = Path(output).resolve()
    plan_raw = read_file(output / 'plan.json')
    require(sha(plan_raw) == expected_plan_sha, 'exact plan hash required')
    plan = json.loads(plan_raw)
    require(plan['schema'] == 'cara.model-retirement-prepared/v1', 'qualification plan schema invalid')
    require(plan['aggregate_ceiling_usd'] == CEILING and plan['per_call_ceiling_usd'] == PER_CALL,
            'qualification limits changed')
    require(plan['migration_skill_prefix'] == MIGRATION_PREFIX
            and plan['skill'] == MIGRATION_PREFIX + plan['attempt'], 'migration accounting scope changed')
    require(not (output / 'STARTED.json').exists(), 'qualification already started; never replay')
    source = Path(plan['source'])
    require(source_hashes(source) == plan['source_hashes'], 'frozen source changed')
    require(sha(read_file(plan['archive'])) == plan['archive_sha256'], 'source archive changed')
    require(sha(read_file(plan['environment'])) == plan['original_environment_sha256'], 'live environment changed')
    require(sha(read_file(output / 'candidate.env')) == plan['candidate_environment_sha256'], 'candidate environment changed')
    require(sha(read_file(output / 'inputs.json')) == plan['qualification_inputs_sha256'], 'qualification inputs changed')
    inputs = json.loads(read_file(output / 'inputs.json'))
    require(len(inputs['cases']) == plan['cases_expected'] and coverage(inputs['cases']) == plan['coverage'],
            'qualification coverage changed')
    for case in inputs['cases']:
        require(sha(canonical(case['messages'])) == case['input_sha256'], 'prepared prompt changed')
        if case['kind'] == 'vision':
            require(case['image'] in {'book.png', 'invoice.png', 'blank.png'}, 'unknown image path')
            require(sha(read_file(output / case['image'])) == case['image_sha256'], 'vision fixture changed')
    fixtures, validator, envlib = libraries(source)
    import common
    cfg = common.load_config(envlib.parse_env_bytes(read_file(output / 'candidate.env')))
    require(str(cfg.db_path) == '/var/lib/tg-ingest-agent/ingest.db', 'authoritative Cara ledger required')
    publish(output / 'STARTED.json', {'at': now(), 'plan_sha256': expected_plan_sha, 'skill': plan['skill']})
    conn = sqlite3.connect('file:' + str(cfg.db_path) + '?mode=rw', uri=True, timeout=3,
                           cached_statements=0)
    conn.row_factory = sqlite3.Row
    results = []
    overrides = inputs['prepared']['config_overrides']
    try:
        with ledger_only(conn, plan['skill']):
            for index, case in enumerate(inputs['cases']):
                result = {'id': case['id'], 'profile': case['profile'], 'model': case['model'],
                          'input_sha256': case['input_sha256']}
                started = time.monotonic()
                try:
                    with redirect_stdout(io.StringIO()):
                        execute_case(cfg, conn, plan['skill'], case, result)
                    rows = ledger_rows(conn, plan['skill'])
                    guard(migration_rows(conn), 0)
                    # Replay is outside ledger_only's SQL authorizer: it owns separate
                    # synthetic databases, and mocks every model/network/process effect.
                    with redirect_stdout(io.StringIO()):
                        if case['kind'] == 'text':
                            result['functional'] = validator.validate_response(source, case['case'], result['raw'], overrides)
                        else:
                            result['functional'] = replay_vision(fixtures, cfg, output, case, result['raw'])
                    result['status'] = 'functional_passed_pending_semantic'
                except Exception as error:
                    result['status'] = 'failed'
                    result['error_type'] = type(error).__name__
                    result['error_detail'] = str(error).replace(cfg.do_key, '<redacted>')[:2000]
                    # Provider/raw details stay in private evidence, not terminal output.
                result['elapsed_seconds'] = round(time.monotonic() - started, 6)
                result['ledger'] = ledger_rows(conn, plan['skill'])
                publish(output / ('case-' + str(index + 1).zfill(3) + '.json'), result)
                results.append(result)
                print(json.dumps({'case': case['id'], 'status': result['status']}), flush=True)
                if result['status'] == 'failed':
                    break
        rows = ledger_rows(conn, plan['skill'])
        aggregate_rows = migration_rows(conn)
    finally:
        conn.close()
    passed = len(results) == len(inputs['cases']) and all(
        result['status'] == 'functional_passed_pending_semantic' for result in results)
    summary = {'schema': 'cara.model-retirement-raw/v1', 'at': now(),
        'status': 'functional_passed_pending_semantic' if passed else 'failed',
        'source_commit': plan['source_commit'], 'archive_sha256': plan['archive_sha256'],
        'candidate_environment_sha256': plan['candidate_environment_sha256'],
        'qualification_inputs_sha256': plan['qualification_inputs_sha256'],
        'plan_sha256': expected_plan_sha, 'coverage': coverage(results),
        'expected_coverage': plan['coverage'], 'cases_expected': plan['cases_expected'],
        'cases_completed': len(results), 'cases': results, 'usage': rows,
        'accounted_cost_usd': sum(row['cost_usd'] for row in rows),
        'known_cost_usd': sum(row['cost_usd'] for row in rows if row['kind'] == 'chat'),
        'unknown_reserve_usd': sum(row['cost_usd'] for row in rows if row['kind'].startswith('unknown_')),
        'unknown_usage_ids': [row['id'] for row in rows if row['kind'].startswith('unknown_')],
        'migration_skill_prefix': MIGRATION_PREFIX, 'migration_usage': aggregate_rows,
        'migration_accounted_cost_usd': sum(row['cost_usd'] for row in aggregate_rows),
        'migration_known_cost_usd': sum(row['cost_usd'] for row in aggregate_rows if row['kind'] == 'chat'),
        'migration_unknown_reserve_usd': sum(row['cost_usd'] for row in aggregate_rows if row['kind'].startswith('unknown_')),
        'migration_unknown_usage_ids': [row['id'] for row in aggregate_rows if row['kind'].startswith('unknown_')],
        'aggregate_ceiling_usd': CEILING, 'per_call_ceiling_usd': PER_CALL,
        'semantic_quality_approved': False, 'messages_sent': 0, 'live_business_data_changed': False}
    publish(output / 'raw.json', summary)
    return {'status': summary['status'], 'raw_sha256': sha(read_file(output / 'raw.json')),
            'cases_completed': len(results), 'accounted_cost_usd': summary['accounted_cost_usd'],
            'migration_accounted_cost_usd': summary['migration_accounted_cost_usd'],
            'unknown_count': len(summary['migration_unknown_usage_ids']),
            'attempt_unknown_count': len(summary['unknown_usage_ids'])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prep = commands.add_parser('prepare')
    for name in ('source', 'archive', 'source-commit', 'environment', 'output', 'attempt'):
        prep.add_argument('--' + name, required=True)
    run = commands.add_parser('execute')
    run.add_argument('--output', required=True)
    run.add_argument('--plan-sha256', required=True)
    args = parser.parse_args()
    try:
        result = (prepare(args.source, args.archive, args.source_commit, args.environment,
                          args.output, args.attempt) if args.command == 'prepare'
                  else execute(args.output, args.plan_sha256))
    except Exception as error:
        print(json.dumps({'status': 'failed', 'error_type': type(error).__name__}), flush=True)
        return 1
    print(json.dumps(result), flush=True)
    return 1 if result['status'] == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
