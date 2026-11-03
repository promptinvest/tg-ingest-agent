"""Pending confirmations preserve card ownership, cancellation and fallthrough."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import llm
import store
import tg_ingest_agent as entry
from testlib import make_config
from texts import T


class PendingResolverTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.agent = entry.Agent(make_config(
            ALLOWED_CHAT_IDS="1", DB_PATH=":memory:",
            MEDIA_DIR=str(Path(scratch.name) / "media")))
        self.addCleanup(self.agent.conn.close)
        self.reply = self.patch(self.agent, "reply")
        self.patch(llm, "chat_profile", side_effect=AssertionError("no model calls"))
        self.patch(llm, "embed", side_effect=AssertionError("no embeddings"))
        self.patch(entry, "tg_call", side_effect=AssertionError("no Telegram"))

    def patch(self, target, name, **kwargs):
        patcher = mock.patch.object(target, name, **kwargs)
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def pending(self, kind, payload=None):
        store.pending_set(self.agent.conn, 1, kind, payload or {})
        return store.pending_get(self.agent.conn, 1)

    def resolve(self, pending, action="confirm", params=None):
        self.agent.resolve_pending(1, action, params or {}, pending, "en")

    def test_table_covers_current_resolution_kinds(self):
        self.assertEqual(set(getattr(entry, "PENDING_RESOLVERS", {})), {
            "journal_correction", "category", "media_capture", "reminder",
            "reminder_fired", "reminder_expired", "note_archive", "delete",
            "habit", "boss_sensitive", "note_edit", "note_retitle", "journal_prompt",
        })

    def test_missing_pending_replies_without_clearing_an_unrelated_slot(self):
        pending = self.pending("habit", {"source_chat_id": 2, "category": "Books"})
        self.resolve(None)
        self.assertEqual(store.pending_get(self.agent.conn, 1), pending)
        self.reply.assert_called_once_with(1, T("en", "nothing_pending"))

    def test_unknown_kind_still_clears_without_a_confirmation_reply(self):
        self.resolve(self.pending("future_kind"))
        self.assertIsNone(store.pending_get(self.agent.conn, 1))
        self.reply.assert_not_called()

    def test_unknown_kind_cancel_still_uses_generic_cancellation(self):
        self.resolve(self.pending("future_kind"), "cancel")
        self.assertIsNone(store.pending_get(self.agent.conn, 1))
        self.reply.assert_called_once_with(1, T("en", "cancelled"))

    def test_journal_correction_cancel_precedes_generic_cancellation(self):
        pending = self.pending("journal_correction", {"date": "2099-01-01"})
        boundary = self.patch(self.agent, "resolve_journal_correction", return_value=True)
        self.agent.turn_reply_message_id = 42
        self.resolve(pending, "cancel")
        boundary.assert_called_once_with(1, "en", pending, "no", card_id=42)
        self.assertEqual(store.pending_get(self.agent.conn, 1), pending)
        self.reply.assert_not_called()

    def test_journal_correction_amend_keeps_card_and_requests_date(self):
        pending = self.pending("journal_correction")
        boundary = self.patch(self.agent, "resolve_journal_correction")
        self.resolve(pending, "amend")
        boundary.assert_not_called()
        self.assertEqual(store.pending_get(self.agent.conn, 1), pending)
        self.reply.assert_called_once_with(1, T("en", "journal_date_needed"))

    def test_wrong_note_card_cancel_does_not_clear_staged_edit(self):
        pending = self.pending("note_edit", {"row_id": 17})
        store.kv_set(self.agent.conn, "note_edit:17", "staged words")
        boundary = self.patch(self.agent, "reject_note_confirmation_reply", return_value=True)
        self.resolve(pending, "cancel")
        boundary.assert_called_once_with(1, "en", pending)
        self.assertEqual(store.kv_get(self.agent.conn, "note_edit:17"), "staged words")
        self.assertEqual(store.pending_get(self.agent.conn, 1), pending)
        self.reply.assert_not_called()

    def test_note_edit_cancel_erases_staged_words(self):
        pending = self.pending("note_edit", {"row_id": 17})
        store.kv_set(self.agent.conn, "note_edit:17", "staged words")
        self.patch(self.agent, "reject_note_confirmation_reply", return_value=False)
        self.resolve(pending, "cancel")
        self.assertEqual(store.kv_get(self.agent.conn, "note_edit:17"), "")
        self.assertIsNone(store.pending_get(self.agent.conn, 1))
        self.reply.assert_called_once_with(1, T("en", "cancelled"))

    def test_wrong_media_card_cancel_does_not_retire_stash(self):
        pending = self.pending("media_capture")
        self.patch(self.agent, "reject_media_confirmation_reply", return_value=True)
        clear = self.patch(self.agent, "_media_clear")
        self.resolve(pending, "cancel")
        clear.assert_not_called()
        self.assertEqual(store.pending_get(self.agent.conn, 1), pending)

    def test_media_cancel_retires_stash_before_clearing_slot(self):
        pending = self.pending("media_capture")
        self.patch(self.agent, "reject_media_confirmation_reply", return_value=False)
        seen = []
        clear = self.patch(self.agent, "_media_clear", side_effect=lambda chat_id:
                           seen.append(store.pending_get(self.agent.conn, chat_id)))
        self.resolve(pending, "cancel")
        clear.assert_called_once_with(1)
        self.assertEqual(seen, [pending])
        self.assertIsNone(store.pending_get(self.agent.conn, 1))

    def test_habit_amend_preserves_existing_decline_semantics(self):
        self.resolve(self.pending("habit", {"source_chat_id": 2, "category": "Books"}), "amend")
        self.assertEqual(store.pref_get(self.agent.conn, "auto_cat_declined:2"), "1")
        self.assertIsNone(store.pref_get(self.agent.conn, "auto_cat:2"))
        self.assertIsNone(store.pending_get(self.agent.conn, 1))
        self.reply.assert_not_called()

    def test_sensitive_memory_amend_does_not_save_or_reply(self):
        remember = self.patch(entry.boss_model, "remember_explicit")
        self.resolve(self.pending("boss_sensitive", {"value": "synthetic", "kind": "fact"}), "amend")
        remember.assert_not_called()
        self.assertIsNone(store.pending_get(self.agent.conn, 1))
        self.reply.assert_not_called()
