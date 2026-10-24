"""Additional source-linked MCP parity over the frozen shared business code.

This additive overlay executes the original runtime assertions unchanged, then
checks their exact typed observations and final pages through reviewed retained
and base-MCP APIs. No new semantic choices, paid models or host transports occur.
The original corpus, original tests and source modules are not changed.
"""
import copy
import json
import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

import fleet_answers
import fleet_facts as facts
import fleet_mcp as mcp
import fleet_mcp_admission as admission
import fleet_mcp_client as client
import fleet_protocol as protocol
import fleet_queries as queries
import fleet_report
import test_fleet_business_corpus as corpus
import test_fleet_business_extended as original
import test_fleet_facts as fact_cases
import test_fleet_mcp as wire

RUNTIME_CASES = (
    "deployment-versus-progress", "runtime-alpha-only", "explicit-diagnostics",
    "stale-health", "backups-timer-only", "application-vs-container",
    "proxy-load-vs-host", "inactive-one-shot", "unknown-unit",
    "short-sample-stability", "false-cited-backup-success",
)
FACT_CASES = ("unsupported-only-project", "sync-date-not-evidence", "historical-conflict")


def fixture(kind):
    """Create only a temporary protected ledger from the existing wire fixture."""
    class Harness(unittest.TestCase):
        def runTest(self):
            pass
    harness = Harness()
    if kind == "base":
        for name in ("make_server", "call", "start", "row", "payload", "control"):
            setattr(Harness, name, getattr(wire.MCPDurableWire, name))
        wire.MCPDurableWire.setUp(harness)
    else:
        Harness.update = wire.MCPRetainedReferences.update
        wire.MCPRetainedReferences.setUp(harness)
    return harness


def blocked():
    return AssertionError("parity reads must not call models, hosts or new subprocesses")


