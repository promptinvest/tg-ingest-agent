"""Source-linked internal/MCP business parity, with original assertions intact.

Nineteen catalog source cases run their existing full Agent assertions with MCP
both disabled and enabled. Direct legacy/clarification behavior remains explicit;
applicable typed lookups additionally compare exact cells/pages through the same
client and in-memory reviewed stdio exchange. No model, host or paid work is live.
"""
import copy
import json
from pathlib import Path
import unittest
from unittest import mock

import cara_fleet
import fleet
import fleet_answers
import fleet_facts
import fleet_host
import fleet_mcp as mcp
import fleet_mcp_client as client
import fleet_protocol as protocol
import fleet_queries as queries
import fleet_report
import llm
import worker_client
import test_fleet_business_corpus as original
import test_fleet_catalog as catalog
import test_fleet_mcp as wire
import test_fleet_mcp_integration as enabled
import test_fleet_project_adapter_integration as adapter_flow
import test_fleet_project_adapters as adapter_fixtures
import test_fleet_project_scope as scoped


def catalog_run(owner, source_id, use_mcp):
    # Local harness deliberately imports no TestCase class into module globals;
    # unittest cannot accidentally discover the reused suites a second time.
    class Harness(unittest.TestCase):
        setUp = enabled.EnabledFactsIntegration.setUp
        tearDown = enabled.EnabledFactsIntegration.tearDown
        reply = enabled.EnabledFactsIntegration.reply
        turn = enabled.EnabledFactsIntegration.turn
        completion = enabled.EnabledFactsIntegration.completion
        row = enabled.EnabledFactsIntegration.row
        payload = enabled.EnabledFactsIntegration.payload
        pump = enabled.EnabledFactsIntegration.pump
        seed_prior = original.OriginalCatalogPipeline.seed_prior
        def runTest(self):
            pass

    harness = Harness()
    harness.setUp()
    try:
        harness.cfg.fleet_mcp_enabled = use_mcp
        # This executes every original required field/value/name/gap/source,
        # no-host, model-limit and no-action assertion, without replacing it.
        original.OriginalCatalogPipeline.check_case(harness, source_id)
        payload = harness.payload()
        actual_pages = payload.get("answer_pages", [payload["answer"]])
        plan = payload["plan"]
        legacy = payload.get("kind") == "inventory"
        if legacy:
            plan = queries.validate_plan(original.raw(original.CHOICES[source_id]), harness.facts)
        if plan.get("kind") == "clarification":
            owner.assertEqual(harness.exchanges, 0)
            return {"actual_pages": actual_pages, "obligations": payload.get("answer_obligations", []),
                    "plan": plan, "lookup": None, "model_calls": payload.get("model_calls", 0)}
        owner.assertEqual(plan["kind"], "catalog")
        before_calls = harness.conn.execute("SELECT count(*) FROM llm_usage").fetchone()[0]
        result = client.lookup(harness.cfg, harness.facts, plan, lang=payload["lang"])
        owner.assertEqual(result["path"], "mcp" if use_mcp else "internal")
        owner.assertEqual(result["cells"], queries.cells(harness.facts, plan))
        owner.assertEqual(result["pages"], queries.render(harness.facts, plan, payload["lang"]))
        owner.assertEqual(harness.conn.execute("SELECT count(*) FROM llm_usage").fetchone()[0], before_calls)
        owner.assertFalse((harness.root / "spool/requests").exists())
        if not legacy:
            owner.assertEqual(payload["answer_obligations"], result["cells"])
            owner.assertEqual(actual_pages, result["pages"])
        return {"actual_pages": actual_pages, "obligations": payload.get("answer_obligations", []),
                "plan": plan, "lookup": {key: result[key] for key in ("facts_integrity", "cells", "pages")},
                "model_calls": payload.get("model_calls", 0)}
    finally:
        harness.tearDown()


