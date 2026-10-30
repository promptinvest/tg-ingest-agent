"""Authenticate the one concurrent Daily Lingo release; never accept other drift."""
from __future__ import annotations

import hashlib
import json
import stat
import os
from pathlib import Path

from collect_preflight import digest, safe_file

ROOT = '/root/daily-lingo-quality-trials/backlog-20261029/operator-attempt1-82d7e6a3'
SOURCE = '7e2dc573b734c428d05133525172157d8b3796891e90f39f01177e603a3fbfae'
IMAGE = 'sha256:6810d4b7fa1b8cded9f6fb770335e9198b04768577c57b16880f388242d75191'
VERIFICATION = 'fef9b3df30edf336a2f5880223a13ed8190bf7fa62a10511eac4b94a901ea3ad'
UNIT_PATHS = tuple('/etc/systemd/system/' + name for name in (
    'daily-lingo-observation-budget.service',
    'daily-lingo-observation-report-20260922.service',
    'daily-lingo-observation-sample.service',
    'daily-lingo-v63-supply-observation.service'))
RECEIPTS = ('acceptance.json', 'process-result-acceptance.json',
            'process-result-deployment.json', 'watchdogs-armed.json', 'step5-receipt.json',
            'phase-pid-deployment.json', 'phase-pid-acceptance.json')


ACCEPTANCE_ROOT = ROOT + '/recovery/acceptance-only-20261029-attempt1-c71b45c9'
POLICY_ROOT = '/root/daily-lingo-quality-trials/backlog-20261029/cara-peer-update-attempt2-25f7a1c4'
POLICY_SOURCE_ROOT = '/root/daily-lingo-quality-trials/backlog-20261029/cara-peer-apply-source-attempt2-25f7a1c4'
POLICY_PATH = '/etc/cara-fleet-host.json'
PROTECTED_PATHS = (*UNIT_PATHS, POLICY_PATH)
POLICY_BEFORE = '452ca5f25f948d24aa698fb36e401e08fa7a411a280d084b94045af8de337a5a'
POLICY_AFTER = '1c8195b7e03ea4ddc549bf7676454eda19cafd45b9a8e1e17ae4d0d91506f0f8'
ACTUAL_ACCEPTANCE = '00c9b890532c7a1f936f1d2c5d1bd0b2e2fe8703bfd385523d1f5d62e2332e41'
ACTUAL_PROCESS = '0ba532ceed5271589c927c526fe30439e2866fb77a868b979df2e74bf0f12fd8'
ACTUAL_PID = 'd14ef230379db78f13433872c97465872941f8d5beaaa33b0f7fc5cbdb5a3b62'
ACTUAL_LAUNCH = '1e8d5a6f0a6d83bb1290cc5cfa3c9d9aee66b9fb5ca3b360b5cfa5f574728e56'
PUBLICATION = '8576c10b4d404ade814d1a3812a7431b46d2501da93e8447d1be6d1de27fd961'
POLICY_RECEIPT_PINS = {
    'success.json': 'c5569d07b420de9da502f36d9beb7728d185d1c8ef70f20feb2b3996b1212650',
    'apply.process.json': '11637d62f96a84858752208dcdf461c8ee880bd865a2dc84fd8fea0b8ad5dffa',
    'COMPLETED.json': 'f30e69524b0cc3e51d7cc927a8bb35838df68b4f9c4a7e8817427f7c3a9cfca0',
}
MANIFEST = 'd8344ed0c12c65ee7b764512d38b9fb234ba49efeacf20977b7d861b63893210'
STEP5 = '4e3a20286d97496c453509eea48c86a2e74c5bc7ba8614a5eb15e44710a3f1bf'
POLICY_PREFIX = '/project_adapters/daily-lingo/runtime/'
POLICY_VALUES = {
    POLICY_PREFIX + 'accepted_release/accepted_at': '2026-10-29T20:37:03.296630+00:00',
    POLICY_PREFIX + 'accepted_release/image': IMAGE,
    POLICY_PREFIX + 'accepted_release/manifest_sha256': MANIFEST,
    POLICY_PREFIX + 'accepted_release/revision': SOURCE,
    POLICY_PREFIX + 'accepted_release/step5_receipt_sha256': STEP5,
    POLICY_PREFIX + 'accepted_release/verification_sha256': VERIFICATION,
    POLICY_PREFIX + 'image': IMAGE,
    POLICY_PREFIX + 'manifest_sha256': MANIFEST,
    POLICY_PREFIX + 'step5_receipt_sha256': STEP5,
}


