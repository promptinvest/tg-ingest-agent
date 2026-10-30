"""Fresh model-retirement release contracts and single-use orchestration.

The host adapter is a separately hash-bound, independently reviewed package. It
must reuse the existing native installer, layout, runtime, namespace/MCP, scoped
readiness and preservation gates. No historical controller is an entry point.
This module does not select a release or invent permission for unknown usage.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

from env_bytes import parse_env_bytes, update_env_bytes
from transform_config import transform_environment
from collect_preflight import accepted_history_valid


def require(value, message):
    if not value:
        raise ValueError(message)


ALLOWED_RUNTIME_DELTA = frozenset({'common.py', 'llm.py', 'cache_usage.py', 'action_truth.py'})
APPROVED_GUARD_BASE = 'e36c0df260df50be2414524f7094fcf58be2634e50aa4389234e84157b5639c6'
APPROVED_GUARD_TARGET = '9d75bfad4042851b0204e3dec64e1872e103e6251fa0f1f3995607facdc4195f'


def runtime_delta(before, after):
    """Only model/pricing files and the owner's exact approved guard may change."""
    require(set(before) == set(after), 'installed runtime membership changed')
    changed = sorted(key for key in before if before[key] != after[key])
    require(set(changed) == ALLOWED_RUNTIME_DELTA, 'unexpected model-retirement runtime delta')
    require(before['action_truth.py'] == APPROVED_GUARD_BASE
            and after['action_truth.py'] == APPROVED_GUARD_TARGET,
            'only the exact approved saved-reminders guard change is permitted')
    return changed


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def utc(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(parsed.tzinfo is not None, 'timezone-aware evidence required')
    return parsed.astimezone(timezone.utc)


def transform_env_bytes(raw):
    old = parse_env_bytes(raw)
    new, plan = transform_environment(old)
    require(set(old) <= set(new), 'environment keys may not be removed')
    updates = {key: value for key, value in new.items() if old.get(key) != value}
    result = update_env_bytes(raw, updates)
    require(parse_env_bytes(result) == new, 'native environment round-trip differs')
    return result, {**plan, 'before_bytes_sha256': sha(raw),
                    'after_bytes_sha256': sha(result), 'changed_keys': sorted(updates),
                    'raw_file_bytes_bound': True}


def file_metadata(path):
    path = Path(path)
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode)
            and info.st_nlink == 1, 'single-link regular environment required')
    raw = path.read_bytes()
    after = path.lstat()
    identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
    require(identity(info) == identity(after), 'environment changed during read')
    return {'sha256': sha(raw), 'uid': info.st_uid, 'gid': info.st_gid,
            'mode': stat.S_IMODE(info.st_mode)}


def replace_environment(path, raw, expected):
    """Native CAS/fsync primitive; caller must hold the stopped release boundary."""
    path = Path(path)
    require(file_metadata(path) == expected, 'environment identity changed before CAS')
    require(os.name == 'posix' and os.geteuid() == 0, 'authenticated Linux root required')
    require(path == Path('/etc/tg-ingest-agent.env'), 'only Cara environment is writable')
    parent = path.parent.lstat()
    require(stat.S_ISDIR(parent.st_mode) and not stat.S_ISLNK(parent.st_mode)
            and parent.st_uid == 0 and not parent.st_mode & 0o022,
            'protected environment directory required')
    require(expected['uid'] == 0 and expected['mode'] == 0o600,
            'private root-owned environment required')
    # Reject malformed candidate before creating any temporary file.
    parse_env_bytes(raw)
    fd, temporary = tempfile.mkstemp(prefix='.cara-model-retirement-', dir=path.parent)
    try:
        os.fchown(fd, expected['uid'], expected['gid'])
        os.fchmod(fd, expected['mode'])
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        require(file_metadata(path) == expected, 'environment identity changed before atomic replacement')
        os.replace(temporary, path)
        parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        actual = file_metadata(path)
        require(actual == {**expected, 'sha256': sha(raw)}, 'environment metadata readback differs')
        return actual
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def verify_unknowns(baseline, current):
    """Preserve historical charges; block every current or new unresolved call.

    The baseline cutoff is the accepted process observed immediately before this
    release, never an old exception's cutoff. Use this fixed cutoff after restart
    too, so a restart cannot turn a newly unknown charge into accepted history.
    """
    require(baseline['current_process_count'] == current['current_process_count'] == 0,
            'current-process unknown paid inference must settle; no exception applies')
    require(accepted_history_valid(baseline) and accepted_history_valid(current),
            'historical unknown rows must match accepted35 original projection and nullable cache values')
    fields = ('count', 'sha256', 'reserved_usd', 'projection_sha256', 'process_cutoff')
    require(all(current[key] == baseline[key] for key in fields),
            'historical unknown rows, charges or fixed process cutoff changed')
    return {'historical_count': baseline['count'], 'sha256': baseline['sha256'],
            'reserved_usd': baseline['reserved_usd'], 'new_unknowns': 0,
            'exception_applied': False, 'row_updates': 0, 'charge_refunds': 0,
            'process_cutoff': baseline['process_cutoff']}


def validate_observation(value, accepted_source, environment_sha256, *, now=None):
    now = now or datetime.now(timezone.utc)
    age = (now - utc(value['at'])).total_seconds()
    require(0 <= age <= 900, 'fresh read-only preflight required (maximum900 seconds)')
    require(value['identity']['source_revision'] == accepted_source
            and value['identity']['status'] == 'verified', 'accepted native identity differs')
    require(value['environment']['sha256'] == environment_sha256,
            'selected original environment differs')
    require(value['loaded_configuration_matches'] is True, 'loaded model configuration differs')
    database = value['database']
    require(database['quick_check_ok'] is True and database['foreign_key_violations'] == 0
            and database['tables'] == 56, 'current database health/schema gate failed')
    require(all(type(count) is int and count == 0 for count in database['work_counts'].values()),
            'active owner work must settle before release')
    verify_unknowns(database['unknowns'], database['unknowns'])
    return value


