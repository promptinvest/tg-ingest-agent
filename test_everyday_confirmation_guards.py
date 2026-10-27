"""Everyday confirmation outcomes under replacement, source edits and target loss."""
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import store
import llm
import media_svc
import tg_ingest_agent as entry
from testlib import make_config


class EverydayConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='everyday-confirmation-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.agent = entry.Agent(make_config(ALLOWED_CHAT_IDS='1', BOT_LANGUAGE='en',
                                            DB_PATH=str(root/'db'), MEDIA_DIR=str(root/'media')))
        self.addCleanup(self.agent.conn.close)
        self.calls=[]; self.sequence=100
        self.patch=mock.patch.object(entry,'tg_call',side_effect=self.send);self.patch.start();self.addCleanup(self.patch.stop)
        self.index=mock.patch.object(self.agent,'index_message');self.index.start();self.addCleanup(self.index.stop)
        self.typing=mock.patch.object(self.agent,'send_chat_action');self.typing.start();self.addCleanup(self.typing.stop)

    @property
    def conn(self): return self.agent.conn

    def send(self, token, method, params=None, **kwargs):
        self.assertFalse(self.conn.in_transaction, 'transport follows durable local commit')
        self.calls.append((method,dict(params or {})));self.sequence+=1
        return {'message_id':self.sequence,'text':(params or {}).get('text','')}

    def card(self,title):
        self.agent._stage_media_card(1,'en',[{'kind':'book','title':title,'aliases':[]}])
        return self.agent._media_stash(1)['card_message_id']

    def saved(self,number=10,summary='Original title'):
        rid=store.insert_message(self.conn,{'chat_id':1,'tg_message_id':number,'received_at':store._now(),'raw_text':'Saved source text'})
        category=store.ensure_category(self.conn,'Books');store.set_suggestion(self.conn,rid,category,summary,'synthetic');store.confirm_category(self.conn,rid,category)
        return rid

    def moved(self):
        rid=store.reminder_add(self.conn,1,'Budget review','2099-01-01T09:00:00+00:00')
        store.reminder_update_due(self.conn,rid,'2099-01-02T09:00:00+00:00')
        return rid

    def test_delivered_note_card_binding_preserves_original_pending_lifetime(self):
        rid=self.saved();no=self.agent.note_no(rid)
        before=[];original=store.pending_set
        def stage(*args,**kwargs):
            result=original(*args,**kwargs)
            before.append(dict(self.conn.execute('SELECT * FROM pending_actions WHERE chat_id=1').fetchone()))
            return result
        with mock.patch.object(store,'pending_set',side_effect=stage):
            self.agent.do_note_edit(1,'en',{'id':no,'new_summary':'Approved replacement'},'change')
        after=dict(self.conn.execute('SELECT * FROM pending_actions WHERE chat_id=1').fetchone())
        self.assertEqual((after['created_at'],after['expires_at']),
                         (before[0]['created_at'],before[0]['expires_at']))
        self.assertEqual(store.pending_get(self.conn,1)['payload']['card_message_id'],self.sequence)

    def test_invalid_delivery_message_id_cannot_become_note_confirmation_authority(self):
        rid=self.saved();no=self.agent.note_no(rid)
        for invalid in [True,'12',0,-1,None]:
            with self.subTest(message_id=invalid), mock.patch.object(self.agent,'reply_prompt',return_value={'message_id':invalid}):
                self.agent.do_note_edit(1,'en',{'id':no,'new_summary':'Approved replacement'},'change')
            self.assertNotIn('card_message_id',store.pending_get(self.conn,1)['payload'])
            self.assertEqual(store.get_message(self.conn,rid)['summary'],'Original title')

    def test_existing_unversioned_note_confirmation_remains_compatible(self):
        rid=self.saved();no=self.agent.note_no(rid)
        store.pending_set(self.conn,1,'note_retitle',{'row_id':rid,'note_no':no,'new_summary':'Legacy approved replacement'})
        self.agent.resolve_pending(1,'confirm',{},store.pending_get(self.conn,1),'en')
        self.assertEqual(store.get_message(self.conn,rid)['summary'],'Legacy approved replacement')

    def test_old_media_save_button_cannot_save_newer_unapproved_card(self):
        old=self.card('First book');new=self.card('Different book')
        self.agent.handle_media_callback('old-save',1,{'message_id':old},'mcap|y')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM messages').fetchone()[0],0)
        self.assertEqual(self.agent._media_stash(1).get('card_message_id'),new)
        self.assertEqual(self.agent._media_stash(1)['entries'][0]['title'],'Different book')

    def test_old_media_cancel_button_cannot_discard_newer_card(self):
        old=self.card('First book');new=self.card('Different book')
        self.agent.handle_media_callback('old-cancel',1,{'message_id':old},'mcap|n')
        self.assertEqual(self.agent._media_stash(1).get('card_message_id'),new)
        self.assertEqual(store.pending_get(self.conn,1)['kind'],'media_capture')

    def test_current_media_save_still_stores_once(self):
        current=self.card('Current book')
        self.agent.handle_media_callback('current',1,{'message_id':current},'mcap|y')
        self.agent.handle_media_callback('duplicate',1,{'message_id':current},'mcap|y')
        rows=self.conn.execute('SELECT summary,status FROM messages').fetchall()
        self.assertEqual([tuple(r) for r in rows],[('Current book','confirmed')])

    def test_current_media_cancel_still_clears_the_card(self):
        current=self.card('Current book')
        self.agent.handle_media_callback('current',1,{'message_id':current},'mcap|n')
        self.assertFalse(self.agent._media_stash(1));self.assertIsNone(store.pending_get(self.conn,1))

    def test_undo_missing_explicit_number_never_restores_another_reminder(self):
        rid=self.moved();before=dict(store.reminder_get(self.conn,rid))
        self.agent.do_reminder_undo(1,'en',{'id':99})
        self.assertEqual(dict(store.reminder_get(self.conn,rid)),before)

    def test_undo_missing_explicit_title_never_restores_another_reminder(self):
        rid=self.moved();before=dict(store.reminder_get(self.conn,rid))
        self.agent.do_reminder_undo(1,'en',{'title_query':'Nonexistent subject'})
        self.assertEqual(dict(store.reminder_get(self.conn,rid)),before)

    def test_valid_numbered_undo_and_bare_unique_undo_still_work(self):
        rid=self.moved();self.agent.do_reminder_undo(1,'en',{'id':1})
        self.assertEqual(store.reminder_get(self.conn,rid)['due_utc'],'2099-01-01T09:00:00+00:00')
        self.agent.do_reminder_undo(1,'en',{})
        self.assertEqual(store.reminder_get(self.conn,rid)['due_utc'],'2099-01-02T09:00:00+00:00')

    def test_old_retitle_confirmation_cannot_overwrite_newer_note_text(self):
        rid=self.saved();no=self.agent.note_no(rid)
        self.agent.do_note_edit(1,'en',{'id':no,'new_summary':'Approved replacement'},'Change this title')
        pending=store.pending_get(self.conn,1)
        store.message_update_summary(self.conn,rid,'Newer independently edited title')
        self.agent.resolve_pending(1,'confirm',{},pending,'en')
        self.assertEqual(store.get_message(self.conn,rid)['summary'],'Newer independently edited title')
        refreshed=store.pending_get(self.conn,1)
        self.assertIsNotNone(refreshed);self.assertEqual(refreshed['kind'],'note_retitle')

    def test_fresh_retitle_confirmation_still_applies(self):
        rid=self.saved();no=self.agent.note_no(rid)
        self.agent.do_note_edit(1,'en',{'id':no,'new_summary':'Approved replacement'},'Change this title')
        self.agent.resolve_pending(1,'confirm',{},store.pending_get(self.conn,1),'en')
        self.assertEqual(store.get_message(self.conn,rid)['summary'],'Approved replacement')

    def test_old_media_reply_cannot_confirm_or_cancel_a_newer_card(self):
        old=self.card('First book');new=self.card('Different book')
        self.agent.turn_reply_message_id=old
        pending=store.pending_get(self.conn,1)
        self.agent.resolve_pending(1,'confirm',{},pending,'en')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM messages').fetchone()[0],0)
        self.assertEqual(self.agent._media_stash(1).get('card_message_id'),new)
        self.agent.resolve_pending(1,'cancel',{},store.pending_get(self.conn,1),'en')
        self.assertEqual(self.agent._media_stash(1).get('card_message_id'),new)

    def test_old_retitle_reply_cannot_confirm_a_replacement_prompt(self):
        rid=self.saved();no=self.agent.note_no(rid)
        self.agent.do_note_edit(1,'en',{'id':no,'new_summary':'First proposal'},'first')
        old=self.sequence
        self.agent.do_note_edit(1,'en',{'id':no,'new_summary':'Second proposal'},'second')
        self.agent.turn_reply_message_id=old
        self.agent.resolve_pending(1,'confirm',{},store.pending_get(self.conn,1),'en')
        self.assertEqual(store.get_message(self.conn,rid)['summary'],'Original title')
        self.assertEqual(store.pending_get(self.conn,1)['payload']['new_summary'],'Second proposal')

    def test_bulk_archive_confirmation_does_not_archive_reused_row_or_partial_batch(self):
        first=self.saved(10,'First note');second=self.saved(11,'Second note')
        self.agent.do_note_lifecycle(1,'en',{'operation':'archive','ids':[self.agent.note_no(first),self.agent.note_no(second)]})
        pending=store.pending_get(self.conn,1)
        store.delete_message(self.conn,second);new=self.saved(12,'Different note')
        self.assertEqual(new,second)
        before=[dict(store.get_message(self.conn,rid)) for rid in (first,new)]
        self.agent.resolve_pending(1,'confirm',{},pending,'en')
        self.assertEqual([dict(store.get_message(self.conn,rid)) for rid in (first,new)],before)

    def capture(self, extract=None, classify=None):
        photo={'message_id':70,'chat':{'id':1},'photo':[{'file_id':'synthetic','file_unique_id':'synthetic','width':1,'height':1}]}
        with mock.patch.object(self.agent,'_download_photo_tmp',return_value=Path(self.temp.name)/'synthetic.jpg'), \
                mock.patch.object(media_svc.media,'classify',side_effect=classify,return_value=('media','synthetic')), \
                mock.patch.object(media_svc.media,'extract',side_effect=extract,return_value=[]):
            return self.agent.handle_media_capture([photo],1,'')

    def test_real_media_model_failure_is_tagged_as_historical_operational_data(self):
        self.capture(extract=llm.LLMError('synthetic failure'))
        row=self.conn.execute("SELECT text,source FROM conversation WHERE role='bot' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(row['source'],'operational:llm_error')

    def test_empty_media_extraction_is_not_mislabeled_as_an_operational_failure(self):
        self.capture()
        row=self.conn.execute("SELECT text,source FROM conversation WHERE role='bot' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertNotIn(row['source'],store.CONVO_OPERATIONAL_SOURCES)

    def test_media_budget_notice_is_tagged_without_changing_its_delivery(self):
        self.capture(classify=llm.BudgetExceeded('day',2,2))
        row=self.conn.execute("SELECT text,source FROM conversation WHERE role='bot' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(row['source'],'operational:budget_stop')

    def test_album_failure_notice_is_tagged_without_replaying_parts(self):
        self.agent.albums={'synthetic':{'parts':[{'message_id':73,'chat':{'id':1}}],'deadline':0,'store':True,'update_ids':[]}}
        with mock.patch.object(self.agent,'finalize',side_effect=RuntimeError('synthetic failure')):
            self.agent.flush_albums(1)
        row=self.conn.execute("SELECT text,source FROM conversation WHERE role='bot' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(row['source'],'operational:album_failed');self.assertFalse(self.agent.albums)

    def test_old_delete_reply_cannot_confirm_or_cancel_a_different_pending_delete(self):
        first=self.saved(10,'First note');second=self.saved(11,'Different note')
        self.agent.do_item_delete(1,'en',{'id':self.agent.note_no(first)});old=self.sequence
        self.agent.do_item_delete(1,'en',{'id':self.agent.note_no(second)})
        self.agent.turn_reply_message_id=old
        self.agent.resolve_pending(1,'confirm',{},store.pending_get(self.conn,1),'en')
        self.assertIsNotNone(store.get_message(self.conn,second))
        pending=store.pending_get(self.conn,1);self.assertEqual(pending['payload']['row_ids'],[second])
        self.agent.resolve_pending(1,'cancel',{},pending,'en')
        self.assertEqual(store.pending_get(self.conn,1)['payload']['row_ids'],[second])

    def test_old_archive_reply_cannot_confirm_a_different_pending_batch(self):
        first=self.saved(10,'First note');second=self.saved(11,'Second note');third=self.saved(12,'Different note')
        self.agent.do_note_lifecycle(1,'en',{'operation':'archive','ids':[self.agent.note_no(first),self.agent.note_no(second)]});old=self.sequence
        self.agent.do_note_lifecycle(1,'en',{'operation':'archive','ids':[self.agent.note_no(second),self.agent.note_no(third)]})
        before=[dict(store.get_message(self.conn,rid)) for rid in (second,third)]
        self.agent.turn_reply_message_id=old
        self.agent.resolve_pending(1,'confirm',{},store.pending_get(self.conn,1),'en')
        self.assertEqual([dict(store.get_message(self.conn,rid)) for rid in (second,third)],before)
        self.assertEqual(store.pending_get(self.conn,1)['payload']['row_ids'],[second,third])

    def test_delete_confirmation_does_not_delete_reused_row_id(self):
        rid=self.saved();no=self.agent.note_no(rid)
        self.agent.do_item_delete(1,'en',{'id':no})
        pending=store.pending_get(self.conn,1)
        store.delete_message(self.conn,rid)
        new=self.saved(11,'A different note')
        self.assertEqual(new,rid,'exercise actual SQLite identity reuse')
        self.agent.resolve_pending(1,'confirm',{},pending,'en')
        self.assertIsNotNone(store.get_message(self.conn,new))
        self.assertEqual(store.get_message(self.conn,new)['summary'],'A different note')


if __name__=='__main__':unittest.main(verbosity=2)