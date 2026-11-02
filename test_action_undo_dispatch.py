"""Owner-visible latest-action undo and delivery-only recovery.

The new exact command crosses real handlers, SQLite and durable outbound
receipts. Model and Telegram boundaries stay synthetic; old phrases and card
ownership retain their existing routing contracts.
"""
import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import action_delivery
import llm
import router
import skill_manifest
import store
import tg_ingest_agent as entry
from testlib import make_config
from texts import T
from tg_api import TelegramError


class ProcessDeath(BaseException):
    pass


class ActionUndoDispatchTests(unittest.TestCase):
    COMMAND = "отмени последнее действие"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cara-undo-dispatch-")
        self.addCleanup(self.temp.cleanup)
        self.cfg = make_config(
            ALLOWED_CHAT_IDS="1", DB_PATH=str(Path(self.temp.name) / "db"),
            MEDIA_DIR=str(Path(self.temp.name) / "media"))
        self.sent = []
        self.transport = self.patch(entry, "tg_call", side_effect=self.send)
        self.patch(entry.Agent, "send_chat_action")
        self.patch(entry.Agent, "maybe_curate_conversation")
        self.patch(llm, "chat_profile", side_effect=AssertionError("no paid model"))
        self.patch(llm, "embed", side_effect=AssertionError("no embedding"))
        self.agent = entry.Agent(self.cfg)
        self.agent.turn_lang = "ru"
        self.addCleanup(lambda: self.agent.conn.close())

    @property
    def conn(self):
        return self.agent.conn

    def patch(self, target, name, **kwargs):
        patcher = mock.patch.object(target, name, **kwargs)
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def send(self, token, method, params, **kwargs):
        self.assertFalse(self.conn.in_transaction, "transport must follow the local commit")
        self.sent.append((method, copy.deepcopy(params)))
        return {"message_id": len(self.sent) + 100, "text": params.get("text", "")}

    def restart(self):
        self.agent.conn.close()
        self.agent = entry.Agent(self.cfg)
        self.agent.turn_lang = "ru"

    def reminder(self, title="Original reminder", due="2099-01-02T09:00:00+00:00"):
        return store.reminder_add(self.conn, 1, title, due)

    def note(self, number=1, category="Books"):
        rid = store.insert_message(self.conn, {
            "chat_id": 1, "tg_message_id": number, "received_at": store._now(),
            "raw_text": "Original owner source " + str(number)})
        store.ensure_category(self.conn, category)
        store.set_suggestion(self.conn, rid, category, "Original summary", "synthetic")
        store.confirm_category(self.conn, rid, category)
        return rid

    def latest(self):
        value = store.kv_get(self.conn, "action_undo:1")
        return json.loads(value) if value else None

    def dispatch(self, text=None, **message):
        text = self.COMMAND if text is None else text
        self.agent.dispatch(1, {"message_id": 901, "text": text, **message}, text)

    def update(self, number=901, text=None, user=1):
        return {"update_id": number, "message": {
            "message_id": number, "chat": {"id": 1, "type": "private"},
            "from": {"id": user}, "date": int(datetime.now(timezone.utc).timestamp()),
            "text": self.COMMAND if text is None else text}}

    def cancel(self, rid):
        self.agent.do_reminder_cancel(1, "ru", {"title_query": store.reminder_get(self.conn, rid)["title"]})

    def assert_undo_text(self, key="action_undo_done", **values):
        self.assertEqual(self.sent[-1][1]["text"], T("ru", key, **values))

    def undone_count(self):
        return self.conn.execute("SELECT COUNT(*) FROM reminder_events WHERE event='action_undone'").fetchone()[0]

    def test_exact_command_restores_actual_cancel_rename_and_reschedule_handlers(self):
        for operation in ("cancel", "rename", "reschedule"):
            with self.subTest(operation=operation):
                rid = self.reminder("Original " + operation)
                before = dict(store.reminder_get(self.conn, rid))
                params = {"title_query": before["title"]}
                if operation == "cancel":
                    self.agent.do_reminder_cancel(1, "ru", params)
                elif operation == "rename":
                    self.agent.do_rename_reminder(1, "ru", dict(params, new_title="Changed title"))
                else:
                    self.agent.do_reschedule(1, "ru", dict(params, due_utc="2099-01-04T11:00:00+00:00"))
                self.assertNotEqual(dict(store.reminder_get(self.conn, rid)), before)
                with mock.patch.object(router, "route", side_effect=AssertionError("exact undo is deterministic")):
                    self.dispatch()
                self.assertEqual(dict(store.reminder_get(self.conn, rid)), before)
                self.assertIsNone(self.latest())
                self.assert_undo_text(n=1)

    def test_exact_command_restores_recategorize_handler_without_erasing_correction_history(self):
        rid = self.note()
        before = dict(store.get_message(self.conn, rid))
        self.agent.do_recategorize(1, "ru", {"id": self.agent.note_no(rid), "category": "Work"})
        self.assertEqual(store.get_message(self.conn, rid)["category"], "Work")
        feedback = [tuple(row) for row in self.conn.execute("SELECT * FROM feedback")]
        self.assertTrue(feedback)
        self.dispatch()
        self.assertEqual(dict(store.get_message(self.conn, rid)), before)
        self.assertEqual([tuple(row) for row in self.conn.execute("SELECT * FROM feedback")], feedback)
        self.assert_undo_text(n=1)

    def test_exact_command_restores_note_lifecycle_handler_metadata(self):
        rid = self.note()
        store.note_set_review(self.conn, rid, "2099-04-01T09:00:00+00:00")
        before = dict(store.get_message(self.conn, rid))
        self.agent.do_note_lifecycle(1, "ru", {"id": self.agent.note_no(rid), "operation": "archive"})
        self.assertEqual(store.get_message(self.conn, rid)["knowledge_state"], "archived")
        self.dispatch()
        self.assertEqual(dict(store.get_message(self.conn, rid)), before)
        self.assert_undo_text(n=1)

    def test_bulk_cancel_is_one_inverse_over_original_targets(self):
        first, second = self.reminder("First"), self.reminder("Second")
        before = [dict(store.reminder_get(self.conn, rid)) for rid in (first, second)]
        self.agent.do_reminder_cancel(1, "ru", {"all": True})
        unrelated = self.reminder("New unrelated reminder")
        self.dispatch()
        self.assertEqual([dict(store.reminder_get(self.conn, rid)) for rid in (first, second)], before)
        self.assertEqual(store.reminder_get(self.conn, unrelated)["status"], "active")
        self.assert_undo_text(n=2)

    def test_bulk_archive_captures_at_confirmation_and_undoes_whole_actual_batch(self):
        ids = (self.note(1), self.note(2))
        before = [dict(store.get_message(self.conn, rid)) for rid in ids]
        self.agent.do_note_lifecycle(1, "ru", {
            "ids": [self.agent.note_no(rid) for rid in ids], "operation": "archive"})
        pending = store.pending_get(self.conn, 1)
        self.assertEqual(pending["kind"], "note_archive")
        self.assertIsNone(self.latest(), "opening the card is not the mutation")
        self.agent.resolve_pending(1, "confirm", {}, pending, "ru")
        self.assertEqual([store.get_message(self.conn, rid)["knowledge_state"] for rid in ids], ["archived"] * 2)
        self.assertIsNone(store.pending_get(self.conn, 1))
        self.dispatch()
        self.assertEqual([dict(store.get_message(self.conn, rid)) for rid in ids], before)
        self.assert_undo_text(n=2)

    def test_old_phrases_keep_reminder_undo_instead_of_latest_note_mutation(self):
        rid = self.reminder()
        self.agent.do_reschedule(1, "ru", {"title_query": "Original reminder", "due_utc": "2099-01-04T09:00:00+00:00"})
        note = self.note()
        self.agent.do_recategorize(1, "ru", {"id": self.agent.note_no(note), "category": "Work"})
        latest = self.latest()
        for phrase in ("отмени", "верни как было", "не то"):
            with self.subTest(phrase=phrase):
                previous = store.reminder_get(self.conn, rid)["prev_due_utc"]
                with mock.patch.object(router, "route", return_value={
                        "action": "reminder_undo", "params": {}, "confidence": 1}) as route:
                    self.dispatch(phrase)
                route.assert_called_once()
                self.assertEqual(store.reminder_get(self.conn, rid)["due_utc"], previous)
                self.assertEqual(store.get_message(self.conn, note)["category"], "Work")
                self.assertEqual(self.latest(), latest)

    def test_old_reminder_undo_reply_binding_does_not_choose_another_moved_reminder(self):
        first, second = self.reminder("First"), self.reminder("Second")
        for rid in (first, second):
            self.agent.do_reschedule(1, "ru", {"title_query": store.reminder_get(self.conn, rid)["title"],
                                             "due_utc": "2099-01-05T09:00:00+00:00"})
        before_second = dict(store.reminder_get(self.conn, second))
        prior = store.reminder_get(self.conn, first)["prev_due_utc"]
        self.agent.turn_reply_reminder_id = first
        with mock.patch.object(router, "route", return_value={
                "action": "reminder_undo", "params": {}, "confidence": 1}) as route:
            self.dispatch("верни как было", reply_to_message={"message_id": 77})
        route.assert_called_once()
        self.assertEqual(store.reminder_get(self.conn, first)["due_utc"], prior)
        self.assertEqual(dict(store.reminder_get(self.conn, second)), before_second)

    def test_pending_card_owns_exact_command_and_purge_uses_its_existing_cancel_path(self):
        rid = self.reminder()
        self.cancel(rid)
        latest = self.latest()
        store.pending_set(self.conn, 1, "category", {"row_id": 77})
        pending = store.pending_get(self.conn, 1)
        with mock.patch.object(router, "route", return_value={
                "action": "converse", "params": {}, "confidence": 1}) as route, \
                mock.patch.object(self.agent, "do_converse"):
            self.dispatch()
        route.assert_called_once()
        self.assertEqual(store.pending_get(self.conn, 1), pending)
        self.assertEqual(self.latest(), latest)
        self.assertEqual(store.reminder_get(self.conn, rid)["status"], "cancelled")

        store.pending_set(self.conn, 1, "purge", {"phrase": "EXACT DESTRUCTIVE PHRASE"})
        with mock.patch.object(router, "route", side_effect=AssertionError("purge retains its deterministic owner")):
            self.dispatch()
        self.assertIsNone(store.pending_get(self.conn, 1))
        self.assertEqual(self.latest(), latest)
        self.assertEqual(store.reminder_get(self.conn, rid)["status"], "cancelled")

    def test_replied_quoted_and_forwarded_contexts_do_not_become_generic_undo(self):
        rid = self.reminder()
        self.cancel(rid)
        latest = self.latest()
        contexts = (
            {"reply_to_message": {"message_id": 77}}, {"quote": {"text": self.COMMAND}},
            {"external_reply": {"origin": {"type": "user"}}},
            {"forward_origin": {"type": "user"}}, {"forward_from": {"id": 2}},
            {"forward_from_chat": {"id": 2}}, {"forward_sender_name": "External source"})
        for context in contexts:
            with self.subTest(context=context), \
                    mock.patch.object(router, "route", return_value={
                        "action": "converse", "params": {}, "confidence": 1}) as route, \
                    mock.patch.object(self.agent, "do_converse"):
                self.dispatch(**context)
                route.assert_called_once()
                self.assertEqual(self.latest(), latest)
                self.assertEqual(store.reminder_get(self.conn, rid)["status"], "cancelled")

    def test_live_task_approval_keeps_ownership_without_consuming_inverse(self):
        rid = self.reminder()
        self.cancel(rid)
        latest = self.latest()
        store.telegram_update_receive(self.conn, self.update(800, "Read reminders"), 1)
        store.convo_add(self.conn, 1, "user", "Read reminders", source="boss", update_id=800)
        task, _ = store.assistant_task_create(self.conn, 1, 800, {
            "objective": "Read reminders", "deliverable": "answer", "steps": [{
                "key": "read", "tool": "reminders.read", "input": {}, "bindings": {},
                "depends_on": [], "purpose": "Read reminders"}]})
        step = store.assistant_task_steps(self.conn, task["id"])[0]
        store.task_approval_create(self.conn, task, step, {"title": "Review"}, "hash", {}, "1")
        approvals = [tuple(row) for row in self.conn.execute("SELECT * FROM task_approvals")]
        with mock.patch.object(router, "route", return_value={
                "action": "converse", "params": {}, "confidence": 1}) as route, \
                mock.patch.object(self.agent, "do_converse"):
            self.dispatch()
        route.assert_called_once()
        self.assertEqual([tuple(row) for row in self.conn.execute("SELECT * FROM task_approvals")], approvals)
        self.assertEqual(self.latest(), latest)

    def test_near_miss_commands_cannot_trigger_latest_action_inverse(self):
        rid = self.reminder()
        self.cancel(rid)
        latest = self.latest()
        for text in ("отмени последнее действие пожалуйста", "не отмени последнее действие",
                     "undo last action 2", "please undo last action", '"отмени последнее действие"'):
            with self.subTest(text=text), \
                    mock.patch.object(router, "route", return_value={
                        "action": "converse", "params": {}, "confidence": 1}) as route, \
                    mock.patch.object(self.agent, "do_converse"):
                self.dispatch(text)
                route.assert_called_once()
                self.assertEqual(self.latest(), latest)
                self.assertEqual(store.reminder_get(self.conn, rid)["status"], "cancelled")

    def test_english_exact_command_uses_english_result_without_model(self):
        rid = self.reminder()
        self.cancel(rid)
        self.agent.turn_lang = "en"
        with mock.patch.object(router, "route", side_effect=AssertionError("no model routing")):
            self.dispatch("Undo the last action!")
        self.assertEqual(store.reminder_get(self.conn, rid)["status"], "active")
        self.assertEqual(self.sent[-1][1]["text"], T("en", "action_undo_done", n=1))

    def test_refusal_replies_report_missing_expired_conflict_and_past_due_without_mutation(self):
        self.dispatch()
        self.assert_undo_text("action_undo_missing")
        rid = self.reminder()
        self.cancel(rid)
        record = self.latest()
        expired_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        record["created_at"] = (expired_at - timedelta(minutes=10)).isoformat()
        record["expires_at"] = expired_at.isoformat()
        store.kv_set(self.conn, "action_undo:1", json.dumps(record))
        self.dispatch()
        self.assert_undo_text("action_undo_expired")
        self.assertEqual(store.reminder_get(self.conn, rid)["status"], "cancelled")

        other = self.reminder("Conflict target")
        self.cancel(other)
        store.reminder_rename(self.conn, other, "Intervening title")
        before = dict(store.reminder_get(self.conn, other))
        self.dispatch()
        self.assert_undo_text("action_undo_conflict")
        self.assertEqual(dict(store.reminder_get(self.conn, other)), before)

        overdue = self.reminder("Unfired overdue", (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat())
        self.cancel(overdue)
        self.dispatch()
        self.assert_undo_text("action_undo_past_due")
        self.assertEqual(store.reminder_get(self.conn, overdue)["status"], "cancelled")

    def test_known_unsent_undo_reply_retries_after_restart_without_repeating_inverse(self):
        rid = self.reminder()
        self.cancel(rid)
        self.sent.clear()
        self.transport.reset_mock()
        self.transport.side_effect = TelegramError("rejected", status=429, retry_after=1)
        update = self.update()
        self.agent.process_update_batch([update])
        self.assertEqual(store.reminder_get(self.conn, rid)["status"], "active")
        self.assertEqual(self.undone_count(), 1)
        self.assertEqual(store.telegram_update_get(self.conn, 901)["status"], "done")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM action_receipts WHERE action='do_action_undo'").fetchone()[0], 1)
        self.restart()
        self.conn.execute("UPDATE reply_outbox SET available_at='2000-01-01' WHERE state='pending'")
        self.conn.commit()
        self.transport.side_effect = self.send
        self.agent.check_reply_deliveries()
        self.agent.process_update_batch([update])
        self.agent.check_reply_deliveries()
        self.assertEqual(self.undone_count(), 1)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][1]["text"], T("ru", "action_undo_done", n=1))
        self.assertIsNone(self.latest())

    def test_ambiguous_undo_reply_never_resends_or_repeats_after_restart(self):
        rid = self.reminder()
        self.cancel(rid)
        self.sent.clear()
        self.transport.reset_mock()
        self.transport.side_effect = TelegramError("timeout", outcome_unknown=True)
        update = self.update()
        self.agent.process_update_batch([update])
        self.assertEqual(store.reminder_get(self.conn, rid)["status"], "active")
        self.assertEqual(self.undone_count(), 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM reply_outbox WHERE state='ambiguous'").fetchone()[0], 1)
        self.restart()
        self.transport.side_effect = self.send
        self.agent.check_reply_deliveries()
        self.agent.process_update_batch([update])
        self.assertEqual(self.transport.call_count, 1)
        self.assertEqual(self.undone_count(), 1)
        self.assertEqual(self.sent, [])

    def test_committed_undo_recovers_saved_receipt_before_rerouting_after_process_death(self):
        rid = self.reminder()
        self.cancel(rid)
        self.sent.clear()
        self.agent._active_update_id = 990
        self.agent._reminder_scope = action_delivery.scope_for_update(990, self.COMMAND)
        with mock.patch.object(action_delivery, "deliver_action", side_effect=ProcessDeath):
            with self.assertRaises(ProcessDeath):
                self.agent.do_action_undo(1, "ru")
        self.assertEqual(store.reminder_get(self.conn, rid)["status"], "active")
        self.restart()
        self.agent._active_update_id = 990
        with mock.patch.object(self.agent, "_dispatch", side_effect=AssertionError("receipt must win before routing")):
            self.dispatch()
        self.assertEqual(self.undone_count(), 1)
        self.assertEqual(len(self.sent), 1)
        self.assert_undo_text(n=1)

    def test_nonowner_command_is_rejected_before_inverse_history_or_delivery(self):
        rid = self.reminder()
        self.cancel(rid)
        latest = self.latest()
        self.sent.clear()
        history = [tuple(row) for row in self.conn.execute("SELECT * FROM conversation")]
        self.agent.process_update_batch([self.update(user=2)])
        self.assertEqual(store.reminder_get(self.conn, rid)["status"], "cancelled")
        self.assertEqual(self.latest(), latest)
        self.assertEqual([tuple(row) for row in self.conn.execute("SELECT * FROM conversation")], history)
        self.assertEqual(self.sent, [])

    def test_undo_uses_existing_kv_and_keeps_schema_and_router_capabilities(self):
        schema = [tuple(row) for row in self.conn.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")]
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        actions = set(router.ACTIONS)
        rid = self.reminder()
        self.cancel(rid)
        self.dispatch()
        self.assertEqual([tuple(row) for row in self.conn.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")], schema)
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], version)
        self.assertEqual(set(router.ACTIONS), actions)
        self.assertNotIn("action_undo", router.ACTIONS)

    def test_successful_undo_trace_records_state_write_while_policy_stays_internal(self):
        rid = self.reminder()
        self.cancel(rid)
        trace_id = entry.trace.start(self.conn, "inbound", 1)
        try:
            self.dispatch()
        finally:
            entry.trace.finish(self.conn, trace_id, "ok")
        routes = [json.loads(row["data"]) for row in store.trace_events(self.conn, trace_id)
                  if row["stage"] == entry.trace.ROUTER_COMPLETED]
        self.assertEqual(len(routes), 1)
        self.assertEqual({key: routes[0][key] for key in ("action", "source", "risk", "status")}, {
            "action": "action_undo", "source": "explicit_action_undo",
            "risk": "state_write", "status": "undone"})
        self.assertEqual(json.loads(store.kv_get(self.conn, "last_state_write_route"))["action"], "action_undo")
        policy = skill_manifest.get_policy("action_undo")
        self.assertTrue(policy["writes_state"])
        self.assertTrue(policy["internal"])
        without_internal = {key: value for key, value in skill_manifest.SKILLS.items() if key != "action_undo"}
        for lang in ("ru", "en"):
            titles = skill_manifest.capability_titles(lang, self.cfg)
            with mock.patch.object(skill_manifest, "SKILLS", without_internal):
                self.assertEqual(skill_manifest.capability_titles(lang, self.cfg), titles)


if __name__ == "__main__":
    unittest.main()
