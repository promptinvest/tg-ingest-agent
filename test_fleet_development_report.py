"""Development sample completeness survives independently of evidence freshness."""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

import fleet
import fleet_answers
import fleet_facts
import fleet_investigation
import fleet_mcp
import fleet_mcp_client
import fleet_project_adapters as adapters
import fleet_protocol as protocol
import fleet_report
import llm
import test_fleet_async as asynchronous
import test_fleet_catalog as catalog
import test_fleet_mcp as wire
import test_fleet_mcp_complete_runtime_parity as parity
import test_fleet_project_adapters as snapshots

FRESH = snapshots.NOW
QUESTION = "What do the recorded repository, CI and issue evidence say about development progress for Project Alpha? Give a detailed table and keep deployment and operational health separate."


def snapshot(document=None, at=None):
    return adapters.development_actions_snapshot(
        document or snapshots.actions_development_document(), "project-alpha",
        snapshots.SOURCE, snapshots.REPO, at or protocol.now(), 3600)


def evidence(state, detail="detailed", presentation="table"):
    inventory = asynchronous.inventory()
    scope = {"version": protocol.READ_VERSION, "kind": "projects",
             "projects": ["project-alpha"], "capabilities": ["development"]}
    plan = dict(operation="inspect", service="", hosts=["host-a"], projects=["project-alpha"],
                facets=["projects"], focus=["progress"], scope="explicit",
                read_scope=scope, detail=detail, presentation=presentation)
    at = protocol.now()
    result = {"version": protocol.VERSION, "observed_at": at, "read_scope": scope,
              "hosts": {"host-a": {"host": "host-a", "observed_at": at, "read_scope": scope,
                        "projects": {"project-alpha": {"development": state}}}}}
    protocol.result(result, ["host-a"], read_scope=scope)
    return result, inventory, plan, fleet_report.build(result, inventory, plan)


def blocked(*_args, **_kwargs):
    raise AssertionError("read/render proof must not perform external I/O")


