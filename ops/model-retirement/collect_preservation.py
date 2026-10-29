"""Read current hashes/states for an explicitly pinned accepted protection scope."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import urllib.request

from collect_preflight import canonical, digest, safe_file, unit_state
import peer_release


def require(value, message):
    if not value:
        raise ValueError(message)


def collect(scope):
    require(scope['schema']=='cara.model-retirement-preservation-scope/v1'
            and len(scope['protected_expected'])==126, 'exact accepted126 scope required')
    protected = {}
    for name in sorted(scope['protected_expected']):
        path = PurePosixPath(name)
        require(path.is_absolute() and '..' not in path.parts and str(path).startswith(
            ('/etc/', '/opt/', '/usr/local/', '/root/', '/var/lib/')), 'bounded protected path required')
        protected[name] = safe_file(name)[1]
    known_differences = [name for name, expected in scope['protected_expected'].items()
                         if expected is not None and expected != protected[name]]
    facts_root = Path('/etc/cara-fleet/facts')
    facts = {str(path):safe_file(path)[1] for path in sorted(facts_root.rglob('*')) if path.is_file()}
    outputs = {name:safe_file(name)[1] for name in scope['source_outputs']}
    observer_services={name.removesuffix('.timer')+'.service' for name in scope['observer_units']}
    units = {name:unit_state(name) for name in sorted(set(scope['preserved_units']) |
                                                    set(scope['observer_units']) | observer_services)}
    observer_states={name:{'active':units[name]['ActiveState']!='inactive',
                           'enabled':units[name]['UnitFileState']=='enabled',
                           'unit_sha256':safe_file(Path('/etc/systemd/system')/name)[1]['sha256']}
                     for name in scope['observer_units']}
    closed_files={name:safe_file(name)[1]['sha256'] for name in scope['closed_trial_files']}
    recovery_root=Path('/var/lib/tg-ingest-agent/backups')
    recovery={str(path):safe_file(path,maximum=256*1024*1024)[1]
              for path in sorted(recovery_root.rglob('*')) if path.is_file()}
    neighbors={}
    for name in ('daily-lingo-app-1','daily-lingo-postgres-1'):
        fmt='{{json .Id}}\t{{json .Image}}\t{{json .State.StartedAt}}\t{{json .State.Status}}\t{{json .State.Pid}}\t{{if .State.Health}}{{json .State.Health.Status}}{{else}}null{{end}}'
        process=subprocess.run(['/usr/bin/docker','inspect','--format='+fmt,name],capture_output=True,
            text=True,timeout=15,env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'},cwd='/')
        require(process.returncode==0,'read-only peer identity inspection failed')
        cells=process.stdout.strip().split('\t')
        require(len(cells)==6,'read-only peer identity shape differs')
        neighbors[name]=dict(zip(('id','image','started','status','pid','health'),map(json.loads,cells)))
    with urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=10) as response:
        health=json.load(response)
        require(response.status==200 and health.get('status')=='ok' and health.get('database')=='ok',
                'peer HTTP/database health failed')
        neighbor_health={'http_status':response.status,'database':health['database']}
    peer_linkage = (peer_release.collect(protected, neighbors)
                    if set(known_differences) == set(peer_release.UNIT_PATHS) else None)
    return {'schema':'cara.model-retirement-preservation-observation/v1',
            'at':datetime.now(timezone.utc).isoformat(),'scope_sha256':digest(scope),
            'protected':protected,'protected_sha256':digest(protected),
            'matches_accepted126':digest(protected)==scope['accepted_protected_sha256'],
            'known_protected_differences':known_differences,
            'peer_release_linkage':peer_linkage,
            'facts_files':facts,'facts_manifest_sha256':digest(facts),
            'facts_match_accepted':digest(facts)==scope['facts_manifest_sha256'],
            'source_outputs':outputs,'source_outputs_match_accepted':outputs==scope['source_outputs'],
            'observer_states':observer_states,'observer_states_match_accepted':observer_states==scope['observer_expected'],
            'observer_services_inactive':all(units[name]['ActiveState']=='inactive' for name in observer_services),
            'closed_trial_files':closed_files,'closed_trial_files_match_accepted':closed_files==scope['closed_trial_files'],
            'recovery':recovery,'neighbors':neighbors,'neighbor_health':neighbor_health,
            'units':units,'service_actions':0,'model_calls':0,'database_writes':0,
            'deployment_authority':None}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--read-only', action='store_true', required=True)
    parser.add_argument('--scope',required=True)
    parser.add_argument('--scope-sha256',required=True)
    args=parser.parse_args()
    raw,_=safe_file(args.scope)
    require(hashlib.sha256(raw).hexdigest()==args.scope_sha256,'scope hash differs')
    print(canonical(collect(json.loads(raw))).decode())


if __name__=='__main__':
    main()
