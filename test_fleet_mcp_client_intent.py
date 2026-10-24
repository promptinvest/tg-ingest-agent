"""Durable client intent uses the actual fleet ledger, never an outbox table."""
import copy
import gzip
import json
import sqlite3
import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import backup
import fleet
import fleet_investigation
import fleet_lifecycle as lifecycle
import fleet_lifecycle_svc
import fleet_mcp as mcp
import fleet_mcp_admission as admission
import fleet_mcp_client as client
import fleet_protocol as protocol
import fleet_shared_reads
import llm
import store
import test_fleet_mcp as mcp_tests
import test_fleet_mcp_admission as admission_tests


class DurableMCPClientIntent(unittest.TestCase):
    setUp_base = admission_tests.MCPReadAdmission.setUp_base
    tearDown_base = admission_tests.MCPReadAdmission.tearDown_base
    reply = admission_tests.MCPReadAdmission.reply
    source = admission_tests.MCPReadAdmission.source
    row = admission_tests.MCPReadAdmission.row
    payload = admission_tests.MCPReadAdmission.payload
    wait_futures = admission_tests.MCPReadAdmission.wait_futures
    transport = staticmethod(admission_tests.MCPReadAdmission.transport)

    def setUp(self):
        admission_tests.MCPReadAdmission.setUp(self)
        self.cfg.fleet_mcp_enabled = True
        self.cfg.fleet_mcp_config = str(self.root / "protected-client-policy.json")
        self.document = mcp_tests.fixture()
        self.config = {**mcp_tests.configured(self.document, ["host-a", "project-alpha"]), **self.policy}
        self.server = mcp_tests.adapter(self.document, self.config["allowed_subjects"],
            principal=self.config["principal"], investigations=self.config["investigations"])
        self.config_patch = mock.patch.object(mcp, "read_config", side_effect=lambda _path: copy.deepcopy(self.config))
        self.config_patch.start()
        self.source()

    def tearDown(self):
        self.config_patch.stop()
        admission_tests.MCPReadAdmission.tearDown(self)

    def exchange(self, messages):
        return mcp_tests.exchange(self.server, messages)

    def prepare(self, update=1, **style):
        return admission.prepare_start(self.conn, self.context, {"source_update": update, **style})

    def reference(self):
        return {"request_id": self.row()["id"]}

    def restore_checked_snapshot(self):
        archive = backup.snapshot(self.cfg, self.conn)
        restored_path = self.root / "checked-client-restore.db"
        with gzip.open(archive, "rb") as source:
            restored_path.write_bytes(source.read())
        self.assertGreater(backup.integrity_check(restored_path), 0)
        restored = sqlite3.connect(restored_path)
        try:
            restored.backup(self.conn)
        finally:
            restored.close()

    def test_prepare_is_inert_durable_and_model_free(self):
        with mock.patch.object(llm, "transport_chat") as paid, mock.patch.object(fleet, "submit") as probes:
            prepared = self.prepare(presentation="table")
            self.agent.check_fleet_requests()
        self.assertEqual(prepared["handle"]["state"], admission.PREPARED)
        self.assertEqual(self.row()["state"], admission.PREPARED)
        self.assertEqual(self.payload()["client_intent"]["idempotency_key"], prepared["arguments"]["idempotency_key"])
        self.assertNotIn("inference", self.payload())
        self.assertEqual(self.payload()["cost"], 0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_events").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM llm_usage").fetchone()[0], 0)
        paid.assert_not_called(); probes.assert_not_called(); self.assertEqual(self.sent, [])

    def test_real_client_commits_intent_before_first_exchange_and_server_promotes_same_row(self):
        seen = []
        def exchange(messages):
            separate = sqlite3.connect(self.root / "agent.db")
            separate.row_factory = sqlite3.Row
            try:
                row = separate.execute("SELECT * FROM fleet_requests").fetchone()
                seen.append(row["id"])
                self.assertEqual(row["state"], admission.PREPARED)
                self.assertEqual(json.loads(row["payload"])["client_intent"]["idempotency_key"], messages[-1]["params"]["arguments"]["idempotency_key"])
            finally:
                separate.close()
            return self.exchange(messages)
        with mock.patch.object(llm, "transport_chat") as paid:
            result = client.start_investigation(self.cfg, 1, presentation="table", exchange=exchange)
        self.assertEqual(result["path"], "mcp")
        self.assertEqual(result["result"]["request_id"], seen[0])
        self.assertEqual(self.row()["state"], admission.STATE)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_events").fetchone()[0], 1)
        paid.assert_not_called(); self.assertEqual(self.sent, [])

    def test_offline_restart_recovers_key_and_format_without_regeneration(self):
        observed = []
        def offline(messages):
            observed.append(messages[-1]["params"]["arguments"])
            raise OSError("synthetic unavailable stdio")
        first = client.start_investigation(self.cfg, 1, presentation="table", detail="detailed", exchange=offline)
        saved = self.payload()
        with mock.patch.object(admission.secrets, "token_hex", side_effect=AssertionError("restart must recover key")):
            second = client.start_investigation(self.cfg, 1, exchange=offline)
        self.assertEqual(first["status"], "reconcile_required")
        self.assertEqual(first["request_id"], second["request_id"])
        self.assertEqual(observed[0], observed[1])
        self.assertEqual(observed[1]["presentation"], "table")
        self.assertEqual(self.payload(), saved)
        self.assertEqual(self.row()["state"], admission.PREPARED)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_events").fetchone()[0], 1)

    def test_lost_server_response_reconnect_does_not_duplicate_admission_or_model(self):
        def lost(messages):
            self.exchange(messages)
            raise OSError("synthetic lost response after promotion")
        with mock.patch.object(llm, "transport_chat") as paid:
            first = client.start_investigation(self.cfg, 1, exchange=lost)
            second = client.start_investigation(self.cfg, 1, exchange=self.exchange)
        self.assertEqual(first["status"], "reconcile_required")
        self.assertEqual(first["request_id"], second["result"]["request_id"])
        self.assertEqual(second["result"]["state"], admission.STATE)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_events").fetchone()[0], 1)
        paid.assert_not_called(); self.assertEqual(self.sent, [])

    def test_only_server_promotion_allows_original_owner_phase(self):
        prepared = self.prepare()
        with mock.patch.object(llm, "transport_chat", side_effect=self.transport) as paid:
            self.agent.check_fleet_requests()
            paid.assert_not_called()
            started = admission.start(self.conn, self.context, prepared["arguments"])
            self.assertEqual(started["request_id"], prepared["handle"]["request_id"])
            self.agent.check_fleet_requests(); self.wait_futures()
        self.assertEqual(paid.call_count, 1)
        self.assertNotEqual(self.row()["state"], admission.PREPARED)
        self.assertEqual(self.sent, [])

    def test_expired_prepared_slot_is_fenced_without_dispatch_and_capacity_is_released(self):
        self.prepare()
        for index in range(2, 9):
            self.source(index, "Synthetic request " + str(index)); self.prepare(index)
        self.source(9, "Ninth synthetic request")
        with self.assertRaises(admission.AdmissionError):
            self.prepare(9)
        old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        self.conn.execute("UPDATE telegram_updates SET received_at=? WHERE update_id=1", (old,)); self.conn.commit()
        with mock.patch.object(llm, "transport_chat") as paid:
            self.agent.check_fleet_requests()
        self.assertEqual(self.row()["state"], "failed")
        self.assertEqual(self.prepare(9)["handle"]["state"], admission.PREPARED)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_requests WHERE state=?", (admission.PREPARED,)).fetchone()[0], 8)
        paid.assert_not_called(); self.assertEqual(self.sent, [])

    def test_conflicting_key_format_source_or_policy_cannot_repurpose_intent(self):
        original = self.prepare(presentation="table")
        for args in ({"idempotency_key": "another"}, {"presentation": "names"}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.prepare(**args)
        self.conn.execute("UPDATE conversation SET text='Edited canonical question' WHERE update_id=1"); self.conn.commit()
        with self.assertRaises(admission.ScopeDenied):
            self.prepare()
        with self.assertRaises(admission.ScopeDenied):
            admission.start(self.conn, self.context, original["arguments"])
        self.assertEqual(self.row()["state"], admission.PREPARED)
        self.assertNotIn("inference", self.payload())

    def test_policy_change_fences_prepared_intent_before_exchange_or_phase(self):
        self.prepare()
        self.config["investigations"]["allowed_hosts"] = []
        with mock.patch.object(llm, "transport_chat") as paid, mock.patch.object(client, "stdio_exchange") as exchange:
            with self.assertRaises(mcp.ScopeDenied):
                client.start_investigation(self.cfg, 1)
            self.agent.check_fleet_requests()
        self.assertEqual(self.row()["state"], "failed")
        paid.assert_not_called(); exchange.assert_not_called()

    def test_prepared_cancel_settles_unsent_work_and_never_promotes_after_reconnect(self):
        prepared = self.prepare()
        self.conn.execute("UPDATE conversation SET text='Edited after prepare' WHERE update_id=1"); self.conn.commit()
        cancelled = admission.cancel(self.conn, self.context, self.reference())
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertEqual(cancelled["cancellation"], "settled")
        self.assertEqual(admission.cancellation_status(self.conn, self.context, self.reference())["cancellation"], "settled")
        self.assertNotIn("scope", cancelled)
        self.assertNotIn("answer_pages", cancelled)
        with self.assertRaises(admission.ScopeDenied):
            admission.start(self.conn, self.context, prepared["arguments"])
        with mock.patch.object(llm, "transport_chat") as paid:
            self.agent.check_fleet_requests()
        paid.assert_not_called(); self.assertEqual(self.sent, [])

    def test_cancelled_prepared_same_source_reconciles_without_promotion(self):
        prepared = self.prepare()
        admission.cancel(self.conn, self.context, self.reference())
        before = self.payload()
        self.assertEqual(admission.start(self.conn, self.context, prepared["arguments"])["state"], "cancelled")
        self.assertEqual(self.payload(), before)

    def test_purge_hold_blocks_preparation_cancel_and_exchange_without_content_changes(self):
        self.prepare(); before = self.payload()
        self.conn.execute("INSERT INTO kv(key,value) VALUES('task_purge_authorization',?)", (protocol.canonical({"phase": "prepared", "nonce": "a"*32}),)); self.conn.commit()
        with mock.patch.object(client, "stdio_exchange") as exchange, mock.patch.object(llm, "transport_chat") as paid:
            result = client.start_investigation(self.cfg, 1)
            self.assertEqual(result["result"]["reason"], "purge_in_progress")
            self.assertEqual(admission.cancel(self.conn, self.context, self.reference())["reason"], "purge_in_progress")
            self.agent.check_fleet_requests()
        self.assertEqual(self.payload(), before)
        exchange.assert_not_called(); paid.assert_not_called()

    def test_prepared_content_is_counted_and_deleted_by_existing_quiesced_purge(self):
        self.prepare(); nonce = "a" * 32
        counts = lifecycle.db_preview(self.conn)
        self.assertEqual(counts, {"fleet_requests": 1, "fleet_events": 1})
        self.conn.execute("INSERT INTO kv(key,value) VALUES('task_purge_authorization',?)", (protocol.canonical({"phase": "prepared", "nonce": nonce}),)); self.conn.commit()
        executor = {"version": "cara-fleet-queue/v1", "action": "purge_content", "nonce": nonce,
            "status": "quiesced", "implementation_hash": "b" * 64, "pid": 123}
        lifecycle.record_quiescence(self.conn, nonce, executor, {"status": "settled", "pending_calls": 0}, counts)
        self.conn.execute("BEGIN IMMEDIATE")
        self.assertEqual(lifecycle.delete_db_content(self.conn, nonce), counts); self.conn.commit()
        self.assertEqual(lifecycle.db_preview(self.conn), {})
        self.assertEqual(admission.status(self.conn, self.context, self.reference() if self.row() else {"request_id": "f"*32})["reason"], "purge_in_progress")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_events").fetchone()[0], 0)

    def test_checked_existing_backup_restore_preserves_prepared_key_without_new_store(self):
        prepared = self.prepare(presentation="table")
        before_tables = self.conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        self.restore_checked_snapshot()
        recovered = self.prepare()
        self.assertEqual(prepared, recovered)
        self.assertEqual(before_tables, self.conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall())
        self.assertEqual(self.row()["state"], admission.PREPARED)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_events").fetchone()[0], 1)

    def test_restricted_channel_allows_only_fixed_promotion_and_unsent_cancel_boundaries(self):
        connection = admission.connect(self.context, write=True)
        try:
            prepared = admission.prepare_start(connection, self.context, {"source_update": 1})
            self.assertEqual(admission.start(connection, self.context, prepared["arguments"])["state"], admission.STATE)
            with self.assertRaises(sqlite3.DatabaseError):
                connection.execute("UPDATE fleet_requests SET state='executing'")
            with self.assertRaises(sqlite3.DatabaseError):
                connection.execute("UPDATE fleet_requests SET state='admission_queued',payload=?,updated_at=? WHERE id=? AND state='admission_prepared'", ("{}", protocol.now(), prepared["handle"]["request_id"]))
        finally:
            connection.close()

    def test_two_clients_generate_only_one_committed_key_for_same_source(self):
        barrier = threading.Barrier(2); results, errors = [], []
        def prepare():
            connection = None
            try:
                connection = admission.connect(self.context, write=True); barrier.wait(2)
                results.append(admission.prepare_start(connection, self.context, {"source_update": 1}))
            except BaseException as exc:
                errors.append(exc)
            finally:
                if connection is not None: connection.close()
        workers = [threading.Thread(target=prepare) for _ in range(2)]
        for worker in workers: worker.start()
        for worker in workers: worker.join(3)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_events").fetchone()[0], 1)

    def test_prepared_state_joins_capacity_lifecycle_and_reference_selectors_only(self):
        for states in (fleet.PENDING_STATES, fleet_investigation.ACTIVE, fleet_shared_reads.STATES,
                       fleet_lifecycle_svc._ACTIVE, mcp.REFERENCE_STATES):
            self.assertIn(admission.PREPARED, states)
        self.assertNotIn(admission.PREPARED, __import__("fleet_async").STATES)


if __name__ == "__main__":
    unittest.main()
