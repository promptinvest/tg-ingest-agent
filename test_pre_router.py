"""Characterize dispatch precedence before moving the guards into a table.

The matrix drives the real dispatcher and real pending slot. Competing skill
resolvers are fake boundaries so a change in the winning guard is observable
without models, Telegram, or a second state-changing action.
"""
import copy
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import llm
import router
import store
import tg_ingest_agent as entry
from testlib import make_config


class PreRouterPrecedenceTests(unittest.TestCase):
    # Includes slots consumed only after model routing: they must not accidentally
    # gain the no-pending shortcuts when dispatch is converted to a table.
    PENDING_KINDS = (
        "purge", "journal_correction", "journal_edit", "media_capture", "category",
        "reminder", "reminder_partial", "reminder_fired", "reminder_op",
        "reminder_expired", "reminder_snooze_choice", "calendar_partial",
        "journal_prompt", "note_retitle", "note_archive", "delete", "note_edit",
        "boss_sensitive", "habit",
    )
    GUARDS = {
        "_disable_note_review_request": False,
        "_reminder_pref_request": False,
        "report_problem_request": (False, ""),
        "resolve_fleet_confirmation_text": False,
        "resolve_journal_correction": False,
        "resolve_gratitude_correction": False,
        "resolve_media_correction": False,
        "explicit_category": "",
        "rejects_category_suggestion": False,
        "_reject_reminder_schedule": False,
        "resolve_snooze_choice_text": False,
        "resolve_fired_followup": False,
        "_resolve_reminder_op": False,
        "resolve_expired_reopen_text": False,
        "resolve_reminder_draft_text": False,
        "resolve_named_reschedule_text": False,
        "_fired_gratitude_category": "",
        "gratitude_invitation": None,
        "_gratitude_prefix_capture": None,
        "_dispatch_numbered_delete": False,
        "resolve_task_approval_text": False,
        "_resolve_proactive_followup": False,
        "continue_partial_reminder": False,
        "continue_partial_calendar": False,
    }

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.agent = entry.Agent(make_config(
            ALLOWED_CHAT_IDS="1", DB_PATH=":memory:",
            MEDIA_DIR=str(Path(self.temp.name) / "media")))
        self.addCleanup(self.agent.conn.close)
        self.boundaries = {}
        for name, result in self.GUARDS.items():
            self.boundaries[name] = self.patch(self.agent, name, return_value=result)
        for name in (
                "send_chat_action", "reply", "reply_operational", "react", "do_converse",
                "_note_route_correction", "_trace_route", "do_report_problem",
                "resolve_purge", "resolve_journal_edit", "resolve_pending",
                "_deterministic_capture", "_run_compound"):
            self.boundaries[name] = self.patch(self.agent, name)
        self.stash = self.patch(self.agent, "_media_stash", return_value={"entries": ["fixture"]})
        self.route = self.patch(router, "route", return_value={
            "action": "converse", "params": {}, "confidence": 1.0})
        self.patch(llm, "chat_profile", side_effect=AssertionError("no paid model"))
        self.patch(llm, "embed", side_effect=AssertionError("no embedding"))
        self.patch(entry, "tg_call", side_effect=AssertionError("no Telegram"))

    @property
    def conn(self):
        return self.agent.conn

    def patch(self, target, name, **kwargs):
        patcher = mock.patch.object(target, name, **kwargs)
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def reset_case(self, kind=None, payload=None):
        store.pending_clear(self.conn, 1)
        if kind:
            store.pending_set(self.conn, 1, kind, payload or {"title": "fixture task"})
        self.agent.turn_reply_reminder_id = None
        for name, boundary in self.boundaries.items():
            boundary.reset_mock()
            boundary.side_effect = None
            boundary.return_value = copy.deepcopy(self.GUARDS.get(name))
        self.route.reset_mock()
        self.route.side_effect = None
        self.route.return_value = {"action": "converse", "params": {}, "confidence": 1.0}
        self.stash.return_value = {"entries": ["fixture"]}

    def dispatch(self, text, **message):
        self.agent.dispatch(1, {"message_id": 901, "text": text, **message}, text)

    def assert_winner(self, action, source):
        self.boundaries["_trace_route"].assert_called_once_with(action, source)
        self.route.assert_not_called()

    def test_preferences_problem_report_and_fleet_decisions_precede_every_pending_kind(self):
        cases = (
            ("_disable_note_review_request", True, "Не предлагай разбирать заметки",
             "proactive_prefs", "note_review_pref"),
            ("_reminder_pref_request", True, "Не сообщай о просроченных напоминаниях",
             "proactive_prefs", "reminder_pref"),
            ("report_problem_request", (True, "wrong reminder"), "Запиши в проблемы",
             None, None),
            ("resolve_fleet_confirmation_text", True, "да", "fleet_maintain", "fleet_confirmation_text"),
        )
        for kind in (None, *self.PENDING_KINDS):
            for name, result, text, action, source in cases:
                with self.subTest(pending=kind, command=name):
                    self.reset_case(kind)
                    self.boundaries[name].return_value = result
                    before = store.pending_get(self.conn, 1)
                    self.dispatch(text)
                    if action:
                        self.assert_winner(action, source)
                    else:
                        self.boundaries["do_report_problem"].assert_called_once_with(
                            1, self.agent.lang(), {"detail": "wrong reminder"}, text)
                        self.route.assert_not_called()
                    self.boundaries["resolve_purge"].assert_not_called()
                    self.boundaries["resolve_journal_edit"].assert_not_called()
                    self.assertEqual(store.pending_get(self.conn, 1), before)

    def test_card_winner_matrix(self):
        cases = (
            ("purge", "да", {}, "purge", "pending_purge_phrase", "resolve_purge"),
            ("journal_correction", "да", {"resolve_journal_correction": True},
             "recategorize", "journal_correction_confirmation", "resolve_journal_correction"),
            ("journal_edit", "исправленный текст", {},
             "ingest", "pending_journal_edit", "resolve_journal_edit"),
            ("media_capture", "убери №3", {"resolve_media_correction": True},
             "ingest", "pending_media_correction", "resolve_media_correction"),
            ("category", "Категория - Документы", {"explicit_category": "Документы"},
             "recategorize", "explicit_category", "resolve_pending"),
            ("category", "не та категория", {"rejects_category_suggestion": True},
             "recategorize", "category_rejected", "reply"),
            ("reminder_op", "второе", {"_resolve_reminder_op": True},
             "reminder_reschedule", "pending_reminder_pick", "_resolve_reminder_op"),
            ("reminder_expired", "да", {"resolve_expired_reopen_text": True},
             "reminder_undo", "expired_reopen_text", "resolve_expired_reopen_text"),
            ("reminder", "да", {"resolve_reminder_draft_text": True},
             "reminder_create", "reminder_draft_text", "resolve_reminder_draft_text"),
            ("reminder_partial", "завтра вечером", {"resolve_reminder_draft_text": True},
             "reminder_create", "reminder_draft_text", "resolve_reminder_draft_text"),
            ("reminder_snooze_choice", "да", {"resolve_snooze_choice_text": True},
             "reminder_reschedule", "snooze_choice", "resolve_snooze_choice_text"),
            ("reminder_fired", "готово", {"resolve_fired_followup": True},
             "reminder_reschedule", "fired_followup", "resolve_fired_followup"),
        )
        for kind, text, overrides, action, source, handler in cases:
            with self.subTest(pending=kind, text=text):
                self.reset_case(kind)
                for name, value in overrides.items():
                    self.boundaries[name].return_value = value
                self.dispatch(text, reply_to_message={"message_id": 811})
                self.assert_winner(action, source)
                self.boundaries[handler].assert_called_once()
                if kind == "journal_correction":
                    self.assertEqual(self.boundaries[handler].call_args.kwargs["card_id"], 811)
                if source == "explicit_category":
                    self.assertEqual(self.boundaries[handler].call_args.args[1:3],
                                     ("amend", {"category": "Документы"}))

    def test_pending_only_router_slots_keep_their_slot_and_skip_numbered_delete(self):
        kinds = ("calendar_partial", "journal_prompt", "note_retitle", "note_archive",
                 "delete", "note_edit", "boss_sensitive", "habit")
        for kind in kinds:
            with self.subTest(pending=kind):
                self.reset_case(kind)
                self.boundaries["_dispatch_numbered_delete"].return_value = True
                before = store.pending_get(self.conn, 1)
                self.dispatch("удали #3")
                self.route.assert_called_once()
                self.assertEqual(self.route.call_args.args[4], before)
                self.boundaries["_dispatch_numbered_delete"].assert_not_called()
                self.assertEqual(store.pending_get(self.conn, 1), before)

    def test_preference_and_card_order_when_more_than_one_resolver_could_consume(self):
        rows = (
            ("_disable_note_review_request", "_reminder_pref_request", "note_review_pref"),
            ("_reminder_pref_request", "resolve_fleet_confirmation_text", "reminder_pref"),
            ("resolve_fleet_confirmation_text", "resolve_gratitude_correction", "fleet_confirmation_text"),
            ("resolve_journal_correction", "resolve_gratitude_correction", "journal_correction_confirmation"),
        )
        for first, later, source in rows:
            with self.subTest(first=first, later=later):
                self.reset_case("journal_correction")
                self.boundaries[first].return_value = True
                self.boundaries[later].return_value = True
                self.dispatch("да")
                self.assertEqual(self.boundaries["_trace_route"].call_args.args[1], source)
                self.boundaries[later].assert_not_called()

    def test_source_gratitude_correction_precedes_journal_edit(self):
        self.reset_case("journal_edit")
        self.boundaries["resolve_gratitude_correction"].return_value = True
        self.dispatch("это благодарность за вчера")
        self.assert_winner("recategorize", "journal_source_correction")
        self.boundaries["resolve_journal_edit"].assert_not_called()

    def test_schedule_rejection_precedes_reminder_pick_and_bound_alarm(self):
        for kind in ("reminder", "reminder_partial", "reminder_fired",
                     "reminder_snooze_choice", "reminder_op"):
            with self.subTest(pending=kind):
                self.reset_case(kind)
                self.agent.turn_reply_reminder_id = 42
                self.boundaries["_reject_reminder_schedule"].return_value = True
                self.boundaries["_resolve_reminder_op"].return_value = True
                self.boundaries["resolve_snooze_choice_text"].return_value = True
                before = store.pending_get(self.conn, 1)
                self.dispatch("каждые три часа")
                self.assert_winner("clarify", "unsupported_reminder_schedule")
                self.boundaries["_resolve_reminder_op"].assert_not_called()
                self.boundaries["resolve_snooze_choice_text"].assert_not_called()
                self.assertEqual(store.pending_get(self.conn, 1), before)

    def test_bound_alarm_followup_precedes_draft_pick_and_expired_reopen(self):
        for kind in ("reminder", "reminder_partial", "reminder_op", "reminder_expired"):
            for winner in ("resolve_snooze_choice_text", "resolve_fired_followup"):
                with self.subTest(pending=kind, winner=winner):
                    self.reset_case(kind)
                    self.agent.turn_reply_reminder_id = 42
                    self.boundaries[winner].return_value = True
                    self.boundaries["resolve_expired_reopen_text"].return_value = True
                    self.boundaries["resolve_reminder_draft_text"].return_value = True
                    self.boundaries["_resolve_reminder_op"].return_value = True
                    self.dispatch("через час")
                    source = "snooze_choice" if winner == "resolve_snooze_choice_text" else "fired_followup"
                    self.assert_winner("reminder_reschedule", source)
                    self.boundaries["_resolve_reminder_op"].assert_not_called()
                    self.boundaries["resolve_expired_reopen_text"].assert_not_called()
                    self.boundaries["resolve_reminder_draft_text"].assert_not_called()

    def test_unbound_reminder_order_and_second_followup_attempt_are_preserved(self):
        self.reset_case("reminder_expired")
        self.boundaries["resolve_expired_reopen_text"].return_value = True
        self.boundaries["resolve_reminder_draft_text"].return_value = True
        self.dispatch("да")
        self.assert_winner("reminder_undo", "expired_reopen_text")
        self.boundaries["resolve_reminder_draft_text"].assert_not_called()

        self.reset_case("reminder")
        self.boundaries["resolve_reminder_draft_text"].return_value = True
        self.boundaries["resolve_snooze_choice_text"].return_value = True
        self.dispatch("через час")
        self.assert_winner("reminder_create", "reminder_draft_text")
        self.boundaries["resolve_snooze_choice_text"].assert_not_called()

        self.reset_case("reminder_fired")
        self.agent.turn_reply_reminder_id = 42
        self.boundaries["resolve_snooze_choice_text"].side_effect = [False, True]
        self.dispatch("через час")
        self.assert_winner("reminder_reschedule", "snooze_choice")
        self.assertEqual(self.boundaries["resolve_snooze_choice_text"].call_count, 2)
        self.assertEqual(self.boundaries["resolve_fired_followup"].call_count, 1)

    def test_empty_media_stash_and_abandoned_reminder_pick_free_slot_for_normal_routing(self):
        for kind in ("media_capture", "reminder_op"):
            with self.subTest(pending=kind):
                self.reset_case(kind)
                self.stash.return_value = {}
                self.dispatch("show the notes I saved yesterday")
                self.route.assert_called_once()
                self.assertIsNone(self.route.call_args.args[4])
                self.assertIsNone(store.pending_get(self.conn, 1))
                self.boundaries["_dispatch_numbered_delete"].assert_called_once()

    def test_pending_refresh_after_snooze_resolver_preserves_new_slot(self):
        self.reset_case()
        def changed_slot(*args):
            store.pending_set(self.conn, 1, "category", {"row_id": 17})
            return False
        self.boundaries["resolve_snooze_choice_text"].side_effect = changed_slot
        self.dispatch("show the notes I saved yesterday")
        self.route.assert_called_once()
        self.assertEqual(self.route.call_args.args[4], {"kind": "category", "payload": {"row_id": 17}})
        self.boundaries["_dispatch_numbered_delete"].assert_not_called()

    def test_named_reschedule_precedes_generic_fired_followup(self):
        self.reset_case("reminder_fired")
        self.boundaries["resolve_named_reschedule_text"].return_value = True
        self.boundaries["resolve_fired_followup"].return_value = True
        self.dispatch("перенеси оплату завтра на 14")
        self.assert_winner("reminder_reschedule", "named_time")
        self.boundaries["resolve_fired_followup"].assert_not_called()

    def test_fired_content_restores_pending_on_model_or_budget_failure(self):
        for failure in (llm.LLMError("fixture unavailable"), llm.BudgetExceeded("day", 1, 1)):
            with self.subTest(failure=type(failure).__name__):
                self.reset_case("reminder_fired", {"title": "bank task", "reminder_id": 17})
                before = store.pending_get(self.conn, 1)
                self.route.side_effect = failure
                self.dispatch("show the notes I saved yesterday")
                self.assertIsNone(self.route.call_args.args[4])
                self.assertEqual(store.pending_get(self.conn, 1), before)
                self.boundaries["reply_operational"].assert_called_once()

    def test_schedule_failure_restores_exact_fired_pending_clock(self):
        self.reset_case("reminder_fired", {"title": "bank task", "reminder_id": 17})
        self.conn.execute("UPDATE pending_actions SET created_at=?, expires_at=? WHERE chat_id=1", (
            (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat(),
            (datetime.now(timezone.utc) + timedelta(minutes=19)).isoformat()))
        self.conn.commit()
        before = tuple(self.conn.execute("SELECT * FROM pending_actions WHERE chat_id=1").fetchone())
        self.route.return_value = {"action": "clarify", "params": {}, "confidence": 1,
                                   "invalid": "unsupported_reminder_schedule"}
        self.dispatch("show the notes I saved yesterday")
        self.assertEqual(tuple(self.conn.execute("SELECT * FROM pending_actions WHERE chat_id=1").fetchone()), before)
        self.boundaries["_trace_route"].assert_called_once_with("clarify", "unsupported_reminder_schedule")

    def test_gratitude_capture_precedes_compound_delete_and_ack(self):
        self.reset_case("reminder_fired", {"title": "Благодарность"})
        self.boundaries["_fired_gratitude_category"].side_effect = lambda pending, msg: (
            "Благодарность" if pending else "")
        self.dispatch("За спокойный вечер")
        self.boundaries["_deterministic_capture"].assert_called_once()
        self.assertEqual(self.boundaries["_deterministic_capture"].call_args.kwargs["source"], "fired_gratitude")
        self.route.assert_not_called()
        self.boundaries["_dispatch_numbered_delete"].assert_not_called()

        self.reset_case()
        self.boundaries["_gratitude_prefix_capture"].return_value = ("Благодарность", "спокойный вечер")
        self.dispatch("благодарность: спокойный вечер")
        self.assertEqual(self.boundaries["_deterministic_capture"].call_args.kwargs,
                         {"source": "gratitude_prefix", "body": "спокойный вечер"})
        self.route.assert_not_called()

    def test_replied_and_delayed_gratitude_keep_their_distinct_source_bindings(self):
        for source in ("replied_gratitude", "delayed_gratitude"):
            with self.subTest(source=source):
                self.reset_case()
                self.boundaries["_fired_gratitude_category"].side_effect = lambda pending, msg: (
                    "Благодарность" if pending else "")
                if source == "replied_gratitude":
                    self.agent.turn_reply_reminder_id = store.reminder_add(
                        self.conn, 1, "Благодарность",
                        (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), "none")
                else:
                    self.boundaries["gratitude_invitation"].return_value = {"title": "Благодарность"}
                self.dispatch("За спокойный вечер")
                self.boundaries["_deterministic_capture"].assert_called_once()
                self.assertEqual(self.boundaries["_deterministic_capture"].call_args.kwargs["source"], source)
                self.route.assert_not_called()
                self.boundaries["_dispatch_numbered_delete"].assert_not_called()

    def test_compound_then_numbered_delete_then_task_approval_then_proactive_winners(self):
        self.reset_case()
        with mock.patch.object(router, "split_compound", return_value=["first", "second"]):
            self.dispatch("first, then second")
        self.boundaries["_run_compound"].assert_called_once()
        self.boundaries["_dispatch_numbered_delete"].assert_not_called()

        self.reset_case()
        self.boundaries["_dispatch_numbered_delete"].return_value = True
        self.boundaries["resolve_task_approval_text"].return_value = True
        self.dispatch("удали #3")
        self.boundaries["resolve_task_approval_text"].assert_not_called()
        self.route.assert_not_called()

        self.reset_case()
        self.boundaries["resolve_task_approval_text"].return_value = True
        self.boundaries["_resolve_proactive_followup"].return_value = True
        self.dispatch("давай")
        self.assert_winner("task_start", "task_approval_text")
        self.boundaries["_resolve_proactive_followup"].assert_not_called()

        self.reset_case()
        self.boundaries["_resolve_proactive_followup"].return_value = True
        self.dispatch("давай")
        self.assert_winner("proactive_heartbeat", "proactive_followup")
        self.boundaries["react"].assert_not_called()

    def test_pending_slot_and_existing_fragment_disable_compound_splitting(self):
        for kind, in_compound in (("category", False), (None, True)):
            with self.subTest(pending=kind, in_compound=in_compound):
                self.reset_case(kind)
                self.agent._in_compound = in_compound
                with mock.patch.object(router, "split_compound", return_value=["first", "second"]) as split:
                    self.dispatch("first, then second")
                split.assert_not_called()
                self.boundaries["_run_compound"].assert_not_called()
                self.route.assert_called_once()
        self.agent._in_compound = False

    def test_question_aware_ack_uses_router_only_for_an_open_question_or_pending_card(self):
        self.reset_case()
        self.dispatch("да")
        self.assert_winner("smalltalk", "smalltalk_ack")
        self.boundaries["react"].assert_called_once_with(1, 901, "👍")

        self.reset_case()
        store.convo_add(self.conn, 1, "bot", "Как назвать заметку?")
        self.dispatch("да")
        self.route.assert_called_once()
        self.boundaries["react"].assert_not_called()

        self.reset_case("category")
        self.dispatch("да")
        self.route.assert_called_once()
        self.boundaries["react"].assert_not_called()

    def test_liveness_precedes_route_correction_and_stale_gratitude_invitation(self):
        self.reset_case()
        self.boundaries["gratitude_invitation"].return_value = {"title": "Благодарность"}
        self.dispatch("пинг")
        self.assert_winner("converse", "liveness_ping")
        self.boundaries["_note_route_correction"].assert_not_called()
        self.boundaries["gratitude_invitation"].assert_not_called()
        self.boundaries["reply"].assert_called_once()


if __name__ == "__main__":
    unittest.main()
