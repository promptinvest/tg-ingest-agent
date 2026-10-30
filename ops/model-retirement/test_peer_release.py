import copy
import hashlib
import json
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
        current[peer.POLICY_PATH]={'sha256':peer.POLICY_AFTER,'uid':0,'gid':0,'mode':0o600}
        for number in range(121):
            current[f'/etc/fixture{number}']={'sha256':'c'*64,'uid':0,'gid':0,'mode':0o600}
        originals={path:{**current[path],'sha256':'d'*64} for path in peer.UNIT_PATHS}
        originals[peer.POLICY_PATH]={'sha256':peer.POLICY_BEFORE,'uid':0,'gid':0,'mode':0o600}
        peer_link['receipt_sha256s'].update({'acceptance.json':peer.ACTUAL_ACCEPTANCE,
            'process-result-acceptance.json':peer.ACTUAL_PROCESS,'phase-pid-acceptance.json':peer.ACTUAL_PID})
        peer_link.update(schema='cara.model-retirement-peer-link/v2',adopted_protected_paths=list(peer.PROTECTED_PATHS),
            continuation={'root':peer.ACCEPTANCE_ROOT,'acceptance_sha256':peer.ACTUAL_ACCEPTANCE,
                'process_sha256':peer.ACTUAL_PROCESS,'pid_sha256':peer.ACTUAL_PID,
                'canonical_publication_sha256':peer.PUBLICATION,'phase_launch_sha256':peer.ACTUAL_LAUNCH},
            policy_transition={'status':'passed','path':peer.POLICY_PATH,'receipt_sha256s':dict(peer.POLICY_RECEIPT_PINS),
                'changed_paths':sorted(peer.POLICY_VALUES),'unchanged_bytes_verified':True,
                'actual_acceptance_sha256':peer.ACTUAL_ACCEPTANCE,
                'before':copy.deepcopy(originals[peer.POLICY_PATH]),
                'after':copy.deepcopy(current[peer.POLICY_PATH])})
        draft={'preservation_scope_sha256':'s','preserved_units':[],
               'peer_original_units':originals,'accepted_protected_sha256':digest({**current,**originals})}
        observed={'schema':'cara.model-retirement-preservation-observation/v1',
            'scope_sha256':'s','protected':current,'matches_accepted126':False,
            'facts_match_accepted':True,'source_outputs_match_accepted':True,
            'observer_states_match_accepted':True,'observer_services_inactive':True,
            'closed_trial_files_match_accepted':True,'known_protected_differences':list(peer.PROTECTED_PATHS),
            'peer_release_linkage':peer_link,'neighbors':args[3],'recovery':{},
            'closed_trial_files':{},'units':{},'facts_manifest_sha256':'f'*64,'source_outputs':{},
            'at':'2026-10-29T20:00:00+00:00'}
        return draft,observed

    def test_exact_five_accepted_peer_files_preserve_remaining121(self):
        draft,observed=self.fixture()
        self.assertEqual(prep.bind_preservation(draft,observed)['baseline_protected'],observed['protected'])

    def test_pending_peer_cannot_be_selected(self):
        draft,observed=self.fixture();observed['peer_release_linkage']['status']='pending'
        with self.assertRaisesRegex(ValueError,'completed peer acceptance'):
            prep.bind_preservation(draft,observed)

    def test_sixth_changed_file_cannot_be_adopted(self):
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


