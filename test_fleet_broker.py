"""Shared fleet facts receipts and owner scope; synthetic data and no paid calls."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import fleet_broker as broker
import fleet_facts as facts
import fleet_queries as queries
import skill_manifest
import store
import task_runner
import tasking
import tasks_svc
import tool_broker
from test_fleet_facts import add_fact, change, fixture
from test_task_runtime import StubAgent


def inputs(query="Read host-a provider", **extra):
    return broker.validate_input({"query": query, "fields": ["provider"], **extra})


def plan(source, lookup_inputs=None):
    value = lookup_inputs or inputs(source)
    return {"objective": "Read requested catalog facts", "deliverable": "answer", "steps": [{
        "key": "facts", "tool": "fleet.lookup", "input": value,
        "bindings": {"query": {"source": "boss_span", "start": 0, "end": len(source),
                                  "source_hash": tasking.source_hash(source), "transform": "literal"}},
        "depends_on": [], "purpose": "Read the local published facts"}]}


class FleetBrokerContract(unittest.TestCase):
    def setUp(self):
        self.document = fixture()
        self.binding = {"task_id": 1, "step_id": 2, "chat_id": 3, "source_update": 4,
                        "source_hash": "a" * 64, "input_hash": "b" * 64, "subjects": []}

    def test_registry_manifest_and_planner_advertise_only_implemented_read(self):
        tool_broker.assert_registry()
        spec = tool_broker.get_spec("fleet.lookup")
        self.assertEqual(spec.risk, "read_only")
        self.assertEqual(spec.bound_inputs, frozenset({"query"}))
        self.assertEqual(spec.output_schema, broker.SCHEMA)
        for name in ("external_network", "uses_llm", "writes_state", "destructive", "requires_confirmation"):
            self.assertFalse(getattr(spec, name))
        self.assertEqual(tool_broker.POLICY_VERSION, "task-tools/v3")
        self.assertEqual(tool_broker.IMPLEMENTATION_VERSION, "tasking/v3")
        self.assertIn("fleet.lookup", {item["id"] for item in tasks_svc._tool_manifest()})
        for key in ("fleet.status", "fleet.result"):
            retained = tool_broker.get_spec(key)
            self.assertEqual(retained.risk, "read_only")
            self.assertEqual(retained.bound_inputs, frozenset({"query"}))
            for flag in ("uses_llm", "external_network", "writes_state", "destructive", "requires_confirmation"):
                self.assertFalse(getattr(retained, flag))
        for key in ("fleet.investigate", "fleet.execute"):
            self.assertIsNone(tool_broker.get_spec(key))

    def test_independent_manifest_drift_fails_startup_and_invocation_policy(self):
        with mock.patch.dict(skill_manifest.TASK_TOOLS["fleet.lookup"], {"external_network": True}):
            with self.assertRaises(RuntimeError):
                tool_broker.assert_registry()
            with self.assertRaises(RuntimeError):
                tool_broker.assert_policy(tool_broker.get_spec("fleet.lookup"))

    def test_input_is_closed_and_cannot_supply_paths_facts_or_authority(self):
        spec = tool_broker.get_spec("fleet.lookup")
        for patch in ({"path": "/etc/secret"}, {"allowed_subjects": ["host-b"]}, {"value": "wrong"},
                      {"fields": ["healthy"]}, {"scope": "everything"}, {"subjects": ["x", "x"]},
                      {"fields": ["provider", "provider"]}, {"presentation": "shell"}):
            with self.subTest(patch=patch), self.assertRaises(tool_broker.ToolInputError):
                tool_broker.validate_input(spec, {"query": "host-a", "fields": ["provider"], **patch})

    def test_known_provider_address_and_placement_use_shared_cells_and_provenance(self):
        value = inputs(fields=["provider", "public_ipv4", "hosted_projects"])
        result = broker.build(self.document, value, self.binding)
        self.assertEqual([cell["field"] for cell in result["cells"]], value["fields"])
        self.assertEqual(result["cells"][0]["value"], "ExampleCloud")
        self.assertEqual(result["cells"][1]["value"], "192.0.2.10")
        self.assertIn("project-alpha", [item["id"] for item in result["cells"][2]["value"]])
        self.assertTrue(result["cells"][2]["claim_ids"])
        self.assertEqual(result["facts_integrity"], self.document["integrity"])
        support = broker.evidence(result)
        broker.validate_current(result, self.document, value, self.binding, support)
        tool_broker.validate_output(tool_broker.get_spec("fleet.lookup"),
            {"schema": broker.SCHEMA, "value": {"lookup": result}}, support)

    def test_model_selector_cannot_read_unnamed_host_or_address_as_identity(self):
        for selector in ("host-b", "192.0.2.10", "/etc/hosts", "host-a;id"):
            with self.subTest(selector=selector), self.assertRaises(ValueError):
                broker.build(self.document, inputs(subjects=[selector]), self.binding)

    def test_subject_label_resolves_to_code_owned_identity(self):
        label = self.document["subjects"][0]["label"]
        value = inputs("Read provider for " + label, subjects=[label])
        result = broker.build(self.document, value, self.binding)
        self.assertEqual(result["authority"]["subjects"], ["host-a"])

    def test_alias_resolves_only_from_recorded_facts(self):
        document = copy.deepcopy(self.document)
        add_fact(document, predicate="aliases", content=["Application Box"])
        document = facts.seal(document)
        value = inputs("Read Application Box provider", subjects=["Application Box"])
        self.assertEqual(broker.build(document, value, self.binding)["plan"]["subjects"], ["host-a"])

    def test_ambiguous_auto_alias_refuses_to_expand_to_multiple_identities(self):
        document = copy.deepcopy(self.document)
        add_fact(document, identity="alias-a", predicate="aliases", content=["Shared Box"], subject="host-a")
        add_fact(document, identity="alias-b", predicate="aliases", content=["Shared Box"], subject="host-b")
        document = facts.seal(document)
        for selectors in ([], ["Shared Box"]):
            with self.subTest(selectors=selectors), self.assertRaisesRegex(ValueError, "ambiguous"):
                broker.build(document, inputs("Shared Box provider", subjects=selectors), self.binding)
        exact = broker.build(document, inputs("host-a provider", subjects=["host-a"]), self.binding)
        self.assertEqual(exact["plan"]["subjects"], ["host-a"])

    def test_all_hosts_requires_explicit_owner_class_scope_and_preserves_every_active_host(self):
        value = inputs("List all my servers", fields=["label"], scope="all_hosts")
        result = broker.build(self.document, value, self.binding)
        self.assertEqual(result["authority"]["subjects"], [subject["id"] for subject in facts.subjects(self.document, "host")])
        self.assertNotIn("host-old", result["authority"]["subjects"])
        self.assertEqual(result["coverage"]["kind"], "hosts")
        for query in ("host-a provider", "Compare all plans for host-a", "All project purposes"):
            with self.subTest(query=query), self.assertRaises(ValueError):
                broker.build(self.document, inputs(query, scope="all_hosts"), self.binding)
        with self.assertRaises(ValueError):
            broker.build(self.document, inputs("All my servers", scope="all_hosts", subjects=["host-a"]), self.binding)

    def test_ru_all_project_request_uses_same_catalog_and_requested_language(self):
        query = "\u0412\u0441\u0435 \u043c\u043e\u0438 \u043f\u0440\u043e\u0435\u043a\u0442\u044b"
        value = inputs(query, fields=["label"], scope="all_projects")
        self.assertEqual(value["lang"], "ru")
        result = broker.build(self.document, value, self.binding)
        self.assertEqual(result["authority"]["subjects"], [subject["id"] for subject in facts.subjects(self.document, "project")])

    def test_conflicts_and_unknowns_remain_typed_without_health_claims(self):
        document = copy.deepcopy(self.document)
        add_fact(document, content="Conflicting Cloud")
        document = facts.seal(document)
        result = broker.build(document, inputs(fields=["provider", "outbound_ipv4"]), self.binding)
        self.assertEqual(result["cells"][0]["status"], "conflict")
        self.assertIsNone(result["cells"][0]["value"])
        self.assertIn("conflict", " ".join(result["pages"]))
        self.assertNotIn("healthy", " ".join(result["pages"]))

    def test_changed_revision_forged_value_provenance_or_evidence_fails_actual_facts_check(self):
        value = inputs()
        result = broker.build(self.document, value, self.binding)
        support = broker.evidence(result)
        with self.assertRaises(ValueError):
            broker.validate_current(result, change(self.document), value, self.binding, support)
        for key, changed in (("value", "FabricatedCloud"), ("source_ids", []), ("claim_ids", [])):
            tampered = copy.deepcopy(result)
            tampered["cells"][0][key] = changed
            with self.subTest(key=key), self.assertRaises(ValueError):
                broker.validate_current(tampered, self.document, value, self.binding, support)
        with self.assertRaises(ValueError):
            broker.validate_current(result, self.document, value, self.binding, [])

    def test_output_schema_rejects_missing_cells_and_mutable_authority(self):
        value = broker.build(self.document, inputs(), self.binding)
        for patch in ({"cells": []}, {"facts_integrity": "f" * 64}, {"authority": {"subjects": ["host-a"]}}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                broker.validate_shape({**value, **patch})

    def test_safe_receipt_preserves_only_known_typed_hashes_and_generated_evidence(self):
        value = broker.build(self.document, inputs(), self.binding)
        data = {"schema": broker.SCHEMA, "value": {"lookup": value}}
        support = broker.evidence(value)
        safe_data, safe_support = broker.safe_receipt(data, support)
        self.assertEqual(safe_data, data)
        self.assertEqual(safe_support, support)
        self.assertEqual(safe_data["value"]["lookup"]["facts_integrity"], self.document["integrity"])
        self.assertEqual(safe_data["value"]["lookup"]["sources"][0]["sha256"], value["sources"][0]["sha256"])
        forged = copy.deepcopy(support)
        forged[0]["source"] += ":" + "f" * 64
        with self.assertRaises(ValueError):
            broker.safe_receipt(data, forged)

    def test_secret_shaped_labels_values_or_pages_cannot_use_hash_exemption(self):
        value = broker.build(self.document, inputs(), self.binding)
        for location in ("label", "value", "pages"):
            tampered = copy.deepcopy(value)
            secret = "sk-" + "s" * 36
            if location == "pages":
                tampered["pages"] = [secret]
            else:
                tampered["cells"][0][location] = secret
            with self.subTest(location=location), self.assertRaisesRegex(ValueError, "secret-shaped"):
                broker.safe_receipt({"schema": broker.SCHEMA, "value": {"lookup": tampered}}, broker.evidence(tampered))
        # A long opaque scalar cannot masquerade as a recognized hash field.
        tampered = copy.deepcopy(value)
        tampered["cells"][0]["value"] = "a" * 64
        with self.assertRaisesRegex(ValueError, "secret-shaped"):
            broker.safe_receipt({"schema": broker.SCHEMA, "value": {"lookup": tampered}}, broker.evidence(tampered))

    def test_oversized_read_is_rejected_without_silent_coverage_truncation(self):
        with self.assertRaises(ValueError):
            broker.build(self.document, inputs("All my servers", scope="all_hosts", fields=list(queries.FIELDS)), self.binding)


class FleetBrokerRuntime(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = store.open_db(Path(self.temp.name) / "fleet-task.db")
        self.agent = StubAgent(self.conn, self.temp.name)
        self.agent.cfg.allowed_chat_ids = {1}
        self.document = fixture()

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def task(self, source="Read host-a provider", **extra):
        update = {"update_id": 101, "message": {"message_id": 201, "chat": {"id": 1}, "from": {"id": 1}, "text": source}}
        store.telegram_update_receive(self.conn, update, chat_id=1)
        store.convo_add(self.conn, 1, "user", source, source="boss", update_id=101, tg_message_id=201)
        task, _ = store.assistant_task_create(self.conn, 1, 101, plan(source, inputs(source, **extra)))
        return task, store.assistant_task_steps(self.conn, task["id"])[0]

    def test_real_task_persists_source_bound_receipt_useful_answer_without_model_network_or_worker(self):
        task, step = self.task()
        with mock.patch.object(queries, "load", return_value=self.document), \
             mock.patch("llm.chat_profile", side_effect=AssertionError("lookup must not call a model")), \
             mock.patch("socket.create_connection", side_effect=AssertionError("lookup must not probe")), \
             mock.patch("worker_client.submit", side_effect=AssertionError("lookup must not dispatch")):
            result = task_runner.run_task(self.agent, self.conn, task["id"])
        self.assertEqual(result["status"], "completed")
        self.assertIn("ExampleCloud", self.agent.completed[0][1])
        receipt = self.conn.execute("SELECT * FROM tool_receipts").fetchone()
        value = json.loads(receipt["data_json"])["value"]["lookup"]
        self.assertEqual(value["authority"]["source_update"], 101)
        self.assertEqual(value["authority"]["source_hash"], task["source_hash"])
        self.assertEqual(value["authority"]["input_hash"], receipt["input_hash"])
        self.assertEqual(value["authority"]["step_id"], step["id"])
        self.assertIsNone(receipt["effect_id"])
        self.assertEqual(store.assistant_task_get(self.conn, task["id"])["model_calls"], 0)
        self.assertNotIn("Read host-a provider", step["input_json"])

    def test_completed_receipt_is_reused_on_step_reconciliation_without_lookup_resend(self):
        task, step = self.task()
        with mock.patch.object(queries, "load", return_value=self.document):
            task_runner.run_task(self.agent, self.conn, task["id"])
        self.conn.execute("UPDATE assistant_tasks SET status='planned' WHERE id=?", (task["id"],))
        self.conn.execute("UPDATE assistant_task_steps SET status='pending' WHERE id=?", (step["id"],))
        self.conn.commit()
        with mock.patch.object(broker, "execute", side_effect=AssertionError("immutable receipt must be reused")):
            task_runner.run_task(self.agent, self.conn, task["id"])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM tool_receipts").fetchone()[0], 1)

    def test_changed_canonical_source_blocks_without_receipt(self):
        task, _ = self.task()
        self.conn.execute("UPDATE conversation SET text='Read host-b provider' WHERE update_id=101")
        self.conn.commit()
        with mock.patch.object(queries, "load", side_effect=AssertionError("stale authority must block first")):
            result = task_runner.run_task(self.agent, self.conn, task["id"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM tool_receipts").fetchone()[0], 0)

    def test_foreign_principal_and_forward_source_cannot_supply_authority(self):
        task, step = self.task()
        value = task_runner.resolve_inputs(self.conn, task, step)
        self.agent.cfg.allowed_chat_ids = {2}
        with self.assertRaises(ValueError):
            broker.authority(self.agent.cfg, self.conn, task, step, value)
        self.agent.cfg.allowed_chat_ids = {1}
        self.conn.execute("UPDATE conversation SET source='forward' WHERE update_id=101")
        self.conn.commit()
        with self.assertRaises(ValueError):
            broker.authority(self.agent.cfg, self.conn, task, step, value)

    def test_predecessor_or_model_cannot_replace_boss_span_authority(self):
        task, step = self.task()
        value = task_runner.resolve_inputs(self.conn, task, step)
        tampered = dict(step)
        tampered["bindings_json"] = json.dumps({"query": {"source": "step_output", "trust": "confirmed_local"}})
        with self.assertRaises(ValueError):
            broker.authority(self.agent.cfg, self.conn, task, tampered, value)

    def test_snapshot_swap_during_read_blocks_receipt(self):
        task, _ = self.task()
        with mock.patch.object(queries, "load", side_effect=[self.document, change(self.document)]):
            result = task_runner.run_task(self.agent, self.conn, task["id"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM tool_receipts").fetchone()[0], 0)

    def test_shared_runner_rejects_forged_handler_output_before_durable_receipt(self):
        task, step = self.task()
        resolved = task_runner.resolve_inputs(self.conn, task, step)
        with mock.patch.object(queries, "load", return_value=self.document):
            result = list(broker.execute(self.agent.cfg, self.conn, task, step, resolved))
        result[2]["value"]["lookup"]["cells"][0]["value"] = "FabricatedCloud"
        with mock.patch.object(queries, "load", return_value=self.document), \
             mock.patch.object(task_runner, "_execute_read_or_draft", return_value=tuple(result)):
            outcome = task_runner.run_task(self.agent, self.conn, task["id"])
        self.assertEqual(outcome["status"], "blocked")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM tool_receipts").fetchone()[0], 0)

    def test_shared_synthesis_consumes_the_same_real_fact_receipt_with_citations(self):
        task, _ = self.task()
        with mock.patch.object(queries, "load", return_value=self.document):
            task_runner.run_task(self.agent, self.conn, task["id"])
        receipt = self.conn.execute("SELECT * FROM tool_receipts").fetchone()
        citation = json.loads(receipt["evidence_json"])[0]["id"]
        captured = []
        def model(cfg, conn, operation, messages, **kwargs):
            captured.extend(messages)
            return json.dumps({"claims": [{"claim": "ExampleCloud is the recorded provider.",
                "citation_ids": [citation], "confidence": 1, "limitation": "Catalog evidence only"}],
                "recommendation": {"text": "Use the recorded catalog fact.", "citation_ids": [citation],
                "confidence": 1, "tradeoffs": []}, "conflicts": [], "unknowns": []})
        with mock.patch("llm.chat_profile", side_effect=model):
            result = task_runner._synthesize(self.agent.cfg, self.conn, task, {}, {"receipt_steps": ["facts"]})
        self.assertEqual(result[0], "ok")
        self.assertIn("ExampleCloud", captured[1]["content"])
        self.assertIn(self.document["integrity"], captured[1]["content"])
        self.assertEqual(result[3][0]["id"], citation)
        tool_broker.validate_output(tool_broker.get_spec("research.synthesize"), result[2], result[3])

    def test_real_installed_loader_does_not_accept_synthetic_public_fixture(self):
        task, step = self.task()
        path = Path(self.temp.name) / "facts.json"
        path.write_text(facts.canonical(self.document), encoding="utf-8")
        self.agent.cfg.fleet_facts = str(path)
        with self.assertRaises(ValueError):
            broker.execute(self.agent.cfg, self.conn, task, step, task_runner.resolve_inputs(self.conn, task, step))

    def test_invocation_policy_drift_blocks_actual_shared_task(self):
        task, _ = self.task()
        with mock.patch.dict(skill_manifest.TASK_TOOLS["fleet.lookup"], {"writes_state": True}):
            result = task_runner.run_task(self.agent, self.conn, task["id"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM tool_receipts").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
