"""Guarded configuration-only Cara model trial; never installs code or restores a DB."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

ENV = Path('/etc/tg-ingest-agent.env')
APP = Path('/opt/tg-ingest-agent')
EXPECTED_SOURCE = 'ee1a319ce53ddc941ebf4b760c084abd865afd19'
EXPECTED_ENV = 'e1ae7f6c65e4076a1a6fb93a87ecf200eb59745f048183acbbea389cfa64b733'
EXPECTED_PINS = '763f79140a92fbc2376bf004f217bfb7aab65d7b9d636500a70cb7ac33aa56da'
MAIN = 'tg-ingest-agent.service'
PEERS = ('cara-worker.service', 'cara-fleet.service', 'whisper-server.service')


def require(value, message):
    if not value:
        raise ValueError(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':')).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def publish(path, value, raw=False):
    data = value if raw else canonical(value)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def run(args, log=None, timeout=120):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            timeout=timeout, check=False)
    if log is not None:
        publish(log, result.stdout, raw=True)
    require(result.returncode == 0, 'command failed: ' + str(args[0]) + '; preserved log ' + str(log))
    return result.stdout.decode('utf-8', errors='replace')


def best_effort_publish(path, value, raw=False):
    """Recovery must proceed even when its evidence filesystem cannot be written."""
    try:
        publish(path, value, raw=raw)
        return True
    except OSError:
        return False


def recovery_run(args, log, timeout=120):
    # Keep subprocess failure authoritative, but a failed recovery-log write
    # cannot turn a successful systemctl start into an interrupted recovery.
    output = run(args, timeout=timeout)
    best_effort_publish(log, output.encode('utf-8'), raw=True)
    return output


def services():
    result = {}
    for unit in (MAIN, *PEERS):
        raw = run(['/usr/bin/systemctl', 'show', unit, '-p', 'ActiveState', '-p',
                   'MainPID', '-p', 'NRestarts', '-p', 'ActiveEnterTimestamp'])
        result[unit] = dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)
    return result


@contextmanager
def readonly(db):
    conn = sqlite3.connect('file:' + str(db) + '?mode=ro', uri=True, timeout=3)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    try:
        yield conn
    finally:
        conn.close()


def table_snapshot(conn):
    """Exact original schema/rows, including sqlite_sequence; no private bodies exported."""
    output = {}
    for name, sql in conn.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name"):
        quoted = '"' + name.replace('"', '""') + '"'
        columns = [row[1] for row in conn.execute('PRAGMA table_info(' + quoted + ')')]
        rows = []
        for row in conn.execute('SELECT * FROM ' + quoted):
            values = [{'bytes': value.hex()} if isinstance(value, bytes) else value for value in row]
            rows.append(digest(canonical(values)))
        output[name] = {'sql': sql, 'columns': columns, 'rows': len(rows),
                        'rows_sha256': digest(canonical(sorted(rows)))}
    return output


def backup_db(db, target):
    require(not target.exists(), 'backup already exists')
    source = sqlite3.connect('file:' + str(db) + '?mode=ro', uri=True, timeout=3)
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
        require(destination.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'backup integrity')
        require(destination.execute('PRAGMA foreign_key_check').fetchone() is None, 'backup foreign keys')
    finally:
        destination.close()
        source.close()
    os.chmod(target, 0o600)


def runtime_hashes():
    return {p.name: digest(p.read_bytes()) for p in sorted(APP.iterdir())
            if p.is_file() and p.suffix in ('.py', '.sh')}


def protected_snapshot(manifest):
    result = {}
    for path in manifest:
        p = Path(path)
        info = p.lstat()
        require(stat.S_ISREG(info.st_mode) and not p.is_symlink(), 'protected regular file required: ' + path)
        result[path] = {'sha256': digest(p.read_bytes()), 'uid': info.st_uid,
                        'gid': info.st_gid, 'mode': stat.S_IMODE(info.st_mode)}
    return result


def native_identity():
    raw = (APP / 'DEPLOYMENT.json').read_bytes()
    value = json.loads(raw)
    require(value['status'] == 'verified' and value['source_revision'] == EXPECTED_SOURCE,
            'accepted installed runtime identity differs')
    return {'sha256': digest(raw), 'deployment_id': value['deployment_id'],
            'source_revision': value['source_revision'], 'build_version': value['build_version']}


def verify_source(initial):
    source = Path(initial['source'])
    for name, expected in initial['runtime_hashes'].items():
        counterpart = source / ('tg_ingest_agent.py' if name == 'agent.py' else name)
        require(counterpart.is_file() and digest(counterpart.read_bytes()) == expected,
                'prepared source changed or missing: ' + name)


def idle(conn, since=None):
    issues = {}
    for name, query in (
        ('reply_delivery', "SELECT COUNT(*) FROM reply_outbox WHERE state IN ('pending','sending')"),
        ('telegram_intake', "SELECT COUNT(*) FROM telegram_updates WHERE status IN ('pending','processing')"),
        ('maintenance_claim', "SELECT COUNT(*) FROM task_approvals WHERE status IN ('claimed','executing')"),
        ('job_claim', "SELECT COUNT(*) FROM jobs WHERE status IN ('claimed','running','processing')"),
        ('task_step', "SELECT COUNT(*) FROM assistant_task_steps WHERE status IN ('claimed','running','waiting_worker','waiting_fleet')"),
        ('task_attempt', "SELECT COUNT(*) FROM task_step_attempts WHERE status='started'"),
        ('fleet_inference', "SELECT COUNT(*) FROM fleet_requests WHERE state NOT IN ('done','failed','cancelled','expired','rejected')"),
    ):
        count = conn.execute(query).fetchone()[0]
        if count:
            issues[name] = count
    since = since or (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    unknown = conn.execute("SELECT COUNT(*) FROM llm_usage WHERE kind LIKE 'unknown_%' AND ts>=?", (since,)).fetchone()[0]
    if unknown:
        issues['current_unknown_paid_inference'] = unknown
    require(not issues, 'runtime is not quiescent: ' + json.dumps(issues))


def process_since(state):
    stamp = state[MAIN]['ActiveEnterTimestamp']
    match = re.search(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', stamp)
    require(match is not None and stamp.endswith('UTC'), 'UTC main process start required')
    return match.group(0).replace(' ', 'T') + '+00:00'


def loaded_models(pid, candidate):
    from trial_config import MODEL_KEYS
    raw = Path('/proc/' + str(pid) + '/environ').read_bytes()
    loaded = dict(part.decode('utf-8').split('=', 1) for part in raw.split(b'\0') if b'=' in part)
    for key in MODEL_KEYS:
        require(loaded.get(key) == candidate.get(key), 'loaded model configuration differs: ' + key)
    return {key: digest((loaded.get(key) or '').encode()) for key in MODEL_KEYS}


def matching_model_environment(current, alternatives, keys):
    """Return a known model configuration or reject unreviewed current drift."""
    absent = object()
    for expected in alternatives:
        if all(current.get(key, absent) == expected.get(key, absent) for key in keys):
            return expected
    raise ValueError('current model environment is unrecognized; no overwrite or restart')


def resume_verified(root, prefix, expected, initial, before):
    """Resume the actual recorded model environment without changing code or data."""
    require(native_identity() == initial['identity'] and runtime_hashes() == initial['runtime_hashes'],
            'runtime changed during failure containment')
    state = services()
    if state[MAIN]['ActiveState'] != 'active':
        recovery_run(['/usr/bin/systemctl', 'start', MAIN], root / (prefix + '-start.log'))
        time.sleep(4)
        state = services()
    require(state[MAIN]['ActiveState'] == 'active' and int(state[MAIN]['MainPID']) > 1,
            'contained main is unavailable')
    loaded_models(state[MAIN]['MainPID'], expected)
    for peer in PEERS:
        require(state[peer] == before[peer], 'peer changed during failure containment: ' + peer)
    return state


def prepare(root, source, pins):
    from trial_config import parse_env_bytes, build_candidate, validate_candidate, update_env_bytes
    require(os.geteuid() == 0 and root.is_absolute() and not root.exists(), 'fresh protected root required')
    root.mkdir(mode=0o700, parents=False)
    publish(root / 'prepare.STARTED', {'at': now(), 'authority': 'owner requested model trial and revert if needed'})
    require(digest(ENV.read_bytes()) == EXPECTED_ENV, 'original environment changed')
    identity = native_identity()
    before = ENV.read_bytes()
    original = parse_env_bytes(before)
    sys.path.insert(0, str(source))
    import common, llm
    cfg = common.load_config(original)
    before_services = services()
    with readonly(cfg.db_path) as conn:
        limits = llm.budget_limits(cfg, conn)
        idle(conn, process_since(before_services))
        notice = [dict(row) for row in conn.execute('SELECT * FROM deployment_notifications')]
        schema = table_snapshot(conn)
    candidate = build_candidate(original, llm)
    validation = validate_candidate(original, candidate, llm, effective_budgets=limits)
    new_raw = update_env_bytes(before, {key: candidate.get(key) for key in candidate if original.get(key) != candidate.get(key)})
    require(parse_env_bytes(new_raw) == candidate, 'candidate serialization differs')
    manifest = json.loads(pins.read_text())
    manifest = manifest.get('manifest', manifest)
    require(len(manifest) == 78 and digest(canonical(manifest)) == EXPECTED_PINS,
            'exact accepted78 protected manifest required')
    captured = protected_snapshot(manifest)
    require(captured == manifest, 'accepted protected identities drifted before trial')
    publish(root / 'original.env', before, raw=True)
    publish(root / 'candidate.env', new_raw, raw=True)
    backup_db(cfg.db_path, root / 'online.sqlite')
    runtime = runtime_hashes()
    for name, expected in runtime.items():
        source_name = 'tg_ingest_agent.py' if name == 'agent.py' else name
        counterpart = source / source_name
        require(counterpart.is_file() and digest(counterpart.read_bytes()) == expected,
                'missing or differing accepted runtime source: ' + name)
    initial = {'schema': 'cara.model-trial-prepared/v1', 'at': now(), 'identity': identity,
               'source': str(source), 'database_path': str(cfg.db_path),
               'before_env_sha256': digest(before), 'candidate_env_sha256': digest(new_raw),
               'before_services': before_services, 'runtime_hashes': runtime, 'protected': captured,
               'tables': schema, 'notice_sha256': digest(canonical(notice)),
               'validation': validation, 'budget_limits_usd': list(limits)}
    publish(root / 'prepared.json', initial)
    print(json.dumps({'status': 'prepared', 'root': str(root), 'before_env_sha256': digest(before),
                      'candidate_env_sha256': digest(new_raw), 'tables': len(schema), 'runtime_files': len(runtime)}))


def replace_env(raw, expected):
    require(digest(ENV.read_bytes()) == expected, 'environment compare-and-swap failed')
    info = ENV.stat()
    require((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (0, 0, 0o600), 'environment ownership differs')
    temporary = ENV.with_name('.tg-ingest-agent.model-trial.' + str(os.getpid()))
    publish(temporary, raw, raw=True)
    os.chown(temporary, 0, 0)
    os.replace(temporary, ENV)
    fd = os.open(ENV.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def verify_preserved(initial, expected_env):
    require(native_identity() == initial['identity'], 'native deployment identity changed')
    require(runtime_hashes() == initial['runtime_hashes'], 'installed runtime changed')
    current = protected_snapshot(initial['protected'])
    for path, expected in initial['protected'].items():
        compare = dict(expected)
        if path == str(ENV):
            compare['sha256'] = expected_env
        require(current[path] == compare, 'protected file changed: ' + path)
    return current


def activate(root, proof, package_hash):
    from trial_config import parse_env_bytes
    initial = json.loads((root / 'prepared.json').read_text())
    require(not (root / 'activate.STARTED').exists(), 'activation is single-use')
    require(digest((root / 'candidate.env').read_bytes()) == initial['candidate_env_sha256']
            and digest((root / 'original.env').read_bytes()) == initial['before_env_sha256'],
            'selected original/candidate environment changed')
    verify_source(initial)
    qualification = json.loads(proof.read_text())
    require(qualification['schema'] == 'cara.model-trial-qualified/v1'
            and qualification['status'] == 'passed'
            and qualification['candidate_env_sha256'] == initial['candidate_env_sha256']
            and qualification['runtime_hashes'] == initial['runtime_hashes']
            and qualification['source_revision'] == initial['identity']['source_revision']
            and qualification['unknown_usage_ids'] == []
            and qualification['accounted_cost_usd'] <= qualification['qualification_ceiling_usd'] == 5.0
            and qualification['manual_quality_passed'] is True
            and qualification['functional_cases_passed'] == qualification['functional_cases_total'] >= 24,
            'fresh passed exact-configuration qualification required')
    require(0 <= (datetime.now(timezone.utc) - datetime.fromisoformat(qualification['at'])).total_seconds() <= 7200,
            'qualification is stale or future-dated')
    require(package_hash and len(package_hash) == 64, 'exact selected operator package hash required')
    package_manifest = json.loads((root.parent / 'operator-files.json').read_text())
    for name, expected in package_manifest.items():
        require(Path(name).name == name and digest((Path(__file__).parent / name).read_bytes()) == expected,
                'selected operator file differs: ' + name)
    require(digest(canonical(package_manifest)) == package_hash, 'selected operator package binding differs')
    publish(root / 'activate.STARTED', {'at': now(), 'proof_sha256': digest(proof.read_bytes()),
            'package_sha256': package_hash, 'window_ends_at': (datetime.now(timezone.utc) + timedelta(minutes=35)).isoformat()})
    changed = False
    transition_started = False
    try:
        verify_preserved(initial, initial['before_env_sha256'])
        sys.path.insert(0, initial['source'])
        import common
        cfg = common.load_config(parse_env_bytes((root / 'original.env').read_bytes()))
        before = services()
        with readonly(cfg.db_path) as conn:
            idle(conn, process_since(before))
        require(all(value['ActiveState'] == 'active' for value in before.values()), 'required service unavailable')
        transition_started = True
        run(['/usr/bin/systemctl', 'stop', MAIN], root / 'stop.log')
        require(services()[MAIN]['MainPID'] == '0', 'main did not quiesce')
        backup_db(cfg.db_path, root / 'stopped.sqlite')
        with readonly(cfg.db_path) as conn:
            idle(conn, process_since(before))
            stopped = table_snapshot(conn)
        with readonly(root / 'stopped.sqlite') as conn:
            require(table_snapshot(conn) == stopped, 'stopped backup row/schema parity failed')
        publish(root / 'stopped-parity.json', {'at': now(), 'tables': stopped})
        candidate_raw = (root / 'candidate.env').read_bytes()
        require(digest(candidate_raw) == initial['candidate_env_sha256'], 'candidate input changed')
        replace_env(candidate_raw, initial['before_env_sha256'])
        changed = True
        with readonly(cfg.db_path) as conn:
            require(table_snapshot(conn) == stopped, 'config transition changed database')
        run(['/usr/bin/systemctl', 'start', MAIN], root / 'start.log')
        time.sleep(4)
        current = services()
        require(current[MAIN]['ActiveState'] == 'active' and int(current[MAIN]['MainPID']) > 1, 'new main unavailable')
        for peer in PEERS:
            require(current[peer] == before[peer], 'peer process changed: ' + peer)
        model_hashes = loaded_models(current[MAIN]['MainPID'], parse_env_bytes(candidate_raw))
        expected_pid = current[MAIN]['MainPID']
        expected_restarts = current[MAIN]['NRestarts']
        run(['/usr/bin/bash', str(Path(initial['source']) / 'verify-cara-runtime.sh')], root / 'runtime-verification.log', timeout=180)
        checks = []
        for index in range(3):
            with readonly(cfg.db_path) as conn:
                require(conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'live integrity failed')
                require(conn.execute('PRAGMA foreign_key_check').fetchone() is None, 'live foreign key failure')
                notice = [dict(row) for row in conn.execute('SELECT * FROM deployment_notifications')]
                require(digest(canonical(notice)) == initial['notice_sha256'], 'deployment notice changed or resent')
            sample = services()
            require(sample[MAIN]['ActiveState'] == 'active' and sample[MAIN]['MainPID'] == expected_pid
                    and sample[MAIN]['NRestarts'] == expected_restarts, 'main health/PID/restart changed')
            status = Path('/proc/' + expected_pid + '/status').read_text()
            uid = re.search(r'^Uid:\s+(\d+)', status, re.M)
            require(uid is not None and int(uid.group(1)) != 0, 'main must remain actually non-root')
            for peer in PEERS:
                require(sample[peer] == before[peer], 'peer process changed during final checks: ' + peer)
            checks.append({'at': now(), 'database_integrity': 'ok', 'notice_preserved': True})
            if index < 2:
                time.sleep(5)
        verify_preserved(initial, initial['candidate_env_sha256'])
        started = datetime.now(timezone.utc)
        receipt = {'schema': 'cara.model-trial-acceptance/v1', 'status': 'accepted', 'at': started.isoformat(),
                   'observation_until': (started + timedelta(hours=72)).isoformat(), 'identity': initial['identity'],
                   'before_env_sha256': initial['before_env_sha256'], 'candidate_env_sha256': initial['candidate_env_sha256'],
                   'qualification_sha256': digest(proof.read_bytes()), 'package_sha256': package_hash,
                   'runtime_hashes': initial['runtime_hashes'], 'loaded_model_key_hashes': model_hashes,
                   'stopped_tables_preserved': len(stopped), 'services_before': before, 'services_after': services(),
                   'health_checks': checks, 'budget_limits_usd': initial['budget_limits_usd'],
                   'database_restored': False, 'code_installed': False, 'operator_messages_sent': 0}
        publish(root / 'accepted.json', receipt)
        print(json.dumps({'status': 'accepted', 'at': receipt['at'], 'observation_until': receipt['observation_until']}))
    except BaseException as exc:
        failure = {'at': now(), 'error_type': type(exc).__name__, 'reason': str(exc), 'model_environment_changed': changed}
        best_effort_publish(root / 'activation-failed.json', failure)
        if transition_started:
            try:
                from trial_config import changed_model_keys, revert_env_bytes
                old_raw = (root / 'original.env').read_bytes()
                candidate_raw = (root / 'candidate.env').read_bytes()
                require(digest(old_raw) == initial['before_env_sha256']
                        and digest(candidate_raw) == initial['candidate_env_sha256'],
                        'recorded containment model inputs changed')
                old = parse_env_bytes(old_raw)
                candidate = parse_env_bytes(candidate_raw)
                current_raw = ENV.read_bytes()
                current = parse_env_bytes(current_raw)
                keys = changed_model_keys(old, candidate)
                known = matching_model_environment(current, (old, candidate), keys)
                if known is candidate:
                    recovery_run(['/usr/bin/systemctl', 'stop', MAIN], root / 'contain-stop.log')
                    restored_raw = revert_env_bytes(current_raw, old, candidate)
                    replace_env(restored_raw, digest(current_raw))
                # Unrelated concurrent edits survive, even if model CAS never ran.
                expected = parse_env_bytes(ENV.read_bytes())
                matching_model_environment(expected, (old,), keys)
                state = resume_verified(root, 'contain', expected, initial, before)
                best_effort_publish(root / 'contained-original-models.json', {
                    'at': now(), 'database_restored': False, 'original_model_keys_verified': True,
                    'current_env_sha256': digest(ENV.read_bytes()), 'services': state})
            except BaseException as recovery_error:
                best_effort_publish(root / 'containment-failed.json', {
                    'at': now(), 'error_type': type(recovery_error).__name__,
                    'database_restored': False, 'environment_overwrite_not_forced': True})
                raise recovery_error from exc
        raise


def revert(root):
    from trial_config import parse_env_bytes, revert_model_keys, update_env_bytes, MODEL_KEYS
    initial = json.loads((root / 'prepared.json').read_text())
    require((root / 'accepted.json').exists() and not (root / 'revert.STARTED').exists(), 'accepted unreverted trial required')
    require(digest((root / 'original.env').read_bytes()) == initial['before_env_sha256']
            and digest((root / 'candidate.env').read_bytes()) == initial['candidate_env_sha256'],
            'recorded model restoration inputs changed')
    require(native_identity() == initial['identity'], 'runtime changed since trial; independent review required')
    verify_source(initial)
    current_raw = ENV.read_bytes()
    current = parse_env_bytes(current_raw)
    old = parse_env_bytes((root / 'original.env').read_bytes())
    candidate = parse_env_bytes((root / 'candidate.env').read_bytes())
    merged = revert_model_keys(current, old, candidate)
    raw = update_env_bytes(current_raw, {key: merged.get(key) for key in MODEL_KEYS if current.get(key) != merged.get(key)})
    sys.path.insert(0, initial['source'])
    import common
    cfg = common.load_config(current)
    before = services()
    with readonly(cfg.db_path) as conn:
        idle(conn, process_since(before))
    publish(root / 'revert.STARTED', {'at': now(), 'current_env_sha256': digest(current_raw)})
    try:
        run(['/usr/bin/systemctl', 'stop', MAIN], root / 'revert-stop.log')
        require(services()[MAIN]['MainPID'] == '0', 'main did not quiesce for reversion')
        backup_db(cfg.db_path, root / 'before-revert.sqlite')
        with readonly(cfg.db_path) as conn:
            idle(conn, process_since(before))
            stopped = table_snapshot(conn)
        with readonly(root / 'before-revert.sqlite') as conn:
            require(table_snapshot(conn) == stopped, 'pre-revert backup differs')
        replace_env(raw, digest(current_raw))
        with readonly(cfg.db_path) as conn:
            require(table_snapshot(conn) == stopped, 'reversion changed database')
        run(['/usr/bin/systemctl', 'start', MAIN], root / 'revert-start.log')
        time.sleep(4)
        after = services()
        require(after[MAIN]['ActiveState'] == 'active', 'reverted main unavailable')
        loaded_models(after[MAIN]['MainPID'], merged)
        for peer in PEERS:
            require(before[peer] == after[peer], 'peer changed during reversion')
        require(native_identity() == initial['identity'] and runtime_hashes() == initial['runtime_hashes'], 'runtime changed during reversion')
        run(['/usr/bin/bash', str(Path(initial['source']) / 'verify-cara-runtime.sh')], root / 'revert-runtime-verification.log', timeout=180)
        publish(root / 'reverted.json', {'at': now(), 'status': 'original_models_restored',
                'env_sha256': digest(ENV.read_bytes()), 'database_restored': False, 'history_preserved': True})
    except BaseException as exc:
        best_effort_publish(root / 'reversion-failed.json', {
            'at': now(), 'error_type': type(exc).__name__, 'database_restored': False})
        try:
            from trial_config import changed_model_keys
            actual_raw = ENV.read_bytes()
            actual = parse_env_bytes(actual_raw)
            keys = changed_model_keys(old, candidate)
            matching_model_environment(actual, (current, merged), keys)
            # Reversion never undoes a successful model CAS during recovery:
            # resume whichever recorded configuration is actually installed.
            state = resume_verified(root, 'revert-contain', actual, initial, before)
            best_effort_publish(root / 'contained-reversion-environment.json', {
                'at': now(), 'database_restored': False, 'actual_model_keys_verified': True,
                'current_env_sha256': digest(actual_raw), 'services': state})
        except BaseException as recovery_error:
            best_effort_publish(root / 'reversion-containment-failed.json', {
                'at': now(), 'error_type': type(recovery_error).__name__,
                'database_restored': False, 'environment_overwrite_not_forced': True})
            raise recovery_error from exc
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'activate', 'revert'))
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--protected-manifest', type=Path)
    parser.add_argument('--qualification', type=Path)
    parser.add_argument('--package-sha256')
    args = parser.parse_args()
    require(os.geteuid() == 0 and args.root.is_absolute(), 'root and absolute trial root required')
    if args.command == 'prepare':
        prepare(args.root, args.source, args.protected_manifest)
    elif args.command == 'activate':
        activate(args.root, args.qualification, args.package_sha256)
    else:
        revert(args.root)


if __name__ == '__main__':
    main()