class ContinuationTests(unittest.TestCase):
    def fixture(self):
        records,pins,*_=fixture()
        pins.update({'acceptance.json':peer.ACTUAL_ACCEPTANCE,
            'process-result-acceptance.json':peer.ACTUAL_PROCESS,
            'phase-pid-acceptance.json':peer.ACTUAL_PID})
        records['process-result-acceptance.json'].update(
            acceptance_sha256=peer.ACTUAL_ACCEPTANCE, passed=True, image=peer.IMAGE, script_sha256='s'*64)
        records['phase-pid-acceptance.json'].update(process_group_id=5678,script_sha256='l'*64)
        publication={'source':peer.SOURCE,'image':peer.IMAGE,'actual_root':peer.ACCEPTANCE_ROOT,
            'canonical_path':peer.ROOT+'/acceptance.json','acceptance_sha256':peer.ACTUAL_ACCEPTANCE,
            'actual_process_sha256':peer.ACTUAL_PROCESS,'exact_byte_publication':True,
            'controller_replayed':False,'actual_script_sha256':'s'*64}
        launch={'source':peer.SOURCE,'image':peer.IMAGE,'script_sha256':'s'*64,'launcher_sha256':'l'*64}
        return records,pins,publication,peer.PUBLICATION,launch,peer.ACTUAL_LAUNCH

    def test_actual_continuation_and_canonical_copy_bind(self):
        self.assertEqual(peer.validate_continuation(*self.fixture())['root'],peer.ACCEPTANCE_ROOT)

    def test_wrong_root_fictional_process_replay_or_script_rejects(self):
        for index,key,value in ((2,'actual_root',peer.ROOT),(2,'controller_replayed',True),
            (2,'actual_process_sha256','f'*64),(2,'actual_script_sha256','t'*64),
            (2,'exact_byte_publication',False)):
            args=list(self.fixture());args[index][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):peer.validate_continuation(*args)
        args=list(self.fixture());args[0]['phase-pid-acceptance.json']['process_group_id']=9999
        with self.assertRaisesRegex(ValueError,'process/script'):peer.validate_continuation(*args)


def policy_fixture():
    """Synthetic policy; no private host policy is embedded in tests."""
    before={'unrelated':'preserve'};after={'unrelated':'preserve'}
    for path,value in peer.POLICY_VALUES.items():
        for root,replacement in ((before,'x'*len(value)),(after,value)):
            current=root
            parts=path.strip('/').split('/')
            for part in parts[:-1]:current=current.setdefault(part,{})
            current[parts[-1]]=replacement
    def encode(root):
        spans={};out=bytearray()
        def render(value,path=''):
            if isinstance(value,dict):
                out.extend(b'{')
                for i,(key,child) in enumerate(sorted(value.items())):
                    if i:out.extend(b',')
                    out.extend(json.dumps(key).encode()+b':');render(child,path+'/'+key)
                out.extend(b'}')
            else:
                start=len(out);out.extend(json.dumps(value).encode())
                if path in peer.POLICY_VALUES:spans[path]=(start,len(out))
        render(root);out.extend(b' '*(2273-len(out)))
        return bytes(out),spans
    old,oldspans=encode(before);new,newspans=encode(after)
    oldsha=hashlib.sha256(old).hexdigest();newsha=hashlib.sha256(new).hexdigest()
    tokens=[{'path':path,'before_start':oldspans[path][0],'before_end':oldspans[path][1],
             'after_start':newspans[path][0],'after_end':newspans[path][1]} for path in peer.POLICY_VALUES]
    meta={'uid':0,'gid':0,'mode':0o600}
    process={'return_code':0,'timed_out':False,'failure_type':None,'owned_group_absent':True,
             'stdout_sha256':peer.POLICY_RECEIPT_PINS['success.json'],'pid':1308057,'pgid':1308057}
    records={'success.json':{'passed':True,'before_sha256':oldsha,'after_sha256':newsha,
        'changed_string_leaves':9,'mode_uid_gid_preserved':True,'other_peer_policy_bytes_preserved':True,
        'Cara_restarts':0,'messages':0,'replay_permitted':False,
        'byte_proof':{'before_sha256':oldsha,'after_sha256':newsha,
                      'actual_acceptance_sha256':peer.ACTUAL_ACCEPTANCE,'changed_tokens':tokens}},
        'apply.process.json':process,'COMPLETED.json':{'passed':True,'apply_process':dict(process),
            'success_sha256':peer.POLICY_RECEIPT_PINS['success.json'],
            'success_path':peer.POLICY_ROOT+'/success.json','actual_acceptance_sha256':peer.ACTUAL_ACCEPTANCE,
            'Cara_restarts':0,'messages':0,'replay_permitted':False}}
    return (old,new,dict(meta),dict(meta),records,dict(peer.POLICY_RECEIPT_PINS)),oldsha,newsha