def require(value, message):
    if not value:
        raise ValueError(message)


def validate_receipts(records, pins, protected, neighbors, environment_sha256, source_units):
    """Pure fixed release validation; an active/deployed peer is insufficient."""
    require(set(records) == set(pins) == set(RECEIPTS), 'complete peer terminal receipts required')
    accepted = records['acceptance.json']
    step = records['step5-receipt.json']
    armed = records['watchdogs-armed.json']
    require(accepted.get('all_checks_passed') is True and accepted.get('health') == 'healthy'
            and accepted.get('source') == SOURCE and accepted.get('image') == IMAGE
            and accepted.get('verification_receipt_sha256') == VERIFICATION,
            'actual exact peer acceptance required')
    for name in ('process-result-acceptance.json', 'process-result-deployment.json'):
        require(records[name].get('source') == SOURCE and type(records[name].get('return_code')) is int
                and records[name]['return_code'] == 0, 'peer native process must finish successfully')
    for name in ('phase-pid-deployment.json','phase-pid-acceptance.json'):
        require(records[name].get('source')==SOURCE and type(records[name].get('pid')) is int
                and records[name]['pid']>1, 'native peer launch process binding required')
    require(step.get('source') == SOURCE and step.get('image') == IMAGE
            and step.get('verification_receipt_sha256') == VERIFICATION
            and step.get('all_checks_passed') is True and armed.get('image') == IMAGE
            and armed.get('step5_receipt_sha256') == pins['step5-receipt.json']
            and armed.get('all_timers_unchanged') is True and armed.get('supply_history_preserved') is True,
            'peer monitor/readiness linkage differs')
    app = neighbors['daily-lingo-app-1']; database = neighbors['daily-lingo-postgres-1']
    require(app['image'] == IMAGE and app['status'] == database['status'] == 'running'
            and app['health'] == database['health'] == 'healthy'
            and accepted.get('pg_id') == step.get('pg_id') == database['id']
            and accepted.get('env_sha256') == environment_sha256,
            'accepted peer current process/environment differs')
    require(set(source_units) == set(UNIT_PATHS), 'only the four current peer unit files may differ')
    require(all(protected[path]['sha256'] == source_units[path] for path in UNIT_PATHS),
            'installed peer unit differs from immutable accepted release units')
    return {'schema':'cara.model-retirement-peer-link/v1', 'status':'passed', 'root':ROOT,
            'source':SOURCE, 'image':IMAGE, 'verification_receipt_sha256':VERIFICATION,
            'accepted_at':accepted['at'], 'receipt_sha256s':pins,
            'unit_source_sha256s':source_units, 'environment_sha256':environment_sha256,
            'neighbors':neighbors, 'active_release_controllers':0,
            'absent_process_groups':{name:records['phase-pid-'+name+'.json']['pid']
                                     for name in ('deployment','acceptance')}}


