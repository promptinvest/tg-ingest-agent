"""Existing-ledger shared task reads: synthetic SQLite, no model/probe/message."""
import copy
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import fleet_protocol as protocol
import fleet_shared_reads as reads
import skill_manifest
import store
import task_runner
import tasking
import tool_broker
from test_task_runtime import StubAgent


def raw_plan(text, tool="fleet.status", **options):
    return {"objective": "Read the existing owner investigation", "deliverable": "answer", "steps": [{
        "key": "existing", "tool": tool, "input": {"query": text, **options},
        "bindings": {"query": {"source": "boss_span", "start": 0, "end": len(text),
            "source_hash": tasking.source_hash(text), "transform": "literal"}},
        "depends_on": [], "purpose": "Read the existing source-bound record"}]}


class SharedFleetReads(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = store.open_db(Path(self.temp.name) / "shared.db")
        self.conn.executescript(protocol.SCHEMA)
        self.agent = StubAgent(self.conn, self.temp.name)
        self.agent.cfg.allowed_chat_ids = {1}
        self.saved_at = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        self.request_id = "a" * 32
        self.original = "Check Project Alpha health now."
        self.source(101, self.original)
        self.document = {"question": self.original, "source_hash": tasking.source_hash(self.original),
            "lang": "en", "model_calls": 2, "cost": 0.01,
            "plan": {"operation": "inspect", "hosts": ["host-a"], "projects": ["project-alpha"], "facets": ["projects"]},
            "result": {"observed_at": self.saved_at},
            "answer_pages": ["Project Alpha: application health failure, HTTP 503. Project Beta was not inspected."]}
        self.save("done")

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def source(self, update_id, text, *, chat=1, kind="boss"):
        update = {"update_id": update_id, "message": {"message_id": update_id + 100, "chat": {"id": chat}, "from": {"id": chat}, "text": text}}
        store.telegram_update_receive(self.conn, update, chat_id=chat)
        store.convo_add(self.conn, chat, "user", text, source=kind, update_id=update_id, tg_message_id=update_id + 100)

    def save(self, state, document=None, created=None, updated=None):
        if document is not None:
            self.document = document
        self.conn.execute("INSERT INTO fleet_requests VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state,payload=excluded.payload,created_at=excluded.created_at,updated_at=excluded.updated_at",
            (self.request_id, 1, 101, state, protocol.canonical(self.document), created or self.saved_at, updated or self.saved_at))
        self.conn.commit()

    def task(self, tool="fleet.status", text="What is the status of that investigation?", **options):
        self.source(202, text)
        task, _ = store.assistant_task_create(self.conn, 1, 202, raw_plan(text, tool, **options))
        return task, store.assistant_task_steps(self.conn, task["id"])[0]

    def run_read(self, tool="fleet.status", text="What is the status of that investigation?", **options):
        task, step = self.task(tool, text, **options)
        before = self.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0]
        with mock.patch("llm.chat_profile", side_effect=AssertionError("retained read cannot use a model")), \
             mock.patch("socket.create_connection", side_effect=AssertionError("retained read cannot probe")), \
             mock.patch("worker_client.submit", side_effect=AssertionError("retained read cannot submit work")):
            outcome = task_runner.run_task(self.agent, self.conn, task["id"])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], before)
        receipt = self.conn.execute("SELECT * FROM tool_receipts WHERE task_id=?", (task["id"],)).fetchone()
        return task, step, outcome, receipt

    def test_registry_closed_read_policy_preserves_versions_and_excludes_effects(self):
        tool_broker.assert_registry()
        for tool in reads.TOOLS:
            spec = tool_broker.get_spec(tool)
            self.assertEqual(spec.risk, "read_only")
            self.assertEqual(spec.bound_inputs, frozenset({"query"}))
            self.assertEqual(spec.output_schema, tool + "/v1")
            for flag in ("uses_llm", "external_network", "writes_state", "requires_confirmation", "destructive"):
                self.assertFalse(getattr(spec, flag))
        start = tool_broker.get_spec("fleet.investigate")
        self.assertEqual(start.risk, "network_read")
        self.assertTrue(start.uses_llm)
        self.assertFalse(start.writes_state)
        self.assertFalse(start.requires_confirmation)
        for tool in ("fleet.execute", "shell"):
            self.assertIsNone(tool_broker.get_spec(tool))
        self.assertEqual(tool_broker.POLICY_VERSION, "task-tools/v3")
        self.assertEqual(tool_broker.IMPLEMENTATION_VERSION, "tasking/v3")

    def test_real_status_task_reads_current_queue_and_deadlines_without_pumping_or_new_work(self):
        self.document["executor_lifecycle"] = {"submitted_at": self.saved_at, "queue_expires_at": (datetime.now(timezone.utc) + timedelta(seconds=180)).isoformat()}
        self.save("queued")
        task, step, outcome, receipt = self.run_read()
        self.assertEqual(outcome["status"], "completed")
        value = json.loads(receipt["data_json"])["value"]["reference"]
        self.assertEqual(value["state"], "queued")
        self.assertFalse(value["result_available"])
        self.assertEqual(value["deadlines"], self.document["executor_lifecycle"])
        self.assertIn("waiting for an executor", self.agent.completed[0][1])
        self.assertEqual(store.assistant_task_get(self.conn, task["id"])["model_calls"], 0)
        self.assertEqual(json.loads(self.conn.execute("SELECT payload FROM fleet_requests").fetchone()[0]), self.document)
        self.assertIsNone(receipt["effect_id"])
        self.assertNotIn("What is the status", step["input_json"])

    def test_real_result_answers_with_meaningful_name_and_original_measurement_dates(self):
        task, _, outcome, receipt = self.run_read("fleet.result", "Show the result of that investigation.")
        self.assertEqual(outcome["status"], "completed")
        value = json.loads(receipt["data_json"])["value"]["reference"]
        self.assertEqual(value["source_update"], 101)
        self.assertEqual(value["observed_at"], self.saved_at)
        self.assertEqual(value["updated_at"], self.saved_at)
        self.assertTrue(value["result_available"])
        answer = self.agent.completed[0][1]
        for expected in ("Project Alpha", "HTTP 503", self.saved_at, "not a fresh check"):
            self.assertIn(expected, answer)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM task_artifacts").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_events").fetchone()[0], 0)
        self.assertEqual(store.assistant_task_get(self.conn, task["id"])["task_cost_usd"], 0)

    def test_missing_result_returns_specific_gap_without_diagnostic_or_retry(self):
        self.save("inspection_started")
        _, _, outcome, receipt = self.run_read("fleet.result", "Show the previous investigation result.")
        self.assertEqual(outcome["status"], "completed")
        value = json.loads(receipt["data_json"])["value"]["reference"]
        self.assertEqual(value["state"], "inspection_started")
        self.assertEqual(value["gap"], "result_not_available")
        self.assertIn("No completed answer", self.agent.completed[0][1])
        self.assertNotIn("CPU", self.agent.completed[0][1])
        self.assertNotIn("HTTP 503", self.agent.completed[0][1])

    def test_intervening_owner_task_prevents_implicit_latest_investigation_scope(self):
        self.source(150, "List my notes instead.")
        _, _, outcome, receipt = self.run_read("fleet.result", "Show that result.")
        self.assertEqual(outcome["status"], "completed")
        value = json.loads(receipt["data_json"])["value"]["reference"]
        self.assertEqual(value["status"], "unavailable")
        self.assertNotIn("HTTP 503", self.agent.completed[0][1])
        self.assertEqual(value["gap"], "not_found_or_not_authorized")

    def test_explicit_source_reference_can_read_archive_without_refreshing_it(self):
        old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        self.document["result"]["observed_at"] = old
        self.save("done", created=old, updated=old)
        self.source(150, "An unrelated owner task.")
        _, _, outcome, receipt = self.run_read("fleet.result", "Show investigation #101 result.", source_update=101)
        self.assertEqual(outcome["status"], "completed")
        value = json.loads(receipt["data_json"])["value"]["reference"]
        self.assertEqual(value["observed_at"], old)
        self.assertIn(old, self.agent.completed[0][1])
        self.assertIn("not a fresh check", self.agent.completed[0][1])

    def test_unmentioned_model_reference_does_not_override_owner_source(self):
        _, _, outcome, receipt = self.run_read("fleet.result", "Show the previous result.", source_update=101)
        self.assertEqual(outcome["status"], "blocked")
        self.assertIsNone(receipt)

    def test_edited_original_request_refuses_stored_answer(self):
        self.conn.execute("UPDATE conversation SET text='Edited source' WHERE update_id=101")
        self.conn.commit()
        _, _, outcome, receipt = self.run_read("fleet.result", "Show result of investigation #101.", source_update=101)
        self.assertEqual(outcome["status"], "completed")
        value = json.loads(receipt["data_json"])["value"]["reference"]
        self.assertEqual(value["gap"], "source_no_longer_current")
        self.assertNotIn("HTTP 503", self.agent.completed[0][1])

    def test_edited_task_source_blocks_before_any_result_receipt(self):
        task, _ = self.task("fleet.result", "Show investigation #101.", source_update=101)
        self.conn.execute("UPDATE conversation SET text='Changed owner request' WHERE update_id=202")
        self.conn.commit()
        with mock.patch.object(reads, "execute", side_effect=AssertionError("stale task cannot read")):
            outcome = task_runner.run_task(self.agent, self.conn, task["id"])
        self.assertEqual(outcome["status"], "blocked")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM tool_receipts").fetchone()[0], 0)

    def test_foreign_principal_and_forwarded_original_never_authorize_read(self):
        task, step = self.task("fleet.result", "Show investigation #101.", source_update=101)
        inputs = task_runner.resolve_inputs(self.conn, task, step)
        self.agent.cfg.allowed_chat_ids = {2}
        with self.assertRaises(reads.ReferenceError):
            reads.execute(self.agent.cfg, self.conn, task, step, inputs)
        self.agent.cfg.allowed_chat_ids = {1}
        self.conn.execute("UPDATE conversation SET source='forward' WHERE update_id=101")
        self.conn.commit()
        value = reads.execute(self.agent.cfg, self.conn, task, step, inputs)[2]["value"]["reference"]
        self.assertEqual(value["gap"], "source_no_longer_current")

    def test_predecessor_receipt_cannot_become_owner_query_authority(self):
        task, step = self.task()
        inputs = task_runner.resolve_inputs(self.conn, task, step)
        forged = dict(step)
        forged["bindings_json"] = json.dumps({"query": {"source": "step_output", "trust": "confirmed_local"}})
        with self.assertRaises(reads.ReferenceError):
            reads.execute(self.agent.cfg, self.conn, task, forged, inputs)

    def test_maintenance_card_never_becomes_result_read_or_confirmation(self):
        self.document["plan"].update(operation="restart", service="fixture.service")
        self.document["approval"] = {"token": "c" * 32, "fingerprint": "d" * 64, "expires": protocol.now()}
        self.document["approval_message"] = 9001
        self.save("approval")
        original = protocol.canonical(self.document)
        _, _, outcome, receipt = self.run_read("fleet.result", "Show investigation #101 result.", source_update=101)
        self.assertEqual(outcome["status"], "completed")
        value = json.loads(receipt["data_json"])["value"]["reference"]
        self.assertEqual(value["gap"], "effects_outside_read_scope")
        self.assertNotIn("token", receipt["data_json"])
        row = self.conn.execute("SELECT state,payload FROM fleet_requests").fetchone()
        self.assertEqual(row[0], "approval")
        self.assertEqual(row[1], original)

    def test_ambiguous_read_keeps_uncertainty_and_never_reexecutes(self):
        self.save("ambiguous")
        _, _, outcome, receipt = self.run_read()
        self.assertEqual(outcome["status"], "completed")
        value = json.loads(receipt["data_json"])["value"]["reference"]
        self.assertEqual(value["state"], "ambiguous")
        self.assertFalse(value["result_available"])
        self.assertIn("outcome uncertain", self.agent.completed[0][1])

    def test_cancelled_record_does_not_deliver_unfinished_answer_or_start_again(self):
        self.save("cancelled")
        _, _, outcome, receipt = self.run_read("fleet.result", "Show investigation #101 result.", source_update=101)
        self.assertEqual(outcome["status"], "completed")
        value = json.loads(receipt["data_json"])["value"]["reference"]
        self.assertEqual(value["state"], "cancelled")
        self.assertEqual(value["gap"], "result_not_available")
        self.assertNotIn("HTTP 503", self.agent.completed[0][1])

    def test_prepared_purge_fences_new_receipts_and_purged_reference_is_not_recreated(self):
        task, step = self.task("fleet.result", "Show investigation #101.", source_update=101)
        inputs = task_runner.resolve_inputs(self.conn, task, step)
        self.conn.execute("INSERT INTO kv(key,value) VALUES('task_purge_authorization',?)", (protocol.canonical({"phase": "prepared", "nonce": "e" * 32}),))
        self.conn.commit()
        with self.assertRaises(reads.ReferenceError):
            reads.execute(self.agent.cfg, self.conn, task, step, inputs)
        row, payload, gap = reads.canonical_reference(self.conn, 1, request_id=self.request_id)
        self.assertEqual((row, payload, gap), (None, None, "purge_in_progress"))
        self.conn.execute("DELETE FROM fleet_requests")
        self.conn.execute("DELETE FROM kv WHERE key='task_purge_authorization'")
        self.conn.commit()
        value = reads.execute(self.agent.cfg, self.conn, task, step, inputs)[2]["value"]["reference"]
        self.assertEqual(value["gap"], "not_found_or_not_authorized")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM tool_receipts").fetchone()[0], 0)

    def test_changed_reference_between_read_and_receipt_is_rejected(self):
        task, _ = self.task("fleet.result", "Show investigation #101.", source_update=101)
        original = reads.execute
        def changed(*args):
            result = original(*args)
            self.conn.execute("UPDATE fleet_requests SET updated_at=?", ((datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(),))
            self.conn.commit()
            return result
        with mock.patch.object(reads, "execute", side_effect=changed):
            outcome = task_runner.run_task(self.agent, self.conn, task["id"])
        self.assertEqual(outcome["status"], "blocked")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM tool_receipts").fetchone()[0], 0)

    def test_task_cancel_during_read_prevents_receipt_and_no_handle_is_dropped(self):
        task, _ = self.task("fleet.result", "Show investigation #101.", source_update=101)
        original = reads.execute
        def cancelled(*args):
            result = original(*args)
            self.conn.execute("UPDATE assistant_tasks SET status='cancel_requested' WHERE id=?", (task["id"],))
            self.conn.commit()
            return result
        with mock.patch.object(reads, "execute", side_effect=cancelled):
            task_runner.run_task(self.agent, self.conn, task["id"])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM tool_receipts").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT state FROM fleet_requests").fetchone()[0], "done")

    def test_forged_handler_answer_cannot_pass_independent_receipt_check(self):
        task, _ = self.task("fleet.result", "Show investigation #101.", source_update=101)
        original = reads.execute
        def forged(*args):
            result = list(original(*args))
            result[2]["value"]["reference"]["pages"] = ["Every backup succeeded."]
            return tuple(result)
        with mock.patch.object(reads, "execute", side_effect=forged):
            outcome = task_runner.run_task(self.agent, self.conn, task["id"])
        self.assertEqual(outcome["status"], "blocked")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM tool_receipts").fetchone()[0], 0)

    def test_sensitive_or_oversized_recorded_answers_return_specific_gap_without_copy(self):
        for page, expected in (("sk-" + "s" * 36, "sensitive_retained_text"), ("x " * 1500, "result_exceeds_shared_reply")):
            with self.subTest(gap=expected):
                document = copy.deepcopy(self.document)
                document["answer_pages"] = [page]
                self.save("done", document)
                task = {"id": 1, "chat_id": 1, "source_update": 202, "source_hash": "x"}
                result, gap = reads._pages(document)
                self.assertEqual(result, [])
                self.assertEqual(gap, expected)
                self.assertNotIn(page, reads._gap(101, gap, "en")["pages"])

    def test_legacy_single_answer_shape_is_retained_without_rewriting_history(self):
        document = copy.deepcopy(self.document)
        document["answer"] = document.pop("answer_pages")[0]
        self.save("done", document)
        _, _, outcome, receipt = self.run_read("fleet.result", "Show investigation #101.", source_update=101)
        self.assertEqual(outcome["status"], "completed")
        self.assertIn(document["answer"], self.agent.completed[0][1])
        self.assertEqual(json.loads(self.conn.execute("SELECT payload FROM fleet_requests").fetchone()[0]), document)

    def test_registry_drift_blocks_actual_invocation(self):
        task, _ = self.task()
        with mock.patch.dict(skill_manifest.TASK_TOOLS["fleet.status"], {"external_network": True}):
            with self.assertRaises(RuntimeError):
                tool_broker.assert_registry()
            outcome = task_runner.run_task(self.agent, self.conn, task["id"])
        self.assertEqual(outcome["status"], "blocked")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM tool_receipts").fetchone()[0], 0)

    def test_closed_inputs_reject_approval_paths_and_noninteger_reference(self):
        for tool in reads.TOOLS:
            for patch in ({"approved": True}, {"command": "id"}, {"path": "/etc/private"}, {"source_update": True}, {"source_update": "101"}, {"lang": []}):
                with self.subTest(tool=tool, patch=patch), self.assertRaises(tool_broker.ToolInputError):
                    tool_broker.validate_input(tool_broker.get_spec(tool), {"query": "Show #101", **patch})

    def test_pure_canonical_helper_is_shared_read_only_and_caller_scope_cannot_expand(self):
        actions = []
        def authorize(action, first, second, database, trigger):
            actions.append(action)
            return sqlite3.SQLITE_OK if action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION} else sqlite3.SQLITE_DENY
        self.conn.set_authorizer(authorize)
        try:
            row, payload, gap = reads.canonical_reference(self.conn, 1, request_id=self.request_id)
        finally:
            self.conn.set_authorizer(None)
        self.assertIsNone(gap)
        self.assertEqual(row["source_update"], 101)
        self.assertEqual(payload, self.document)
        self.assertNotIn(sqlite3.SQLITE_INSERT, actions)
        scope = reads.read_scope(payload, allowed_hosts=["host-a"], allowed_projects=["project-alpha"])
        self.assertEqual(scope["projects"], ["project-alpha"])
        with self.assertRaises(reads.ScopeDenied):
            reads.read_scope(payload, allowed_hosts=["host-b"], allowed_projects=["project-alpha"])

    def test_duplicate_payload_keys_and_wrong_phase_identity_are_rejected(self):
        self.conn.execute("UPDATE fleet_requests SET payload=?", ('{"source_hash":"a","source_hash":"b"}',))
        self.conn.commit()
        with self.assertRaises(reads.ReferenceError):
            reads.canonical_reference(self.conn, 1, request_id=self.request_id)
        self.save("planning_started")
        self.document["inference"] = {"nonce": "a" * 32, "binding": {"source_hash": "f" * 64}}
        self.save("planning_started")
        row, payload, _ = reads.canonical_reference(self.conn, 1, request_id=self.request_id)
        with self.assertRaises(reads.ReferenceError):
            reads.metadata(row, payload)

    def test_implicit_old_context_is_not_silently_reused(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        self.save("done", created=old, updated=old)
        _, _, outcome, receipt = self.run_read("fleet.result", "Show that result.")
        self.assertEqual(outcome["status"], "completed")
        value = json.loads(receipt["data_json"])["value"]["reference"]
        self.assertEqual(value["gap"], "no_current_canonical_context")
        self.assertNotIn("HTTP 503", self.agent.completed[0][1])

    def test_new_source_hash_is_held_by_existing_task_and_receipt_not_a_new_ledger(self):
        task, step, outcome, receipt = self.run_read()
        self.assertEqual(outcome["status"], "completed")
        self.assertEqual(task["source_hash"], tasking.source_hash("What is the status of that investigation?"))
        self.assertEqual(receipt["task_id"], task["id"])
        self.assertEqual(receipt["step_id"], step["id"])
        self.assertEqual(receipt["policy_version"], tool_broker.POLICY_VERSION)
        self.assertNotIn("source_hash", receipt["data_json"])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
