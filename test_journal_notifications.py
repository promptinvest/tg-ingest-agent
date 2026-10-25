"""Synthetic end-to-end notification/journal corrections; no private owner data."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import journals
import reminders_svc
import router
import store
import tg_ingest_agent
from testlib import make_config, setUpModule, tearDownModule


class JournalValuesTests(unittest.TestCase):
    def test_people_drop_pronouns_and_preserve_real_names(self):
        source = "Тебя, меня, нас и Анну-Марию благодарю. Maya Chen and you."
        clean, status = journals.validate_payload("gratitude", {
            "people": ["тебя", "меня", "нас", "you", "Анна-Мария", "Maya Chen"]}, source)
        self.assertEqual(clean["people"], ["Анна-Мария", "Maya Chen"])
        self.assertEqual(status, "complete")

    def test_existing_family_groups_still_supported(self):
        self.assertEqual(journals.validate_payload(
            "gratitude", {"people": ["Семья", "Мама"]}, "Спасибо, мама и семья")[0]["people"],
            ["Семья", "Мама"])

    def test_dates_use_local_calendar_and_keep_receipt_default(self):
        receipt = "2026-05-04T22:10:00+00:00"
        self.assertEqual(journals.occurred_at("Вчера был концерт", receipt, 3),
                         "2026-05-03T21:00:00+00:00")
        self.assertEqual(journals.occurred_at("Quiet concert", receipt, 3), receipt)
        self.assertEqual(journals.occurred_at("day before yesterday", receipt, -4),
                         "2026-05-02T04:00:00+00:00")

    def test_invitation_is_default_but_explicit_date_wins(self):
        receipt = "2026-05-05T08:00:00+00:00"
        invitation = "2026-05-04T18:00:00+00:00"
        self.assertEqual(journals.occurred_at("Концерт", receipt, 3, invitation), invitation)
        self.assertEqual(journals.occurred_at("сегодня концерт", receipt, 3, invitation),
                         "2026-05-04T21:00:00+00:00")

    def test_explicit_invalid_date_is_not_silently_today(self):
        with self.assertRaises(ValueError):
            journals.occurred_at("31.02.2026", "2026-05-04T08:00:00+00:00", 3)

    def test_correction_is_distinct_from_entry_and_query(self):
        for text in ["Это было для благодарности", "That was meant for gratitude",
                     'Вчера надо было записать благодарности. Я просил записать "Концерт"']:
            self.assertTrue(journals.correction_intent(text), text)
        for text in ["gratitude: quiet concert", "Покажи благодарности", "Чай на террасе",
                     "gratitude: that was a lovely concert", "В благодарности — это было чудесно"]:
            self.assertFalse(journals.correction_intent(text), text)


class JournalFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.agent = tg_ingest_agent.Agent(make_config(
            ALLOWED_CHAT_IDS="1", DB_PATH=str(Path(self.tmp.name) / "test.db"),
            MEDIA_DIR=str(Path(self.tmp.name) / "media")))
        self.conn = self.agent.conn
        self.addCleanup(self.conn.close)
        store.pref_set(self.conn, "timezone_offset", "3")
        self.category = "Благодарность"
        store.set_category_kind(self.conn, self.category, "journal")
        self.now = datetime.now(timezone.utc)
        self.patches = []
        for name in ("send_chat_action", "answer_callback", "edit_message"):
            patch = mock.patch.object(self.agent, name)
            patch.start()
            self.addCleanup(patch.stop)

    def note(self, text="Чай на террасе", *, chat_id=1, confirmed=False):
        number = self.conn.execute("SELECT COALESCE(MAX(id),0)+1 FROM messages").fetchone()[0]
        rid = store.insert_message(self.conn, {
            "chat_id": chat_id, "tg_message_id": number+10, "received_at": (self.now-timedelta(days=1)).isoformat(),
            "raw_text": text})
        store.set_suggestion(self.conn, rid, "Отдых", text, "fixture")
        store.set_suggestion_message(self.conn, rid, number+100)
        if confirmed:
            store.confirm_category(self.conn, rid, self.category,
                                   journal_payload={"people": ["Анна"]}, journal_status="complete")
        return store.get_message(self.conn, rid)

    def preview(self, text, *, pending=None, msg=None):
        with mock.patch.object(journals, "extract", return_value=({"subject": "Чай"}, "complete")), \
                mock.patch.object(self.agent, "reply", return_value={"message_id": 900}) as reply:
            handled = self.agent.resolve_gratitude_correction(
                1, "ru", msg or {"text": text}, text, pending)
        return handled, reply

    def confirm(self, answer="yes", card=900, nonce=None):
        pending = store.pending_get(self.conn, 1)
        with mock.patch.object(self.agent, "reply") as reply:
            result = self.agent.resolve_journal_correction(
                1, "ru", pending, answer, card_id=card,
                nonce=nonce if nonce is not None else pending["payload"]["nonce"])
        return result, reply

    def test_expired_category_correction_previews_original_without_duplicate(self):
        row = self.note()
        handled, reply = self.preview("Это было для благодарности")
        self.assertTrue(handled)
        self.assertIn(row["raw_text"], reply.call_args.args[1])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)
        self.assertEqual(store.get_message(self.conn, row["id"])["status"], "suggested")
        self.assertIsNone(store.journal_entry_get(self.conn, row["id"]))
        self.confirm()
        self.assertEqual(store.get_message(self.conn, row["id"])["category"], self.category)
        self.assertEqual(store.get_message(self.conn, row["id"])["raw_text"], row["raw_text"])
        self.assertEqual(store.journal_entry_get(self.conn, row["id"])["occurred_at"], row["received_at"])

    def test_quoted_retrospective_correction_uses_exact_old_content(self):
        row = self.note("Музыкальный вечер")
        self.note("Другая запись")
        text = 'Вчера надо было записать благодарности. Я просил записать "Музыкальный вечер"'
        self.preview(text)
        pending = store.pending_get(self.conn, 1)
        self.assertEqual(pending["payload"]["row_id"], row["id"])
        self.confirm()
        entry = store.journal_entry_get(self.conn, row["id"])
        self.assertEqual((datetime.fromisoformat(entry["occurred_at"])+timedelta(hours=3)).date(),
                         (self.now+timedelta(hours=3)-timedelta(days=1)).date())
        self.assertEqual(store.get_message(self.conn, row["id"])["raw_text"], "Музыкальный вечер")

    def test_duplicate_quoted_sources_require_target_not_latest_guess(self):
        self.note("Музыкальный вечер")
        self.note("Музыкальный вечер")
        self.preview('Это для благодарности: "Музыкальный вечер"')
        self.assertIsNone(store.pending_get(self.conn, 1))

    def test_existing_confirmation_is_not_replaced(self):
        row = self.note()
        store.pending_set(self.conn, 1, "reminder", {"title": "счёт"})
        before = dict(self.conn.execute("SELECT * FROM pending_actions").fetchone())
        self.preview("Это было для благодарности", pending=store.pending_get(self.conn, 1))
        self.assertEqual(dict(self.conn.execute("SELECT * FROM pending_actions").fetchone()), before)
        self.assertIsNone(store.journal_entry_get(self.conn, row["id"]))

    def test_cancel_changes_no_note_or_entry(self):
        row = self.note()
        self.preview("Это было для благодарности")
        self.confirm("no")
        self.assertEqual(dict(store.get_message(self.conn, row["id"])), dict(row))
        self.assertIsNone(store.journal_entry_get(self.conn, row["id"]))

    def test_wrong_card_or_nonce_cannot_apply(self):
        row = self.note()
        self.preview("Это было для благодарности")
        self.confirm(card=901)
        self.confirm(nonce="wrong")
        self.assertEqual(store.get_message(self.conn, row["id"])["status"], "suggested")
        self.assertIsNotNone(store.pending_get(self.conn, 1))

    def test_source_change_after_preview_rejects_confirmation(self):
        row = self.note()
        self.preview("Это было для благодарности")
        self.conn.execute("UPDATE messages SET raw_text='Изменённый источник' WHERE id=?", (row["id"],))
        self.conn.commit()
        self.confirm()
        self.assertIsNone(store.journal_entry_get(self.conn, row["id"]))
        self.assertEqual(store.get_message(self.conn, row["id"])["raw_text"], "Изменённый источник")

    def test_confirmed_entry_keeps_old_metadata_in_history(self):
        row = self.note("Спасибо Анне", confirmed=True)
        old = dict(store.journal_entry_get(self.conn, row["id"]))
        self.preview("Это было для благодарности")
        self.confirm()
        entry = store.journal_entry_get(self.conn, row["id"])
        self.assertEqual(entry["id"], old["id"])
        self.assertEqual(entry["created_at"], old["created_at"])
        history = self.conn.execute("SELECT suggested FROM feedback WHERE skill='journal_correction'").fetchone()
        self.assertEqual(json.loads(history["suggested"])["journal_entry"], old)

    def test_foreign_note_and_forward_cannot_supply_correction_authority(self):
        self.note(chat_id=2)
        self.preview("Это было для благодарности")
        self.assertIsNone(store.pending_get(self.conn, 1))
        handled, _ = self.preview("Это было для благодарности", msg={
            "text": "Это было для благодарности", "forward_origin": {"type": "user"}})
        self.assertFalse(handled)

    def invitation(self, age=55):
        # Noon local avoids an accidental date boundary in a duration fixture.
        fixed = self.now.replace(hour=12, minute=0, second=0, microsecond=0)
        due = fixed-timedelta(minutes=age)
        rid = store.reminder_add(self.conn, 1, self.category, due.isoformat(), "daily")
        store.reminder_touch_fired(self.conn, rid, due.isoformat())
        store.convo_add(self.conn, 1, "bot", "Journal invitation")
        with mock.patch.object(reminders_svc, "datetime", wraps=datetime) as dt:
            dt.now.return_value = fixed
            self.agent._remember_fired_message(700, rid,
                occurrence_rows=[store.reminder_get(self.conn, rid)], chat_id=1)
            self.agent._finalize_fired_notification(rid, due.isoformat())
        return rid, due, fixed

    def test_delayed_plain_reply_keeps_journal_context_after_pending_expiry(self):
        _, due, fixed = self.invitation()
        store.convo_add(self.conn, 1, "user", "Чай на террасе")
        with mock.patch.object(reminders_svc, "datetime", wraps=datetime) as dt:
            dt.now.return_value = fixed
            invitation = self.agent.gratitude_invitation(1, {"text": "Чай на террасе"}, delayed=True)
        self.assertEqual(invitation["occurred_at"], due.isoformat())

    def test_intervening_owner_request_stops_unthreaded_capture(self):
        self.invitation()
        store.convo_add(self.conn, 1, "user", "Покажи серверы")
        store.convo_add(self.conn, 1, "user", "Обычная заметка")
        self.assertIsNone(self.agent.gratitude_invitation(1, {"text": "Обычная заметка"}, delayed=True))

    def test_previous_day_requires_explicit_reply(self):
        rid, due, fixed = self.invitation(age=24*60)
        store.convo_add(self.conn, 1, "user", "Обычная заметка")
        with mock.patch.object(reminders_svc, "datetime", wraps=datetime) as dt:
            dt.now.return_value = fixed
            self.assertIsNone(self.agent.gratitude_invitation(1, {"text": "Обычная заметка"}, delayed=True))
        explicit = self.agent.gratitude_invitation(1, {"reply_to_message": {"message_id": 700}})
        self.assertEqual(explicit["occurred_at"], due.isoformat())

    def test_invitation_survives_restart(self):
        _, due, fixed = self.invitation()
        store.convo_add(self.conn, 1, "user", "Чай")
        fresh = tg_ingest_agent.Agent(self.agent.cfg)
        self.addCleanup(fresh.conn.close)
        with mock.patch.object(reminders_svc, "datetime", wraps=datetime) as dt:
            dt.now.return_value = fixed
            self.assertEqual(fresh.gratitude_invitation(1, {"text": "Чай"}, delayed=True)["occurred_at"], due.isoformat())

    def test_legacy_explicit_binding_uses_card_date(self):
        rid = store.reminder_add(self.conn, 1, self.category, self.now.isoformat(), "daily")
        self.agent._remember_fired_message(600, rid)
        old = self.now-timedelta(days=2)
        value = self.agent.gratitude_invitation(1, {"reply_to_message": {
            "message_id": 600, "date": int(old.timestamp())}})
        self.assertEqual(datetime.fromisoformat(value["occurred_at"]).date(), old.date())

    def update(self, text, *, reply_id=None, reply_date=None, number=800):
        msg = {"message_id": number, "chat": {"id": 1, "type": "private"},
               "from": {"id": 1}, "date": int(self.now.timestamp()), "text": text}
        if reply_id:
            msg["reply_to_message"] = {"message_id": reply_id, "from": {"is_bot": True},
                                       "date": reply_date, "text": "Journal invitation"}
        return {"update_id": number, "message": msg}

    def test_full_old_card_capture_preview_and_confirm_use_same_past_date(self):
        old = (self.now-timedelta(days=1)).replace(microsecond=0)
        rid = store.reminder_add(self.conn, 1, self.category, old.isoformat(), "daily")
        store.reminder_touch_fired(self.conn, rid, old.isoformat())
        self.agent._remember_fired_message(700, rid)
        with mock.patch.object(journals, "extract", return_value=({"subject": "Чай"}, "complete")), \
                mock.patch.object(self.agent, "index_message"), \
                mock.patch.object(router, "route", side_effect=AssertionError("no router")), \
                mock.patch.object(self.agent, "reply", return_value={"message_id": 900}) as reply:
            self.agent.handle_update(self.update("Чай на террасе", reply_id=700,
                                                reply_date=int(old.timestamp())))
        row = self.conn.execute("SELECT * FROM messages").fetchone()
        self.assertEqual(row["raw_text"], "Чай на террасе")
        expected = self.agent._fmt_iso_local(old.isoformat()).split(",")[0]
        self.assertIn(expected, reply.call_args.args[1])
        self.assertIsNone(store.journal_entry_get(self.conn, row["id"]))
        with mock.patch.object(self.agent, "reply"), mock.patch.object(tg_ingest_agent, "tg_call"):
            self.agent.handle_callback({"id": "test", "from": {"id": 1}, "message": {
                "message_id": 900, "chat": {"id": 1}}, "data": "s|"+str(row["id"])})
        self.assertEqual(store.journal_entry_get(self.conn, row["id"])["occurred_at"], old.isoformat())

    def test_invalid_date_cannot_be_confirmed_or_autosaved(self):
        store.pref_set(self.conn, "gratitude_autosave", "on")
        with mock.patch.object(journals, "extract", return_value=({}, "empty")), \
                mock.patch.object(self.agent, "index_message"), \
                mock.patch.object(self.agent, "reply", return_value={"message_id": 900}) as reply:
            self.agent._deterministic_capture(1, "ru", self.update("31.02.2026: чай")["message"],
                                              self.category, "fixture")
        row = self.conn.execute("SELECT * FROM messages").fetchone()
        self.assertIsNone(store.journal_entry_get(self.conn, row["id"]))
        keyboard = reply.call_args.kwargs["reply_markup"]["inline_keyboard"]
        self.assertFalse(any(b["callback_data"].startswith("s|") for group in keyboard for b in group))
        with mock.patch.object(self.agent, "reply"):
            self.agent.apply_category_confirm(1, row, self.category, None)
        self.assertIsNone(store.journal_entry_get(self.conn, row["id"]))

    def test_correction_reply_is_never_captured_as_new_entry(self):
        self.note("Музыкальный вечер")
        old = self.now-timedelta(days=1)
        rid = store.reminder_add(self.conn, 1, self.category, old.isoformat(), "daily")
        self.agent._remember_fired_message(700, rid)
        with mock.patch.object(journals, "extract", return_value=({}, "empty")), \
                mock.patch.object(self.agent, "_deterministic_capture") as capture, \
                mock.patch.object(router, "route", side_effect=AssertionError("no model router")), \
                mock.patch.object(self.agent, "reply", return_value={"message_id": 900}):
            self.agent.handle_update(self.update(
                'Вчера надо было записать благодарности. Я просил записать "Музыкальный вечер"',
                reply_id=700, reply_date=int(old.timestamp())))
        capture.assert_not_called()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)
        self.assertEqual(store.pending_get(self.conn, 1)["kind"], "journal_correction")

    def test_command_nouns_and_polite_requests_do_not_become_journal_entries(self):
        context = {"kind": "reminder_fired", "payload": {"title": self.category}}
        for text in ["Список моих серверов и проектов", "Статус напоминаний",
                     "Please list reminders", "Можешь показать расходы"]:
            self.assertEqual(self.agent._fired_gratitude_category(context, {"text": text}), "", text)
        self.assertEqual(self.agent._fired_gratitude_category(context, {"text": "Чай на террасе"}), self.category)

    def test_old_daily_notification_callback_cannot_close_new_occurrence(self):
        rid, due, fixed = self.invitation(age=24*60)
        with mock.patch.object(reminders_svc, "datetime", wraps=datetime) as dt:
            dt.now.return_value = due
            self.agent._remember_fired_message(700, rid,
                occurrence_rows=[store.reminder_get(self.conn, rid)], chat_id=1)
        self.agent._finalize_fired_notification(rid, due.isoformat())
        store.reminder_touch_fired(self.conn, rid, fixed.isoformat())
        before = dict(store.reminder_get(self.conn, rid))
        with mock.patch.object(self.agent, "reply"), mock.patch.object(self.agent, "answer_callback") as answer:
            self.agent.handle_reminder_callback("test", 1, {"message_id": 700}, "rm|skip|"+str(rid))
        self.assertEqual(dict(store.reminder_get(self.conn, rid)), before)
        self.assertIn("прошлому", answer.call_args.args[1])

    def test_old_daily_notification_text_cannot_close_new_occurrence(self):
        old = self.now-timedelta(days=1)
        rid = store.reminder_add(self.conn, 1, self.category, old.isoformat(), "daily")
        self.agent._remember_fired_message(700, rid)
        store.reminder_touch_fired(self.conn, rid, self.now.isoformat())
        before = dict(store.reminder_get(self.conn, rid))
        with mock.patch.object(router, "route", side_effect=AssertionError("no router")), \
                mock.patch.object(self.agent, "reply") as reply:
            self.agent.handle_update(self.update("готово", reply_id=700, reply_date=int(old.timestamp())))
        self.assertEqual(dict(store.reminder_get(self.conn, rid)), before)
        self.assertIn("прошлому", reply.call_args.args[1])

    def test_late_journal_notification_explicitly_names_its_date(self):
        old = self.now-timedelta(days=1)
        rid = store.reminder_add(self.conn, 1, self.category, old.isoformat(), "daily")
        with mock.patch.object(self.agent, "reply", return_value={"message_id": 900}) as reply:
            self.agent._send_fired_card(1, [store.reminder_get(self.conn, rid)])
        expected = self.agent._fmt_iso_local(old.isoformat()).split(",")[0]
        self.assertIn(expected, reply.call_args.args[1])
        skip = reply.call_args.kwargs["reply_markup"]["inline_keyboard"][0][0]["text"]
        self.assertIn(expected, skip)

    def test_failed_notification_delivery_creates_no_binding(self):
        rid = store.reminder_add(self.conn, 1, self.category,
            (self.now-timedelta(minutes=1)).isoformat(), "daily")
        with mock.patch.object(self.agent, "_recent_boss_msg", return_value=False), \
                mock.patch.object(self.agent, "reply", return_value=None):
            self.agent.fire_due_reminders()
        self.assertEqual(self.agent._fired_messages(), {})
        self.assertIsNone(store.reminder_get(self.conn, rid)["last_fired_at"])

    def test_current_category_correction_cancel_restores_original_pending_expiry(self):
        row = self.note()
        store.pending_set(self.conn, 1, "category", {"row_id": row["id"]}, ttl_seconds=777)
        before = dict(self.conn.execute("SELECT * FROM pending_actions").fetchone())
        self.preview("Это было для благодарности", pending=store.pending_get(self.conn, 1))
        self.assertEqual(store.pending_get(self.conn, 1)["kind"], "journal_correction")
        self.confirm("no")
        self.assertEqual(dict(self.conn.execute("SELECT * FROM pending_actions").fetchone()), before)

    def test_date_amendment_requires_new_card_and_invalidates_old_nonce(self):
        self.note()
        self.preview("Это было для благодарности")
        old = store.pending_get(self.conn, 1)["payload"]
        with mock.patch.object(self.agent, "reply", return_value={"message_id": 901}):
            self.agent.resolve_journal_correction(
                1, "ru", store.pending_get(self.conn, 1), "за вчера", card_id=900)
        current = store.pending_get(self.conn, 1)["payload"]
        self.assertNotEqual(current["nonce"], old["nonce"])
        self.confirm(card=900, nonce=old["nonce"])
        self.assertIsNone(store.journal_entry_get(self.conn, old["row_id"]))
        self.confirm(card=901)
        self.assertIsNotNone(store.journal_entry_get(self.conn, old["row_id"]))

    def test_semantic_confirm_still_uses_exact_pending_source(self):
        row = self.note()
        self.preview("Это было для благодарности")
        with mock.patch.object(self.agent, "reply"):
            self.agent.resolve_pending(1, "confirm", {}, store.pending_get(self.conn, 1), "ru")
        self.assertIsNotNone(store.journal_entry_get(self.conn, row["id"]))

    def test_intervening_bot_notification_also_stops_unthreaded_capture(self):
        self.invitation()
        store.convo_add(self.conn, 1, "bot", "Please confirm the document")
        store.convo_add(self.conn, 1, "user", "Да")
        self.assertIsNone(self.agent.gratitude_invitation(1, {"text": "Да"}, delayed=True))

    def test_newer_nonjournal_reminder_does_not_expose_older_journal_context(self):
        self.invitation()
        rid = store.reminder_add(self.conn, 1, "Оплатить счёт", self.now.isoformat(), "daily")
        self.agent._remember_fired_message(701, rid,
            occurrence_rows=[store.reminder_get(self.conn, rid)], chat_id=1)
        store.convo_add(self.conn, 1, "user", "Счёт оплачен")
        self.assertIsNone(self.agent.gratitude_invitation(1, {"text": "Счёт оплачен"}, delayed=True))


    def test_polite_journal_reads_never_open_correction_or_consume_pending(self):
        self.note()
        store.pending_set(self.conn, 1, "reminder", {"title": "invoice"})
        before = dict(self.conn.execute("SELECT * FROM pending_actions").fetchone())
        for text in ["Could you show my gratitude entries for this week?",
                     "Можешь показать благодарности за эту неделю?",
                     "How many gratitude entries did I record this week?",
                     "Когда была эта благодарность?", "Please list that gratitude journal", "Can you explain this gratitude journal?",
                     "Расскажи об этой благодарности"]:
            handled, reply = self.preview(text, pending=store.pending_get(self.conn, 1))
            self.assertFalse(handled, text)
            reply.assert_not_called()
            self.assertEqual(dict(self.conn.execute("SELECT * FROM pending_actions").fetchone()), before)

    def test_metadata_only_correction_preserves_existing_explicit_event_date(self):
        row = self.note(confirmed=True)
        old = (self.now-timedelta(days=12)).isoformat()
        self.conn.execute("UPDATE journal_entries SET occurred_at=? WHERE message_id=?", (old, row["id"]))
        self.conn.commit()
        self.preview("That was meant for gratitude")
        self.assertEqual(store.pending_get(self.conn, 1)["payload"]["occurred_at"], old)
        self.confirm()
        self.assertEqual(store.journal_entry_get(self.conn, row["id"])["occurred_at"], old)

    def test_saved_ack_and_edited_card_match_preview_and_storage_date(self):
        row = self.note()
        old = (self.now-timedelta(days=9)).isoformat()
        store.kv_set(self.conn, f"journal_draft:{row['id']}", json.dumps({
            "occurred_at": old, "payload": {"subject": "Чай"}, "status": "complete"}))
        expected = self.agent._fmt_iso_local(old).split(",")[0]
        with mock.patch.object(self.agent, "reply") as reply, \
                mock.patch.object(tg_ingest_agent, "tg_call") as call:
            self.agent.apply_category_confirm(1, row, self.category, None)
        self.assertIn(expected, reply.call_args.args[1])
        edits = [c.args[2]["text"] for c in call.call_args_list if c.args[1] == "editMessageText"]
        self.assertEqual(len(edits), 1)
        self.assertIn(expected, edits[0])
        self.assertIn(row["raw_text"], edits[0])
        self.assertIn("Чай", edits[0])
        self.assertEqual(store.journal_entry_get(self.conn, row["id"])["occurred_at"], old)

    def test_invalid_journal_date_keeps_exact_pending_on_text_and_button_confirm(self):
        row = self.note()
        store.set_suggestion(self.conn, row["id"], self.category, "", "fixture")
        store.kv_set(self.conn, f"journal_draft:{row['id']}", json.dumps({"date_error": True}))
        store.pending_set(self.conn, 1, "category", {"row_id": row["id"]}, ttl_seconds=777)
        before = dict(self.conn.execute("SELECT * FROM pending_actions").fetchone())
        with mock.patch.object(self.agent, "reply"):
            self.agent.resolve_pending(1, "confirm", {}, store.pending_get(self.conn, 1), "ru")
            self.assertEqual(dict(self.conn.execute("SELECT * FROM pending_actions").fetchone()), before)
            self.agent.handle_callback({"id": "test", "from": {"id": 1}, "message": {
                "message_id": row["suggestion_message_id"], "chat": {"id": 1}}, "data": "s|"+str(row["id"])})
        self.assertEqual(dict(self.conn.execute("SELECT * FROM pending_actions").fetchone()), before)
        self.assertIsNone(store.journal_entry_get(self.conn, row["id"]))

    def test_invalid_journal_date_does_not_block_ordinary_note_recategorization(self):
        row = self.note()
        store.set_suggestion(self.conn, row["id"], self.category, "", "fixture")
        store.kv_set(self.conn, f"journal_draft:{row['id']}", json.dumps({"date_error": True}))
        store.pending_set(self.conn, 1, "category", {"row_id": row["id"]})
        with mock.patch.object(self.agent, "reply"), mock.patch.object(tg_ingest_agent, "tg_call"):
            self.agent.resolve_pending(1, "amend", {"category": "Отдых"}, store.pending_get(self.conn, 1), "ru")
        saved = store.get_message(self.conn, row["id"])
        self.assertEqual((saved["status"], saved["category"]), ("confirmed", "Отдых"))
        self.assertIsNone(store.pending_get(self.conn, 1))
        self.assertIsNone(store.journal_entry_get(self.conn, row["id"]))

    def test_success_and_stale_correction_restore_unrelated_fired_pending_exactly(self):
        for stale in [False, True]:
            with self.subTest(stale=stale):
                row = self.note()
                store.pending_set(self.conn, 1, "reminder_fired", {"reminder_id": 999}, ttl_seconds=777)
                before = dict(self.conn.execute("SELECT * FROM pending_actions").fetchone())
                self.preview("Это было для благодарности", pending=store.pending_get(self.conn, 1))
                if stale:
                    self.conn.execute("UPDATE messages SET raw_text='Changed source' WHERE id=?", (row["id"],))
                    self.conn.commit()
                self.confirm()
                self.assertEqual(dict(self.conn.execute("SELECT * FROM pending_actions").fetchone()), before)
                self.assertEqual(store.journal_entry_get(self.conn, row["id"]) is None, stale)

    def test_exact_occurrence_blocks_two_fires_less_than_five_minutes_apart(self):
        rid, due, _ = self.invitation(age=1)
        self.assertTrue(self.agent._fired_notification_current(1, rid, 700))
        store.reminder_touch_fired(self.conn, rid, (due+timedelta(minutes=2)).isoformat())
        self.assertFalse(self.agent._fired_notification_current(1, rid, 700))
        before = dict(store.reminder_get(self.conn, rid))
        with mock.patch.object(self.agent, "reply"):
            self.agent.handle_reminder_callback("test", 1, {"message_id": 700}, "rm|skip|"+str(rid))
        self.assertEqual(dict(store.reminder_get(self.conn, rid)), before)

    def test_unknown_legacy_date_refuses_recurring_action_and_one_shot_still_works(self):
        rid = store.reminder_add(self.conn, 1, "Daily", self.now.isoformat(), "daily")
        store.reminder_touch_fired(self.conn, rid)
        store.kv_set(self.conn, self.agent._FIRED_MSGS_KV, json.dumps({"700": rid}))
        for stamp in [None, 0, "invalid"]:
            self.assertFalse(self.agent._fired_notification_current(1, rid, 700, stamp))
        stamp = int(datetime.now(timezone.utc).timestamp())
        self.assertTrue(self.agent._fired_notification_current(1, rid, 700, stamp))
        store.reminder_touch_fired(self.conn, rid)
        self.assertFalse(self.agent._fired_notification_current(1, rid, 700, stamp))
        once = store.reminder_add(self.conn, 1, "Once", self.now.isoformat())
        self.assertTrue(self.agent._fired_notification_current(1, once, 999))

    def test_actual_fire_finalizes_binding_and_restart_preserves_it(self):
        rid = store.reminder_add(self.conn, 1, self.category,
            (self.now-timedelta(minutes=1)).isoformat(), "daily")
        with mock.patch.object(self.agent, "_recent_boss_msg", return_value=False), \
                mock.patch.object(self.agent, "reply", return_value={"message_id": 700}):
            self.agent.fire_due_reminders()
        record = self.agent._fired_messages()["700"]
        self.assertEqual(record["fired_event_ids"][str(rid)], self.agent._latest_fired_event(rid)["id"])
        fresh = tg_ingest_agent.Agent(self.agent.cfg)
        self.addCleanup(fresh.conn.close)
        self.assertTrue(fresh._fired_notification_current(1, rid, 700))


    def test_journal_without_structured_definition_still_confirms_and_acknowledges(self):
        row = self.note()
        store.set_category_kind(self.conn, "Личный дневник", "journal")
        self.assertIsNone(store.journal_def_by_category(self.conn, "Личный дневник"))
        with mock.patch.object(self.agent, "reply") as reply, mock.patch.object(tg_ingest_agent, "tg_call"):
            self.agent.apply_category_confirm(1, row, "Личный дневник", None)
        self.assertEqual(store.get_message(self.conn, row["id"])["status"], "confirmed")
        self.assertIn("Личный дневник", reply.call_args.args[1])
        self.assertIsNone(store.journal_entry_get(self.conn, row["id"]))

    def test_stale_date_amendment_restores_unrelated_fired_pending(self):
        row = self.note()
        store.pending_set(self.conn, 1, "reminder_fired", {"reminder_id": 999}, ttl_seconds=777)
        before = dict(self.conn.execute("SELECT * FROM pending_actions").fetchone())
        self.preview("Это было для благодарности", pending=store.pending_get(self.conn, 1))
        self.conn.execute("UPDATE messages SET raw_text='Changed source' WHERE id=?", (row["id"],))
        self.conn.commit()
        self.confirm("за вчера")
        self.assertEqual(dict(self.conn.execute("SELECT * FROM pending_actions").fetchone()), before)
        self.assertIsNone(store.journal_entry_get(self.conn, row["id"]))

    def test_correction_preview_uses_real_line_breaks(self):
        self.note()
        _, reply = self.preview("That was meant for gratitude")
        text = reply.call_args.args[1]
        self.assertNotIn("\\n", text)
        self.assertIn("\n", text)


class JournalLegacyReadTests(unittest.TestCase):
    def test_legacy_pronouns_do_not_appear_as_named_people_without_data_rewrite(self):
        payload = {"people": ["тебя", "Maya Chen"]}
        before = json.dumps(payload)
        self.assertEqual(journals.person_counts([(payload, 2)]), [("Maya Chen", 1, [2])])
        self.assertNotIn("тебя", "\n".join(journals.draft_lines("ru", payload)))
        self.assertEqual(json.dumps(payload), before)

    def test_mentioned_title_or_historical_date_does_not_change_entry_date(self):
        receipt = "2026-05-04T08:00:00+00:00"
        for text in ["Спасибо за песню Yesterday", "Вспомнил поездку 2020-02-01", "Читал роман Сегодня"]:
            self.assertEqual(journals.occurred_at(text, receipt, 3), receipt, text)
