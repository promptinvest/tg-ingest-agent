import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace

import collect_preflight as collector


class CollectorTests(unittest.TestCase):
    def test_actual_current_schema_queries_work_and_do_not_modify_database(self):
        source = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(source))
        import store
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'synthetic.db'
            connection = store.open_db(path)
            connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            connection.close()
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            observed = collector.database_observation(path, '2026-10-29T10:32:00+00:00')
            self.assertEqual(observed['tables'], 56)
            self.assertTrue(observed['quick_check_ok'])
            self.assertEqual(observed['foreign_key_violations'], 0)
            self.assertTrue(all(count == 0 for count in observed['work_counts'].values()))
            self.assertEqual(observed['unknowns']['count'], 0)
            self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_router_binding_and_native_export_quotes(self):
        self.assertIn('ROUTER_MODEL', collector.MODEL_KEYS)
        self.assertEqual(collector.parse_env(b'export ROUTER_MODEL="selected"\nSECRET=unchanged\n'),
                         {'ROUTER_MODEL':'selected','SECRET':'unchanged'})

    def test_systemd_timestamp_parsing_does_not_use_host_timezone(self):
        self.assertEqual(collector.started_utc('Thu 2026-10-29 10:32:00 UTC'),
                         '2026-10-29T10:32:00+00:00')

    def test_timer_has_strict_timer_properties_without_service_process_fields(self):
        values={key:'inactive' if key=='ActiveState' else 'fixture' for key in collector.TIMER_PROPERTIES}
        result=SimpleNamespace(returncode=0,stdout='\n'.join(key+'='+value for key,value in values.items()))
        with mock.patch.object(collector.subprocess,'run',return_value=result) as run:
            self.assertEqual(collector.unit_state('observer.timer'),values)
            self.assertNotIn('MainPID',run.call_args.args[0])
            self.assertNotIn('NRestarts',run.call_args.args[0])

    def test_main_service_still_requires_all_process_identity_properties(self):
        result=SimpleNamespace(returncode=0,stdout='LoadState=loaded\nActiveState=active\n')
        with mock.patch.object(collector.subprocess,'run',return_value=result):
            with self.assertRaisesRegex(ValueError,'incomplete service identity: tg-ingest-agent'):
                collector.unit_state('tg-ingest-agent')


if __name__ == '__main__':
    unittest.main()
