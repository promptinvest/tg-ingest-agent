"""Production async fleet dispatch, durable phases and compatibility boundaries."""
import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import common
import fleet
import fleet_async
import fleet_protocol as protocol
import llm
import router
import runtime
import store
import tg_ingest_agent as entry
import worker_client
from testlib import make_config


def inventory():
    return {"version": protocol.VERSION,
            "hosts": {"host-a": {"label": "Example application host", "role": "Synthetic applications",
                                  "aliases": [], "restart_services": ["fixture-worker.service"]}},
            "projects": {"project-alpha": {"label": "Project Alpha", "hosts": ["host-a"],
                                           "recorded_at": "2026-10-24", "notes": "Synthetic placement."}}}


def plan():
    return {"hosts": ["host-a"], "facets": ["resources"], "clarification": "",
            "operation": "inspect", "service": ""}


def response():
    return {"version": protocol.VERSION, "observed_at": protocol.now(), "hosts": {
        "host-a": {"host": "host-a", "observed_at": protocol.now(),
                   "resources": {"cpu_percent": 12, "cpu_sample_seconds": .2,
                                 "memory_used_percent": 30, "disk_used_percent": 20},
                   "projects": {}}}}


def completion(value):
    return {"choices": [{"message": {"content": json.dumps(value)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


class FleetAsyncExecution(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.handlers = dict(runtime._HANDLERS)
        self.cfg = make_config(DB_PATH=str(self.root / "agent.db"), MEDIA_DIR=str(self.root / "media"),
                               CARA_FLEET_ENABLED="true", LLM_PROFILES_JSON=json.dumps({
                                   name: {"primary": "deepseek-4-flash", "fallbacks": [], "max_tokens": 120,
                                          "json_required": True, "timeout": 90}
                                   for name in ("task_planner", "task_synthesis")}))
        self.cfg.fleet_inventory = str(self.root / "inventory.json")
        self.cfg.fleet_facts = str(self.root / "absent-facts.json")
        self.cfg.fleet_spool = str(self.root / "spool")
        (self.root / "inventory.json").write_text(protocol.canonical(inventory()), encoding="utf-8")
        self.agent = entry.Agent(self.cfg)
        self.agent.fleet_async = True
        self.conn = self.agent.conn
        self.sent, self.release_events, self.jobs = [], [], []
        self.reply_patch = mock.patch.object(self.agent, "reply", side_effect=self.reply)
        self.reply_patch.start()
        self.typing_patch = mock.patch.object(self.agent, "send_chat_action")
        self.typing_patch.start()
        common.set_current_trace(None)

    def tearDown(self):
        for event in self.release_events:
            event.set()
        for call in list(getattr(self.agent, "_fleet_inference_calls", {}).values()) + self.jobs:
            if call.future is not None:
                try:
                    call.future.result(timeout=3)
                except Exception:
                    pass
        self.reply_patch.stop()
        self.typing_patch.stop()
        self.conn.close()
        runtime._HANDLERS.clear()
        runtime._HANDLERS.update(self.handlers)
        common.set_current_trace(None)
        self.tmp.cleanup()

    def reply(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))
        return {"message_id": 100 + len(self.sent)}

    def source(self, update_id=1, text="Check the fixture memory now"):
        update = {"update_id": update_id, "message": {"chat": {"id": 111}, "from": {"id": 111},
                  "message_id": update_id + 10, "date": int(time.time()), "text": text}}
        store.telegram_update_receive(self.conn, update, chat_id=111)
        store.convo_add(self.conn, 111, "user", text, source="boss", update_id=update_id,
                        tg_message_id=update_id + 10)
        self.agent._current_update_id = update_id
        return update

    def start(self, update_id=1):
        update = self.source(update_id)
        self.agent.do_fleet(111, "en", update["message"]["text"])
        return self.row(update_id)

    def row(self, update_id=1):
        return self.conn.execute("SELECT * FROM fleet_requests WHERE source_update=?", (update_id,)).fetchone()

    def payload(self, update_id=1):
        return json.loads(self.row(update_id)["payload"])

    def wait_futures(self):
        for call in getattr(self.agent, "_fleet_inference_calls", {}).values():
            if call.future is not None:
                try:
                    call.future.result(timeout=3)
                except Exception:
                    pass

    def drain(self, state="queued", update_id=1):
        for _ in range(20):
            if self.row(update_id)["state"] == state:
                return self.row(update_id)
            self.agent.check_fleet_requests()
            self.wait_futures()
        self.fail("fixture did not reach " + state + "; actual=" + self.row(update_id)["state"])

    @staticmethod
    def transport(prepared):
        if prepared["skill"] == "fleet_planner":
            return completion(plan())
        return completion({"highlights": [], "hypotheses": [], "next_checks": []})

    def test_request_is_source_bound_and_persisted_before_any_paid_planning(self):
        with mock.patch.object(llm, "chat_profile", side_effect=AssertionError("owner model blocked")), \
                mock.patch.object(llm, "transport_chat") as send:
            row = self.start()
        self.assertEqual(row["state"], "planning_queued")
        send.assert_not_called()
        payload = self.payload()
        self.assertEqual(payload["version"], 2)
        self.assertEqual(payload["inference"]["binding"]["source_hash"], payload["source_hash"])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM llm_usage").fetchone()[0], 0)
        self.assertEqual(list((self.root / "spool" / "requests").glob("*.json")), [])
        self.assertEqual(len(self.sent), 1)

    def test_planning_inspection_and_synthesis_use_shared_grounded_delivery(self):
        with mock.patch.object(llm, "transport_chat", side_effect=self.transport) as send:
            self.start()
            self.drain()
            with mock.patch.object(worker_client, "poll", return_value=response()):
                self.drain("done")
        self.assertEqual(send.call_count, 2)
        payload = self.payload()
        self.assertEqual(payload["model_calls"], 2)
        self.assertEqual(payload["inference"]["name"], "synthesis")
        self.assertEqual(len(payload["inference_history"]), 1)
        self.assertIn("Example application host", payload["answer"])
        self.assertIn("12", payload["answer"])
        self.assertEqual(len(payload["result_messages"]), len(payload["answer_pages"]))
        self.assertEqual(len(self.sent), 1 + len(payload["answer_pages"]))
        self.assertTrue(all(row["kind"] == "chat" for row in self.conn.execute("SELECT kind FROM llm_usage")))

    def test_schema_repair_stays_within_original_two_planning_attempts(self):
        with mock.patch.object(llm, "transport_chat", side_effect=[completion({}), completion(plan())]) as send:
            self.start()
            self.drain()
        self.assertEqual(send.call_count, 2)
        self.assertEqual(self.payload()["planning_attempt"], 2)
        self.assertEqual(self.payload()["model_calls"], 2)
        self.assertIn("Repair the previous schema error", json.loads(send.call_args.args[0]["request"].data)["messages"][0]["content"])

    def test_source_edit_discards_late_result_without_delivery_or_new_send(self):
        with mock.patch.object(llm, "transport_chat", side_effect=self.transport) as send:
            self.start()
            self.agent.check_fleet_requests()
            self.wait_futures()
            before = len(self.sent)
            self.conn.execute("UPDATE conversation SET text='Changed owner question' WHERE update_id=1")
            self.conn.commit()
            self.agent.check_fleet_requests()
            self.agent.check_fleet_requests()
        self.assertEqual(self.row()["state"], "failed")
        self.assertEqual(len(self.sent), before)
        self.assertEqual(send.call_count, 1)
        self.assertFalse((self.root / "spool" / "requests").exists())

    def test_wrong_nonce_or_revised_authority_cannot_accept_late_completion(self):
        for change in ("nonce", "facts"):
            with self.subTest(change=change):
                update_id = 1 if change == "nonce" else 2
                with mock.patch.object(llm, "transport_chat", side_effect=self.transport) as send:
                    self.start(update_id)
                    self.agent.check_fleet_requests()
                    self.wait_futures()
                    before = len(self.sent)
                    if change == "nonce":
                        row = self.row(update_id)
                        payload = json.loads(row["payload"])
                        payload["inference"]["binding"]["nonce"] = "f" * 32
                        self.agent._fleet_save(row, row["state"], payload, "fixture_nonce_changed")
                        self.agent.check_fleet_requests()
                    else:
                        with mock.patch.object(self.agent, "_fleet_facts_revision", return_value="f" * 64):
                            self.agent.check_fleet_requests()
                    self.agent.check_fleet_requests()
                self.assertEqual(self.row(update_id)["state"], "failed")
                self.assertEqual(len(self.sent), before)
                self.assertEqual(send.call_count, 1)

    def test_crash_after_started_planning_never_replays_unknown_paid_work(self):
        with mock.patch.object(llm, "transport_chat", side_effect=self.transport) as send:
            self.start()
            self.agent.check_fleet_requests()
            self.wait_futures()
            self.jobs.extend(self.agent._fleet_inference_calls.values())
            self.agent._fleet_inference_calls = {}
            self.agent.check_fleet_requests()
            self.agent.check_fleet_requests()
        self.assertEqual(self.row()["state"], "failed")
        self.assertEqual(send.call_count, 1)
        self.assertEqual(self.payload()["inference"]["error"], "InterruptedInference")
        self.assertEqual(self.conn.execute("SELECT kind FROM llm_usage").fetchone()[0], "unknown_chat")

    def test_completed_planning_receipt_resumes_without_second_model_call(self):
        with mock.patch.object(llm, "transport_chat", side_effect=self.transport) as send:
            self.start()
            self.agent.check_fleet_requests()
            self.wait_futures()
            call = next(iter(self.agent._fleet_inference_calls.values()))
            self.assertTrue(call.advance())
            row = self.row()
            self.agent._fleet_complete_phase(row, json.loads(row["payload"]), call)
            self.agent._fleet_inference_calls = {}
            self.agent.check_fleet_requests()
        self.assertEqual(self.row()["state"], "queued")
        self.assertEqual(send.call_count, 1)

    def test_interrupted_synthesis_uses_retained_measured_report(self):
        with mock.patch.object(llm, "transport_chat", side_effect=self.transport) as send:
            self.start()
            self.drain()
            with mock.patch.object(worker_client, "poll", return_value=response()):
                self.agent.check_fleet_requests()
            self.assertEqual(self.row()["state"], "synthesis_queued")
            self.agent.check_fleet_requests()
            self.wait_futures()
            self.jobs.extend(self.agent._fleet_inference_calls.values())
            self.agent._fleet_inference_calls = {}
            self.agent.check_fleet_requests()
        self.assertEqual(self.row()["state"], "done")
        self.assertEqual(send.call_count, 2)
        self.assertIn("automatic analysis is unavailable", self.payload()["answer"])
        self.assertIn("Example application host", self.payload()["answer"])

    def test_task_budget_rejects_reservation_before_transport(self):
        self.cfg.task_cost_limit_usd = 0.000000001
        with mock.patch.object(llm, "transport_chat") as send:
            self.start()
            self.agent.check_fleet_requests()
        self.assertEqual(self.row()["state"], "failed")
        send.assert_not_called()
        self.assertEqual(self.conn.execute("SELECT kind,cost_usd FROM llm_usage").fetchone()["kind"], "rejected_chat")
        self.assertEqual(self.payload()["cost"], 0)

    def test_eight_jobs_pump_without_starvation_with_one_background_model(self):
        entered = [threading.Event()]
        release = threading.Event()
        self.release_events.append(release)
        indices, lock = [], threading.Lock()
        def blocked(prepared):
            with lock:
                index = len(indices)
                indices.append(index)
            if index < 1:
                entered[index].set()
                if not release.wait(3):
                    raise AssertionError("fixture model was not released")
            return self.transport(prepared)
        with mock.patch.object(llm, "transport_chat", side_effect=blocked):
            for update_id in range(1, 9):
                self.start(update_id)
            self.agent.check_fleet_requests()
            self.assertTrue(all(event.wait(2) for event in entered))
            self.assertEqual(len(self.agent._fleet_inference_calls), 8)
            states = [row[0] for row in self.conn.execute("SELECT state FROM fleet_requests")]
            self.assertEqual(states.count("planning_started"), 1)
            self.assertEqual(states.count("planning_queued"), 7)
            self.assertEqual(self.conn.execute("SELECT count(*) FROM llm_usage").fetchone()[0], 1)
            before = self.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0]
            self.start(9)
            self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], before)
            release.set()
            for update_id in range(1, 9):
                self.drain(update_id=update_id)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_requests WHERE state='queued'").fetchone()[0], 8)

    def test_production_run_handles_owner_update_and_due_reminder_during_blocked_model(self):
        # This test uses the real run(), process_update_batch and dispatcher;
        # the model is held by synchronization, without a wall-clock sleep.
        self.agent.fleet_async = False
        self.agent.last_sweep = time.time()
        entered, release = threading.Event(), threading.Event()
        self.release_events.append(release)
        polls = []
        first = {"update_id": 101, "message": {"chat": {"id": 111}, "from": {"id": 111},
                 "message_id": 201, "date": int(time.time()), "text": "Check the fixture memory now"}}
        second = {"update_id": 102, "message": {"chat": {"id": 111}, "from": {"id": 111},
                  "message_id": 202, "date": int(time.time()), "text": "/stats"}}
        def transport(prepared):
            entered.set()
            if not release.wait(3):
                raise AssertionError("fixture live-loop model was not released")
            return self.transport(prepared)
        def poll(token, method, params=None, timeout=None):
            if method != "getUpdates":
                return {}
            polls.append(params["timeout"])
            if len(polls) == 1:
                return [first]
            if len(polls) == 2:
                self.assertTrue(entered.wait(2))
                store.reminder_add(self.conn, 111, "fixture reminder during model",
                                   (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat())
                return [second]
            self.assertFalse(release.is_set())
            self.agent.stop = True
            return []
        def route(cfg, conn, chat, text, pending, **kwargs):
            return {"action": "stats" if text == "/stats" else "fleet_investigate",
                    "params": {}, "confidence": 1, "risk": "read"}
        with mock.patch.object(llm, "transport_chat", side_effect=transport), \
                mock.patch.object(llm, "chat_profile", side_effect=AssertionError("model on owner loop")), \
                mock.patch.object(router, "route", side_effect=route), \
                mock.patch.object(entry, "tg_call", side_effect=poll), \
                mock.patch.object(entry.Agent, "SCHEDULER_TICKS", ("check_assistant_tasks", "fire_due_reminders")), \
                mock.patch.object(self.agent, "announce_deploy_if_changed"), \
                mock.patch.object(self.agent, "replay_pending_updates"), \
                mock.patch.object(self.agent, "_recent_boss_msg", return_value=None):
            self.agent.run()
        self.assertTrue(self.agent.fleet_async)
        self.assertEqual(polls, [50, 2, 2])
        self.assertEqual(store.telegram_update_get(self.conn, 102)["status"], "done")
        self.assertEqual(self.row(101)["state"], "planning_started")
        self.assertTrue(any("fixture reminder during model" in text for _, text, _ in self.sent))
        self.assertFalse(release.is_set())

    def test_every_new_phase_has_pending_completion_poll_cadence(self):
        self.start()
        for state in fleet_async.STATES:
            with self.subTest(state=state):
                self.conn.execute("UPDATE fleet_requests SET state=?", (state,))
                self.conn.commit()
                self.assertEqual(self.agent._pending_work_poll_timeout(), 2)

    def test_consumed_synthesis_resumes_delivery_without_repeating_model(self):
        with mock.patch.object(llm, "transport_chat", side_effect=self.transport) as send:
            self.start()
            self.drain()
            with mock.patch.object(worker_client, "poll", return_value=response()):
                self.agent.check_fleet_requests()
            self.agent.check_fleet_requests()
            self.wait_futures()
            with mock.patch.object(self.agent, "_fleet_after_analysis", side_effect=SystemExit):
                with self.assertRaises(SystemExit):
                    self.agent.check_fleet_requests()
            self.assertEqual(self.row()["state"], "synthesis_consumed")
            self.agent.check_fleet_requests()
        self.assertEqual(self.row()["state"], "done")
        self.assertEqual(send.call_count, 2)
        self.assertEqual(len(self.sent), 1 + len(self.payload()["answer_pages"]))

    def test_source_edit_during_inspection_suppresses_late_observation_delivery(self):
        with mock.patch.object(llm, "transport_chat", side_effect=self.transport) as send:
            self.start()
            self.drain()
            before = len(self.sent)
            self.conn.execute("UPDATE conversation SET text='Changed during inspection' WHERE update_id=1")
            self.conn.commit()
            with mock.patch.object(worker_client, "poll", return_value=response()) as poll:
                self.agent.check_fleet_requests()
        self.assertEqual(self.row()["state"], "failed")
        self.assertEqual(len(self.sent), before)
        self.assertEqual(send.call_count, 1)
        poll.assert_not_called()
