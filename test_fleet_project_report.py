"""Typed application-answer obligations preserve evidence without dumping diagnostics."""
import copy
import unittest
from unittest import mock

import answer_policy
import fleet_answers
import fleet_project_report as presentation
import fleet_protocol as protocol
import fleet_report
import llm
import store
import task_runner
import test_fleet_async as asynchronous
import test_fleet_monitor_adapters as fixture
import test_fleet_monitor_integration as monitor
import test_fleet_task_bridge as bridge

FIELDS = ["generation", "reserves", "day_budget"]


def evidence(fields=None, focus=None, kind="daily_lingo_observer", states=None):
    inventory = asynchronous.inventory()
    scope = {"version": protocol.READ_VERSION, "kind": "projects", "projects": ["project-alpha"], "capabilities": ["runtime"]}
    plan = dict(operation="inspect", hosts=["host-a"], projects=["project-alpha"], facets=["projects"],
                focus=focus or ["resources"], read_scope=scope)
    if fields is not None:
        plan["answer_fields"] = list(fields)
    at = protocol.now()
    state, _ = monitor.BoundMonitorIntegration().read(kind, values=states, now=at)
    value = {"version": protocol.VERSION, "observed_at": at, "read_scope": scope,
             "hosts": {"host-a": {"host": "host-a", "observed_at": at, "read_scope": scope,
                       "projects": {"project-alpha": {"runtime": state}}}}}
    protocol.result(value, ["host-a"], read_scope=scope)
    return fleet_report.build(value, inventory, plan), plan, state


