#!/usr/bin/env python3
"""Offline behavioral qualification of Cara's real installed module layout.

No live config, persistence, Telegram, model or service is used. Source mode
constructs the exact installer payload; installed mode copies actual artifact
bytes and can additionally require byte equality with the selected source.
Each entry-point form runs in a new isolated Python process outside that tree.
"""
import argparse
import hashlib
import importlib
import importlib.abc
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import sqlite3
import subprocess
import sys
import tempfile

SCENARIOS = ('ordinary_text_dispatch', 'empty_reminder_expiry', 'committed_reminder_delivery',
             'known_unsent_delivery_retry', 'ambiguous_delivery_no_resend')


def require(value, message):
    if not value:
        raise RuntimeError(message)


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_files(root):
    root = Path(root).resolve()
    match = re.search(r'^MODULES="([^"\n]+)"', (root / 'install-tg-ingest-agent-pilot-remote.sh').read_text(encoding='utf-8'), re.M)
    require(match is not None, 'installer MODULES list required')
    names = match.group(1).split()
    require(len(names) == len(set(names)) and all(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*\.py', name) for name in names), 'closed installer Python filenames required')
    require('tg_ingest_agent.py' not in names and 'agent.py' not in names, 'entry point must be installed only as agent.py')
    result = {'agent.py': root / 'tg_ingest_agent.py'}
    result.update({name: root / name for name in names})
    require(all(path.is_file() and not path.is_symlink() for path in result.values()), 'complete regular source payload required')
    return result


def installed_files(root):
    root = Path(root).resolve()
    require(root.is_dir() and not (root / 'tg_ingest_agent.py').exists(), 'installed layout must omit source-named entry point')
    result = {path.name: path for path in root.glob('*.py')}
    require('agent.py' in result and all(path.is_file() and not path.is_symlink() for path in result.values()), 'regular installed agent.py payload required')
    return result


def qualify(*, source_root=None, installed_root=None):
    require(source_root is not None or installed_root is not None, 'selected source or installed root required')
    source = source_files(source_root) if source_root is not None else None
    files = installed_files(installed_root) if installed_root is not None else source
    hashes = {name: file_hash(path) for name, path in sorted(files.items())}
    if source is not None:
        require(hashes == {name: file_hash(path) for name, path in sorted(source.items())}, 'installed Python bytes differ from exact selected payload')
    with tempfile.TemporaryDirectory(prefix='cara-installed-layout-') as directory:
        scratch = Path(directory).resolve()
        layout = scratch / 'package'; layout.mkdir()
        for name, path in files.items():
            shutil.copyfile(path, layout / name)
        require(not (layout / 'tg_ingest_agent.py').exists(), 'source alias leaked into installed payload')
        results = []
        for mode in ('agent', '__main__'):
            runtime = scratch / ('runtime-' + mode); runtime.mkdir()
            output = runtime / 'result.json'
            command = [sys.executable, '-I', '-B', str(Path(__file__).resolve()), '--child', mode,
                       '--layout', str(layout), '--runtime', str(runtime), '--result', str(output)]
            child_env = {'PATH': os.defpath, 'PYTHONIOENCODING': 'utf-8',
                         'TMPDIR': str(runtime), 'TMP': str(runtime), 'TEMP': str(runtime)}
            if os.name == 'nt':
                child_env['SYSTEMROOT'] = os.environ.get('SYSTEMROOT', r'C:\Windows')
            process = subprocess.run(command, cwd=str(runtime), env=child_env,
                                     capture_output=True, text=True, timeout=45)
            require(process.returncode == 0 and output.is_file(),
                    'installed-layout ' + mode + ' failed: ' + process.stderr[-4000:])
            result = json.loads(output.read_text(encoding='utf-8'))
            require(result['status'] == 'passed' and result['scenarios'] == list(SCENARIOS)
                    and result['network_attempts'] == result['process_attempts'] == result['actual_model_calls'] == result['actual_telegram_calls'] == 0,
                    'offline entry-point assertions failed')
            results.append(result)
        require(hashes == {name: file_hash(path) for name, path in sorted(files.items())}, 'selected artifact changed during qualification')
        require(not list(layout.rglob('*.pyc')) and not list(layout.rglob('__pycache__')), 'isolated qualification wrote bytecode into artifact')
    return {'schema': 'cara.installed-layout-smoke/v1', 'status': 'passed', 'artifact_sha256s': hashes,
            'source_match': source is not None, 'installed_bytes_checked': installed_root is not None,
            'entrypoints': ['agent', '__main__'], 'scenarios': list(SCENARIOS), 'results': results,
            'source_named_module_absent': True, 'bytecode_artifacts': 0,
            'network_attempts': 0, 'process_attempts': 0, 'actual_model_calls': 0, 'actual_telegram_calls': 0,
            'native_database_access': False, 'service_actions': 0}


def offline_path_policy(event, args, *, runtime, read_roots, script):
    """Closed synthetic filesystem boundary; also directly negative-tested."""
    def path(value):
        require(isinstance(value, (str, bytes, os.PathLike)), 'descriptor-based filesystem access is not a smoke fixture')
        return Path(os.fsdecode(value)).resolve()

    def writable(value):
        require(path(value).is_relative_to(runtime), 'writes must stay in synthetic runtime')

    if event == 'sqlite3.connect':
        require(not str(args[0]).lower().startswith('file:'), 'SQLite URI is not a smoke fixture')
        require(path(args[0]).is_relative_to(runtime), 'only synthetic SQLite is allowed')
    elif event == 'open':
        target = path(args[0])
        require(target == script or any(target.is_relative_to(root) for root in read_roots), 'read outside synthetic/interpreter roots forbidden')
        flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC):
            writable(args[0])
    elif event == 'os.symlink':
        raise RuntimeError('symlink creation is not a smoke fixture')
    elif event in {'os.rename', 'os.link'}:
        writable(args[0]); writable(args[1])
        require(all(value in (-1, None) for value in args[2:]), 'directory descriptors are not a smoke fixture')
    elif event in {'os.mkdir', 'os.remove', 'os.rmdir', 'os.chmod', 'os.chown', 'os.truncate', 'os.utime', 'os.chdir'}:
        writable(args[0])
        # Path-based operations are sufficient for these fixed fixtures.
        if event in {'os.mkdir', 'os.chmod'} and len(args) > 2:
            require(args[2] in (-1, None), 'directory descriptors are not a smoke fixture')
        if event in {'os.remove', 'os.rmdir'} and len(args) > 1:
            require(args[1] in (-1, None), 'directory descriptors are not a smoke fixture')
        if event == 'os.chown' and len(args) > 3:
            require(args[3] in (-1, None), 'directory descriptors are not a smoke fixture')
        if event == 'os.utime' and len(args) > 3:
            require(args[3] in (-1, None), 'directory descriptors are not a smoke fixture')


def child(mode, layout, runtime, result_path):
    # Import stdlib before installing audit denials (SSL subclasses socket).
    import ssl  # noqa: F401
    import socket  # noqa: F401
    import sysconfig
    layout, runtime = Path(layout).resolve(), Path(runtime).resolve()
    result_path = Path(result_path).resolve()
    require(sys.flags.isolated and sys.dont_write_bytecode, 'fresh -I -B interpreter required')
    require(Path.cwd().resolve() == runtime and layout.parent == runtime.parent
            and result_path.parent == runtime, 'private out-of-source runtime required')
    require(not (layout / 'tg_ingest_agent.py').exists() and 'tg_ingest_agent' not in sys.modules, 'source entry-point alias must be absent')
    sys.path.insert(0, str(layout))

    class NoSourceEntry(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname == 'tg_ingest_agent' or fullname.startswith('tg_ingest_agent.'):
                raise ModuleNotFoundError('tg_ingest_agent is not an installed module')
            return None

    sys.meta_path.insert(0, NoSourceEntry())
    attempts = {'network': 0, 'process': 0}
    read_roots = [layout, runtime] + [Path(sysconfig.get_path(key)).resolve() for key in ('stdlib', 'platstdlib', 'purelib', 'platlib')]
    if os.name == 'posix':
        read_roots.append(Path('/usr/share/zoneinfo'))
    script = Path(__file__).resolve()

    def audit(event, args):
        if event.startswith('socket.'):
            attempts['network'] += 1
            raise RuntimeError('network forbidden in installed-layout smoke')
        if event in {'subprocess.Popen', 'os.system', 'os.exec', 'os.posix_spawn', 'os.fork'}:
            attempts['process'] += 1
            raise RuntimeError('child processes forbidden in installed-layout smoke')
        offline_path_policy(event, args, runtime=runtime, read_roots=read_roots, script=script)

    sys.addaudithook(audit)
    import common
    import store
    import llm
    import tg_api
    import action_delivery
    cfg = common.load_config({'TELEGRAM_BOT_TOKEN': '123:offline', 'ALLOWED_CHAT_IDS': '1',
                              'DO_MODEL_ACCESS_KEY': 'offline-model', 'BOT_LANGUAGE': 'en',
                              'DB_PATH': str(runtime / 'synthetic.db'), 'MEDIA_DIR': str(runtime / 'media'),
                              'TASK_ARTIFACTS_DIR': str(runtime / 'tg-ingest-agent/task-artifacts'), 'TASK_WORKER_SPOOL': str(runtime / 'cara-worker/spool'),
                              'MENTOR_REVIEW_SPOOL': str(runtime / 'cara-mentor/spool'), 'MENTOR_RUNNER_SPOOL': str(runtime / 'cara-mentor-runner/spool'),
                              'PROACTIVE_ENABLED': 'false', 'REMINDER_FIRED_EXPIRE_DAYS': '1'})
    cfg.fleet_facts = str(runtime / 'facts.json')
    common.load_config = lambda *args, **kwargs: cfg
    transport = {'agent': None, 'mode': 'success', 'calls': [], 'model_calls': 0}

    def fake_model(*args, **kwargs):
        require(len(args) >= 4 and args[2] == 'router', 'only the synthetic router request is expected')
        transport['model_calls'] += 1
        return '{"action":"help","params":{},"confidence":0.99}'

    def fake_send(token, method, params=None, **kwargs):
        agent = transport['agent']
        require(agent is not None and not agent.conn.in_transaction, 'transport must follow local commit')
        transport['calls'].append((method, dict(params or {})))
        if method == 'sendMessage' and transport['mode'] == 'rejected':
            raise tg_api.TelegramError('synthetic rejection', status=429)
        if method == 'sendMessage' and transport['mode'] == 'ambiguous':
            raise tg_api.TelegramError('synthetic timeout', outcome_unknown=True)
        return {'message_id': 100 + len(transport['calls']), 'text': (params or {}).get('text', '')}

    llm.chat_profile = fake_model
    tg_api.tg_call = fake_send
    completed = []

    def checks(agent, namespace):
        transport['agent'] = agent
        namespace['tg_call'] = fake_send
        try:
            # Empty expiry traverses a decorated no-op, the production crash.
            agent.check_reminder_expiry()
            require(not transport['calls'] and agent.conn.execute('SELECT COUNT(*) FROM reply_outbox').fetchone()[0] == 0,
                    'empty expiry must have no delivery effect')
            text = 'Show the available note and reminder commands.'
            update = {'update_id': 7001, 'message': {'message_id': 71, 'chat': {'id': 1, 'type': 'private'},
                                                      'from': {'id': 1}, 'text': text}}
            agent.process_update_batch([update])
            require(store.telegram_update_get(agent.conn, 7001)['status'] == 'done' and transport['model_calls'] == 1,
                    'ordinary text must traverse real dispatch/router successfully')
            require(any(method == 'sendMessage' and params.get('text') for method, params in transport['calls']),
                    'ordinary reply must be delivered through current entry-point transport')
            agent.check_reminder_expiry()
            completed.extend(SCENARIOS[:2])
            draft = {'title': 'offline verification reminder', 'due_utc': '2099-01-01T09:00:00+00:00',
                     'recurrence': 'none', 'card_message_id': 77}
            store.pending_set(agent.conn, 1, 'reminder', draft)
            key = action_delivery.scope_for_update(7002, 'confirm fixture')
            agent._active_update_id = 7002; agent._reminder_scope = key
            agent.resolve_pending(1, 'confirm', {}, store.pending_get(agent.conn, 1), 'en')
            require(len(store.reminders_active(agent.conn, 1)) == 1 and action_delivery.receipt(agent.conn, key) is not None,
                    'confirmed reminder effect and receipt must be committed')
            row = agent.conn.execute('SELECT * FROM reply_outbox WHERE action_key=?', (key,)).fetchone()
            require(row is not None and row['state'] == 'sent' and row['attempts'] == 1, 'committed reminder response must be sent exactly once')
            count = len(transport['calls']); action_delivery.deliver_action(agent, key)
            require(len(transport['calls']) == count and len(store.reminders_active(agent.conn, 1)) == 1, 'receipt replay must not repeat effect or send')
            completed.append(SCENARIOS[2])
            agent._active_update_id = None; agent._reminder_scope = None
            transport['mode'] = 'rejected'; agent.reply(1, 'synthetic known-unsent reply')
            row = agent.conn.execute('SELECT * FROM reply_outbox ORDER BY id DESC LIMIT 1').fetchone()
            require(row['state'] == 'pending' and row['attempts'] == 1, 'definite rejection must remain retryable')
            agent.conn.execute("UPDATE reply_outbox SET available_at='2000-01-01' WHERE id=?", (row['id'],)); agent.conn.commit()
            transport['mode'] = 'success'; agent.check_reply_deliveries()
            retried = agent.conn.execute('SELECT * FROM reply_outbox WHERE id=?', (row['id'],)).fetchone()
            require(retried['state'] == 'sent' and retried['attempts'] == 2, 'known-unsent response must retry delivery only')
            count = len(transport['calls']); agent.check_reply_deliveries()
            require(len(transport['calls']) == count, 'successful retry must not repeat')
            completed.append(SCENARIOS[3])
            transport['mode'] = 'ambiguous'; agent.reply(1, 'synthetic ambiguous reply')
            row = agent.conn.execute('SELECT * FROM reply_outbox ORDER BY id DESC LIMIT 1').fetchone()
            require(row['state'] == 'ambiguous' and row['attempts'] == 1, 'unknown outcome must become terminal ambiguous')
            count = len(transport['calls']); agent.check_reply_deliveries()
            require(len(transport['calls']) == count, 'ambiguous response must never automatically resend')
            completed.append(SCENARIOS[4])
        finally:
            agent.conn.close()
        # Recovery also traverses the actual installed Agent constructor.
        again = namespace['Agent'](cfg); transport['agent'] = again
        try:
            again.check_reply_deliveries()
            require(len(transport['calls']) == count and tuple(again.conn.execute('SELECT state,attempts FROM reply_outbox WHERE id=?', (row['id'],)).fetchone()) == ('ambiguous', 1), 'restart must retain ambiguous no-resend')
        finally:
            again.conn.close()

    if mode == 'agent':
        entry = importlib.import_module('agent')
        require(Path(entry.__file__).resolve() == layout / 'agent.py', 'actual installed entry point required')
        checks(entry.Agent(cfg), entry.__dict__)
    else:
        invoked = []
        def trace_call(frame, event, arg):
            if event == 'call' and frame.f_code.co_name == 'main' and Path(frame.f_code.co_filename).resolve() == layout / 'agent.py':
                namespace = frame.f_globals
                require(namespace['__name__'] == '__main__', 'script-style entry identity required')
                namespace['Agent'].run = lambda self: (invoked.append(True), checks(self, namespace))
                sys.settrace(None)
            return None
        sys.settrace(trace_call)
        previous = sys.argv; sys.argv = [str(layout / 'agent.py')]
        try:
            try:
                runpy.run_path(str(layout / 'agent.py'), run_name='__main__')
            except SystemExit as exc:
                require(exc.code in (None, 0), 'script main returned failure')
        finally:
            sys.settrace(None); sys.argv = previous
        require(invoked == [True], 'real script main must construct Agent and invoke run once')
    require(completed == list(SCENARIOS) and not any(attempts.values()) and 'tg_ingest_agent' not in sys.modules,
            'all offline layout scenarios and boundaries must pass')
    result_path.write_text(json.dumps({'status': 'passed', 'entrypoint': mode, 'scenarios': completed,
                                     'isolated': True, 'bytecode_disabled': True, 'mock_model_calls': transport['model_calls'],
                                     'mock_telegram_calls': len(transport['calls']), 'network_attempts': attempts['network'],
                                     'process_attempts': attempts['process'], 'actual_model_calls': 0, 'actual_telegram_calls': 0}, sort_keys=True), encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path)
    parser.add_argument('--installed-root', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--child', choices=('agent', '__main__'), help=argparse.SUPPRESS)
    parser.add_argument('--layout', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--runtime', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--result', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.child:
        child(args.child, args.layout, args.runtime, args.result)
        return 0
    report = qualify(source_root=args.source_root, installed_root=args.installed_root)
    raw = json.dumps(report, sort_keys=True, separators=(',', ':')).encode()
    if args.output:
        with args.output.open('xb') as stream:
            stream.write(raw)
    print(raw.decode())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())