"""Read-only model-retirement inventory. Never imports the live application.

Output contains hashes, counts, model roles and service identities only. This is
an observation, not deployment authorization or a substitute for release gates.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess

from env_bytes import parse_env_bytes

APP = Path('/opt/tg-ingest-agent')
DB = Path('/var/lib/tg-ingest-agent/ingest.db')
ENV = Path('/etc/tg-ingest-agent.env')
UNITS = ('tg-ingest-agent', 'cara-worker', 'cara-fleet', 'whisper-server',
         'cara-mentor', 'cara-mentor-runner')
PROPERTIES = ('LoadState', 'ActiveState', 'SubState', 'UnitFileState', 'MainPID',
              'NRestarts', 'InvocationID', 'ExecMainStartTimestamp',
              'ExecMainStartTimestampMonotonic')
TIMER_PROPERTIES = ('LoadState', 'ActiveState', 'SubState', 'UnitFileState', 'InvocationID')
MODEL_KEYS = frozenset({'DO_CHAT_MODEL', 'ROUTER_MODEL', 'STT_MODEL', 'DO_STT_MODEL', 'DO_EMBEDDING_MODEL',
                        'DO_VISION_MODEL', 'VISION_MODEL', 'LLM_PROFILES_JSON',
                        'PRICING_JSON', 'BUDGET_DAILY_USD', 'BUDGET_MONTHLY_USD',
                        'TASK_COST_LIMIT_USD'})
SHA = re.compile(r'[a-f0-9]{64}\Z')
ACCEPTED_UNKNOWN_COLUMNS = ('id','ts','day','month','skill','kind','model',
                            'tokens_in','tokens_out','seconds','cost_usd','trace_id')
ACCEPTED_UNKNOWN_COUNT = 35
ACCEPTED_UNKNOWN_SHA256 = 'c38aedf3a167aebd3d8277cfa74f6f18e5ebc7619c3cf147d559d401eb72150f'
ACCEPTED_UNKNOWN_RECEIPT = '53e37d7a211867869f080254593ca70ed20001aaca975c3fc93398345eb3c040'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def safe_file(path, maximum=32 * 1024 * 1024):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ValueError('regular non-symlink evidence required')
    if info.st_size > maximum:
        raise ValueError('evidence exceeds size bound')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        opened = os.fstat(fd)
        if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError('evidence changed while opening')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(maximum + 1)
        after = os.fstat(fd)
        identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
        if identity(opened) != identity(after) or len(raw) != opened.st_size:
            raise ValueError('evidence changed while reading')
        return raw, {'sha256': hashlib.sha256(raw).hexdigest(), 'uid': info.st_uid,
                     'gid': info.st_gid, 'mode': stat.S_IMODE(info.st_mode)}
    finally:
        os.close(fd)


def parse_env(raw):
    """Strict native EnvironmentFile parsing; no application imports or effects."""
    return parse_env_bytes(raw)


def unit_state(unit):
    if not re.fullmatch(r'[a-zA-Z0-9_.@:-]{1,128}',unit):
        raise ValueError('fixed service or timer name required')
    properties = TIMER_PROPERTIES if unit.endswith('.timer') else PROPERTIES
    command = ['/usr/bin/systemctl', 'show', unit]
    for name in properties:
        command.extend(('-p', name))
    result = subprocess.run(command, capture_output=True, text=True, timeout=15,
                            env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}, cwd='/')
    if result.returncode:
        raise ValueError('service identity read failed: '+unit)
    values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if set(values) != set(properties):
        raise ValueError('incomplete service identity: '+unit)
    return values


def started_utc(value):
    # systemctl under LANG=C emits "Thu 2026-10-29 10:32:18 UTC".
    parsed = datetime.strptime(value, '%a %Y-%m-%d %H:%M:%S UTC')
    return parsed.replace(tzinfo=timezone.utc).isoformat()


def unknown_observation(conn, started):
    cursor = conn.execute("SELECT * FROM llm_usage WHERE kind LIKE 'unknown_%' ORDER BY id")
    columns = [column[0] for column in cursor.description]
    rows = [dict(zip(columns, row)) for row in cursor]
    cursor = conn.execute("SELECT * FROM llm_usage WHERE kind LIKE 'unknown_%' "
                          'AND julianday(ts)>=julianday(?) ORDER BY id', (started,))
    current = [dict(zip(columns, row)) for row in cursor]
    if not set(ACCEPTED_UNKNOWN_COLUMNS) <= set(columns):
        raise ValueError('accepted original unknown projection missing')
    # Exact accepted cache_release.rows_digest serialization. No exception
    # approval is imported: this proves unchanged historical rows and charges.
    encode_cell=lambda value: {'blob':base64.b64encode(value).decode('ascii')} if isinstance(value,bytes) else value
    encoded=sorted(canonical([encode_cell(row[name]) for name in ACCEPTED_UNKNOWN_COLUMNS]) for row in rows)
    original_hash=hashlib.sha256()
    for raw in encoded:
        original_hash.update(str(len(raw)).encode('ascii')+b':'+raw)
    historical={'count':len(encoded),'sha256':original_hash.hexdigest(),
                'cache_nonnull':sum(row.get('cache_usage_json') is not None for row in rows),
                'receipt_sha256':ACCEPTED_UNKNOWN_RECEIPT}
    return {'count': len(rows), 'sha256': digest(rows),
            'reserved_usd': sum(row['cost_usd'] for row in rows),
            'current_process_count': len(current), 'current_process_sha256': digest(current),
            'current_process_reserved_usd': sum(row['cost_usd'] for row in current),
            'projection_sha256': digest(columns), 'process_cutoff': started,
            'accepted_history':historical,
            'serialization': 'all original columns including cache_usage_json; ORDER BY id; canonical JSON UTF-8'}


def accepted_history_valid(value):
    return value.get('accepted_history') == {
        'count':ACCEPTED_UNKNOWN_COUNT,'sha256':ACCEPTED_UNKNOWN_SHA256,
        'cache_nonnull':0,'receipt_sha256':ACCEPTED_UNKNOWN_RECEIPT}


def database_observation(path, started):
    # mode=ro is WAL aware; immutable=1 would incorrectly ignore active WAL data.
    with closing(sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True, timeout=2)) as conn:
        conn.execute('PRAGMA query_only=ON')
        conn.execute('PRAGMA trusted_schema=OFF')
        conn.execute('BEGIN')
        integrity = conn.execute('PRAGMA quick_check').fetchall()
        foreign_keys = conn.execute('PRAGMA foreign_key_check').fetchall()
        schema = conn.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall()
        counts = {}
        queries = {
            'active_tasks': "SELECT COUNT(*) FROM assistant_tasks WHERE status NOT IN ('completed','failed','cancelled')",
            'active_steps': "SELECT COUNT(*) FROM assistant_task_steps WHERE status IN ('claimed','running','waiting_worker','waiting_fleet')",
            'active_fleet': "SELECT COUNT(*) FROM fleet_requests WHERE state NOT IN ('done','failed','ambiguous','cancelled')",
            'pending_updates': "SELECT COUNT(*) FROM telegram_updates WHERE status='pending'",
            'active_jobs': "SELECT COUNT(*) FROM jobs WHERE status IN ('claimed','running','processing')",
            'pending_replies': "SELECT COUNT(*) FROM reply_outbox WHERE state IN ('pending','sending')",
            'active_attempts': "SELECT COUNT(*) FROM task_step_attempts WHERE status='started'",
            'active_approvals': "SELECT COUNT(*) FROM task_approvals WHERE status IN ('claimed','executing')",
        }
        for key, query in queries.items():
            counts[key] = conn.execute(query).fetchone()[0]
        unknowns = unknown_observation(conn, started)
        prefs = conn.execute("SELECT key,value FROM preferences WHERE key IN ('budget_daily_usd','budget_monthly_usd') ORDER BY key").fetchall()
        conn.rollback()
    return {'quick_check_ok': integrity == [('ok',)],
            'foreign_key_violations': len(foreign_keys), 'schema_sha256': digest(schema),
            'tables': sum(row[0] == 'table' for row in schema), 'work_counts': counts,
            'unknowns': unknowns, 'budget_preferences_sha256': digest(prefs)}


def collect():
    units = {unit: unit_state(unit) for unit in UNITS}
    main = units['tg-ingest-agent']
    if main['ActiveState'] != 'active' or not main['MainPID'].isdigit() or int(main['MainPID']) <= 0:
        raise ValueError('current active main process required')
    raw, env_metadata = safe_file(ENV)
    values = parse_env(raw)
    profiles = json.loads(values.get('LLM_PROFILES_JSON', '{}'))
    roles = {}
    if not isinstance(profiles, dict):
        raise ValueError('profile object required')
    for role, profile in profiles.items():
        if isinstance(profile, dict):
            roles[role] = {key: profile[key] for key in ('primary', 'fallbacks') if key in profile}
    pid = main['MainPID']
    process_raw = (Path('/proc') / pid / 'environ').read_bytes()
    process_env = dict(item.decode().split('=', 1) for item in process_raw.split(b'\0') if b'=' in item)
    model_hashes = {key: digest(values.get(key)) for key in sorted(MODEL_KEYS)}
    loaded_hashes = {key: digest(process_env.get(key)) for key in sorted(MODEL_KEYS)}
    manifest_raw, manifest_metadata = safe_file(APP / 'DEPLOYMENT.json')
    marker = json.loads(manifest_raw)
    native_identity = {key: marker.get(key) for key in ('status', 'source_revision', 'source_dirty',
                                                      'build_version', 'deployment_id')}
    native_identity['sha256'] = manifest_metadata['sha256']
    runtime = {path.name: safe_file(path)[1] for path in sorted(APP.iterdir())
               if path.suffix in ('.py', '.sh') and path.is_file()}
    observed = {'schema': 'cara.model-retirement-read-only-observation/v1',
                'at': datetime.now(timezone.utc).isoformat(), 'units': units,
                'identity': native_identity, 'environment': env_metadata,
                'environment_key_hashes': model_hashes, 'loaded_key_hashes': loaded_hashes,
                'loaded_configuration_matches': model_hashes == loaded_hashes,
                'roles': roles, 'direct_models': {key: values[key] for key in
                    ('DO_CHAT_MODEL', 'ROUTER_MODEL', 'STT_MODEL', 'DO_STT_MODEL', 'DO_EMBEDDING_MODEL', 'DO_VISION_MODEL', 'VISION_MODEL')
                    if key in values}, 'runtime': runtime,
                'database': database_observation(DB, started_utc(main['ExecMainStartTimestamp'])),
                'service_actions': 0, 'model_calls': 0, 'database_writes': 0,
                'authorization': None}
    if unit_state('tg-ingest-agent') != main:
        raise ValueError('main process changed during observation')
    if safe_file(ENV)[1] != env_metadata:
        raise ValueError('environment changed during observation')
    return observed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--read-only', action='store_true', required=True)
    parser.parse_args()
    if os.name != 'posix' or os.geteuid() != 0:
        raise SystemExit('read-only authenticated Linux root collection required')
    print(canonical(collect()).decode())


if __name__ == '__main__':
    main()