class OriginalCatalogBothPaths(unittest.TestCase):
    def check_original(self, source_id):
        self.assertIn(source_id, original.BUSINESS)
        internal = catalog_run(self, source_id, False)
        via_mcp = catalog_run(self, source_id, True)
        self.assertEqual(via_mcp, internal, source_id)


def catalog_case(source_id):
    def case(self):
        self.check_original(source_id)
    case.__doc__ = "Original source ID, both capability paths: " + source_id
    return case


for source_id in original.CHOICES:
    setattr(OriginalCatalogBothPaths, "test_" + source_id.replace("-", "_"), catalog_case(source_id))

class FreshSelectedProjectAdmission(unittest.TestCase):
    setUp = enabled.DurableMCPAgentIntegration.setUp
    tearDown = enabled.DurableMCPAgentIntegration.tearDown
    reply = enabled.DurableMCPAgentIntegration.reply
    source = enabled.DurableMCPAgentIntegration.source
    row = enabled.DurableMCPAgentIntegration.row
    payload = enabled.DurableMCPAgentIntegration.payload
    wait_futures = enabled.DurableMCPAgentIntegration.wait_futures
    control = enabled.DurableMCPAgentIntegration.control
    admit = enabled.DurableMCPAgentIntegration.admit
    drain = enabled.DurableMCPAgentIntegration.drain

    def test_base_start_collects_only_selected_project_and_returns_useful_dated_answer(self):
        self.check_selected_project(ci_connected=True)

    def test_unconnected_ci_keeps_visible_revision_without_full_hash_or_false_ci(self):
        self.check_selected_project(ci_connected=False)

    def check_selected_project(self, ci_connected):
        # Real admission, owner planner, spool, executor/gateway and result reader;
        # only the pre-existing protected transports/files/models are doubles.
        inventory = self.base.inventory()
        inventory["projects"]["project-beta"] = {"label": "Project Beta", "hosts": ["host-a"],
            "recorded_at": "2026-10-20", "notes": "Unrelated colocated application."}
        Path(self.cfg.fleet_inventory).write_text(protocol.canonical(inventory), encoding="utf-8")
        capabilities = ["backups", "development", "runtime"]
        scope = {"version": protocol.READ_VERSION, "kind": "projects",
                 "projects": ["project-alpha"], "capabilities": capabilities}
        plan = {"hosts": ["host-a"], "facets": ["projects"], "clarification": "",
            "operation": "inspect", "service": "", "scope": "explicit", "projects": ["project-alpha"],
            "focus": ["backups", "progress", "services"], "read_scope": scope}
        documents = {"/protected/alpha-backup.json": adapter_fixtures.backup_document(),
                     "/protected/alpha-development.json": adapter_fixtures.development_document()}
        if not ci_connected:
            documents["/protected/alpha-development.json"]["ci"] = {
                "status": "not_connected", "captured_at": None, "items": [], "complete": False}
        reads, remotes, results = [], [], []
        def source(path):
            reads.append(path)
            if path not in documents:
                raise AssertionError("colocated or arbitrary project source collected")
            return copy.deepcopy(documents[path])
        def state(unit, timeout=5):
            self.assertEqual(unit, "alpha-job.service")
            return {"LoadState": "loaded", "ActiveState": "inactive", "SubState": "dead",
                    "Type": "oneshot", "Result": "success"}
        def remote(_transport, target, request):
            self.assertEqual(target, "host-a")
            self.assertEqual(request["read_scope"], scope)
            remotes.append(copy.deepcopy(request))
            return fleet_host.run(adapter_flow.config(), request)
        def transport(prepared):
            return self.base.completion(plan if prepared["skill"] == "fleet_planner" else
                {"highlights": [], "hypotheses": [], "next_checks": []})
        def complete_spool(*_args, **_kwargs):
            operation = self.payload()["operation"]
            self.assertEqual(operation["read_scope"], scope)
            self.assertTrue(list((self.root / "spool/requests").glob("*.json")))
            result = cara_fleet.inspect(inventory, {}, operation)
            results.append(copy.deepcopy(result))
            return result
        import fleet_project_adapters as adapters
        with mock.patch.object(protocol, "now", return_value=adapter_fixtures.NOW), \
                mock.patch.object(protocol, "age", return_value=0), \
                mock.patch.object(llm, "transport_chat", side_effect=transport) as model, \
                mock.patch.object(adapters, "_read_fixed_json", side_effect=source), \
                mock.patch.object(cara_fleet, "remote", side_effect=remote), \
                mock.patch.object(fleet_host, "unit_state", side_effect=state), \
                mock.patch.object(fleet_host, "resources", side_effect=AssertionError("host CPU substituted for project")), \
                mock.patch.object(fleet_host, "read", side_effect=AssertionError("unselected manifest")), \
                mock.patch.object(fleet_host, "command", side_effect=AssertionError("unselected command/effect")):
            handle = self.admit("Inspect Project Alpha runtime, verified backup and repository/CI/issues. Use a table.", presentation="table")
            with mock.patch.object(worker_client, "poll", side_effect=complete_spool):
                self.drain()
            result = self.control("investigation_result", {"request_id": handle["request_id"]})
            direct = self.admission.result(self.conn, self.admission.context(self.config), {"request_id": handle["request_id"]})
        self.assertEqual(result["path"], "mcp")
        self.assertEqual(result["result"], direct)
        self.assertEqual(result["result"]["scope"]["projects"], ["project-alpha"])
        text = "\n".join(direct["answer_pages"])
        for value in ("Project Alpha", "successful backup 2026-10-24 11:30:00 UTC",
                      "restore verification 2026-10-24 11:40:00 UTC", "integrity=ok",
                      "latest recorded backup", adapter_fixtures.REPO,
                      "Example / Project Alpha: keep useful names", "success", "2026-10-24", "|"):
            self.assertIn(value, text)
        self.assertNotIn("Project Beta", text)
        # Archive basenames are private restore-matching keys, never public output.
        self.assertNotIn(adapter_fixtures.ARCHIVE, text)
        self.assertNotIn("CPU", text)
        self.assertNotIn("development complete", text.lower())
        self.assertIn("revision " + adapter_fixtures.REVISION[:12], text)
        self.assertNotIn("revision ***", text)
        self.assertNotIn(adapter_fixtures.REVISION, text)
        typed = results[0]["hosts"]["host-a"]["projects"]["project-alpha"]["development"]
        self.assertEqual(typed["repository"]["revision"], adapter_fixtures.REVISION)
        if ci_connected:
            self.assertIn("#101 success @" + adapter_fixtures.REVISION[:12], text)
        else:
            self.assertIn("CI: not connected", text)
            self.assertNotIn("#101", text)
            self.assertEqual(typed["gaps"]["ci"], "not_connected")
        self.assertEqual(set(reads), set(documents))
        self.assertEqual([item["operation"] for item in remotes], ["capabilities", "inspect"])
        self.assertEqual(len(results), 1)
        self.assertEqual(set(results[0]["hosts"]["host-a"]["projects"]), {"project-alpha"})
        self.assertEqual(set(results[0]["hosts"]["host-a"]["projects"]["project-alpha"]), set(capabilities))
        self.assertEqual(model.call_count, 2)
        self.assertEqual(self.payload()["model_calls"], 2)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 1)
        self.assertEqual(self.sent, [])

