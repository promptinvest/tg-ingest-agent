"""Alert delivery identities survive retries without hiding later real episodes."""
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

import store
import tg_ingest_agent as entry
from testlib import make_config
from tg_api import TelegramError


class ScheduledTransitionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cfg = make_config(ALLOWED_CHAT_IDS="1", DB_PATH=str(Path(self.temp.name) / "db"),
                               MEDIA_DIR=str(Path(self.temp.name) / "media"))
        self.agent = entry.Agent(self.cfg)
        self.addCleanup(self.agent.conn.close)
        self.conn = self.agent.conn
        self.cfg.disk_alert_min_free_pct = 10
        self.mode = "accepted"
        self.delivered = []
        self.transport = mock.patch.object(entry, "tg_call", side_effect=self.send)
        self.transport.start()
        self.addCleanup(self.transport.stop)
        self.free = 100
        self.collect = mock.patch.object(entry.sysinfo, "collect", side_effect=self.disk_data)
        self.collect.start()
        self.addCleanup(self.collect.stop)
        store.kv_set(self.conn, "disk_space", "ok")

    def send(self, _token, _method, params, **_kwargs):
        if self.mode == "rejected":
            raise TelegramError("synthetic rate limit", status=429)
        self.delivered.append(params["text"])
        if self.mode == "ambiguous":
            raise TelegramError("synthetic acknowledgement lost", outcome_unknown=True)
        return {"message_id": 900 + len(self.delivered)}

    def disk_data(self, _path):
        return {"disk_total": 1000, "disk_free": self.free}

    def check_disk(self, free):
        self.free = free
        self.agent.last_disk_check = 0
        self.agent.check_disk_space()

    def outbox_due(self):
        self.conn.execute("UPDATE reply_outbox SET available_at='2000-01-01'")
        self.conn.commit()

    def test_ambiguous_low_stays_one_episode_inside_recovery_margin(self):
        self.mode = "ambiguous"
        self.check_disk(90)
        self.assertEqual(len(self.delivered), 1)
        self.mode = "accepted"
        for free in (110, 95, 115, 85):
            self.outbox_due()
            self.check_disk(free)
        self.assertEqual(len(self.delivered), 1)
        self.assertEqual(store.kv_get(self.conn, "disk_space"), "ok")

    def test_later_low_episode_is_admitted_after_clear_observed_recovery(self):
        self.mode = "ambiguous"
        self.check_disk(90)
        self.mode = "accepted"
        self.check_disk(130)
        self.check_disk(90)
        self.check_disk(80)
        self.assertEqual(len(self.delivered), 2)
        self.assertEqual(store.kv_get(self.conn, "disk_space"), "low")

    def test_acknowledged_low_recovery_and_next_low_each_deliver_once(self):
        for free in (90, 110, 95, 130, 140, 90, 80):
            self.check_disk(free)
        self.assertEqual(len(self.delivered), 3)
        self.assertEqual(store.kv_get(self.conn, "disk_space"), "low")

    def test_known_unsent_low_can_retry_its_original_occurrence(self):
        self.mode = "rejected"
        self.check_disk(90)
        self.assertEqual(self.delivered, [])
        self.assertEqual(store.kv_get(self.conn, "disk_space"), "ok")
        self.mode = "accepted"
        self.outbox_due()
        self.check_disk(80)
        self.check_disk(70)
        self.assertEqual(len(self.delivered), 1)
        self.assertEqual(store.kv_get(self.conn, "disk_space"), "low")

    def test_disk_stamp_failure_repairs_without_repeating_acknowledged_send(self):
        original = store.kv_set
        failed = False

        def fail_stamp(conn, key, value):
            nonlocal failed
            if key == "disk_space" and value == "low" and not failed:
                failed = True
                raise sqlite3.OperationalError("synthetic disk state write outage")
            return original(conn, key, value)

        with mock.patch.object(store, "kv_set", side_effect=fail_stamp):
            with self.assertRaises(sqlite3.OperationalError):
                self.check_disk(90)
        self.check_disk(80)
        self.assertEqual(len(self.delivered), 1)
        self.assertEqual(store.kv_get(self.conn, "disk_space"), "low")

    def test_group_stamp_failure_does_not_split_and_repeat_acknowledged_alert(self):
        self.cfg.do_model = "synthetic-a"
        self.cfg.vision_model = ""
        self.cfg.stt_enabled = False
        self.cfg.model_health_interval = 1
        self.cfg.model_health_confirm = 1
        self.cfg.model_health_transient_confirm = 1
        for model in ("synthetic-a", "synthetic-b"):
            store.kv_set(self.conn, "mh:" + model, "ok")
            store.kv_set(self.conn, "mhc:" + model, "ok")
        original = store.kv_set
        failed = False

        def fail_stamp(conn, key, value):
            nonlocal failed
            if key == "mhc:synthetic-b" and value == "dead" and not failed:
                failed = True
                raise sqlite3.OperationalError("synthetic second model stamp outage")
            return original(conn, key, value)

        def sweep():
            self.agent.last_model_health = 0
            self.agent.check_model_health()

        with mock.patch.object(entry.llm, "budget_state", return_value=("ok", "day", 0, 2)), \
                mock.patch.object(entry.llm, "profiles", return_value={
                    "converse_warm": {"primary": "synthetic-b"}}), \
                mock.patch.object(self.agent, "_fallbacks_for", return_value=[]), \
                mock.patch.object(entry.llm, "model_ok", return_value=(False, "synthetic outage")) as probe:
            with mock.patch.object(store, "kv_set", side_effect=fail_stamp):
                with self.assertRaises(sqlite3.OperationalError):
                    sweep()
            self.assertEqual(store.kv_get(self.conn, "mhc:synthetic-a"), "ok")
            self.assertEqual(store.kv_get(self.conn, "mhc:synthetic-b"), "ok")
            sweep()
            sweep()
            self.assertEqual(len(self.delivered), 1)
            self.assertEqual(store.kv_get(self.conn, "mhc:synthetic-a"), "dead")
            self.assertEqual(store.kv_get(self.conn, "mhc:synthetic-b"), "dead")
            probe.return_value = (True, "")
            sweep()
            probe.return_value = (False, "synthetic later outage")
            sweep()
            sweep()
            self.assertEqual(len(self.delivered), 3)


if __name__ == "__main__":
    unittest.main()
