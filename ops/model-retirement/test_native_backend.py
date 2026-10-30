"""Offline boundary tests; no native backend, application, SSH or provider calls."""
import copy
import ast
import contextlib
import io
import json
import os
from pathlib import Path
import secrets
import tempfile
import types
import unittest
from unittest.mock import patch

import native_backend as native
import release_operator as contract


class PureBoundaries(unittest.TestCase):
    def test_retired_mentor_projection_matches_native_writer_shape(self):
        raw = (b'# retained\nDO_MODEL_ACCESS_KEY="synthetic-only"\n'
               b'DO_CHAT_MODEL=deepseek-v4.1-flash-1017\nOTHER=private\n')
        projected = native.mentor_projections(raw, 120, 1800)
        self.assertEqual(projected['cara-mentor.env'],
                         b'DO_MODEL_ACCESS_KEY="synthetic-only"\nDO_CHAT_MODEL="deepseek-v4.1-flash-1017"\n')
        self.assertEqual(projected['cara-mentor-runner.env'], b'MENTOR_TEST_TIMEOUT_SECONDS="120"\n')
        self.assertNotIn(b'OTHER', b''.join(projected.values()))
        with self.assertRaises(ValueError):
            native.mentor_projections(raw + b'MENTOR_TEST_TIMEOUT_SECONDS=1\n', 120, 1800)

    def test_gate_envelope_must_bind_real_raw_evidence(self):
        gate = {'status': 'passed', 'source_commit': 'a' * 40, 'archive_sha256': 'b' * 64,
                'candidate_environment_sha256': 'c' * 64}
        envelope = {'schema': 'cara.model-retirement-gate/v1', 'gate': 'model_qualification',
                    **gate, 'evidence_files': {'raw.json': 'd' * 64}}
        native.validate_gate('model_qualification', gate, envelope, {'raw.json': 'd' * 64})
        for wrong in ({}, {'raw.json': 'e' * 64}):
            with self.assertRaises(ValueError):
                native.validate_gate('model_qualification', gate, envelope, wrong)
        bad = {**envelope, 'status': 'functional_passed_pending_semantic'}
        with self.assertRaises(ValueError):
            native.validate_gate('model_qualification', gate, bad, {'raw.json': 'd' * 64})

    def test_only_exact_four_runtime_files_with_approved_guard_can_change(self):
        old = {name: name + '-old' for name in ('common.py', 'llm.py', 'cache_usage.py', 'store.py')}
        old['action_truth.py'] = contract.APPROVED_GUARD_BASE
        new = {**old, **{name: name + '-new' for name in native.ALLOWED_RUNTIME_DELTA}}
        new['action_truth.py'] = contract.APPROVED_GUARD_TARGET
        self.assertEqual(native.runtime_delta(old, new), sorted(native.ALLOWED_RUNTIME_DELTA))
        for bad in ({**new, 'store.py': 'changed'}, {**new, 'extra.py': 'new'},
                    {key: val for key, val in new.items() if key != 'store.py'},
                    {**new, 'action_truth.py': old['action_truth.py']},
                    {**new, 'llm.py': old['llm.py']}):
            with self.assertRaises(ValueError):
                native.runtime_delta(old, bad)

    def test_guard_approval_does_not_authorize_other_guard_bytes(self):
        old = dict.fromkeys(contract.ALLOWED_RUNTIME_DELTA, 'before')
        old['action_truth.py'] = contract.APPROVED_GUARD_BASE
        new = dict.fromkeys(contract.ALLOWED_RUNTIME_DELTA, 'after')
        new['action_truth.py'] = contract.APPROVED_GUARD_TARGET
        for before, after in (({**old, 'action_truth.py': 'a' * 64}, new),
                              (old, {**new, 'action_truth.py': 'b' * 64})):
            with self.assertRaisesRegex(ValueError, 'exact approved'):
                native.runtime_delta(before, after)

    def test_recovery_changes_cannot_be_adopted_as_new_preflight_baseline(self):
        original = {'neighbors': {'peer': 'unchanged'}, 'preserved_units': {'timer': 'active'},
                    'recovery': {'original-backup': {'sha256': 'original'}}}
        plan = {'baseline_' + key: value for key, value in original.items()}
        native.validate_baseline_peers(plan, copy.deepcopy(original))
        for changed in ({}, {'original-backup': {'sha256': 'different'}},
                        {**original['recovery'], 'new-backup': {'sha256': 'new'}}):
            with self.assertRaisesRegex(ValueError, 'recovery'):
                native.validate_baseline_peers(plan, {**original, 'recovery': changed})

    def test_reference_loader_rejects_hash_before_import(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / 'reference').mkdir()
            (base / 'reference' / 'accepted_backend.py').write_text('raise RuntimeError("executed")')
            with self.assertRaisesRegex(ValueError, 'reference'):
                native.load_reference(base, native.FROZEN_REFERENCES)

    def test_no_default_launch(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            native.main([])

    def test_native_late_inputs_are_checked_before_start(self):
        plan = {'test_summary': 'Passed exact native checks', 'identity': {'controller_nonce': 'a' * 32},
                'carried_gateways': dict.fromkeys(('armenia', 'exit', 'pd', 'pi', 'rus', 'utah'), {}),
                'mcp': {'enabled': True, 'config_path': '/etc/cara-fleet/mcp.json',
                        'config_sha256': 'b' * 64, 'tools': ['facts']},
                'facts': {'integrity': 'c' * 64, 'revision': 2}, 'facts_obligations': [{}],
                'facts_cells_sha256': 'd' * 64, 'facts_pages_sha256': 'e' * 64,
                'spool_operations': [{'operation': 'inspect', 'hosts': ['synthetic'],
                                      'facets': ['projects'], 'inventory_hash': 'f' * 64} for _ in range(9)],
                'spool_expectations': [{} for _ in range(9)], 'publishers': {}}
        native.validate_post_transition_inputs(plan)
        for key in plan:
            with self.subTest(missing=key), self.assertRaises(ValueError):
                native.validate_post_transition_inputs({name: value for name, value in plan.items() if name != key})
        for summary in ('', '   ', None):
            with self.subTest(summary=summary), self.assertRaises(ValueError):
                native.validate_post_transition_inputs({**plan, 'test_summary': summary})
        malformed = copy.deepcopy(plan)
        del malformed['mcp']['config_path']
        with self.assertRaises(ValueError):
            native.validate_post_transition_inputs(malformed)

    def test_mentor_projection_matches_actual_unchanged_native_writers(self):
        source = Path(__file__).resolve().parents[2] / 'cara_mentor.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        names = {'_read_selected_env', '_write_selected_env', 'write_inference_env', 'write_runner_env'}
        subset = ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef)
                                 and node.name in names], type_ignores=[])
        self.assertEqual(len(subset.body), 4)
        # Native target is Linux: force binary descriptors on Windows so the
        # host C runtime cannot rewrite the writer's explicit LF bytes.
        native_os = types.SimpleNamespace(**{name: getattr(os, name) for name in dir(os)})
        native_os.open = lambda path, flags, mode: os.open(path, flags | getattr(os, 'O_BINARY', 0), mode)
        namespace = {'Path': Path, 'json': json, 'os': native_os, 'secrets': secrets,
                     'protocol': types.SimpleNamespace(DEFAULT_RUNNER_TEST_TIMEOUT_SECONDS=900,
                                                       MAX_RUNNER_TEST_TIMEOUT_SECONDS=1200)}
        exec(compile(subset, str(source), 'exec'), namespace)
        samples = [b'DO_MODEL_ACCESS_KEY=synthetic\nDO_CHAT_MODEL=old\n',
                   'DO_MODEL_ACCESS_KEY="synthetic"\r\nDO_CHAT_MODEL=deepseek-v4.1-flash-1017\r\nMENTOR_MODEL=тест\r\n'.encode(),
                   b" DO_MODEL_ACCESS_KEY='synthetic' \nMENTOR_TEST_TIMEOUT_SECONDS=1200\nDO_INFERENCE_BASE_URL=\"https://synthetic.invalid/\\\"x\"\n"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            for raw in samples:
                (path / 'main.env').write_bytes(raw)
                namespace['write_inference_env'](path / 'main.env', path / native.MENTOR_FILES[0])
                namespace['write_runner_env'](path / 'main.env', path / native.MENTOR_FILES[1])
                self.assertEqual(native.mentor_projections(raw),
                                 {name: (path / name).read_bytes() for name in native.MENTOR_FILES})


class QualificationEvidence(unittest.TestCase):
    def fixture(self):
        identity = {'source_commit': 'a' * 40, 'archive_sha256': 'b' * 64}
        plan = {'identity': identity, 'candidate_environment_sha256': 'c' * 64}
        binding = {**identity, 'candidate_environment_sha256': 'c' * 64}
        hashes = {'plan': 'd' * 64, 'inputs': 'e' * 64, 'raw': 'f' * 64}
        profiles = ('router_fast', 'ingest_balanced', 'ask_grounded', 'converse_warm',
                    'memory_curator', 'memory_consolidate', 'task_planner', 'task_synthesis')
        models = ['deepseek-v4.1-flash-1017', 'openai-gpt-oss-120b']
        prompt = [{'role': 'user', 'content': 'Synthetic only'}]
        input_sha = native.sha(native.canonical(prompt))
        originals, cases = [], []
        for profile in profiles:
            for number in range(3):
                case = {'id': profile + str(number), 'profile': profile, 'messages': prompt,
                        'input_sha256': input_sha, 'runtime_profile': {'primary': 'unchanged', 'fallbacks': models}}
                originals.append(case)
                for model in models:
                    cases.append({**case, 'id': case['id'] + '__' + model, 'model': model, 'kind': 'text'})
        for name in ('vision-describe-ru', 'vision-describe-en', 'vision-classify-ru',
                     'vision-classify-en', 'vision-extract-book', 'vision-extract-blank'):
            cases.append({'id': name, 'profile': 'vision', 'model': 'deepseek-v4.1-flash',
                          'kind': 'vision', 'input_sha256': input_sha, 'messages': prompt})
        coverage = {}
        for case in cases:
            coverage.setdefault(case['profile'], {}).setdefault(case['model'], []).append(case['id'])
        prepared = {'schema': 'cara.model-retirement-prepared/v1', **binding,
                    'qualification_inputs_sha256': hashes['inputs'], 'coverage': coverage,
                    'cases_expected': len(cases), 'aggregate_ceiling_usd': .25, 'per_call_ceiling_usd': .15}
        inputs = {'prepared': {'cases': originals}, 'cases': cases}
        usage = [{'id': index, 'model': case['model'], 'kind': 'chat', 'cost_usd': .0001}
                 for index, case in enumerate(cases)]
        raw_cases = [{**case, 'provider_model': case['model'], 'status': 'functional_passed_pending_semantic',
                      'finish_reason': 'stop', 'raw': 'synthetic', 'raw_sha256': native.sha(b'synthetic'),
                      'reservation_id': index} for index, case in enumerate(cases)]
        raw = {'schema': 'cara.model-retirement-raw/v1', 'status': 'functional_passed_pending_semantic',
               **binding, 'plan_sha256': hashes['plan'], 'qualification_inputs_sha256': hashes['inputs'],
               'coverage': coverage, 'expected_coverage': coverage, 'cases_expected': len(cases),
               'cases_completed': len(cases), 'cases': raw_cases, 'usage': usage, 'migration_usage': usage,
               'accounted_cost_usd': sum(row['cost_usd'] for row in usage),
               'migration_accounted_cost_usd': sum(row['cost_usd'] for row in usage),
               'unknown_usage_ids': [], 'migration_unknown_usage_ids': [], 'unknown_reserve_usd': 0,
               'migration_unknown_reserve_usd': 0, 'messages_sent': 0, 'live_business_data_changed': False,
               'aggregate_ceiling_usd': .25, 'per_call_ceiling_usd': .15}
        review = {'schema': 'cara.model-retirement-semantic/v1', 'status': 'passed', **binding,
                  'plan_sha256': hashes['plan'], 'inputs_sha256': hashes['inputs'], 'raw_sha256': hashes['raw'],
                  'cases': [{'id': case['id'], 'raw_sha256': case['raw_sha256'], 'status': 'passed',
                             'reason': 'Synthetic accepted response'} for case in raw_cases]}
        return plan, prepared, inputs, raw, review, hashes

    def test_complete_accounted_raw_and_independent_semantics(self):
        self.assertEqual(native.verify_qualification(*self.fixture())['cases'], 54)

    def test_rejects_missing_case_pending_review_unknown_cost_and_wrong_raw_binding(self):
        mutations = [lambda data: data[3]['cases'].pop(),
                     lambda data: data[4]['cases'].pop(),
                     lambda data: data[4]['cases'][0].update(status='pending'),
                     lambda data: data[4].update(raw_sha256='wrong'),
                     lambda data: data[3]['usage'][0].update(kind='unknown_chat'),
                     lambda data: data[3]['usage'][0].update(cost_usd=float('nan')),
                     lambda data: data[3]['usage'][0].update(cost_usd=.16),
                     lambda data: data[3]['cases'][0].update(provider_model='wrong'),
                     lambda data: data[3]['cases'][0].update(reservation_id=1)]
        for mutation in mutations:
            data = self.fixture()
            mutation(data)
            with self.subTest(mutation=mutation), self.assertRaises((ValueError, KeyError)):
                native.verify_qualification(*data)


class PeerAdoption(unittest.TestCase):
    def fixture(self):
        original = {f'/synthetic/protected/{i}': {'sha256': 'old', 'uid': 0, 'gid': 0, 'mode': 0o600}
                    for i in range(121)}
        units = {path: {'sha256': 'old-unit', 'uid': 0, 'gid': 0, 'mode': 0o644}
                 for path in native.peer_release.UNIT_PATHS}
        original.update(units)
        originals = {**units, native.peer_release.POLICY_PATH: {
            'sha256': native.peer_release.POLICY_BEFORE, 'uid': 0, 'gid': 0, 'mode': 0o600}}
        original.update(originals)
        protected = {**original, **{path: {**value, 'sha256': 'new-unit'} for path, value in units.items()}}
        protected[native.peer_release.POLICY_PATH] = {
            **originals[native.peer_release.POLICY_PATH], 'sha256': native.peer_release.POLICY_AFTER}
        neighbors = {'synthetic-peer': 'bound'}
        proof = {'schema': 'cara.model-retirement-peer-link/v2', 'status': 'passed',
                 'root': native.peer_release.ROOT, 'source': native.peer_release.SOURCE,
                 'image': native.peer_release.IMAGE, 'verification_receipt_sha256': native.peer_release.VERIFICATION,
                 'receipt_sha256s': {name: name + '-raw-hash' for name in native.peer_release.RECEIPTS},
                 'active_release_controllers': 0, 'neighbors': neighbors,
                 'unit_source_sha256s': {path: 'new-unit' for path in units},
                 'adopted_protected_paths': list(native.peer_release.PROTECTED_PATHS),
                 'continuation': {
                     'root': native.peer_release.ACCEPTANCE_ROOT,
                     'acceptance_sha256': native.peer_release.ACTUAL_ACCEPTANCE,
                     'process_sha256': native.peer_release.ACTUAL_PROCESS,
                     'pid_sha256': native.peer_release.ACTUAL_PID,
                     'canonical_publication_sha256': native.peer_release.PUBLICATION,
                     'phase_launch_sha256': native.peer_release.ACTUAL_LAUNCH},
                 'policy_transition': {
                     'status': 'passed', 'path': native.peer_release.POLICY_PATH,
                     'receipt_sha256s': native.peer_release.POLICY_RECEIPT_PINS,
                     'changed_paths': sorted(native.peer_release.POLICY_VALUES),
                     'unchanged_bytes_verified': True,
                     'actual_acceptance_sha256': native.peer_release.ACTUAL_ACCEPTANCE,
                     'before': originals[native.peer_release.POLICY_PATH],
                     'after': protected[native.peer_release.POLICY_PATH]}}
        proof['receipt_sha256s'].update({
            'acceptance.json': native.peer_release.ACTUAL_ACCEPTANCE,
            'process-result-acceptance.json': native.peer_release.ACTUAL_PROCESS,
            'phase-pid-acceptance.json': native.peer_release.ACTUAL_PID})
        pin = native.sha(native.canonical(original))
        plan = {'baseline_protected': protected, 'baseline_neighbors': neighbors,
                'peer_original_units': originals, 'accepted_protected_sha256': pin,
                'peer_release_linkage': proof}
        return plan, copy.deepcopy(proof), pin

    def test_exact_five_file_adoption_reconstructs_entire_accepted_map(self):
        plan, proof, pin = self.fixture()
        with patch.object(native, 'ACCEPTED_PROTECTED_SHA256', pin):
            native.verify_peer_selection(plan, proof)

    def test_refuses_new_receipt_unrelated_file_or_monitor_mode_change(self):
        for kind in ('receipt', 'fifth-file', 'mode', 'pending', 'other-peer'):
            plan, proof, pin = self.fixture()
            if kind == 'receipt':
                proof['receipt_sha256s'][native.peer_release.RECEIPTS[0]] = 'new-acceptance'
            elif kind == 'fifth-file':
                plan['baseline_protected']['/synthetic/protected/0']['sha256'] = 'unrelated-change'
            elif kind == 'mode':
                plan['baseline_protected'][native.peer_release.UNIT_PATHS[0]]['mode'] = 0o640
            elif kind == 'pending':
                proof['status'] = 'pending'
                plan['peer_release_linkage'] = copy.deepcopy(proof)
            else:
                proof['neighbors'] = {'different-peer': 'bound'}
            with self.subTest(kind=kind), patch.object(native, 'ACCEPTED_PROTECTED_SHA256', pin):
                with self.assertRaises(ValueError):
                    native.verify_peer_selection(plan, proof)

    def test_unexplained_protection_changes_cannot_be_adopted_without_peer_proof(self):
        plan, _, pin = self.fixture()
        plan['peer_release_linkage'] = None
        with patch.object(native, 'ACCEPTED_PROTECTED_SHA256', pin), self.assertRaises(ValueError):
            native.verify_peer_selection(plan, None)


class NativeBoundary(unittest.TestCase):
    def backend(self):
        # Deliberately no inherited operations: unexpected legacy calls fail.
        core = types.SimpleNamespace(RootBackend=type('FrozenRoot', (), {}))
        cls = native.backend_type(core)
        result = object.__new__(cls)
        result.core = core
        result.plan = {'identity': {'controller_nonce': 'synthetic'}, 'baseline_protected': {}}
        result.unknown_baseline = {'process_cutoff': '2026-10-29T10:31:46+00:00',
                                   'count': 35, 'sha256': 'a', 'reserved_usd': .02215046,
                                   'projection_sha256': 'b', 'current_process_count': 0,
                                   'accepted_history': {'count': native.observe.ACCEPTED_UNKNOWN_COUNT,
                                       'sha256': native.observe.ACCEPTED_UNKNOWN_SHA256, 'cache_nonnull': 0,
                                       'receipt_sha256': native.observe.ACCEPTED_UNKNOWN_RECEIPT}}
        result.main_process_started_at = result.unknown_baseline['process_cutoff']
        result.approved_changes = {}
        return result

    def test_forward_containment_does_not_require_unknown_settlement_or_restore(self):
        backend = self.backend()
        calls = []
        backend.stop_main_executor = lambda: calls.append('stop')
        backend.units = lambda names: {name: {'ActiveState': 'inactive', 'MainPID': '0'} for name in names}
        backend.write = lambda name, value: calls.append((name, value))
        backend.forward_pause(backend.plan)
        self.assertEqual(calls[0], 'stop')
        self.assertFalse(calls[1][1]['old_database_restored'])
        self.assertTrue(calls[1][1]['current_source_preserved'])

    def test_failure_before_first_phase_retains_started_and_records_failure(self):
        backend = self.backend()
        with tempfile.TemporaryDirectory() as directory:
            backend.root = Path(directory)
            marker = backend.root / 'STARTED'
            marker.write_bytes(b'original-single-use-marker')
            backend.core.read_json = lambda path: self.fail('missing phase must not be read')
            writes = []
            backend.write = lambda name, value: writes.append((name, value))
            backend.failure(backend.plan, forward_paused=False)
            self.assertEqual(writes[0][1]['phase'], 'not_recorded')
            self.assertFalse(writes[0][1]['forward_paused'])
            self.assertEqual(marker.read_bytes(), b'original-single-use-marker')
            with self.assertRaisesRegex(ValueError, 'already started'):
                backend.reject_started()

    def test_bind_unknown_baseline_keeps_initial_cutoff_and_rejects_later_unknowns(self):
        backend = self.backend()
        baseline = copy.deepcopy(backend.unknown_baseline)
        backend.bind_unknown_baseline(baseline)
        self.assertEqual(backend.main_process_started_at, baseline['process_cutoff'])
        baseline['current_process_count'] = 1
        with self.assertRaises(ValueError):
            backend.bind_unknown_baseline(baseline)

    def test_prohibited_legacy_operations_always_fail(self):
        backend = self.backend()
        for name in ('trial_boundary', 'publish_pause_gate', '_source_publish', 'wait_gateway_receipts'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                getattr(backend, name)()

    def test_final_restart_retains_initial_unknown_cutoff(self):
        backend = self.backend()
        backend.window = lambda identity: None
        calls = []
        backend._idle = lambda: calls.append(('idle', backend.main_process_started_at))
        backend.write = lambda name, value: calls.append((name, value))
        backend.command = lambda argv, **options: calls.append(argv)
        backend.final_restart()
        self.assertEqual(calls[0], ('idle', '2026-10-29T10:31:46+00:00'))
        self.assertEqual(backend.main_process_started_at, backend.unknown_baseline['process_cutoff'])
        self.assertEqual(calls[-1], ['/usr/bin/systemctl', 'restart', 'tg-ingest-agent'])

    def test_retired_units_use_actual_six_property_native_projection(self):
        backend = self.backend()
        row = {'LoadState': 'loaded', 'ActiveState': 'inactive', 'MainPID': '0',
               'NRestarts': '0', 'InvocationID': '', 'ExecMainStartTimestampMonotonic': '0'}
        backend.units = lambda names: {name: dict(row) for name in names}
        calls = []
        backend.command = lambda argv: calls.append(argv) or 'disabled\n'
        backend._mentors_disabled()
        self.assertEqual(calls, [['/usr/bin/systemctl', 'show', unit, '-p', 'UnitFileState', '--value']
                                 for unit in ('cara-mentor', 'cara-mentor-runner')])
        backend.command = lambda argv: 'enabled\n'
        with self.assertRaisesRegex(ValueError, 'disabled'):
            backend._mentors_disabled()

    def test_closed_trial_uses_collector_state_including_enablement(self):
        backend = self.backend()
        states = {'synthetic-observer.timer': {'LoadState': 'loaded', 'ActiveState': 'inactive',
                   'SubState': 'dead', 'UnitFileState': 'disabled', 'InvocationID': ''}}
        backend.plan.update(closed_trial_files={}, closed_trial_units=states)
        backend.units = lambda names: self.fail('closed-trial state must retain collector enablement properties')
        with patch.object(native.observe, 'unit_state', side_effect=lambda name: states[name]):
            backend.preserve_closed_trial(write_receipt=False)
        with patch.object(native.observe, 'unit_state', return_value={**next(iter(states.values())),
                                                                      'UnitFileState': 'enabled'}):
            with self.assertRaisesRegex(ValueError, 'closed trial'):
                backend.preserve_closed_trial(write_receipt=False)

    def test_prestart_database_drift_fails_before_mentor_restore_or_restart(self):
        backend = self.backend()
        with tempfile.TemporaryDirectory() as directory:
            backend.root = Path(directory)
            (backend.root / 'STARTED').write_bytes(b'synthetic')
            backend.plan.update(runtime_hashes={}, carried_gateways={}, module_sha256s={})
            backend.authorization_sha256 = 'auth'
            original = {'tables': {str(i): [] for i in range(56)}, 'ddl': []}
            files = {'installer-guard-plan.json': {'authorization_sha256': 'auth',
                       'stopped_original_sha256': 'sha', 'baseline_sha256': 'sha',
                       'environment_transition_sha256': 'sha'},
                     'preflight.json': {}, 'stopped-original.json': original,
                     'environment-transition.json': {'metadata': {}}}
            backend.core.read_json = lambda path: files[Path(path).name]
            backend.core.file_sha = lambda path: 'sha'
            backend.core.read_db = lambda: types.SimpleNamespace(close=lambda: None)
            backend.core.database_snapshot = lambda conn: {'drift': []}
            backend.core.database_ddl = lambda conn: []
            backend.window = lambda identity: None
            backend.assert_stopped = lambda: None
            backend._runtime_exact = lambda hashes: None
            backend._restore_retired_mentor = lambda: self.fail('must reject before Mentor write')
            backend.command = lambda *args, **kwargs: self.fail('must reject before native command')
            with self.assertRaisesRegex(ValueError, 'prestart56'):
                backend.installer_prestart('before-worker-start')


if __name__ == '__main__':
    unittest.main()
