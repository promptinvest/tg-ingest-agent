"""A logical scheduled occurrence keeps its identity across uncertain delivery."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

import store
import tg_ingest_agent as entry
from testlib import make_config
from tg_api import TelegramError


class ScheduledOccurrenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cfg = make_config(ALLOWED_CHAT_IDS="1", DB_PATH=str(Path(self.temp.name) / "db"),
                               MEDIA_DIR=str(Path(self.temp.name) / "media"))
        self.agent = entry.Agent(self.cfg)
        self.addCleanup(lambda: self.agent.conn.close())
        self.conn = self.agent.conn
        self.sent = []
        self.transport = mock.patch.object(entry, "tg_call", side_effect=self.send).start()
        self.addCleanup(mock.patch.stopall)
        self.agent.cfg.morning_brief_hour = 0
        store.pref_set(self.conn, "morning_brief", "on")
        for target, value in (("settings", {"enabled": True}), ("in_quiet_hours", False)):
            patch = mock.patch.object(entry.proactive, target, return_value=value)
            patch.start()
        mock.patch.object(entry.review, "morning_brief", return_value="Synthetic brief").start()
        mock.patch.object(entry.review, "had_owner_turns", return_value=True).start()
        mock.patch.object(entry.review, "chat_text", return_value="Synthetic weekly report").start()
        mock.patch.object(self.agent, "_sched_backing_off", return_value=False).start()
        store.kv_set(self.conn, "next_review_utc", (datetime.now(timezone.utc)-timedelta(hours=1)).isoformat())

    def send(self, _token, method, params, **_kwargs):
        self.sent.append((method, params))
        return {"message_id": 800 + len(self.sent)}

    def uncertain(self, *args, **kwargs):
        self.send(*args, **kwargs)
        raise TelegramError("synthetic lost acknowledgement", outcome_unknown=True)

    def due_outbox(self):
        self.conn.execute("UPDATE reply_outbox SET available_at='2000-01-01'")
        self.conn.commit()

    def test_ambiguous_morning_brief_never_sends_same_day_again(self):
        self.transport.side_effect = self.uncertain
        self.agent.check_morning_brief()
        self.transport.side_effect = self.send
        for _ in range(4):
            self.due_outbox()
            self.agent.check_morning_brief()
        self.assertEqual(len(self.sent), 1)

    def test_ambiguous_weekly_review_never_sends_same_slot_again(self):
        self.transport.side_effect = self.uncertain
        self.agent.check_weekly_review()
        self.transport.side_effect = self.send
        for _ in range(4):
            self.due_outbox()
            self.agent.check_weekly_review()
        self.assertEqual(len(self.sent), 1)

    def test_known_unsent_brief_can_retry_and_stamp_once(self):
        self.transport.side_effect = TelegramError("synthetic rate limit", status=429)
        self.agent.check_morning_brief()
        self.assertIsNone(store.kv_get(self.conn, "morning_brief_day"))
        self.transport.side_effect = self.send
        self.due_outbox()
        self.agent.check_morning_brief()
        self.agent.check_morning_brief()
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(store.kv_get(self.conn, "morning_brief_day"))

    def test_acknowledged_brief_repairs_stamp_without_resend(self):
        original = store.kv_set
        rejected = False
        def fail_stamp(conn, key, value):
            nonlocal rejected
            if key == "morning_brief_day" and not rejected:
                rejected = True
                raise sqlite3.OperationalError("synthetic completion write outage")
            return original(conn, key, value)
        with mock.patch.object(store, "kv_set", side_effect=fail_stamp):
            with self.assertRaises(sqlite3.OperationalError):
                self.agent.check_morning_brief()
        self.agent.check_morning_brief()
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(store.kv_get(self.conn, "morning_brief_day"))

    def test_new_morning_slot_is_not_suppressed_by_old_delivery(self):
        self.agent.check_morning_brief()
        tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
        class Tomorrow(datetime):
            @classmethod
            def now(cls, tz=None):
                return tomorrow if tz else tomorrow.replace(tzinfo=None)
        with mock.patch.object(entry, "datetime", Tomorrow):
            self.agent.check_morning_brief()
        self.assertEqual(len(self.sent), 2)

    def test_ambiguous_slot_survives_process_reconstruction(self):
        self.transport.side_effect = self.uncertain
        self.agent.check_morning_brief()
        self.agent.conn.close()
        self.agent = entry.Agent(self.cfg)
        self.conn = self.agent.conn
        mock.patch.object(self.agent, "_sched_backing_off", return_value=False).start()
        self.transport.side_effect = self.send
        self.due_outbox()
        self.agent.check_morning_brief()
        self.assertEqual(len(self.sent), 1)