class TypedProjectAnswers(unittest.TestCase):
    def test_answer_field_contract_is_closed_ordered_and_independent(self):
        value = ["day_budget", "generation"]
        result = presentation.validate_answer_fields(value)
        self.assertEqual(result, value)
        self.assertIsNot(result, value)
        self.assertEqual(len(presentation.ANSWER_FIELDS), 9)
        for invalid in (None, "generation", True, [], [True], ["generation", "generation"], ["shell"], list(presentation.ANSWER_FIELD_ORDER) + ["generation"]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                presentation.validate_answer_fields(invalid)

    def test_actual_ru_fields_answer_business_question_without_internal_dump(self):
        report, plan, state = evidence(FIELDS)
        before = copy.deepcopy(report)
        answer = "\n".join(fleet_answers.render(report, None, plan, "ru"))
        for fact in ("Project Alpha", "генерация включена", "резервы", "USD17", "2026-10-24"):
            self.assertIn(fact, answer)
        for cell, count in state["reserves"]["cells"].items():
            self.assertIn(cell + "=" + str(count), answer)
        for unwanted in ("app_health", "postgres_health", "unfinished", "probe", "heartbeat", "CPU/RAM", "цель", "принятый релиз"):
            self.assertNotIn(unwanted, answer)
        self.assertLess(answer.count("2026-10-24"), 5)
        self.assertLess(len(answer), 850)
        self.assertEqual(report, before)
        self.assertTrue(any(item.get("capability") == "project_metrics" for item in report["observations"].values()))

    def test_different_generation_and_reserve_dates_keep_their_meanings(self):
        report, plan, state = evidence(FIELDS)
        answer = "\n".join(fleet_answers.render(report, None, plan))
        self.assertIn("generation " + presentation._date(state["generation"]["as_of"]).strftime("%H:%M:%S"), answer)
        self.assertIn("reserves " + presentation._date(state["reserves"]["as_of"]).strftime("%H:%M:%S"), answer)
        self.assertNotEqual(state["generation"]["as_of"], state["reserves"]["as_of"])
        self.assertNotIn("spent", answer)
        self.assertNotIn("remaining", answer)

    def test_detailed_explicit_fields_keep_only_requested_business_fields(self):
        report, plan, _state = evidence(FIELDS)
        answer = "\n".join(fleet_answers.render(report, None, dict(plan, detail="detailed")))
        for required in ("Project Alpha", "generation enabled", "reserves", "USD17", "read "):
            self.assertIn(required, answer)
        for unwanted in ("app_health", "unfinished_calls", "probe ", "heartbeat ", "project request/traffic", "accepted release"):
            self.assertNotIn(unwanted, answer)

    def test_detailed_without_fields_is_exact_original_full_report(self):
        report, plan, _state = evidence()
        original = copy.deepcopy(report)
        answer = fleet_answers.render(report, None, dict(plan, detail="detailed"))
        self.assertEqual(answer, fleet_report.render(original, dict(highlights=[], hypotheses=[], next_checks=[])))
        for retained in ("app_health", "unfinished_calls", "probe", "heartbeat", "CPU/RAM/disk"):
            self.assertIn(retained, "\n".join(answer))

    def test_no_fields_health_target_job_and_metric_information_is_preserved(self):
        report, plan, _state = evidence()
        answer = "\n".join(fleet_answers.render(report, None, plan))
        for retained in ("app_health=healthy", "postgres_health=healthy", "unfinished_calls", "target", "accepted release", "CPU/RAM/disk"):
            self.assertIn(retained, answer)

    def test_requested_unknown_target_and_release_remain_specific(self):
        report, plan, _state = evidence(["target", "accepted_release"])
        answer = "\n".join(fleet_answers.render(report, None, plan))
        self.assertIn("verified target: not connected", answer)
        self.assertIn("accepted release: not connected", answer)
        self.assertNotIn("generation enabled", answer)
        self.assertNotIn("daily limit", answer)

    def test_explicit_application_health_and_unfinished_work_are_not_hidden(self):
        report, plan, _state = evidence(["application_health", "unfinished_work"])
        answer = "\n".join(fleet_answers.render(report, None, plan))
        for requested in ("application: healthy", "PostgreSQL: healthy", "unfinished calls", "unfinished refills"):
            self.assertIn(requested, answer)
        self.assertNotIn("generation enabled", answer)

    def test_requested_project_metrics_gap_survives(self):
        report, plan, _state = evidence(["project_metrics"])
        answer = "\n".join(fleet_answers.render(report, None, plan))
        self.assertIn("CPU/RAM/disk usage metrics are not connected", answer)
        self.assertNotIn("generation enabled", answer)
        self.assertNotIn("daily limit", answer)

    def test_metric_field_without_accidental_resource_focus_still_has_specific_gap(self):
        report, plan, _state = evidence(["project_metrics"], focus=["overview"])
        answer = "\n".join(fleet_answers.render(report, None, plan))
        self.assertIn("contains no project CPU/RAM/disk measurements", answer)
        self.assertIn("request/traffic load metrics", answer)

    def test_proxy_cannot_supply_application_fields_but_legacy_projection_is_preserved(self):
        report, plan, _state = evidence(kind="authenticated_proxy_route")
        old = "\n".join(fleet_answers.render(report, None, plan))
        projected = "\n".join(fleet_answers.render(report, None, dict(plan, answer_fields=FIELDS)))
        for requested in ("generation: not exposed", "reserves: not exposed", "daily limit: not exposed"):
            self.assertIn(requested, projected)
        for excluded in ("12345", "cumulative", "CPU/RAM/disk", "route"):
            self.assertNotIn(excluded, projected)
        for retained in ("cumulative", "12345", "current_connections", "CPU/RAM/disk", "unique users"):
            self.assertIn(retained, old)

    def test_stale_source_is_not_refreshed_by_current_transport_read(self):
        state, _reader = monitor.BoundMonitorIntegration().read("daily_lingo_observer", now="2026-10-24T15:11:54Z")
        observation = dict(capability="runtime", implementation_version=state["implementation_version"], value=state)
        answer = presentation.render(observation, FIELDS)
        self.assertIn("observation is stale", answer)
        self.assertIn("11:59:30", answer)
        self.assertNotIn("15:11:54", answer)

    def test_missing_budget_observation_is_unavailable_not_stale(self):
        values = monitor.documents("daily_lingo_observer")
        snapshot = next(value for name, value in values.items() if "/snapshots/" in name)
        snapshot["budget_guard"]["heartbeat"] = None
        report, plan, _state = evidence(["day_budget"], states=values)
        answer = "\n".join(fleet_answers.render(report, None, plan))
        self.assertIn("daily limit: unknown", answer)
        self.assertIn("budget observation unavailable", answer)
        self.assertNotIn("stale", answer)

    def test_typed_unavailable_application_has_requested_gap_without_load_dump(self):
        values = monitor.documents("daily_lingo_observer")
        snapshot = next(value for name, value in values.items() if "/snapshots/" in name)
        snapshot["read_only"] = False
        report, plan, _state = evidence(["generation"], states=values)
        answer = "\n".join(fleet_answers.render(report, None, plan))
        self.assertIn("generation: observation unavailable", answer)
        self.assertNotIn("generation enabled", answer)
        self.assertNotIn("CPU/RAM/disk", answer)
        self.assertNotIn("daily limit", answer)
    def test_material_unhealthy_exception_is_dated_and_relevant(self):
        values = monitor.documents("daily_lingo_observer")
        snapshot = next(value for name, value in values.items() if "/snapshots/" in name)
        snapshot["app_health"] = "unhealthy"
        report, plan, _state = evidence(["generation"], states=values)
        answer = "\n".join(fleet_answers.render(report, None, plan))
        self.assertIn("application: unhealthy", answer)
        report, plan, _state = evidence(["day_budget"], states=values)
        self.assertNotIn("application: unhealthy", "\n".join(fleet_answers.render(report, None, plan)))

    def test_confirmed_detail_and_explicit_format_keep_scoped_obligations(self):
        report, plan, _state = evidence(FIELDS)
        with mock.patch.object(answer_policy.persona, "boss_preference_hint", return_value="Detailed answers"):
            answer = "\n".join(fleet_answers.render(report, None, dict(plan, presentation="table"), conn=object()))
        self.assertIn("| Server | Evidence |", answer)
        self.assertIn("USD17", answer)
        self.assertNotIn("app_health", answer)
        self.assertNotIn("CPU/RAM", answer)

    def test_two_projects_metadata_does_not_renumber_old_findings(self):
        inventory = asynchronous.inventory()
        inventory["projects"]["project-beta"] = dict(inventory["projects"]["project-alpha"], label="Project Beta")
        scope = dict(version=protocol.READ_VERSION, kind="projects", projects=["project-alpha", "project-beta"], capabilities=["runtime"])
        plan = dict(hosts=["host-a"], facets=["projects"], focus=["resources"], read_scope=scope, projects=scope["projects"])
        state, _reader = monitor.BoundMonitorIntegration().read("daily_lingo_observer")
        value = dict(version=protocol.VERSION, observed_at=fixture.NOW, read_scope=scope, hosts={"host-a": dict(
            host="host-a", observed_at=fixture.NOW, read_scope=scope, projects={p: {"runtime": state} for p in scope["projects"]})})
        report = fleet_report.build(value, inventory, plan)
        # Changing obligations may rebuild presentation; it cannot remap old IDs.
        plan_with = dict(plan, answer_fields=FIELDS)
        report_with = fleet_report.build(value, inventory, plan_with)
        self.assertEqual(report["observations"], report_with["observations"])
        self.assertEqual(report["findings"], report_with["findings"])
        decision = dict(highlights=["O2"], hypotheses=[], next_checks=[])
        self.assertEqual(report_with["observations"]["O2"]["capability"], "project_metrics")
        answer = "\n".join(fleet_answers.render(report_with, decision, plan_with))
        self.assertNotIn("CPU/RAM", answer)
        self.assertIn("generation enabled", answer)
        self.assertIn("Project Beta", answer)
        self.assertEqual(report_with["observations"]["O3"]["project"], "project-beta")

    def test_same_project_other_host_uses_its_own_supported_or_unavailable_field_answer(self):
        inventory = asynchronous.inventory()
        inventory["hosts"]["host-b"] = dict(inventory["hosts"]["host-a"], label="Example proxy host")
        inventory["projects"]["project-alpha"]["hosts"].append("host-b")
        scope = dict(version=protocol.READ_VERSION, kind="projects", projects=["project-alpha"], capabilities=["runtime"])
        plan = dict(hosts=["host-a", "host-b"], facets=["projects"], focus=["resources"], read_scope=scope, projects=scope["projects"])
        hosts = {}
        for host, kind in (("host-a", "daily_lingo_observer"), ("host-b", "authenticated_proxy_route")):
            state, _reader = monitor.BoundMonitorIntegration().read(kind)
            hosts[host] = dict(host=host, observed_at=fixture.NOW, read_scope=scope, projects={"project-alpha": {"runtime": state}})
        report = fleet_report.build(dict(version=protocol.VERSION, observed_at=fixture.NOW, read_scope=scope, hosts=hosts), inventory, plan)
        old = fleet_answers.render(report, None, plan)
        new = fleet_answers.render(report, None, dict(plan, answer_fields=["generation"]))
        old_line = next(line for line in "\n".join(old).splitlines() if line.startswith("Example proxy host:"))
        new_line = next(line for line in "\n".join(new).splitlines() if line.startswith("Example proxy host:"))
        self.assertIn("CPU/RAM/disk", old_line)
        self.assertIn("cumulative", old_line)
        self.assertIn("generation: not exposed", new_line)
        self.assertNotIn("CPU/RAM/disk", new_line)
        self.assertNotIn("cumulative", new_line)
        self.assertNotIn("CPU/RAM/disk", next(line for line in "\n".join(new).splitlines() if line.startswith("Example application host:")))
    def test_real_task_wait_receipt_and_single_delivery_use_scoped_answer(self):
        test = bridge.FleetTaskBridge();test.setUp()
        try:
            question = "По датированному наблюдению Project Alpha: генерация, резервы и дневной лимит, кратко по-русски с датой."
            task = test.task(question, calls=1, cost=.01)
            scope = {"version": protocol.READ_VERSION, "kind": "projects", "projects": ["project-alpha"], "capabilities": ["runtime"]}
            plan = dict(operation="inspect", hosts=["host-a"], projects=["project-alpha"], facets=["projects"], focus=["resources"],
                        service="", scope="explicit", clarification="", read_scope=scope, answer_fields=FIELDS)
            test.begin(task)
            with mock.patch.object(llm, "transport_chat", return_value=asynchronous.completion(plan)):
                try:
                    test.drain()
                except AssertionError as error:
                    self.fail(str(error) + "; synthetic phase=" + protocol.canonical(test.payload()))
            state, _reader = monitor.BoundMonitorIntegration().read("daily_lingo_observer", now=protocol.now())
            value = dict(version=protocol.VERSION, observed_at=protocol.now(), read_scope=scope, hosts={"host-a": dict(
                host="host-a", observed_at=protocol.now(), read_scope=scope, projects={"project-alpha": {"runtime": state}})})
            with mock.patch("worker_client.poll", return_value=value), mock.patch.object(llm, "transport_chat", return_value=asynchronous.completion(dict(highlights=[], hypotheses=[], next_checks=[]))):
                test.drain("done")
            before = copy.deepcopy(test.payload()["result"])
            task_runner.poll_fleet_results(test.agent, test.conn)
            self.assertEqual(store.assistant_task_get(test.conn, task["id"])["status"], "completed")
            self.assertEqual(len(test.receipts(task)), 1)
            self.assertEqual(test.delivery.call_count, 1)
            self.assertEqual(test.sent, [])
            answer = test.delivery.call_args.args[2]["text"]
            for required in ("Project Alpha", "USD17", "2026-10-24"):
                self.assertIn(required, answer)
            for excluded in ("app_health", "unfinished_calls", "CPU/RAM/disk", "heartbeat", "probe "):
                self.assertNotIn(excluded, answer)
            self.assertLessEqual(len(answer), 450)
            self.assertEqual(test.payload()["result"], before)
            self.assertIn("app_health", before["hosts"]["host-a"]["projects"]["project-alpha"]["runtime"]["application"])
        finally:
            test.tearDown()


if __name__ == "__main__":
    unittest.main()