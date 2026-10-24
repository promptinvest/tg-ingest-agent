"""Original business source IDs executed against real code-owned answer paths.

Frozen semantic choices isolate pipeline/rendering acceptance from actual-model
language qualification. These tests do not claim model quality, live gateway
qualification, or accepted deployment of the separate durable MCP capabilities.
The original corpus files remain unchanged and are the source of questions and
assertions. Existing independent lifecycle/runtime suites remain required.
"""
import copy
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import common
import converse_svc
import fleet
import fleet_mcp as mcp
import fleet_mcp_client as client
import fleet_protocol as protocol
import fleet_queries as queries
import llm
import store
import tasking
import worker_client
import test_fleet_catalog as catalog_tests
from test_fleet_facts import FIXTURE, fixture
from test_fleet_mcp import adapter, configured, exchange

BUSINESS = {case["id"]: case for case in json.loads((FIXTURE.parent / "regression-cases.json").read_text(encoding="utf-8-sig"))["cases"]}
MCP = {case["id"]: case for case in json.loads((FIXTURE.parent / "mcp-regression-cases.json").read_text(encoding="utf-8-sig"))["cases"]}

# Reviewed semantic choices, never generated answer prose or invented facts.
# Each real owner question below still passes source binding, budget reservation,
# async plan validation, answer-obligation expansion and production delivery.
CHOICES = {
    "catalog-ru-owner-first": ([], ["label"], "all", "list"),
    "catalog-ru-server-first": ([], ["label"], "all", "list"),
    "catalog-en-paraphrase": ([], ["label"], "all", "list"),
    "catalog-ru-paraphrase": ([], ["label"], "all", "list"),
    "ip-followup": ([], ["public_ipv4"], "inherit", "list"),
    "providers": ([], ["provider"], "all", "list"),
    "requested-table": ([], ["label", "provider", "public_ipv4", "hosted_projects"], "all", "table"),
    "shared-host-projects": ([], ["hosted_projects"], "inherit", "list"),
    "edge-host-projects": ([], ["hosted_projects"], "inherit", "list"),
    "catalog-outage": (["host-a"], ["public_ipv4"], "explicit", "list"),
    "public-versus-egress": (["host-c"], ["public_ipv4", "outbound_ipv4"], "explicit", "list"),
    "unknown-provider": (["host-c"], ["provider"], "explicit", "list"),
    "ambiguous-host": ([], ["hosted_projects"], "explicit", "list"),
    "missing-probe": ([], ["hosted_projects"], "inherit", "list"),
    "correction-retains-question": ([], [], "inherit", "list"),
    "reformat-no-reprobe": (["project-alpha", "project-beta"], ["label"], "explicit", "names"),
    "untrusted-note-action": ([], ["label"], "all", "list"),
    "known-ip-model-unavailable": (["host-a"], ["public_ipv4"], "explicit", "list"),
    "unsupported-generated-answer-fallback": (["host-a"], ["public_ipv4"], "explicit", "list"),
}


def raw(choice):
    subjects, fields, scope, presentation = choice
    return dict(kind="catalog", subjects=list(subjects), fields=list(fields), scope=scope,
                presentation=presentation, detail="concise", clarification="")


