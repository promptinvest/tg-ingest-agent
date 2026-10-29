import copy
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

import collect_preservation as collect
import prepare_selection as prep


class PreservationTests(unittest.TestCase):
    def scope(self):
        entry={'sha256':'a'*64,'uid':0,'gid':0,'mode':0o600}
        protected={f'/etc/fixture-{index}':copy.deepcopy(entry) for index in range(125)}
        protected['/opt/cara-fixture']=copy.deepcopy(entry)
        return {'schema':'cara.model-retirement-preservation-scope/v1',
                'protected_expected':protected,'accepted_protected_sha256':collect.digest(protected),
                'preserved_units':['whisper-server'],'observer_units':['observer.timer'],
                'observer_expected':{'observer.timer':{'active':False,'enabled':True,'unit_sha256':'a'*64}},
                'source_outputs':{},'facts_manifest_sha256':collect.digest({}),
                'closed_trial_files':{'/root/fixture-final.json':'a'*64}}

    def run_collector(self, scope, *, change=None):
        entry={'sha256':'a'*64,'uid':0,'gid':0,'mode':0o600}
        def read(path, **kwargs):
            metadata=copy.deepcopy(entry)
            if str(path)==change:metadata['sha256']='b'*64
            return b'',metadata
        response=io.BytesIO(b'{"status":"ok","database":"ok"}');response.status=200
        process=SimpleNamespace(returncode=0,stdout='"id"\t"image"\t"date"\t"running"\t42\t"healthy"\n')
        with mock.patch.object(collect,'safe_file',side_effect=read), \
             mock.patch.object(Path,'rglob',return_value=[]), \
             mock.patch.object(collect,'unit_state',return_value={'ActiveState':'inactive','UnitFileState':'enabled'}), \
             mock.patch.object(collect.subprocess,'run',return_value=process), \
             mock.patch.object(collect.urllib.request,'urlopen',return_value=response):
            return collect.collect(scope)

    def test_fresh_metadata_shape_matches_accepted_manifest_representation(self):
        value=self.run_collector(self.scope())
        self.assertTrue(value['matches_accepted126'])
        self.assertTrue(value['facts_match_accepted'])
        self.assertTrue(value['observer_states_match_accepted'])
        self.assertTrue(value['closed_trial_files_match_accepted'])
        self.assertEqual(value['neighbor_health'],{'http_status':200,'database':'ok'})
        self.assertEqual(value['service_actions'],0)
        self.assertIsNone(value['deployment_authority'])

    def test_changed_protected_file_is_reported_not_blessed(self):
        value=self.run_collector(self.scope(),change='/opt/cara-fixture')
        self.assertFalse(value['matches_accepted126'])
        self.assertEqual(value['known_protected_differences'],['/opt/cara-fixture'])

    def test_scope_cannot_select_unrelated_private_path(self):
        scope=self.scope();scope['protected_expected']['/home/unrelated']=scope['protected_expected'].pop('/opt/cara-fixture')
        with self.assertRaisesRegex(ValueError,'bounded protected'):
            self.run_collector(scope)

    def test_incomplete_native_verification_summary_blocks_selection_immediately(self):
        for value in (None, '', 'x'*2001):
            with self.subTest(value_type=type(value).__name__), \
                 self.assertRaisesRegex(ValueError,'native verification summary required'):
                prep.selected_plan({'status':'prepared_not_deployable'}, {}, {},
                                   {'test_summary':value}, '/unused')


if __name__=='__main__':
    unittest.main()
