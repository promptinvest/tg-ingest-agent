import copy
import unittest
from unittest import mock

import peer_release as peer
import prepare_selection as prep
from collect_preflight import digest


def fixture():
    pins={name:'a'*64 for name in peer.RECEIPTS}
    records={
        'acceptance.json':{'all_checks_passed':True,'health':'healthy','source':peer.SOURCE,
            'image':peer.IMAGE,'verification_receipt_sha256':peer.VERIFICATION,'pg_id':'pg',
            'env_sha256':'e'*64,'at':'2026-10-29T20:00:00+00:00'},
        'process-result-acceptance.json':{'source':peer.SOURCE,'return_code':0},
        'process-result-deployment.json':{'source':peer.SOURCE,'return_code':0},
        'phase-pid-deployment.json':{'source':peer.SOURCE,'pid':1234},
        'phase-pid-acceptance.json':{'source':peer.SOURCE,'pid':5678},
        'step5-receipt.json':{'source':peer.SOURCE,'image':peer.IMAGE,
            'verification_receipt_sha256':peer.VERIFICATION,'all_checks_passed':True,'pg_id':'pg'},
        'watchdogs-armed.json':{'image':peer.IMAGE,'step5_receipt_sha256':'a'*64,
            'all_timers_unchanged':True,'supply_history_preserved':True}}
    neighbors={'daily-lingo-app-1':{'image':peer.IMAGE,'status':'running','health':'healthy'},
               'daily-lingo-postgres-1':{'id':'pg','status':'running','health':'healthy'}}
    entry={'sha256':'b'*64,'uid':0,'gid':0,'mode':0o644}
    protected={path:dict(entry) for path in peer.UNIT_PATHS}
    source_units={path:'b'*64 for path in peer.UNIT_PATHS}
    return records,pins,protected,neighbors,'e'*64,source_units


class PeerAcceptanceTests(unittest.TestCase):
    def test_completed_exact_peer_chain_passes(self):
        result=peer.validate_receipts(*fixture())
        self.assertEqual(result['status'],'passed')
        self.assertEqual(set(result['unit_source_sha256s']),set(peer.UNIT_PATHS))

    def test_deployed_peer_without_actual_acceptance_rejects(self):
        args=list(fixture());args[0]['acceptance.json'].pop('all_checks_passed')
        with self.assertRaisesRegex(ValueError,'actual exact peer acceptance'):
            peer.validate_receipts(*args)

    def test_failed_or_nonterminal_process_rejects(self):
        args=list(fixture());args[0]['process-result-acceptance.json']['return_code']=None
        with self.assertRaisesRegex(ValueError,'finish successfully'):
            peer.validate_receipts(*args)

    def test_unit_must_equal_accepted_immutable_source(self):
        args=list(fixture());args[5][peer.UNIT_PATHS[0]]='c'*64
        with self.assertRaisesRegex(ValueError,'immutable accepted release units'):
            peer.validate_receipts(*args)

    def test_current_environment_must_match_acceptance(self):
        args=list(fixture());args[4]='d'*64
        with self.assertRaisesRegex(ValueError,'current process/environment'):
            peer.validate_receipts(*args)

    def test_recorded_child_and_group_must_both_be_absent(self):
        records=fixture()[0]
        with mock.patch.object(peer.os,'kill',side_effect=ProcessLookupError), \
             mock.patch.object(peer.os,'killpg',return_value=None,create=True):
            with self.assertRaisesRegex(ValueError,'process group remains active'):
                peer.require_absent_processes(records)
        with mock.patch.object(peer.os,'kill',side_effect=ProcessLookupError), \
             mock.patch.object(peer.os,'killpg',side_effect=ProcessLookupError,create=True):
            peer.require_absent_processes(records)


class FixedPreservationAdoptionTests(unittest.TestCase):
    def fixture(self):
        args=fixture();peer_link=peer.validate_receipts(*args)
        current=copy.deepcopy(args[2])
        for number in range(122):
            current[f'/etc/fixture{number}']={'sha256':'c'*64,'uid':0,'gid':0,'mode':0o600}
        originals={path:{**current[path],'sha256':'d'*64} for path in peer.UNIT_PATHS}
        draft={'preservation_scope_sha256':'s','preserved_units':[],
               'peer_original_units':originals,'accepted_protected_sha256':digest({**current,**originals})}
        observed={'schema':'cara.model-retirement-preservation-observation/v1',
            'scope_sha256':'s','protected':current,'matches_accepted126':False,
            'facts_match_accepted':True,'source_outputs_match_accepted':True,
            'observer_states_match_accepted':True,'observer_services_inactive':True,
            'closed_trial_files_match_accepted':True,'known_protected_differences':list(peer.UNIT_PATHS),
            'peer_release_linkage':peer_link,'neighbors':args[3],'recovery':{},
            'closed_trial_files':{},'units':{},'facts_manifest_sha256':'f'*64,'source_outputs':{},
            'at':'2026-10-29T20:00:00+00:00'}
        return draft,observed

    def test_exact_four_accepted_peer_files_preserve_remaining122(self):
        draft,observed=self.fixture()
        self.assertEqual(prep.bind_preservation(draft,observed)['baseline_protected'],observed['protected'])

    def test_pending_peer_cannot_be_selected(self):
        draft,observed=self.fixture();observed['peer_release_linkage']['status']='pending'
        with self.assertRaisesRegex(ValueError,'completed peer acceptance'):
            prep.bind_preservation(draft,observed)

    def test_fifth_changed_file_cannot_be_adopted(self):
        draft,observed=self.fixture();observed['known_protected_differences'].append('/etc/fixture1')
        with self.assertRaisesRegex(ValueError,'completed peer acceptance'):
            prep.bind_preservation(draft,observed)

    def test_other_unreported_change_breaks_original_aggregate(self):
        draft,observed=self.fixture();observed['protected']['/etc/fixture1']['sha256']='e'*64
        with self.assertRaisesRegex(ValueError,'remaining accepted126'):
            prep.bind_preservation(draft,observed)

    def test_peer_mode_change_cannot_be_adopted(self):
        draft,observed=self.fixture();observed['protected'][peer.UNIT_PATHS[0]]['mode']=0o666
        with self.assertRaisesRegex(ValueError,'ownership/mode'):
            prep.bind_preservation(draft,observed)


if __name__=='__main__':
    unittest.main()