class RuntimeSourceAssertions(unittest.TestCase):
    def check_source(self, identity):
        # The original test supplies and asserts every expected fact/gap. Record
        # its real build/render arguments without replacing the implementation.
        class Harness(unittest.TestCase):
            case = original.OriginalRuntimeBusiness.case
            def runTest(self):
                pass
        harness = Harness()
        builds, renders = [], []
        build, render = fleet_report.build, fleet_answers.render
        def capture_build(*args, **kwargs):
            value = build(*args, **kwargs)
            builds.append((copy.deepcopy(args), copy.deepcopy(kwargs), copy.deepcopy(value)))
            return value
        def capture_render(*args, **kwargs):
            pages = render(*args, **kwargs)
            renders.append((copy.deepcopy(args), copy.deepcopy(kwargs), copy.deepcopy(pages)))
            return pages
        with mock.patch.object(fleet_report, "build", side_effect=capture_build), \
                mock.patch.object(fleet_answers, "render", side_effect=capture_render), \
                mock.patch("socket.create_connection", side_effect=blocked()), \
                mock.patch("llm.transport_chat", side_effect=blocked()):
            getattr(original.OriginalRuntimeBusiness, "test_" + identity.replace("-", "_"))(harness)
        self.assertEqual(len(builds), 1, identity)
        self.assertEqual(len(renders), 1, identity)
        args, kwargs, evidence = builds[0]
        raw, inventory, plan = args[:3]
        pages = renders[0][2]
        self.assertEqual(pages, render(evidence, None, plan), identity)
        self.assertNotIn("Project Beta", "\n".join(pages))
        self.base_parity(identity, plan, raw, pages)
        if "read_scope" in raw:
            self.observation_parity(identity, raw, inventory, plan, pages, kwargs)

    def base_parity(self, identity, plan, raw, pages):
        harness = fixture("base")
        try:
            question = corpus.BUSINESS[identity]["question"] or "Read recorded backup evidence for Example application host."
            harness.writer.execute("UPDATE conversation SET text=?", (question,))
            harness.writer.commit()
            # No style override: retain the existing upstream preference/detail.
            harness.args = {"source_update": 7, "idempotency_key": identity, "lang": "en"}
            if plan.get("catalog_plan"):
                harness.config["allowed_subjects"] = [item["id"] for item in harness.document["subjects"]]
                harness.server = harness.make_server()
            handle = harness.start()
            payload = harness.payload()
            context = admission.context(harness.config)
            context["requested_format"] = {"lang": "en"}
            semantic = copy.deepcopy(plan)
            # A scoped executor envelope carries project IDs in read_scope;
            # semantic planner input must also retain that canonical selection.
            if semantic.get("read_scope", {}).get("kind") == "projects":
                semantic["projects"] = list(semantic["read_scope"]["projects"])
            selected = admission._plan(context, semantic)
            payload.update(plan=selected, result=copy.deepcopy(raw), answer_pages=list(pages),
                           retained_delivery={"state": "available"})
            harness.writer.execute("UPDATE fleet_requests SET state='done',payload=?", (facts.canonical(payload),))
            harness.writer.commit()
            before = harness.path.read_bytes()
            arguments = {"request_id": handle["request_id"]}
            with mock.patch("socket.create_connection", side_effect=blocked()), \
                    mock.patch("llm.transport_chat", side_effect=blocked()), \
                    mock.patch.object(client.subprocess, "Popen", side_effect=blocked()):
                direct = admission.result(harness.writer, admission.context(harness.config), arguments)
                via = harness.control("investigation_result", arguments)
            self.assertEqual(via["path"], "mcp", identity)
            self.assertEqual(via["result"], direct, identity)
            self.assertEqual(direct["answer_pages"], pages, identity)
            self.assertEqual(direct["observed_at"], raw["observed_at"], identity)
            self.assertEqual(direct["scope"]["hosts"], plan["hosts"], identity)
            self.assertEqual(direct["scope"]["projects"], selected.get("projects", []), identity)
            self.assertEqual(harness.path.read_bytes(), before)
            self.assertEqual(harness.writer.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 1)
            self.assertEqual(harness.writer.execute("SELECT count(*) FROM fleet_events").fetchone()[0], 1)
        finally:
            harness.doCleanups()

    def observation_parity(self, identity, raw, inventory, plan, pages, build_kwargs):
        harness = fixture("retained")
        try:
            payload = wire.reference_payload()
            payload["plan"] = copy.deepcopy(plan)
            payload["plan"].update(operation="inspect", read_contract_hash=protocol.digest(protocol.capability_contract()))
            payload["plan"]["projects"] = list(raw["read_scope"]["projects"])
            payload["result"] = copy.deepcopy(raw)
            harness.update(payload)
            harness.clock = datetime.now(timezone.utc)
            stamp = harness.clock.isoformat()
            harness.writer.execute("UPDATE fleet_requests SET created_at=?,updated_at=?", (stamp, stamp))
            harness.writer.commit()
            capabilities = raw["read_scope"]["capabilities"]
            harness.policy["allowed_capabilities"] = list(capabilities)
            reader = mcp.RetainedReferences(harness.policy, clock=lambda: harness.clock)
            document = wire.fixture()
            server = wire.adapter(document, references=harness.policy)
            server.references.clock = lambda: harness.clock
            config = wire.configured(document)
            config["references"] = copy.deepcopy(harness.policy)
            cfg = SimpleNamespace(fleet_mcp_enabled=True, fleet_mcp_config="/etc/example/mcp.json")
            arguments = {"request_id": wire.REFERENCE_ID, "cursor": "", "max_age_seconds": 90,
                         "projects": ["project-alpha"], "capabilities": list(capabilities)}
            before = harness.path.read_bytes()
            with mock.patch.object(facts, "load", return_value=document), mock.patch.object(mcp, "read_config", return_value=config), \
                    mock.patch("socket.create_connection", side_effect=blocked()), \
                    mock.patch("llm.transport_chat", side_effect=blocked()), \
                    mock.patch.object(client.subprocess, "Popen", side_effect=blocked()):
                direct = reader.read("project_observe", arguments)
                via = client.reference(cfg, "project_observe", arguments, reader,
                                       exchange=lambda messages: wire.exchange(server, messages))
            self.assertEqual(via["path"], "mcp", identity)
            self.assertEqual(via["result"], direct, identity)
            self.assertEqual(direct["scope"]["projects"], ["project-alpha"])
            self.assertEqual(direct["result_observed_at"], raw["observed_at"])
            recovered = copy.deepcopy(raw)
            recovered["hosts"]["host-a"]["projects"] = {"project-alpha": {}}
            for cell in direct["observations"]:
                self.assertEqual(cell["project_id"], "project-alpha")
                self.assertEqual(cell["host_id"], "host-a")
                self.assertEqual(cell["observation"], raw["hosts"]["host-a"]["projects"]["project-alpha"][cell["capability"]])
                recovered["hosts"]["host-a"]["projects"]["project-alpha"][cell["capability"]] = cell["observation"]
            self.assertEqual(recovered, raw)
            rebuilt = fleet_report.build(recovered, inventory, plan, **build_kwargs)
            # Both production paths consume canonical persisted JSON. The
            # original direct helper deliberately has insertion-ordered dicts;
            # retain its assertions above, then compare the same ledger boundary.
            persisted = json.loads(protocol.canonical(raw))
            baseline = fleet_report.build(persisted, inventory, plan, **build_kwargs)
            expected_pages = fleet_answers.render(baseline, None, plan)
            self.assertEqual(fleet_answers.render(rebuilt, None, plan), expected_pages, identity)
            self.assertEqual(harness.path.read_bytes(), before)
        finally:
            harness.doCleanups()