class RetainedTypedProjectBothPaths(unittest.TestCase):
    setUp = wire.MCPRetainedReferences.setUp
    update = wire.MCPRetainedReferences.update
    arguments = wire.MCPRetainedReferences.arguments

    def check_adapter(self, kind):
        import fleet_project_adapters as adapters
        import test_fleet_monitor_integration as monitor_flow
        if kind in {"daily_lingo_observer", "authenticated_proxy_route"}:
            binding = monitor_flow.binding(kind)
            documents = monitor_flow.documents(kind)
            capability = "runtime"
        else:
            binding = adapter_fixtures.config("development" if kind == "development_actions" else kind,
                                               path="/protected/example-evidence.json")
            binding["adapter"] = adapters.ADAPTERS[kind]["implementation_version"]
            document = (adapter_fixtures.backup_document() if kind == "backups" else
                        adapter_fixtures.actions_development_document() if kind == "development_actions" else
                        adapter_fixtures.development_document())
            documents = {binding["path"]: document}
            capability = "backups" if kind == "backups" else "development"
        def read(path, maximum_bytes=adapters.MAX_SOURCE_BYTES):
            self.assertIn(path, documents)
            return copy.deepcopy(documents[path])
        with mock.patch.object(adapters, "_read_fixed_json", side_effect=read):
            observation = adapters.read_bound(kind, binding, "project-alpha", adapter_fixtures.NOW)
        self.assertEqual(observation["status"], "recorded")
        protocol._capability_payload(capability, observation)
        payload = wire.reference_payload()
        scope = {"version": protocol.READ_VERSION, "kind": "projects",
                 "projects": ["project-alpha"], "capabilities": [capability]}
        payload["plan"]["read_scope"] = scope
        payload["result"]["read_scope"] = scope
        payload["result"]["observed_at"] = observation["observed_at"]
        selected = payload["result"]["hosts"]["host-a"]
        selected["read_scope"] = scope
        selected["observed_at"] = observation["observed_at"]
        selected["projects"] = {"project-alpha": {capability: observation}}
        self.update(payload)
        self.policy["allowed_capabilities"] = [capability]
        self.reader = mcp.RetainedReferences(self.policy, clock=lambda: self.clock)
        self.server = wire.adapter(references=self.policy)
        self.server.references.clock = lambda: self.clock
        arguments = self.arguments("project_observe", capabilities=[capability])
        before = self.writer.execute("SELECT payload FROM fleet_requests").fetchone()[0]
        internal = self.reader.read("project_observe", arguments)
        config = {**wire.configured(wire.fixture()), "references": copy.deepcopy(self.policy)}
        from types import SimpleNamespace
        cfg = SimpleNamespace(fleet_mcp_enabled=True, fleet_mcp_config="/etc/example-facts/mcp.json")
        with mock.patch.object(mcp, "read_config", return_value=config), \
                mock.patch("socket.create_connection", side_effect=AssertionError("retained parity must not inspect hosts")), \
                mock.patch.object(llm, "transport_chat", side_effect=AssertionError("retained parity must not call models")):
            via_mcp = client.reference(cfg, "project_observe", arguments, self.reader,
                                      exchange=lambda messages: wire.exchange(self.server, messages))
        self.assertEqual(via_mcp["path"], "mcp")
        self.assertEqual(via_mcp["result"], internal)
        self.assertEqual(internal["scope"]["projects"], ["project-alpha"])
        self.assertEqual(len(internal["observations"]), 1)
        self.assertEqual(internal["observations"][0]["observation"], observation)
        self.assertEqual(self.writer.execute("SELECT payload FROM fleet_requests").fetchone()[0], before)
        text = fleet_facts.canonical(via_mcp)
        self.assertNotIn("Project Beta", text)
        self.assertNotIn("SYNTHETIC-PRIVATE-CANARY", text)
        self.assertNotIn("SYNTHETIC-OWNER-TEXT-CANARY", text)
        self.assertNotIn("/protected/", text)


def retained_case(kind):
    def case(self):
        self.check_adapter(kind)
    case.__doc__ = "Same retained typed data, dates and gaps through both paths: " + kind
    return case


for kind in ("backups", "development", "development_actions", "daily_lingo_observer", "authenticated_proxy_route"):
    setattr(RetainedTypedProjectBothPaths, "test_" + kind, retained_case(kind))


if __name__ == "__main__":
    unittest.main()
