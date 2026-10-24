"""CF-04 pending completion latency and existing poll cadence regressions."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import store
import tg_ingest_agent as entry


class FleetCompletionPolling(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.open_db(Path(self.tmp.name) / "polling.db")
        # Exercise the real loop with only its unrelated startup work replaced.
        # A fixture connection remains on this thread throughout the fake poll.
        self.agent = entry.Agent.__new__(entry.Agent)
        self.agent.conn = self.conn
        self.agent.cfg = SimpleNamespace(
            token="fixture-token", do_model="fixture-model",
            allowed_chat_ids={111}, fleet_enabled=True,
            retry_interval=60, poll_timeout=50,
        )
        self.agent.albums = {}
        self.agent.stop = False
        self.agent.last_sweep = 10_000.0

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def request(self, state):
        self.conn.execute(
            "INSERT INTO fleet_requests VALUES(?,?,?,?,?,?,?)",
            ("fixture-request", 111, 1, state, "{}", "2026-10-24", "2026-10-24"))
        self.conn.commit()

    def test_pending_executor_and_recovery_phases_shorten_poll(self):
        for state in ("preparing", "queued", "executing", "ready",
                      "analysis_started", "approval_sending", "delivering"):
            with self.subTest(state=state):
                self.conn.execute("DELETE FROM fleet_requests")
                self.request(state)
                self.assertEqual(self.agent._pending_work_poll_timeout(), 2)

    def test_owner_approval_and_terminal_rows_do_not_keep_poll_short(self):
        for state in ("approval", "done", "failed", "cancelled", "ambiguous"):
            with self.subTest(state=state):
                self.conn.execute("DELETE FROM fleet_requests")
                self.request(state)
                self.assertEqual(self.agent._pending_work_poll_timeout(), 50)

    def test_existing_voice_album_and_idle_cadence_is_preserved(self):
        self.assertEqual(self.agent._pending_work_poll_timeout(), 50)
        self.agent.albums = {"fixture-album": {}}
        self.assertEqual(self.agent._pending_work_poll_timeout(), 2)
        self.agent.albums = {}
        self.agent.voice_jobs = {1: object()}
        self.assertEqual(self.agent._pending_work_poll_timeout(), 2)
        self.agent.voice_jobs.clear()
        self.assertEqual(self.agent._pending_work_poll_timeout(), 50)

    def test_disabled_fleet_does_not_read_its_queue(self):
        self.agent.cfg.fleet_enabled = False
        self.agent.conn = mock.Mock()
        self.assertEqual(self.agent._pending_work_poll_timeout(), 50)
        self.agent.conn.execute.assert_not_called()

    def test_pending_fleet_never_lengthens_an_operator_shorter_poll(self):
        self.request("queued")
        self.agent.cfg.poll_timeout = 1
        self.assertEqual(self.agent._pending_work_poll_timeout(), 1)

    def test_failed_pending_read_uses_existing_database_containment(self):
        self.agent.conn = mock.Mock()
        error = sqlite3.OperationalError("fixture read unavailable")
        self.agent.conn.execute.side_effect = error
        with mock.patch.object(self.agent, "_db_stall") as stalled:
            self.assertEqual(self.agent._pending_work_poll_timeout(), 2)
        stalled.assert_called_once_with(error)

    def test_executor_completes_during_poll_then_idle_poll_is_restored(self):
        self.request("queued")
        clock = {"now": 10_000.0}
        ready = {"value": False}
        polls, delivered, reminders, handled = [], [], [], []

        def consume():
            if ready["value"] and not delivered:
                delivered.append(clock["now"])
                self.conn.execute(
                    "UPDATE fleet_requests SET state='done' WHERE id='fixture-request'")
                self.conn.commit()

        def poll(token, method, params=None, timeout=None):
            if method != "getUpdates":
                return {}
            polls.append((params["timeout"], timeout))
            if len(polls) == 1:
                # The executor completes while Telegram's poll is waiting.
                # Advance the fake clock rather than sleeping in a flaky test.
                clock["now"] += params["timeout"]
                ready["value"] = True
                return [{"update_id": 2}]
            self.agent.stop = True
            return []

        def process(updates):
            handled.extend((update["update_id"], clock["now"]) for update in updates)
            return max((update["update_id"] for update in updates), default=None)

        with mock.patch.object(entry.time, "time", lambda: clock["now"]), \
                mock.patch.object(entry.common, "sd_notify", return_value=True), \
                mock.patch.object(entry, "tg_call", side_effect=poll), \
                mock.patch.object(entry.Agent, "SCHEDULER_TICKS",
                                  ("check_assistant_tasks", "fire_due_reminders")), \
                mock.patch.object(self.agent, "watchdog_ping"), \
                mock.patch.object(self.agent, "_warn_if_watchdog_budget_is_too_tight"), \
                mock.patch.object(self.agent, "announce_deploy_if_changed"), \
                mock.patch.object(self.agent, "replay_pending_updates"), \
                mock.patch.object(self.agent, "flush_voice_jobs"), \
                mock.patch.object(self.agent, "flush_albums"), \
                mock.patch.object(self.agent, "_poll_recovered"), \
                mock.patch.object(self.agent, "check_assistant_tasks", side_effect=consume), \
                mock.patch.object(self.agent, "fire_due_reminders",
                                  side_effect=lambda: reminders.append(clock["now"])), \
                mock.patch.object(self.agent, "process_update_batch", side_effect=process):
            self.agent.run()

        self.assertEqual(polls, [(2, 17), (50, 65)])
        self.assertEqual(delivered, [10_002.0])
        self.assertEqual(handled, [(2, 10_002.0)])
        self.assertEqual(reminders, [10_000.0, 10_002.0])
        self.assertEqual(store.kv_get(self.conn, "offset"), "3")