def runtime_case(identity):
    def case(self):
        self.check_source(identity)
    case.__doc__ = "Original assertions plus internal/base/retained MCP parity: " + identity
    return case


for _identity in RUNTIME_CASES:
    setattr(RuntimeSourceAssertions, "test_" + _identity.replace("-", "_"), runtime_case(_identity))


class MixedSourceAssertions(unittest.TestCase):
    setUp = corpus.OriginalCatalogPipeline.setUp
    tearDown = corpus.OriginalCatalogPipeline.tearDown
    turn = corpus.OriginalCatalogPipeline.turn
    pump = corpus.OriginalCatalogPipeline.pump
    reply = corpus.OriginalCatalogPipeline.reply
    completion = corpus.OriginalCatalogPipeline.completion
    base_parity = RuntimeSourceAssertions.base_parity

    def test_mixed_catalog_runtime(self):
        corpus.OriginalCatalogPipeline.test_mixed_catalog_runtime_preserves_both_requested_answer_obligations(self)
        row = self.conn.execute("SELECT payload FROM fleet_requests WHERE source_update=1").fetchone()
        payload = json.loads(row[0])
        self.assertTrue(payload["catalog_combined"])
        self.assertEqual(payload["plan"]["read_scope"]["projects"], ["project-alpha"])
        self.base_parity("mixed-catalog-runtime", payload["plan"], payload["result"], payload["answer_pages"])


class PreferenceSourceAssertions(unittest.TestCase):
    setUp = original.OriginalOwnerFlowBusiness.setUp
    tearDown = original.OriginalOwnerFlowBusiness.tearDown
    turn = original.OriginalOwnerFlowBusiness.turn
    reply = original.OriginalOwnerFlowBusiness.reply
    pump = original.OriginalOwnerFlowBusiness.pump
    completion = original.OriginalOwnerFlowBusiness.completion
    base_parity = RuntimeSourceAssertions.base_parity

    def check_preference(self, identity):
        builds, renders = [], []
        build, render = fleet_report.build, fleet_answers.render
        def capture_build(*args, **kwargs):
            value = build(*args, **kwargs)
            builds.append(copy.deepcopy(args))
            return value
        def capture_render(*args, **kwargs):
            value = render(*args, **kwargs)
            renders.append((copy.deepcopy(args[2]), copy.deepcopy(value)))
            return value
        with mock.patch.object(fleet_report, "build", side_effect=capture_build), \
                mock.patch.object(fleet_answers, "render", side_effect=capture_render):
            getattr(original.OriginalOwnerFlowBusiness, "test_" + identity.replace("-", "_"))(self)
        self.assertEqual(len(builds), 1)
        self.assertTrue(renders)
        raw = builds[0][0]
        for plan, pages in renders:
            self.base_parity(identity, plan, raw, pages)

    def test_brevity_preference(self):
        self.check_preference("brevity-preference")

    def test_detail_override(self):
        self.check_preference("detail-override")


