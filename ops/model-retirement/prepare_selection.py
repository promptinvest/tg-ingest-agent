"""Data-only preparation for a fresh Cara model retirement release.

Uses accepted evidence as dated ancestry, never as permission or fresh health.
Draft output deliberately cannot satisfy release_operator.validate_plan.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import tarfile

from collect_preflight import canonical, digest
import peer_release
from release_operator import (REQUIRED_GATES, require, runtime_delta, utc,
                              validate_observation, validate_plan, verify_unknowns)

ACCEPTED_PINS = {
    'authorization.json': 'b293755b65537f0d99a0093d136283e6a0e961e51d5b12b8d2a384dad905845e',
    'acceptance.json': '33c233dcbb1601a925890ec47ecb5464d8d35d3cca76657f83930477b1f95a79',
    'independent-final-audit.json': '2241154d04d11e302c192aa9ed60db742daf4c215241faec56751e82a109a6a9',
    'cache-stopped-before.json':'53e37d7a211867869f080254593ca70ed20001aaca975c3fc93398345eb3c040',
    'cache-migration.json':'4f3ee642811c8b1b1998578dd630b272f19455157414994806059df83cd560b5',
}
ACCEPTED_SOURCE = 'e834c2927b411302acd1d015e9e621f89e37eb19'
ACCEPTED_ARCHIVE = 'c064f6b3ddef0787713369cdec5cfb5d816fe3733aab3bb2addd894251572863'
SCOPE_KEYS = ('facts', 'facts_cells_sha256', 'facts_manifest_sha256', 'facts_obligations',
              'facts_pages_sha256', 'lifecycle', 'lifecycle_file', 'mcp', 'preserved_units',
              'publishers', 'required_layout_cases', 'source_outputs',
              'spool_expectations', 'spool_operations', 'configured_task_cost_limit_usd')
CORE_UNIT_PROPERTIES = ('LoadState','ActiveState','MainPID','NRestarts','InvocationID',
                        'ExecMainStartTimestampMonotonic')
REFERENCE_PINS = {
    'accepted_backend.py':'7bbb9d5b532aa6074305244964fd6ae7541a12df40e20405549d251581fc3961',
    'cara_next_release_checks_draft.py':'42ad9c37814e119270020afd43e1fee39570c06c92bdcb7bb23a838530c167b5',
}


def load_accepted(directory):
    result = {}
    for name, expected in ACCEPTED_PINS.items():
        path = Path(directory) / name
        require(path.is_file() and not path.is_symlink(), 'accepted regular evidence required')
        raw = path.read_bytes()
        require(hashlib.sha256(raw).hexdigest() == expected, 'accepted receipt changed: ' + name)
        result[name] = json.loads(raw)
    accepted = result['acceptance.json']
    audit = result['independent-final-audit.json']
    require(accepted['status'] == 'accepted' and audit['status'] == 'passed'
            and accepted['identity']['source_commit'] == audit['source_commit'] == ACCEPTED_SOURCE
            and accepted['identity']['archive_sha256'] == audit['archive_sha256'] == ACCEPTED_ARCHIVE,
            'exact accepted source ancestry required')
    require(audit['acceptance_sha256'] == ACCEPTED_PINS['acceptance.json']
            and audit['authorization_sha256'] == ACCEPTED_PINS['authorization.json'],
            'accepted/audit evidence linkage differs')
    migration=result['cache-migration.json'];stopped=result['cache-stopped-before.json']
    require(audit['migration_sha256']==accepted['migration_sha256']==ACCEPTED_PINS['cache-migration.json']
            and migration['before_sha256']==digest(stopped)
            and migration['parity']['unknown_rows']==stopped['unknown_rows']==35
            and migration['parity']['unknown_sha256']==stopped['unknown_sha256'],
            'accepted historical35 projection linkage differs')
    return result


def preservation_scope(records):
    plan = records['authorization.json']
    accepted = records['acceptance.json']
    audit = records['independent-final-audit.json']
    original = deepcopy(plan['baseline_protected'])
    original.update(plan['deployment_files'])
    nonce = plan['identity']['controller_nonce']
    for phase in (1, 2):
        for suffix in ('', '-other'):
            path = f'/etc/cara-fleet/qualification/{nonce}-{phase}{suffix}.json'
            require(path not in original, 'fresh accepted fixture path overlaps baseline')
            # Accepted audit authenticates their aggregate map; individual hashes
            # are collected afresh without exporting private configuration bytes.
            original[path] = None
    require(len(original) == 126, 'all accepted126 protected paths required')
    require(accepted['protected_sha256'] == audit['protected_sha256'], 'accepted protection hashes differ')
    observers = plan['closed_trial']['observer_states']
    return {'schema':'cara.model-retirement-preservation-scope/v1',
            'accepted_pins':deepcopy(ACCEPTED_PINS), 'protected_expected':original,
            'accepted_protected_sha256':audit['protected_sha256'],
            'preserved_units':deepcopy(plan['preserved_units']),
            'observer_units':sorted(observers), 'observer_expected':deepcopy(observers),
            'source_outputs':deepcopy(plan['source_outputs']),
            'facts_manifest_sha256':plan['facts_manifest_sha256'],
            'closed_trial_files':{
                '/root/cara-ping-flow-20261027/release-attempt1/trial-observations/'+name:pin
                for name,pin in plan['closed_trial_files'].items()},
            'closed_trial_end':plan['closed_trial']['trial_end'],
            'deployment_authority':None}


def build_draft(records, observation, observation_sha256):
    plan = records['authorization.json']
    accepted = records['acceptance.json']
    audit = records['independent-final-audit.json']
    actual_runtime = {name:entry['sha256'] for name,entry in observation['runtime'].items()}
    require(actual_runtime == plan['runtime_hashes'] and len(actual_runtime) == 95,
            'observed95 runtime artifacts differ from accepted source')
    require(observation['identity']['source_revision'] == ACCEPTED_SOURCE
            and observation['identity']['status'] == 'verified'
            and observation['identity']['sha256'] == audit['native_sha256'],
            'observed current native marker differs from accepted audit')
    require(observation['database']['tables'] == 56, 'current56-table schema required')
    require(observation['database']['unknowns']['current_process_count'] == 0,
            'current unknown requires review before package preparation')
    verify_unknowns(observation['database']['unknowns'],observation['database']['unknowns'])
    expected_env = plan['baseline_protected']['/etc/tg-ingest-agent.env']
    require(observation['environment'] == expected_env and observation['loaded_configuration_matches'] is True,
            'original environment or loaded model configuration differs')
    require(len(plan['spool_operations']) == len(plan['spool_expectations']) == 9,
            'all original9 scoped reads required')
    scope = preservation_scope(records)
    return {'schema':'cara.model-retirement-draft-selection/v1',
            'status':'prepared_not_deployable','prepared_at':datetime.now(timezone.utc).isoformat(),
            'accepted_source_commit':ACCEPTED_SOURCE,'accepted_archive_sha256':ACCEPTED_ARCHIVE,
            'accepted_receipt_sha256s':deepcopy(ACCEPTED_PINS),
            'baseline_manifest':deepcopy(accepted['manifest']),
            'baseline_native_sha256':audit['native_sha256'],
            'baseline_runtime_hashes':actual_runtime,
            'baseline_module_sha256s':{name:value for name,value in actual_runtime.items() if name.endswith('.py')},
            'original_environment_sha256':observation['environment']['sha256'],
            'original_environment_metadata':deepcopy(observation['environment']),
            'historical_unknown_baseline':deepcopy(observation['database']['unknowns']),
            'observation_sha256':observation_sha256,'observation_at':observation['at'],
            'preservation_scope_sha256':digest(scope),
            'accepted_protected_sha256':scope['accepted_protected_sha256'],
            'peer_original_units':{path:deepcopy(scope['protected_expected'][path])
                                   for path in peer_release.PROTECTED_PATHS},
            'historical_closed_trial':deepcopy(plan['closed_trial']),
            'historical_closed_trial_files':deepcopy(plan['closed_trial_files']),
            **{key:deepcopy(plan[key]) for key in SCOPE_KEYS},
            'pending':['fresh126_file_preservation_and_peer_state','fresh_closed_trial_states',
                       'exact_candidate_source_archive','exact_candidate_environment',
                       'full_linux_verification','exact_head_ci','bounded_model_qualification',
                       'independent_semantic_review','operator_qualification_and_review',
                       'fresh_preflight','selected_active_window'],
            'gates':{},'identity':None,'candidate_environment_sha256':None,
            'approved_unknown_exception':None,'deployment_authority':None,
            'schema_change':False,'database_restores':0,'new_trial_epochs':0}


def bind_preservation(draft, observed):
    """Bind a fresh exact preservation observation; any drift needs review first."""
    require(observed['schema']=='cara.model-retirement-preservation-observation/v1'
            and observed['scope_sha256']==draft['preservation_scope_sha256'],
            'preservation observation scope differs')
    required=('facts_match_accepted','source_outputs_match_accepted',
              'observer_states_match_accepted','observer_services_inactive','closed_trial_files_match_accepted')
    require(all(observed[name] is True for name in required),
            'fresh protection differs from accepted evidence; explicit reconciliation required')
    require(len(observed['protected'])==126, 'all126 accepted protected files required')
    peer = observed.get('peer_release_linkage')
    if observed['matches_accepted126'] is not True:
        require(set(observed['known_protected_differences'])==set(peer_release.PROTECTED_PATHS)
                and isinstance(peer,dict) and peer.get('status')=='passed'
                and peer.get('root')==peer_release.ROOT and peer.get('source')==peer_release.SOURCE
                and peer.get('image')==peer_release.IMAGE
                and peer.get('verification_receipt_sha256')==peer_release.VERIFICATION
                and set(peer.get('receipt_sha256s',{}))==set(peer_release.RECEIPTS)
                and peer.get('active_release_controllers')==0
                and peer.get('neighbors')==observed['neighbors'],
                'exact completed peer acceptance linkage required')
        peer_release.validate_adoption(peer,observed['protected'],draft['peer_original_units'],
                                       draft['accepted_protected_sha256'])
    else:
        require(observed['known_protected_differences']==[], 'unexpected known protected differences')
    return {'baseline_protected':deepcopy(observed['protected']),
            'peer_release_linkage':deepcopy(peer),
            'baseline_neighbors':deepcopy(observed['neighbors']),
            'baseline_preserved_units':{name:{key:observed['units'][name][key] for key in CORE_UNIT_PROPERTIES
                                             if key in observed['units'][name]}
                                        for name in draft['preserved_units']},
            'baseline_recovery':deepcopy(observed['recovery']),
            'closed_trial_files':deepcopy(observed['closed_trial_files']),
            'closed_trial_units':{name:deepcopy(state) for name,state in observed['units'].items()
                                  if name.startswith('cara-model-trial-')},
            'facts_manifest_sha256':observed['facts_manifest_sha256'],
            'source_outputs':deepcopy(observed['source_outputs']),
            'preservation_observed_at':observed['at']}


def pinned(path, expected):
    path=Path(path)
    require(path.is_file() and not path.is_symlink(), 'regular pinned input required')
    raw=path.read_bytes()
    require(hashlib.sha256(raw).hexdigest()==expected, 'selected input hash differs')
    return raw


def archive_files(archive_path, expected_archive):
    pinned(archive_path, expected_archive)
    files={};total=0
    with tarfile.open(archive_path,'r:*') as archive:
        for member in archive:
            name=member.name
            require(not name.startswith('/') and '\\' not in name and
                    all(part not in ('','.','..') for part in name.rstrip('/').split('/')),
                    'safe normalized source archive path required')
            if member.isdir():continue
            require(member.isfile() and name not in files and member.size<=8*1024*1024,
                    'regular bounded unique source member required')
            total+=member.size
            require(total<=128*1024*1024, 'source archive expanded limit exceeded')
            files[name]=archive.extractfile(member).read()
    return files


def candidate_runtime(archive_path, expected_archive):
    files=archive_files(archive_path, expected_archive)
    installer=files['install-tg-ingest-agent-pilot-remote.sh']
    match=re.search(r'^MODULES="([^"]+)"$',installer.decode(),re.MULTILINE)
    require(match is not None, 'canonical installer module list required')
    names=match.group(1).split()
    require(len(names)==len(set(names)) and all(re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]*\.py',name)
            for name in names), 'closed installer runtime names required')
    modules={name:hashlib.sha256(files[name]).hexdigest() for name in names}
    modules['agent.py']=hashlib.sha256(files['tg_ingest_agent.py']).hexdigest()
    runtime={**modules,'verify-cara-runtime.sh':hashlib.sha256(files['verify-cara-runtime.sh']).hexdigest()}
    return modules,runtime,hashlib.sha256(installer).hexdigest()


def operator_revision_binding(envelopes, inputs, package_hashes):
    """Bind a selected separate operator in either qualification mode."""
    mode=envelopes['model_qualification'].get('qualification_mode')
    require(mode in (None,'full_native_existing_correction','full_native_retirement'), 'unrecognized qualification mode')
    if mode is None and 'operator_source_commit' not in inputs:
        return {}
    label='fullflow' if mode else 'first-pass'
    revision=inputs.get('operator_source_commit')
    require(isinstance(revision,str) and re.fullmatch(r'[0-9a-f]{40}',revision)
            and revision!=inputs['source_commit'], 'distinct '+label+' operator source commit required')
    require(envelopes['semantic_review'].get('qualification_mode')==mode,
            label+' model and semantic evidence modes differ')
    for name in ('source_review','operator_qualification'):
        require(envelopes[name].get('operator_source_commit')==revision
                and envelopes[name].get('operator_package_sha256')==digest(package_hashes),
                'separately reviewed exact '+label+' operator package required: '+name)
    result={'operator_source_commit':revision}
    if mode:
        result['qualification_mode']=mode
    return result


def selected_plan(draft, observation, preservation, inputs, package_directory):
    """Pure/data-only final binding. Does not copy, install, launch or call models.

    All seven gate envelopes must already exist and link nonempty exact raw
    evidence maps. The backend independently validates these same bytes again.
    """
    require(draft['status']=='prepared_not_deployable', 'original inert draft required')
    require(isinstance(inputs.get('test_summary'),str) and 0<len(inputs['test_summary'])<=2000,
            'bounded exact native verification summary required')
    validate_observation(observation,draft['accepted_source_commit'],draft['original_environment_sha256'])
    require(0<=(datetime.now(timezone.utc)-utc(preservation['at'])).total_seconds()<=900,
            'fresh preservation observation required')
    verify_unknowns(draft['historical_unknown_baseline'],observation['database']['unknowns'])
    bound=bind_preservation(draft,preservation)
    package=Path(package_directory)
    modules,runtime,installer=candidate_runtime(inputs['source_archive'],inputs['archive_sha256'])
    original_source=archive_files(inputs['accepted_source_archive'],ACCEPTED_ARCHIVE)
    candidate_source=archive_files(inputs['source_archive'],inputs['archive_sha256'])
    native_names={name for name in original_source if name.endswith('.service')}
    native_names.update(('install-tg-ingest-agent-pilot-remote.sh','verify-cara-runtime.sh'))
    preserved_source={name:hashlib.sha256(original_source[name]).hexdigest() for name in sorted(native_names)}
    require(all(candidate_source.get(name)==original_source[name] for name in native_names),
            'accepted native installer/unit source changed')
    require(set(runtime)==set(draft['baseline_runtime_hashes']), 'runtime set must remain exact95 artifacts')
    runtime_delta(draft['baseline_runtime_hashes'],runtime)
    require(set(inputs['gates'])==REQUIRED_GATES, 'all7 selected gate envelopes required')
    package_hashes=deepcopy(inputs['package_sha256s'])
    require({name:package_hashes.get('reference/'+name) for name in REFERENCE_PINS}==REFERENCE_PINS,
            'exact two accepted primitive references required')
    for name,pin in package_hashes.items():
        require(not name.startswith('/') and '\\' not in name and all(part not in ('','.','..')
                for part in name.split('/')), 'safe package relative path required')
        pinned(package/name,pin)
    gates={};envelopes={}
    for name,selected in inputs['gates'].items():
        raw=pinned(package/selected['receipt_file'],selected['receipt_sha256'])
        receipt=json.loads(raw)
        envelopes[name]=receipt
        require(receipt['schema']=='cara.model-retirement-gate/v1' and receipt['gate']==name
                and receipt['status']=='passed' and receipt['source_commit']==inputs['source_commit']
                and receipt['archive_sha256']==inputs['archive_sha256']
                and receipt['candidate_environment_sha256']==inputs['candidate_environment_sha256'],
                'exact passed gate envelope required: '+name)
        require(isinstance(receipt['evidence_files'],dict) and receipt['evidence_files'],
                'gate must reference original raw evidence')
        for filename,pin in receipt['evidence_files'].items():
            require(not filename.startswith('/') and '\\' not in filename and
                    all(part not in ('','.','..') for part in filename.split('/')),
                    'safe evidence relative path required')
            pinned(package/filename,pin)
        gates[name]={key:receipt[key] for key in ('status','source_commit','archive_sha256','candidate_environment_sha256')}
        gates[name].update(selected)
    result={key:deepcopy(value) for key,value in draft.items() if key not in
            {'pending','prepared_at','preservation_scope_sha256','historical_closed_trial','historical_closed_trial_files',
             'gates','identity','status','schema','deployment_authority'}}
    result.update(bound)
    result.update(operator_revision_binding(envelopes,inputs,package_hashes))
    result.update(schema='cara.model-retirement-release/v1',status='qualified_ready_to_deploy',
        release_root=inputs['release_root'],identity={'source_commit':inputs['source_commit'],
            'archive_sha256':inputs['archive_sha256'],'controller_sha256':package_hashes['native_backend.py'],
            'operator_package_sha256':digest(package_hashes),'controller_nonce':inputs['controller_nonce'],
            'window_start':inputs['window_start'],'window_end':inputs['window_end']},
        gates=gates,package_sha256s=package_hashes,reference_sha256s=deepcopy(REFERENCE_PINS),
        module_sha256s=modules,runtime_hashes=runtime,native_installer_sha256=installer,
        preserved_source_sha256s=preserved_source,
        test_summary=inputs['test_summary'],
        candidate_environment_sha256=inputs['candidate_environment_sha256'],
        baseline_observation_file=inputs['baseline_observation_file'],
        baseline_observation_sha256=inputs['baseline_observation_sha256'],
        carried_gateways=deepcopy(inputs['carried_gateways']),
        authorization_basis=inputs['authorization_basis'],deployment_authority=deepcopy(inputs['deployment_authority']))
    result.update(observation_sha256=inputs['baseline_observation_sha256'],
                  observation_at=observation['at'],historical_unknown_baseline=deepcopy(observation['database']['unknowns']))
    require(result['deployment_authority'] is not None and result['authorization_basis'],
            'actual scoped owner request required; no inherited authority')
    require(pinned(package/inputs['baseline_observation_file'],inputs['baseline_observation_sha256'])
            and json.loads((package/inputs['baseline_observation_file']).read_bytes())==observation,
            'selected baseline observation bytes differ')
    return validate_plan(result)


def write_new(path, value):
    with Path(path).open('xb') as stream:
        stream.write(canonical(value) + b'\n')


def main():
    if sys.argv[1:2]==['select-data-only']:
        return select_main(sys.argv[2:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--accepted-directory', required=True)
    parser.add_argument('--observation', required=True)
    parser.add_argument('--observation-sha256', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    raw = Path(args.observation).read_bytes()
    require(hashlib.sha256(raw).hexdigest() == args.observation_sha256, 'observed input hash differs')
    records = load_accepted(args.accepted_directory)
    draft = build_draft(records, json.loads(raw), args.observation_sha256)
    scope = preservation_scope(records)
    root = Path(args.output)
    require(not root.exists() and not root.is_symlink(), 'fresh preparation directory required')
    root.mkdir(parents=False, mode=0o700)
    write_new(root/'selection-draft.json', draft)
    write_new(root/'preservation-scope.json', scope)
    print(canonical({'status':draft['status'],'protected_paths':len(scope['protected_expected']),
                     'source_commit':ACCEPTED_SOURCE,'service_actions':0,'model_calls':0,
                     'deployment_authority':None}).decode())


def select_main(argv):
    parser=argparse.ArgumentParser(description='Bind already-passed exact evidence; never deploy.')
    parser.add_argument('--inputs',required=True)
    parser.add_argument('--inputs-sha256',required=True)
    args=parser.parse_args(argv)
    inputs=json.loads(pinned(args.inputs,args.inputs_sha256))
    load=lambda key:json.loads(pinned(inputs[key]['path'],inputs[key]['sha256']))
    plan=selected_plan(load('draft'),load('observation'),load('preservation'),
                       inputs['selection'],inputs['package_directory'])
    path=Path(inputs['output'])
    require(path.parent.is_dir() and not path.exists() and not path.is_symlink(),
            'fresh selection receipt in existing protected directory required')
    write_new(path,plan)
    print(canonical({'status':'selected_data_only','authorization_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                     'service_actions':0,'model_calls':0,'database_writes':0,'controller_launched':False}).decode())


if __name__ == '__main__':
    main()