def require_absent_processes(records):
    for phase in ('deployment','acceptance'):
        record=records['phase-pid-'+phase+'.json'];pid=record.get('pid')
        require(record.get('source')==SOURCE and type(pid) is int and pid>1,
                'native peer launch process binding required')
        for check in (os.kill,os.killpg):
            try:
                check(pid,0)
            except ProcessLookupError:
                continue
            raise ValueError('peer release PID/process group remains active')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def validate_continuation(records, pins, publication, publication_sha256, launch, launch_sha256):
    """The canonical copy is not a fictional second acceptance process."""
    require(pins['acceptance.json'] == ACTUAL_ACCEPTANCE
            and pins['process-result-acceptance.json'] == ACTUAL_PROCESS
            and pins['phase-pid-acceptance.json'] == ACTUAL_PID
            and publication_sha256 == PUBLICATION and launch_sha256 == ACTUAL_LAUNCH,
            'fixed actual continuation pins differ')
    require(publication.get('source') == SOURCE and publication.get('image') == IMAGE
            and publication.get('actual_root') == ACCEPTANCE_ROOT
            and publication.get('canonical_path') == ROOT + '/acceptance.json'
            and publication.get('acceptance_sha256') == ACTUAL_ACCEPTANCE
            and publication.get('actual_process_sha256') == ACTUAL_PROCESS
            and publication.get('exact_byte_publication') is True
            and publication.get('controller_replayed') is False,
            'canonical publication must bind the actual continuation')
    process = records['process-result-acceptance.json']
    pid = records['phase-pid-acceptance.json']
    require(process.get('acceptance_sha256') == ACTUAL_ACCEPTANCE
            and process.get('passed') is True and process.get('image') == IMAGE
            and process.get('script_sha256') == launch.get('script_sha256')
                == publication.get('actual_script_sha256')
            and pid.get('script_sha256') == launch.get('launcher_sha256')
            and launch.get('source') == SOURCE and launch.get('image') == IMAGE
            and pid.get('process_group_id') == pid.get('pid'),
            'actual continuation process/script linkage differs')
    return {'root': ACCEPTANCE_ROOT, 'acceptance_sha256': ACTUAL_ACCEPTANCE,
            'process_sha256': ACTUAL_PROCESS, 'pid_sha256': ACTUAL_PID,
            'canonical_publication_sha256': PUBLICATION, 'phase_launch_sha256': ACTUAL_LAUNCH}


def _leaves(value, path=''):
    if isinstance(value, dict):
        return {key: leaf for name, child in value.items()
                for key, leaf in _leaves(child, path + '/' + name).items()}
    if isinstance(value, list):
        return {key: leaf for number, child in enumerate(value)
                for key, leaf in _leaves(child, path + '/' + str(number)).items()}
    return {path: value}


def validate_policy_transition(before_raw, after_raw, before_meta, after_meta, records, pins):
    """Authenticate the consumed updater and independently check all other bytes."""
    require(pins == POLICY_RECEIPT_PINS, 'fixed peer policy receipt pins differ')
    require(_sha(before_raw) == POLICY_BEFORE and _sha(after_raw) == POLICY_AFTER
            and len(before_raw) == len(after_raw) == 2273, 'exact before/after policy bytes required')
    expected_meta = {'uid': 0, 'gid': 0, 'mode': 0o600}
    require({key: before_meta[key] for key in expected_meta} == expected_meta
            and {key: after_meta[key] for key in expected_meta} == expected_meta,
            'peer policy ownership/mode differs')
    success, process, completion = (records[name] for name in
        ('success.json', 'apply.process.json', 'COMPLETED.json'))
    require(success.get('passed') is True and success.get('before_sha256') == POLICY_BEFORE
            and success.get('after_sha256') == POLICY_AFTER
            and success.get('changed_string_leaves') == 9
            and success.get('mode_uid_gid_preserved') is True
            and success.get('other_peer_policy_bytes_preserved') is True
            and success.get('Cara_restarts') == success.get('messages') == 0
            and success.get('replay_permitted') is False, 'consumed peer policy update did not pass')
    require(type(process.get('return_code')) is int and process['return_code'] == 0
            and process.get('timed_out') is False and process.get('failure_type') is None
            and process.get('owned_group_absent') is True
            and process.get('stdout_sha256') == pins['success.json']
            and type(process.get('pid')) is int and process['pid'] > 1
            and process.get('pgid') == process['pid'], 'peer policy native process failed')
    require(completion.get('passed') is True and completion.get('apply_process') == process
            and completion.get('success_sha256') == pins['success.json']
            and completion.get('success_path') == POLICY_ROOT + '/success.json'
            and completion.get('actual_acceptance_sha256') == ACTUAL_ACCEPTANCE
            and completion.get('Cara_restarts') == completion.get('messages') == 0
            and completion.get('replay_permitted') is False, 'peer policy completion linkage differs')
    before, after = _leaves(json.loads(before_raw)), _leaves(json.loads(after_raw))
    changes = {path for path in set(before) | set(after) if before.get(path) != after.get(path)}
    require(changes == set(POLICY_VALUES)
            and all(isinstance(before[path], str) and after[path] == value
                    for path, value in POLICY_VALUES.items()), 'only the nine accepted identity leaves may change')
    proof = success['byte_proof']; tokens = proof['changed_tokens']
    require(proof.get('before_sha256') == POLICY_BEFORE and proof.get('after_sha256') == POLICY_AFTER
            and proof.get('actual_acceptance_sha256') == ACTUAL_ACCEPTANCE
            and len(tokens) == 9 and {t['path'] for t in tokens} == changes,
            'nine-token byte proof required')
    before_cursor = after_cursor = 0
    for token in sorted(tokens, key=lambda item: item['before_start']):
        bs, be, ns, ne = (token[key] for key in
                         ('before_start','before_end','after_start','after_end'))
        require(all(type(n) is int for n in (bs, be, ns, ne))
                and before_cursor <= bs < be <= len(before_raw)
                and after_cursor <= ns < ne <= len(after_raw)
                and before_raw[before_cursor:bs] == after_raw[after_cursor:ns],
                'unselected peer policy bytes changed')
        require(json.loads(before_raw[bs:be]) == before[token['path']]
                and json.loads(after_raw[ns:ne]) == after[token['path']],
                'policy token does not bind its parsed identity leaf')
        before_cursor, after_cursor = be, ne
    require(before_raw[before_cursor:] == after_raw[after_cursor:], 'unselected peer policy suffix changed')
    return {'status': 'passed', 'path': POLICY_PATH,
            'before': {'sha256': POLICY_BEFORE, **expected_meta},
            'after': {'sha256': POLICY_AFTER, **expected_meta},
            'changed_paths': sorted(changes), 'unchanged_bytes_verified': True,
            'receipt_sha256s': pins, 'actual_acceptance_sha256': ACTUAL_ACCEPTANCE,
            'absent_process_group': process['pid']}