class DevelopmentCoverageAnswers(unittest.TestCase):
    def test_stale_ci_and_issues_disclose_incompleteness_in_both_languages(self):
        state = snapshot()
        before = copy.deepcopy(state)
        self.assertEqual(state["gaps"]["ci"], "stale")
        self.assertEqual(state["gaps"]["issues"], "stale")
        for ru in (False, True):
            with self.subTest(ru=ru):
                answer = fleet_report._dated_project_summary("development", state, ru)
                for required in (("CI: данные устарели", "задачи: данные устарели",
                                  "CI: неполная выборка", "задачи: неполная выборка") if ru else
                                 ("CI: stale evidence", "issues: stale evidence",
                                  "CI: partial coverage", "issues: partial coverage")):
                    self.assertIn(required, answer)
                self.assertIn("2026-10-24 11:50:00 UTC", answer)
                self.assertIn("2026-10-24 11:20:00 UTC", answer)
                self.assertIn("Example / Project Alpha: keep useful names", answer)
        self.assertEqual(state, before)

    def test_fresh_partial_coverage_is_not_duplicated(self):
        state = snapshot(at=FRESH)
        self.assertEqual(state["gaps"], {"ci": "partial_coverage", "issues": "partial_coverage"})
        answer = fleet_report._dated_project_summary("development", state)
        self.assertEqual(answer.count("CI: partial coverage"), 1)
        self.assertEqual(answer.count("issues: partial coverage"), 1)
        self.assertNotIn("stale evidence", answer)

    def test_stale_complete_samples_do_not_gain_partial_coverage(self):
        document = snapshots.actions_development_document()
        for name in ("ci", "issues"):
            document[name]["complete"] = True
        state = snapshot(document)
        answer = fleet_report._dated_project_summary("development", state)
        self.assertIn("CI: stale evidence", answer)
        self.assertIn("issues: stale evidence", answer)
        self.assertNotIn("partial coverage", answer)

    def test_unconnected_sources_remain_unconnected_without_false_coverage(self):
        document = snapshots.actions_development_document()
        document["ci"] = dict(source_format=adapters.ACTIONS_SOURCE, status="not_connected",
                              captured_at=None, items=[], complete=False)
        document["issues"] = dict(status="not_connected", captured_at=None, items=[], complete=False)
        answer = fleet_report._dated_project_summary("development", snapshot(document))
        self.assertIn("CI: not connected", answer)
        self.assertIn("issues: not connected", answer)
        self.assertNotIn("partial coverage", answer)

    def test_mixed_fresh_and_stale_samples_keep_each_coverage_once(self):
        document = snapshots.actions_development_document()
        document["exported_at"] = "2026-10-24T11:59:55Z"
        document["ci"]["captured_at"] = "2026-10-24T11:59:50Z"
        state = adapters.development_actions_snapshot(document, "project-alpha", snapshots.SOURCE,
                                                      snapshots.REPO, FRESH, 60)
        self.assertEqual(state["gaps"]["ci"], "partial_coverage")
        self.assertEqual(state["gaps"]["issues"], "stale")
        answer = fleet_report._dated_project_summary("development", state)
        self.assertEqual(answer.count("CI: partial coverage"), 1)
        self.assertEqual(answer.count("issues: partial coverage"), 1)
        self.assertIn("issues: stale evidence", answer)

    def test_wrong_head_qualifier_and_stale_coverage_survive_together(self):
        document = snapshots.actions_development_document()
        document["ci"]["items"][0]["revision"] = "b" * 40
        state = snapshot(document)
        answer = fleet_report._dated_project_summary("development", state)
        for required in ("CI: stale evidence", "CI: partial coverage",
                         "issues: partial coverage", "CI does not cover recorded revision"):
            self.assertIn(required, answer)

    def test_detailed_table_and_concise_answers_preserve_dates_coverage_and_raw(self):
        state = snapshot()
        for lang in ("en", "ru"):
            for detail in ("concise", "detailed"):
                with self.subTest(lang=lang, detail=detail):
                    result, _inventory, plan, report = evidence(state, detail)
                    before = copy.deepcopy((state, result, report))
                    answer = "\n".join(fleet_answers.render(report, None, plan, lang))
                    for required in ("Project Alpha", "example/project-alpha", "CI", "2026-10-24", "|"):
                        self.assertIn(required, answer)
                    self.assertIn("неполная выборка" if lang == "ru" else "partial coverage", answer)
                    self.assertIn("данные устарели" if lang == "ru" else "stale evidence", answer)
                    self.assertNotIn("development complete", answer.lower())
                    self.assertEqual((state, result, report), before)

    def test_backups_output_is_unchanged_by_development_coverage_rule(self):
        state = snapshots.backup_project()
        before = copy.deepcopy(state)
        answer = fleet_report._dated_project_summary("backups", state)
        self.assertIn("Dated backup results", answer)
        self.assertIn("successful backup", answer)
        self.assertNotIn("partial coverage", answer)
        self.assertEqual(state, before)

    def test_retained_mcp_projects_return_identical_typed_evidence_and_answer(self):
        result, inventory, plan, report = evidence(snapshot())
        expected = fleet_answers.render(report, None, plan)
        harness = parity.fixture("retained")
        try:
            payload = wire.reference_payload()
            payload["plan"] = dict(plan, read_contract_hash=protocol.digest(protocol.capability_contract()))
            payload["result"] = copy.deepcopy(result)
            harness.update(payload)
            at = datetime.now(timezone.utc)
            harness.writer.execute("UPDATE fleet_requests SET created_at=?,updated_at=?", (at.isoformat(), at.isoformat()))
            harness.writer.commit()
            harness.policy["allowed_capabilities"] = ["development"]
            reader = fleet_mcp.RetainedReferences(harness.policy, clock=lambda: at)
            document = wire.fixture()
            server = wire.adapter(document, references=harness.policy)
            server.references.clock = lambda: at
            config = wire.configured(document)
            config["references"] = copy.deepcopy(harness.policy)
            cfg = SimpleNamespace(fleet_mcp_enabled=True, fleet_mcp_config="/etc/example/mcp.json")
            args = dict(request_id=wire.REFERENCE_ID, cursor="", max_age_seconds=90,
                        projects=["project-alpha"], capabilities=["development"])
            before = harness.path.read_bytes()
            with mock.patch.object(fleet_facts, "load", return_value=document), \
                    mock.patch.object(fleet_mcp, "read_config", return_value=config), \
                    mock.patch.object(llm, "transport_chat", side_effect=blocked), \
                    mock.patch.object(fleet_mcp_client.subprocess, "Popen", side_effect=blocked):
                direct = reader.read("project_observe", args)
                via = fleet_mcp_client.reference(cfg, "project_observe", args, reader,
                                                exchange=lambda messages: wire.exchange(server, messages))
            self.assertEqual(via["path"], "mcp")
            self.assertEqual(via["result"], direct)
            self.assertEqual(len(direct["observations"]), 1)
            cell = direct["observations"][0]
            self.assertEqual(cell["observation"], result["hosts"]["host-a"]["projects"]["project-alpha"]["development"])
            recovered = copy.deepcopy(result)
            recovered["hosts"]["host-a"]["projects"]["project-alpha"]["development"] = cell["observation"]
            # Both consume the persisted canonical observation order.
            canonical = json.loads(protocol.canonical(result))
            rebuilt = fleet_report.build(recovered, inventory, plan)
            baseline = fleet_report.build(canonical, inventory, plan)
            self.assertEqual(fleet_answers.render(rebuilt, None, plan),
                             fleet_answers.render(baseline, None, plan))
            self.assertIn("partial coverage", "\n".join(expected))
            self.assertEqual(harness.path.read_bytes(), before)
        finally:
            harness.doCleanups()

    def test_actual_retained_owner_loop_keeps_staleness_and_incomplete_sample(self):
        real_submit = fleet.submit
        test = catalog.CatalogDispatch()
        test.setUp()
        try:
            inventory = asynchronous.inventory()
            Path(test.cfg.fleet_inventory).write_text(protocol.canonical(inventory), encoding="utf-8")
            test.turn(1, QUESTION, dispatch=False)
            handle = fleet_investigation.start(test.agent, chat_id=111, source_update=1,
                                              caller_key="dated-development-coverage", lang="en")
            request_id = handle["request_id"]
            def row():
                return test.conn.execute("SELECT * FROM fleet_requests WHERE id=?", (request_id,)).fetchone()
            def payload():
                return json.loads(row()["payload"])
            raw = dict(operation="inspect", service="", hosts=["host-a"], projects=["project-alpha"],
                       facets=["projects"], focus=["progress"], scope="explicit",
                       read_scope=dict(version=protocol.READ_VERSION, kind="projects",
                                       projects=["project-alpha"], capabilities=["development"]),
                       presentation="table", detail="detailed", clarification="")
            def advance(state):
                for _ in range(24):
                    if row()["state"] == state:
                        return
                    test.agent.check_fleet_requests()
                    for call in list(getattr(test.agent, "_fleet_inference_calls", {}).values()):
                        if call.future is not None:
                            call.future.result(timeout=3)
                self.fail("owner loop did not reach " + state + ": " + row()["state"])
            with mock.patch.object(fleet, "submit", side_effect=real_submit), \
                    mock.patch.object(llm, "transport_chat", return_value=test.completion(raw)) as planning:
                advance("queued")
            self.assertEqual(planning.call_count, 1)
            self.assertEqual(payload()["plan"]["presentation"], "table")
            state = snapshot(at=protocol.now())
            value, _, _, _ = evidence(state)
            with mock.patch("worker_client.poll", return_value=value):
                test.agent.check_fleet_requests()
            self.assertEqual(row()["state"], "synthesis_queued")
            with mock.patch.object(llm, "transport_chat", return_value=test.completion(
                    dict(highlights=["O1"], hypotheses=[], next_checks=[]))) as synthesis:
                advance("done")
            self.assertEqual(synthesis.call_count, 1)
            final = payload()
            answer = "\n".join(final["answer_pages"])
            for required in ("Project Alpha", "CI: stale evidence", "issues: stale evidence",
                             "CI: partial coverage", "issues: partial coverage", "source updated", "|"):
                self.assertIn(required, answer)
            self.assertEqual(final["result"], value)
            self.assertEqual(final["model_calls"], 2)
            self.assertEqual(test.sent, [])
            self.assertEqual(final["retained_delivery"]["telegram_attempts"], 0)
            saved = fleet_investigation.result(test.agent, chat_id=111, source_update=1,
                                              caller_key="dated-development-coverage")
            self.assertEqual(saved["pages"], final["answer_pages"])
            self.assertEqual(test.conn.execute(
                "SELECT count(*) FROM fleet_events WHERE request_id=? AND kind='retained_result_recorded'",
                (request_id,)).fetchone()[0], 1)
        finally:
            test.tearDown()