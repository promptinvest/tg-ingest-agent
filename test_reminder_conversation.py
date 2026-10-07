"""Synthetic regressions for the October 7 reminder conversation loop."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import reminders
import router
import store
import tg_ingest_agent
from testlib import make_config, setUpModule, tearDownModule


class ReminderConversationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.agent = tg_ingest_agent.Agent(make_config(
            ALLOWED_CHAT_IDS="1", DB_PATH=str(Path(self.tmp.name) / "test.db"),
            MEDIA_DIR=str(Path(self.tmp.name) / "media")))
        self.conn = self.agent.conn
        self.addCleanup(self.conn.close)
        store.pref_set(self.conn, "timezone_offset", "3")

    def reminder(self, title="отчёт", *, fired=True, recurrence="none", chat_id=1):
        now = datetime.now(timezone.utc)
        due = now - timedelta(minutes=1) if fired else now + timedelta(days=2)
        rid = store.reminder_add(self.conn, chat_id, title, due.isoformat(), recurrence)
        if fired:
            self.conn.execute("UPDATE reminders SET last_fired_at=? WHERE id=?",
                              (now.isoformat(), rid))
            self.conn.commit()
        return rid

    def prior_snoozes(self, rid):
        for _ in range(2):
            store.reminder_event(self.conn, rid, "snoozed", "fixture")

    def fired_pending(self, rid):
        row = store.reminder_get(self.conn, rid)
        store.pending_set(self.conn, 1, "reminder_fired",
                          {"reminder_id": rid, "title": row["title"]})
        self.agent._remember_reminder(rid)

    def dispatch(self, text):
        with mock.patch.object(router, "route", side_effect=AssertionError("no model routing")), \
                mock.patch.object(self.agent, "reply") as reply:
            self.agent.dispatch(1, {"message_id": 700, "text": text}, text)
        return reply.call_args.args[1]

    def callback(self, rid, op="tmw", text="card"):
        with mock.patch.object(router, "route", side_effect=AssertionError("no model routing")), \
                mock.patch.object(self.agent, "answer_callback") as answer, \
                mock.patch.object(self.agent, "edit_message") as edit, \
                mock.patch.object(self.agent, "reply") as reply:
            self.agent.handle_reminder_callback(
                "callback", 1, {"message_id": 700, "text": text}, f"rm|{op}|{rid}")
        return answer, edit, reply

    def tomorrow(self, hour=9):
        return reminders.local_day_at(3, 1, hour)

    def test_requested_day_on_third_snooze_is_confirmed_without_another_question(self):
        rid = self.reminder()
        self.prior_snoozes(rid)
        self.fired_pending(rid)
        answer = self.dispatch("Перенеси на завтра")
        self.assertIn("напомню завтра 09:00", answer)
        self.assertNotIn("третий", answer)
        self.assertNotIn("?", answer)
        self.assertEqual(store.reminder_get(self.conn, rid)["due_utc"], self.tomorrow())
        self.assertIsNone(store.pending_get(self.conn, 1))

    def test_same_day_escalation_reports_success_and_remembers_the_followup(self):
        rid = self.reminder()
        self.prior_snoozes(rid)
        self.fired_pending(rid)
        # Keep this same-day check independent of the test runner's clock.
        with mock.patch.object(self.agent, "_parse_fired_followup",
                               return_value=("amend", {"due_utc": datetime.now(timezone.utc).isoformat()})):
            answer = self.dispatch("через час")
        self.assertIn("напомню", answer)
        self.assertIn("третий раз", answer)
        self.assertEqual(store.pending_get(self.conn, 1)["kind"], "reminder_snooze_choice")
        answer = self.dispatch("Завтра")
        self.assertIn("завтра 09:00", answer)
        self.assertNotIn("третий", answer)
        self.assertEqual(store.reminder_get(self.conn, rid)["due_utc"], self.tomorrow())
        self.assertIsNone(store.pending_get(self.conn, 1))

    def test_yes_to_two_alternatives_does_not_close_or_move_anything(self):
        rid = self.reminder(fired=False)
        before = dict(store.reminder_get(self.conn, rid))
        store.pending_set(self.conn, 1, "reminder_snooze_choice", {"reminder_id": rid})
        answer = self.dispatch("Да")
        self.assertIn("на какой день", answer)
        self.assertEqual(dict(store.reminder_get(self.conn, rid)), before)
        self.assertEqual(store.pending_get(self.conn, 1)["kind"], "reminder_snooze_choice")

    def test_close_answers_the_choice_for_that_target(self):
        rid = self.reminder(fired=False)
        other = self.reminder("банк", fired=False)
        store.pending_set(self.conn, 1, "reminder_snooze_choice", {"reminder_id": rid})
        self.dispatch("закрой")
        self.assertEqual(store.reminder_get(self.conn, rid)["status"], "done")
        self.assertEqual(store.reminder_get(self.conn, other)["status"], "active")
        self.assertIsNone(store.pending_get(self.conn, 1))

    def test_repeated_tomorrow_clicks_preserve_events_and_undo(self):
        rid = self.reminder()
        self.prior_snoozes(rid)
        before = store.reminder_get(self.conn, rid)["due_utc"]
        self.callback(rid)
        count = self.conn.execute("SELECT COUNT(*) FROM reminder_events").fetchone()[0]
        for _ in range(2):
            answer, edit, reply = self.callback(rid, text="card\n— ⏰ напомню завтра 09:00")
            self.assertIsNone(edit.call_args.kwargs["reply_markup"])
            self.assertEqual(edit.call_args.args[2].count("напомню"), 1)
            reply.assert_not_called()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM reminder_events").fetchone()[0], count)
        self.assertEqual(store.reminder_get(self.conn, rid)["prev_due_utc"], before)

    def test_full_named_command_works_after_fired_context_was_consumed(self):
        self.reminder("другой", fired=False)
        rid = self.reminder(fired=False)  # raw id differs from display position
        other = self.reminder("банк", fired=False)
        other_before = dict(store.reminder_get(self.conn, other))
        answer = self.dispatch("перенеси отчёт на завтра")
        self.assertIn("перенесла", answer.casefold())
        self.assertEqual(store.reminder_get(self.conn, rid)["due_utc"], self.tomorrow())
        self.assertEqual(dict(store.reminder_get(self.conn, other)), other_before)

    def test_repeat_named_command_preserves_previous_due_date(self):
        rid = self.reminder(fired=False)
        self.dispatch("перенеси отчёт на завтра")
        row = dict(store.reminder_get(self.conn, rid))
        count = self.conn.execute("SELECT COUNT(*) FROM reminder_events").fetchone()[0]
        self.dispatch("перенеси отчёт на завтра")
        self.assertEqual(dict(store.reminder_get(self.conn, rid)), row)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM reminder_events").fetchone()[0], count)

    def test_explicit_clock_and_english_title_work_without_router(self):
        rid = self.reminder("report", fired=False)
        self.dispatch("move report to tomorrow at 5 pm")
        self.assertEqual(store.reminder_get(self.conn, rid)["due_utc"], self.tomorrow(17))

    def test_unknown_named_target_does_not_move_last_touched(self):
        rid = self.reminder(fired=False)
        self.agent._remember_reminder(rid)
        before = dict(store.reminder_get(self.conn, rid))
        self.assertFalse(self.agent.resolve_named_reschedule_text(1, "ru", "перенеси банк на завтра"))
        self.assertEqual(dict(store.reminder_get(self.conn, rid)), before)

    def test_existing_unique_title_substring_still_works(self):
        rid = self.reminder("позвонить в банк", fired=False)
        self.dispatch("перенеси банк на завтра")
        self.assertEqual(store.reminder_get(self.conn, rid)["due_utc"], self.tomorrow())

    def test_numbered_and_compound_routes_are_preserved(self):
        rid = self.reminder(fired=False)
        before = dict(store.reminder_get(self.conn, rid))
        for text in ("перенеси #1 на завтра", "перенеси первое на завтра",
                     "перенеси все на завтра", "перенеси отчёт на завтра и закрой банк"):
            self.assertFalse(self.agent.resolve_named_reschedule_text(1, "ru", text), text)
        self.assertEqual(dict(store.reminder_get(self.conn, rid)), before)

    def test_duplicate_titles_require_a_pinned_choice(self):
        one = self.reminder(fired=False)
        two = self.reminder(fired=False)
        before = [dict(r) for r in store.reminders_active(self.conn, 1)]
        self.dispatch("перенеси отчёт на завтра")
        pending = store.pending_get(self.conn, 1)
        self.assertEqual(pending["kind"], "reminder_op")
        self.assertEqual(pending["payload"]["ids"], [one, two])
        self.assertEqual([dict(r) for r in store.reminders_active(self.conn, 1)], before)
        self.dispatch("второе")
        self.assertEqual(store.reminder_get(self.conn, two)["due_utc"], self.tomorrow())
        self.assertEqual(dict(store.reminder_get(self.conn, one)), before[0])

    def test_duplicate_fired_titles_do_not_guess_from_last_touched(self):
        one = self.reminder()
        two = self.reminder()
        self.fired_pending(two)
        before = [dict(r) for r in store.reminders_active(self.conn, 1)]
        self.dispatch("перенеси отчёт на завтра")
        self.assertEqual(store.pending_get(self.conn, 1)["kind"], "reminder_op")
        self.assertEqual([dict(r) for r in store.reminders_active(self.conn, 1)], before)

    def test_explicit_title_overrides_overlapping_stale_subject(self):
        stale = self.reminder("банк и отчёт")
        target = self.reminder("банк", fired=False)
        self.fired_pending(stale)
        before = dict(store.reminder_get(self.conn, stale))
        self.dispatch("перенеси банк на завтра")
        self.assertEqual(store.reminder_get(self.conn, target)["due_utc"], self.tomorrow())
        self.assertEqual(dict(store.reminder_get(self.conn, stale)), before)

    def test_named_fired_series_uses_echo_and_preserves_anchor(self):
        rid = self.reminder(recurrence="daily")
        original = dict(store.reminder_get(self.conn, rid))
        self.dispatch("перенеси отчёт на завтра")
        self.assertEqual(dict(store.reminder_get(self.conn, rid)), original)
        echoes = [r for r in store.reminders_active(self.conn, 1) if r["id"] != rid]
        self.assertEqual(len(echoes), 1)
        self.assertEqual(echoes[0]["due_utc"], self.tomorrow())

    def test_named_batch_member_keeps_other_fired_members_pending(self):
        one = self.reminder("банк")
        two = self.reminder()
        before = dict(store.reminder_get(self.conn, one))
        store.pending_set(self.conn, 1, "reminder_fired", {
            "reminder_id": one, "title": "банк", "reminder_ids": [one, two],
            "titles": ["банк", "отчёт"]})
        self.dispatch("перенеси отчёт на завтра")
        pending = store.pending_get(self.conn, 1)
        self.assertEqual(pending["kind"], "reminder_fired")
        self.assertEqual(pending["payload"]["reminder_ids"], [one])
        self.assertEqual(dict(store.reminder_get(self.conn, one)), before)
        self.assertEqual(store.reminder_get(self.conn, two)["due_utc"], self.tomorrow())

    def test_named_command_preserves_foreign_confirmation(self):
        rid = self.reminder(fired=False)
        store.pending_set(self.conn, 1, "delete", {"row_id": 123})
        before = store.pending_get(self.conn, 1)
        self.dispatch("перенеси отчёт на завтра")
        self.assertEqual(store.reminder_get(self.conn, rid)["due_utc"], self.tomorrow())
        self.assertEqual(store.pending_get(self.conn, 1), before)

    def test_foreign_confirmation_survives_reply_bound_short_snooze(self):
        rid = self.reminder()
        self.prior_snoozes(rid)
        self.agent.turn_reply_reminder_id = rid
        store.pending_set(self.conn, 1, "category", {"row_id": 123})
        before = store.pending_get(self.conn, 1)
        with mock.patch.object(self.agent, "reply"):
            self.agent.resolve_fired_followup(1, "ru", "через 1 минуту", before)
        self.assertEqual(store.pending_get(self.conn, 1), before)

    def test_recurring_snooze_choice_targets_echo_not_series(self):
        rid = self.reminder(recurrence="daily")
        original = dict(store.reminder_get(self.conn, rid))
        self.prior_snoozes(rid)
        with mock.patch.object(self.agent, "reply"):
            self.agent.resolve_pending(1, "amend", {"due_utc": datetime.now(timezone.utc).isoformat()},
                {"kind": "reminder_fired", "payload": {"reminder_id": rid, "title": "отчёт"}}, "ru")
        echo = store.pending_get(self.conn, 1)["payload"]["reminder_id"]
        self.assertNotEqual(echo, rid)
        self.dispatch("завтра")
        self.assertEqual(dict(store.reminder_get(self.conn, rid)), original)
        self.assertEqual(store.reminder_get(self.conn, echo)["due_utc"], self.tomorrow())
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM reminders").fetchone()[0], 2)

    def test_reply_to_recurring_snooze_card_moves_existing_echo_once(self):
        rid = self.reminder(recurrence="daily")
        original = dict(store.reminder_get(self.conn, rid))
        self.prior_snoozes(rid)
        self.agent._remember_fired_message(700, rid)
        # Deterministic same-day button time, independent of midnight.
        fixed = datetime.now(timezone.utc).replace(hour=12, minute=0)
        with mock.patch("reminders_svc.datetime", wraps=datetime) as clock:
            clock.now.return_value = fixed
            self.callback(rid, "h1")
        pending = store.pending_get(self.conn, 1)
        self.assertEqual(pending["kind"], "reminder_snooze_choice")
        echo = pending["payload"]["reminder_id"]
        self.agent.turn_reply_reminder_id = self.agent.fired_reminder_for_message(700)
        self.dispatch("завтра")
        self.assertEqual(store.reminder_get(self.conn, echo)["due_utc"], self.tomorrow())
        self.assertEqual(dict(store.reminder_get(self.conn, rid)), original)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM reminders").fetchone()[0], 2)
        self.assertIsNone(store.pending_get(self.conn, 1))

    def test_reply_to_another_alarm_keeps_its_stronger_target(self):
        source = self.reminder(recurrence="daily")
        echo = self.reminder(fired=False)
        other = self.reminder("банк")
        before = dict(store.reminder_get(self.conn, echo))
        store.pending_set(self.conn, 1, "reminder_snooze_choice", {
            "reminder_id": echo, "source_reminder_id": source, "title": "отчёт"})
        self.agent.turn_reply_reminder_id = other
        self.dispatch("завтра")
        self.assertEqual(dict(store.reminder_get(self.conn, echo)), before)
        self.assertEqual(store.reminder_get(self.conn, other)["due_utc"], self.tomorrow())
        self.assertEqual(store.pending_get(self.conn, 1)["payload"]["reminder_id"], echo)

    def test_new_subject_is_not_eaten_as_snooze_choice(self):
        rid = self.reminder(fired=False)
        before = dict(store.reminder_get(self.conn, rid))
        pending = {"kind": "reminder_snooze_choice", "payload": {"reminder_id": rid}}
        store.pending_set(self.conn, 1, pending["kind"], pending["payload"])
        self.assertFalse(self.agent.resolve_snooze_choice_text(
            1, "ru", pending, "напомни завтра в 10 про врача"))
        self.assertEqual(dict(store.reminder_get(self.conn, rid)), before)
        self.assertIsNone(store.pending_get(self.conn, 1))

    def test_plain_day_cannot_target_random_future_last_reminder(self):
        rid = self.reminder(fired=False)
        self.agent._remember_reminder(rid)
        before = dict(store.reminder_get(self.conn, rid))
        self.assertFalse(self.agent.resolve_fired_followup(1, "ru", "завтра", None))
        self.assertEqual(dict(store.reminder_get(self.conn, rid)), before)

    def test_callback_cannot_touch_another_chat(self):
        rid = self.reminder(chat_id=2)
        before = dict(store.reminder_get(self.conn, rid))
        answer, edit, reply = self.callback(rid)
        self.assertEqual(dict(store.reminder_get(self.conn, rid)), before)
        edit.assert_not_called()
        reply.assert_not_called()
