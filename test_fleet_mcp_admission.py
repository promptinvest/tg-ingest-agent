"""Durable source-bound read admission; synthetic I/O, real SQLite/owner phases."""
import copy
import json
import sqlite3
import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import fleet
import fleet_facts
import fleet_investigation
import fleet_lifecycle
import fleet_mcp_admission as admission
import fleet_protocol as protocol
import llm
import store
import test_fleet_async as async_tests
from test_fleet_facts import fixture


def config(path):
    return {"principal": "fixture-client", "allowed_subjects": ["host-a", "project-alpha"],
        "investigations": {"version": admission.VERSION, "capability_hash": admission.contract_hash(), "enabled": True,
            "database_path": str(path), "owner_chat_id": 111, "allowed_hosts": ["host-a"],
            "allowed_projects": ["project-alpha"], "allowed_capabilities": list(protocol.PROJECT_CAPABILITIES),
            "allowed_facets": sorted(protocol.FACETS)}}


class MCPReadAdmission(unittest.TestCase):
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
        self.policy = config(self.root / "agent.db")
        self.context = admission.context(self.policy)
        self.context_patch = mock.patch.object(admission, "load_context", side_effect=lambda cfg: admission.context(self.policy))
        self.context_patch.start()

    def tearDown(self):
        self.context_patch.stop()
        self.tearDown_base()

    def start(self, text="Check the fixture memory now", update=1, key="fixture-request-1", **style):
        if store.telegram_update_get(self.conn, update) is None:
            self.source(update, text)
        return admission.start(self.conn, self.context, {"source_update": update, "idempotency_key": key, **style})

    def reference(self):
        return {"request_id": self.row()["id"]}

    def scoped_result(self):
        data = async_tests.response()
        selected = self.payload()["operation"]["read_scope"]
        data["read_scope"] = selected
        for raw in data["hosts"].values():
            raw.pop("projects", None)
            raw["read_scope"] = selected
        return data

    def test_admission_is_durable_before_any_agent_model_probe_or_telegram(self):
        with mock.patch.object(llm, "transport_chat") as send, mock.patch.object(fleet, "submit") as probe, mock.patch.object(self.agent, "_fleet_begin_async", side_effect=AssertionError("adapter cannot invoke Agent")):
            handle = self.start()
        self.assertEqual(handle["version"], admission.VERSION)
        self.assertEqual(handle["state"], admission.STATE)
        self.assertNotIn("inference", self.payload())
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_events").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM llm_usage").fetchone()[0], 0)
        send.assert_not_called(); probe.assert_not_called()
        self.assertEqual(self.sent, [])

    def test_same_start_key_reconciles_identical_handle_without_new_row_event_or_phase(self):
        first = self.start()
        before = self.payload()
        again = self.start()
        self.assertEqual(first, again)
        self.assertEqual(self.payload(), before)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_events").fetchone()[0], 1)
        self.assertEqual(admission.classify(self.conn, self.context, first["request_id"]), "admitted")

    def test_source_or_format_or_caller_conflict_cannot_repurpose_request(self):
        self.start()
        for args in ({"key": "another"}, {"lang": "ru"}, {"presentation": "table"}, {"update": 2}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.start(**args)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 1)

    def test_other_principal_cannot_reconcile_read_or_cancel_admitted_handle(self):
        self.start()
        other = config(self.root / "agent.db")
        other["principal"] = "another-client"
        ctx = admission.context(other)
        for method in (admission.status, admission.result, admission.cancel):
            with self.subTest(method=method.__name__), self.assertRaises(admission.ScopeDenied):
                method(self.conn, ctx, self.reference())
        with self.assertRaises(admission.ScopeDenied):
            admission.classify(self.conn, ctx, self.reference()["request_id"])
        self.assertNotIn("retained_cancel", self.payload())

    def test_ordinary_existing_request_cannot_be_repurposed_by_interface(self):
        update = self.source()
        self.agent.do_fleet(111, "en", update["message"]["text"])
        original = self.payload()
        with self.assertRaises(admission.Conflict):
            self.start()
        self.assertEqual(self.payload(), original)
        self.assertEqual(admission.classify(self.conn, self.context, self.row()["id"]), "legacy")

    def test_no_question_path_owner_target_or_command_argument_is_accepted(self):
        self.source()
        for field, value in {"question": "Different question", "hosts": ["host-a"], "owner_chat_id": 111,
                             "database_path": "/tmp/other.db", "command": "id", "source_span": [1, 4]}.items():
            with self.subTest(field=field), self.assertRaises(admission.AdmissionError):
                admission.start(self.conn, self.context, {"source_update": 1, "idempotency_key": "fixed", field: value})
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 0)

    def test_real_owner_source_required_wrong_update_owner_and_forwarded_data_denied(self):
        self.source()
        self.conn.execute("UPDATE telegram_updates SET chat_id=222 WHERE update_id=1")
        self.conn.commit()
        with self.assertRaises(admission.ScopeDenied):
            self.start()
        self.conn.execute("UPDATE telegram_updates SET chat_id=111 WHERE update_id=1")
        self.conn.execute("UPDATE conversation SET source='forward' WHERE update_id=1")
        self.conn.commit()
        with self.assertRaises(admission.ScopeDenied):
            self.start()

    def test_fresh_source_age_is_bounded_existing_handle_reconciles_without_fresh_admission(self):
        first = self.start()
        old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        self.conn.execute("UPDATE telegram_updates SET received_at=? WHERE update_id=1", (old,))
        self.conn.commit()
        self.assertEqual(self.start()["request_id"], first["request_id"])
        self.source(2, "Another fixture check")
        self.conn.execute("UPDATE telegram_updates SET received_at=? WHERE update_id=2", (old,))
        self.conn.commit()
        with self.assertRaises(admission.ScopeDenied):
            self.start(update=2, key="different-key")

    def test_global_existing_capacity_is_unchanged_and_counts_admission_queue(self):
        for index in range(1, 9):
            self.start(update=index, key="key-" + str(index))
        with self.assertRaises(admission.AdmissionError):
            self.start(update=9, key="key-9")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 8)

    def test_owner_loop_binds_original_phase_once_and_host_read_excludes_projects(self):
        first = self.start(presentation="table", detail="detailed")
        with mock.patch.object(llm, "transport_chat", side_effect=self.transport) as send:
            self.drain()
            operation = self.payload()["operation"]
            self.assertEqual(operation["read_scope"], {"version": protocol.READ_VERSION, "kind": "host", "projects": [], "capabilities": []})
            self.assertEqual(operation["facets"], ["resources"])
            with mock.patch("worker_client.poll", return_value=self.scoped_result()):
                self.drain("done")
        answer = admission.result(self.conn, self.context, self.reference())
        self.assertEqual(first["request_id"], answer["request_id"])
        self.assertEqual(send.call_count, 2)
        self.assertEqual(self.payload()["plan"]["presentation"], "table")
        self.assertIn("CPU 12%", "\n".join(answer["answer_pages"]))
        self.assertEqual(self.sent, [])

    def test_catalog_result_is_actual_answer_with_requested_format_and_no_host_probe(self):
        plan = dict(kind="catalog", subjects=["host-a"], fields=["public_ipv4"], scope="explicit", presentation="list", detail="concise", clarification="")
        with mock.patch.object(fleet_facts, "load", return_value=fixture()), mock.patch.object(llm, "transport_chat", return_value=async_tests.completion(plan)) as send, mock.patch.object(fleet, "submit", side_effect=AssertionError("known facts cannot inspect")):
            self.start(text="Give the recorded address of Example application host.", presentation="table")
            self.drain("done")
        answer = admission.result(self.conn, self.context, self.reference())
        self.assertIn("192.0.2.10", "\n".join(answer["answer_pages"]))
        self.assertEqual(self.payload()["plan"]["presentation"], "table")
        self.assertEqual(send.call_count, 1)
        self.assertEqual(self.sent, [])

    def test_disallowed_runtime_target_is_denied_before_any_host_collection_without_repair(self):
        self.policy["investigations"]["allowed_hosts"] = []
        self.context = admission.context(self.policy)
        with mock.patch.object(llm, "transport_chat", side_effect=self.transport) as send, mock.patch.object(fleet, "submit") as probe:
            self.start()
            self.drain("failed")
        self.assertEqual(send.call_count, 1)
        probe.assert_not_called()
        self.assertEqual(self.sent, [])

    def test_disallowed_catalog_subject_never_uses_local_fallback_to_bypass_scope(self):
        self.policy["allowed_subjects"] = ["project-alpha"]
        self.context = admission.context(self.policy)
        plan = dict(kind="catalog", subjects=["host-a"], fields=["public_ipv4"], scope="explicit", presentation="list", detail="concise", clarification="")
        with mock.patch.object(fleet_facts, "load", return_value=fixture()), mock.patch.object(llm, "transport_chat", return_value=async_tests.completion(plan)) as send, mock.patch.object(fleet, "submit") as probe:
            self.start(text="Give the recorded address of Example application host.")
            self.drain("failed")
        self.assertNotIn("answer_pages", self.payload())
        self.assertEqual(send.call_count, 1)
        probe.assert_not_called()

    def test_maintenance_model_output_creates_no_card_or_executor_job(self):
        plan = dict(async_tests.plan(), operation="restart", service="fixture-worker.service")
        with mock.patch.object(llm, "transport_chat", return_value=async_tests.completion(plan)), mock.patch.object(fleet, "submit") as probe:
            self.start()
            self.drain("failed")
        probe.assert_not_called()
        self.assertNotIn("approval", self.payload())
        self.assertEqual(self.sent, [])

    def test_current_protected_policy_change_fences_queued_work_before_paid_transport(self):
        self.start()
        self.policy["investigations"]["allowed_facets"] = ["projects"]
        with mock.patch.object(llm, "transport_chat") as send:
            self.agent.check_fleet_requests()
        self.assertEqual(self.row()["state"], "failed")
        send.assert_not_called()

    def test_source_edit_fences_admission_result_and_all_phase_consumption(self):
        self.start()
        self.conn.execute("UPDATE conversation SET text='Edited owner source' WHERE update_id=1")
        self.conn.commit()
        with mock.patch.object(llm, "transport_chat") as send:
            self.agent.check_fleet_requests()
        self.assertEqual(self.row()["state"], "failed")
        send.assert_not_called()
        with self.assertRaises(admission.ScopeDenied):
            admission.result(self.conn, self.context, self.reference())

    def test_unsent_cancel_is_requested_until_owner_loop_confirms_no_dispatch(self):
        self.start()
        first = admission.cancel(self.conn, self.context, self.reference())
        before = self.conn.execute("SELECT count(*) FROM fleet_events").fetchone()[0]
        again = admission.cancel(self.conn, self.context, self.reference())
        self.assertEqual(first, again)
        self.assertEqual(first["state"], admission.STATE)
        self.assertEqual(first["cancellation"], "requested")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_events").fetchone()[0], before)
        with mock.patch.object(llm, "transport_chat") as send:
            self.agent.check_fleet_requests()
        self.assertEqual(self.row()["state"], "cancelled")
        send.assert_not_called()
        self.assertEqual(self.sent, [])

    def test_inflight_cancel_remains_honest_and_settles_original_usage_without_replay(self):
        entered, release = threading.Event(), threading.Event()
        self.release_events.append(release)
        def held(prepared):
            entered.set(); release.wait(3)
            return self.transport(prepared)
        with mock.patch.object(llm, "transport_chat", side_effect=held) as send:
            self.start()
            self.agent.check_fleet_requests()
            self.assertTrue(entered.wait(1))
            handle = admission.cancel(self.conn, self.context, self.reference())
            self.assertEqual(handle["cancellation"], "requested")
            self.assertNotEqual(handle["state"], "cancelled")
            release.set(); self.wait_futures()
            self.agent.check_fleet_requests(); self.agent.check_fleet_requests()
        self.assertEqual(send.call_count, 1)
        self.assertEqual(len(self.conn.execute("SELECT * FROM llm_usage").fetchall()), 1)
        self.assertEqual(admission.result(self.conn, self.context, self.reference())["answer_pages"], [])
        self.assertEqual(self.sent, [])

    def test_started_phase_lost_after_process_restart_is_unknown_and_never_resent(self):
        self.start()
        with mock.patch.object(llm, "transport_chat", side_effect=self.transport):
            self.agent.check_fleet_requests()
            self.wait_futures()
        self.jobs.extend(self.agent._fleet_inference_calls.values())
        self.agent._fleet_inference_calls.clear()
        with mock.patch.object(llm, "transport_chat") as resend:
            self.agent.check_fleet_requests()
        resend.assert_not_called()
        self.assertEqual(self.row()["state"], "failed")
        answer = admission.result(self.conn, self.context, self.reference())
        self.assertEqual(answer["gap"], "unknown_outcome")

    def test_purge_holds_new_admission_status_result_cancel_and_owner_loop(self):
        self.start()
        before = self.payload()
        with mock.patch.object(fleet_lifecycle, "admission_paused", return_value=True), mock.patch.object(llm, "transport_chat") as send:
            self.assertEqual(admission.status(self.conn, self.context, self.reference())["reason"], "purge_in_progress")
            self.assertEqual(admission.result(self.conn, self.context, self.reference())["reason"], "purge_in_progress")
            self.assertEqual(admission.cancel(self.conn, self.context, self.reference())["reason"], "purge_in_progress")
            self.assertEqual(self.start()["reason"], "purge_in_progress")
            self.agent.check_fleet_requests()
        self.assertEqual(self.payload(), before)
        send.assert_not_called()

    def test_explicit_qualified_policy_required_allowlist_order_is_semantically_stable(self):
        ordered = copy.deepcopy(self.policy)
        for key in ("allowed_hosts", "allowed_projects", "allowed_capabilities", "allowed_facets"):
            ordered["investigations"][key].reverse()
        ordered["allowed_subjects"].reverse()
        self.assertEqual(admission.context(ordered), self.context)
        for change in ({"enabled": "true"}, {"version": "unknown/v1"}, {"capability_hash": "f" * 64}, {"allowed_facets": ["shell"]}):
            with self.subTest(change=change), self.assertRaises(admission.AdmissionError):
                admission.policy({**self.policy["investigations"], **change})

    def test_fixed_database_channel_cannot_modify_source_spend_schema_or_existing_execution_state(self):
        self.source()
        connection = admission.connect(self.context, write=True)
        try:
            self.assertEqual(admission.start(connection, self.context, {"source_update": 1, "idempotency_key": "channel"})["state"], admission.STATE)
            for sql in ("DELETE FROM fleet_requests", "UPDATE fleet_requests SET state='executing'", "UPDATE conversation SET text='injected'", "SELECT * FROM llm_usage", "CREATE TABLE arbitrary (x)", "ATTACH DATABASE ':memory:' AS other"):
                with self.subTest(sql=sql), self.assertRaises(sqlite3.DatabaseError):
                    connection.execute(sql)
        finally:
            connection.close()
        self.assertEqual(self.row()["state"], admission.STATE)

    def test_readonly_channel_and_disabled_policy_never_gain_write_admission(self):
        self.source()
        connection = admission.connect(self.context)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                admission.start(connection, self.context, {"source_update": 1, "idempotency_key": "readonly"})
        finally:
            connection.close()
        disabled = copy.deepcopy(self.policy)
        disabled["investigations"]["enabled"] = False
        with self.assertRaises(admission.ScopeDenied):
            admission.connect(admission.context(disabled), write=True)

    def test_two_connections_reconcile_one_idempotent_row_without_any_model(self):
        self.source()
        barrier = threading.Barrier(2)
        results, errors = [], []
        def run():
            try:
                connection = admission.connect(self.context, write=True)
                try:
                    barrier.wait(2)
                    results.append(admission.start(connection, self.context, {"source_update": 1, "idempotency_key": "same-key"}))
                finally:
                    connection.close()
            except BaseException as exc:
                errors.append(exc)
        workers = [threading.Thread(target=run) for _ in range(2)]
        with mock.patch.object(llm, "transport_chat") as send:
            for worker in workers: worker.start()
            for worker in workers: worker.join(3)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["request_id"], results[1]["request_id"])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_events").fetchone()[0], 1)
        send.assert_not_called()

    def test_source_is_rechecked_inside_durable_admission_transaction(self):
        self.source()
        original = admission._source
        reads = []
        def edit(connection, value, update):
            result = original(connection, value, update)
            if not reads:
                connection.execute("UPDATE conversation SET text='Changed before intent commit' WHERE update_id=1")
                connection.commit()
            reads.append(update)
            return result
        with mock.patch.object(admission, "_source", side_effect=edit), self.assertRaises(admission.ScopeDenied):
            self.start()
        self.assertEqual(len(reads), 2)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 0)

    def test_unknown_source_and_missing_handle_never_create_synthetic_owner_data(self):
        with self.assertRaises(admission.ScopeDenied):
            admission.start(self.conn, self.context, {"source_update": 88, "idempotency_key": "missing"})
        identity = "f" * 32
        self.assertEqual(admission.classify(self.conn, self.context, identity), "missing")
        for method in (admission.status, admission.result, admission.cancel):
            self.assertEqual(method(self.conn, self.context, {"request_id": identity})["reason"], "not_found_or_not_authorized")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM telegram_updates").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM conversation").fetchone()[0], 0)

    def test_expired_queued_admission_and_revoked_owner_never_dispatch_a_model(self):
        self.start()
        self.conn.execute("UPDATE telegram_updates SET received_at=? WHERE update_id=1", ((datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(),))
        self.conn.commit()
        with mock.patch.object(llm, "transport_chat") as send:
            self.agent.check_fleet_requests()
        self.assertEqual(self.row()["state"], "failed")
        send.assert_not_called()

    def test_completed_answer_scope_reports_selected_data_not_broader_policy_allowlist(self):
        self.policy["investigations"]["allowed_hosts"] = ["host-a", "host-b"]
        self.context = admission.context(self.policy)
        with mock.patch.object(llm, "transport_chat", side_effect=self.transport):
            self.start()
            self.drain()
            with mock.patch("worker_client.poll", return_value=self.scoped_result()):
                self.drain("done")
        answer = admission.result(self.conn, self.context, self.reference())
        self.assertEqual(answer["scope"]["hosts"], ["host-a"])
        self.assertEqual(answer["scope"]["projects"], [])
        self.assertEqual(answer["scope"]["facets"], ["resources"])
        self.assertNotIn("host-b", answer["scope"]["hosts"])

    def test_cancel_after_source_edit_still_only_stops_own_existing_read(self):
        self.start()
        self.conn.execute("UPDATE conversation SET text='Edited after admission' WHERE update_id=1")
        self.conn.commit()
        self.assertEqual(admission.cancel(self.conn, self.context, self.reference())["cancellation"], "requested")
        with mock.patch.object(llm, "transport_chat") as send:
            self.agent.check_fleet_requests()
        send.assert_not_called()
        self.assertEqual(self.sent, [])

    def test_suppressed_result_checks_current_scope_independently_of_original_execution(self):
        self.start()
        payload = self.payload()
        payload["plan"] = {"operation": "inspect", "hosts": ["host-b"], "projects": [], "facets": ["resources"],
            "read_scope": {"version": protocol.READ_VERSION, "kind": "host", "projects": [], "capabilities": []}}
        payload["answer_pages"] = ["Out-of-scope synthetic data"]
        payload["retained_delivery"] = {"state": "available"}
        self.conn.execute("UPDATE fleet_requests SET state='done',payload=? WHERE id=?", (protocol.canonical(payload), self.row()["id"]))
        self.conn.commit()
        with self.assertRaises(admission.ScopeDenied):
            admission.result(self.conn, self.context, self.reference())

    def test_readonly_cancellation_reconciliation_after_source_edit_contains_no_old_content(self):
        self.start()
        self.conn.execute("UPDATE conversation SET text='Edited source' WHERE update_id=1")
        self.conn.commit()
        cancelled = admission.cancel(self.conn, self.context, self.reference())
        before = self.conn.execute("SELECT count(*) FROM fleet_events").fetchone()[0]
        connection = admission.connect(self.context)
        try:
            status = admission.cancellation_status(connection, self.context, self.reference())
        finally:
            connection.close()
        self.assertEqual(status, cancelled)
        self.assertEqual(status["cancellation"], "requested")
        self.assertNotIn("scope", status)
        self.assertNotIn("answer_pages", status)
        self.assertNotIn("question", status)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_events").fetchone()[0], before)

    def test_owner_is_rechecked_by_main_before_its_first_paid_phase(self):
        self.start()
        self.cfg.allowed_chat_ids = set()
        with mock.patch.object(llm, "transport_chat") as send:
            self.agent.check_fleet_requests()
        send.assert_not_called()
        self.assertEqual(self.row()["state"], "failed")
        self.assertEqual(self.sent, [])

    def test_additional_read_cannot_broaden_policy_or_reintroduce_legacy_unscoped_collection(self):
        self.start()
        row, payload = self.row(), self.payload()
        bad = {"operation": "inspect", "hosts": ["host-a"], "facets": ["resources"], "inventory_hash": "f" * 64}
        with self.assertRaises(admission.ScopeDenied):
            admission.validate_operation(self.agent, row, payload, bad)
        bad["read_scope"] = {"version": protocol.READ_VERSION, "kind": "host", "projects": [], "capabilities": []}
        bad["hosts"] = ["host-b"]
        with self.assertRaises(admission.ScopeDenied):
            admission.validate_operation(self.agent, row, payload, bad)

    def test_plan_normalization_keeps_selected_projects_and_only_approved_capabilities(self):
        self.start()
        selected = {"version": protocol.READ_VERSION, "kind": "projects", "projects": ["project-alpha"], "capabilities": ["release"]}
        plan = {"operation": "inspect", "hosts": ["host-a"], "projects": ["project-alpha"], "facets": ["projects"], "read_scope": selected, "clarification": ""}
        normalized = admission.validate_plan(self.agent, self.row(), self.payload(), plan)
        self.assertEqual(normalized["read_scope"], selected)
        self.assertEqual(normalized["facets"], ["projects"])
        self.policy["investigations"]["allowed_capabilities"] = []
        with self.assertRaises(admission.ScopeDenied):
            admission.validate_plan(self.agent, self.row(), self.payload(), plan)


if __name__ == "__main__":
    unittest.main()
