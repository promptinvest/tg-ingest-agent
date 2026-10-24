"""Retained owner-loop start foundation; real async paths, synthetic I/O only."""
import json
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import fleet
import fleet_facts
import fleet_investigation as investigation
import fleet_protocol as protocol
import fleet_queue
import llm
import store
import test_fleet_async as async_tests
import tool_broker
import worker_client
from test_fleet_facts import fixture


class RetainedInvestigation(unittest.TestCase):
    setUp = async_tests.FleetAsyncExecution.setUp
    tearDown = async_tests.FleetAsyncExecution.tearDown
    reply = async_tests.FleetAsyncExecution.reply
    source = async_tests.FleetAsyncExecution.source
    row = async_tests.FleetAsyncExecution.row
    payload = async_tests.FleetAsyncExecution.payload
    wait_futures = async_tests.FleetAsyncExecution.wait_futures
    drain = async_tests.FleetAsyncExecution.drain
    transport = staticmethod(async_tests.FleetAsyncExecution.transport)

    def admit(self, update=1, text="Check the fixture memory now", caller="caller-a", **options):
        if store.telegram_update_get(self.conn, update) is None:
            self.source(update, text)
        return investigation.start(self.agent, chat_id=111, source_update=update, caller_key=caller, **options)

    def reference(self, caller="caller-a", update=1):
        return dict(chat_id=111, source_update=update, caller_key=caller)

    def test_intent_caller_source_policy_and_phase_are_durable_before_any_send(self):
        with mock.patch.object(llm, "transport_chat") as send:
            handle = self.admit()
        send.assert_not_called()
        self.assertEqual(handle["state"], "planning_queued")
        payload = self.payload()
        waiter = payload["external_waiter"]
        self.assertEqual(waiter["version"], investigation.VERSION)
        self.assertEqual(waiter["principal"], "owner")
        self.assertEqual(waiter["request_id"], handle["request_id"])
        self.assertEqual(waiter["source_hash"], payload["inference"]["binding"]["source_hash"])
        self.assertEqual(waiter["delivery_mode"], "retained")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM llm_usage").fetchone()[0], 0)
        self.assertEqual(len(self.sent), 0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM telegram_updates").fetchone()[0], 1)

    def test_lost_start_response_reconciles_same_handle_without_new_phase_or_calls(self):
        original = self.agent._fleet_begin_async
        def lost(*args, **kwargs):
            original(*args, **kwargs)
            raise ConnectionError("synthetic lost response after durable admission")
        with mock.patch.object(self.agent, "_fleet_begin_async", side_effect=lost), self.assertRaises(ConnectionError):
            self.admit()
        before = self.payload()
        handle = self.admit()
        self.assertEqual(handle["request_id"], self.row()["id"])
        self.assertEqual(self.payload(), before)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 1)
        self.assertEqual(len(self.sent), 0)

    def test_conflicting_input_or_caller_cannot_repurpose_same_source(self):
        original = self.admit()
        for options in ({"lang": "ru"}, {"caller": "caller-b"}, {"source_span": [0, 5]}):
            with self.subTest(options=options), self.assertRaises(investigation.Conflict):
                self.admit(**options)
        self.assertEqual(self.admit()["request_id"], original["request_id"])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 1)

    def test_same_caller_key_on_a_different_owner_source_conflicts(self):
        original = self.admit()
        with self.assertRaises(investigation.Conflict):
            self.admit(2, "Check the fixture CPU now")
        self.assertEqual(self.row()["id"], original["request_id"])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 1)

    def test_existing_ordinary_request_is_never_repurposed(self):
        update = self.source()
        self.agent.do_fleet(111, "en", update["message"]["text"])
        before = self.payload()
        with self.assertRaises(investigation.Conflict):
            self.admit()
        self.assertEqual(self.payload(), before)
        self.assertNotIn("external_waiter", before)
        self.assertEqual(len(self.sent), 1)  # Original ordinary progress reply remains.

    def test_real_async_inspection_and_synthesis_retain_one_answer_with_zero_telegram(self):
        with mock.patch.object(llm, "transport_chat", side_effect=self.transport) as send:
            initial = self.admit()
            self.drain()
            with mock.patch.object(worker_client, "poll", return_value=async_tests.response()):
                self.drain("done")
        result = investigation.result(self.agent, **self.reference())
        self.assertEqual(result["request_id"], initial["request_id"])
        self.assertEqual(send.call_count, 2)
        self.assertEqual(result["model_calls"], 2)
        self.assertIn("CPU 12%", "\n".join(result["pages"]))
        self.assertEqual(self.payload()["retained_delivery"]["telegram_attempts"], 0)
        self.assertNotIn("result_message", self.payload())
        self.assertEqual(len(self.sent), 0)
        before = self.payload()
        self.assertEqual(investigation.result(self.agent, **self.reference()), result)
        self.assertEqual(self.admit()["request_id"], initial["request_id"])
        self.assertEqual(self.payload(), before)

    def test_catalog_and_clarification_results_are_retained_without_progress_or_messages(self):
        document = fixture()
        plan = dict(kind="catalog", subjects=["host-a"], fields=["public_ipv4"], scope="explicit", presentation="list", detail="concise", clarification="")
        with mock.patch.object(fleet_facts, "load", return_value=document), \
             mock.patch.object(llm, "transport_chat", return_value=async_tests.completion(plan)), \
             mock.patch.object(fleet, "submit", side_effect=AssertionError("catalog must not inspect")):
            self.admit(text="Give the recorded address of Example application host.")
            self.drain("done")
        result = investigation.result(self.agent, **self.reference())
        self.assertIn("192.0.2.10", "\n".join(result["pages"]))
        self.assertEqual(len(self.sent), 0)

    def test_model_unavailable_known_address_uses_same_grounded_fallback_without_telegram(self):
        document = fixture()
        with mock.patch.object(fleet_facts, "load", return_value=document), \
             mock.patch.object(llm, "transport_chat", side_effect=TimeoutError("synthetic unknown transport")) as send, \
             mock.patch.object(fleet, "submit", side_effect=AssertionError("known address needs no probe")):
            self.admit(text="Give the recorded address of Example application host.")
            self.drain("done")
        result = investigation.result(self.agent, **self.reference())
        self.assertEqual(send.call_count, 1)
        self.assertIn("192.0.2.10", "\n".join(result["pages"]))
        self.assertEqual(len(self.sent), 0)

    def test_scope_denial_is_not_repaired_or_bypassed_by_known_facts_fallback(self):
        class ScopeDenied(ValueError):
            pass
        document = fixture()
        plan = dict(kind="catalog", subjects=["host-a"], fields=["public_ipv4"], scope="explicit", presentation="list", detail="concise", clarification="")
        with mock.patch.object(fleet_facts, "load", return_value=document), \
             mock.patch.object(llm, "transport_chat", return_value=async_tests.completion(plan)) as send, \
             mock.patch.object(self.agent, "_fleet_validate_planning", side_effect=ScopeDenied("caller scope denied")):
            self.admit(text="Give the recorded address of Example application host.")
            self.drain("failed")
        self.assertEqual(send.call_count, 1)
        self.assertIsNone(investigation.result(self.agent, **self.reference()))
        self.assertNotIn("answer_pages", self.payload())
        self.assertEqual(len(self.sent), 0)

    def test_purge_between_handle_and_result_read_cannot_recreate_content(self):
        with mock.patch.object(llm, "transport_chat", side_effect=self.transport):
            self.admit()
            self.drain()
            with mock.patch.object(worker_client, "poll", return_value=async_tests.response()):
                self.drain("done")
        original = investigation.status
        def deleted(*args, **kwargs):
            handle = original(*args, **kwargs)
            self.conn.execute("DELETE FROM fleet_events")
            self.conn.execute("DELETE FROM fleet_requests")
            self.conn.commit()
            return handle
        with mock.patch.object(investigation, "status", side_effect=deleted):
            self.assertIsNone(investigation.result(self.agent, **self.reference()))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 0)
        self.assertEqual(len(self.sent), 0)

    def test_model_restart_cannot_produce_effect_card_or_executor_submission(self):
        plan = dict(hosts=["host-a"], facets=["services"], operation="restart", service="fixture-worker.service", clarification="")
        with mock.patch.object(llm, "transport_chat", return_value=async_tests.completion(plan)), \
             mock.patch.object(fleet, "submit", side_effect=AssertionError("retained read cannot restart")), \
             mock.patch.object(self.agent, "_fleet_approval", side_effect=AssertionError("retained read cannot request a card")):
            self.admit(text="Check the configured service state.")
            self.drain("failed")
        self.assertEqual(len(self.sent), 0)
        self.assertNotIn("approval", self.payload())

    def test_unknown_paid_planning_outcome_is_not_replayed_or_announced(self):
        with mock.patch.object(llm, "transport_chat", side_effect=TimeoutError("synthetic unknown transport")) as send:
            initial = self.admit()
            self.drain("failed")
            again = self.admit()
        self.assertEqual(send.call_count, 1)
        self.assertEqual(initial["request_id"], again["request_id"])
        self.assertIsNone(investigation.result(self.agent, **self.reference()))
        self.assertEqual(len(self.sent), 0)
        self.assertEqual(self.conn.execute("SELECT kind FROM llm_usage").fetchone()[0], "unknown_chat")

    def test_source_edit_before_dispatch_cancels_authority_without_model_or_delivery(self):
        self.admit()
        self.conn.execute("UPDATE conversation SET text='Changed owner request' WHERE update_id=1")
        self.conn.commit()
        with mock.patch.object(llm, "transport_chat") as send:
            self.agent.check_fleet_requests()
        send.assert_not_called()
        self.assertEqual(self.row()["state"], "failed")
        with self.assertRaises(investigation.AdmissionError):
            self.admit()
        self.assertEqual(len(self.sent), 0)

    def test_real_source_span_binds_full_owner_hash_without_creating_owner_updates(self):
        text = "Read notes; Check the fixture memory now"
        start = text.index("Check")
        with mock.patch.object(llm, "transport_chat", side_effect=self.transport):
            self.admit(text=text, source_span=[start, len(text)])
            self.drain()
        payload = self.payload()
        self.assertEqual(payload["question"], text[start:])
        self.assertEqual(payload["source_hash"], fleet.tasking.source_hash(text))
        self.assertEqual(store.assistant_task_source(self.conn, 111, 1)["text"], text)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM telegram_updates").fetchone()[0], 1)
        self.assertEqual(len(self.sent), 0)

    def test_unqualified_principal_missing_source_and_foreign_chat_refuse_before_admission(self):
        self.source()
        for options in ({"principal": "model"}, {"chat_id": 222}, {"source_update": 99}):
            args = {**self.reference(), **options}
            with self.subTest(options=options), self.assertRaises(investigation.AdmissionError):
                investigation.start(self.agent, **args)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 0)

    def test_revoked_owner_or_disabled_feature_cannot_start_the_next_phase(self):
        self.admit()
        self.cfg.allowed_chat_ids = {222}
        with mock.patch.object(llm, "transport_chat") as send:
            self.agent.check_fleet_requests()
        send.assert_not_called()
        self.assertEqual(self.row()["state"], "failed")
        self.assertEqual(len(self.sent), 0)

    def test_source_admission_window_does_not_expire_reconciliation_of_existing_handle(self):
        initial = self.admit()
        old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        self.conn.execute("UPDATE telegram_updates SET received_at=? WHERE update_id=1", (old,))
        self.conn.commit()
        self.assertEqual(self.admit()["request_id"], initial["request_id"])
        self.source(2, "Check another current fixture")
        self.conn.execute("UPDATE telegram_updates SET received_at=? WHERE update_id=2", (old,))
        self.conn.commit()
        with self.assertRaises(investigation.AdmissionError):
            self.admit(2, caller="other")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 1)

    def test_existing_maximum_eight_pending_admissions_is_not_increased(self):
        for index in range(8):
            self.conn.execute("INSERT INTO fleet_requests VALUES(?,?,?,?,?,?,?)", (str(index), 111, 20+index, "queued", "{}", protocol.now(), protocol.now()))
        self.conn.commit()
        with self.assertRaises(investigation.AdmissionError):
            self.admit()
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 8)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM llm_usage").fetchone()[0], 0)

    def test_unsent_cancel_is_confirmed_and_retry_never_restarts(self):
        initial = self.admit()
        cancelled = investigation.cancel(self.agent, **self.reference())
        self.assertEqual(cancelled["state"], "cancelled")
        with mock.patch.object(llm, "transport_chat") as send:
            self.agent.check_fleet_requests()
            retry = self.admit()
        send.assert_not_called()
        self.assertEqual(retry["request_id"], initial["request_id"])
        self.assertEqual(retry["state"], "cancelled")
        self.assertEqual(len(self.sent), 0)

    def test_inflight_cancel_remains_requested_settles_original_usage_and_discards_content(self):
        entered, release = threading.Event(), threading.Event()
        self.release_events.append(release)
        def blocked(prepared):
            entered.set()
            if not release.wait(3):
                raise AssertionError("fixture model not released")
            return self.transport(prepared)
        with mock.patch.object(llm, "transport_chat", side_effect=blocked) as send:
            self.admit()
            self.agent.check_fleet_requests()
            self.assertTrue(entered.wait(2))
            cancelled = investigation.cancel(self.agent, **self.reference())
            self.assertEqual(cancelled["cancellation"], "requested")
            self.assertNotEqual(cancelled["state"], "cancelled")
            release.set()
            self.wait_futures()
            for _ in range(3):
                self.agent.check_fleet_requests()
        self.assertEqual(send.call_count, 1)
        self.assertEqual(self.row()["state"], "failed")
        self.assertIsNone(investigation.result(self.agent, **self.reference()))
        self.assertEqual(self.payload()["cost"], self.conn.execute("SELECT SUM(cost_usd) FROM llm_usage").fetchone()[0])
        self.assertEqual(len(self.sent), 0)

    def test_executor_cancel_marker_is_bound_but_does_not_claim_confirmed_cancellation(self):
        fleet_queue.publish_ready(self.root / "spool")
        with mock.patch.object(llm, "transport_chat", side_effect=self.transport):
            self.admit()
            self.drain()
        binding = self.payload()["binding"]
        handle = investigation.cancel(self.agent, **self.reference())
        marker = self.root / "spool/requests" / (".cancel-" + binding["job_id"] + ".json")
        self.assertTrue(marker.exists())
        self.assertEqual(json.loads(marker.read_text())["nonce"], binding["nonce"])
        self.assertEqual(handle["cancellation"], "requested")
        self.assertNotEqual(handle["state"], "cancelled")
        with mock.patch.object(worker_client, "poll", side_effect=AssertionError("cancelled request cannot accept a result")):
            self.agent.check_fleet_requests()
        self.assertEqual(self.row()["state"], "failed")
        self.assertEqual(len(self.sent), 0)

    def test_same_queue_deadline_guard_records_failure_without_retained_mode_notice(self):
        fleet_queue.publish_ready(self.root / "spool")
        with mock.patch.object(llm, "transport_chat", side_effect=self.transport):
            self.admit()
            self.drain()
        future = datetime.now(timezone.utc) + timedelta(seconds=250)
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return future.astimezone(tz) if tz else future.replace(tzinfo=None)
        with mock.patch.object(protocol, "datetime", Clock), mock.patch.object(worker_client, "poll", return_value=None):
            self.agent.check_fleet_requests()
        self.assertEqual(self.row()["state"], "failed")
        self.assertEqual(self.payload()["executor_status"]["state"], "queue_timeout")
        self.assertEqual(len(self.sent), 0)

    def test_purge_holds_admission_and_reads_and_deleted_handle_cannot_recreate_content(self):
        self.admit()
        self.conn.execute("INSERT INTO kv(key,value) VALUES('task_purge_authorization',?)", (protocol.canonical({"phase": "prepared", "nonce": "e" * 32}),))
        self.conn.commit()
        self.assertIsNone(investigation.status(self.agent, **self.reference()))
        self.assertIsNone(investigation.result(self.agent, **self.reference()))
        self.source(2, "Check another fixture")
        with self.assertRaises(investigation.AdmissionError):
            self.admit(2, caller="other")
        self.conn.execute("DELETE FROM fleet_events")
        self.conn.execute("DELETE FROM fleet_requests")
        self.conn.commit()
        self.assertIsNone(investigation.result(self.agent, **self.reference()))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 0)
        self.assertEqual(len(self.sent), 0)

    def test_started_phase_lost_after_restart_never_repeats_paid_planning(self):
        with mock.patch.object(llm, "transport_chat", side_effect=self.transport) as send:
            self.admit()
            self.agent.check_fleet_requests()
            self.wait_futures()
            self.jobs.extend(self.agent._fleet_inference_calls.values())
            self.agent._fleet_inference_calls = {}
            self.drain("failed")
            retry = self.admit()
        self.assertEqual(send.call_count, 1)
        self.assertEqual(retry["state"], "failed")
        self.assertTrue(self.payload()["inference"]["usage_unknown"])
        self.assertEqual(len(self.sent), 0)

    def test_crash_between_intent_and_phase_keeps_handle_and_cannot_admit_a_second_job(self):
        with mock.patch.object(self.agent, "_fleet_queue_phase", side_effect=ConnectionError("synthetic interrupted admission")), self.assertRaises(ConnectionError):
            self.admit()
        first = self.row()["id"]
        retry = self.admit()
        self.assertEqual(retry["request_id"], first)
        with mock.patch.object(llm, "transport_chat") as send:
            self.agent.check_fleet_requests()
        send.assert_not_called()
        self.assertEqual(self.row()["state"], "failed")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 1)
        self.assertEqual(len(self.sent), 0)

    def test_only_qualified_read_task_start_is_advertised(self):
        spec = tool_broker.get_spec("fleet.investigate")
        self.assertEqual(spec.risk, "network_read")
        self.assertTrue(spec.uses_llm)
        self.assertFalse(spec.writes_state)
        self.assertFalse(spec.requires_confirmation)
        self.assertIsNone(tool_broker.get_spec("fleet.execute"))
        self.assertIsNone(tool_broker.get_spec("investigation_start"))
        self.assertIsNotNone(tool_broker.get_spec("fleet.status"))


if __name__ == "__main__":
    unittest.main()
