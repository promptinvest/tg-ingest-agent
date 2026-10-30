import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3
import unittest
from unittest import mock

import collect_preflight as collector
import release_operator as release


class EnvironmentTests(unittest.TestCase):
    def test_only_changed_assignments_rewrite_and_primaries_survive(self):
        raw = (b'# preserved\r\nSECRET=private-example\r\nDO_CHAT_MODEL=deepseek-4-pro\r\n'
               b'LLM_PROFILES_JSON=\'{"router_fast":{"primary":"primary","fallbacks":["deepseek-4-flash"]}}\'\r\n'
               b'VISION_MODEL=llama-4-maverick\r\nPRICING_JSON=\'{"custom":[2,3]}\'\r\n')
        after, proof = release.transform_env_bytes(raw)
        self.assertTrue(after.startswith(b'# preserved\r\nSECRET=private-example\r\nDO_CHAT_MODEL=deepseek-4-pro\r\n'))
        parsed = release.parse_env_bytes(after)
        self.assertEqual(json.loads(parsed['LLM_PROFILES_JSON'])['router_fast']['primary'], 'primary')
        self.assertEqual(parsed['VISION_MODEL'], 'nemotron-nano-12b-v2-vl')
        self.assertEqual(proof['before_bytes_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertNotIn('private-example', json.dumps(proof))

    def test_duplicate_native_key_rejected_before_transform(self):
        with self.assertRaises(ValueError):
            release.transform_env_bytes(b'DO_CHAT_MODEL=a\nDO_CHAT_MODEL=b\n')

    def test_atomic_replacement_refuses_changed_compare_and_swap(self):
        with mock.patch.object(release, 'file_metadata', return_value={'sha256': 'different'}):
            with self.assertRaisesRegex(ValueError, 'environment identity'):
                release.replace_environment('/unused', b'new', {'sha256': 'old'})


class UnknownTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.execute('CREATE TABLE llm_usage(id INTEGER,ts TEXT,day TEXT,month TEXT,skill TEXT,kind TEXT,model TEXT,tokens_in INTEGER,tokens_out INTEGER,seconds REAL,cost_usd REAL,trace_id TEXT,cache_usage_json TEXT)')
        self.conn.execute("INSERT INTO llm_usage VALUES(1,'2026-10-28T12:00:00+00:00','2026-10-28','2026-10','healthcheck','unknown_chat','kimi-k2.6',0,0,NULL,0.001,NULL,NULL)")
        fixture=collector.unknown_observation(self.conn,'2026-10-29T10:32:00+00:00')['accepted_history']
        for key,value in (('ACCEPTED_UNKNOWN_COUNT',1),('ACCEPTED_UNKNOWN_SHA256',fixture['sha256'])):
            patch=mock.patch.object(collector,key,value);patch.start();self.addCleanup(patch.stop)
        self.addCleanup(self.conn.close)

    def test_historical_unknown_is_preserved_without_exemption(self):
        prior = collector.unknown_observation(self.conn, '2026-10-29T10:32:00+00:00')
        proof = release.verify_unknowns(prior, prior)
        self.assertEqual(proof['historical_count'], 1)
        self.assertEqual(proof['reserved_usd'], .001)
        self.assertFalse(proof['exception_applied'])
        self.assertEqual(self.conn.execute('SELECT cost_usd FROM llm_usage').fetchone(), (.001,))

    def test_current_unknown_blocks_even_when_named_healthcheck(self):
        self.conn.execute("INSERT INTO llm_usage VALUES(2,'2026-10-29T12:00:00+00:00','2026-10-29','2026-10','healthcheck','unknown_chat','kimi-k2.6',0,0,NULL,0.001,NULL,NULL)")
        current = collector.unknown_observation(self.conn, '2026-10-29T10:32:00+00:00')
        with self.assertRaisesRegex(ValueError, 'current-process unknown'):
            release.verify_unknowns(current, current)

    def test_changed_historical_charge_or_new_old_dated_row_blocks(self):
        prior = collector.unknown_observation(self.conn, '2026-10-29T10:32:00+00:00')
        self.conn.execute('UPDATE llm_usage SET cost_usd=0')
        with self.assertRaisesRegex(ValueError, 'historical unknown'):
            release.verify_unknowns(prior, collector.unknown_observation(self.conn, prior['process_cutoff']))

    def test_identical_fresh_hashes_cannot_rebaseline_altered_history(self):
        self.conn.execute('UPDATE llm_usage SET cost_usd=0')
        changed=collector.unknown_observation(self.conn,'2026-10-29T10:32:00+00:00')
        with self.assertRaisesRegex(ValueError,'match accepted35'):
            release.verify_unknowns(changed,changed)

    def test_historical_cache_backfill_is_rejected(self):
        self.conn.execute("UPDATE llm_usage SET cache_usage_json='{}'")
        changed=collector.unknown_observation(self.conn,'2026-10-29T10:32:00+00:00')
        with self.assertRaisesRegex(ValueError,'nullable cache'):
            release.verify_unknowns(changed,changed)


class PreflightTests(unittest.TestCase):
    def observation(self):
        now = datetime.now(timezone.utc)
        return {'at': now.isoformat(), 'identity': {'source_revision': 'a'*40, 'status':'verified'},
                'environment': {'sha256': 'b'*64}, 'loaded_configuration_matches': True,
                'database': {'quick_check_ok':True,'foreign_key_violations':0,'tables':56,
                             'work_counts': {'active_tasks': 0}, 'unknowns': {
                                 'count':35,'sha256':'c'*64,'reserved_usd':.01,'current_process_count':0,
                                 'current_process_sha256':collector.digest([]),'projection_sha256':'d'*64,
                                 'accepted_history':{'count':35,'sha256':collector.ACCEPTED_UNKNOWN_SHA256,
                                                     'cache_nonnull':0,'receipt_sha256':collector.ACCEPTED_UNKNOWN_RECEIPT},
                                 'process_cutoff':(now-timedelta(hours=1)).isoformat()}},
                'units': {'tg-ingest-agent': {'MainPID':'42','ExecMainStartTimestamp':'Thu 2026-10-29 10:32:00 UTC'}}}

    def test_stale_or_busy_preflight_is_rejected(self):
        value=self.observation()
        release.validate_observation(value, 'a'*40, 'b'*64)
        value['at']=(datetime.now(timezone.utc)-timedelta(seconds=901)).isoformat()
        with self.assertRaisesRegex(ValueError,'fresh'):
            release.validate_observation(value, 'a'*40, 'b'*64)
        value=self.observation();value['database']['work_counts']['active_tasks']=1
        with self.assertRaisesRegex(ValueError,'work'):
            release.validate_observation(value, 'a'*40, 'b'*64)


def selected_plan():
    now = datetime.now(timezone.utc)
    identity = {'source_commit':'e'*40,'archive_sha256':'f'*64,'controller_sha256':'1'*64,
                'operator_package_sha256':'2'*64,'window_start':now.isoformat(),
                'window_end':(now+timedelta(minutes=55)).isoformat()}
    gates = {name:{'status':'passed','source_commit':identity['source_commit'],
                   'archive_sha256':identity['archive_sha256'],'receipt_sha256':'3'*64,
                   'candidate_environment_sha256':'4'*64} for name in release.REQUIRED_GATES}
    return {'schema':'cara.model-retirement-release/v1','status':'qualified_ready_to_deploy',
            'identity':identity,'gates':gates,'schema_change':False,'database_restores':0,
            'new_trial_epochs':0,'candidate_environment_sha256':'4'*64,
            'accepted_source_commit':'a'*40,'original_environment_sha256':'b'*64,
            'module_sha256s':{'agent.py':'5'*64}}


class FakeBackend:
    def __init__(self, fail=None, containment_fails=False):
        self.calls=[];self.fail=fail;self.containment_fails=containment_fails
        self.observation=PreflightTests().observation()

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append(name)
            if name == self.fail or (name=='forward_pause' and self.containment_fails):
                raise ValueError('injected '+name)
            if name=='current_observation':return self.observation
            if name=='install_exact':return {'module_sha256s':{'agent.py':'5'*64}}
            if name=='namespace_readiness':
                phase=args[-1]
                proof={'service':{'pid':1 if phase=='before_mark_verified' else 2,'process_start_ticks':30}}
                return proof,proof
            return {}
        return call


class ControllerTests(unittest.TestCase):
    def test_old_exception_or_incomplete_gate_is_not_accepted(self):
        plan=selected_plan();plan['approved_unknown_exception']={'old':'approved'}
        with self.assertRaisesRegex(ValueError,'historical exception'):
            release.Controller(plan,FakeBackend())
        plan=selected_plan();del plan['gates']['semantic_review']
        with self.assertRaisesRegex(ValueError,'all unchanged'):
            release.Controller(plan,FakeBackend())

    def test_busy_preflight_cannot_reach_backup_stop_or_environment_change(self):
        backend=FakeBackend();backend.observation['database']['work_counts']['active_tasks']=1
        with self.assertRaisesRegex(ValueError,'owner work'):
            release.Controller(selected_plan(),backend).run()
        for method in ('backup_online','stop_main_executor','replace_model_environment','forward_pause'):
            self.assertNotIn(method,backend.calls)

    def test_environment_failure_requires_forward_containment_never_restore(self):
        backend=FakeBackend('replace_model_environment')
        with self.assertRaisesRegex(ValueError,'injected'):
            release.Controller(selected_plan(),backend).run()
        self.assertEqual(backend.calls.count('forward_pause'),1)
        self.assertNotIn('install_exact',backend.calls)
        self.assertNotIn('acceptance',backend.calls)

    def test_failed_containment_is_not_reported_as_a_successful_pause(self):
        backend=FakeBackend('install_exact',containment_fails=True)
        with self.assertRaisesRegex(RuntimeError,'containment unverified'):
            release.Controller(selected_plan(),backend).run()
        self.assertNotIn('acceptance',backend.calls)

    def test_all_existing_runtime_readiness_and_notice_boundaries_remain(self):
        backend=FakeBackend();release.Controller(selected_plan(),backend).run()
        self.assertEqual(backend.calls.count('namespace_readiness'),2)
        self.assertEqual(backend.calls.count('qualify_selected_mcp_fixture'),2)
        self.assertEqual(backend.calls.count('verify_loaded_environment'),2)
        self.assertEqual(backend.calls.count('notice'),1)
        self.assertEqual(backend.calls.count('mark_verified'),1)
        self.assertLess(backend.calls.index('backup_stopped'),backend.calls.index('replace_model_environment'))
        self.assertLess(backend.calls.index('replace_model_environment'),backend.calls.index('install_exact'))
        self.assertEqual(backend.calls[-1],'acceptance')


if __name__ == '__main__':
    unittest.main()