def validate_adoption(link, protected, originals, accepted_sha256):
    """Only the accepted four units and exact nine-leaf policy update may differ."""
    require(link.get('schema') == 'cara.model-retirement-peer-link/v2'
            and link.get('status') == 'passed' and link.get('root') == ROOT
            and link.get('source') == SOURCE and link.get('image') == IMAGE
            and link.get('verification_receipt_sha256') == VERIFICATION
            and link.get('adopted_protected_paths') == list(PROTECTED_PATHS)
            and set(originals) == set(PROTECTED_PATHS), 'completed peer acceptance and policy proof required')
    transition = link.get('policy_transition', {})
    require(transition.get('status') == 'passed' and transition.get('path') == POLICY_PATH
            and transition.get('receipt_sha256s') == POLICY_RECEIPT_PINS
            and transition.get('changed_paths') == sorted(POLICY_VALUES)
            and transition.get('unchanged_bytes_verified') is True
            and transition.get('actual_acceptance_sha256') == ACTUAL_ACCEPTANCE
            and transition.get('before') == originals[POLICY_PATH]
            and transition.get('after') == protected[POLICY_PATH]
            and protected[POLICY_PATH]['sha256'] == POLICY_AFTER
            and originals[POLICY_PATH]['sha256'] == POLICY_BEFORE,
            'fixed peer policy transition differs')
    require(link.get('continuation') == {
            'root': ACCEPTANCE_ROOT, 'acceptance_sha256': ACTUAL_ACCEPTANCE,
            'process_sha256': ACTUAL_PROCESS, 'pid_sha256': ACTUAL_PID,
            'canonical_publication_sha256': PUBLICATION, 'phase_launch_sha256': ACTUAL_LAUNCH}, 'actual continuation proof missing')
    require(link['receipt_sha256s']['acceptance.json'] == ACTUAL_ACCEPTANCE
            and link['receipt_sha256s']['process-result-acceptance.json'] == ACTUAL_PROCESS
            and link['receipt_sha256s']['phase-pid-acceptance.json'] == ACTUAL_PID,
            'peer continuation raw receipt pins differ')
    restored = dict(protected)
    for path in PROTECTED_PATHS:
        require(all(protected[path][key] == originals[path][key] for key in ('uid','gid','mode')),
                'peer ownership/mode change is not part of accepted transition')
        require(protected[path]['sha256'] != originals[path]['sha256'],
                'exact five peer files must change')
        restored[path] = originals[path]
    require(len(restored) == 126 and digest(restored) == accepted_sha256,
            'remaining accepted126 protected files changed')
    require(set(link['unit_source_sha256s']) == set(UNIT_PATHS)
            and all(protected[path]['sha256'] == link['unit_source_sha256s'][path] for path in UNIT_PATHS),
            'installed peer units differ from accepted source')


