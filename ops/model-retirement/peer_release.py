"""Authenticate the one concurrent Daily Lingo release; never accept other drift."""
from __future__ import annotations

import json
import os
from pathlib import Path

from collect_preflight import safe_file

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


def collect(protected, neighbors):
    """Read only fixed receipt/unit/hash paths and controller identities."""
    root = Path(ROOT)
    missing = [name for name in RECEIPTS if not (root/name).exists()]
    if missing:
        return {'schema':'cara.model-retirement-peer-link/v1', 'status':'pending',
                'root':ROOT, 'missing_terminal_receipts':missing}
    records = {}; pins = {}
    for name in RECEIPTS:
        raw, metadata = safe_file(root/name)
        records[name] = json.loads(raw); pins[name] = metadata['sha256']
    source_units = {path:safe_file(root/'units'/Path(path).name)[1]['sha256'] for path in UNIT_PATHS}
    environment_sha256 = safe_file('/opt/daily-lingo/.env')[1]['sha256']
    require_absent_processes(records)
    for directory in Path('/proc').iterdir():
        if not directory.name.isdecimal():
            continue
        try:
            arguments = (directory/'cmdline').read_bytes().split(b'\0')
        except (FileNotFoundError, ProcessLookupError):
            continue
        require(not any(argument.decode(errors='replace') in
                {ROOT+'/'+name for name in ('launch.py', 'deployment.py', 'deployment_supervisor.py',
                                           'acceptance_supervisor.py', 'final_checks.py')}
                for argument in arguments), 'peer release controller remains active')
    return validate_receipts(records, pins, protected, neighbors, environment_sha256, source_units)
