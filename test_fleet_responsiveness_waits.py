"""Logical 90-second model waits through the real owner loop; no network I/O."""
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
import json
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

import fleet
import fleet_inference
import fleet_protocol as protocol
import llm
import reminders_svc
import router
import runtime
import store
import tg_ingest_agent as entry
import worker_client
import test_fleet_async as fixtures


class LogicalClock:
    def __init__(self):
        self.origin = datetime.now(timezone.utc)
        self.elapsed = 0

    def time(self):
        return self.origin.timestamp() + self.elapsed

    def now(self, zone=timezone.utc):
        value = self.origin + timedelta(seconds=self.elapsed)
        return value.astimezone(zone) if zone is not None else value.replace(tzinfo=None)

    def advance(self, seconds):
        self.elapsed += seconds


class FleetProductionResponsiveness(unittest.TestCase):
    base_setup = fixtures.FleetAsyncExecution.setUp
    tearDown = fixtures.FleetAsyncExecution.tearDown
    source = fixtures.FleetAsyncExecution.source
    row = fixtures.FleetAsyncExecution.row
    payload = fixtures.FleetAsyncExecution.payload
    wait_futures = fixtures.FleetAsyncExecution.wait_futures
    drain = fixtures.FleetAsyncExecution.drain
    start = fixtures.FleetAsyncExecution.start
    transport = staticmethod(fixtures.FleetAsyncExecution.transport)

    def setUp(self):
        self.base_setup()
        self.clock = LogicalClock()
        self.owner = threading.get_ident()
        self.timeline = []
        self.polls = []
        self.agent.last_sweep = self.clock.time()

    def reply(self, chat_id, text, **kwargs):
        clock = getattr(self, "clock", None)
        if clock is not None:
            self.timeline.append((clock.elapsed, threading.get_ident(), text))
        return fixtures.FleetAsyncExecution.reply(self, chat_id, text, **kwargs)

    def update(self, identity, text):
        return {"update_id": identity, "message": {"chat": {"id": 111}, "from": {"id": 111},
                "message_id": identity + 100, "date": int(self.clock.time()), "text": text}}

    @contextmanager
    def production_loop(self, poll):
        clock = self.clock
        class ClockDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock.now(tz)
        def route(cfg, conn, chat, text, pending, **kwargs):
            return {"action": "stats" if text == "/stats" else "fleet_investigate",
                    "params": {}, "confidence": 1, "risk": "read"}
        with ExitStack() as stack:
            for module in (protocol, store, reminders_svc):
                stack.enter_context(mock.patch.object(module, "datetime", ClockDatetime))
            stack.enter_context(mock.patch.object(entry, "time", SimpleNamespace(
                time=clock.time, monotonic=time.monotonic, sleep=time.sleep)))
            stack.enter_context(mock.patch.object(entry, "tg_call", side_effect=poll))
            stack.enter_context(mock.patch.object(router, "route", side_effect=route))
            stack.enter_context(mock.patch.object(llm, "chat_profile", side_effect=AssertionError("model I/O on owner loop")))
            stack.enter_context(mock.patch.object(entry.Agent, "SCHEDULER_TICKS", ("check_assistant_tasks", "fire_due_reminders")))
            stack.enter_context(mock.patch.object(self.agent, "announce_deploy_if_changed"))
            stack.enter_context(mock.patch.object(self.agent, "replay_pending_updates"))
            stack.enter_context(mock.patch.object(self.agent, "_recent_boss_msg", return_value=None))
            # Sweep scheduling remains real; unrelated maintenance effects are
            # outside this deterministic owner/reminder responsiveness fixture.
            stack.enter_context(mock.patch.object(self.agent, "check_reminder_expiry"))
            stack.enter_context(mock.patch.object(self.agent, "enqueue_maintenance_jobs"))
            stack.enter_context(mock.patch.object(runtime, "drain"))
            yield

    def alarms(self, seconds):
        return [(value, store.reminder_add(self.conn, 111, "logical reminder " + str(value),
                   (self.clock.origin + timedelta(seconds=value)).isoformat())) for value in seconds]

    def assert_progress(self, alarms, owner_updates, *, pending_until):
        for due, identity in alarms:
            row = store.reminder_get(self.conn, identity)
            self.assertIsNotNone(row["last_fired_at"])
            fired = (datetime.fromisoformat(row["last_fired_at"]) - self.clock.origin).total_seconds()
            self.assertGreaterEqual(fired, due)
            self.assertLessEqual(fired - due, self.agent.PENDING_WORK_POLL_SECONDS)
            matches = [(at, thread) for at, thread, text in self.timeline if "logical reminder " + str(due) in text]
            self.assertEqual(len(matches), 1)
            self.assertLess(matches[0][0], pending_until)
            self.assertEqual(matches[0][1], self.owner)
        for due, identity in owner_updates:
            update = store.telegram_update_get(self.conn, identity)
            self.assertEqual(update["status"], "done")
            created = datetime.fromisoformat(update["updated_at"])
            elapsed = (created - self.clock.origin).total_seconds()
            self.assertGreaterEqual(elapsed, due)
            self.assertLessEqual(elapsed - due, self.agent.PENDING_WORK_POLL_SECONDS)
        self.assertTrue(all(thread == self.owner for _, thread, _ in self.timeline))

    def test_planner_held_for_90_logical_seconds_keeps_real_owner_and_reminders_moving(self):
        entered, release = threading.Event(), threading.Event()
        self.release_events.append(release)
        alarms = self.alarms((15, 45, 75))
        owner_updates = [(10, 102), (40, 103), (70, 104)]
        next_owner = list(owner_updates)
        held, completion = {}, {}
        def transport(prepared):
            self.assertNotEqual(threading.get_ident(), self.owner)
            self.assertEqual(prepared["skill"], "fleet_planner")
            self.assertGreater(prepared["timeout"], 89)
            self.assertLessEqual(prepared["timeout"], 90)
            held["started"] = self.clock.elapsed
            entered.set()
            if not release.wait(15):
                raise AssertionError("synchronized fixture planner was not released")
            held["released"] = self.clock.elapsed
            return self.transport(prepared)
        def poll(token, method, params=None, timeout=None):
            if method != "getUpdates":
                return {}
            self.polls.append((self.clock.elapsed, params["timeout"]))
            if len(self.polls) == 1:
                return [self.update(101, "Check the fixture memory now")]
            self.assertTrue(entered.wait(2))
            self.assertEqual(params["timeout"], self.agent.PENDING_WORK_POLL_SECONDS)
            current = self.row(101)
            if current["state"] == "queued":
                completion["consumed"] = self.clock.elapsed
                self.agent.stop = True
                return []
            self.assertEqual(current["state"], "planning_started")
            if self.clock.elapsed >= 90:
                self.assert_progress(alarms, owner_updates, pending_until=91)
                self.assertFalse(release.is_set())
                release.set()
                self.agent._fleet_inference_calls[current["id"]].future.result(timeout=2)
                completion["ready"] = self.clock.elapsed
            self.clock.advance(params["timeout"])
            if next_owner and self.clock.elapsed >= next_owner[0][0]:
                _, identity = next_owner.pop(0)
                return [self.update(identity, "/stats")]
            return []
        with self.production_loop(poll), mock.patch.object(llm, "transport_chat", side_effect=transport) as paid, \
                mock.patch.object(worker_client, "poll", return_value=None):
            self.agent.run()
        self.assertEqual(held["released"] - held["started"], 90)
        self.assertLessEqual(completion["consumed"] - completion["ready"], self.agent.PENDING_WORK_POLL_SECONDS)
        self.assertEqual(self.polls[0][1], self.cfg.poll_timeout)
        self.assertTrue(all(timeout == 2 for _, timeout in self.polls[1:]))
        self.assertEqual(paid.call_count, 1)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM llm_usage").fetchone()[0], 1)
        self.assertEqual(self.row(101)["state"], "queued")

    def held_syntheses(self, count):
        with mock.patch.object(llm, "transport_chat", side_effect=self.transport):
            for identity in range(1, count + 1):
                self.start(identity)
                self.drain("queued", update_id=identity)
        entered = [threading.Event() for _ in range(count)]
        release = [threading.Event() for _ in range(count)]
        self.release_events.extend(release)
        calls, report_times, ready, consumed = [], [], {}, {}
        active = 0
        maximum = 0
        guard = threading.Lock()
        alarms = self.alarms((15, 45, 75) if count == 1 else (15, 45, 75, 105, 135, 165))
        owner_updates = [(value, 101 + index) for index, value in enumerate((10, 40, 70) if count == 1 else (10, 40, 70, 100, 130, 160))]
        next_owner = list(owner_updates)
        def transport(prepared):
            nonlocal active, maximum
            self.assertNotEqual(threading.get_ident(), self.owner)
            self.assertEqual(prepared["skill"], "fleet_synthesis")
            self.assertGreater(prepared["timeout"], 89)
            self.assertLessEqual(prepared["timeout"], 90)
            with guard:
                index = len(calls)
                calls.append({"started": self.clock.elapsed})
                active += 1
                maximum = max(maximum, active)
            entered[index].set()
            try:
                if not release[index].wait(15):
                    raise AssertionError("synchronized fixture synthesis was not released")
                calls[index]["released"] = self.clock.elapsed
                return self.transport(prepared)
            finally:
                with guard:
                    active -= 1
        def worker_result(spool, **binding):
            report_times.append((binding["task_id"], self.clock.elapsed))
            return fixtures.response()
        def poll(token, method, params=None, timeout=None):
            if method != "getUpdates":
                return {}
            self.polls.append((self.clock.elapsed, params["timeout"]))
            rows = [self.row(identity) for identity in range(1, count + 1)]
            for row in rows:
                if row["state"] == "done" and row["id"] not in consumed:
                    consumed[row["id"]] = self.clock.elapsed
            if len(consumed) == count:
                self.assertEqual(params["timeout"], self.cfg.poll_timeout)
                self.agent.stop = True
                return []
            self.assertEqual(params["timeout"], self.agent.PENDING_WORK_POLL_SECONDS)
            if len(self.polls) == 1:
                self.assertTrue(all(row["state"] == "synthesis_queued" for row in rows))
                return []
            index = min(len(consumed), count - 1)
            self.assertTrue(entered[index].wait(2))
            if self.clock.elapsed - calls[index]["started"] >= 90:
                self.assertFalse(release[index].is_set())
                release[index].set()
                current = next(row for row in rows if row["state"] == "synthesis_started")
                self.agent._fleet_inference_calls[current["id"]].future.result(timeout=2)
                ready[current["id"]] = self.clock.elapsed
            self.clock.advance(params["timeout"])
            if next_owner and self.clock.elapsed >= next_owner[0][0]:
                _, identity = next_owner.pop(0)
                return [self.update(identity, "/stats")]
            return []
        with self.production_loop(poll), mock.patch.object(llm, "transport_chat", side_effect=transport) as paid, \
                mock.patch.object(worker_client, "poll", side_effect=worker_result):
            self.agent.run()
        self.assert_progress(alarms, owner_updates, pending_until=count * 90 + 3)
        self.assertEqual([at for _, at in report_times], [0] * count, "reports must become available in one owner tick")
        self.assertEqual(len(calls), count)
        self.assertEqual(paid.call_count, count)
        self.assertEqual(maximum, fleet_inference.MAX_ACTIVE_TRANSPORTS)
        self.assertEqual(maximum, 1)
        self.assertTrue(all(call["released"] - call["started"] == 90 for call in calls))
        self.assertEqual(set(ready), set(consumed))
        self.assertTrue(all(consumed[key] - ready[key] <= self.agent.PENDING_WORK_POLL_SECONDS for key in ready))
        self.assertTrue(all(timeout == 2 for _, timeout in self.polls[:-1]))
        self.assertEqual(self.polls[-1][1], self.cfg.poll_timeout)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM llm_usage").fetchone()[0], count * 2)
        for identity in range(1, count + 1):
            self.assertEqual(self.row(identity)["state"], "done")
            self.assertIn("CPU 12%", self.payload(identity)["answer"])
            self.assertEqual(self.payload(identity)["model_calls"], 2)

    def test_synthesis_held_for_90_logical_seconds_keeps_real_owner_and_reminders_moving(self):
        self.held_syntheses(1)

    def test_two_simultaneous_reports_do_not_serialize_model_waits_on_owner_loop(self):
        self.held_syntheses(2)


if __name__ == "__main__":
    unittest.main()