def _require_gone(pid):
    for check in (os.kill, os.killpg):
        try:
            check(pid, 0)
        except ProcessLookupError:
            continue
        raise ValueError('peer updater PID/process group remains active')


def collect(protected, neighbors):
    """Read fixed actual receipts and private byte-proof inputs; never run old controllers."""
    root, actual = Path(ROOT), Path(ACCEPTANCE_ROOT)
    paths = {name: (actual if name in ('process-result-acceptance.json',
                                     'phase-pid-acceptance.json') else root) / name
             for name in RECEIPTS}
    missing = [str(path) for path in paths.values() if not path.exists()]
    if missing:
        return {'schema':'cara.model-retirement-peer-link/v2', 'status':'pending',
                'root':ROOT, 'missing_terminal_receipts':missing}
    records, pins = {}, {}
    for name, path in paths.items():
        raw, metadata = safe_file(path)
        records[name] = json.loads(raw); pins[name] = metadata['sha256']
    actual_raw, _ = safe_file(actual / 'acceptance.json')
    require(_sha(actual_raw) == pins['acceptance.json'] == ACTUAL_ACCEPTANCE,
            'canonical acceptance bytes differ from the actual continuation')
    publication_raw, publication_meta = safe_file(actual / 'canonical-publication.json')
    launch_raw, launch_meta = safe_file(actual / 'phase-launch-acceptance.json')
    continuation = validate_continuation(records, pins, json.loads(publication_raw),
        publication_meta['sha256'], json.loads(launch_raw), launch_meta['sha256'])
    source_units = {path:safe_file(root/'units'/Path(path).name)[1]['sha256'] for path in UNIT_PATHS}
    environment_sha256 = safe_file('/opt/daily-lingo/.env')[1]['sha256']
    require_absent_processes(records)
    policy_records, policy_pins = {}, {}
    for name, expected in POLICY_RECEIPT_PINS.items():
        path = Path(POLICY_ROOT if name == 'success.json' else POLICY_SOURCE_ROOT) / name
        raw, metadata = safe_file(path)
        require(metadata['sha256'] == expected, 'fixed consumed updater receipt changed')
        policy_records[name] = json.loads(raw); policy_pins[name] = metadata['sha256']
    before_raw, _ = safe_file(Path(POLICY_ROOT) / 'policy.before.private')
    original_raw, _ = safe_file(Path(POLICY_ROOT) / 'policy.before.metadata.private.json')
    original = json.loads(original_raw)
    before_meta = {'uid': original['st_uid'], 'gid': original['st_gid'], 'mode': stat.S_IMODE(original['st_mode'])}
    after_raw, after_meta = safe_file(POLICY_PATH)
    require(after_meta == protected[POLICY_PATH], 'policy changed during preservation observation')
    policy = validate_policy_transition(before_raw, after_raw, before_meta, after_meta, policy_records, policy_pins)
    _require_gone(policy_records['apply.process.json']['pid'])
    controller_paths = {str(base / name) for base in (root, actual) for name in
                        ('launch.py', 'deployment.py', 'deployment_supervisor.py',
                         'acceptance_supervisor.py', 'final_checks.py')}
    controller_paths.add(POLICY_SOURCE_ROOT + '/cara_peer_apply_once.py')
    for directory in Path('/proc').iterdir():
        if not directory.name.isdecimal():
            continue
        try:
            arguments = (directory/'cmdline').read_bytes().split(b'\0')
        except (FileNotFoundError, ProcessLookupError):
            continue
        require(not any(argument.decode(errors='replace') in controller_paths for argument in arguments),
                'peer release controller remains active')
    result = validate_receipts(records, pins, protected, neighbors, environment_sha256, source_units)
    result.update(schema='cara.model-retirement-peer-link/v2', continuation=continuation,
                  policy_transition=policy, adopted_protected_paths=list(PROTECTED_PATHS))
    return result
