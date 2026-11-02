"""An inverse supersedes only its own definitely unsent outcome deliveries."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import action_delivery
import events
import llm
import store
import tg_ingest_agent as entry
from testlib import make_config
from tg_api import TelegramError


class ProcessDeath(BaseException):
    pass


class ActionUndoOutboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cara-undo-outbox-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.cfg = make_config(
            ALLOWED_CHAT_IDS="1", DB_PATH=str(root / "state.db"),
            MEDIA_DIR=str(root / "media"))
        self.sent = []
        self.delivery = "sent"
        self.patch(entry, "tg_call", side_effect=self.send)
        self.patch(llm, "chat_profile", side_effect=AssertionError("no model calls"))
        self.patch(llm, "embed", side_effect=AssertionError("no embedding calls"))
        self.agent = entry.Agent(self.cfg)
        self.addCleanup(lambda: self.agent.conn.close())

    def patch(self, target, name, **kwargs):
        patcher = mock.patch.object(target, name, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    @property
    def conn(self):
        return self.agent.conn

    def send(self, token, method, params, **kwargs):
        self.assertFalse(self.conn.in_transaction, "network follows the local commit")
        if self.delivery == "pending":
            raise TelegramError("synthetic known-unsent rejection", status=429, retry_after=1)
        if self.delivery == "ambiguous":
            raise TelegramError("synthetic unknown send", outcome_unknown=True)
        self.sent.append((method, dict(params)))
        return {"message_id": 100 + len(self.sent), "text": params.get("text", "")}

    def scope(self, text, update=900):
        self.agent._active_update_id = update
        self.agent._reminder_scope = action_delivery.scope_for_update(update, text)

    def outbox(self, row_id):
        return dict(self.conn.execute(
            "SELECT * FROM reply_outbox WHERE id=?", (row_id,)).fetchone())

    def receipts(self):
        return {row["action_key"]: dict(row) for row in self.conn.execute(
            "SELECT * FROM action_receipts")}

    def assert_receipts_retained(self, originals):
        current = self.receipts()
        for key, before in originals.items():
            self.assertIn(key, current)
            self.assertEqual(current[key], before)

    def reminder_action(self, delivery="pending"):
        rid = store.reminder_add(self.conn, 1, "Bank call", "2099-01-02T09:00:00+00:00")
        self.scope("fragment:0:move first to Friday")
        self.delivery = delivery
        self.agent.do_reschedule(1, "en", {
            "id": 1, "due_utc": "2099-01-04T09:00:00+00:00"})
        row = self.conn.execute("SELECT id FROM reply_outbox ORDER BY id DESC LIMIT 1").fetchone()
        self.assertIsNotNone(row)
        return rid, self.outbox(row["id"])

    def note(self, number=12):
        rid = store.insert_message(self.conn, {
            "chat_id": 1, "tg_message_id": number, "received_at": store._now(),
            "raw_text": "Synthetic owner's source"})
        store.ensure_category(self.conn, "Books")
        store.set_suggestion(self.conn, rid, "Books", "Synthetic summary", "synthetic")
        store.confirm_category(self.conn, rid, "Books")
        return rid, store.get_message(self.conn, rid)["note_no"]

    def undo(self):
        self.scope("undo last action", update=901)
        self.delivery = "sent"
        result = self.agent.do_action_undo(1, "en")
        self.assertEqual(result["status"], "undone")
        return result

    def assert_superseded(self, before):
        after = self.outbox(before["id"])
        self.assertEqual(after["state"], "failed")
        self.assertTrue(after["error"].startswith("superseded_by_action_undo:"))
        self.assertIsNotNone(after["finished_at"])
        for field in ("delivery_key", "action_key", "source_update", "method",
                      "params_json", "metadata_json", "record_conversation",
                      "result_json", "attempts", "created_at"):
            self.assertEqual(after[field], before[field], field)

    def make_due(self):
        self.conn.execute("UPDATE reply_outbox SET available_at='2000-01-01'")
        self.conn.commit()

    def test_reminder_undo_stops_stale_retry_preserves_receipt_and_other_compound_output(self):
        rid, original = self.reminder_action()
        self.assertEqual((original["state"], original["attempts"]), ("pending", 1))
        receipts = self.receipts()
        self.assertIn(original["action_key"], receipts)
        # Same physical incoming update, different fragment/output. Binding by
        # source_update alone would incorrectly suppress this unrelated reply.
        self.scope("fragment:1:unrelated status")
        self.agent.reply(1, "Unrelated compound status")
        unrelated = self.outbox(self.conn.execute(
            "SELECT MAX(id) FROM reply_outbox").fetchone()[0])
        self.assertEqual(unrelated["source_update"], original["source_update"])
        self.assertNotEqual(unrelated["delivery_key"], original["delivery_key"])
        self.undo()
        self.assertEqual(store.reminder_get(self.conn, rid)["due_utc"],
                         "2099-01-02T09:00:00+00:00")
        self.assert_superseded(original)
        self.assertEqual(self.outbox(unrelated["id"]), unrelated)
        self.assert_receipts_retained(receipts)
        self.make_due()
        self.sent.clear()
        action_delivery.drain(self.agent, self.send, limit=20)
        self.assertEqual([p["text"] for method, p in self.sent if method == "sendMessage"],
                         ["Unrelated compound status"])
        # The original update can still find its immutable action receipt;
        # replay is delivery-only and cannot revive a superseded success.
        self.sent.clear()
        action_delivery.deliver_action(self.agent, original["action_key"])
        self.assertEqual(self.sent, [])

    def test_note_lifecycle_undo_supersedes_its_unsent_reply_only(self):
        rid, number = self.note()
        self.scope("archive note")
        self.delivery = "pending"
        self.agent.do_note_lifecycle(1, "en", {"id": number, "operation": "archive"})
        original = self.outbox(self.conn.execute("SELECT MAX(id) FROM reply_outbox").fetchone()[0])
        self.assertEqual(original["state"], "pending")
        self.assertEqual(store.get_message(self.conn, rid)["knowledge_state"], "archived")
        self.scope("another fragment with the same update")
        self.agent.reply(1, "Unrelated note status")
        unrelated = self.outbox(self.conn.execute("SELECT MAX(id) FROM reply_outbox").fetchone()[0])
        receipts = self.receipts()
        self.undo()
        self.assertEqual(store.get_message(self.conn, rid)["knowledge_state"], "active")
        self.assert_superseded(original)
        self.assertEqual(self.outbox(unrelated["id"]), unrelated)
        self.assert_receipts_retained(receipts)
        self.make_due()
        self.sent.clear()
        action_delivery.drain(self.agent, self.send, limit=20)
        self.assertEqual([p["text"] for method, p in self.sent if method == "sendMessage"],
                         ["Unrelated note status"])

    def test_recategorize_undo_stops_queued_category_claim_without_erasing_it(self):
        rid, number = self.note()
        self.scope("move note to Ideas")
        self.delivery = "pending"
        self.agent.do_recategorize(1, "en", {"id": number, "category": "Ideas"})
        original = self.outbox(self.conn.execute("SELECT MAX(id) FROM reply_outbox").fetchone()[0])
        self.assertEqual(original["state"], "pending")
        self.assertEqual(store.get_message(self.conn, rid)["category"], "Ideas")
        self.undo()
        self.assertEqual(store.get_message(self.conn, rid)["category"], "Books")
        self.assert_superseded(original)
        self.make_due()
        self.sent.clear()
        action_delivery.drain(self.agent, self.send, limit=20)
        self.assertEqual(self.sent, [])

    def test_bulk_archive_confirmation_binds_unsent_result_after_card_is_consumed(self):
        first, first_no = self.note(12)
        second, second_no = self.note(13)
        self.scope("open bulk archive card", update=890)
        self.agent.do_note_lifecycle(1, "en", {
            "ids": [first_no, second_no], "operation": "archive"})
        pending = store.pending_get(self.conn, 1)
        self.assertEqual(pending["kind"], "note_archive")
        delivered_card = self.outbox(self.conn.execute(
            "SELECT MAX(id) FROM reply_outbox").fetchone()[0])
        self.assertEqual(delivered_card["state"], "sent")
        self.scope("confirm bulk archive", update=900)
        self.delivery = "pending"
        self.agent.resolve_pending(1, "confirm", {}, pending, "en")
        original = self.outbox(self.conn.execute(
            "SELECT MAX(id) FROM reply_outbox").fetchone()[0])
        self.assertEqual((original["state"], original["attempts"]), ("pending", 1))
        self.assertIsNone(store.pending_get(self.conn, 1))
        self.assertEqual([store.get_message(self.conn, rid)["knowledge_state"]
                          for rid in (first, second)], ["archived", "archived"])
        self.undo()
        self.assertEqual([store.get_message(self.conn, rid)["knowledge_state"]
                          for rid in (first, second)], ["active", "active"])
        self.assert_superseded(original)
        self.assertEqual(self.outbox(delivered_card["id"]), delivered_card)
        self.make_due()
        self.sent.clear()
        action_delivery.drain(self.agent, self.send, limit=20)
        self.assertEqual(self.sent, [])

    def test_sent_original_delivery_is_preserved_exactly(self):
        _rid, original = self.reminder_action(delivery="sent")
        self.assertEqual(original["state"], "sent")
        receipts = self.receipts()
        self.undo()
        self.assertEqual(self.outbox(original["id"]), original)
        self.assert_receipts_retained(receipts)

    def test_ambiguous_original_delivery_is_preserved_and_never_retried(self):
        _rid, original = self.reminder_action(delivery="ambiguous")
        self.assertEqual(original["state"], "ambiguous")
        receipts = self.receipts()
        self.undo()
        self.assertEqual(self.outbox(original["id"]), original)
        self.assert_receipts_retained(receipts)
        self.make_due()
        self.sent.clear()
        action_delivery.drain(self.agent, self.send, limit=20)
        self.assertEqual(self.sent, [])

    def test_sending_original_is_not_reclassified_by_undo(self):
        _rid, queued = self.reminder_action()
        # A sender may have crossed the network boundary. Undo cannot turn an
        # unobserved outcome into definitely unsent or grant a retry.
        self.conn.execute("UPDATE reply_outbox SET state='sending',error=NULL WHERE id=?", (queued["id"],))
        self.conn.commit()
        original = self.outbox(queued["id"])
        self.undo()
        self.assertEqual(self.outbox(original["id"]), original)

    def test_note_queued_before_transport_death_remains_bound_after_restart(self):
        rid, number = self.note()
        self.scope("archive note before transport")

        def stop_before_delivery(agent, row, sender):
            # Independent read proves the association is durable before any
            # transport/finally path can complete, rather than only in memory.
            observer = store.open_db(self.cfg.db_path)
            try:
                record = json.loads(store.kv_get(observer, "action_undo:1"))
                self.assertIn(row["id"], record["outbox_ids"])
                self.assertEqual(observer.execute(
                    "SELECT state FROM reply_outbox WHERE id=?", (row["id"],)).fetchone()[0], "pending")
            finally:
                observer.close()
            raise ProcessDeath()

        with mock.patch.object(action_delivery, "deliver", side_effect=stop_before_delivery):
            with self.assertRaises(ProcessDeath):
                self.agent.do_note_lifecycle(1, "en", {"id": number, "operation": "archive"})
        original = self.outbox(self.conn.execute("SELECT MAX(id) FROM reply_outbox").fetchone()[0])
        self.assertEqual((original["state"], original["attempts"]), ("pending", 0))
        self.agent.conn.close()
        self.agent = entry.Agent(self.cfg)
        self.undo()
        self.assertEqual(store.get_message(self.conn, rid)["knowledge_state"], "active")
        self.assert_superseded(original)
        self.make_due()
        self.sent.clear()
        action_delivery.drain(self.agent, self.send, limit=20)
        self.assertEqual(self.sent, [])

    def test_conflicted_inverse_does_not_supersede_original_pending_output(self):
        rid, original = self.reminder_action()
        store.reminder_rename(self.conn, rid, "Intervening target edit")
        self.scope("undo last action", update=901)
        self.delivery = "sent"
        result = self.agent.do_action_undo(1, "en")
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(self.outbox(original["id"]), original)

    def test_failed_inverse_rolls_back_state_and_supersession_together(self):
        rid, original = self.reminder_action()
        record = store.kv_get(self.conn, "action_undo:1")
        receipts = self.receipts()
        self.scope("undo last action", update=901)
        self.delivery = "sent"
        with mock.patch.object(events, "record_done", side_effect=RuntimeError("undo audit failed")):
            with self.assertRaisesRegex(RuntimeError, "undo audit failed"):
                self.agent.do_action_undo(1, "en")
        self.assertEqual(store.reminder_get(self.conn, rid)["due_utc"],
                         "2099-01-04T09:00:00+00:00")
        self.assertEqual(self.outbox(original["id"]), original)
        self.assertEqual(store.kv_get(self.conn, "action_undo:1"), record)
        self.assertEqual(self.receipts(), receipts)


if __name__ == "__main__":
    unittest.main()
