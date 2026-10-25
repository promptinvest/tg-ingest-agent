"""Delayed retries never bypass existing caller-owned delivery finalizers."""
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import action_delivery
import fleet_protocol
import inference_io
import store
import tg_ingest_agent as entry
from testlib import make_config
from tg_api import TelegramError


class CallerDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cfg = make_config(ALLOWED_CHAT_IDS="1", DB_PATH=str(Path(self.tmp.name) / "db"),
                          MEDIA_DIR=str(Path(self.tmp.name) / "media"))
        self.agent = entry.Agent(cfg)
        self.conn = self.agent.conn
        self.addCleanup(self.conn.close)
        self.sent = []
        self.transport = mock.patch.object(entry, "tg_call", side_effect=self.send).start()
        self.addCleanup(mock.patch.stopall)

    def send(self, token, method, params, **kwargs):
        self.sent.append((method, params))
        return {"message_id": len(self.sent) + 500}

    def rejected(self):
        self.transport.side_effect = TelegramError("fixture explicit rate limit", status=429)

    def drain(self):
        self.conn.execute("UPDATE reply_outbox SET available_at='2000-01-01'")
        self.conn.commit()
        self.transport.side_effect = self.send
        self.agent.check_reply_deliveries()

    def test_scheduled_send_stays_with_original_slot_owner(self):
        self.rejected()
        self.assertFalse(self.agent._send_all("fixture scheduled notice"))
        self.drain()
        self.assertEqual(self.sent, [])
        self.assertTrue(self.agent._send_all("fixture scheduled notice"))
        self.agent.check_reply_deliveries()
        self.assertEqual(len(self.sent), 1)

    def test_morning_brief_retry_sets_day_once_without_detached_outbox_send(self):
        store.pref_set(self.conn, "morning_brief", "on")
        self.agent.cfg.morning_brief_hour = 0
        with mock.patch.object(entry.proactive, "settings", return_value={"enabled": True}), \
                mock.patch.object(entry.proactive, "in_quiet_hours", return_value=False), \
                mock.patch.object(entry.review, "morning_brief", return_value="fixture morning brief"), \
                mock.patch.object(self.agent, "_sched_backing_off", return_value=False):
            self.rejected()
            self.agent.check_morning_brief()
            self.assertFalse(store.kv_get(self.conn, "morning_brief_day"))
            self.drain()
            self.assertEqual(self.sent, [])
            self.agent.check_morning_brief()
            self.assertTrue(store.kv_get(self.conn, "morning_brief_day"))
            self.agent.check_morning_brief()
            self.agent.check_reply_deliveries()
        self.assertEqual(len(self.sent), 1)

    def test_fleet_report_cannot_late_deliver_without_message_id_receipt(self):
        payload = {"answer": "fixture fleet report", "lang": "en"}
        now = fleet_protocol.now()
        self.conn.execute("INSERT INTO fleet_requests VALUES(?,?,?,?,?,?,?)",
                          ("fixture-caller-report", 1, 0, "ready", json.dumps(payload), now, now))
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM fleet_requests WHERE id='fixture-caller-report'").fetchone()
        self.rejected()
        with mock.patch.object(entry.worker_client, "acknowledge") as acknowledge:
            with self.assertRaisesRegex(ValueError, "delivery ambiguous"):
                self.agent._fleet_deliver_result(row, payload)
            self.drain()
        self.assertEqual(self.sent, [])
        self.assertNotIn("result_message", payload)
        acknowledge.assert_not_called()

    def test_caller_guard_restores_plain_reply_delivery_recovery(self):
        self.rejected()
        self.agent._send_all("fixture caller-owned notice")
        self.agent.reply(1, "fixture ordinary reply")
        self.drain()
        self.assertEqual([params["text"] for method, params in self.sent], ["fixture ordinary reply"])
        self.assertFalse(getattr(self.agent, "_reply_requires_caller", False))

    def test_known_sent_bookkeeping_debt_cannot_restore_purged_reminder_content(self):
        store.reminder_add(self.conn, 1, "private fixture title", "2099-01-01T09:00:00+00:00")
        with mock.patch.object(action_delivery, "_record_sent", side_effect=sqlite3.OperationalError("fixture write outage")):
            with self.assertRaises(sqlite3.Error):
                self.agent.do_reminder_cancel(1, "en", {"id": 1})
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.agent._reply_stamp_owed)
        store.purge_execute(self.conn, "reminders")
        self.agent.check_reply_deliveries()
        self.assertFalse(self.agent._reply_stamp_owed)
        self.assertEqual(len(self.sent), 1)
        row = self.conn.execute("SELECT state,params_json,metadata_json FROM reply_outbox").fetchone()
        self.assertEqual(tuple(row), ("failed", "{}", "{}"))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM conversation WHERE role='bot'").fetchone()[0], 0)

    def converse(self, text="Please keep this answer concise", update=90001):
        store.convo_add(self.conn, 1, "user", text, update_id=update, tg_message_id=7001)
        self.agent._active_update_id = update
        self.agent._reply_sequence = 0
        with mock.patch.object(self.agent, "send_chat_action"), \
                mock.patch.object(self.agent, "converse_context", return_value="fixture"), \
                mock.patch.object(self.agent, "_converse_grounding", return_value=""), \
                mock.patch.object(entry.llm, "chat_profile", return_value="Here is a clear synthetic conversation answer."):
            self.agent.do_converse(1, "en", text)

    def test_ordinary_converse_rejection_recovers_and_curates_only_after_delivery(self):
        self.agent.turn_lang = "ru"
        observed = []

        def curate(chat_id, lang=None, force=False):
            self.assertFalse(self.conn.in_transaction)
            observed.append((chat_id, lang, force, self.agent.turn_lang,
                             self.agent._active_update_id, self.agent._reply_delivery_metadata))

        with mock.patch.object(self.agent, "maybe_curate_conversation", side_effect=curate) as curation:
            self.rejected()
            self.converse()
            curation.assert_not_called()
            self.drain()
            self.agent.check_reply_deliveries()
            self.assertEqual(curation.call_count, 1)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(observed, [(1, "en", False, "en", None, None)])
        self.assertEqual(self.agent.turn_lang, "ru")
        self.assertEqual(self.agent._active_update_id, 90001)
        metadata = json.loads(self.conn.execute("SELECT metadata_json FROM reply_outbox").fetchone()[0])
        self.assertEqual(metadata["converse"]["state"], "done")
        self.assertNotIn("caller_finalization", metadata)

    def test_edited_converse_source_never_delivers_or_curates_old_answer(self):
        with mock.patch.object(self.agent, "maybe_curate_conversation") as curation:
            self.rejected()
            self.converse()
            store.convo_set_text(self.conn, 1, 7001, "Edited request")
            self.drain()
        self.assertEqual(self.sent, [])
        curation.assert_not_called()

    def test_immediate_converse_curates_once_after_actual_acknowledgement(self):
        with mock.patch.object(self.agent, "maybe_curate_conversation") as curation:
            self.converse()
            curation.assert_called_once_with(1, lang="en", force=False)
            self.agent.check_reply_deliveries()
            self.assertEqual(curation.call_count, 1)
        self.assertEqual(len(self.sent), 1)

    def test_interrupted_converse_finalizer_is_not_replayed_after_restart(self):
        class ProcessDeath(BaseException):
            pass

        with mock.patch.object(self.agent, "maybe_curate_conversation", side_effect=ProcessDeath):
            with self.assertRaises(ProcessDeath):
                self.converse()
        self.assertEqual(len(self.sent), 1)
        metadata = json.loads(self.conn.execute("SELECT metadata_json FROM reply_outbox").fetchone()[0])
        self.assertEqual(metadata["converse"]["state"], "claimed")
        action_delivery.recover(self.conn)
        with mock.patch.object(self.agent, "maybe_curate_conversation") as curation:
            self.agent.check_reply_deliveries()
        curation.assert_not_called()
        self.assertEqual(len(self.sent), 1)

    def test_delayed_converse_curation_transport_wait_still_fires_due_alarm(self):
        ready = threading.Event()
        calls = []

        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self):
                if not ready.wait(3):
                    raise AssertionError("due alarm did not progress during delayed curation")
                return b"{}"

        def curate(chat_id, lang=None, force=False):
            calls.append((chat_id, lang, force))
            self.assertFalse(self.conn.in_transaction)
            inference_io.json_request(lambda *a, **k: Response(), object(), 4, entry.llm.LLMError)

        def sender(token, method, params, **kwargs):
            self.assertFalse(self.conn.in_transaction)
            result = self.send(token, method, params, **kwargs)
            if "CURATION WAIT ALARM" in params.get("text", ""):
                ready.set()
            return result

        with mock.patch.object(self.agent, "maybe_curate_conversation", side_effect=curate), \
                mock.patch.object(self.agent, "_recent_boss_msg", return_value=None):
            self.rejected()
            self.converse()
            self.assertEqual(calls, [])
            rid = store.reminder_add(self.conn, 1, "CURATION WAIT ALARM", "2020-01-01T00:00:00+00:00")
            self.conn.execute("UPDATE reply_outbox SET available_at='2000-01-01'")
            self.conn.commit()
            self.transport.side_effect = sender
            self.agent.check_reply_deliveries()
        self.assertTrue(ready.is_set())
        self.assertTrue(store.reminder_get(self.conn, rid)["last_fired_at"])
        self.assertEqual(calls, [(1, "en", False)])
        self.assertEqual(len([params for method, params in self.sent
                              if "CURATION WAIT ALARM" in params.get("text", "")]), 1)

    def note(self, number, text="fixture note"):
        mid = store.insert_message(self.conn, {"chat_id": 1, "tg_message_id": number,
            "received_at": "2026-10-25T09:00:00+00:00", "raw_text": text})
        store.set_suggestion(self.conn, mid, "News", text, "fixture")
        store.confirm_category(self.conn, mid, store.ensure_category(self.conn, "News"))
        return mid, store.ensure_note_no(self.conn, mid)

    def test_plain_delete_archive_and_retitle_prompts_cannot_arrive_after_new_pending(self):
        first, first_no = self.note(8101, "first")
        second, second_no = self.note(8102, "second")
        calls = (
            lambda: self.agent.do_item_delete(1, "en", {"id": first_no}),
            lambda: self.agent.do_item_delete(1, "en", {"ids": [first_no, second_no]}),
            lambda: self.agent.do_note_lifecycle(1, "en", {"operation": "archive", "ids": [first_no, second_no]}),
            lambda: self.agent.do_note_edit(1, "en", {"id": first_no, "new_summary": "changed title"}, "change title"),
        )
        for index, call in enumerate(calls):
            with self.subTest(prompt=index):
                store.pending_clear(self.conn, 1)
                self.rejected()
                call()
                self.assertIsNotNone(store.pending_get(self.conn, 1))
                store.pending_set(self.conn, 1, "category", {"row_id": second})
                self.drain()
                self.assertEqual(self.sent, [])
                self.assertEqual(store.pending_get(self.conn, 1), {"kind": "category", "payload": {"row_id": second}})
                self.assertIsNotNone(store.get_message(self.conn, first))

    def test_review_delivery_cannot_arrive_without_its_ordinal_snapshot(self):
        old, _ = self.note(8110, "old review")
        current, _ = self.note(8111, "new review")
        self.agent._review_snapshot_set([old], ttl_seconds=24 * 3600)
        self.rejected()
        self.assertEqual(self.agent.do_note_review(1, "en", preset_ids=[current]), [])
        self.drain()
        self.assertEqual(self.sent, [])
        self.assertEqual([row["id"] for row in self.agent._review_snapshot_rows()], [old])
        self.assertEqual(self.agent.do_note_review(1, "en", preset_ids=[current]), [current])
        self.assertEqual([row["id"] for row in self.agent._review_snapshot_rows()], [current])

    def test_calendar_details_prompt_stays_with_its_own_pending(self):
        self.rejected()
        self.agent.do_calendar_add(1, "en", {})
        self.assertEqual(store.pending_get(self.conn, 1)["kind"], "calendar_partial")
        store.pending_set(self.conn, 1, "category", {"row_id": 999})
        self.drain()
        self.assertEqual(self.sent, [])
        self.assertEqual(store.pending_get(self.conn, 1)["kind"], "category")

    def test_plain_prompt_helper_preserves_reply_seam_and_restores_ordinary_retry(self):
        with mock.patch.object(self.agent, "reply", return_value={"message_id": 999}) as reply:
            self.assertEqual(self.agent.reply_prompt(1, "fixture question"), {"message_id": 999})
            reply.assert_called_once_with(1, "fixture question")
        self.rejected()
        self.agent.reply_prompt(1, "fixture pending question")
        self.agent.reply(1, "fixture ordinary response")
        self.drain()
        self.assertEqual([params["text"] for method, params in self.sent], ["fixture ordinary response"])
        self.assertFalse(getattr(self.agent, "_reply_requires_caller", False))
