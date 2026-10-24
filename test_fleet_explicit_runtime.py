"""Real Agent scope/runtime and relevance contracts; synthetic transports only."""
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest import mock

import cara_fleet
import fleet_answers
import fleet_host
import fleet_project_adapters as adapters
import fleet_protocol as protocol
import fleet_report
import llm
import worker_client
import test_fleet_async as asynchronous
import test_fleet_monitor_adapters as monitor
import test_fleet_monitor_integration as monitor_flow


QUESTION = ("По последнему датированному наблюдению Project Alpha: включена ли генерация, "
            "каковы резервы и дневной лимит? Ответь кратко по-русски и укажи дату.")


def runtime_plan(**changes):
    value = {"hosts": ["host-a"], "facets": ["projects"], "clarification": "",
        "operation": "inspect", "service": "", "scope": "explicit",
        "projects": ["project-alpha"], "focus": ["overview"],
        "read_scope": {"version": protocol.READ_VERSION, "kind": "projects",
                       "projects": ["project-alpha"], "capabilities": ["runtime"]},
        "presentation": "prose", "detail": "concise"}
    value.update(changes)
    return value


class ExplicitDatedRuntime(unittest.TestCase):
    def fixture(self):
        case = asynchronous.FleetAsyncExecution()
        case.setUp()
        self.addCleanup(case.tearDown)
        inventory = asynchronous.inventory()
        inventory["projects"]["project-beta"] = {"label": "Project Beta", "hosts": ["host-a"],
            "recorded_at": "2026-10-24", "notes": "Unrelated colocated application"}
        Path(case.cfg.fleet_inventory).write_text(protocol.canonical(inventory), encoding="utf-8")
        return case, inventory

    def queue(self, case):
        case.source(1, QUESTION)
        with mock.patch.object(llm, "transport_chat", return_value=asynchronous.completion(runtime_plan())) as send:
            case.agent.do_fleet(111, "ru", QUESTION)
            case.drain()
        self.assertEqual(send.call_count, 1)
        return case.agent._fleet_recent(111, QUESTION)

    def test_full_agent_prompt_and_strict_validator_distinguish_evidence_time_from_prior(self):
        case, inventory = self.fixture()
        messages = case.agent._fleet_planning_messages(QUESTION, inventory, {}, "ru")
        data = json.loads(messages[1]["content"])
        self.assertEqual(data["prior_investigation"], {})
        self.assertEqual(data["owner_question"], QUESTION)
        self.assertIn("inherit is unavailable", messages[0]["content"])
        self.assertIn("operational scope rule does not replace the local catalog prior_catalog contract", messages[0]["content"])
        self.assertIn("Evidence recency never supplies conversational prior scope", messages[0]["content"])
        failed = runtime_plan(scope="inherit", facets=["resources"])
        before = copy.deepcopy(failed)
        for prior in ({}, None, {"inventory_hash": "0" * 64, "hosts": ["host-a"]}):
            with self.subTest(prior=prior), self.assertRaisesRegex(ValueError, "no current fleet conversation scope"):
                case.agent._fleet_validate_planning(protocol.canonical(failed), inventory, prior)
        self.assertEqual(failed, before)
        valid = case.agent._fleet_validate_planning(protocol.canonical(runtime_plan()), inventory, {})
        self.assertEqual(valid["scope"], "explicit")
        self.assertEqual(valid["facets"], ["projects"])
        self.assertEqual(valid["read_scope"]["capabilities"], ["runtime"])
        self.assertEqual(valid["projects"], ["project-alpha"])
        self.assertEqual(case.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 0)

    def test_named_dated_ru_question_reaches_only_selected_runtime_and_useful_answer(self):
        case, inventory = self.fixture()
        documents = monitor_flow.documents("daily_lingo_observer")
        policy = monitor_flow.SelectedMonitorBusiness().policy("daily_lingo_observer")
        reads, remotes, results = [], [], []
        def read(path, maximum_bytes=adapters.MAX_SOURCE_BYTES):
            self.assertEqual(maximum_bytes, 2 * 1024 * 1024)
            self.assertIn(path, documents)
            reads.append(path)
            return copy.deepcopy(documents[path])
        def remote(_transport, host, request):
            self.assertEqual(host, "host-a")
            self.assertEqual(request["read_scope"], runtime_plan()["read_scope"])
            remotes.append(copy.deepcopy(request))
            return fleet_host.run(policy, request)
        def transport(prepared):
            return asynchronous.completion(runtime_plan() if prepared["skill"] == "fleet_planner" else
                {"highlights": [], "hypotheses": [], "next_checks": []})
        def poll(*_args, **_kwargs):
            operation = case.payload()["operation"]
            self.assertEqual(operation["facets"], ["projects"])
            self.assertEqual(operation["read_scope"], runtime_plan()["read_scope"])
            value = cara_fleet.inspect(inventory, {}, operation)
            results.append(copy.deepcopy(value))
            return value
        with mock.patch.object(protocol, "now", return_value=monitor.NOW), \
                mock.patch.object(protocol, "age", return_value=0), \
                mock.patch.object(llm, "transport_chat", side_effect=transport) as model, \
                mock.patch.object(adapters, "_read_fixed_json", side_effect=read), \
                mock.patch.object(cara_fleet, "remote", side_effect=remote), \
                mock.patch.object(fleet_host, "resources", side_effect=AssertionError("unselected host resources")), \
                mock.patch.object(fleet_host, "read", side_effect=AssertionError("unselected project manifest")), \
                mock.patch.object(fleet_host, "command", side_effect=AssertionError("new probe or effect")), \
                mock.patch.object(fleet_host, "unit_state", side_effect=AssertionError("unselected unit")):
            case.source(1, QUESTION)
            case.agent.do_fleet(111, "ru", QUESTION)
            case.drain()
            with mock.patch.object(worker_client, "poll", side_effect=poll):
                case.drain("done")
        self.assertEqual(model.call_count, 2)
        self.assertEqual(case.payload()["model_calls"], 2)
        self.assertEqual(case.payload()["plan"]["scope"], "explicit")
        self.assertEqual([item["operation"] for item in remotes], ["capabilities", "inspect"])
        self.assertEqual(len(results), 1)
        self.assertEqual(set(results[0]["hosts"]["host-a"]["projects"]), {"project-alpha"})
        state = results[0]["hosts"]["host-a"]["projects"]["project-alpha"]["runtime"]
        self.assertEqual(state["source_observed_at"], monitor.CAPTURED)
        self.assertNotIn("resources", results[0]["hosts"]["host-a"])
        self.assertEqual(reads, ["/fixed/observer/latest-result.json",
            "/fixed/observer/snapshots/" + monitor.STAMP + ".json", "/fixed/observer/latest-result.json"])
        answer = case.payload()["answer"]
        for fact in ("Project Alpha", "enabled", "nb/A2", "USD17", "2026-10-24", "11:59:30"):
            self.assertIn(fact, answer)
        self.assertIn("Снимок:", answer)
        for unrelated in ("Project Beta", "CPU", "disk", monitor.CANARY):
            self.assertNotIn(unrelated, answer)
        self.assertEqual(case.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 1)

    def test_genuine_pronoun_and_format_followups_keep_canonical_runtime_obligations(self):
        case, inventory = self.fixture()
        previous = self.queue(case)
        self.assertEqual(previous["projects"], ["project-alpha"])
        raw = runtime_plan(scope="inherit", hosts=[], projects=[], answer_mode="reformat", presentation="table")
        plan = case.agent._fleet_validate_planning(protocol.canonical(raw), inventory, previous)
        self.assertEqual(plan["scope"], "inherit")
        self.assertEqual(plan["answer_mode"], "reformat")
        self.assertEqual(plan["hosts"], previous["hosts"])
        self.assertEqual(plan["projects"], previous["projects"])
        self.assertEqual(plan["read_scope"], previous["read_scope"])
        frame = case.agent._fleet_context_frame("Put that in a table", plan, inventory, previous, 2)
        self.assertEqual(frame["original_question"], QUESTION)
        self.assertEqual(frame["read_scope"], previous["read_scope"])
        self.assertEqual(case.conn.execute("SELECT count(*) FROM llm_usage").fetchone()[0], 1)

    def test_edited_owner_source_removes_prior_and_still_rejects_inherit(self):
        case, inventory = self.fixture()
        self.assertTrue(self.queue(case))
        case.conn.execute("UPDATE conversation SET text='Changed source' WHERE update_id=1")
        case.conn.commit()
        previous = case.agent._fleet_recent(111, QUESTION)
        self.assertEqual(previous, {})
        with self.assertRaisesRegex(ValueError, "no current fleet conversation scope"):
            case.agent._fleet_validate_planning(protocol.canonical(runtime_plan(scope="inherit")), inventory, previous)
        self.assertEqual(case.conn.execute("SELECT count(*) FROM llm_usage").fetchone()[0], 1)

    def test_expired_prior_is_not_revived_by_latest_observation_words(self):
        case, inventory = self.fixture()
        self.assertTrue(self.queue(case))
        old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        case.conn.execute("UPDATE fleet_requests SET created_at=?", (old,))
        case.conn.commit()
        previous = case.agent._fleet_recent(111, QUESTION)
        self.assertEqual(previous, {})
        with self.assertRaisesRegex(ValueError, "no current fleet conversation scope"):
            case.agent._fleet_validate_planning(protocol.canonical(runtime_plan(scope="inherit")), inventory, previous)
        self.assertEqual(case.conn.execute("SELECT count(*) FROM llm_usage").fetchone()[0], 1)

    def test_memory_only_focus_keeps_ram_process_gap_date_without_unrelated_dump(self):
        case, inventory = self.fixture()
        raw = asynchronous.plan()
        raw.update(scope="explicit", projects=[], focus=["memory"], detail="concise")
        plan = case.agent._fleet_validate_planning(protocol.canonical(raw), inventory, {})
        self.assertEqual(plan["focus"], ["memory"])
        report = fleet_report.build(asynchronous.response(), inventory, plan)
        decision = {"highlights": [], "hypotheses": [{"host": "host-a", "kind": "longer_sample"}], "next_checks": []}
        answer = "\n".join(fleet_answers.render(report, decision, plan, "en"))
        for required in ("RAM", "30%", "process measurements unavailable", "Snapshot:"):
            self.assertIn(required, answer)
        for unrelated in ("CPU", "disk", "Project Alpha", "Project Beta", "Possible cause", "Longer measurements"):
            self.assertNotIn(unrelated, answer)
        self.assertTrue(any(item["field"] == "resources.cpu_percent" for item in report["observations"].values()))
        self.assertTrue(any(item["field"] == "resources.top_memory" and item["status"] == "unavailable"
                            for item in report["observations"].values()))

    def test_memory_plan_without_read_scope_collects_only_host_resources_through_real_spool(self):
        case, inventory = self.fixture()
        question = "Investigate the current memory usage on Example application host and give a concise answer."
        raw = asynchronous.plan()
        raw.update(scope="explicit", projects=[], focus=["memory"], detail="concise")
        self.assertNotIn("read_scope", raw)
        scope = {"version": protocol.READ_VERSION, "kind": "host", "projects": [], "capabilities": []}
        policy = monitor_flow.SelectedMonitorBusiness().policy("daily_lingo_observer")
        remotes = []
        def remote(_transport, host, request):
            self.assertEqual(host, "host-a")
            self.assertEqual(request["read_scope"], scope)
            self.assertIn(request["operation"], {"capabilities", "inspect"})
            if request["operation"] == "inspect":
                self.assertEqual(request["facets"], ["resources"])
            remotes.append(copy.deepcopy(request))
            return fleet_host.run(policy, request)
        def transport(prepared):
            self.assertIn(prepared["skill"], {"fleet_planner", "fleet_synthesis"})
            return asynchronous.completion(raw if prepared["skill"] == "fleet_planner" else
                {"highlights": [], "hypotheses": [], "next_checks": []})
        forbidden = AssertionError("unselected project, HTTP, container, unit or model path")
        with mock.patch.object(llm, "transport_chat", side_effect=transport) as model, \
                mock.patch.object(llm, "chat_profile", side_effect=forbidden) as synchronous_model, \
                mock.patch.object(cara_fleet, "remote", side_effect=remote), \
                mock.patch.object(fleet_host, "resources", return_value={"memory_used_percent": 30}) as resources, \
                mock.patch.object(fleet_host, "projects", side_effect=forbidden) as project_manifests, \
                mock.patch.object(fleet_host, "_project_read", side_effect=forbidden) as project_read, \
                mock.patch.object(fleet_host, "read", side_effect=forbidden) as fixed_read, \
                mock.patch.object(fleet_host, "command", side_effect=forbidden) as command, \
                mock.patch.object(fleet_host, "unit_state", side_effect=forbidden) as unit_state, \
                mock.patch.object(fleet_host.urllib.request, "build_opener", side_effect=forbidden) as http, \
                mock.patch.object(adapters, "read_bound", side_effect=forbidden) as bound_read, \
                mock.patch.object(adapters, "_read_fixed_json", side_effect=forbidden) as adapter_source:
            case.source(1, question)
            case.agent.do_fleet(111, "en", question)
            case.drain()
            operation = case.payload()["operation"]
            self.assertEqual(operation["read_scope"], scope)
            self.assertEqual(operation["hosts"], ["host-a"])
            self.assertEqual(operation["facets"], ["resources"])
            requests = list((case.root / "spool/requests").glob("w_*.json"))
            self.assertEqual(len(requests), 1)
            envelope = json.loads(requests[0].read_text())
            self.assertEqual(envelope["input"], operation)
            self.assertEqual(envelope["input_hash"], protocol.digest(operation))
            config = case.root / "executor-config"
            config.mkdir()
            (config / "inventory.json").write_text(protocol.canonical(inventory), encoding="utf-8")
            (config / "transport.json").write_text("{}", encoding="utf-8")
            cara_fleet.run_once(case.root, config)
            receipt = json.loads((case.root / "spool/results" / requests[0].name).read_text())
            self.assertEqual(receipt["status"], "ok")
            host = receipt["result"]["hosts"]["host-a"]
            self.assertEqual(set(host), {"host", "observed_at", "read_scope", "resources"})
            self.assertEqual(host["resources"], {"memory_used_percent": 30})
            self.assertEqual(host["read_scope"], scope)
            case.drain("done")
        resources.assert_called_once_with()
        for guard in (synchronous_model, project_manifests, project_read, fixed_read, command,
                      unit_state, http, bound_read, adapter_source):
            guard.assert_not_called()
        self.assertEqual([request["operation"] for request in remotes], ["capabilities", "inspect"])
        self.assertEqual(model.call_count, 2)
        self.assertEqual(case.payload()["model_calls"], 2)
        self.assertEqual(case.payload()["result"], receipt["result"])
        self.assertEqual(case.payload()["plan"]["focus"], ["memory"])
        answer = case.payload()["answer"]
        for required in ("RAM", "30%", "process measurements unavailable", "Snapshot:"):
            self.assertIn(required, answer)
        for unrelated in ("CPU", "disk", "Project Alpha", "Project Beta", "Possible cause", "Longer measurements"):
            self.assertNotIn(unrelated, answer)
        self.assertEqual(case.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 1)
        self.assertFalse((case.root / "consumed").exists())

    def test_explicit_broad_resources_plus_memory_retains_existing_overview_semantics(self):
        case, inventory = self.fixture()
        raw = asynchronous.plan()
        raw.update(scope="explicit", projects=[], focus=["resources", "memory"], detail="concise")
        plan = case.agent._fleet_validate_planning(protocol.canonical(raw), inventory, {})
        self.assertEqual(plan["focus"], ["resources", "memory"])
        report = fleet_report.build(asynchronous.response(), inventory, plan)
        decision = {"highlights": [], "hypotheses": [{"host": "host-a", "kind": "longer_sample"}], "next_checks": []}
        answer = "\n".join(fleet_answers.render(report, decision, plan, "en"))
        for expected in ("CPU", "RAM", "30%", "disk", "Project Alpha", "Possible cause", "Longer measurements"):
            self.assertIn(expected, answer)