class FactsSourceAssertions(unittest.TestCase):
    def check_source(self, identity):
        # Preserve all existing source-case assertions before paired reads.
        harness = original.OriginalFactsBusiness()
        getattr(harness, "test_" + identity.replace("-", "_"))()
        document = fact_cases.fixture()
        if identity == "unsupported-only-project":
            coverage = next(item for item in document["completeness"] if item["scope"] == {"subject_id": "host-a", "predicate": "hosted_projects"})
            coverage.update(status="unknown", coverage=[])
            subjects, fields = ["host-a"], ["hosted_projects"]
        elif identity == "sync-date-not-evidence":
            case = corpus.BUSINESS[identity]
            document["generated_at"] = case["generated_at"] + "T13:00:00Z"
            for source in document["sources"]:
                source["checked_at"] = case["generated_at"] + "T12:00:00Z"
            subjects, fields = ["host-c"], ["outbound_ipv4"]
        else:
            document = fact_cases.change(document)
            fact_cases.add_fact(document, "historical-reactivation", "lifecycle", "active", "host-old",
                                evidence_at="2026-07-01", source_ids=["fixture-source-old"])
            subjects, fields = ["host-old"], ["lifecycle"]
        document = facts.seal(document)
        plan = queries.validate_plan(dict(kind="catalog", subjects=subjects, fields=fields, scope="explicit",
                                         presentation="list", detail="detailed", clarification=""), document)
        server = wire.adapter(document)
        config = wire.configured(document)
        cfg = SimpleNamespace(fleet_mcp_enabled=True, fleet_mcp_config="/etc/example/mcp.json")
        with mock.patch.object(facts, "load", return_value=document), mock.patch.object(mcp, "read_config", return_value=config), \
                mock.patch("socket.create_connection", side_effect=blocked()), \
                mock.patch("llm.transport_chat", side_effect=blocked()), \
                mock.patch.object(client.subprocess, "Popen", side_effect=blocked()):
            internal = client.internal_fallback(cfg, document, plan)
            via = client.lookup(cfg, document, plan, exchange=lambda messages: wire.exchange(server, messages))
        self.assertEqual(via["path"], "mcp")
        self.assertEqual(via["cells"], internal["cells"])
        self.assertEqual(via["pages"], internal["pages"])
        self.assertEqual(via["cells"], queries.cells(document, plan))
        self.assertEqual(via["pages"], queries.render(document, plan))
        text = "\n".join(via["pages"])
        if identity == "unsupported-only-project":
            self.assertIn("catalog completeness unknown", text)
            self.assertIn("Project Alpha", text)
            self.assertIn("Project Beta", text)
        elif identity == "sync-date-not-evidence":
            self.assertIn("2026-06-01", text)
            self.assertNotIn("2026-10-24", text)
        else:
            self.assertEqual(facts.lookup(document, "host-old", "lifecycle")["status"], "conflict")
            self.assertNotIn("host-old", {item["id"] for item in facts.subjects(document, "host")})


def facts_case(identity):
    def case(self):
        self.check_source(identity)
    case.__doc__ = "Original fact-source assertions plus exact lookup parity: " + identity
    return case


for _identity in FACT_CASES:
    setattr(FactsSourceAssertions, "test_" + _identity.replace("-", "_"), facts_case(_identity))


if __name__ == "__main__":
    unittest.main()