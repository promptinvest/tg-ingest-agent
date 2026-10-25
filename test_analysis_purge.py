"""New analysis copies follow source erasure without changing old history.

Use native task/read/receipt/accounting/delivery paths against fresh synthetic
databases. Only outward model and Telegram transport are replaced.
"""
import json
import re
import tempfile
import threading
import time
import unittest
from unittest import mock

import analysis_engine as engine
import analysis_lifecycle as lifecycle
import common
import llm
import store
import task_runner
import tasks_svc
import tg_ingest_agent
from test_analysis_engine import completion, prepare_analysis_case


class AnalysisPurgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        profiles = {name: {"primary": "deepseek-4-flash", "fallbacks": [], "max_tokens": 1200,
                           "json_required": True} for name in ("task_planner", "task_synthesis")}
        self.case = prepare_analysis_case("compare_en", self.tmp.name, {
            "LLM_PROFILES_JSON": json.dumps(profiles)})
        self.agent, self.conn = self.case["agent"], self.case["conn"]
        self.prompts, self.messages = [], []
        self.hold_stage = None
        self.entered, self.release = threading.Event(), threading.Event()
        self.initial = {"reads": [{"tool": "knowledge.search", "input": {"query": "Plan", "limit": 8},
                                   "reason": "Read the saved comparison evidence"}], "unknowns": []}
        self.transport = mock.patch.object(llm, "transport_chat", side_effect=self.model)
        self.transport.start()
        self.telegram = mock.patch.object(tasks_svc, "tg_call", side_effect=self.send)
        self.telegram.start()

    def tearDown(self):
        self.release.set()
        for handle in list(engine._calls(self.agent).values()):
            if handle.future is not None:
                handle.future.result(timeout=3)
            handle.cancel()
            handle.advance()
        self.transport.stop(); self.telegram.stop()
        self.conn.close(); self.tmp.cleanup()
        common.set_current_trace(None)

    def send(self, token, method, fields):
        self.messages.append((method, fields))
        return {"message_id": 7000 + len(self.messages)}

    def model(self, prepared):
        messages = json.loads(prepared["request"].data)["messages"]
        self.prompts.append(messages)
        user = messages[-1]["content"]
        stage = "initial" if "PHASE=initial" in user else "adaptive" if "PHASE=adaptive" in user else "final"
        if stage == self.hold_stage:
            self.entered.set()
            self.release.wait(3)
        if stage == "initial":
            return completion(self.initial)
        if stage == "adaptive":
            return completion({"reads": [], "unknowns": []})
        sources = json.loads(re.search(r"<SOURCES>\n(.*?)\n</SOURCES>", user, re.S)[1])
        facts = [{"source_id": item["id"], "quote": item["text"][:min(60, len(item["text"]))]}
                 for item in sources[:2]]
        return completion({"facts": facts, "conclusion": {
            "text": "The recorded terms differ; verify current terms before choosing.",
            "source_ids": [fact["source_id"] for fact in facts]},
            "conflicts": [], "unknowns": ["Current terms are unverified."]})

    def start(self, *, source=None, context=None):
        if source is None:
            update_id, text = self.case["update_id"], self.case["text"]
        else:
            update_id = self.conn.execute("SELECT MAX(update_id)+1 FROM telegram_updates").fetchone()[0]
            text = source
            message = {"message_id": update_id, "chat": {"id": 111}, "from": {"id": 111}, "text": text}
            store.telegram_update_receive(self.conn, {"update_id": update_id, "message": message}, 111)
            store.convo_add(self.conn, 111, "user", text, source="boss", update_id=update_id,
                            tg_message_id=update_id)
        row, _ = store.assistant_task_create(self.conn, 111, update_id,
                                             engine.build_plan(text, context_task_id=context))
        return row["id"]

    def drain(self, task_id):
        for _ in range(1000):
            task_runner.tick(self.agent, self.conn)
            row = store.assistant_task_get(self.conn, task_id)
            if row["status"] in {"completed", "blocked", "failed", "cancelled"}:
                return row
            time.sleep(.002)
        self.fail("Analysis did not settle")

    def note_used(self, task_id):
        row = store.assistant_task_get(self.conn, task_id)
        state = json.loads(row["plan_json"])["analysis_state"]
        source = next(source for read in state["reads"] for source in read.get("sources", [])
                      if source["kind"] == "note")
        return store.message_by_note_no(self.conn, source["note_no"], chat_id=111)

    def assert_erased(self, task_id):
        row = store.assistant_task_get(self.conn, task_id)
        self.assertTrue(lifecycle.is_erased(row))
        self.assertEqual(row["final_summary"], lifecycle.erased_text(row))
        plan = json.loads(row["plan_json"])
        self.assertEqual(plan["analysis_state"]["reads"], [])
        self.assertIsNone(plan["analysis_state"]["context"])
        for receipt in self.conn.execute("SELECT * FROM tool_receipts WHERE task_id=?", (task_id,)):
            self.assertEqual(json.loads(receipt["data_json"]), {
                "schema": lifecycle.SCHEMA, "value": {"evidence_removed": True}})
            self.assertEqual(receipt["evidence_json"], "[]")
        return row

    def usage(self):
        return [tuple(row) for row in self.conn.execute("SELECT * FROM llm_usage ORDER BY id")]

    def test_single_delete_preserves_original_delivery_and_accounting_metadata(self):
        task_id = self.start(); before = self.drain(task_id)
        self.assertEqual(before["status"], "completed")
        delivery_fields = ("delivery_status", "delivery_attempts", "final_message_id", "status_message_id",
                           "delivered_at", "model_calls", "task_cost_usd", "objective", "source_update", "status")
        original_receipts = {row["id"]: {key: row[key] for key in (
            "receipt_uid", "idempotency_key", "task_id", "step_id", "tool", "input_hash",
            "policy_version", "implementation_version", "status", "created_at")}
            for row in self.conn.execute("SELECT * FROM tool_receipts WHERE task_id=?", (task_id,))}
        prior_usage = self.usage()
        note = self.note_used(task_id)
        store.delete_message(self.conn, note["id"])
        after = self.assert_erased(task_id)
        for key in delivery_fields:
            self.assertEqual(after[key], before[key], key)
        for row in self.conn.execute("SELECT * FROM tool_receipts WHERE task_id=?", (task_id,)):
            self.assertEqual({key: row[key] for key in original_receipts[row["id"]]}, original_receipts[row["id"]])
        self.assertEqual(self.usage(), prior_usage)
        calls, sends = len(self.prompts), len(self.messages)
        self.agent.retry_task_deliveries()
        self.agent.on_task_completed(after, before["final_summary"], None)
        self.agent.on_task_blocked(after, before["final_summary"])
        self.agent.on_task_cancelled(after, before["final_summary"])
        task_runner.run_task(self.agent, self.conn, task_id)
        self.assertEqual((len(self.prompts), len(self.messages)), (calls, sends))
        with mock.patch.object(self.agent, "reply") as reply:
            self.agent.do_task_show(111, "en", {"id": task_id})
            self.assertIn("evidence was removed", reply.call_args.args[1])
            self.assertNotIn("recorded terms differ", reply.call_args.args[1])
            self.agent.do_task_resume(111, "en", {"id": task_id})
        self.assertEqual(len(self.prompts), calls)

    def test_category_purge_discloses_only_new_affected_analysis_copies(self):
        self.assertNotIn("analysis_evidence_copies", store.purge_preview(self.conn, "category", "Options"))
        task_id = self.start(); self.drain(task_id)
        preview = store.purge_preview(self.conn, "category", "Options")
        self.assertEqual(preview["analysis_evidence_copies"], 1)
        self.assertIn("delivery history remains", self.agent._purge_impact_text("en", preview))
        info, _ = store.purge_execute(self.conn, "category", "Options")
        self.assertEqual(info, preview)
        self.assert_erased(task_id)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)

    def test_task_show_transport_copy_is_tagged_and_scrubbed_with_source(self):
        task_id = self.start(); self.drain(task_id)
        with mock.patch.object(tg_ingest_agent, "tg_call", return_value={"message_id": 9100}):
            self.agent.do_task_show(111, "en", {"id": task_id})
        copies = [row for row in self.conn.execute("SELECT * FROM reply_outbox")
                  if json.loads(row["metadata_json"]).get("analysis_task_id") == task_id]
        self.assertTrue(copies)
        self.assertTrue(any("recorded terms differ" in row["params_json"] for row in copies))
        store.delete_message(self.conn, self.note_used(task_id)["id"])
        for prior in copies:
            row = self.conn.execute("SELECT * FROM reply_outbox WHERE id=?", (prior["id"],)).fetchone()
            self.assertEqual(row["params_json"], "{}")
            self.assertEqual(row["delivery_key"], prior["delivery_key"])

    def test_old_pending_delivery_handle_cannot_send_after_source_erasure(self):
        task_id = self.start()
        with mock.patch.object(tasks_svc, "tg_call", return_value=None):
            completed = self.drain(task_id)
        self.assertEqual(completed["delivery_status"], "ambiguous")
        self.assertTrue(store.assistant_task_authorize_redelivery(self.conn, task_id, 111))
        old_pending = store.assistant_task_get(self.conn, task_id)
        store.delete_message(self.conn, self.note_used(task_id)["id"])
        sends = len(self.messages)
        self.assertFalse(self.agent.on_task_completed(old_pending, completed["final_summary"], None))
        self.assertEqual(len(self.messages), sends)
        self.assertEqual(store.assistant_task_get(self.conn, task_id)["delivery_status"], "pending")

    def test_reminder_purge_erases_snapshots_without_rewriting_delivery(self):
        self.initial = {"reads": [{"tool": "reminders.read", "input": {}, "reason": "Read reminder timing"}], "unknowns": []}
        store.reminder_add(self.conn, 111, "Synthetic meeting", "2026-10-28T09:00:00+00:00")
        task_id = self.start(); before = self.drain(task_id)
        self.assertEqual(before["status"], "completed")
        self.assertEqual(store.purge_preview(self.conn, "reminders")["analysis_evidence_copies"], 1)
        store.purge_execute(self.conn, "reminders")
        after = self.assert_erased(task_id)
        self.assertEqual(after["delivery_status"], before["delivery_status"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM reminders").fetchone()[0], 0)

    def test_descendant_contexts_are_erased_but_unrelated_and_legacy_tasks_are_exact(self):
        parent = self.start(); self.drain(parent)
        child = self.start(source=f"For task #{parent}, compare the capacity criterion too.", context=parent)
        self.assertEqual(self.drain(child)["status"], "completed")
        unrelated = self.start(source="Compare two newly supplied alternatives.")
        unrelated_before = tuple(store.assistant_task_get(self.conn, unrelated))
        update_id = self.conn.execute("SELECT MAX(update_id)+1 FROM telegram_updates").fetchone()[0]
        text = "Read reminder metadata for a legacy fixture."
        store.telegram_update_receive(self.conn, {"update_id": update_id, "message": {
            "message_id": update_id, "chat": {"id": 111}, "from": {"id": 111}, "text": text}}, 111)
        store.convo_add(self.conn, 111, "user", text, source="boss", update_id=update_id)
        legacy, _ = store.assistant_task_create(self.conn, 111, update_id, {
            "objective": "Read reminder metadata", "deliverable": "answer", "steps": [{
                "key": "read", "tool": "reminders.read", "input": {}, "bindings": {},
                "depends_on": [], "purpose": "Read existing reminders"}]})
        legacy_source = legacy["id"]
        legacy_before = tuple(legacy)
        note = self.note_used(parent)
        store.delete_message(self.conn, note["id"])
        self.assert_erased(parent); self.assert_erased(child)
        self.assertEqual(tuple(store.assistant_task_get(self.conn, unrelated)), unrelated_before)
        self.assertEqual(tuple(store.assistant_task_get(self.conn, legacy_source)), legacy_before)

    def test_messages_purge_preserves_journal_and_journal_purge_preserves_category(self):
        task_id = self.start(); self.drain(task_id)
        mid = store.insert_message(self.conn, {"chat_id": 111, "tg_message_id": 991100,
            "received_at": "2026-10-25T10:00:00+00:00", "raw_text": "Synthetic diary entry"})
        store.set_suggestion(self.conn, mid, "Diary", "Synthetic diary entry", "fixture")
        store.confirm_category(self.conn, mid, "Diary")
        store.set_category_kind(self.conn, "Diary", "journal")
        diary = tuple(store.message_by_note_no(self.conn, 4, chat_id=111))
        store.purge_execute(self.conn, "messages")
        self.assert_erased(task_id)
        self.assertEqual(tuple(store.message_by_note_no(self.conn, 4, chat_id=111)), diary)
        store.purge_execute(self.conn, "journal", "Diary")
        self.assertIsNone(store.message_by_note_no(self.conn, 4, chat_id=111))
        self.assertTrue(store.is_journal(self.conn, "Diary"))

    def test_scoped_delete_revokes_late_response_and_preserves_exact_usage_ids(self):
        self.hold_stage = "adaptive"
        task_id = self.start()
        for _ in range(1000):
            task_runner.run_task(self.agent, self.conn, task_id)
            if self.entered.wait(.002):
                break
        self.assertTrue(self.entered.is_set())
        before = store.assistant_task_get(self.conn, task_id)
        usage_ids = [uid for phase in json.loads(before["plan_json"])["analysis_state"]["phases"]
                     for uid in phase["usage_ids"]]
        note = self.note_used(task_id)
        store.delete_message(self.conn, note["id"])
        erased = self.assert_erased(task_id)
        self.assertEqual(erased["status"], "cancelled")
        self.assertEqual([uid for phase in json.loads(erased["plan_json"])["analysis_state"]["phases"]
                          for uid in phase["usage_ids"]], usage_ids)
        self.release.set()
        for _ in range(1000):
            engine.poll(self.agent, self.conn)
            if not engine._calls(self.agent):
                break
            time.sleep(.002)
        self.assertFalse(engine._calls(self.agent))
        self.assert_erased(task_id)
        self.assertEqual(len(self.messages), 0)
        self.assertEqual(len(self.prompts), 2)
        self.assertTrue(all(row[0] == "chat" for row in self.conn.execute("SELECT kind FROM llm_usage")))

    def test_all_purge_requires_actual_async_settlement_before_existing_cascade(self):
        self.hold_stage = "initial"
        task_id = self.start()
        task_runner.run_task(self.agent, self.conn, task_id)
        self.assertTrue(self.entered.wait(1))
        nonce = "b" * 32
        store.kv_set(self.conn, "task_purge_authorization", json.dumps({"nonce": nonce, "phase": "prepared"}))
        confirmed = self.agent._content_purge_preview("all")
        self.assertFalse(lifecycle.begin_all_purge(self.agent, 111, "en", nonce, confirmed))
        self.assertTrue(lifecycle.admission_held(self.conn))
        with self.assertRaises(ValueError):
            store.purge_execute(self.conn, "all", task_purge_nonce=nonce)
        self.assertIsNotNone(store.assistant_task_get(self.conn, task_id))
        self.release.set()
        for _ in range(1000):
            if lifecycle.begin_all_purge(self.agent, 111, "en", nonce, confirmed):
                break
            time.sleep(.002)
        self.assertFalse(engine._calls(self.agent))
        usage = self.usage()
        store.purge_execute(self.conn, "all", task_purge_nonce=nonce)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM assistant_tasks").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM tool_receipts").fetchone()[0], 0)
        self.assertEqual(self.usage(), usage)
        self.assertEqual(len(self.messages), 0)

    def test_new_source_during_wait_renews_typed_preview_without_replaying_confirmation(self):
        task_id = self.start(); self.drain(task_id)
        nonce = "c" * 32
        store.kv_set(self.conn, "task_purge_authorization", json.dumps({"nonce": nonce, "phase": "prepared"}))
        confirmed = self.agent._content_purge_preview("all")
        self.assertTrue(lifecycle.begin_all_purge(self.agent, 111, "en", nonce, confirmed))
        store.insert_message(self.conn, {"chat_id": 111, "tg_message_id": 991111,
                                        "received_at": "2026-10-25T10:00:00+00:00",
                                        "raw_text": "New synthetic content after confirmation"})
        with mock.patch.object(self.agent, "reply") as reply, \
                mock.patch.object(self.agent, "_complete_content_purge") as complete:
            self.assertTrue(lifecycle.continue_all_purge(self.agent))
        complete.assert_not_called(); reply.assert_called_once()
        pending = store.pending_get(self.conn, 111)
        self.assertEqual(pending["payload"]["analysis_purge_nonce"], nonce)
        self.assertIsNotNone(store.assistant_task_get(self.conn, task_id))

    def test_edited_confirmation_cancels_wait_without_deleting_data(self):
        task_id = self.start(); self.drain(task_id)
        nonce = "d" * 32
        store.kv_set(self.conn, "task_purge_authorization", json.dumps({"nonce": nonce, "phase": "prepared"}))
        self.assertTrue(lifecycle.begin_all_purge(self.agent, 111, "en", nonce,
                                                self.agent._content_purge_preview("all")))
        self.conn.execute("UPDATE conversation SET text='Edited confirmation' WHERE update_id=?",
                          (self.case["update_id"],)); self.conn.commit()
        with mock.patch.object(self.agent, "_complete_content_purge") as complete:
            lifecycle.continue_all_purge(self.agent)
        complete.assert_not_called()
        self.assertFalse(lifecycle.admission_held(self.conn))
        self.assertIsNotNone(store.assistant_task_get(self.conn, task_id))
        self.assertGreater(self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
