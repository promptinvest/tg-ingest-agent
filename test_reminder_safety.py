"""October 19 regressions: reply targets, draft identity and complete schedules."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import proactive
import reminder_time
import reminders
import router
import store
import tg_ingest_agent
from texts import T
from testlib import make_config, setUpModule, tearDownModule


class ReminderSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_config(ALLOWED_CHAT_IDS="1", DB_PATH=str(Path(self.tmp.name) / "test.db"),
                               MEDIA_DIR=str(Path(self.tmp.name) / "media"))
        self.agent = tg_ingest_agent.Agent(self.cfg)
        self.conn = self.agent.conn
        self.addCleanup(self.conn.close)
        store.pref_set(self.conn, "timezone_offset", "3")
        for name in ("send_chat_action", "answer_callback", "edit_message"):
            patch = mock.patch.object(self.agent, name)
            patch.start()
            self.addCleanup(patch.stop)

    def add(self, title, *, fired=False, chat_id=1):
        now = datetime.now(timezone.utc)
        due = now - timedelta(hours=4) if fired else now + timedelta(days=2)
        rid = store.reminder_add(self.conn, chat_id, title, due.isoformat(), "none")
        if fired:
            store.reminder_touch_fired(self.conn, rid, due.isoformat())
        return rid

    def state(self):
        return {table: [tuple(r) for r in self.conn.execute("SELECT * FROM " + table)]
                for table in ("reminders", "reminder_events", "pending_actions", "llm_usage")}

    def nudge(self, ids, *, delivered=True):
        rows = [store.reminder_get(self.conn, rid) for rid in ids]
        self.agent.last_proactive = 0
        def run(conn, cfg, lang, send, **kwargs):
            return "overdue" if send("Waiting on you: " + ", ".join(r["title"] for r in rows[:3])) else None
        with mock.patch.object(proactive, "overdue_rows", return_value=rows), \
                mock.patch.object(proactive, "run", side_effect=run), \
                mock.patch.object(self.agent, "reply", return_value={"message_id": 900} if delivered else None):
            self.agent.check_proactive()

    def reply_to_nudge(self, text):
        update = {"update_id": 900001, "message": {
            "message_id": 901, "chat": {"id": 1, "type": "private"}, "from": {"id": 1},
            "date": int(datetime.now(timezone.utc).timestamp()), "text": text,
            "reply_to_message": {"message_id": 900, "from": {"is_bot": True},
                                 "text": "Waiting on you: payment"}}}
        with mock.patch.object(router, "route", side_effect=AssertionError("no model routing")), \
                mock.patch.object(self.agent, "reply") as reply:
            self.agent.handle_update(update)
        return reply.call_args.args[1]

    def draft(self, title, message_id, *, due=None):
        due = due or (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        with mock.patch.object(self.agent, "reply", return_value={"message_id": message_id}):
            self.agent.do_reminder_create(1, "ru", {"title": title, "due_utc": due, "recurrence": "none"})

    def click(self, message_id, data):
        with mock.patch.object(self.agent, "reply"):
            self.agent.handle_callback({"id": "review", "from": {"id": 1}, "data": data,
                                        "message": {"message_id": message_id,
                                                    "chat": {"id": 1, "type": "private"}, "text": "draft"}})

    def test_explicit_nudge_reply_wins_over_unrelated_snooze_choice(self):
        target = self.add("payment", fired=True)
        other = self.add("other work")
        store.pending_set(self.conn, 1, "reminder_snooze_choice",
                          {"reminder_id": other, "source_reminder_id": other, "title": "other work"})
        original = dict(store.reminder_get(self.conn, other))
        for key, value in (("quiet_start", "0"), ("quiet_end", "0"),
                           ("proactive_days", "all"), ("proactive_enabled", "true")):
            store.pref_set(self.conn, key, value)
        self.agent.last_proactive = 0
        with mock.patch.object(self.agent, "reply", return_value={"message_id": 900}) as sent:
            self.agent.check_proactive()
        self.assertIn("payment", sent.call_args.args[1])
        self.assertEqual(self.agent.fired_reminders_for_message(900), [target])
        answer = self.reply_to_nudge("Перенеси на завтра")
        self.assertIn("payment", answer)
        self.assertEqual(dict(store.reminder_get(self.conn, other)), original)
        self.assertEqual(store.reminder_get(self.conn, target)["due_utc"], reminders.local_day_at(3, 1, 9))
        self.assertEqual(store.pending_get(self.conn, 1)["payload"]["reminder_id"], other)

    def test_delivered_nudge_binding_survives_restart(self):
        target = self.add("payment", fired=True)
        self.nudge([target])
        reopened = store.open_db(self.cfg.db_path)
        try:
            self.assertEqual(json.loads(store.kv_get(reopened, "fired_reminder_msgs"))["900"]["ids"], [target])
        finally:
            reopened.close()

    def test_failed_nudge_delivery_does_not_bind_or_replace_prior_context(self):
        target = self.add("payment", fired=True)
        store.kv_set(self.conn, "proactive_context", "original")
        self.nudge([target], delivered=False)
        self.assertEqual(self.agent.fired_reminders_for_message(900), [])
        self.assertEqual(store.kv_get(self.conn, "proactive_context"), "original")

    def test_batch_nudge_binds_only_visible_titles(self):
        ids = [self.add("work " + str(i), fired=True) for i in range(5)]
        self.nudge(ids)
        self.assertEqual(self.agent.fired_reminders_for_message(900), ids[:3])
        self.reply_to_nudge("готово 2")
        self.assertEqual([store.reminder_get(self.conn, rid)["status"] for rid in ids],
                         ["active", "done", "active", "active", "active"])

    def test_reply_to_closed_nudge_does_not_fall_back_to_other_reminder(self):
        target = self.add("payment", fired=True)
        other = self.add("other work", fired=True)
        self.nudge([target])
        store.reminder_close(self.conn, target, "done")
        self.agent._remember_reminder(other)
        original = self.state()
        self.assertIn("закрыто", self.reply_to_nudge("Перенеси на завтра"))
        self.assertEqual(self.state(), original)

    def test_old_draft_buttons_cannot_change_the_replacement(self):
        twin = self.add("A")
        for data in ("dr|set", "dr|no", "dr|time", f"dr|move|{twin}"):
            with self.subTest(data=data):
                self.draft("A", 910)
                self.draft("B", 911)
                before = self.state()
                self.click(910, data)
                self.assertEqual(self.state(), before)
                self.assertEqual(self.agent.answer_callback.call_args.args[1], T("ru", "card_expired"))

    def test_amended_same_title_draft_invalidates_the_original_card(self):
        due = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        self.draft("A", 910, due=due)
        pending = store.pending_get(self.conn, 1)
        with mock.patch.object(self.agent, "reply", return_value={"message_id": 911}):
            self.agent.resolve_pending(1, "amend", {"due_utc": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()}, pending, "ru")
        before = self.state()
        self.click(910, "dr|set")
        self.assertEqual(self.state(), before)
        self.click(911, "dr|set")
        self.assertEqual(len(store.reminders_active(self.conn, 1)), 1)

    def test_current_card_buttons_preserve_set_cancel_and_time_flows(self):
        self.draft("A", 910)
        self.click(910, "dr|set")
        self.assertEqual([r["title"] for r in store.reminders_active(self.conn, 1)], ["A"])
        self.draft("B", 911)
        self.click(911, "dr|time")
        self.assertEqual(store.pending_get(self.conn, 1)["kind"], "reminder_partial")
        self.draft("C", 912)
        self.click(912, "dr|no")
        self.assertIsNone(store.pending_get(self.conn, 1))

    def test_current_move_button_must_name_the_offered_twin(self):
        target = self.add("work A")
        wrong = self.add("work B")
        self.draft("work A", 910)
        before = self.state()
        self.click(910, f"dr|move|{wrong}")
        self.assertEqual(self.state(), before)
        self.agent.edit_message.assert_not_called()
        self.click(910, f"dr|move|{target}")
        self.assertIsNone(store.pending_get(self.conn, 1))
        self.assertEqual(len(store.reminders_active(self.conn, 1)), 2)

    def test_binding_preserves_pending_expiry_and_survives_restart(self):
        self.draft("A", 910)
        before = tuple(self.conn.execute("SELECT created_at,expires_at FROM pending_actions").fetchone())
        draft = store.pending_get(self.conn, 1)["payload"]
        with mock.patch.object(self.agent, "reply", return_value={"message_id": 911}):
            self.agent._send_reminder_draft(1, "ru", draft)
        self.assertEqual(tuple(self.conn.execute("SELECT created_at,expires_at FROM pending_actions").fetchone()), before)
        reopened = store.open_db(self.cfg.db_path)
        try:
            self.assertEqual(store.pending_get(reopened, 1)["payload"]["card_message_id"], 911)
        finally:
            reopened.close()

    def test_legacy_unbound_button_is_rejected_but_text_confirmation_still_works(self):
        due = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        store.pending_set(self.conn, 1, "reminder", {"title": "A", "due_utc": due, "recurrence": "none"})
        before = self.state()
        self.click(910, "dr|set")
        self.assertEqual(self.state(), before)
        with mock.patch.object(self.agent, "reply"):
            self.agent.dispatch(1, {"message_id": 920}, "да")
        self.assertEqual(len(store.reminders_active(self.conn, 1)), 1)

    def test_hourly_source_is_refused_before_router_or_pending_mutation(self):
        target = self.add("A", fired=True)
        store.pending_set(self.conn, 1, "reminder_fired", {"reminder_id": target, "title": "A"})
        before = self.state()
        with mock.patch.object(router, "route", side_effect=AssertionError("no model call")), \
                mock.patch.object(self.agent, "reply") as reply:
            self.agent.dispatch(1, {"message_id": 920}, "Напоминать каждый час до 21:00")
        self.assertEqual(self.state(), before)
        self.assertEqual(reply.call_args.args[1], T("ru", "reminder_schedule_unsupported"))

    def test_invalid_repeat_params_cannot_partially_reschedule(self):
        rid = self.add("A")
        self.agent._remember_reminder(rid)
        due = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        for extra in ({"recurrence": "hourly"}, {"recurrence": "daily"},
                      {"until_utc": due}, {"interval_minutes": 60}, {"repeat_count": 3}):
            with self.subTest(extra=extra):
                before = self.state()
                with mock.patch.object(self.agent, "reply") as reply:
                    self.agent.do_reschedule(1, "ru", dict(extra, due_utc=due))
                self.assertEqual(self.state(), before)
                self.assertEqual(reply.call_args.args[1], T("ru", "reminder_schedule_unsupported"))

    def test_model_cannot_omit_repeat_semantics_and_drop_fired_pending(self):
        rid = self.add("work", fired=True)
        store.pending_set(self.conn, 1, "reminder_fired", {"reminder_id": rid, "title": "work"})
        before = self.state()
        decision = {"action": "reminder_create", "params": {
            "title": "work", "due_utc": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            "recurrence": "none"}, "confidence": .99}
        with mock.patch.object(router, "route", return_value=decision), \
                mock.patch.object(self.agent, "reply") as reply:
            self.agent.dispatch(1, {"message_id": 920}, "Please remind me every hour until 21:00")
        self.assertEqual(self.state(), before)
        self.assertEqual(reply.call_args.args[1], T("ru", "reminder_schedule_unsupported"))

    def test_invalid_repeat_params_cannot_create_or_amend_draft(self):
        due = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        self.draft("original", 910)
        for method in (lambda: self.agent.do_reminder_create(1, "ru", {"title": "B", "due_utc": due, "recurrence": "hourly"}),
                       lambda: self.agent.resolve_pending(1, "amend", {"recurrence": "hourly"}, store.pending_get(self.conn, 1), "ru")):
            before = self.state()
            with mock.patch.object(self.agent, "reply"):
                method()
            self.assertEqual(self.state(), before)

    def test_router_rejects_unsupported_schedule_contract(self):
        for action in ("reminder_create", "reminder_reschedule"):
            route, reason = router.validate_route_reason({"action": action, "params": {"recurrence": "hourly"}, "confidence": .99}, False)
            self.assertIsNone(route)
            self.assertEqual(reason, "unsupported_reminder_schedule")

    def test_model_amend_cannot_partially_change_a_fired_reminder(self):
        rid = self.add("work", fired=True)
        store.pending_set(self.conn, 1, "reminder_fired", {"reminder_id": rid, "title": "work"})
        before = self.state()
        with mock.patch.object(self.agent, "reply"):
            self.agent.resolve_pending(1, "amend", {
                "due_utc": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                "recurrence": "hourly"}, store.pending_get(self.conn, 1), "ru")
        self.assertEqual(self.state(), before)

    def test_supported_schedule_forms_remain_supported(self):
        for rec in ("none", "daily", "weekly", "WEEKLY"):
            self.assertFalse(reminder_time.unsupported_schedule({"recurrence": rec}))
        for text in ("напомни каждый день в 9", "remind me every Monday at 9", "напомни через час", "snooze an hour"):
            self.assertFalse(reminder_time.unsupported_schedule({}, text))
        self.assertFalse(reminder_time.unsupported_schedule({}, 'напомни завтра купить книгу «Каждый час»'))
        self.assertFalse(reminder_time.unsupported_schedule({"due_utc": "anything"}, "перенеси на завтра", reschedule=True))

    def test_unavailable_repeat_forms_are_not_normalized_away(self):
        for text in ("Напоминать каждый час до 21:00", "remind me every 30 minutes", "remind me hourly",
                     "напомни ежемесячно", "remind me every other day", "remind me daily until Friday"):
            with self.subTest(text=text):
                self.assertTrue(reminder_time.unsupported_schedule({}, text))

    def text_reply(self, text, target, *, decision=None):
        update = {"update_id": 900002, "message": {
            "message_id": 930, "chat": {"id": 1, "type": "private"}, "from": {"id": 1},
            "date": int(datetime.now(timezone.utc).timestamp()), "text": text,
            "reply_to_message": {"message_id": target, "from": {"is_bot": True},
                                 "text": "Earlier reminder card"}}}
        with mock.patch.object(router, "route", **(
                {"return_value": decision} if decision else
                {"side_effect": AssertionError("no model routing")})), \
                mock.patch.object(self.agent, "reply", return_value={"message_id": 931}) as reply:
            self.agent.handle_update(update)
        return reply

    def test_old_draft_text_replies_preserve_replacement_and_expiry(self):
        for text in ("да", "нет", "завтра", "yes", "tomorrow"):
            with self.subTest(text=text):
                self.draft("old work", 910)
                self.draft("new work", 911)
                before = self.state()
                expiry = tuple(self.conn.execute("SELECT created_at,expires_at FROM pending_actions").fetchone())
                reply = self.text_reply(text, 910)
                self.assertEqual(self.state(), before)
                self.assertEqual(tuple(self.conn.execute("SELECT created_at,expires_at FROM pending_actions").fetchone()), expiry)
                lang = "en" if text in ("yes", "tomorrow") else "ru"
                self.assertEqual(reply.call_args.args[1], T(lang, "card_expired"))

    def test_current_draft_text_reply_and_unquoted_confirmation_still_work(self):
        self.draft("current work", 910)
        self.text_reply("да", 910)
        self.assertEqual([r["title"] for r in store.reminders_active(self.conn, 1)], ["current work"])
        self.draft("unquoted work", 911)
        with mock.patch.object(self.agent, "reply"):
            self.agent.dispatch(1, {"message_id": 932}, "да")
        self.assertEqual(len(store.reminders_active(self.conn, 1)), 2)

    def test_fired_reply_wins_over_draft_without_losing_draft(self):
        rid = self.add("fired work", fired=True)
        self.agent._remember_fired_message(900, [rid])
        self.draft("new work", 911)
        pending = tuple(self.conn.execute("SELECT * FROM pending_actions").fetchone())
        due = store.reminder_get(self.conn, rid)["due_utc"]
        self.text_reply("завтра", 900)
        self.assertNotEqual(store.reminder_get(self.conn, rid)["due_utc"], due)
        self.assertEqual(tuple(self.conn.execute("SELECT * FROM pending_actions").fetchone()), pending)

    def test_fired_reply_wins_over_partial_draft(self):
        rid = self.add("fired work", fired=True)
        self.agent._remember_fired_message(900, [rid])
        with mock.patch.object(self.agent, "reply", return_value={"message_id": 911}):
            self.agent.start_partial_reminder(1, "ru", {"title": "new work"})
        pending = tuple(self.conn.execute("SELECT * FROM pending_actions").fetchone())
        self.text_reply("готово", 900)
        self.assertEqual(store.reminder_get(self.conn, rid)["status"], "done")
        self.assertEqual(tuple(self.conn.execute("SELECT * FROM pending_actions").fetchone()), pending)

    def test_fired_reply_precedes_other_reminder_pending_guards(self):
        for kind, payload in (("reminder_expired", {"reminder_ids": []}),
                              ("reminder_op", {"ids": [], "op": "reschedule"})):
            with self.subTest(kind=kind):
                rid = self.add("fired work", fired=True)
                self.agent._remember_fired_message(900, [rid])
                store.pending_set(self.conn, 1, kind, payload)
                pending = tuple(self.conn.execute("SELECT * FROM pending_actions").fetchone())
                self.text_reply("да", 900)
                self.assertEqual(store.reminder_get(self.conn, rid)["status"], "done")
                self.assertEqual(tuple(self.conn.execute("SELECT * FROM pending_actions").fetchone()), pending)

    def test_closed_fired_reply_cannot_confirm_current_draft(self):
        rid = self.add("fired work", fired=True)
        self.agent._remember_fired_message(900, [rid])
        store.reminder_close(self.conn, rid, "done")
        self.draft("new work", 911)
        before = self.state()
        self.text_reply("да", 900)
        self.assertEqual(self.state(), before)

    def test_partial_prompt_reply_is_bound_and_stale_prompt_cannot_complete_it(self):
        for target in (910, 911):
            with self.subTest(target=target):
                self.draft("old work", 910)
                with mock.patch.object(self.agent, "reply", return_value={"message_id": 911}):
                    self.agent.start_partial_reminder(1, "ru", {"title": "new work"})
                before = self.state()
                self.text_reply("завтра", target)
                if target == 910:
                    self.assertEqual(self.state(), before)
                else:
                    pending = store.pending_get(self.conn, 1)
                    self.assertEqual(pending["kind"], "reminder")
                    self.assertEqual(pending["payload"]["title"], "new work")

    def test_model_confirmation_on_old_card_cannot_mutate_current_draft(self):
        for action in ("confirm", "cancel", "amend"):
            with self.subTest(action=action):
                self.draft("old work", 910)
                self.draft("new work", 911)
                before = self.state()
                decision = {"action": action, "params": {"title": "wrong work"}, "confidence": .99}
                self.text_reply("Пожалуйста, именно так и сделай", 910, decision=decision)
                self.assertEqual(self.state(), before)

    def test_model_reply_on_old_card_cannot_mutate_partial_draft(self):
        self.draft("old work", 910)
        with mock.patch.object(self.agent, "reply", return_value={"message_id": 911}):
            self.agent.start_partial_reminder(1, "ru", {"title": "new work"})
        for action in ("confirm", "cancel", "amend"):
            with self.subTest(action=action):
                before = self.state()
                decision = {"action": action, "params": {
                    "due_utc": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}, "confidence": .99}
                self.text_reply("Пожалуйста, именно так и сделай", 910, decision=decision)
                self.assertEqual(self.state(), before)

    def test_partial_binding_preserves_expiry_and_restart(self):
        with mock.patch.object(self.agent, "reply", return_value={"message_id": 911}):
            self.agent.start_partial_reminder(1, "ru", {"title": "new work"})
        pending = store.pending_get(self.conn, 1)
        before = tuple(self.conn.execute("SELECT created_at,expires_at FROM pending_actions").fetchone())
        with mock.patch.object(self.agent, "reply", return_value={"message_id": 912}):
            self.agent._send_partial_reminder_prompt(1, "ru", pending["payload"])
        self.assertEqual(tuple(self.conn.execute("SELECT created_at,expires_at FROM pending_actions").fetchone()), before)
        reopened = store.open_db(self.cfg.db_path)
        try:
            self.assertEqual(store.pending_get(reopened, 1)["payload"]["card_message_id"], 912)
        finally:
            reopened.close()

    def test_russian_interval_and_monthly_sources_survive_model_omission(self):
        for text in ("Напоминай каждые два часа", "Напоминай раз в месяц в 9:00",
                     "Напоминай каждые три дня", "Напоминай два раза в неделю"):
            with self.subTest(text=text):
                self.draft("original work", 910)
                before = self.state()
                decision = {"action": "reminder_create", "params": {
                    "title": "work", "due_utc": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                    "recurrence": "none"}, "confidence": .99}
                with mock.patch.object(router, "route", return_value=decision), \
                        mock.patch.object(self.agent, "reply") as reply:
                    self.agent.dispatch(1, {"message_id": 932}, text)
                self.assertEqual(self.state(), before)
                self.assertEqual(reply.call_args.args[1], T("ru", "reminder_schedule_unsupported"))

    def test_supported_repeat_phrasing_and_quoted_titles_stay_supported(self):
        for text in ("напомни раз в день в 9", "напомни раз в неделю в понедельник",
                     "напомни один раз завтра", "напомни через два часа",
                     'напомни завтра прочитать «Раз в месяц»',
                     'напомни завтра прочитать "Каждые два часа"'):
            with self.subTest(text=text):
                self.assertFalse(reminder_time.unsupported_schedule({}, text))

    def test_repeat_followup_preserves_pending_before_router(self):
        self.draft("original work", 910)
        for text in ("раз в месяц", "каждые два часа", "два раза в неделю"):
            with self.subTest(text=text):
                before = self.state()
                with mock.patch.object(router, "route", side_effect=AssertionError("no model call")), \
                        mock.patch.object(self.agent, "reply") as reply:
                    self.agent.dispatch(1, {"message_id": 932}, text)
                self.assertEqual(self.state(), before)
                self.assertEqual(reply.call_args.args[1], T("ru", "reminder_schedule_unsupported"))


if __name__ == "__main__":
    unittest.main()
