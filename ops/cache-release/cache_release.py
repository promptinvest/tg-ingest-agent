#!/usr/bin/env python3
"""Data-only post-trial release preparation and stopped migration contracts.

This is NOT a production controller. It has no install, service, provider or
message capability. A prepared receipt explicitly leaves guarded native release
acceptance pending. Imports are inert; database access uses SQLite read-only mode.
"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import tarfile


BASELINE_SOURCE = '83e8162611e34e4cf3b387a8b782160f8a71b8b7'
BASELINE_ARCHIVE = '3f7c3b9766c94b0ec429ed593a341e237d4470cdd507b90ff78053bfef05cdb1'
BASELINE_ACCEPTANCE = '256368fa3f2ac26f2a5010ef37db289113912666a902d83a93a939c7bcf4cddf'
BASELINE_CONTINUATION = '54d21267e864c286d270c319d321a13899a4f3d70c5d518eb3d3db66ddd8e2fe'
TRIAL_END = '2026-10-28T13:36:17.924552+00:00'
OBSERVERS = tuple('cara-model-trial-20261025-' + part + 'observer.timer' for part in
                  ('', 'continuation-', 'analysis-', 'layout-', 'functional-', 'ping-'))
CACHE_COLUMN = 'cache_usage_json'
RUNTIME_CHANGES = frozenset({'llm.py', 'store.py', 'call_budget.py', 'spend.py',
                             'review.py', 'install-tg-ingest-agent-pilot-remote.sh'})
RUNTIME_ADDITIONS = frozenset({'cache_usage.py'})
WORKFLOW = '.github/workflows/test.yml'
WORKFLOW_ADDITION = (b'      - name: Check post-trial cache release preservation contracts\n'
                     b'        working-directory: ops/cache-release\n'
                     b"        run: python -B -m unittest discover -p 'test_*.py'\n")
REQUIRED_RELEASE_GATES = (
    'fresh_runtime_environment_peer_and_idle_preflight',
    'fresh_online_and_stopped_protected_backups',
    'stopped_original_schema_and_rows_baseline',
    'staged_exact_installed_entrypoint',
    'reviewed_additive_migration_once_while_stopped',
    'all_original_columns_rows_unknown_charges_preserved',
    'exact_installed_runtime_and_entrypoint_before_worker_start',
    'exact_installed_runtime_and_entrypoint_before_main_start',
    'native_runtime_and_worker_security',
    'native_namespace_readiness_before_mark_verified',
    'private_mcp_fixture_before_mark_verified',
    'all_nine_scoped_production_reads',
    'all_protected_configuration_sources_peers_preserved',
    'native_mark_verified_once',
    'quiescent_final_main_restart',
    'native_namespace_readiness_after_final_restart',
    'private_mcp_fixture_after_final_restart',
    'three_fresh_healthy_samples',
    'one_native_terminal_notice_with_durable_receipt',
    'completed_trial_receipts_and_timer_states_unchanged',
    'independent_final_live_acceptance_audit',
)


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def pin(value):
    require(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value),
            'SHA256 required')
    return value


def utc(value):
    require(isinstance(value, str) and value.endswith(('Z', '+00:00')),
            'explicit UTC timestamp required')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(result.utcoffset().total_seconds() == 0, 'UTC required')
    return result


def decode(raw):
    def unique(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate JSON key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def read_pinned(path, expected, limit=128 * 1024 * 1024):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), 'regular input file required')
    require(0 < path.stat().st_size <= limit, 'input size outside bound')
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == pin(expected), 'input SHA256 differs')
    return raw


def archive_files(raw):
    require(isinstance(raw, bytes) and len(raw) <= 128 * 1024 * 1024, 'bounded archive required')
    files, total = {}, 0
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            require(not path.is_absolute() and '..' not in path.parts and
                    '\\' not in member.name and path.as_posix() == member.name.rstrip('/'),
                    'unsafe archive path')
            if member.isdir():
                continue
            require(member.isfile() and not member.issym() and not member.islnk(),
                    'archive links or special members forbidden')
            require(member.name not in files and member.size <= 16 * 1024 * 1024,
                    'duplicate or oversized archive member')
            total += member.size
            require(total <= 256 * 1024 * 1024, 'expanded archive too large')
            handle = archive.extractfile(member)
            require(handle is not None, 'archive member unavailable')
            files[member.name] = handle.read()
    return files


def verify_source_delta(before, after):
    require(set(before) <= set(after), 'original source file removed')
    changed = {name for name in before if before[name] != after[name]}
    added = set(after) - set(before)
    def nonruntime(name):
        return (name in {'CARA.md', 'SOLUTION.md'} or
                name.startswith(('docs/', 'ops/cache-release/')) or
                ('/' not in name and name.startswith('test_') and name.endswith('.py')))
    for name in changed:
        require(name in RUNTIME_CHANGES or nonruntime(name) or name == WORKFLOW,
                'unapproved runtime/source change: ' + name)
    if WORKFLOW in changed:
        require(after[WORKFLOW].count(WORKFLOW_ADDITION) == 1 and
                after[WORKFLOW].replace(WORKFLOW_ADDITION, b'', 1) == before[WORKFLOW],
                'CI may add the cache helper step once; original checks must remain exact')
    for name in added:
        require(name in RUNTIME_ADDITIONS or nonruntime(name), 'unapproved runtime/source addition: ' + name)
    require({'CARA.md', 'SOLUTION.md'} <= changed, 'both living specifications must change')
    require(RUNTIME_ADDITIONS <= added, 'cache module must be a new runtime addition')
    installer = 'install-tg-ingest-agent-pilot-remote.sh'
    if installer in before:
        def modules(raw):
            found = re.search(rb'^MODULES="([^"\n]+)"', raw, re.M)
            require(found is not None, 'installer runtime list missing')
            names = found.group(1).decode('ascii').split()
            require(len(names) == len(set(names)), 'duplicate installer runtime entry')
            return names
        old_modules, new_modules = modules(before[installer]), modules(after[installer])
        require([name for name in new_modules if name != 'cache_usage.py'] == old_modules and
                new_modules.count('cache_usage.py') == 1,
                'installer must preserve original runtime list and add cache_usage.py once')
    return {'runtime_changed': sorted(changed & RUNTIME_CHANGES),
            'runtime_added': sorted(added & RUNTIME_ADDITIONS),
            'repository_changed': sorted(changed), 'repository_added': sorted(added),
            'unchanged_files': len(before) - len(changed),
            'prompt_model_profile_files_unchanged': True}


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


def cell(value):
    if isinstance(value, bytes):
        return {'blob': base64.b64encode(value).decode('ascii')}
    return value


def rows_digest(rows):
    # Sorting encoded rows preserves duplicate cardinality without depending on
    # insertion order, rowid allocation or mixed SQLite value-type comparisons.
    encoded = sorted(canonical([cell(v) for v in row]) for row in rows)
    result = hashlib.sha256()
    for raw in encoded:
        result.update(str(len(raw)).encode('ascii') + b':' + raw)
    return len(encoded), result.hexdigest()


def database_snapshot(path, original=None):
    """Read one consistent stopped/scratch database, emitting only schema/hashes.

    The caller must independently establish that the production service is stopped
    and bind the protected backup. This function cannot prove service quiescence.
    """
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), 'regular database input required')
    require(not Path(str(path) + '-wal').exists() and not Path(str(path) + '-journal').exists(),
            'standalone stopped backup required; WAL/journal must not be ignored')
    # Immutable mode prevents even shared-memory side effects. It is safe only
    # for the independently authenticated stopped copy, never a live WAL DB.
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True) as conn:
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        require(conn.execute('PRAGMA integrity_check').fetchall() == [('ok',)],
                'database integrity check failed')
        require(conn.execute('PRAGMA foreign_key_check').fetchall() == [],
                'database foreign key check failed')
        schema = conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        tables = {}
        for kind, name, _table, sql in schema:
            if kind != 'table':
                continue
            columns = [list(row) for row in conn.execute('PRAGMA table_xinfo(' + quoted(name) + ')')]
            projection = (original['tables'][name]['projection'] if original and name in original['tables']
                          else [row[1] for row in columns])
            require(set(projection) <= {row[1] for row in columns}, 'original column missing')
            count, row_hash = rows_digest(conn.execute('SELECT ' + ','.join(map(quoted, projection)) +
                                                       ' FROM ' + quoted(name)))
            tables[name] = {'columns': columns, 'projection': projection, 'row_count': count,
                            'rows_sha256': row_hash, 'sql': sql}
        require('llm_usage' in tables, 'llm_usage table required')
        usage = tables['llm_usage']
        unknown_count, unknown_hash = rows_digest(conn.execute(
            'SELECT ' + ','.join(map(quoted, usage['projection'])) +
            " FROM llm_usage WHERE kind LIKE 'unknown_%'"))
        cache_nonnull = (conn.execute('SELECT COUNT(*) FROM llm_usage WHERE cache_usage_json IS NOT NULL').fetchone()[0]
                         if CACHE_COLUMN in {row[1] for row in usage['columns']} else None)
        return {'schema': 'cara.cache-stopped-db-snapshot/v1', 'tables': tables,
                'schema_objects': [list(row) for row in schema],
                'user_version': conn.execute('PRAGMA user_version').fetchone()[0],
                'unknown_rows': unknown_count, 'unknown_sha256': unknown_hash,
                'historical_cache_values': cache_nonnull,
                'integrity_check': 'ok', 'foreign_key_check': []}


def expected_usage_ddl(original_sql):
    # SQLite itself supplies the exact ALTER spelling; no live DB is opened here.
    with sqlite3.connect(':memory:') as conn:
        conn.execute(original_sql)
        conn.execute('ALTER TABLE llm_usage ADD COLUMN cache_usage_json TEXT')
        return conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='llm_usage'").fetchone()[0]


def verify_migration(before, after):
    for value in (before, after):
        require(value['schema'] == 'cara.cache-stopped-db-snapshot/v1', 'snapshot schema differs')
        require(len(value['tables']) == 56, 'all 56 original tables required')
        require(value['integrity_check'] == 'ok' and value['foreign_key_check'] == [], 'integrity failed')
    require(set(before['tables']) == set(after['tables']), 'tables changed')
    require(before['user_version'] == after['user_version'], 'unapproved schema user_version change')
    expected_objects = []
    for kind, name, table, sql in before['schema_objects']:
        if kind == 'table' and name == 'llm_usage':
            sql = expected_usage_ddl(sql)
        expected_objects.append([kind, name, table, sql])
    for name, old in before['tables'].items():
        new = after['tables'][name]
        expected = old['columns']
        require(CACHE_COLUMN not in old['projection'], 'baseline cache column already exists')
        if name == 'llm_usage':
            expected = expected + [[len(expected), CACHE_COLUMN, 'TEXT', 0, None, 0, 0]]
        require(new['columns'] == expected, 'only one named nullable TEXT column may change')
        require(new['projection'] == old['projection'], 'original column projection differs')
        require((new['row_count'], new['rows_sha256']) == (old['row_count'], old['rows_sha256']),
                'original rows changed: ' + name)
    require(after['schema_objects'] == expected_objects, 'unapproved schema object/DDL change')
    require(after['historical_cache_values'] == 0, 'historical cache backfill forbidden')
    require((before['unknown_rows'], before['unknown_sha256']) ==
            (after['unknown_rows'], after['unknown_sha256']), 'unknown usage/reserves changed')
    return {'schema': 'cara.cache-stopped-migration-parity/v1', 'status': 'passed',
            'before_sha256': digest(before), 'after_sha256': digest(after),
            'table_count': 56, 'changed_original_rows': 0, 'changed_original_columns': 0,
            'added_column': 'llm_usage.cache_usage_json TEXT NULL',
            'historical_cache_values': 0, 'unknown_rows': before['unknown_rows'],
            'unknown_sha256': before['unknown_sha256'], 'migration_invocations_expected': 1,
            'production_quiescence_independently_required': True}


def post_trial_contract(prior, timers, at, terminal=None):
    require(prior.get('trial_end') == TRIAL_END, 'original trial deadline differs')
    require(utc(at) > utc(TRIAL_END), 'original trial must have completed')
    require(isinstance(timers, dict) and set(timers) == set(OBSERVERS), 'complete observer timer evidence required')
    for value in timers.values():
        require(isinstance(value, dict) and set(value) == {'active', 'enabled', 'unit_sha256'},
                'closed observer state required')
        require(value['active'] is False and type(value['enabled']) is bool,
                'active observer must be investigated, never silently stopped')
        pin(value['unit_sha256'])
    require(isinstance(terminal, dict), 'terminal observer receipt required')
    require(terminal.get('final') is True and terminal.get('absolute_end') == TRIAL_END,
            'completed terminal observation required')
    pin(terminal.get('receipt_sha256'))
    require(utc(terminal['at']) >= utc(TRIAL_END), 'terminal observation predates trial end')
    return {'schema': 'cara.cache-post-trial-preservation/v1', 'at': at, 'trial_end': TRIAL_END,
            'prior_continuation_sha256': digest(prior), 'terminal': decode(canonical(terminal)),
            'observer_states': decode(canonical(timers)), 'observer_states_sha256': digest(timers),
            'new_trial_epochs': 0, 'observer_actions': 0, 'original_end_reset': False,
            'new_model_quality_claim': False}


def verify_gate_receipts(receipts, source_commit, archive_sha256):
    require(isinstance(source_commit, str) and re.fullmatch(r'[0-9a-f]{40}', source_commit),
            'full source commit required')
    pin(archive_sha256)
    required = {'linux_full', 'ci', 'installed_layout', 'cache_accounting', 'source_review'}
    require(set(receipts) == required, 'complete exact-source offline gate receipts required')
    for name, receipt in receipts.items():
        require(isinstance(receipt, dict) and receipt.get('status') == 'passed', 'gate not passed: ' + name)
        require(receipt.get('source_commit') == source_commit and receipt.get('archive_sha256') == archive_sha256,
                'gate source binding differs: ' + name)
        require(type(receipt.get('model_calls')) is int and receipt['model_calls'] == 0 and
                type(receipt.get('messages_sent')) is int and receipt['messages_sent'] == 0,
                'offline scope differs: ' + name)
    linux = receipts['linux_full']
    require(linux.get('normal_passed') is True and linux.get('shuffled_passed') is True and
            linux.get('network_disabled') is True and linux.get('runs_as_root') is True and
            linux.get('original_tests_preserved') is True, 'complete Linux regression gate required')
    require(receipts['ci'].get('exact_head_passed') is True, 'exact-head CI required')
    require(receipts['installed_layout'].get('installed_filename') == 'agent.py' and
            receipts['installed_layout'].get('source_entrypoint_absent') is True and
            receipts['installed_layout'].get('both_entry_modes_passed') is True,
            'exact installed-entrypoint qualification required')
    require(receipts['cache_accounting'].get('malformed_missing_aliases_and_provider_semantics_passed') is True and
            receipts['cache_accounting'].get('unknown_reserves_and_history_preserved') is True,
            'cache/accounting failure coverage required')
    require(receipts['source_review'].get('prompts_payloads_profiles_unchanged') is True and
            receipts['source_review'].get('budget_limits_and_commercial_overrides_unchanged') is True,
            'independent scope review required')
    return {name: digest(value) for name, value in receipts.items()}


def prepare(args):
    before_raw = read_pinned(args.baseline_archive, BASELINE_ARCHIVE)
    candidate_raw = read_pinned(args.candidate_archive, args.archive_sha256)
    acceptance_raw = read_pinned(args.baseline_acceptance, BASELINE_ACCEPTANCE)
    continuation_raw = read_pinned(args.baseline_continuation, BASELINE_CONTINUATION)
    inputs_raw = read_pinned(args.inputs, args.inputs_sha256, 8 * 1024 * 1024)
    inputs, acceptance, continuation = map(decode, (inputs_raw, acceptance_raw, continuation_raw))
    require(re.fullmatch(r'[0-9a-f]{40}', args.source_commit), 'full source commit required')
    require(acceptance['status'] == 'accepted' and
            acceptance['identity']['source_commit'] == BASELINE_SOURCE,
            'accepted baseline identity differs')
    scope = verify_source_delta(archive_files(before_raw), archive_files(candidate_raw))
    at = datetime.now(timezone.utc).isoformat()
    trial = post_trial_contract(continuation, inputs['observer_states'], at, inputs['terminal_observation'])
    receipts = verify_gate_receipts(inputs['gates'], args.source_commit, args.archive_sha256)
    record = {'schema': 'cara.cache-release-preparation/v1', 'status': 'prepared_not_deployable',
              'at': at, 'source_commit': args.source_commit, 'archive_sha256': args.archive_sha256,
              'baseline_source': BASELINE_SOURCE, 'baseline_acceptance_sha256': BASELINE_ACCEPTANCE,
              'baseline_continuation_sha256': BASELINE_CONTINUATION,
              'inputs_sha256': args.inputs_sha256, 'scope': scope,
              'offline_gate_receipts': receipts, 'post_trial': trial,
              'pending_live_gates': list(REQUIRED_RELEASE_GATES),
              'deployment_authorization': None, 'selected_window': None,
              'qualified_live_backend': None, 'schema_migration_executed': False,
              'production_mutations': 0, 'model_calls': 0, 'messages_sent': 0,
              'completed_controllers_replayed': 0}
    output = Path(args.output)
    require(not output.exists() and not output.is_symlink(), 'fresh preparation output required')
    require(output.parent.is_dir() and not output.parent.is_symlink(), 'existing regular output parent required')
    output.mkdir(mode=0o700)
    # Exclusive creation: partial/failed preparation stays evidence, never replayed.
    for name, raw in (('preparation.json', canonical(record)),
                      ('inputs.json', inputs_raw)):
        with (output / name).open('xb') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    command = parser.add_subparsers(dest='command', required=True)
    prep = command.add_parser('prepare', help='write fresh data-only preparation; cannot launch')
    for name in ('baseline-archive', 'candidate-archive', 'baseline-acceptance',
                 'baseline-continuation', 'inputs', 'output'):
        prep.add_argument('--' + name, required=True, type=Path)
    for name in ('archive-sha256', 'source-commit', 'inputs-sha256'):
        prep.add_argument('--' + name, required=True)
    snapshot = command.add_parser('snapshot', help='read stopped/scratch SQLite; emit schema and hashes only')
    snapshot.add_argument('--db', required=True, type=Path)
    snapshot.add_argument('--original', type=Path)
    parity = command.add_parser('verify-parity', help='verify two stopped snapshot receipts')
    parity.add_argument('--before', required=True, type=Path)
    parity.add_argument('--after', required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == 'prepare':
        result = prepare(args)
        result = {key: result[key] for key in ('status', 'source_commit', 'production_mutations')}
    elif args.command == 'snapshot':
        original = decode(args.original.read_bytes()) if args.original else None
        result = database_snapshot(args.db, original)
    else:
        result = verify_migration(decode(args.before.read_bytes()), decode(args.after.read_bytes()))
    print(canonical(result).decode('ascii'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
