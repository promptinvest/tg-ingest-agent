"""Selected, single-use native model-retirement adapter.

Importing is inert. Only --launch with a hash-bound fresh authorization can run.
The two frozen references supply audited native primitives, never a historical
controller. Every dated source/schema/trial/unknown-exception method is overridden.
No schema migration, rollback, trial epoch, gateway update or paid call exists here.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import time

import collect_preflight as observe
import peer_release
from env_bytes import parse_env_bytes
from release_operator import (Controller, file_metadata, replace_environment,
                              require, sha, transform_env_bytes, utc,
                              validate_observation, validate_plan, verify_unknowns)

FROZEN_REFERENCES = {
    'accepted_backend.py': '7bbb9d5b532aa6074305244964fd6ae7541a12df40e20405549d251581fc3961',
    'cara_next_release_checks_draft.py': '42ad9c37814e119270020afd43e1fee39570c06c92bdcb7bb23a838530c167b5',
}
ALLOWED_RUNTIME_DELTA = frozenset({'common.py', 'llm.py', 'cache_usage.py'})
MENTOR_FILES = ('cara-mentor.env', 'cara-mentor-runner.env')
ACCEPTED_PROTECTED_SHA256 = 'b81dff508089edf6535e82f919184bfac4bc3878d254c7c14d0df9d80ac47951'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode()


def now():
    return datetime.now(timezone.utc).isoformat()


def relative_file(root, name):
    item = PurePosixPath(name)
    require(isinstance(name, str) and name and not item.is_absolute()
            and '..' not in item.parts and '\\' not in name and str(item) == name,
            'canonical relative evidence path required')
    path = Path(root).joinpath(*item.parts)
    for parent in (*reversed(path.parents), path):
        require(not parent.is_symlink(), 'evidence path contains symlink')
    return path


def runtime_delta(before, after):
    require(set(before) == set(after), 'installed runtime membership changed')
    changed = sorted(key for key in before if before[key] != after[key])
    require(set(changed) == ALLOWED_RUNTIME_DELTA, 'unexpected model-retirement runtime delta')
    return changed


def validate_baseline_peers(plan, value):
    require(value['neighbors'] == plan['baseline_neighbors']
            and value['preserved_units'] == plan['baseline_preserved_units']
            and value['recovery'] == plan['baseline_recovery'],
            'fresh peer/preserved-unit/recovery identity changed')


def validate_post_transition_inputs(plan):
    """Reject missing inherited native inputs before STARTED or any transition."""
    required = {'test_summary', 'identity', 'carried_gateways', 'mcp', 'facts',
                'facts_obligations', 'facts_cells_sha256', 'facts_pages_sha256',
                'spool_operations', 'spool_expectations', 'publishers'}
    require(required <= set(plan), 'native post-transition input missing')
    require(isinstance(plan['test_summary'], str) and 0 < len(plan['test_summary']) <= 2000
            and bool(plan['test_summary'].strip()) and '\0' not in plan['test_summary'],
            'bounded nonempty native test summary required')
    require(isinstance(plan['identity'], dict) and isinstance(plan['identity'].get('controller_nonce'), str)
            and re.fullmatch('[a-f0-9]{32}', plan['identity']['controller_nonce']), 'fresh native nonce required')
    require(isinstance(plan['carried_gateways'], dict)
            and set(plan['carried_gateways']) == {'armenia', 'exit', 'pd', 'pi', 'rus', 'utah'},
            'six carried native gateways required')
    mcp, facts = plan['mcp'], plan['facts']
    require(isinstance(mcp, dict) and {'enabled', 'config_path', 'config_sha256', 'tools'} <= set(mcp)
            and type(mcp['enabled']) is bool and isinstance(mcp['config_path'], str)
            and mcp['config_path'].startswith('/etc/cara-fleet/')
            and isinstance(mcp['tools'], list) and bool(mcp['tools']), 'native MCP inputs incomplete')
    require(isinstance(facts, dict) and isinstance(facts.get('integrity'), str) and facts['integrity']
            and type(facts.get('revision')) is int and facts['revision'] >= 1
            and isinstance(plan['facts_obligations'], list) and bool(plan['facts_obligations'])
            and all(isinstance(value, dict) for value in plan['facts_obligations']), 'native facts inputs incomplete')
    for value in (mcp['config_sha256'], plan['facts_cells_sha256'], plan['facts_pages_sha256']):
        require(isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value), 'native readiness digest missing')
    operations, expectations = plan['spool_operations'], plan['spool_expectations']
    require(isinstance(operations, list) and isinstance(expectations, list)
            and len(operations) == len(expectations) == 9
            and isinstance(plan['publishers'], (dict, list)), 'all nine native scoped reads required')
    for operation, expected in zip(operations, expectations):
        require(isinstance(operation, dict) and {'operation', 'hosts', 'facets', 'inventory_hash'} <= set(operation)
                and operation['operation'] == 'inspect' and isinstance(operation['hosts'], list)
                and bool(operation['hosts']) and isinstance(operation['facets'], list)
                and isinstance(expected, dict), 'native scoped-read input incomplete')
        if 'read_scope' in operation:
            scope = operation['read_scope']
            require(isinstance(scope, dict) and {'projects', 'capabilities', 'kind', 'version'} <= set(scope)
                    and isinstance(scope['projects'], list) and isinstance(scope['capabilities'], list),
                    'native scoped-read scope incomplete')
            keys = {host + '/' + project + '/' + cap for host in operation['hosts']
                    for project in scope['projects'] for cap in scope['capabilities']}
            require(set(expected) == keys and all(isinstance(value, dict)
                    and set(value) == {'implementation_version', 'allowed_statuses', 'source_id'}
                    for value in expected.values()), 'native scoped-read expectations incomplete')


def verify_peer_selection(plan, actual):
    """Adopt exactly the four authenticated peer monitor files, nothing else."""
    protected = plan['baseline_protected']
    require(len(protected) == 126 and plan['accepted_protected_sha256'] == ACCEPTED_PROTECTED_SHA256,
            'accepted126 protected ancestry differs')
    selected = plan.get('peer_release_linkage')
    if selected is None:
        require(actual is None and sha(canonical(protected)) == ACCEPTED_PROTECTED_SHA256,
                'unexplained protected drift cannot become a baseline')
        return
    require(selected.get('schema') == 'cara.model-retirement-peer-link/v1'
            and selected.get('status') == 'passed' and actual == selected,
            'current peer acceptance/receipt bytes differ from selected proof')
    require(selected.get('root') == peer_release.ROOT and selected.get('source') == peer_release.SOURCE
            and selected.get('image') == peer_release.IMAGE
            and selected.get('verification_receipt_sha256') == peer_release.VERIFICATION
            and set(selected.get('receipt_sha256s', {})) == set(peer_release.RECEIPTS)
            and selected.get('active_release_controllers') == 0
            and selected.get('neighbors') == plan['baseline_neighbors'],
            'precise completed peer release linkage required')
    originals = plan['peer_original_units']
    require(set(originals) == set(peer_release.UNIT_PATHS)
            and selected.get('unit_source_sha256s') == {
                path: protected[path]['sha256'] for path in peer_release.UNIT_PATHS},
            'only four selected peer monitor files may change')
    for path, before in originals.items():
        after = protected[path]
        require(before['sha256'] != after['sha256'] and
                {key: value for key, value in before.items() if key != 'sha256'} ==
                {key: value for key, value in after.items() if key != 'sha256'},
                'peer adoption must preserve original monitor ownership/mode')
    require(sha(canonical({**protected, **originals})) == ACCEPTED_PROTECTED_SHA256,
            'unrelated accepted protected bytes changed during peer adoption')


def validate_gate(name, selected, value, actual_evidence):
    require(value.get('schema') == 'cara.model-retirement-gate/v1'
            and value.get('gate') == name, 'normalized gate schema/name differs')
    for key in ('status', 'source_commit', 'archive_sha256'):
        require(value.get(key) == selected[key], 'raw gate binding differs: ' + name)
    require(value['status'] == 'passed', 'pending gate cannot authorize release')
    if name in ('model_qualification', 'semantic_review'):
        require(value.get('candidate_environment_sha256') == selected['candidate_environment_sha256'],
                'raw model gate environment differs')
    require(isinstance(value.get('evidence_files'), dict) and value['evidence_files']
            and value['evidence_files'] == actual_evidence, 'raw gate evidence differs or absent')
    return value


def verify_qualification(plan, prepared, inputs, raw, semantic, hashes):
    """Independently bind raw native accounting and every reviewed answer."""
    text_models = {'deepseek-v4.1-flash-1017', 'openai-gpt-oss-120b'}
    vision_model = 'deepseek-v4.1-flash'
    profiles = {'router_fast', 'ingest_balanced', 'ask_grounded', 'converse_warm',
                'memory_curator', 'memory_consolidate', 'task_planner', 'task_synthesis'}
    identity = plan['identity']
    for value in (prepared, raw, semantic):
        require(all(value.get(key) == identity[key] for key in ('source_commit', 'archive_sha256'))
                and value.get('candidate_environment_sha256') == plan['candidate_environment_sha256'],
                'model/semantic source or environment binding differs')
    require(prepared['schema'] == 'cara.model-retirement-prepared/v1'
            and raw['schema'] == 'cara.model-retirement-raw/v1'
            and raw['status'] == 'functional_passed_pending_semantic'
            and semantic['schema'] == 'cara.model-retirement-semantic/v1'
            and semantic['status'] == 'passed', 'complete functional and independent semantic evidence required')
    require(raw['plan_sha256'] == semantic['plan_sha256'] == hashes['plan']
            and prepared['qualification_inputs_sha256'] == raw['qualification_inputs_sha256']
            == semantic['inputs_sha256'] == hashes['inputs']
            and semantic['raw_sha256'] == hashes['raw'], 'qualification raw byte lineage differs')
    originals = inputs['prepared']['cases']
    require(len(originals) == 24 and len({case['id'] for case in originals}) == 24
            and {case['profile'] for case in originals} == profiles, 'all24 original native cases required')
    expected = {}
    for case in originals:
        require(sha(canonical(case['messages'])) == case['input_sha256'], 'original prompt hash differs')
        for model in set(case['runtime_profile']['fallbacks']) & text_models:
            require(model != case['runtime_profile']['primary'], 'primary qualification outside selected scope')
            expected[case['id'] + '__' + model] = (case['profile'], model, case['input_sha256'])
    vision_ids = {'vision-describe-ru', 'vision-describe-en', 'vision-classify-ru',
                  'vision-classify-en', 'vision-extract-book', 'vision-extract-blank'}
    cases = inputs['cases']
    require(len({case['id'] for case in cases}) == len(cases), 'duplicate qualification inputs')
    vision = [case for case in cases if case['kind'] == 'vision']
    require({case['id'] for case in vision} == vision_ids, 'all6 native vision cases required')
    for case in vision:
        require(case['model'] == vision_model and sha(canonical(case['messages'])) == case['input_sha256'],
                'vision input/model identity differs')
        expected[case['id']] = (case['profile'], vision_model, case['input_sha256'])
    require({case['id']: (case['profile'], case['model'], case['input_sha256']) for case in cases} == expected,
            'expanded current fallback/vision coverage differs')
    require({row[0] for row in expected.values()} >= profiles
            and {row[1] for row in expected.values()} == text_models | {vision_model}, 'selected model coverage incomplete')
    coverage = {}
    for case in cases:
        coverage.setdefault(case['profile'], {}).setdefault(case['model'], []).append(case['id'])
    require(prepared['coverage'] == raw['coverage'] == raw['expected_coverage'] == coverage
            and prepared['cases_expected'] == raw['cases_expected'] == raw['cases_completed'] == len(expected),
            'qualification completion/coverage differs')
    require(len(raw['cases']) == len(expected)
            and {case['id'] for case in raw['cases']} == set(expected), 'raw case completion differs')
    usage = raw['usage']
    require(len(usage) == len(expected) and len({row['id'] for row in usage}) == len(usage),
            'one accounted native reservation per case required')
    by_id = {row['id']: row for row in usage}
    reviews = semantic['cases']
    require(len(reviews) == len(expected) and {item['id'] for item in reviews} == set(expected),
            'every answer needs independent semantic review')
    reviewed = {item['id']: item for item in reviews}
    reservations = set()
    for case in raw['cases']:
        require((case['profile'], case['model'], case['input_sha256']) == expected[case['id']]
                and case['provider_model'] == case['model']
                and case['status'] == 'functional_passed_pending_semantic'
                and case['finish_reason'] != 'length' and sha(case['raw'].encode()) == case['raw_sha256'],
                'raw model identity/function/output differs')
        row = by_id[case['reservation_id']]
        require(row['model'] == case['model'] and row['kind'] == 'chat', 'case reservation not settled as native chat')
        reservations.add(case['reservation_id'])
        review = reviewed[case['id']]
        require(review['raw_sha256'] == case['raw_sha256'] and review['status'] == 'passed'
                and isinstance(review.get('reason'), str) and bool(review['reason'].strip()),
                'raw answer semantic review missing or failed')
    require(reservations == set(by_id), 'reservation reused or unbound')
    aggregate = raw['migration_usage']
    require(len({row['id'] for row in aggregate}) == len(aggregate)
            and all(any(all(item.get(key) == row.get(key) for key in row) for item in aggregate) for row in usage),
            'aggregate migration accounting omitted current reservation')
    for row in aggregate:
        cost = row['cost_usd']
        require(row['kind'] in ('chat', 'rejected_chat') and type(cost) in (int, float)
                and math.isfinite(cost) and 0 <= cost <= .15
                and (row['kind'] != 'rejected_chat' or cost == 0), 'unknown/invalid migration charge')
    total = sum(row['cost_usd'] for row in aggregate)
    require(total <= .25 + 1e-12 and raw['migration_accounted_cost_usd'] == total
            and raw['accounted_cost_usd'] == sum(row['cost_usd'] for row in usage), 'migration budget accounting differs')
    require(raw['unknown_usage_ids'] == raw['migration_unknown_usage_ids'] == []
            and raw['unknown_reserve_usd'] == raw['migration_unknown_reserve_usd'] == 0
            and raw['messages_sent'] == 0 and raw['live_business_data_changed'] is False,
            'unknown charge or live business effect in qualification')
    require(all(value['aggregate_ceiling_usd'] == .25 and value['per_call_ceiling_usd'] == .15
                for value in (prepared, raw)), 'qualification budget limits differ')
    return {'cases': len(expected), 'migration_accounted_cost_usd': total, 'new_unknowns': 0,
            'semantic_cases': len(reviews), 'raw_sha256': hashes['raw']}


def qualification_helpers(plan):
    mode = plan.get('qualification_mode')
    require(mode in (None, 'full_native_existing_correction'), 'unrecognized qualification mode')
    return {'fullflow_gate.py', 'qualify_nativeflow.py'} if mode else set()


def verify_model_gates(plan, evidence, bound_role, source_hashes, fullflow_validator=None):
    """Dispatch explicit, separately reviewed qualification contracts only."""
    helpers = qualification_helpers(plan)
    model, semantics = (evidence[name] for name in ('model_qualification', 'semantic_review'))
    model_roles, semantic_roles = model['evidence_roles'], semantics['evidence_roles']
    roles = {'plan', 'inputs', 'raw'} | ({'original_plan', 'original_raw', 'original_case'} if helpers else set())
    require(set(model_roles) == roles and set(semantic_roles) == {'review'},
            'model/semantic raw role bindings incomplete')
    require(model.get('qualification_mode') == semantics.get('qualification_mode') == plan.get('qualification_mode'),
            'model and semantic gate modes differ from selection')
    args = (plan, *(bound_role(model, model_roles[key]) for key in ('plan', 'inputs', 'raw')),
            bound_role(semantics, semantic_roles['review']),
            {key: model['evidence_files'][filename] for key, filename in model_roles.items()})
    if not helpers:
        return verify_qualification(*args)
    revision = plan['operator_source_commit']
    require(isinstance(revision, str) and bool(re.fullmatch('[0-9a-f]{40}', revision))
            and revision != plan['identity']['source_commit'], 'distinct qualified operator revision required')
    package_sha = sha(canonical(plan['package_sha256s']))
    for name in ('source_review', 'operator_qualification'):
        require(evidence[name].get('operator_source_commit') == revision
                and evidence[name].get('operator_package_sha256') == package_sha,
                'new operator source review/qualification package binding differs')
    require(fullflow_validator is not None, 'authenticated nativeflow validator required')
    return fullflow_validator(*args,
        *(bound_role(model, model_roles[key]) for key in ('original_plan', 'original_raw', 'original_case')),
        source_hashes)


def mentor_projections(raw, default_timeout=900, max_timeout=1200):
    """Exact native writer projection; original files are restored before startup.

    This intentionally mirrors cara_mentor's simple selected-key parser, including
    its quote handling, rather than using the stricter main EnvironmentFile parser.
    Tests compare these bytes with the unchanged native writers using fake paths.
    """
    values = {}
    for line in raw.decode('utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = (part.strip() for part in line.split('=', 1))
        if len(value) >= 2 and value[:1] == value[-1:] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    require(bool(values.get('DO_MODEL_ACCESS_KEY')), 'native Mentor projection requires configured key')
    inference = ('DO_MODEL_ACCESS_KEY', 'DO_INFERENCE_BASE_URL', 'DO_CHAT_MODEL',
                 'MENTOR_MODEL', 'MENTOR_LLM_TIMEOUT_SECONDS', 'MENTOR_MAX_CALLS_PER_WEEK')
    values.setdefault('MENTOR_TEST_TIMEOUT_SECONDS', str(default_timeout))
    require(120 <= int(values['MENTOR_TEST_TIMEOUT_SECONDS']) <= max_timeout,
            'native Mentor runner timeout invalid')
    def render(keys):
        return ''.join(f'{key}={json.dumps(values[key], ensure_ascii=False)}\n'
                       for key in keys if values.get(key)).encode()
    return {MENTOR_FILES[0]: render(inference),
            MENTOR_FILES[1]: render(('MENTOR_TEST_TIMEOUT_SECONDS',))}


def load_reference(root, selected):
    """Authenticate both exact frozen files before importing either one."""
    require(selected == FROZEN_REFERENCES, 'unreviewed native reference selection')
    directory = Path(root) / 'reference'
    for filename, expected in FROZEN_REFERENCES.items():
        try:
            raw, _ = observe.safe_file(relative_file(directory, filename))
        except OSError as error:
            raise ValueError('native reference unavailable') from error
        require(sha(raw) == expected, 'native reference bytes changed')
    checks_name = 'cara_next_release_checks_draft'
    for name, filename in ((checks_name, checks_name + '.py'),
                           ('cara_model_retirement_frozen_backend', 'accepted_backend.py')):
        require(name not in sys.modules, 'native reference already loaded; no module reuse')
        spec = importlib.util.spec_from_file_location(name, directory / filename)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
    return sys.modules['cara_model_retirement_frozen_backend']


class NativeMethods:
    """Fresh policy over frozen native I/O primitives; no inherited dated policy."""

    def __init__(self, root, controller_path, authorization_sha256):
        require(os.name == 'posix' and os.geteuid() == 0, 'authenticated Linux root required')
        self.root = self.core.protected(root, directory=True)
        require(re.fullmatch(r'/root/cara-model-retirement-20[0-9]{6}/release-attempt[1-9][0-9]*-[a-f0-9]{8,32}',
                             str(self.root)), 'fresh model-retirement release root required')
        self.source = self.core.protected(self.root / 'source', directory=True)
        self.controller = self.core.protected(controller_path)
        self.authorization_sha256 = authorization_sha256
        self.plan = self.baseline_value = self.stopped_original = self.unknown_baseline = None
        self.approved_changes = {}
        self.publisher_outputs = {}

    def write(self, name, value):
        require(re.fullmatch(r'[a-z0-9-]{1,70}', name), 'fixed private receipt name required')
        target = self.root / (name + '.json')
        # Phase is a progress pointer; all other results are immutable originals.
        require(name == 'phase' or not target.exists(), 'receipt already exists; never replay')
        self.core.atomic(target, canonical(value) + b'\n')

    def reject_started(self):
        require(not any((self.root / name).exists() or (self.root / name).is_symlink()
                        for name in ('STARTED', 'acceptance.json', 'failure.json', 'preflight.json')),
                'release already started; inspect originals instead of replaying')

    def window(self, identity):
        require(self.core.file_sha(self.core.protected(self.root / 'authorization.json'))
                == self.authorization_sha256, 'authorization changed after selection')
        require(utc(identity['window_start']) <= datetime.now(timezone.utc) <= utc(identity['window_end']),
                'selected one-hour release window expired/not active')

    def phase(self, name, identity):
        self.write('phase', {'schema': 'cara.model-retirement-phase/v1', 'phase': name,
                            'identity': identity, 'authorization_sha256': self.authorization_sha256,
                            'at': now()})

    def read_inputs(self, plan):
        validate_plan(plan)
        validate_post_transition_inputs(plan)
        require(self.core.read_json(self.root / 'authorization.json') == plan,
                'loaded authorization differs')
        self.plan = copy.deepcopy(plan)
        require(str(self.root) == plan['release_root'], 'selected new release root differs')
        require(self.core.file_sha(self.controller) == plan['identity']['controller_sha256'],
                'fresh adapter controller hash differs')
        for name, expected in plan['package_sha256s'].items():
            require(self.core.file_sha(self.core.protected(relative_file(self.root, name))) == expected,
                    'qualified operator package changed')
        require(sha(canonical(plan['package_sha256s'])) == plan['identity']['operator_package_sha256'],
                'operator package map binding differs')
        expected_helpers = {'native_backend.py', 'release_operator.py', 'env_bytes.py',
                            'transform_config.py', 'collect_preflight.py', 'peer_release.py'}
        extra_helpers = qualification_helpers(plan)
        require(expected_helpers | extra_helpers <= set(plan['package_sha256s']) and
                all(plan['package_sha256s'].get('reference/' + name) == value
                    for name, value in FROZEN_REFERENCES.items()), 'operator helper coverage incomplete')
        for name in expected_helpers:
            module = sys.modules[name[:-3]] if name != 'native_backend.py' else sys.modules[__name__]
            require(Path(module.__file__).resolve() == self.root / name,
                    'imported operator helper is outside authenticated package')
        fullflow_validator = None
        if extra_helpers:
            # Import only after every byte in the selected package is authenticated.
            # The paid companion is evidence provenance, never an imported release action.
            spec = importlib.util.spec_from_file_location('cara_authenticated_fullflow_gate', self.root / 'fullflow_gate.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            fullflow_validator = module.verify_fullflow
        files = self._archive(plan['identity']['archive_sha256'])
        modules = self.core.checks.expected_modules(
            (self.source / 'install-tg-ingest-agent-pilot-remote.sh').read_text(),
            {name: value for name, value in files.items() if '/' not in name and name.endswith('.py')})
        require(modules == plan['module_sha256s'], 'selected installer module map differs')
        require(plan['runtime_hashes'] == {**modules, 'verify-cara-runtime.sh': files['verify-cara-runtime.sh']},
                'complete installed runtime map differs')
        delta = runtime_delta(plan['baseline_runtime_hashes'], plan['runtime_hashes'])
        require({'install-tg-ingest-agent-pilot-remote.sh', 'verify-cara-runtime.sh',
                 'tg-ingest-agent.service', 'cara-worker.service', 'cara-mentor.service',
                 'cara-mentor-runner.service', 'cara-failed-notify@.service'}
                <= set(plan['preserved_source_sha256s']), 'native installer/unit source preservation incomplete')
        require(all(files.get(name) == expected for name, expected in plan['preserved_source_sha256s'].items()),
                'native installer/unit preservation differs')
        # The installer and dormant Mentor writer must retain their native semantics.
        for name in ('cara_mentor.py', 'mentor_protocol.py', 'verify_installed_layout.py'):
            require(plan['baseline_module_sha256s'][name] == modules[name], 'native preservation helper changed')
        evidence = {}
        for name, gate in plan['gates'].items():
            raw = self.core.read_bytes(self.core.protected(relative_file(self.root, gate['receipt_file'])))
            require(sha(raw) == gate['receipt_sha256'], 'selected gate envelope changed')
            envelope = self.core.json_bytes(raw)
            actual = {file: self.core.file_sha(self.core.protected(relative_file(self.root, file)))
                      for file in envelope.get('evidence_files', {})}
            evidence[name] = validate_gate(name, gate, envelope, actual)
        def bound_role(envelope, filename):
            require(filename in envelope['evidence_files'], 'unbound model evidence path')
            return self.core.read_json(relative_file(self.root, filename))
        evidence['model_validation'] = verify_model_gates(plan, evidence, bound_role, files, fullflow_validator)
        observation_path = relative_file(self.root, plan['baseline_observation_file'])
        require(self.core.file_sha(observation_path) == plan['baseline_observation_sha256'],
                'selected fresh baseline observation changed')
        selected_observation = self.core.read_json(observation_path)
        validate_observation(selected_observation, plan['accepted_source_commit'],
                             plan['original_environment_sha256'])
        require(selected_observation['identity']['sha256'] == plan['baseline_native_sha256']
                and {key: value['sha256'] for key, value in selected_observation['runtime'].items()}
                == plan['baseline_runtime_hashes'], 'fresh observed native/runtime binding differs')
        self.bind_unknown_baseline(selected_observation['database']['unknowns'])
        self.selected_observation = selected_observation
        self.publisher_outputs = copy.deepcopy(plan['source_outputs'])
        self._verify_peer_release()
        self.window(plan['identity'])
        return {'gates': evidence, 'source_delta': delta, 'model_calls': 0,
                'baseline_observation_sha256': plan['baseline_observation_sha256']}

    def current_observation(self):
        value = observe.collect()
        require(value['units'] == self.selected_observation['units'], 'accepted process identity changed')
        verify_unknowns(self.unknown_baseline, value['database']['unknowns'])
        return value

    def bind_unknown_baseline(self, value):
        verify_unknowns(value, value)
        if self.unknown_baseline is not None:
            verify_unknowns(self.unknown_baseline, value)
        self.unknown_baseline = copy.deepcopy(value)
        self.main_process_started_at = value['process_cutoff']

    def protected_manifest(self):
        result = self.core.RootBackend.protected_manifest(self)
        for path in self.plan['baseline_protected']:
            if path not in result:
                result[path] = self.core.metadata(self.core.protected(path))
        return result

    def _expected_protected(self, baseline, gateway):
        return {**baseline['protected'], **self.approved_changes}

    def _verify_peer_release(self):
        actual = None
        if self.plan.get('peer_release_linkage') is not None:
            actual = peer_release.collect(self.plan['baseline_protected'], self.neighbors())
        verify_peer_selection(self.plan, actual)

    def check_preinstall_preservation(self, baseline, gateway):
        # Authenticate the same terminal receipts again at every stopped/prestart
        # and final preservation boundary; a newer peer release is not adopted.
        self._verify_peer_release()
        return self.core.RootBackend.check_preinstall_preservation(self, baseline, gateway)

    def _idle(self):
        require(self.unknown_baseline is not None, 'fixed initial unknown baseline required')
        cutoff = self.unknown_baseline['process_cutoff']
        observed = observe.database_observation(self.core.DB, cutoff)
        require(observed['quick_check_ok'] and observed['foreign_key_violations'] == 0
                and observed['tables'] == 56, 'canonical56-table database health failed')
        require(all(type(value) is int and value == 0 for value in observed['work_counts'].values()),
                'active owner work must settle before transition')
        verify_unknowns(self.unknown_baseline, observed['unknowns'])
        require(observed['budget_preferences_sha256'] ==
                self.selected_observation['database']['budget_preferences_sha256'], 'budget preferences changed')
        with closing(self.core.read_db()) as connection:
            require(connection.execute('PRAGMA integrity_check').fetchall() == [('ok',)], 'canonical integrity failed')
            work = self.core.idle_database_work(connection, cutoff)
            pins = [list(row) for row in connection.execute("SELECT key,value FROM kv WHERE key LIKE 'backup%' ORDER BY key")]
            ddl = self.core.database_ddl(connection)
            for raw, in connection.execute("SELECT result FROM jobs WHERE status='failed' AND julianday(finished_at)>=julianday(?)", (cutoff,)):
                if raw:
                    value = self.core.json_bytes(raw.encode())
                    require(not (isinstance(value, dict) and value.get('background_inference') == 'started'),
                            'current-process unknown paid inference must settle: maintenance')
        if self.stopped_original is not None:
            require(ddl == self.stopped_original['ddl'], 'canonical schema changed')
        directory = self.core.SPOOL / 'requests'
        info = directory.lstat()
        require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode), 'unsafe request spool')
        with os.scandir(directory) as entries:
            require(next(entries, None) is None, 'unsettled request/cancel/purge spool residue')
        return {'table_count': 56, 'main_process_started_at': cutoff, **work,
                'backup_pins': pins, 'integrity_check': 'ok', 'foreign_key_violations': [],
                'unsettled_spool': 0, 'unknowns': observed['unknowns']}

    def baseline(self, plan):
        value = self.core.RootBackend.baseline(self, plan)
        require(value['manifest'] == plan['baseline_manifest']
                and self.core.file_sha(self.core.APP / 'DEPLOYMENT.json') == plan['baseline_native_sha256'],
                'accepted native identity changed')
        self._runtime_exact(plan['baseline_runtime_hashes'])
        validate_baseline_peers(plan, value)
        for name in ('tg-ingest-agent', 'cara-worker', 'cara-fleet', 'whisper-server'):
            require(value['units'][name]['ActiveState'] == 'active'
                    and value['units'][name]['NRestarts'] == '0', 'healthy baseline services required')
        self._mentors_disabled()
        self.preserve_closed_trial(write_receipt=False)
        for path, expected in self.publisher_outputs.items():
            require(self.core.metadata(self.core.protected(path)) == expected, 'source output changed')
        return value

    def _runtime_exact(self, expected):
        actual = {p.name: self.core.file_sha(self.core.protected(p)) for p in self.core.APP.iterdir()
                  if p.is_file() and p.suffix in ('.py', '.sh')}
        require(actual == expected, 'complete installed runtime bytes differ')

    def _mentors_disabled(self):
        values = self.units(('cara-mentor', 'cara-mentor-runner'))
        require(all(row['ActiveState'] == 'inactive' and row['MainPID'] == '0'
                    for row in values.values()),
                'retired Mentor services must remain disabled and inactive')
        for unit in values:
            require(self.command(['/usr/bin/systemctl', 'show', unit, '-p', 'UnitFileState', '--value']).strip()
                    == 'disabled', 'retired Mentor unit must remain disabled')

    def staged_functional_gate(self):
        self.command(['/usr/bin/python3', '-I', '-B', str(self.source / 'verify_installed_layout.py'),
                      '--source-root', str(self.source)], log='staged-installed-layout.log', timeout=150)

    def backup_stopped(self, baseline, online):
        self.assert_stopped()
        require(self._idle()['backup_pins'] == baseline['backup_pins'], 'backup pins changed')
        stopped = self._backup('stopped-before.db')
        with closing(self.core.read_db()) as connection, closing(self.core.read_db(self.root / 'stopped-before.db')) as backup:
            original = {'tables': self.core.database_snapshot(connection), 'ddl': self.core.database_ddl(connection)}
            require(len(original['tables']) == 56 and self.core.database_snapshot(backup) == original['tables']
                    and self.core.database_ddl(backup) == original['ddl'], 'stopped56 backup row/schema parity failed')
        self.stopped_original = original
        self.write('stopped-original', original)
        parity = {'at': now(), 'original_tables': 56, 'total_tables': 56,
                  'changed_original_rows': 0, 'changed_original_columns': 0,
                  'schema_changed': False, 'migration_invocations': 0,
                  'tables_sha256': sha(canonical(original['tables'])), 'ddl_sha256': sha(canonical(original['ddl']))}
        self.write('stopped-parity', parity)
        result = {**online, 'stopped_sqlite': stopped, 'stopped_parity': parity}
        self.write('backup-receipts', result)
        return result

    def verify_stopped_no_schema_change(self, stopped):
        self.assert_stopped()
        with closing(self.core.read_db()) as connection:
            require(self.core.database_snapshot(connection) == self.stopped_original['tables']
                    and self.core.database_ddl(connection) == self.stopped_original['ddl'],
                    'stopped original rows or schema changed')
        require(stopped['stopped_parity']['migration_invocations'] == 0, 'migration forbidden')

    def preserve_closed_trial(self, write_receipt=True):
        for path, expected in self.plan['closed_trial_files'].items():
            require(self.core.file_sha(self.core.protected(path)) == expected, 'closed trial original changed')
        require({unit: observe.unit_state(unit) for unit in self.plan['closed_trial_units']}
                == self.plan['closed_trial_units'],
                'closed trial/observer unit state changed')
        if write_receipt:
            self.write('closed-trial-preserved', {'at': now(), 'files_sha256': sha(canonical(self.plan['closed_trial_files'])),
                                                'new_trial_epochs': 0, 'observers_replayed': 0})

    def carry_existing_gateways(self, plan, stopped):
        self.assert_stopped()
        gateways = plan['carried_gateways']
        require(set(gateways) == {'armenia', 'exit', 'pd', 'pi', 'rus', 'utah'}, 'six accepted gateways required')
        self.write('gateway-qualification', {'receipts': gateways, 'updates_performed': 0,
                                             'historical_dates_unchanged': True})
        self.write('source-publications', {'outputs': self.publisher_outputs, 'publishers_run': 0})
        return gateways

    def replace_model_environment(self, plan):
        self.assert_stopped()
        raw = self.core.read_bytes(self.core.protected(observe.ENV))
        expected = self.baseline_value['protected'][str(observe.ENV)]
        require(sha(raw) == plan['original_environment_sha256'] == expected['sha256'], 'original env changed')
        candidate, proof = transform_env_bytes(raw)
        require(sha(candidate) == plan['candidate_environment_sha256'], 'qualified candidate env differs')
        for name, content in (('original.env', raw), ('candidate.env', candidate)):
            require(not (self.root / name).exists(), 'private environment evidence already exists')
            self.core.atomic(self.root / name, content)
        for name in MENTOR_FILES:
            path = Path('/etc') / name
            require(self.core.metadata(path) == self.baseline_value['protected'][str(path)],
                    'retired Mentor original changed')
            self.core.atomic(self.root / ('original-' + name), self.core.read_bytes(path))
        self.approved_changes[str(observe.ENV)] = replace_environment(observe.ENV, candidate, expected)
        self.write('environment-transition', {'at': now(), 'proof': proof, 'metadata': self.approved_changes[str(observe.ENV)],
                                            'secrets_recorded': False, 'history_rewritten': False})

    def _installer_adapter(self):
        options = {'root': str(self.root), 'authorization_sha256': self.authorization_sha256,
                   'package_sha256s': self.plan['package_sha256s']}
        return ('#!/usr/bin/python3 -I\nOPTIONS=' + repr(options) + '\n' + r'''
import hashlib,os,stat,sys
from pathlib import Path
root=Path(OPTIONS['root'])
for path in (*reversed(root.parents),root):
    info=path.lstat()
    assert stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode) and info.st_uid==0 and not info.st_mode&0o022
for name,expected in OPTIONS['package_sha256s'].items():
    path=root/name
    info=path.lstat()
    assert stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode) and info.st_nlink==1 and info.st_uid==0 and not info.st_mode&0o022
    assert hashlib.sha256(path.read_bytes()).hexdigest()==expected
sys.path.insert(0,str(root))
import native_backend
backend=native_backend.load_selected(root,OPTIONS['authorization_sha256'])
arguments=sys.argv[1:]
phase=backend.core.installer_systemctl_action(arguments)
if phase: backend.installer_prestart(phase)
os.execve('/usr/bin/systemctl',['/usr/bin/systemctl',*arguments],{'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8'})
''').encode()

    def install_exact(self, plan, gateway):
        self.assert_stopped()
        self.check_preinstall_preservation(self.baseline_value, gateway)
        packages = ['python3', 'ca-certificates', 'sqlite3', 'python3-pdfminer', 'git']
        lines = self.command(['/usr/bin/dpkg-query', '-W', '-f=${Package} ${Status}\n', *packages]).splitlines()
        require(len(lines) == len(packages) and all(line.endswith('install ok installed') for line in lines),
                'existing native prerequisites unavailable')
        adapter = self.root / 'prerequisites'
        require(not adapter.exists(), 'installer adapter already exists')
        adapter.mkdir(mode=0o700)
        self.core.atomic(adapter / 'apt-get', b'''#!/usr/bin/python3
import subprocess,sys
a=sys.argv[1:]
if a==['update']: print('Existing prerequisites verified; package index unchanged')
elif a==['install','-y','--no-install-recommends','python3','ca-certificates','sqlite3','python3-pdfminer','git']:
 p=subprocess.run(['/usr/bin/dpkg-query','-W','-f=${Status}\\n',*a[3:]],capture_output=True,text=True)
 assert p.returncode==0 and len(p.stdout.splitlines())==5 and all(s=='install ok installed' for s in p.stdout.splitlines())
else: raise SystemExit('Unexpected native dependency command')
''', 0o700)
        self.write('installer-guard-plan', {'authorization_sha256': self.authorization_sha256,
                  'stopped_original_sha256': self.core.file_sha(self.root / 'stopped-original.json'),
                  'baseline_sha256': self.core.file_sha(self.root / 'preflight.json'),
                  'environment_transition_sha256': self.core.file_sha(self.root / 'environment-transition.json')})
        self.core.atomic(adapter / 'systemctl', self._installer_adapter(), 0o700)
        self.installed_at = now()
        self.command(['/usr/bin/bash', str(self.source / 'install-tg-ingest-agent-pilot-remote.sh')],
                     log='install.log', timeout=240,
                     env={'STAGE_DIR': str(self.source), 'DEPLOY_SOURCE_REVISION': plan['identity']['source_commit'],
                          'DEPLOY_SOURCE_DIRTY': 'false', 'DEPLOY_TEST_SUMMARY': plan['test_summary'],
                          'PATH': str(adapter) + ':/usr/bin:/bin:/usr/sbin'})
        self._runtime_exact(plan['runtime_hashes'])
        for phase in ('before-worker-start', 'before-main-start'):
            value = self.core.read_json(self.root / (phase + '.json'))
            require(value['identity'] == plan['identity'] and value['module_sha256s'] == plan['module_sha256s']
                    and value['tables'] == 56 and value['changed_rows'] == 0 and not value['schema_changed'],
                    'native prestart evidence differs')
        deployment = self.core.read_json(self.core.APP / 'DEPLOYMENT.json')
        require(deployment['status'] == 'installed' and deployment['source_revision'] == plan['identity']['source_commit']
                and deployment['source_dirty'] is False
                and deployment['deployment_id'] != plan['baseline_manifest']['deployment_id'], 'installed native identity differs')
        result = {'at': self.installed_at, 'module_sha256s': plan['module_sha256s'], 'deployment': deployment}
        self.write('installed-source', result)
        self.start_installed_executor(plan)
        return result

    def installer_prestart(self, phase):
        require(phase in ('before-worker-start', 'before-main-start'), 'unknown native prestart boundary')
        self.window(self.plan['identity'])
        require((self.root / 'STARTED').is_file(), 'installer cannot precede single-use start')
        guard = self.core.read_json(self.root / 'installer-guard-plan.json')
        require(guard['authorization_sha256'] == self.authorization_sha256, 'prestart authority differs')
        for key, name in (('stopped_original_sha256', 'stopped-original.json'),
                          ('baseline_sha256', 'preflight.json'),
                          ('environment_transition_sha256', 'environment-transition.json')):
            require(self.core.file_sha(self.root / name) == guard[key], 'prestart original evidence changed')
        self.baseline_value = self.core.read_json(self.root / 'preflight.json')
        self.stopped_original = self.core.read_json(self.root / 'stopped-original.json')
        self.approved_changes[str(observe.ENV)] = self.core.read_json(self.root / 'environment-transition.json')['metadata']
        self.assert_stopped()
        self._runtime_exact(self.plan['runtime_hashes'])
        with closing(self.core.read_db()) as connection:
            require(len(self.stopped_original['tables']) == 56
                    and self.core.database_snapshot(connection) == self.stopped_original['tables']
                    and self.core.database_ddl(connection) == self.stopped_original['ddl'], 'native prestart56 row/DDL parity failed')
        require(self.core.file_sha(observe.ENV) == self.plan['candidate_environment_sha256'], 'native installer changed candidate env')
        if phase == 'before-worker-start':
            self._restore_retired_mentor()
        else:
            require((self.root / 'retired-mentor-preserved.json').exists(), 'retired Mentor restoration missing')
        self._mentors_disabled()
        self.check_preinstall_preservation(self.baseline_value, self.plan['carried_gateways'])
        self.preserve_closed_trial(write_receipt=False)
        self.command(['/usr/bin/python3', '-I', '-B', str(self.source / 'verify_installed_layout.py'),
                      '--source-root', str(self.source), '--installed-root', str(self.core.APP)],
                     log=phase + '-layout.log', timeout=150)
        self.write(phase, {'at': now(), 'identity': self.plan['identity'], 'phase': phase,
                          'module_sha256s': self.plan['module_sha256s'], 'tables': 56,
                          'changed_rows': 0, 'schema_changed': False, 'retired_mentor_preserved': True})

    def _restore_retired_mentor(self):
        import grp
        expected = mentor_projections(self.core.read_bytes(self.root / 'candidate.env'))
        result = {}
        for name, group in zip(MENTOR_FILES, ('cara-mentor-spool', 'cara-mentor-runner-spool')):
            path = Path('/etc') / name
            generated = {'sha256': sha(expected[name]), 'uid': 0, 'gid': grp.getgrnam(group).gr_gid, 'mode': 0o640}
            require(file_metadata(path) == generated, 'native generated Mentor bytes/metadata differ')
            original = self.baseline_value['protected'][str(path)]
            raw = self.core.read_bytes(self.root / ('original-' + name))
            require(sha(raw) == original['sha256'], 'retired Mentor original changed')
            self.core.atomic(self.root / ('installer-generated-' + name), expected[name])
            # Parent dir and existing target are authenticated; compare immediately
            # before root's atomic fsync replacement and verify original metadata.
            self.core.protected(path)
            require(file_metadata(path) == generated, 'Mentor file changed before restoration')
            self.core.atomic(path, raw, original['mode'], original['uid'], original['gid'])
            require(file_metadata(path) == original, 'retired Mentor original restoration differs')
            result[name] = {'original': original, 'native_generated': generated}
        self.write('retired-mentor-preserved', {'at': now(), 'files': result, 'services_started': 0})

    def start_installed_executor(self, plan):
        self.window(plan['identity'])
        state = self.units(('cara-fleet',))['cara-fleet']
        require(state['NRestarts'] == '0', 'executor restart failure')
        if state['ActiveState'] == 'inactive' and state['MainPID'] == '0':
            self.command(['/usr/bin/systemctl', 'start', 'cara-fleet'], log='start-installed-executor.log', timeout=30)
        else:
            require(state['ActiveState'] in ('active', 'activating') and int(state['MainPID']) > 1
                    and state['MainPID'] != self.baseline_value['units']['cara-fleet']['MainPID'],
                    'fresh stock-installer executor dependency required')

    def wait_stable_identity(self, unit, deadline):
        previous = None
        transient = {'root does not qualify application readiness',
                     'process real/effective/saved identity differs', 'service PID unavailable'}
        while True:
            self.window(self.plan['identity'])
            require(time.monotonic() < deadline, 'original40s startup bound expired')
            state = self.units((unit,))[unit]
            require(state['LoadState'] == 'loaded' and state['ActiveState'] in ('active', 'activating')
                    and state['NRestarts'] == '0', 'readiness service unavailable')
            try:
                actual = self.process_identity(unit)
            except ValueError as error:
                if str(error) not in transient:
                    raise
                actual = None
            except (FileNotFoundError, ProcessLookupError):
                actual = None
            if actual is not None and str(actual['pid']) != state['MainPID']:
                actual = None
            if actual is not None and actual == previous:
                require(time.monotonic() < deadline, 'original40s startup bound expired')
                return actual
            previous = actual
            time.sleep(min(.2, max(0, deadline - time.monotonic())))

    def namespace_readiness(self, plan, installed, phase):
        deadline = time.monotonic() + 40
        for unit in ('tg-ingest-agent', 'cara-fleet', 'cara-worker'):
            self.wait_stable_identity(unit, deadline)
        result = self.core.RootBackend.namespace_readiness(self, plan, installed, phase)
        require(time.monotonic() < deadline, 'original40s whole namespace readiness bound expired')
        return result

    def verify_readiness(self, expected, actual):
        return self.core.checks.facts_readiness(expected, actual)

    def verify_notice(self, value):
        return self.core.checks.acceptance_notice(value)

    def verify_loaded_environment(self, plan):
        raw = self.core.read_bytes(self.core.protected(observe.ENV))
        require(sha(raw) == plan['candidate_environment_sha256'], 'installed candidate environment changed')
        configured = parse_env_bytes(raw)
        identity = self.process_identity('tg-ingest-agent')
        loaded = dict(part.decode().split('=', 1) for part in
                      (Path('/proc') / str(identity['pid']) / 'environ').read_bytes().split(b'\0') if b'=' in part)
        require(all(loaded.get(key) == configured.get(key) for key in observe.MODEL_KEYS),
                'actual process model/price/budget configuration differs')
        require(self.process_identity('tg-ingest-agent') == identity, 'process changed during configuration check')

    def final_preservation(self, plan, baseline, gateway):
        self.check_preinstall_preservation(baseline, gateway)
        require(self.units(plan['preserved_units']) == baseline['preserved_units'], 'original speech/monitor/timer identity changed')
        require(self.core.metadata(self.core.LIFECYCLE) == baseline['lifecycle_file']
                and self.core.manifest([p for p in self.core.FACTS.rglob('*') if p.is_file() and not p.is_symlink()])
                == baseline['facts_files'], 'facts/lifecycle/history changed')
        for path, expected in self.publisher_outputs.items():
            require(self.core.metadata(path) == expected, 'root source snapshot changed')
        require(self._idle()['backup_pins'] == baseline['backup_pins'], 'backup pins changed')
        require(self.units(('whisper-server',))['whisper-server'] == baseline['units']['whisper-server'],
                'speech process identity changed')
        self._runtime_exact(plan['runtime_hashes'])
        self._mentors_disabled()
        self.preserve_closed_trial(write_receipt=False)
        self.verify_loaded_environment(plan)

    def mark_verified(self, plan, installed):
        value = self.core.read_json(self.core.APP / 'DEPLOYMENT.json')
        require(value['status'] == 'installed' and value['deployment_id'] == installed['deployment']['deployment_id'],
                'native verification already marked or changed')
        self.command(['/usr/bin/python3', '-B', str(self.core.APP / 'deployment_notice.py'), 'mark-verified',
                      '--manifest', str(self.core.APP / 'DEPLOYMENT.json'), '--verification-summary',
                      'Exact model-retirement source and environment; full native/CI/layout/operator and bounded model+semantic gates; original56 schema/history/unknown charges preserved; actual nonroot namespace/MCP/scoped reads/runtime and peer health passed; no trial restart, migration or gateway update'],
                     log='mark-verified.log')
        require(self.core.read_json(self.core.APP / 'DEPLOYMENT.json')['status'] == 'verified', 'native mark-verified failed')

    def final_restart(self):
        # Never reset the initial cutoff: a restart cannot reclassify unknown cost.
        deadline = time.monotonic() + 120
        while True:
            self.window(self.plan['identity'])
            try:
                self._idle()
                break
            except ValueError as error:
                if not str(error).startswith(('active owner work must settle',
                                              'unsettled request/cancel/purge spool residue',
                                              'current-process unknown paid inference must settle')):
                    raise
                require(time.monotonic() < deadline, 'quiescent final restart deadline; no restart issued')
                time.sleep(1)
        self.write('before-final-restart-idle', {'at': now(), 'process_cutoff': self.unknown_baseline['process_cutoff'],
                                                'active_owner_work': 0, 'pending_paid_transports': 0})
        self.command(['/usr/bin/systemctl', 'restart', 'tg-ingest-agent'], log='final-main-restart.log', timeout=120)

    def acceptance(self, plan, evidence, baseline, gateway, before, after, notice):
        self.final_preservation(plan, baseline, gateway)
        value = {'schema': 'cara.model-retirement-acceptance/v1', 'status': 'accepted', 'at': now(),
                 'identity': plan['identity'], 'manifest': self.core.read_json(self.core.APP / 'DEPLOYMENT.json'),
                 'source_evidence': evidence, 'normal_notice': notice,
                 'before_mark_verified_readiness_sha256': sha(canonical(before)),
                 'after_final_restart_readiness_sha256': sha(canonical(after)),
                 'protected_sha256': sha(canonical(self.protected_manifest())),
                 'recovery_sha256': sha(canonical(self.recovery())), 'neighbors_sha256': sha(canonical(self.neighbors())),
                 'original_environment_sha256': plan['original_environment_sha256'],
                 'candidate_environment_sha256': plan['candidate_environment_sha256'],
                 'historical_unknowns': self.unknown_baseline, 'new_unknowns': 0,
                 'original_tables': 56, 'total_tables': 56, 'schema_changed': False,
                 'stopped_parity_sha256': self.core.file_sha(self.root / 'stopped-parity.json'),
                 'canonical_migration_invocations': 0, 'database_restores': 0, 'gateway_updates_performed': 0,
                 'source_publishers_run': 0, 'new_trial_epochs': 0, 'manual_messages': 0,
                 'historical_updates_replayed': 0, 'completed_controllers_replayed': 0}
        self.write('acceptance', value)
        return value

    def forward_pause(self, plan):
        # Containment must still work if an unknown call or work caused failure.
        # Stop current processes only; never restore DB/environment/source.
        try:
            self.stop_main_executor()
        finally:
            state = self.units(('tg-ingest-agent', 'cara-fleet'))
            self.write('containment', {'at': now(), 'identity': plan['identity'], 'units': state,
                                      'current_source_preserved': True, 'old_database_restored': False})
        require(all(row['ActiveState'] == 'inactive' and row['MainPID'] == '0' for row in state.values()),
                'forward containment unverified')

    def failure(self, plan, forward_paused):
        phase_path = self.root / 'phase.json'
        phase = self.core.read_json(phase_path)['phase'] if phase_path.exists() else 'not_recorded'
        self.write('failure', {'at': now(), 'identity': plan['identity'], 'forward_paused': forward_paused,
                               'phase': phase,
                               'current_source_and_database_preserved': True, 'rollback_performed': False})

    def _forbidden(self, *args, **kwargs):
        raise ValueError('model retirement forbids historical controller/trial/gateway/publisher execution')

    trial_boundary = publish_pause_gate = _source_publish = wait_gateway_receipts = _forbidden


def backend_type(core):
    return type('ModelRetirementBackend', (NativeMethods, core.RootBackend), {'core': core})


def load_selected(root, authorization_sha256):
    root = Path(root)
    require(os.name == 'posix' and os.geteuid() == 0, 'authenticated Linux root required')
    for path in (*reversed(root.parents), root):
        info = path.lstat()
        require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
                and info.st_uid == 0 and not info.st_mode & 0o022, 'protected root ancestry required')
    raw, meta = observe.safe_file(root / 'authorization.json')
    require(meta['uid'] == 0 and not meta['mode'] & 0o022 and sha(raw) == authorization_sha256,
            'selected authorization hash/protection differs')
    plan = validate_plan(json.loads(raw))
    for name, expected in plan['package_sha256s'].items():
        data, metadata = observe.safe_file(relative_file(root, name))
        require(metadata['uid'] == 0 and not metadata['mode'] & 0o022 and sha(data) == expected,
                'selected package protection/bytes differ')
    core = load_reference(root, plan['reference_sha256s'])
    backend = backend_type(core)(root, Path(__file__), authorization_sha256)
    # Prestart reconstruction authenticates all inputs without writing receipts.
    backend.read_inputs(plan)
    return backend


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--launch', action='store_true', required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--authorization-sha256', required=True)
    args = parser.parse_args(argv)
    backend = load_selected(args.root, args.authorization_sha256)
    Controller(backend.plan, backend).run()


if __name__ == '__main__':
    main()
