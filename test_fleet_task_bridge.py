"""Real task wait/accounting/receipt path, with synthetic model/worker/Telegram I/O."""
import copy
import json
import sqlite3
import threading
import unittest
from unittest import mock
from urllib.error import HTTPError

import fleet
import fleet_facts
import fleet_investigation as investigation
import fleet_lifecycle
import fleet_protocol as protocol
import llm
import store
import task_runner
import tasking
import tool_broker
import test_fleet_async as async_tests
from test_fleet_facts import fixture


def raw_plan(text):
    return {"objective": "Answer the owner fleet question", "deliverable": "answer", "steps": [{
        "key": "inspect", "tool": "fleet.investigate", "input": {"query": text},
        "bindings": {"query": {"source": "boss_span", "start": 0, "end": len(text),
            "source_hash": tasking.source_hash(text), "transform": "literal"}},
        "depends_on": [], "purpose": "Investigate the actual owner question"}]}


class FleetTaskBridge(unittest.TestCase):
    setUp_base = async_tests.FleetAsyncExecution.setUp
    tearDown_base = async_tests.FleetAsyncExecution.tearDown
    reply = async_tests.FleetAsyncExecution.reply
    source = async_tests.FleetAsyncExecution.source
    row = async_tests.FleetAsyncExecution.row
    payload = async_tests.FleetAsyncExecution.payload
    wait_futures = async_tests.FleetAsyncExecution.wait_futures
    drain = async_tests.FleetAsyncExecution.drain
    transport = staticmethod(async_tests.FleetAsyncExecution.transport)

    def setUp(self):
        self.setUp_base()
        self.delivery_patch = mock.patch("tasks_svc.tg_call", return_value={"message_id": 9001})
        self.delivery = self.delivery_patch.start()

    def tearDown(self):
        self.delivery_patch.stop()
        self.tearDown_base()

    def task(self, text="Check the fixture memory now", *, calls=0, cost=0):
        self.source(1, text)
        task, _ = store.assistant_task_create(self.conn, 111, 1, raw_plan(text))
        self.conn.execute("UPDATE assistant_tasks SET model_calls=?,task_cost_usd=? WHERE id=?", (calls, cost, task["id"]))
        self.conn.commit()
        return store.assistant_task_get(self.conn, task["id"])

    def step(self, task):
        return store.assistant_task_steps(self.conn, task["id"])[0]

    def begin(self, task):
        return task_runner.run_task(self.agent, self.conn, task["id"])

    def receipts(self, task):
        return self.conn.execute("SELECT * FROM tool_receipts WHERE task_id=?", (task["id"],)).fetchall()

    def complete(self, task):
        self.drain()
        with mock.patch("worker_client.poll", return_value=async_tests.response()):
            self.drain("done")
        task_runner.poll_fleet_results(self.agent, self.conn)
        return store.assistant_task_get(self.conn, task["id"])

    def test_wait_reference_is_durable_before_fleet_admission_and_paid_io(self):
        task = self.task()
        original = investigation.start
        def observe(agent, **args):
            step = self.step(task)
            self.assertEqual(step["status"], "waiting_fleet")
            self.assertEqual(step["worker_job_id"], investigation.request_id(111, step["worker_nonce"]))
            self.assertEqual(step["worker_input_hash"], protocol.digest({"query": "Check the fixture memory now"}))
            self.assertEqual(self.conn.execute("SELECT status FROM task_step_attempts").fetchone()[0], "waiting")
            return original(agent, **args)
        with mock.patch.object(investigation, "start", side_effect=observe), mock.patch.object(llm, "transport_chat") as transport:
            outcome = self.begin(task)
        self.assertEqual(outcome["status"], "waiting_fleet")
        self.assertEqual(self.step(task)["attempts"], 1)
        self.assertEqual(self.payload()["task_waiter"]["step_id"], self.step(task)["id"])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM llm_usage").fetchone()[0], 0)
        transport.assert_not_called()
        self.assertEqual(self.sent, [])
        self.delivery.assert_not_called()

    def test_actual_task_wait_to_one_receipt_one_existing_task_delivery(self):
        task = self.task(calls=1, cost=.01)  # Already-paid task planning remains counted.
        with mock.patch.object(llm, "transport_chat", side_effect=self.transport) as transport:
            self.begin(task)
            finished = self.complete(task)
            for _ in range(3):
                task_runner.poll_fleet_results(self.agent, self.conn)
                task_runner.run_task(self.agent, self.conn, task["id"])
        self.assertEqual(finished["status"], "completed")
        self.assertEqual(finished["delivery_status"], "delivered")
        self.assertEqual(transport.call_count, 2)
        self.assertEqual(finished["model_calls"], 3)
        self.assertEqual(len(self.receipts(task)), 1)
        persisted = json.loads(self.receipts(task)[0]["data_json"])["value"]["investigation"]
        self.assertEqual(persisted["request_id"], self.step(task)["worker_job_id"])
        self.assertEqual(json.loads(self.receipts(task)[0]["evidence_json"]), investigation.task_evidence(persisted["request_id"]))
        self.assertEqual(self.step(task)["attempts"], 1)
        self.assertEqual(self.delivery.call_count, 1)
        self.assertIn("CPU 12%", self.delivery.call_args.args[2]["text"])
        self.assertEqual(self.sent, [])
        usage = self.conn.execute("SELECT sum(cost_usd) FROM llm_usage").fetchone()[0]
        self.assertAlmostEqual(finished["task_cost_usd"], .01 + usage)
        self.assertEqual(self.payload()["retained_delivery"]["telegram_attempts"], 0)

    def test_wait_polling_never_consumes_attempts_or_restarts_admission(self):
        task = self.task()
        self.begin(task)
        before = self.conn.execute("SELECT count(*) FROM fleet_events").fetchone()[0]
        with mock.patch.object(investigation, "start", side_effect=AssertionError("wait must not start again")), mock.patch.object(llm, "transport_chat") as send:
            for _ in range(10):
                task_runner.poll_fleet_results(self.agent, self.conn)
                task_runner.run_task(self.agent, self.conn, task["id"])
        self.assertEqual(self.step(task)["attempts"], 1)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM task_step_attempts").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_events").fetchone()[0], before)
        send.assert_not_called()

    def test_lost_admission_response_reconciles_same_row_and_budget(self):
        task = self.task()
        original = investigation.start
        def lost(agent, **args):
            original(agent, **args)
            raise ConnectionError("synthetic lost response")
        with mock.patch.object(investigation, "start", side_effect=lost):
            self.begin(task)
        request = self.step(task)["worker_job_id"]
        with mock.patch.object(llm, "transport_chat", side_effect=self.transport) as send:
            self.complete(task)
        self.assertEqual(len(self.receipts(task)), 1)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(self.row()["id"], request)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 1)

    def test_missing_admission_after_reference_is_a_gap_never_resubmitted(self):
        task = self.task()
        with mock.patch.object(investigation, "start", side_effect=ConnectionError("synthetic interruption before fleet intent")):
            self.begin(task)
        with mock.patch.object(investigation, "start", side_effect=AssertionError("must not readmit")), mock.patch.object(llm, "transport_chat") as send:
            task_runner.poll_fleet_results(self.agent, self.conn)
        finished = store.assistant_task_get(self.conn, task["id"])
        self.assertEqual(finished["status"], "completed")
        receipt = self.receipts(task)[0]
        self.assertEqual(receipt["status"], "partial")
        self.assertIn("admission_unavailable", receipt["data_json"])
        self.assertEqual(finished["model_calls"], 0)
        send.assert_not_called()

    def test_original_task_call_limit_blocks_nested_first_paid_transport(self):
        task = self.task(calls=self.cfg.task_model_call_limit)
        with mock.patch.object(llm, "transport_chat") as send:
            self.begin(task)
            self.drain("failed")
            task_runner.poll_fleet_results(self.agent, self.conn)
        send.assert_not_called()
        finished = store.assistant_task_get(self.conn, task["id"])
        self.assertEqual(finished["model_calls"], self.cfg.task_model_call_limit)
        self.assertEqual(finished["task_cost_usd"], 0)
        self.assertEqual(self.conn.execute("SELECT sum(cost_usd) FROM llm_usage").fetchone()[0], 0)
        self.assertIn("investigation_failed", self.receipts(task)[0]["data_json"])

    def test_task_cost_reservation_includes_prior_planning_spend(self):
        task = self.task(calls=1, cost=self.cfg.task_cost_limit_usd - .0000000001)
        with mock.patch.object(llm, "transport_chat") as send:
            self.begin(task)
            self.drain("failed")
            task_runner.poll_fleet_results(self.agent, self.conn)
        send.assert_not_called()
        finished = store.assistant_task_get(self.conn, task["id"])
        self.assertEqual(finished["model_calls"], 1)
        self.assertAlmostEqual(finished["task_cost_usd"], task["task_cost_usd"])

    def test_every_fallback_reserves_a_task_call_and_can_exhaust_remaining_limit(self):
        task = self.task(calls=self.cfg.task_model_call_limit - 1)
        profiles = llm.profiles(self.cfg)
        profiles["task_planner"]["fallbacks"] = ["deepseek-4-pro"]
        rejection = HTTPError("https://example.invalid", 400, "synthetic rejection", {}, None)
        with mock.patch.object(llm, "profiles", return_value=profiles), mock.patch.object(llm, "transport_chat", side_effect=rejection) as send:
            self.begin(task)
            self.drain("failed")
            task_runner.poll_fleet_results(self.agent, self.conn)
        self.assertEqual(send.call_count, 1)
        finished = store.assistant_task_get(self.conn, task["id"])
        self.assertEqual(finished["model_calls"], self.cfg.task_model_call_limit)
        self.assertEqual(finished["task_cost_usd"], 0)

    def test_unknown_paid_transport_is_counted_once_and_never_replayed(self):
        task = self.task(calls=1, cost=.01)
        with mock.patch.object(llm, "transport_chat", side_effect=TimeoutError("synthetic unknown")) as send:
            self.begin(task)
            self.drain("failed")
            for _ in range(4):
                task_runner.poll_fleet_results(self.agent, self.conn)
        finished = store.assistant_task_get(self.conn, task["id"])
        self.assertEqual(send.call_count, 1)
        self.assertEqual(finished["model_calls"], 2)
        self.assertGreater(finished["task_cost_usd"], .01)
        receipt = self.receipts(task)[0]
        self.assertEqual(receipt["status"], "partial")
        self.assertIn("unknown_outcome", receipt["data_json"])
        self.assertEqual(self.delivery.call_count, 1)

    def test_restart_waiting_step_is_not_reclaimed_or_reinvoked(self):
        task = self.task()
        self.begin(task)
        store.assistant_task_reclaim_stale(self.conn)
        self.assertEqual(self.step(task)["status"], "waiting_fleet")
        self.assertEqual(self.step(task)["attempts"], 1)
        with mock.patch.object(llm, "transport_chat", side_effect=self.transport):
            self.complete(task)
        self.assertEqual(len(self.receipts(task)), 1)
        self.assertEqual(self.step(task)["attempts"], 1)

    def test_source_edit_before_dispatch_blocks_transport_and_result_receipt(self):
        task = self.task()
        self.begin(task)
        self.conn.execute("UPDATE conversation SET text='Edited owner request' WHERE update_id=1")
        self.conn.commit()
        with mock.patch.object(llm, "transport_chat") as send:
            self.agent.check_fleet_requests()
            task_runner.poll_fleet_results(self.agent, self.conn)
        self.assertEqual(store.assistant_task_get(self.conn, task["id"])["status"], "blocked")
        self.assertEqual(len(self.receipts(task)), 0)
        send.assert_not_called()
        self.assertEqual(self.sent, [])

    def test_cancellation_before_dispatch_keeps_source_and_creates_no_receipt(self):
        task = self.task()
        self.begin(task)
        self.assertEqual(store.assistant_task_cancel(self.conn, task["id"], 111), "cancel_requested")
        with mock.patch.object(llm, "transport_chat") as send:
            task_runner.poll_fleet_results(self.agent, self.conn)
            self.agent.check_fleet_requests()
            task_runner.poll_fleet_results(self.agent, self.conn)
        self.assertEqual(store.assistant_task_get(self.conn, task["id"])["status"], "cancelled")
        self.assertEqual(self.step(task)["status"], "cancelled")
        self.assertEqual(len(self.receipts(task)), 0)
        send.assert_not_called()
        self.assertEqual(store.assistant_task_source(self.conn, 111, 1)["text"], "Check the fixture memory now")

    def test_cancelled_inflight_call_keeps_reserved_spend_until_original_settles(self):
        task = self.task(calls=1, cost=.01)
        entered, release = threading.Event(), threading.Event()
        self.release_events.append(release)
        def held(prepared):
            entered.set()
            release.wait(3)
            return self.transport(prepared)
        with mock.patch.object(llm, "transport_chat", side_effect=held) as send:
            self.begin(task)
            self.agent.check_fleet_requests()
            self.assertTrue(entered.wait(1))
            reserved = store.assistant_task_get(self.conn, task["id"])["task_cost_usd"]
            self.assertGreater(reserved, .01)
            store.assistant_task_cancel(self.conn, task["id"], 111)
            task_runner.poll_fleet_results(self.agent, self.conn)
            self.assertEqual(store.assistant_task_get(self.conn, task["id"])["status"], "cancel_requested")
            self.assertEqual(self.step(task)["status"], "waiting_fleet")
            self.assertEqual(self.receipts(task), [])
            release.set()
            self.wait_futures()
            self.agent.check_fleet_requests()
            self.agent.check_fleet_requests()
            task_runner.poll_fleet_results(self.agent, self.conn)
        finished = store.assistant_task_get(self.conn, task["id"])
        self.assertEqual(send.call_count, 1)
        self.assertEqual(finished["status"], "cancelled")
        self.assertEqual(finished["model_calls"], 2)
        self.assertAlmostEqual(finished["task_cost_usd"], .01 + self.conn.execute("SELECT sum(cost_usd) FROM llm_usage").fetchone()[0])
        self.assertEqual(self.receipts(task), [])
        self.assertEqual(self.sent, [])

    def test_purge_hold_prevents_admission_or_receipt_and_deleted_rows_stay_deleted(self):
        task = self.task()
        self.begin(task)
        with mock.patch.object(fleet_lifecycle, "admission_paused", return_value=True), mock.patch.object(llm, "transport_chat") as send:
            self.agent.check_fleet_requests()
            task_runner.poll_fleet_results(self.agent, self.conn)
        self.assertEqual(self.step(task)["status"], "waiting_fleet")
        self.assertEqual(self.receipts(task), [])
        send.assert_not_called()
        nonce = self.agent.prepare_task_purge()
        fleet_lifecycle.begin_quiescence(self.conn, nonce)
        executor = {"version": "cara-fleet-queue/v1", "action": "purge_content", "nonce": nonce,
                    "status": "quiesced", "implementation_hash": "d" * 64, "pid": 123}
        fleet_lifecycle.record_quiescence(self.conn, nonce, executor,
            {"status": "settled", "pending_calls": 0}, fleet_lifecycle.db_preview(self.conn))
        preview = store.purge_preview(self.conn, "all")
        info, _ = store.purge_execute(self.conn, "all", task_purge_nonce=nonce)
        self.assertEqual(info, preview)
        self.agent.check_fleet_requests()
        task_runner.poll_fleet_results(self.agent, self.conn)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM assistant_tasks").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM tool_receipts").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 0)

    def test_recorded_catalog_answer_uses_same_task_receipt_without_host_probe(self):
        task = self.task("Give the recorded address of Example application host.", calls=1)
        plan = dict(kind="catalog", subjects=["host-a"], fields=["public_ipv4"], scope="explicit", presentation="list", detail="concise", clarification="")
        with mock.patch.object(fleet_facts, "load", return_value=fixture()), mock.patch.object(llm, "transport_chat", return_value=async_tests.completion(plan)) as send, mock.patch.object(fleet, "submit", side_effect=AssertionError("known facts need no host probe")):
            self.begin(task)
            self.drain("done")
            task_runner.poll_fleet_results(self.agent, self.conn)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(len(self.receipts(task)), 1)
        self.assertIn("192.0.2.10", self.delivery.call_args.args[2]["text"])
        self.assertEqual(self.sent, [])

    def test_stored_reference_tampering_cannot_read_another_investigation(self):
        task = self.task()
        self.begin(task)
        self.conn.execute("UPDATE assistant_task_steps SET worker_input_hash=? WHERE id=?", ("0" * 64, self.step(task)["id"]))
        self.conn.commit()
        with mock.patch.object(llm, "transport_chat") as send:
            self.agent.check_fleet_requests()
            task_runner.poll_fleet_results(self.agent, self.conn)
        self.assertEqual(len(self.receipts(task)), 0)
        self.assertEqual(store.assistant_task_get(self.conn, task["id"])["status"], "blocked")
        send.assert_not_called()

    def test_model_or_predecessor_output_cannot_replace_canonical_query_binding(self):
        text = "Check the fixture memory now"
        candidate = raw_plan(text)
        candidate["steps"][0]["bindings"]["query"]["source"] = "step_output"
        with self.assertRaises(tasking.PlanError):
            tasking.validate_plan(candidate, text)
        candidate = raw_plan(text)
        candidate["steps"][0]["bindings"] = {}
        with self.assertRaises(tasking.PlanError):
            tasking.validate_plan(candidate, text)
        candidate = raw_plan(text)
        candidate["steps"][0]["input"]["query"] = "Run an unrelated inspection"
        with self.assertRaises(tasking.PlanError):
            tasking.validate_plan(candidate, text)

    def test_registry_is_read_only_networked_and_versions_legacy_tools_stay_exact(self):
        spec = tool_broker.get_spec("fleet.investigate")
        tool_broker.assert_registry()
        self.assertEqual(spec.bound_inputs, frozenset({"query"}))
        self.assertEqual(spec.risk, "network_read")
        self.assertTrue(spec.uses_llm)
        self.assertTrue(spec.external_network)
        self.assertFalse(spec.writes_state)
        self.assertFalse(spec.requires_confirmation)
        self.assertFalse(spec.allowed_proactive)
        self.assertEqual(tool_broker.POLICY_VERSION, "task-tools/v3")
        self.assertEqual(tool_broker.IMPLEMENTATION_VERSION, "tasking/v3")
        self.assertIsNone(tool_broker.get_spec("fleet.execute"))
        self.assertIsNone(tool_broker.get_spec("shell"))

    def test_policy_drift_blocks_task_before_admission(self):
        task = self.task()
        with mock.patch("skill_manifest.TASK_TOOLS", {**__import__("skill_manifest").TASK_TOOLS,
                "fleet.investigate": {"risk": "external_write"}}), mock.patch.object(llm, "transport_chat") as send:
            outcome = self.begin(task)
        self.assertEqual(outcome["status"], "blocked")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 0)
        send.assert_not_called()

    def test_each_actual_fallback_is_counted_and_reconciled_by_original_usage_id(self):
        task = self.task(calls=1, cost=.01)
        profiles = llm.profiles(self.cfg)
        profiles["task_planner"]["fallbacks"] = ["deepseek-4-pro"]
        attempts = []
        def transport(prepared):
            attempts.append(prepared["model"])
            if len(attempts) == 1:
                raise HTTPError("https://example.invalid", 400, "synthetic rejection", {}, None)
            return self.transport(prepared)
        with mock.patch.object(llm, "profiles", return_value=profiles), mock.patch.object(llm, "transport_chat", side_effect=transport):
            self.begin(task)
            finished = self.complete(task)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(finished["model_calls"], 4)
        self.assertEqual(len(self.payload()["task_usage"]), 3)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM llm_usage WHERE kind='rejected_chat'").fetchone()[0], 1)
        self.assertAlmostEqual(finished["task_cost_usd"], .01 + self.conn.execute("SELECT sum(cost_usd) FROM llm_usage").fetchone()[0])
        before = finished["task_cost_usd"]
        for _ in range(5):
            task_runner.poll_fleet_results(self.agent, self.conn)
        self.assertEqual(store.assistant_task_get(self.conn, task["id"])["task_cost_usd"], before)
        self.assertEqual(self.delivery.call_count, 1)

    def test_task_budget_can_stop_synthesis_while_delivering_existing_grounded_evidence(self):
        task = self.task(calls=self.cfg.task_model_call_limit - 1)
        with mock.patch.object(llm, "transport_chat", side_effect=self.transport) as send:
            self.begin(task)
            finished = self.complete(task)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(finished["model_calls"], self.cfg.task_model_call_limit)
        self.assertIn("CPU 12%", self.delivery.call_args.args[2]["text"])
        self.assertEqual(len(self.receipts(task)), 1)
        self.assertEqual(self.sent, [])

    def test_task_reservation_and_fleet_dispatch_binding_commit_before_transport(self):
        task = self.task(calls=1, cost=.01)
        original = investigation.dispatch_bound
        inspected = []
        def check(agent, row, payload, receipt):
            original(agent, row, payload, receipt)
            current = store.assistant_task_get(self.conn, task["id"])
            durable = json.loads(self.conn.execute("SELECT payload FROM fleet_requests WHERE id=?", (row["id"],)).fetchone()[0])
            self.assertIn(str(receipt["reservation_id"]), durable["task_usage"])
            self.assertEqual(durable["inference"]["attempts"][-1]["reservation_id"], receipt["reservation_id"])
            self.assertFalse(self.conn.in_transaction)
            self.assertEqual(current["model_calls"], 2 + len(inspected))
            inspected.append(receipt["reservation_id"])
        with mock.patch.object(investigation, "dispatch_bound", side_effect=check), mock.patch.object(llm, "transport_chat", side_effect=self.transport):
            self.begin(task)
            self.complete(task)
        self.assertEqual(len(inspected), 2)

    def test_source_edit_after_completed_fleet_read_refuses_task_answer_receipt(self):
        task = self.task()
        with mock.patch.object(llm, "transport_chat", side_effect=self.transport):
            self.begin(task)
            self.drain()
            with mock.patch("worker_client.poll", return_value=async_tests.response()):
                self.drain("done")
        self.conn.execute("UPDATE conversation SET text='Changed after the read' WHERE update_id=1")
        self.conn.commit()
        task_runner.poll_fleet_results(self.agent, self.conn)
        self.assertEqual(self.receipts(task), [])
        self.assertEqual(store.assistant_task_get(self.conn, task["id"])["status"], "blocked")
        self.assertNotIn("CPU 12%", self.delivery.call_args.args[2]["text"])

    def test_missing_row_reference_tampering_blocks_instead_of_creating_gap_receipt(self):
        task = self.task()
        with mock.patch.object(investigation, "start", side_effect=ConnectionError("before intent")):
            self.begin(task)
        self.conn.execute("UPDATE assistant_task_steps SET worker_job_id=? WHERE id=?", ("f" * 32, self.step(task)["id"]))
        self.conn.commit()
        task_runner.poll_fleet_results(self.agent, self.conn)
        self.assertEqual(self.receipts(task), [])
        self.assertEqual(store.assistant_task_get(self.conn, task["id"])["status"], "blocked")

    def test_purge_hold_refuses_new_wait_admission(self):
        task = self.task()
        with mock.patch.object(fleet_lifecycle, "admission_paused", return_value=True), mock.patch.object(llm, "transport_chat") as send:
            self.begin(task)
        self.assertIsNone(self.step(task)["worker_job_id"])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 0)
        send.assert_not_called()

    def test_receipt_transaction_failure_and_explicit_resume_reconcile_without_paid_replay(self):
        task = self.task()
        with mock.patch.object(llm, "transport_chat", side_effect=self.transport) as send:
            self.begin(task)
            self.drain()
            with mock.patch("worker_client.poll", return_value=async_tests.response()):
                self.drain("done")
            original = store.task_receipt_create
            def interrupted(*args, **kwargs):
                original(*args, **kwargs)
                raise RuntimeError("synthetic interruption before receipt commit")
            with mock.patch.object(store, "task_receipt_create", side_effect=interrupted):
                task_runner.poll_fleet_results(self.agent, self.conn)
            self.assertEqual(self.receipts(task), [])
            self.assertTrue(store.assistant_task_resume(self.conn, task["id"], 111))
            with mock.patch.object(investigation, "start", side_effect=AssertionError("resume cannot start another investigation")):
                self.begin(store.assistant_task_get(self.conn, task["id"]))
                task_runner.poll_fleet_results(self.agent, self.conn)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(len(self.receipts(task)), 1)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 1)
        self.assertEqual(store.assistant_task_get(self.conn, task["id"])["status"], "completed")
        # One blocked-task outcome plus one explicitly resumed completion, both
        # through the established task delivery machinery; fleet sends none.
        self.assertEqual(self.delivery.call_count, 2)
        self.assertEqual(self.sent, [])

    def test_ambiguous_task_delivery_never_repeats_investigation_or_final_send(self):
        task = self.task()
        self.delivery.return_value = None
        with mock.patch.object(llm, "transport_chat", side_effect=self.transport) as send:
            self.begin(task)
            finished = self.complete(task)
            for _ in range(3):
                task_runner.poll_fleet_results(self.agent, self.conn)
                self.agent.retry_task_deliveries()
                task_runner.run_task(self.agent, self.conn, task["id"])
        self.assertEqual(send.call_count, 2)
        self.assertEqual(finished["delivery_status"], "ambiguous")
        self.assertEqual(self.delivery.call_count, 1)
        self.assertEqual(len(self.receipts(task)), 1)
        self.assertEqual(self.sent, [])

    def test_plan_combines_fleet_clauses_under_existing_one_source_request_contract(self):
        text = "Inspect both fixture projects."
        candidate = raw_plan(text)
        extra = copy.deepcopy(candidate["steps"][0])
        extra["key"] = "second"
        candidate["steps"].append(extra)
        with self.assertRaisesRegex(tasking.PlanError, "one fleet.investigate"):
            tasking.validate_plan(candidate, text)
        self.assertEqual(len(tasking.validate_plan(raw_plan(text), text)["steps"]), 1)

    def test_receipt_result_is_built_after_the_transaction_boundary_from_current_row(self):
        task = self.task()
        with mock.patch.object(llm, "transport_chat", side_effect=self.transport):
            self.begin(task)
            self.drain()
            with mock.patch("worker_client.poll", return_value=async_tests.response()):
                self.drain("done")
        original = investigation.task_outcome
        def check(agent, task_row, step, row, payload):
            self.assertTrue(self.conn.in_transaction)
            fresh = self.conn.execute("SELECT payload FROM fleet_requests WHERE id=?", (row["id"],)).fetchone()[0]
            self.assertEqual(json.loads(fresh), payload)
            return original(agent, task_row, step, row, payload)
        with mock.patch.object(investigation, "task_outcome", side_effect=check) as outcome:
            task_runner.poll_fleet_results(self.agent, self.conn)
        self.assertEqual(outcome.call_count, 1)
        self.assertEqual(len(self.receipts(task)), 1)

    def test_existing_independent_reminder_card_does_not_cancel_admitted_read_or_deliver_early(self):
        due = "2099-01-01T09:00:00+00:00"
        text = "Check the fixture memory now; remind me to call Alice at " + due
        self.source(1, text)
        candidate = raw_plan(text)
        digest = tasking.source_hash(text)
        candidate["steps"].append({"key": "reminder", "tool": "reminder.propose",
            "input": {"title": "call Alice", "due_utc": due, "recurrence": "none"},
            "bindings": {
                "title": {"source": "boss_span", "start": text.index("call Alice"), "end": text.index("call Alice") + len("call Alice"), "source_hash": digest, "transform": "literal"},
                "due_utc": {"source": "boss_span", "start": text.index(due), "end": len(text), "source_hash": digest, "transform": "reminder_due"}},
            "depends_on": [], "purpose": "Preserve the separately confirmed reminder"})
        task, _ = store.assistant_task_create(self.conn, 111, 1, candidate)
        with mock.patch.object(llm, "transport_chat", side_effect=self.transport) as send:
            self.assertEqual(self.begin(task)["status"], "waiting_fleet")
            self.assertEqual(self.begin(task)["status"], "waiting_approval")
            approval = self.conn.execute("SELECT * FROM task_approvals WHERE task_id=?", (task["id"],)).fetchone()
            original = dict(approval)
            self.drain()
            with mock.patch("worker_client.poll", return_value=async_tests.response()):
                self.drain("done")
            task_runner.poll_fleet_results(self.agent, self.conn)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(store.assistant_task_get(self.conn, task["id"])["status"], "waiting_approval")
        self.assertEqual(self.step(task)["status"], "succeeded")
        self.assertEqual(len(self.receipts(task)), 1)
        self.assertEqual(dict(self.conn.execute("SELECT * FROM task_approvals WHERE id=?", (approval["id"],)).fetchone()), original)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM reminders").fetchone()[0], 0)
        self.assertEqual(len(self.sent), 1)  # Only the unchanged reminder card.
        self.assertIn("call Alice", self.sent[0][1])
        self.assertIn("ta|", str(self.sent[0][2]["reply_markup"]))
        self.delivery.assert_not_called()

    def restore_snapshot(self):
        snapshot = sqlite3.connect(self.root / "restored-task.db")
        try:
            self.conn.backup(snapshot)
        finally:
            snapshot.close()
        self.conn.close()
        self.conn = store.open_db(self.root / "restored-task.db")
        self.agent.conn = self.conn

    def test_checked_sqlite_backup_restores_queued_wait_and_completes_original_reference(self):
        task = self.task(calls=1, cost=.01)
        self.begin(task)
        before = self.payload()
        identity = self.step(task)["worker_job_id"]
        self.restore_snapshot()
        self.assertEqual(self.conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(self.payload(), before)
        self.assertEqual(self.step(task)["status"], "waiting_fleet")
        store.assistant_task_reclaim_stale(self.conn)
        with mock.patch.object(investigation, "start", side_effect=AssertionError("restore must reuse the durable reference")), mock.patch.object(llm, "transport_chat", side_effect=self.transport) as send:
            finished = self.complete(task)
        self.assertEqual(finished["status"], "completed")
        self.assertEqual(send.call_count, 2)
        self.assertEqual(self.step(task)["worker_job_id"], identity)
        self.assertEqual(self.step(task)["attempts"], 1)
        self.assertEqual(len(self.receipts(task)), 1)
        self.assertEqual(self.delivery.call_count, 1)
        self.assertEqual(self.sent, [])

    def test_checked_sqlite_backup_restores_unknown_started_usage_without_transport_replay(self):
        task = self.task(calls=1, cost=.01)
        entered, release = threading.Event(), threading.Event()
        self.release_events.append(release)
        def held(prepared):
            entered.set()
            release.wait(3)
            return self.transport(prepared)
        with mock.patch.object(llm, "transport_chat", side_effect=held) as send:
            self.begin(task)
            self.agent.check_fleet_requests()
            self.assertTrue(entered.wait(1))
            self.assertEqual(self.row()["state"], "planning_started")
            reserved = store.assistant_task_get(self.conn, task["id"])["task_cost_usd"]
            self.restore_snapshot()
            release.set()
            self.wait_futures()
            # A process crash loses in-memory handles. The backup contains the
            # committed started boundary and conservative original reservation.
            self.jobs.extend(self.agent._fleet_inference_calls.values())
            self.agent._fleet_inference_calls.clear()
        self.assertEqual(send.call_count, 1)
        self.assertEqual(self.conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        store.assistant_task_reclaim_stale(self.conn)
        with mock.patch.object(llm, "transport_chat", side_effect=AssertionError("unknown paid work cannot be replayed")) as resend:
            self.agent.check_fleet_requests()
            task_runner.poll_fleet_results(self.agent, self.conn)
        resend.assert_not_called()
        finished = store.assistant_task_get(self.conn, task["id"])
        self.assertEqual(finished["status"], "completed")
        self.assertEqual(finished["model_calls"], 2)
        self.assertEqual(finished["task_cost_usd"], reserved)
        self.assertEqual(self.step(task)["attempts"], 1)
        self.assertEqual(len(self.receipts(task)), 1)
        self.assertIn("unknown_outcome", self.receipts(task)[0]["data_json"])
        self.assertEqual(self.sent, [])

    def safe_fixture(self, task):
        identity = self.step(task)["worker_job_id"]
        value = {"version": investigation.TASK_VERSION, "request_id": identity,
            "source_update": 1, "state": "done", "observed_at": None,
            "pages": ["Fixture host: CPU 12%."], "gap": ""}
        return {"schema": "fleet.investigate/v1", "value": {"investigation": value}}, investigation.task_evidence(identity)

    def test_safe_receipt_preserves_only_exact_code_owned_reference_and_evidence(self):
        task = self.task()
        self.begin(task)
        data, evidence = self.safe_fixture(task)
        safe, support = investigation.safe_task_receipt(data, evidence, request_id=self.step(task)["worker_job_id"])
        self.assertEqual(safe, data)
        self.assertEqual(support, evidence)
        with self.assertRaises(investigation.AdmissionError):
            investigation.safe_task_receipt(data, evidence, request_id="f" * 32)
        changed = copy.deepcopy(evidence)
        changed[0]["source"] = "fleet:" + "e" * 32
        with self.assertRaises(investigation.AdmissionError):
            investigation.safe_task_receipt(data, changed, request_id=self.step(task)["worker_job_id"])

    def test_safe_receipt_rejects_secret_shaped_pages_labels_and_extra_hashes(self):
        task = self.task()
        self.begin(task)
        for target in ("pages", "label", "extra"):
            with self.subTest(target=target):
                data, evidence = self.safe_fixture(task)
                if target == "pages":
                    data["value"]["investigation"]["pages"] = ["sk-" + "S" * 30]
                elif target == "label":
                    evidence[0]["label"] = "AKIA" + "Z" * 16
                else:
                    data["value"]["investigation"]["arbitrary_hash"] = "a" * 64
                step = self.step(task)
                with self.assertRaises(ValueError):
                    store.task_receipt_create(self.conn, step, input_hash=step["worker_input_hash"], status="ok",
                        summary="Synthetic unsafe receipt", data=data, evidence=evidence)
                self.assertEqual(self.receipts(task), [])

    def test_worker_spool_reconciliation_excludes_fleet_references(self):
        task = self.task()
        self.begin(task)
        with mock.patch("worker_client.reconcile", return_value={}) as reconcile:
            task_runner.reconcile_worker_spool(self.agent, self.conn)
        self.assertEqual(reconcile.call_args.args[1], set())
        self.assertEqual(reconcile.call_args.args[2], set())


if __name__ == "__main__":
    unittest.main()