class PolicyTransitionTests(unittest.TestCase):
    def call(self,args,oldsha,newsha):
        with mock.patch.multiple(peer,POLICY_BEFORE=oldsha,POLICY_AFTER=newsha):
            return peer.validate_policy_transition(*args)

    def test_exact_nine_identity_tokens_preserve_every_other_byte(self):
        args,oldsha,newsha=policy_fixture()
        value=self.call(args,oldsha,newsha)
        self.assertTrue(value['unchanged_bytes_verified'])
        self.assertEqual(value['changed_paths'],sorted(peer.POLICY_VALUES))

    def test_policy_ownership_change_rejects(self):
        args,oldsha,newsha=policy_fixture();args[3]['mode']=0o644
        with self.assertRaisesRegex(ValueError,'ownership/mode'):self.call(args,oldsha,newsha)

    def test_failed_process_or_wrong_completion_rejects(self):
        for key,value in (('return_code',1),('timed_out',True),('owned_group_absent',False),
                          ('pid',0),('pgid',9999),('stdout_sha256','f'*64)):
            args,oldsha,newsha=policy_fixture();args[4]['apply.process.json'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):self.call(args,oldsha,newsha)
        args,oldsha,newsha=policy_fixture();args[4]['COMPLETED.json']['actual_acceptance_sha256']='f'*64
        with self.assertRaisesRegex(ValueError,'completion linkage'):self.call(args,oldsha,newsha)

    def test_receipt_tampering_or_eleventh_hour_policy_change_rejects(self):
        args,oldsha,newsha=policy_fixture();args[5]['success.json']='f'*64
        with self.assertRaisesRegex(ValueError,'receipt pins'):self.call(args,oldsha,newsha)
        args,oldsha,newsha=policy_fixture();args=list(args);args[1]=args[1].replace(b'preserve',b'changed!')
        with self.assertRaisesRegex(ValueError,'exact before/after'):self.call(args,oldsha,newsha)

    def test_unselected_whitespace_change_rejects_even_with_recomputed_hash(self):
        args,oldsha,newsha=policy_fixture();args=list(args)
        args[1]=args[1][:-1]+b'\n';newsha=hashlib.sha256(args[1]).hexdigest()
        args[4]['success.json']['after_sha256']=newsha
        args[4]['success.json']['byte_proof']['after_sha256']=newsha
        with self.assertRaisesRegex(ValueError,'unselected peer policy suffix'):self.call(args,oldsha,newsha)

    def test_relabelled_duplicate_or_overlapping_token_rejects(self):
        for mutate in ('duplicate','offset','leaf'):
            args,oldsha,newsha=policy_fixture();tokens=args[4]['success.json']['byte_proof']['changed_tokens']
            if mutate=='duplicate':tokens[1]=dict(tokens[0])
            elif mutate=='offset':tokens[0]['before_start']+=1
            else:tokens[0]['path']='/unrelated'
            with self.subTest(mutate=mutate),self.assertRaises((ValueError,KeyError)):
                self.call(args,oldsha,newsha)

    def test_live_updater_group_blocks_even_when_pid_absent(self):
        with mock.patch.object(peer.os,'kill',side_effect=ProcessLookupError), \
             mock.patch.object(peer.os,'killpg',return_value=None,create=True):
            with self.assertRaisesRegex(ValueError,'updater PID/process group'):peer._require_gone(1308057)


if __name__=='__main__':
    unittest.main()