class OriginalCatalogPipeline(unittest.TestCase):
    # Reuse only fixture/lifecycle helpers, not another class's test methods.
    setUp = catalog_tests.CatalogDispatch.setUp
    tearDown = catalog_tests.CatalogDispatch.tearDown
    turn = catalog_tests.CatalogDispatch.turn
    pump = catalog_tests.CatalogDispatch.pump
    reply = catalog_tests.CatalogDispatch.reply
    completion = catalog_tests.CatalogDispatch.completion

    def seed_prior(self, case):
        if "prior_scope" not in case:
            return
        question = case.get("prior_question") or "Read the named catalog entities."
        fields = ["public_ipv4"] if case["id"] == "correction-retains-question" else ["hosted_projects"] if case["id"] == "reformat-no-reprobe" else ["label"]
        plan = queries.validate_plan(raw((case["prior_scope"], fields, "explicit", "list")), self.facts)
        self.turn(0, question, dispatch=False)
        frame = queries.frame(question, plan, 0)
        payload = dict(question=question, source_hash=tasking.source_hash(question), plan=plan,
                       kind="catalog", context=frame, facts_integrity=self.facts["integrity"],
                       answer_pages=queries.render(self.facts, plan), lang="en")
        self.conn.execute("INSERT INTO fleet_requests VALUES(?,?,?,?,?,?,?)",
            ("corpus-prior", 111, 0, "done", protocol.canonical(payload), protocol.now(), protocol.now()))
        self.conn.commit()

    def test_mixed_catalog_runtime_preserves_both_requested_answer_obligations(self):
        case = BUSINESS["mixed-catalog-runtime"]
        self.patchers[-1].stop()  # This scenario must exercise the real spool publication.
        scope = {"version": protocol.READ_VERSION, "kind": "projects", "projects": ["project-alpha"], "capabilities": ["http_health"]}
        runtime_plan = dict(hosts=["host-a"], facets=["projects"], clarification="", operation="inspect", service="",
                            scope="explicit", projects=["project-alpha"], focus=["projects"], read_scope=scope)
        mixed = {"kind": "mixed", "catalog": raw(([], ["label"], "all", "list")), "runtime": runtime_plan}
        def transport(prepared):
            return self.completion(mixed if prepared["skill"] == "fleet_planner" else {"highlights": [], "hypotheses": [], "next_checks": []})
        question = case["question"]
        self.turn(1, question, dispatch=False)
        with mock.patch.object(llm, "transport_chat", side_effect=transport), \
             mock.patch.object(fleet, "submit", wraps=fleet.submit) as submit:
            self.agent.do_fleet(111, common.detect_lang(question) or "en", question)
            for _ in range(20):
                self.agent.check_fleet_requests()
                for call in getattr(self.agent, "_fleet_inference_calls", {}).values():
                    if call.future is not None:
                        call.future.result(timeout=3)
                row = self.conn.execute("SELECT * FROM fleet_requests WHERE source_update=1").fetchone()
                if row["state"] == "queued":
                    break
            self.assertEqual(row["state"], "queued")
            payload = json.loads(row["payload"])
            operation = payload["operation"]
            self.assertEqual(operation["hosts"], ["host-a"])
            self.assertEqual(operation["facets"], ["projects"])
            self.assertEqual(operation["read_scope"], scope)
            self.assertEqual(submit.call_count, 1)
            self.assertEqual({cell["subject_id"] for cell in payload["catalog_obligations"]}, {"host-a", "host-b", "host-c"})
            measured = {"version": protocol.VERSION, "observed_at": protocol.now(), "read_scope": scope,
                "hosts": {"host-a": {"host": "host-a", "observed_at": protocol.now(), "read_scope": scope,
                    "projects": {"project-alpha": {"http_health": protocol.capability_observation("http_health", "observed", http_status=503, health={"status": "failed"})}}}}}
            protocol.result(measured, ["host-a"], read_scope=scope)
            with mock.patch.object(worker_client, "poll", return_value=measured):
                self.assertEqual(self.pump(), "done")
        payload = json.loads(self.conn.execute("SELECT payload FROM fleet_requests WHERE source_update=1").fetchone()[0])
        answer = "\n".join(payload["answer_pages"])
        for label in ("Example application host", "Example edge host", "Example observer host", "Project Alpha", "HTTP 503"):
            self.assertIn(label, answer, case["id"])
        self.assertNotIn("Project Beta", answer)
        self.assertNotIn("CPU", answer)
        self.assertTrue(payload["catalog_combined"])
        self.assertEqual(payload["model_calls"], 2)

    def check_case(self, source_id):
        case = BUSINESS[source_id]
        expected = case["expected"]
        self.seed_prior(case)
        if "external_note" in case:
            store.convo_add(self.conn, 111, "user", case["external_note"], source="forward")
        question = case["question"]
        self.turn(1, question, dispatch=False)
        chosen = raw(CHOICES[source_id])
        if source_id == "ambiguous-host":
            chosen.update(kind="clarification", subjects=[], clarification="host reference")
        if source_id == "unsupported-generated-answer-fallback":
            chosen["answer"] = case["raw_output"]
        if case.get("executor") == "unavailable":
            Path(self.cfg.fleet_inventory).unlink()
        transport_effect = TimeoutError("synthetic unknown paid outcome") if case.get("model") == "unavailable" else None
        with mock.patch.object(self.agent, "_fleet_try_catalog_direct", wraps=self.agent._fleet_try_catalog_direct) as direct, \
             mock.patch.object(llm, "transport_chat", side_effect=transport_effect, return_value=self.completion(chosen)) as transport, \
             mock.patch("socket.create_connection", side_effect=AssertionError("catalog host probe forbidden")):
            if case.get("executor") != "unavailable":
                direct.side_effect = lambda *args, **kwargs: False
            self.agent.do_fleet(111, common.detect_lang(question) or "en", question)
            self.assertEqual(self.pump(), "done", source_id)
        row = self.conn.execute("SELECT * FROM fleet_requests WHERE source_update=1").fetchone()
        payload = json.loads(row["payload"])
        answer = "\n".join(payload.get("answer_pages", [payload["answer"]]))
        self.assertEqual(payload["question"], question)
        self.assertEqual(payload["source_hash"], tasking.source_hash(question))
        subjects = {subject["id"]: subject["label"] for subject in self.facts["subjects"]}
        legacy_roster = payload.get("kind") == "inventory"
        if legacy_roster:
            # Preserve the existing typed direct-roster receipt shape. Its
            # label obligations are proved against actual delivered text and
            # exact configured host IDs, never fabricated stored catalog cells.
            self.assertEqual(set(payload["plan"]), {"hosts"})
            self.assertEqual(expected.get("required_fields", ["label"]), ["label"])
            actual_subjects = payload["plan"]["hosts"]
            self.assertEqual(actual_subjects, ["host-a", "host-b", "host-c"])
            for identity in actual_subjects:
                self.assertIn(subjects[identity], answer, (source_id, identity, "label"))
            self.assertNotIn(subjects["host-old"], answer)
            self.assertEqual(transport.call_count, 0)
        else:
            self.assertEqual(payload["plan"]["kind"], expected.get("result_kind", "catalog"))
            actual_subjects = payload["plan"]["subjects"]
        obligations = payload.get("answer_obligations", [])
        if "scope" in expected:
            self.assertEqual(actual_subjects, expected["scope"], source_id)
        for field in expected.get("required_fields", []):
            if legacy_roster:
                self.assertEqual(field, "label")
                continue
            self.assertIn(field, payload["plan"]["fields"], source_id)
            for identity in actual_subjects:
                self.assertTrue(any(cell["subject_id"] == identity and cell["field"] == field for cell in obligations), (source_id, identity, field))
                if field == "label":
                    self.assertIn(subjects[identity], answer, (source_id, identity, field))
        for identity in expected.get("required_entities", []):
            self.assertIn(subjects[identity], answer, source_id)
            represented = set(payload["plan"]["subjects"])
            represented.update(item["id"] for cell in obligations if isinstance(cell["value"], list) for item in cell["value"])
            self.assertIn(identity, represented, source_id)
        for value in expected.get("required_values", []):
            self.assertIn(value, answer, source_id)
        for required in expected.get("required_unknowns", []):
            identity, field = required.split(".")
            self.assertTrue(any(cell["subject_id"] == identity and cell["field"] == field and cell["status"] == "unknown" for cell in obligations), source_id)
        if expected.get("presentation") == "table":
            self.assertIn("|", answer)
        if expected.get("presentation") == "names only":
            self.assertEqual(payload["plan"]["fields"], ["label"])
            self.assertEqual(payload["plan"]["presentation"], "names")
        if expected.get("keep_evidence_date"):
            self.assertIn("2026-06-01", answer)
            self.assertNotIn("freshly", answer)
        if source_id == "unsupported-generated-answer-fallback":
            self.assertEqual(transport.call_count, 2)
            self.assertNotIn("backup", answer.lower())
        if case.get("model") == "unavailable":
            self.assertEqual(transport.call_count, 1)
        for forbidden in ("CPU", "PID", "jsonrpc", "resource hypotheses", "diagnostic dump"):
            self.assertNotIn(forbidden, answer)
        self.assertFalse((self.root / "spool/requests").exists())
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_requests WHERE state IN (\'approval\',\'executing\')").fetchone()[0], 0)


def catalog_test(source_id):
    def test(self):
        self.check_case(source_id)
    test.__doc__ = "Original regression source ID: " + source_id
    return test


for source_id in CHOICES:
    setattr(OriginalCatalogPipeline, "test_" + source_id.replace("-", "_"), catalog_test(source_id))


class OriginalCleanupCorpus(unittest.TestCase):
    def test_original_cleanup_variants_preserve_names_and_remove_only_narrated_gestures(self):
        for case in BUSINESS.values():
            if case.get("kind") != "output_cleanup":
                continue
            for text in case.get("raw_output_variants", [case["raw_output"]]):
                with self.subTest(source_id=case["id"], text=text):
                    answer = converse_svc.ConverseMixin._strip_roleplay(text)
                    for required in case["expected"]["required_final_text"]:
                        self.assertIn(required, answer)
                    for forbidden in case["expected"].get("forbidden_final_text", []):
                        self.assertNotIn(forbidden, answer)


class AdditionalFactsCorpus(unittest.TestCase):
    def setUp(self):
        self.document = fixture()
        self.cfg = SimpleNamespace(fleet_mcp_enabled=True, fleet_mcp_config="/etc/example-facts/mcp.json")

    def call(self, plan, transport=None, allowed=None, lang="en"):
        with mock.patch.object(mcp, "read_config", return_value=configured(self.document, allowed)), \
             mock.patch("fleet_facts.load", return_value=self.document), \
             mock.patch("socket.create_connection", side_effect=AssertionError("facts cannot probe hosts")):
            return client.lookup(self.cfg, self.document, plan, lang=lang,
                exchange=transport or (lambda messages: exchange(adapter(self.document, allowed), messages)))

    def test_mcp_catalog_parity(self):
        case = MCP["mcp-catalog-parity"]
        plan = queries.validate_plan(raw((["host-a"], case["expected"]["required_fields"], "explicit", "list")), self.document)
        result = self.call(plan)
        self.assertEqual(result["path"], "mcp")
        self.assertEqual(result["facts_integrity"], self.document["integrity"])
        self.assertEqual(result["cells"], queries.cells(self.document, plan))
        answer = "\n".join(result["pages"])
        for value in ("ExampleCloud", "192.0.2.10"):
            self.assertIn(value, answer, case["id"])

    def test_mcp_outage_known_facts(self):
        case = MCP["mcp-outage-known-facts"]
        plan = queries.validate_plan(raw((["host-a"], ["public_ipv4"], "explicit", "list")), self.document)
        result = self.call(plan, mock.Mock(side_effect=OSError("synthetic interface outage")))
        self.assertEqual(result["path"], "internal", case["id"])
        self.assertEqual(result["pages"], queries.render(self.document, plan))
        self.assertIn("192.0.2.10", "\n".join(result["pages"]))

    def test_mcp_natural_reply_parity_frozen_obligations_all_original_variants(self):
        case = MCP["mcp-natural-reply-parity"]
        choices = [(["host-a", "host-b", "host-c"], ["label", "public_ipv4", "provider"], "explicit", "table"),
                   (["project-alpha", "project-beta"], ["label"], "explicit", "names"),
                   (["host-c"], ["public_ipv4", "outbound_ipv4"], "explicit", "list")]
        for index, question in enumerate(case["variants"]):
            candidate = raw(choices[index])
            candidate["detail"] = "detailed" if index == 2 else "concise"
            plan = queries.validate_plan(candidate, self.document)
            lang = common.detect_lang(question) or "en"
            with self.subTest(question=question):
                result = self.call(plan, lang=lang)
                self.assertEqual(result["path"], "mcp")
                self.assertEqual(result["cells"], queries.cells(self.document, plan))
                self.assertEqual(result["pages"], queries.render(self.document, plan, lang))
                self.assertNotIn("jsonrpc", "\n".join(result["pages"]))
                if index == 0:
                    self.assertIn("|", result["pages"][0])
                elif index == 1:
                    self.assertEqual(result["pages"], ["Project Alpha\nProject Beta"])
                else:
                    self.assertIn("2026-06-01", "\n".join(result["pages"]))

    def test_mcp_contract_not_business_completion(self):
        case = MCP["mcp-contract-not-business-completion"]
        plan = queries.validate_plan(raw((["host-a"], ["public_ipv4"], "explicit", "list")), self.document)
        def cpu_only(messages):
            responses = exchange(adapter(self.document), messages)
            responses[2]["result"]["structuredContent"]["cells"] = [{"cpu_percent": 0}]
            return responses
        result = self.call(plan, cpu_only)
        self.assertEqual(result["path"], "internal", case["id"])
        self.assertIn("192.0.2.10", "\n".join(result["pages"]))
        self.assertNotIn("CPU", "\n".join(result["pages"]))


# Curated source-ID crosswalk for release reporting. These addresses identify
# executed assertions, not qualification receipts; use actual isolated results.
ORIGINAL_CASE_TESTS = {
    **{identity: "test_fleet_business_corpus.OriginalCatalogPipeline.test_" + identity.replace("-", "_")
       for identity in CHOICES},
    "mixed-catalog-runtime": "test_fleet_business_corpus.OriginalCatalogPipeline.test_mixed_catalog_runtime_preserves_both_requested_answer_obligations",
    **{identity: "test_fleet_business_corpus.OriginalCleanupCorpus.test_original_cleanup_variants_preserve_names_and_remove_only_narrated_gestures"
       for identity in ("bold-name-preserved", "single-emphasis-preserved", "mixed-roleplay-emphasis")},
    **{identity: "test_fleet_business_extended.OriginalRuntimeBusiness.test_" + identity.replace("-", "_")
       for identity in ("deployment-versus-progress", "runtime-alpha-only", "explicit-diagnostics",
          "stale-health", "backups-timer-only", "application-vs-container", "proxy-load-vs-host",
          "inactive-one-shot", "unknown-unit", "short-sample-stability", "false-cited-backup-success")},
    **{identity: "test_fleet_business_extended.OriginalFactsBusiness.test_" + identity.replace("-", "_")
       for identity in ("unsupported-only-project", "sync-date-not-evidence", "publish-rollback", "historical-conflict")},
    **{identity: "test_fleet_business_extended.OriginalOwnerFlowBusiness.test_" + identity.replace("-", "_")
       for identity in ("brevity-preference", "detail-override", "unrelated-task")},
    "source-edit": "test_fleet_business_extended.OriginalAsyncBusiness.test_source_edit",
    "slow-model-responsiveness": "test_fleet_business_extended.OriginalAsyncBusiness.test_slow_model_responsiveness",
    "pending-completion": "test_fleet_business_extended.OriginalPendingCompletionBusiness.test_pending_completion",
    "queue-deadlines": "test_fleet_business_extended.OriginalQueueBusiness.test_queue_deadlines",
    "ambiguous-effect": "test_fleet_business_extended.OriginalEffectRecoveryBusiness.test_ambiguous_effect",
    "purge-late-output": "test_fleet_business_extended.OriginalPurgeBusiness.test_purge_late_output",
}
MCP_CASE_TESTS = {
    **{identity: "test_fleet_business_corpus.AdditionalFactsCorpus.test_" + identity.replace("-", "_")
       for identity in ("mcp-catalog-parity", "mcp-outage-known-facts", "mcp-contract-not-business-completion")},
    "mcp-natural-reply-parity": "test_fleet_business_corpus.AdditionalFactsCorpus.test_mcp_natural_reply_parity_frozen_obligations_all_original_variants",
    "mcp-disabled-original-flows": "test_fleet_mcp_business.MCPDisabledOriginalBusiness.test_mcp_disabled_original_flows",
    **{identity: "test_fleet_mcp_business.MCPMeaningAndAuthorityBusiness.test_" + identity.replace("-", "_")
       for identity in ("mcp-unsupported-version-schema", "mcp-caller-scope", "mcp-discovery-injection", "mcp-no-write-surface")},
    **{identity: "test_fleet_mcp_business.MCPRetainedBusiness.test_" + identity.replace("-", "_")
       for identity in ("mcp-project-isolation", "mcp-no-hidden-content")},
    "mcp-async-responsiveness": "test_fleet_mcp_business.MCPAsyncBusiness.test_mcp_async_responsiveness",
    **{identity: "test_fleet_mcp_business.MCPDurableBusiness.test_" + identity.replace("-", "_")
       for identity in ("mcp-reconnect-no-duplicate", "mcp-tasks-optional", "mcp-cancel-truthful")},
}


if __name__ == "__main__":
    unittest.main()