REQUIRED_GATES = frozenset({'linux_full', 'ci', 'installed_layout', 'source_review',
                            'operator_qualification', 'model_qualification', 'semantic_review'})


def validate_plan(plan):
    require(plan['schema'] == 'cara.model-retirement-release/v1'
            and plan['status'] == 'qualified_ready_to_deploy', 'qualified fresh release plan required')
    require(set(plan['gates']) == REQUIRED_GATES, 'all unchanged release gates must be bound')
    require(plan.get('approved_unknown_exception') is None, 'historical exception cannot authorize new release')
    require(plan['schema_change'] is False and plan['database_restores'] == 0
            and plan['new_trial_epochs'] == 0, 'schema/trial/recovery expansion forbidden')
    identity = plan['identity']
    for key, length in (('source_commit', 40), ('archive_sha256', 64),
                        ('controller_sha256', 64), ('operator_package_sha256', 64)):
        value = identity[key]
        require(isinstance(value, str) and len(value) == length
                and all(char in '0123456789abcdef' for char in value), 'exact selected identity required')
    duration = (utc(identity['window_end']) - utc(identity['window_start'])).total_seconds()
    require(0 < duration <= 3600, 'original one-hour deployment window limit required')
    for name, gate in plan['gates'].items():
        require(gate['status'] == 'passed' and gate['source_commit'] == identity['source_commit']
                and gate['archive_sha256'] == identity['archive_sha256'],
                'exact-source passed gate required: ' + name)
        require(len(gate['receipt_sha256']) == 64 and all(char in '0123456789abcdef'
                for char in gate['receipt_sha256']), 'raw gate receipt hash required')
        if name in ('model_qualification', 'semantic_review'):
            require(gate['candidate_environment_sha256'] == plan['candidate_environment_sha256'],
                    'qualification must bind exact candidate environment')
    return plan


class Controller:
    """Small orchestration over the separately reviewed concrete host adapter.

    Adapter read_inputs authenticates all raw gates/package/source and independently
    checks model/semantic coverage. Native probes are not replaced with booleans.
    No default adapter exists: preparation alone cannot execute a deployment.
    """
    def __init__(self, plan, backend):
        self.plan = validate_plan(plan)
        self.backend = backend
        self.transition_started = False

    def phase(self, name):
        self.backend.window(self.plan['identity'])
        self.backend.phase(name, self.plan['identity'])

    def run(self):
        b, p = self.backend, self.plan
        b.reject_started()
        b.window(p['identity'])
        evidence = b.read_inputs(p)
        b.exclusive_start(p['identity'])
        try:
            self.phase('fresh_preflight')
            baseline = b.baseline(p)
            observed = b.current_observation()
            validate_observation(observed, p['accepted_source_commit'], p['original_environment_sha256'])
            b.bind_unknown_baseline(observed['database']['unknowns'])
            self.phase('staged_installed_layout')
            b.staged_functional_gate()
            online = b.backup_online(baseline)
            b.assert_idle_before_stop()
            self.phase('stopping_main_and_executor')
            self.transition_started = True
            b.stop_main_executor()
            b.assert_stopped()
            stopped = b.backup_stopped(baseline, online)
            b.verify_stopped_no_schema_change(stopped)
            b.preserve_closed_trial()
            gateways = b.carry_existing_gateways(p, stopped)
            b.check_preinstall_preservation(baseline, gateways)
            self.phase('atomic_model_environment')
            b.replace_model_environment(p)
            self.phase('exact_native_install')
            installed = b.install_exact(p, gateways)
            require(installed['module_sha256s'] == p['module_sha256s'], 'installed runtime map differs')
            b.verify_runtime(p, installed)
            b.verify_loaded_environment(p)
            self.phase('before_mark_verified_readiness')
            expected, before = b.namespace_readiness(p, installed, 'before_mark_verified')
            b.verify_readiness(expected, before)
            b.qualify_selected_mcp_fixture(p, before['service'])
            b.project_reads(p, gateways)
            b.final_preservation(p, baseline, gateways)
            self.phase('mark_verified_once')
            b.mark_verified(p, installed)
            self.phase('quiescent_final_main_restart')
            b.final_restart()
            self.phase('after_final_restart_readiness')
            expected, after = b.namespace_readiness(p, installed, 'after_final_restart')
            require((after['service']['pid'], after['service']['process_start_ticks']) !=
                    (before['service']['pid'], before['service']['process_start_ticks']),
                    'fresh final process required')
            b.verify_readiness(expected, after)
            b.qualify_selected_mcp_fixture(p, after['service'])
            b.verify_loaded_environment(p)
            b.healthy_samples(count=3, interval_seconds=5)
            b.final_preservation(p, baseline, gateways)
            self.phase('normal_notice_once')
            notice = b.notice(p, installed)
            b.verify_notice(notice)
            self.phase('accepted')
            return b.acceptance(p, evidence, baseline, gateways, before, after, notice)
        except BaseException as error:
            contained = False
            if self.transition_started:
                try:
                    b.forward_pause(p)
                    contained = True
                except BaseException:
                    b.failure(p, forward_paused=False)
                    raise RuntimeError('forward containment unverified; source/environment/database retained') from error
            b.failure(p, forward_paused=contained)
            raise


if __name__ == '__main__':
    raise SystemExit('No selected backend: independently bind and qualify a fresh host adapter before launch.')
