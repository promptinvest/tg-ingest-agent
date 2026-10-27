"""An older edit card never authorizes newer staged source text."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import store
import tg_ingest_agent as entry
from testlib import make_config


class NoteSourceEditBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.agent = entry.Agent(make_config(ALLOWED_CHAT_IDS="1", BOT_LANGUAGE="en", DB_PATH=":memory:",
                                             MEDIA_DIR=str(Path(self.temp.name) / "media")))
        self.addCleanup(self.agent.conn.close)
        self.sequence = 100
        def send(*args, **kwargs):
            self.sequence += 1
            return {"message_id": self.sequence}
        self.transport = mock.patch.object(entry, "tg_call", side_effect=send).start()
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(self.agent, "index_message").start()
        self.rid = store.insert_message(self.agent.conn, {"chat_id": 1, "tg_message_id": 10,
            "received_at": store._now(), "raw_text": "Original saved text"})
        store.set_suggestion(self.agent.conn, self.rid, "Books", "Saved title", "synthetic")
        store.confirm_category(self.agent.conn, self.rid, "Books")

    def offer(self, text):
        self.agent.offer_note_edit(store.get_message(self.agent.conn, self.rid), {"text": text}, text)
        return self.sequence

    def raw(self):
        return store.get_message(self.agent.conn, self.rid)["raw_text"]

    def staged(self):
        return store.kv_get(self.agent.conn, f"note_edit:{self.rid}")

    def test_old_accept_does_not_apply_a_newer_staged_source(self):
        old = self.offer("First offered replacement")
        self.offer("Different replacement never approved by old card")
        before = self.staged()
        self.agent.handle_note_edit_callback("old", 1, {"message_id": old}, f"ne|{self.rid}|y")
        self.assertEqual(self.raw(), "Original saved text")
        self.assertEqual(self.staged(), before)
        self.assertIsNotNone(store.pending_get(self.agent.conn, 1))

    def test_old_decline_does_not_discard_newer_staged_source(self):
        old = self.offer("First offered replacement")
        self.offer("Different replacement")
        before = self.staged()
        self.agent.handle_note_edit_callback("old", 1, {"message_id": old}, f"ne|{self.rid}|n")
        self.assertEqual(self.staged(), before)
        self.assertIsNotNone(store.pending_get(self.agent.conn, 1))

    def test_explicit_reply_to_old_card_cannot_confirm_newer_source(self):
        old = self.offer("First offered replacement")
        self.offer("Different replacement")
        self.agent.turn_reply_message_id = old
        before = self.staged()
        self.agent.resolve_pending(1, "confirm", {}, store.pending_get(self.agent.conn, 1), "en")
        self.assertEqual(self.raw(), "Original saved text")
        self.assertEqual(self.staged(), before)

    def test_intervening_saved_source_change_invalidates_old_offer(self):
        card = self.offer("Offered replacement")
        store.message_update_raw_text(self.agent.conn, self.rid, "New independently saved text")
        self.agent.handle_note_edit_callback("current", 1, {"message_id": card}, f"ne|{self.rid}|y")
        self.assertEqual(self.raw(), "New independently saved text")

    def test_current_card_can_still_apply_source_once(self):
        card = self.offer("Approved replacement")
        self.agent.handle_note_edit_callback("current", 1, {"message_id": card}, f"ne|{self.rid}|y")
        self.agent.handle_note_edit_callback("duplicate", 1, {"message_id": card}, f"ne|{self.rid}|y")
        self.assertEqual(self.raw(), "Approved replacement")
        self.assertFalse(self.staged())
        self.assertEqual(self.agent.conn.execute("SELECT COUNT(*) FROM note_outcomes WHERE event='note_edited'").fetchone()[0], 1)

    def test_unrecognized_button_cannot_discard_staged_text(self):
        card = self.offer("Offered replacement")
        before = self.staged()
        self.agent.handle_note_edit_callback("invalid", 1, {"message_id": card}, f"ne|{self.rid}|other")
        self.assertEqual(self.staged(), before)

    def test_undelivered_new_offer_cannot_be_approved_by_a_bare_yes(self):
        self.offer("First offered replacement")
        with mock.patch.object(self.agent, "reply", return_value=None):
            self.offer("Different undelivered replacement")
        self.agent.resolve_pending(1, "confirm", {}, store.pending_get(self.agent.conn, 1), "en")
        self.assertEqual(self.raw(), "Original saved text")


if __name__ == "__main__":
    unittest.main()
