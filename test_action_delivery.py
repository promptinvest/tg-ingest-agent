"""Crash/rejection qualification for reminder effects and delivery-only recovery."""
import builtins
import json
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import action_delivery
import inference_io
import llm
import relationship
import store
import tg_ingest_agent as entry
from testlib import make_config
from tg_api import TelegramError


class ProcessDeath(BaseException):
    pass


class ActionDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cfg = make_config(ALLOWED_CHAT_IDS="1", DB_PATH=str(Path(self.temp.name) / "db"),
                               MEDIA_DIR=str(Path(self.temp.name) / "media"), REMINDER_FIRED_EXPIRE_DAYS="1")
        self.sent = []
        self.transport = mock.patch.object(entry, "tg_call", side_effect=self.send).start()
        self.addCleanup(mock.patch.stopall)
        self.agent = entry.Agent(self.cfg)
        self.addCleanup(lambda: self.agent.conn.close())

    def send(self, token, method, params, **kwargs):
        self.assertFalse(self.agent.conn.in_transaction, "transport must follow the local commit")
        self.sent.append((method, params))
        return {"message_id": len(self.sent) + 100, "text": params.get("text", "")}

    @property
    def conn(self):
        return self.agent.conn

    def restart(self):
        self.agent.conn.close()
        self.agent = entry.Agent(self.cfg)

    def scope(self, text, update=900):
        self.agent._active_update_id = update
        self.agent._reminder_scope = action_delivery.scope_for_update(update, text)

    def draft(self, title="confirm example"):
        return {"title": title, "due_utc": "2099-01-01T09:00:00+00:00",
                "recurrence": "none", "card_message_id": 77}

    def pending(self, draft=None):
        store.pending_set(self.conn, 1, "reminder", draft or self.draft())
        return store.pending_get(self.conn, 1)

    def reject_source_entry_import(self):
        original = builtins.__import__

        def guarded(name, *args, **kwargs):
            if name == "tg_ingest_agent":
                raise ModuleNotFoundError("No module named 'tg_ingest_agent'")
            return original(name, *args, **kwargs)

        return mock.patch.object(builtins, "__import__", side_effect=guarded)

    def test_delivery_resolves_live_transport_after_agent_creation(self):
        pending = self.pending()
        self.scope("yes", 991)
        replacement_calls = []

        def replacement(*args, **kwargs):
            replacement_calls.append((args, kwargs))
            return {"message_id": 200, "text": args[2].get("text", "")}

        self.assertIs(self.agent._action_delivery_sender(), self.transport)
        with mock.patch.object(entry, "tg_call", side_effect=replacement) as current:
            self.assertIs(self.agent._action_delivery_sender(), current)
            with self.reject_source_entry_import():
                self.agent.resolve_pending(1, "confirm", {}, pending, "en")
        self.assertEqual(len(replacement_calls), 1)
        self.assertEqual(len(self.sent), 0)
        self.assertIs(self.agent._action_delivery_sender(), self.transport)
        self.assertEqual(self.conn.execute("SELECT state FROM reply_outbox").fetchone()[0], "sent")
        self.assertEqual(len(store.reminders_active(self.conn, 1)), 1)

    def test_noop_expiry_sweep_needs_no_source_entry_module_or_transport(self):
        with self.reject_source_entry_import(), mock.patch.object(self.agent, "_reping_stale_fired"), \
                mock.patch.object(llm, "chat_profile", side_effect=AssertionError("no model call")):
            self.agent.check_reminder_expiry()
        self.assertEqual(len(self.sent), 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM reply_outbox").fetchone()[0], 0)
        self.assertEqual(store.reminders_active(self.conn, 1), [])

    def test_ordinary_dispatch_crosses_declined_reminder_handlers_without_source_import(self):
        with self.reject_source_entry_import(), mock.patch.object(self.agent, "send_chat_action"), \
                mock.patch.object(entry.router, "route", return_value={"action": "reminder_list", "params": {}, "confidence": 1}) as route, \
                mock.patch.object(llm, "chat_profile", side_effect=AssertionError("no model call")):
            self.agent.dispatch(1, {"message_id": 51}, "show my reminders")
        route.assert_called_once()
        self.assertEqual([method for method, _params in self.sent], ["sendMessage"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM reply_outbox WHERE state='sent'").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0], 0)

    def test_draft_callback_delivers_once_without_source_entry_import(self):
        self.pending()
        self.scope("callback", 992)
        with self.reject_source_entry_import(), \
                mock.patch.object(llm, "chat_profile", side_effect=AssertionError("no model call")):
            self.agent.handle_reminder_callback("cb", 1, {"message_id": 77, "text": "draft"}, "dr|set")
            self.agent.handle_reminder_callback("cb", 1, {"message_id": 77, "text": "draft"}, "dr|set")
        self.assertEqual(len(store.reminders_active(self.conn, 1)), 1)
        self.assertIsNone(store.pending_get(self.conn, 1))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0], 1)
        self.assertEqual([method for method, _params in self.sent], ["sendMessage", "answerCallbackQuery", "editMessageText"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM reply_outbox WHERE state='sent'").fetchone()[0], 3)

    def test_failed_reply_retries_delivery_after_inbound_is_done(self):
        update = {"update_id": 901, "message": {"message_id": 1, "chat": {"id": 1}, "from": {"id": 1}, "text": "hi"}}
        self.transport.side_effect = TelegramError("rejected", status=429, retry_after=1)
        with mock.patch.object(self.agent, "handle_update", side_effect=lambda u: self.agent.reply(1, "answer")) as handler:
            self.agent.process_update_batch([update])
            self.assertEqual(handler.call_count, 1)
        self.assertEqual(store.telegram_update_get(self.conn, 901)["status"], "done")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM conversation WHERE role='bot'").fetchone()[0], 0)
        self.restart()
        self.conn.execute("UPDATE reply_outbox SET available_at='2000-01-01'"); self.conn.commit()
        self.transport.side_effect = self.send
        self.agent.check_reply_deliveries()
        self.agent.process_update_batch([update])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM conversation WHERE role='bot'").fetchone()[0], 1)

    def test_unknown_delivery_is_terminal_and_never_automatically_resent(self):
        self.transport.side_effect = TelegramError("timeout", outcome_unknown=True)
        self.assertIsNone(self.agent.reply(1, "answer"))
        self.restart()
        self.agent.check_reply_deliveries()
        self.assertEqual(self.transport.call_count, 1)
        row = self.conn.execute("SELECT * FROM reply_outbox").fetchone()
        self.assertEqual((row["state"], row["attempts"]), ("ambiguous", 1))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM issues WHERE kind='reply_delivery_ambiguous'").fetchone()[0], 1)

    def test_interrupted_sending_recovers_as_ambiguous(self):
        row = action_delivery.enqueue(self.agent, "sendMessage", {"chat_id": 1, "text": "answer"})
        self.conn.execute("UPDATE reply_outbox SET state='sending',attempts=1 WHERE id=?", (row["id"],)); self.conn.commit()
        self.restart()
        self.agent.check_reply_deliveries()
        self.assertEqual(self.transport.call_count, 0)
        self.assertEqual(self.conn.execute("SELECT state FROM reply_outbox").fetchone()[0], "ambiguous")

    def test_create_before_commit_death_rolls_back_effect_and_preserves_pending(self):
        pending = self.pending()
        self.scope("yes")
        with mock.patch.object(relationship, "log_event", side_effect=ProcessDeath):
            with self.assertRaises(ProcessDeath):
                self.agent.resolve_pending(1, "confirm", {}, pending, "en")
        self.restart()
        self.assertEqual(len(store.reminders_active(self.conn, 1)), 0)
        self.assertIsNotNone(store.pending_get(self.conn, 1))
        self.scope("yes")
        self.agent.resolve_pending(1, "confirm", {}, store.pending_get(self.conn, 1), "en")
        self.assertEqual(len(store.reminders_active(self.conn, 1)), 1)
        self.assertIsNone(store.pending_get(self.conn, 1))

    def test_committed_create_replay_delivers_saved_result_without_recreating(self):
        pending = self.pending()
        self.scope("yes")
        with mock.patch.object(action_delivery, "deliver_action", side_effect=ProcessDeath):
            with self.assertRaises(ProcessDeath):
                self.agent.resolve_pending(1, "confirm", {}, pending, "en")
        self.restart()
        self.scope("yes")
        with mock.patch.object(self.agent, "_dispatch", side_effect=AssertionError("must not reroute")):
            self.agent.dispatch(1, {"message_id": 1}, "yes")
        self.assertEqual(len(store.reminders_active(self.conn, 1)), 1)
        self.assertIsNone(store.pending_get(self.conn, 1))
        self.assertEqual(len(self.sent), 1)

    def test_ordinal_cancel_replay_keeps_next_reminder_active(self):
        first = store.reminder_add(self.conn, 1, "first", "2099-01-01T09:00:00+00:00")
        second = store.reminder_add(self.conn, 1, "second", "2099-01-02T09:00:00+00:00")
        self.scope("cancel #1")
        with mock.patch.object(action_delivery, "deliver_action", side_effect=ProcessDeath):
            with self.assertRaises(ProcessDeath):
                self.agent.do_reminder_cancel(1, "en", {"id": 1})
        self.restart()
        self.scope("cancel #1")
        self.agent.dispatch(1, {"message_id": 1}, "cancel #1")
        self.assertEqual(store.reminder_get(self.conn, first)["status"], "cancelled")
        self.assertEqual(store.reminder_get(self.conn, second)["status"], "active")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM reminder_events WHERE event='closed'").fetchone()[0], 1)

    def test_ordinal_reschedule_replay_does_not_move_new_first_position(self):
        first = store.reminder_add(self.conn, 1, "first", "2099-01-01T09:00:00+00:00")
        second = store.reminder_add(self.conn, 1, "second", "2099-01-02T09:00:00+00:00")
        later = "2099-01-04T09:00:00+00:00"
        self.scope("move #1")
        with mock.patch.object(action_delivery, "deliver_action", side_effect=ProcessDeath):
            with self.assertRaises(ProcessDeath):
                self.agent.do_reschedule(1, "en", {"id": 1, "due_utc": later})
        self.restart()
        self.scope("move #1")
        self.agent.dispatch(1, {"message_id": 1}, "move #1")
        self.assertEqual(store.reminder_get(self.conn, first)["due_utc"], later)
        self.assertEqual(store.reminder_get(self.conn, second)["due_utc"], "2099-01-02T09:00:00+00:00")

    def test_move_failure_preserves_exact_confirmation_for_retry(self):
        rid = store.reminder_add(self.conn, 1, "twin", "2099-01-01T09:00:00+00:00")
        draft = self.draft("twin"); draft.update(twin_id=rid, due_utc="2099-01-04T09:00:00+00:00")
        self.pending(draft)
        self.scope("callback")
        with mock.patch.object(store, "reminder_update_due", side_effect=sqlite3.OperationalError("outage")):
            with self.assertRaises(sqlite3.Error):
                self.agent._handle_draft_callback("cb", 1, {"message_id": 77, "text": "draft"}, "move", rid)
        self.assertEqual(store.pending_get(self.conn, 1)["payload"]["card_message_id"], 77)
        self.assertEqual(store.reminder_get(self.conn, rid)["due_utc"], "2099-01-01T09:00:00+00:00")
        self.agent._handle_draft_callback("cb", 1, {"message_id": 77, "text": "draft"}, "move", rid)
        self.assertEqual(store.reminder_get(self.conn, rid)["due_utc"], draft["due_utc"])
        self.assertIsNone(store.pending_get(self.conn, 1))

    def old_reminder(self):
        old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        rid = store.reminder_add(self.conn, 1, "old", old)
        self.conn.execute("UPDATE reminders SET last_fired_at=? WHERE id=?", (old, rid)); self.conn.commit()
        return rid

    def test_expiry_announcement_retries_after_committed_expiration(self):
        rid = self.old_reminder()
        self.transport.side_effect = TelegramError("rate limit", status=429)
        with mock.patch.object(self.agent, "_reping_stale_fired"):
            self.agent.check_reminder_expiry()
        self.assertEqual(store.reminder_get(self.conn, rid)["status"], "expired")
        self.assertIsNone(store.pending_get(self.conn, 1))
        self.restart()
        self.transport.side_effect = self.send
        self.conn.execute("UPDATE reply_outbox SET available_at='2000-01-01'"); self.conn.commit()
        self.agent.check_reply_deliveries()
        self.assertEqual(store.pending_get(self.conn, 1)["payload"]["reminder_ids"], [rid])
        with mock.patch.object(self.agent, "_reping_stale_fired"):
            self.agent.check_reminder_expiry()
        self.assertEqual(len(self.sent), 1)

    def test_expiry_intent_failure_rolls_back_expiration(self):
        rid = self.old_reminder()
        with mock.patch.object(self.agent, "_reping_stale_fired"), mock.patch.object(action_delivery, "enqueue", side_effect=sqlite3.OperationalError("outage")):
            with self.assertRaises(sqlite3.Error):
                self.agent.check_reminder_expiry()
        self.assertEqual(store.reminder_get(self.conn, rid)["status"], "active")

    def test_unknown_alarm_outcome_does_not_resend_next_sweep(self):
        store.reminder_add(self.conn, 1, "alarm", "2020-01-01T09:00:00+00:00")
        self.transport.side_effect = TelegramError("timeout", outcome_unknown=True)
        with mock.patch.object(self.agent, "_recent_boss_msg", return_value=None):
            self.agent.fire_due_reminders(); self.agent.fire_due_reminders()
        self.assertEqual(self.transport.call_count, 1)

    def test_no_historical_update_is_backfilled_into_send_queue(self):
        update = {"update_id": 20, "message": {"text": "old"}}
        store.telegram_update_receive(self.conn, update, 1)
        store.telegram_update_fail(self.conn, 20, "historical failure", terminal=True)
        self.restart()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM reply_outbox").fetchone()[0], 0)

    def test_draft_amend_retains_delivered_card_binding(self):
        pending = self.pending()
        self.scope("change time")
        self.agent.resolve_pending(1, "amend", {"due_utc": "2099-01-03T09:00:00+00:00"}, pending, "en")
        new = store.pending_get(self.conn, 1)
        self.assertEqual(new["payload"]["card_message_id"], 101)

    def test_successful_reply_returns_result_and_records_once(self):
        self.agent._active_update_id = 903
        self.agent._reply_sequence = 0
        first = self.agent.reply(1, "answer")
        self.agent._reply_sequence = 0
        second = self.agent.reply(1, "answer")
        self.assertEqual(first, second)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM conversation WHERE role='bot'").fetchone()[0], 1)

    def test_background_delivery_never_overwrites_unrelated_pending(self):
        self.old_reminder()
        self.pending()
        with mock.patch.object(self.agent, "_reping_stale_fired"):
            self.agent.check_reminder_expiry()
        self.assertEqual(store.pending_get(self.conn, 1)["kind"], "reminder")

    def test_failed_draft_card_never_auto_appears_without_caller_binding(self):
        self.transport.side_effect = TelegramError("rate limit", status=429)
        draft = self.draft()
        self.agent.do_reminder_create(1, "en", draft)
        self.assertNotIn("card_message_id", store.pending_get(self.conn, 1)["payload"])
        self.transport.side_effect = self.send
        self.conn.execute("UPDATE reply_outbox SET available_at='2000-01-01'"); self.conn.commit()
        self.agent.check_reply_deliveries()
        self.assertEqual(len(self.sent), 0)
        self.agent.do_reminder_create(1, "en", draft)
        self.assertEqual(store.pending_get(self.conn, 1)["payload"]["card_message_id"], 101)

    def test_due_alarm_known_rejection_is_finalized_only_by_original_sweep(self):
        rid = store.reminder_add(self.conn, 1, "alarm", "2020-01-01T09:00:00+00:00")
        self.transport.side_effect = TelegramError("rate limit", status=429)
        with mock.patch.object(self.agent, "_recent_boss_msg", return_value=None):
            self.agent.fire_due_reminders()
            self.conn.execute("UPDATE reply_outbox SET available_at='2000-01-01'"); self.conn.commit()
            self.transport.side_effect = self.send
            self.agent.check_reply_deliveries()
            self.assertEqual(len(self.sent), 0)
            self.agent.fire_due_reminders()
        self.assertEqual(self.agent.fired_reminders_for_message(101), [rid])
        self.assertIsNotNone(store.reminder_get(self.conn, rid)["last_fired_at"])

    def test_expiry_reopened_before_retry_cancels_stale_announcement(self):
        rid = self.old_reminder()
        self.transport.side_effect = TelegramError("rate limit", status=429)
        with mock.patch.object(self.agent, "_reping_stale_fired"):
            self.agent.check_reminder_expiry()
        store.reminder_reopen(self.conn, rid, "2099-01-01T09:00:00+00:00")
        self.conn.execute("UPDATE reply_outbox SET available_at='2000-01-01'"); self.conn.commit()
        self.transport.side_effect = self.send
        self.agent.check_reply_deliveries()
        self.assertEqual(len(self.sent), 0)
        self.assertIsNone(store.pending_get(self.conn, 1))

    def test_undo_replay_does_not_swap_the_dates_back(self):
        rid = store.reminder_add(self.conn, 1, "undo", "2099-01-01T09:00:00+00:00")
        store.reminder_update_due(self.conn, rid, "2099-01-04T09:00:00+00:00")
        self.scope("undo move")
        with mock.patch.object(action_delivery, "deliver_action", side_effect=ProcessDeath):
            with self.assertRaises(ProcessDeath):
                self.agent.do_reminder_undo(1, "en", {"id": 1})
        self.restart()
        self.scope("undo move")
        self.agent.dispatch(1, {"message_id": 1}, "undo move")
        self.assertEqual(store.reminder_get(self.conn, rid)["due_utc"], "2099-01-01T09:00:00+00:00")

    def test_router_wait_alarm_preserves_the_in_flight_bare_reminder_target(self):
        first = store.reminder_add(self.conn, 1, "A", "2099-01-01T09:00:00+00:00")
        second = store.reminder_add(self.conn, 1, "WAIT ALARM B", "2020-01-01T09:00:00+00:00")
        self.agent._remember_reminder(first)
        ready = threading.Event()

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self):
                if not ready.wait(3):
                    raise AssertionError("the owner-thread alarm heartbeat did not run")
                return b'{}'

        def sender(token, method, params, **kwargs):
            result = self.send(token, method, params, **kwargs)
            if "WAIT ALARM B" in params.get("text", ""):
                ready.set()
            return result

        def route(*args, **kwargs):
            inference_io.json_request(lambda *a, **k: Response(), object(), 4, llm.LLMError)
            return {"action": "reminder_reschedule", "params": {"due_utc": "2099-01-04T09:00:00+00:00"}, "confidence": 1, "risk": "state_write"}

        self.transport.side_effect = sender
        update = {"update_id": 904, "message": {"message_id": 1, "chat": {"id": 1}, "from": {"id": 1}, "text": "update that reminder for the planning session"}}
        with mock.patch.object(entry.router, "route", side_effect=route), mock.patch.object(self.agent, "send_chat_action"), mock.patch.object(self.agent, "_recent_boss_msg", return_value=None):
            self.agent.process_update_batch([update])
        self.assertTrue(ready.is_set())
        self.assertEqual(store.reminder_get(self.conn, first)["due_utc"], "2099-01-04T09:00:00+00:00")
        self.assertEqual(store.reminder_get(self.conn, second)["due_utc"], "2020-01-01T09:00:00+00:00")
        self.assertIsNone(store.pending_get(self.conn, 1))

    def test_ambiguous_occurrence_is_not_resent_in_a_new_batch(self):
        old = store.reminder_add(self.conn, 1, "unknown", "2020-01-01T09:00:00+00:00")
        self.transport.side_effect = TelegramError("timeout", outcome_unknown=True)
        with mock.patch.object(self.agent, "_recent_boss_msg", return_value=None):
            self.agent.fire_due_reminders()
            new = store.reminder_add(self.conn, 1, "new", "2020-01-02T09:00:00+00:00")
            self.transport.side_effect = self.send
            self.agent.fire_due_reminders()
        self.assertEqual(len(self.sent), 1)
        self.assertIn("new", self.sent[0][1]["text"])
        self.assertNotIn("unknown", self.sent[0][1]["text"])
        self.assertIsNone(store.reminder_get(self.conn, old)["last_fired_at"])
        self.assertIsNotNone(store.reminder_get(self.conn, new)["last_fired_at"])

    def test_edited_source_cancels_stale_ordinary_reply_before_retry(self):
        store.convo_add(self.conn, 1, "user", "old source", update_id=950, tg_message_id=51)
        self.agent._active_update_id = 950
        self.transport.side_effect = TelegramError("rate limit", status=429)
        self.agent.reply(1, "old answer")
        store.convo_set_text(self.conn, 1, 51, "edited source")
        self.conn.execute("UPDATE reply_outbox SET available_at='2000-01-01'"); self.conn.commit()
        self.transport.side_effect = self.send
        self.agent.check_reply_deliveries()
        self.assertEqual(len(self.sent), 0)
        self.assertEqual(self.conn.execute("SELECT error FROM reply_outbox").fetchone()[0], "source changed before delivery")

    def test_committed_action_acknowledges_effect_even_if_source_is_later_edited(self):
        store.convo_add(self.conn, 1, "user", "yes", update_id=951, tg_message_id=52)
        pending = self.pending()
        self.scope("yes", 951)
        self.transport.side_effect = TelegramError("rate limit", status=429)
        self.agent.resolve_pending(1, "confirm", {}, pending, "en")
        store.convo_set_text(self.conn, 1, 52, "edited after the action")
        self.conn.execute("UPDATE reply_outbox SET available_at='2000-01-01'"); self.conn.commit()
        self.transport.side_effect = self.send
        self.agent.check_reply_deliveries()
        self.assertEqual(len(self.sent), 1)
        self.assertIn("confirm example", self.sent[0][1]["text"])

    def test_presend_database_failure_never_consumes_an_unsent_alarm(self):
        rid = store.reminder_add(self.conn, 1, "unsent", "2020-01-01T09:00:00+00:00")
        with mock.patch.object(self.agent, "_recent_boss_msg", return_value=None), mock.patch.object(action_delivery, "enqueue", side_effect=sqlite3.OperationalError("disk is full")):
            with self.assertRaises(sqlite3.Error):
                self.agent.fire_due_reminders()
        self.assertNotIn(rid, self.agent._reminder_stamp_owed)
        self.assertIsNone(store.reminder_get(self.conn, rid)["last_fired_at"])
        self.assertEqual(len(self.sent), 0)
        with mock.patch.object(self.agent, "_recent_boss_msg", return_value=None):
            self.agent.fire_due_reminders()
        self.assertEqual(len(self.sent), 1)
        self.assertIsNotNone(store.reminder_get(self.conn, rid)["last_fired_at"])

    def test_acknowledged_send_retries_only_its_database_bookkeeping(self):
        with mock.patch.object(store, "convo_add", side_effect=sqlite3.OperationalError("disk is full")):
            with self.assertRaises(sqlite3.Error):
                self.agent.reply(1, "delivered answer")
        self.agent.check_reply_deliveries()
        self.agent.check_reply_deliveries()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.conn.execute("SELECT state FROM reply_outbox").fetchone()[0], "sent")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM conversation WHERE text='delivered answer'").fetchone()[0], 1)

    def test_purge_does_not_restore_delivered_text_from_bookkeeping_debt(self):
        with mock.patch.object(store, "convo_add", side_effect=sqlite3.OperationalError("disk is full")):
            with self.assertRaises(sqlite3.Error):
                self.agent.reply(1, "private delivered text")
        store.purge_execute(self.conn, "all")
        self.agent.check_reply_deliveries()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM conversation").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT params_json FROM reply_outbox").fetchone()[0], "{}")
        self.assertEqual(len(self.sent), 1)

    def test_ambiguity_question_is_not_retried_after_pending_is_replaced(self):
        store.reminder_add(self.conn, 1, "one", "2099-01-01T09:00:00+00:00")
        store.reminder_add(self.conn, 1, "two", "2099-01-02T09:00:00+00:00")
        self.scope("move an unspecified reminder")
        self.transport.side_effect = TelegramError("rate limit", status=429)
        self.agent.do_reschedule(1, "en", {"due_utc": "2099-01-04T09:00:00+00:00"})
        self.assertEqual(store.pending_get(self.conn, 1)["kind"], "reminder_op")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0], 0)
        store.pending_set(self.conn, 1, "category", {"row_id": 99})
        self.conn.execute("UPDATE reply_outbox SET available_at='2000-01-01'"); self.conn.commit()
        self.transport.side_effect = self.send
        self.agent.check_reply_deliveries()
        self.assertEqual(len(self.sent), 0)
        self.assertEqual(store.pending_get(self.conn, 1)["kind"], "category")

    def test_committed_snooze_question_requires_exact_live_pending(self):
        rid = store.reminder_add(self.conn, 1, "snoozed", "2020-01-01T09:00:00+00:00")
        for _ in range(2):
            store.reminder_event(self.conn, rid, "snoozed")
        self.scope("snooze it for an hour")
        self.transport.side_effect = TelegramError("rate limit", status=429)
        # Keep the synthetic local clock away from midnight on any runner day.
        with mock.patch.object(self.agent, "tz_offset", return_value=12 - datetime.now(timezone.utc).hour):
            self.agent.resolve_pending(1, "amend", {"snooze_minutes": 60}, {
                "kind": "reminder_fired", "payload": {"reminder_id": rid, "title": "snoozed"}}, "en")
        self.assertEqual(store.pending_get(self.conn, 1)["kind"], "reminder_snooze_choice")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0], 1)
        store.pending_set(self.conn, 1, "category", {"row_id": 99})
        self.conn.execute("UPDATE reply_outbox SET available_at='2000-01-01'"); self.conn.commit()
        self.transport.side_effect = self.send
        self.agent.check_reply_deliveries()
        self.assertEqual(len(self.sent), 0)
        self.assertEqual(self.conn.execute("SELECT error FROM reply_outbox").fetchone()[0], "confirmation changed before delivery")

    def test_recreated_identical_pending_does_not_authorize_an_old_card(self):
        @action_delivery.reminder_action()
        def commit_question(agent):
            rid = store.reminder_add(agent.conn, 1, "question", "2099-01-01T09:00:00+00:00")
            store.pending_set(agent.conn, 1, "reminder_snooze_choice", {"reminder_id": rid})
            agent.reply(1, "Choose a day", reply_markup={"inline_keyboard": [[{"text": "Tomorrow", "callback_data": "tmw"}]]})

        self.scope("question")
        self.transport.side_effect = TelegramError("rate limit", status=429)
        commit_question(self.agent)
        pending = store.pending_get(self.conn, 1)
        self.conn.execute("UPDATE pending_actions SET expires_at='2099-01-01T00:00:00+00:00'")
        self.conn.commit()
        self.assertEqual(store.pending_get(self.conn, 1), pending)
        self.conn.execute("UPDATE reply_outbox SET available_at='2000-01-01'"); self.conn.commit()
        self.transport.side_effect = self.send
        self.agent.check_reply_deliveries()
        self.assertEqual(len(self.sent), 0)

    def test_purged_uncertain_alarm_cannot_suppress_a_new_identical_reminder(self):
        old = store.reminder_add(self.conn, 1, "alarm", "2020-01-01T09:00:00+00:00")
        self.transport.side_effect = TelegramError("timeout", outcome_unknown=True)
        with mock.patch.object(self.agent, "_recent_boss_msg", return_value=None):
            self.agent.fire_due_reminders()
            store.purge_execute(self.conn, "reminders")
            new = store.reminder_add(self.conn, 1, "alarm", "2020-01-01T09:00:00+00:00")
            self.assertEqual(old, new)  # exercise SQLite's real ID reuse
            self.transport.side_effect = self.send
            self.agent.fire_due_reminders()
        self.assertEqual(len(self.sent), 1)
        self.assertIsNotNone(store.reminder_get(self.conn, new)["last_fired_at"])

    def test_satisfied_ordinal_reschedule_still_has_a_replay_receipt(self):
        first = store.reminder_add(self.conn, 1, "first", "2099-01-01T09:00:00+00:00")
        second = store.reminder_add(self.conn, 1, "second", "2099-01-02T09:00:00+00:00")
        self.scope("move first to its existing time")
        with mock.patch.object(action_delivery, "deliver_action", side_effect=ProcessDeath):
            with self.assertRaises(ProcessDeath):
                self.agent.do_reschedule(1, "en", {"id": 1, "due_utc": "2099-01-01T09:00:00+00:00"})
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0], 1)
        # A later recurrence advance changes the display order before recovery.
        store.reminder_update_due(self.conn, first, "2099-01-04T09:00:00+00:00", reason="recurrence_advanced")
        self.restart()
        self.scope("move first to its existing time")
        self.agent.dispatch(1, {"message_id": 1}, "move first to its existing time")
        self.assertEqual(store.reminder_get(self.conn, first)["due_utc"], "2099-01-04T09:00:00+00:00")
        self.assertEqual(store.reminder_get(self.conn, second)["due_utc"], "2099-01-02T09:00:00+00:00")

    def test_recorded_alarm_restart_never_repeats_an_uncertain_send(self):
        rid = store.reminder_add(self.conn, 1, "recorded", "2020-01-01T09:00:00+00:00")
        with mock.patch.object(store, "convo_add", side_effect=sqlite3.OperationalError("read-only database")):
            with self.assertRaises(sqlite3.Error):
                self.agent.fire_due_reminders()
        self.restart()
        self.agent.fire_due_reminders()
        self.agent.fire_due_reminders()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.conn.execute("SELECT state FROM reply_outbox").fetchone()[0], "ambiguous")
        self.assertIsNone(store.reminder_get(self.conn, rid)["last_fired_at"])

    def test_unknown_purge_preview_does_not_change_its_own_confirmed_impact(self):
        mid = store.insert_message(self.conn, {
            "chat_id": 1, "tg_message_id": 51, "received_at": "ts", "raw_text": "private note"})
        store.set_suggestion(self.conn, mid, "News", "summary", "model")
        store.confirm_category(self.conn, mid, store.ensure_category(self.conn, "News"))
        self.transport.side_effect = TelegramError("unknown preview outcome", outcome_unknown=True)
        self.agent.do_purge(1, "en", {"scope": "all"})
        pending = store.pending_get(self.conn, 1)
        self.assertEqual(self.conn.execute("SELECT state FROM reply_outbox").fetchone()[0], "ambiguous")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM issues").fetchone()[0], 0)
        # Exercise real reply/outbox for both preview and acknowledgement. A
        # transport refusal must not add an unpreviewed issue and loop forever.
        with mock.patch.object(self.agent, "purge_task_external_state", return_value=True):
            self.agent.resolve_purge(1, "en", pending, pending["payload"]["phrase"])
        self.assertEqual(store.purge_preview(self.conn, "all")["messages"], 0)
        self.assertIsNone(store.pending_get(self.conn, 1))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM issues").fetchone()[0], 0)

    def test_129th_eligible_reply_is_not_starved_by_caller_bound_cards(self):
        for index in range(128):
            action_delivery.enqueue(self.agent, "sendMessage", {"chat_id": 1, "text": f"card {index}", "reply_markup": {"inline_keyboard": []}}, record=True)
        action_delivery.enqueue(self.agent, "sendMessage", {"chat_id": 1, "text": "ordinary answer"}, record=True)
        self.assertEqual(action_delivery.drain(self.agent, self.send), 1)
        self.assertEqual([params["text"] for method, params in self.sent], ["ordinary answer"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM reply_outbox WHERE state='pending'").fetchone()[0], 128)

    def test_stale_reply_does_not_consume_the_transport_attempt_limit(self):
        store.convo_add(self.conn, 1, "user", "old", update_id=990, tg_message_id=61)
        self.agent._active_update_id = 990
        action_delivery.enqueue(self.agent, "sendMessage", {"chat_id": 1, "text": "stale"}, record=True)
        store.convo_set_text(self.conn, 1, 61, "edited")
        self.agent._active_update_id = None
        action_delivery.enqueue(self.agent, "sendMessage", {"chat_id": 1, "text": "current"}, record=True)
        self.assertEqual(action_delivery.drain(self.agent, self.send), 1)
        self.assertEqual([params["text"] for method, params in self.sent], ["current"])

    def test_old_pending_converse_finalizer_is_not_hidden_by_new_sent_rows(self):
        old = action_delivery.enqueue(self.agent, "sendMessage", {"chat_id": 1, "text": "old delivered"}, record=True)
        self.conn.execute("UPDATE reply_outbox SET metadata_json=? WHERE id=?", (
            json.dumps({"kind": "converse_reply", "converse": {"state": "pending"}}), old["id"]))
        for index in range(128):
            action_delivery.enqueue(self.agent, "sendMessage", {"chat_id": 1, "text": f"delivered {index}"}, record=True)
        self.conn.execute("UPDATE reply_outbox SET state='sent'"); self.conn.commit()
        with mock.patch.object(action_delivery, "_finalize_delivered") as finalize:
            self.assertEqual(action_delivery.drain(self.agent, self.send), 0)
        self.assertEqual(finalize.call_count, 1)
        self.assertEqual(finalize.call_args.args[1]["id"], old["id"])


if __name__ == "__main__":
    unittest.main()
