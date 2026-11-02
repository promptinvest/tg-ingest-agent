"""Latest-action inverses preserve exact targets, history, and old undo APIs."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import action_delivery
import store


class ActionUndoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cara-action-undo-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state.db"
        self.conn = store.open_db(self.path)
        self.addCleanup(lambda: self.conn.close())

    def reminder(self, title="Original", due="2099-01-02T09:00:00+00:00", chat_id=1):
        return store.reminder_add(self.conn, chat_id, title, due)

    def note(self, number=1, category="Books"):
        rid = store.insert_message(self.conn, {
            "chat_id": 1, "tg_message_id": number, "received_at": store._now(),
            "raw_text": "Owner's original source",
        })
        name = store.ensure_category(self.conn, category)
        store.set_suggestion(self.conn, rid, name, "Original summary", "synthetic")
        store.confirm_category(self.conn, rid, name)
        return rid

    def cancel(self, *ids):
        with action_delivery.local_transaction(self.conn):
            with store.action_undo_group(self.conn, 1, "reminder_cancel"):
                for rid in ids:
                    store.reminder_close(self.conn, rid, "cancelled")

    def latest(self):
        return json.loads(store.kv_get(self.conn, "action_undo:1"))

    def handler(self):
        import tg_ingest_agent as entry
        from testlib import make_config
        self.conn.close()
        agent = entry.Agent(make_config(ALLOWED_CHAT_IDS="1", BOT_LANGUAGE="en",
                                       DB_PATH=str(self.path), MEDIA_DIR=str(self.path.parent / "media")))
        self.conn = agent.conn
        return agent

    def test_cancel_inverse_survives_reopen_and_is_consumed_once(self):
        rid = self.reminder()
        before = dict(store.reminder_get(self.conn, rid))
        self.cancel(rid)
        self.conn.close()
        self.conn = store.open_db(self.path)
        self.assertEqual(store.action_undo_latest(self.conn, 1),
                         {"status": "undone", "action": "reminder_cancel", "count": 1})
        self.assertEqual(dict(store.reminder_get(self.conn, rid)), before)
        self.assertEqual(store.action_undo_latest(self.conn, 1)["status"], "missing")
        self.assertEqual([r[0] for r in self.conn.execute(
            "SELECT event FROM reminder_events WHERE reminder_id=? ORDER BY id", (rid,))],
            ["created", "closed", "action_undone"])

    def test_owner_chat_cannot_undo_another_chats_action(self):
        rid = self.reminder()
        self.cancel(rid)
        self.assertEqual(store.action_undo_latest(self.conn, 2)["status"], "missing")
        self.assertEqual(store.reminder_get(self.conn, rid)["status"], "cancelled")

    def test_exact_expiry_boundary_refuses_without_consuming_or_mutating(self):
        rid = self.reminder()
        self.cancel(rid)
        record = self.latest()
        before = dict(store.reminder_get(self.conn, rid))
        result = store.action_undo_latest(self.conn, 1,
            now=datetime.fromisoformat(record["expires_at"]))
        self.assertEqual(result["status"], "expired")
        self.assertEqual(dict(store.reminder_get(self.conn, rid)), before)
        self.assertEqual(self.latest(), record)

    def test_before_expiry_boundary_succeeds(self):
        rid = self.reminder()
        self.cancel(rid)
        stamp = datetime.fromisoformat(self.latest()["expires_at"]) - timedelta(microseconds=1)
        self.assertEqual(store.action_undo_latest(self.conn, 1, now=stamp)["status"], "undone")

    def test_batch_conflict_is_all_or_none(self):
        first, second = self.reminder("First"), self.reminder("Second")
        self.cancel(first, second)
        store.reminder_rename(self.conn, second, "Intervening owner edit")
        before = [dict(store.reminder_get(self.conn, rid)) for rid in (first, second)]
        self.assertEqual(store.action_undo_latest(self.conn, 1)["status"], "conflict")
        self.assertEqual([dict(store.reminder_get(self.conn, rid)) for rid in (first, second)], before)

    def test_batch_inverse_uses_original_ids_after_active_list_renumbers(self):
        first, second = self.reminder("First"), self.reminder("Second")
        self.cancel(first, second)
        other = self.reminder("Unrelated newly active reminder")
        result = store.action_undo_latest(self.conn, 1)
        self.assertEqual(result["count"], 2)
        self.assertEqual([store.reminder_get(self.conn, rid)["title"]
                          for rid in (first, second, other)],
                         ["First", "Second", "Unrelated newly active reminder"])

    def test_cancelled_delivered_alarm_restores_firing_marker_without_refire(self):
        due = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        rid = self.reminder(due=due)
        store.reminder_touch_fired(self.conn, rid)
        before = dict(store.reminder_get(self.conn, rid))
        self.cancel(rid)
        self.assertEqual(store.action_undo_latest(self.conn, 1)["status"], "undone")
        self.assertEqual(dict(store.reminder_get(self.conn, rid)), before)
        self.assertNotIn(rid, [r["id"] for r in store.reminders_due(
            self.conn, datetime.now(timezone.utc).isoformat())])

    def test_cancel_inverse_cannot_rearm_an_unfired_overdue_alarm(self):
        due = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        rid = self.reminder(due=due)
        self.cancel(rid)
        self.assertEqual(store.action_undo_latest(self.conn, 1)["status"], "past_due")
        self.assertEqual(store.reminder_get(self.conn, rid)["status"], "cancelled")

    def test_reschedule_inverse_restores_all_previous_timing_fields(self):
        rid = self.reminder()
        store.reminder_update_due(self.conn, rid, "2099-01-03T09:00:00+00:00")
        before = dict(store.reminder_get(self.conn, rid))
        with store.action_undo_group(self.conn, 1, "reminder_reschedule"):
            store.reminder_update_due(self.conn, rid, "2099-01-04T09:00:00+00:00")
        self.assertEqual(store.action_undo_latest(self.conn, 1)["status"], "undone")
        self.assertEqual(dict(store.reminder_get(self.conn, rid)), before)

    def test_existing_named_reminder_undo_still_toggles_and_invalidates_generic_snapshot(self):
        rid = self.reminder()
        with store.action_undo_group(self.conn, 1, "reminder_reschedule"):
            store.reminder_update_due(self.conn, rid, "2099-01-04T09:00:00+00:00")
        self.assertEqual(store.reminder_restore_due(self.conn, rid), "2099-01-02T09:00:00+00:00")
        self.assertEqual(store.reminder_restore_due(self.conn, rid), "2099-01-04T09:00:00+00:00")
        self.assertEqual(store.action_undo_latest(self.conn, 1)["status"], "conflict")

    def test_rename_inverse_and_history_survive(self):
        rid = self.reminder()
        with store.action_undo_group(self.conn, 1, "reminder_rename"):
            store.reminder_rename(self.conn, rid, "New title")
        self.assertEqual(store.action_undo_latest(self.conn, 1)["status"], "undone")
        self.assertEqual(store.reminder_get(self.conn, rid)["title"], "Original")
        self.assertTrue(store.reminder_has_event(self.conn, rid, "renamed"))

    def test_latest_action_does_not_fall_back_to_an_older_one(self):
        first, second = self.reminder("First"), self.reminder("Second")
        self.cancel(first)
        with store.action_undo_group(self.conn, 1, "reminder_rename"):
            store.reminder_rename(self.conn, second, "Changed")
        self.assertEqual(store.action_undo_latest(self.conn, 1)["status"], "undone")
        self.assertEqual(store.action_undo_latest(self.conn, 1)["status"], "missing")
        self.assertEqual(store.reminder_get(self.conn, first)["status"], "cancelled")

    def test_noop_does_not_replace_actual_last_action(self):
        rid = self.reminder()
        self.cancel(rid)
        record = self.latest()
        with store.action_undo_group(self.conn, 1, "reminder_rename"):
            store.reminder_rename(self.conn, rid, "Original")
        self.assertEqual(self.latest(), record)

    def test_capture_failure_rolls_back_actual_mutation_together_with_record(self):
        rid = self.reminder()
        before = dict(store.reminder_get(self.conn, rid))
        with self.assertRaisesRegex(RuntimeError, "capture failure"):
            with store.action_undo_group(self.conn, 1, "reminder_rename"):
                with mock.patch.object(store, "_action_undo_hash", side_effect=RuntimeError("capture failure")):
                    store.reminder_rename(self.conn, rid, "Must roll back")
        self.assertEqual(dict(store.reminder_get(self.conn, rid)), before)
        self.assertIsNone(store.kv_get(self.conn, "action_undo:1"))

    def test_note_lifecycle_restores_exact_prior_metadata_and_preserves_source(self):
        rid = self.note()
        store.note_set_review(self.conn, rid, "2099-04-01T12:00:00+00:00")
        store.note_make_temporary(self.conn, rid, "2099-05-01T12:00:00+00:00")
        before = dict(store.get_message(self.conn, rid))
        with store.action_undo_group(self.conn, 1, "note_lifecycle"):
            store.note_archive(self.conn, rid, "archived by boss")
        self.assertEqual(store.action_undo_latest(self.conn, 1)["status"], "undone")
        self.assertEqual(dict(store.get_message(self.conn, rid)), before)

    def test_note_source_edit_blocks_inverse(self):
        rid = self.note()
        with store.action_undo_group(self.conn, 1, "note_lifecycle"):
            store.note_archive(self.conn, rid, "archived by boss")
        self.conn.execute("UPDATE messages SET raw_text='New owner source' WHERE id=?", (rid,))
        self.conn.commit()
        self.assertEqual(store.action_undo_latest(self.conn, 1)["status"], "conflict")
        self.assertEqual(store.get_message(self.conn, rid)["knowledge_state"], "archived")

    def test_recategorize_inverse_restores_lifecycle_and_exact_dated_journal_entry(self):
        store.set_category_kind(self.conn, "Diary", "journal")
        definition = store.journal_def_ensure(self.conn, "test-diary", "Diary", "freeform", "Diary")
        rid = self.note(category="Diary")
        # Keep exact validated content, source date, extraction state and row id.
        self.conn.execute("UPDATE journal_entries SET occurred_at=?,payload_json=?,extraction_status=? WHERE message_id=?",
                          ("2026-01-04T11:00:00+00:00", '{"people":["Ada"],"subject":"Original"}', "complete", rid))
        self.conn.commit()
        before = dict(store.get_message(self.conn, rid))
        entry = dict(store.journal_entry_get(self.conn, rid))
        self.assertEqual(entry["journal_id"], definition["id"])
        with store.action_undo_group(self.conn, 1, "recategorize"):
            store.confirm_category(self.conn, rid, "Books")
        issued_no = store.get_message(self.conn, rid)["note_no"]
        self.assertIsNone(store.journal_entry_get(self.conn, rid))
        self.assertEqual(store.action_undo_latest(self.conn, 1)["status"], "undone")
        # A number issued during the move remains issued, never reused/removed.
        before["note_no"] = issued_no
        self.assertEqual(dict(store.get_message(self.conn, rid)), before)
        self.assertEqual(dict(store.journal_entry_get(self.conn, rid)), entry)

    def test_recategorize_between_journals_restores_original_membership(self):
        for category in ("Diary", "Other diary"):
            store.set_category_kind(self.conn, category, "journal")
            store.journal_def_ensure(self.conn, category, category, "freeform", category)
        rid = self.note(category="Diary")
        before = dict(store.journal_entry_get(self.conn, rid))
        with store.action_undo_group(self.conn, 1, "recategorize"):
            store.confirm_category(self.conn, rid, "Other diary", journal_payload={"subject": "Changed"})
        self.assertEqual(store.action_undo_latest(self.conn, 1)["status"], "undone")
        self.assertEqual(dict(store.journal_entry_get(self.conn, rid)), before)

    def test_changed_journal_definition_refuses_without_partial_restoration(self):
        store.set_category_kind(self.conn, "Diary", "journal")
        store.journal_def_ensure(self.conn, "test-diary", "Diary", "freeform", "Diary")
        rid = self.note(category="Diary")
        with store.action_undo_group(self.conn, 1, "recategorize"):
            store.confirm_category(self.conn, rid, "Books")
        store.journal_def_update(self.conn, "test-diary", active=0)
        self.assertEqual(store.action_undo_latest(self.conn, 1)["status"], "conflict")
        self.assertEqual(store.get_message(self.conn, rid)["category"], "Books")
        self.assertIsNone(store.journal_entry_get(self.conn, rid))

    def test_undo_failure_rolls_back_restored_state_and_retains_inverse(self):
        rid = self.reminder()
        self.cancel(rid)
        record = self.latest()
        with self.assertRaisesRegex(RuntimeError, "history failure"):
            with mock.patch.object(store, "reminder_event", side_effect=RuntimeError("history failure")):
                store.action_undo_latest(self.conn, 1)
        self.assertEqual(store.reminder_get(self.conn, rid)["status"], "cancelled")
        self.assertEqual(self.latest(), record)

    def test_fired_one_shot_close_and_snooze_capture_deterministic_mutations(self):
        agent = self.handler()
        for action, params in (("confirm", {}), ("amend", {"due_utc": "2099-01-08T09:00:00+00:00"})):
            with self.subTest(action=action):
                rid = self.reminder(due=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())
                store.reminder_touch_fired(self.conn, rid)
                before = dict(store.reminder_get(self.conn, rid))
                agent.resolve_fired_action(1, "en", {"reminder_id": rid, "title": "Original"}, action, params)
                self.assertEqual(store.action_undo_latest(self.conn, 1)["status"], "undone")
                self.assertEqual(dict(store.reminder_get(self.conn, rid)), before)

    def test_recurring_snooze_inverse_cancels_only_created_echo_and_retains_history(self):
        agent = self.handler()
        rid = store.reminder_add(self.conn, 1, "Daily review", "2099-01-08T09:00:00+00:00", "daily")
        store.reminder_touch_fired(self.conn, rid)
        before = dict(store.reminder_get(self.conn, rid))
        agent.resolve_fired_action(1, "en", {"reminder_id": rid, "title": "Daily review"},
                                   "amend", {"due_utc": "2099-01-08T12:00:00+00:00"})
        target = self.latest()["targets"][0]
        self.assertTrue(target["created"])
        self.assertNotEqual(target["id"], rid)
        self.assertEqual(store.action_undo_latest(self.conn, 1)["status"], "undone")
        self.assertEqual(dict(store.reminder_get(self.conn, rid)), before)
        self.assertEqual(store.reminder_get(self.conn, target["id"])["status"], "cancelled")
        self.assertTrue(store.reminder_has_event(self.conn, target["id"], "created"))
        self.assertTrue(store.reminder_has_event(self.conn, target["id"], "closed"))
        self.assertTrue(store.reminder_has_event(self.conn, rid, "snoozed"))

    def test_created_echo_cannot_be_undone_after_firing(self):
        agent = self.handler()
        rid = store.reminder_add(self.conn, 1, "Daily review", "2099-01-08T09:00:00+00:00", "daily")
        agent.resolve_fired_action(1, "en", {"reminder_id": rid, "title": "Daily review"},
                                   "amend", {"due_utc": "2099-01-08T12:00:00+00:00"})
        echo = self.latest()["targets"][0]["id"]
        store.reminder_touch_fired(self.conn, echo)
        self.assertEqual(store.action_undo_latest(self.conn, 1)["status"], "conflict")
        self.assertEqual(store.reminder_get(self.conn, echo)["status"], "active")

    def test_recurring_acknowledgement_prevents_undo_of_older_unrelated_action(self):
        agent = self.handler()
        older = self.reminder("Older unrelated action")
        self.cancel(older)
        rid = store.reminder_add(self.conn, 1, "Daily review", "2099-01-08T09:00:00+00:00", "daily")
        before = dict(store.reminder_get(self.conn, rid))
        agent.resolve_fired_action(1, "en", {"reminder_id": rid, "title": "Daily review"}, "confirm", {})
        self.assertEqual(store.action_undo_latest(self.conn, 1)["status"], "missing")
        self.assertEqual(store.reminder_get(self.conn, older)["status"], "cancelled")
        self.assertEqual(dict(store.reminder_get(self.conn, rid)), before)

    def test_mixed_fired_batch_does_not_recreate_inverse_after_unsupported_ack(self):
        agent = self.handler()
        first, last = self.reminder("First"), self.reminder("Last")
        recurring = store.reminder_add(self.conn, 1, "Daily", "2099-01-08T09:00:00+00:00", "daily")
        agent.resolve_fired_action(1, "en", {"reminder_ids": [first, recurring, last],
                                   "titles": ["First", "Daily", "Last"]}, "confirm", {})
        self.assertEqual(store.action_undo_latest(self.conn, 1)["status"], "missing")
        self.assertEqual(store.reminder_get(self.conn, first)["status"], "done")
        self.assertEqual(store.reminder_get(self.conn, last)["status"], "done")

    def test_legacy_malformed_due_refuses_without_crash_or_mutation(self):
        for due in ("not-an-ISO-date", "2026-11-02T09:00:00", "99999999999999"):
            with self.subTest(due=due):
                rid = self.reminder(due=due)
                self.cancel(rid)
                before = dict(store.reminder_get(self.conn, rid))
                self.assertEqual(store.action_undo_latest(self.conn, 1)["status"], "invalid")
                self.assertEqual(dict(store.reminder_get(self.conn, rid)), before)

    def test_malformed_record_refuses_before_any_target_is_restored(self):
        rid = self.reminder()
        self.cancel(rid)
        record = self.latest()
        for corrupt in ([], {"action": "not_allowed"}, dict(record, targets=[{"id": rid}]),
                        dict(record, expires_at="bad-date"), dict(record, outbox_ids=[True])):
            with self.subTest(record=corrupt):
                store.kv_set(self.conn, "action_undo:1", json.dumps(corrupt))
                self.assertEqual(store.action_undo_latest(self.conn, 1)["status"], "invalid")
                self.assertEqual(store.reminder_get(self.conn, rid)["status"], "cancelled")

    def test_original_category_becoming_journal_blocks_recategorize_inverse(self):
        rid = self.note(category="Books")
        store.ensure_category(self.conn, "Other")
        with store.action_undo_group(self.conn, 1, "recategorize"):
            store.confirm_category(self.conn, rid, "Other")
        store.set_category_kind(self.conn, "Books", "journal")
        self.assertEqual(store.action_undo_latest(self.conn, 1)["status"], "conflict")
        self.assertEqual(store.get_message(self.conn, rid)["category"], "Other")

    def test_source_deletion_removes_matching_inverse_but_preserves_unrelated_record(self):
        first, other = self.note(1), self.note(2)
        with store.action_undo_group(self.conn, 1, "note_lifecycle"):
            store.note_archive(self.conn, first, "archived by boss")
        record = self.latest()
        store.delete_message(self.conn, other)
        self.assertEqual(self.latest(), record)
        store.delete_message(self.conn, first)
        self.assertIsNone(store.kv_get(self.conn, "action_undo:1"))

    def test_message_and_reminder_purges_clear_own_inverse_content(self):
        rid = self.note()
        with store.action_undo_group(self.conn, 1, "note_lifecycle"):
            store.note_archive(self.conn, rid, "private lifecycle detail")
        store.purge_execute(self.conn, "messages")
        self.assertIsNone(store.kv_get(self.conn, "action_undo:1"))
        reminder = self.reminder()
        self.cancel(reminder)
        store.purge_execute(self.conn, "reminders")
        self.assertIsNone(store.kv_get(self.conn, "action_undo:1"))

    def test_journal_purge_erases_previous_payload_retained_only_by_inverse(self):
        store.set_category_kind(self.conn, "Diary", "journal")
        store.journal_def_ensure(self.conn, "test-diary", "Diary", "freeform", "Diary")
        rid = self.note(category="Diary")
        store.ensure_category(self.conn, "Books")
        with store.action_undo_group(self.conn, 1, "recategorize"):
            store.confirm_category(self.conn, rid, "Books")
        self.assertTrue(self.latest()["targets"][0]["journal"])
        store.purge_execute(self.conn, "journal", "Diary")
        self.assertIsNone(store.kv_get(self.conn, "action_undo:1"))
        self.assertIsNotNone(store.get_message(self.conn, rid))

    def test_lazy_journal_card_number_is_bound_without_false_undo_conflict(self):
        agent = self.handler()
        for category in ("Diary", "Other diary"):
            store.set_category_kind(self.conn, category, "journal")
            store.journal_def_ensure(self.conn, category, category, "freeform", category)
        rid = self.note(category="Diary")
        self.conn.execute("UPDATE messages SET note_no=NULL,suggestion_message_id=123 WHERE id=?", (rid,))
        self.conn.commit()
        import tg_ingest_agent as entry
        with mock.patch.object(entry, "tg_call", return_value={"message_id": 456}):
            agent.do_recategorize(1, "en", {"query": "Diary", "category": "Other diary"})
        self.assertIsNotNone(store.get_message(self.conn, rid)["note_no"])
        self.assertEqual(store.action_undo_latest(self.conn, 1)["status"], "undone")
        self.assertEqual(store.get_message(self.conn, rid)["category"], "Diary")


if __name__ == "__main__":
    unittest.main()
