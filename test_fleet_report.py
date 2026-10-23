"""Question/answer contracts, adversarial conclusions and complete fleet coverage."""
import copy
import json
import unittest
from unittest import mock

import fleet
import fleet_context
import fleet_host
import fleet_protocol as protocol
import fleet_report as report
import worker_client
import test_fleet as base

inventory, plan = base.inventory, base.plan


def fixture():
    catalog = inventory()
    catalog["hosts"] = {h: dict(catalog["hosts"]["pd"], label=h.upper()) for h in ("pd", "pi", "utah", "armenia", "rus", "exit")}
    catalog["projects"]["proxy"] = {"label": "Proxy", "hosts": ["armenia", "rus"], "recorded_at": "2026-10-21", "notes": "Recorded route, not current protocol health."}
    request = dict(plan(), hosts=list(catalog["hosts"]), facets=["resources", "services", "projects", "errors"], focus=["overview"])
    response = {"version": protocol.VERSION, "observed_at": protocol.now(), "hosts": {}}
    for h in request["hosts"]:
        response["hosts"][h] = {"host": h, "observed_at": protocol.now(),
            "resources": {"cpu_percent": 0.0, "cpu_sample_seconds": .2, "memory_used_percent": 45.5, "disk_used_percent": 34.2},
            "services": {"ssh.service": {"LoadState": "loaded", "ActiveState": "active", "SubState": "running"}},
            "projects": {}, "errors": {"ssh.service": {"warning_or_worse": 0, "sampled": 2, "window_seconds": 3600, "capped": False}}}
    return catalog, request, response


class ReportContracts(unittest.TestCase):
    def test_actual_details_exchange_cannot_omit_three_hosts(self):
        catalog, request, response = fixture()
        built = report.build(response, catalog, request)
        answer = "\n".join(report.render(built, {"highlights": ["O1"], "hypotheses": [], "next_checks": []}, "ru"))
        for host in request["hosts"]:
            self.assertIn(host.upper() + " — " + catalog["hosts"][host]["label"], answer)
        self.assertNotIn("[O", answer)
        self.assertNotIn("[R", answer)
        self.assertNotIn("ActiveState", answer)
        self.assertNotIn("Проектов не развёрнуто", answer)

    def test_missing_host_is_a_visible_gap_including_fallback(self):
        catalog, request, response = fixture()
        response["hosts"]["pi"] = {"available": False}
        built = report.build(response, catalog, request)
        answer = "\n".join(report.render(built, lang="en"))
        self.assertIn("PI — PI\n• fresh observations unavailable", answer)
        self.assertEqual(built["observations"][built["coverage"]["pi"]["findings"][0]]["status"], "unavailable")

    def test_nonexistent_timer_is_not_an_inactive_backup(self):
        catalog, request, response = fixture()
        response["hosts"]["pi"]["services"]["wrong.timer"] = {"LoadState": "not-found", "ActiveState": "inactive", "SubState": "dead"}
        response["hosts"]["pi"]["services"]["real.timer"] = {"LoadState": "loaded", "ActiveState": "active", "SubState": "waiting"}
        built = report.build(response, catalog, request)
        answer = "\n".join(report.render(built))
        self.assertIn("not found: wrong.timer", answer)
        self.assertNotIn("inactive/failed: wrong.timer", answer)
        self.assertNotIn("backup is healthy", answer)

    def test_legacy_service_without_load_state_is_unknown(self):
        catalog, request, response = fixture()
        response["hosts"]["pi"]["services"]["ssh.service"].pop("LoadState")
        built = report.build(response, catalog, request)
        obs = [o for o in built["observations"].values() if o["host"] == "pi" and o["field"] == "services.ssh.service"][0]
        self.assertEqual(obs["status"], "unavailable")

    def test_gateway_requests_load_state(self):
        with mock.patch.object(fleet_host, "command", return_value="LoadState=not-found\nActiveState=inactive\nInvocationID=\n") as run:
            state = fleet_host.unit_state("wrong.timer")
        self.assertIn("LoadState", run.call_args.args[0][-1])
        self.assertEqual(state["LoadState"], "not-found")

    def test_cited_cpu_cannot_become_healthy_backups_or_monthly_stability(self):
        catalog, request, response = fixture()
        built = report.build(response, catalog, request)
        with self.assertRaises(ValueError):
            fleet.render_analysis({"observations": ["O1"], "interpretation": "All backups healthy, stable for a month [O1]", "gaps": [], "next_steps": []}, built, "en")

    def test_hypothesis_requires_actual_process_measurements(self):
        catalog, request, response = fixture()
        built = report.build(response, catalog, request)
        bad = {"highlights": [], "hypotheses": [{"host": "pd", "kind": "memory_consumers"}], "next_checks": []}
        with self.assertRaises(ValueError): report.selection(bad, built)
        request["focus"] = ["memory"]
        response["hosts"]["pd"]["resources"]["top_memory"] = [{"name": "python", "pid": 123, "rss_bytes": 726*1048576, "cpu_one_core_percent": 0}]
        built = report.build(response, catalog, request)
        answer = "\n".join(report.render(built, bad))
        self.assertIn("726.0 MiB RSS", answer)
        self.assertIn("Hypothesis — PD", answer)
        self.assertIn("may explain part", answer)

    def test_log_window_cap_and_unavailability_are_retained(self):
        catalog, request, response = fixture()
        response["hosts"]["pd"]["errors"] = {"ssh": {"warning_or_worse": 7, "window_seconds": 3600, "capped": True}, "app": {"available": False}}
        built = report.build(response, catalog, request)
        answer = "\n".join(report.render(built))
        self.assertIn("at least 7", answer)
        self.assertIn("1/2 configured logs observed", answer)
        self.assertIn("window 3600s", answer)
        self.assertNotIn("no historical failures", answer)

    def test_dated_policy_is_separate_from_current_backup_results(self):
        catalog, request, response = fixture()
        request["focus"] = ["backups", "progress"]
        built = report.build(response, catalog, request)
        answer = "\n".join(report.render(built))
        self.assertIn("fresh backup results are not connected", answer)
        self.assertIn("development progress is unknown", answer)
        self.assertIn("recorded_at", built["records"]["daily-lingo"])

    def test_history_is_two_dated_snapshots_and_never_a_stability_claim(self):
        catalog, request, response = fixture()
        request["focus"] = ["trend"]
        old = copy.deepcopy(response); old["observed_at"] = "2026-10-22T10:00:00+00:00"
        for v in old["hosts"].values():
            v["observed_at"] = old["observed_at"]; v["resources"]["memory_used_percent"] = 40
        answer = "\n".join(report.render(report.build(response, catalog, request, [old])))
        self.assertIn("40% (2026-10-22 10:00:00 UTC)", answer)
        self.assertIn("two-snapshot comparison", answer)
        self.assertIn("do not establish long-term stability", answer)

    def test_model_can_never_add_text_commands_or_extra_hosts(self):
        catalog, request, response = fixture(); built = report.build(response, catalog, request)
        for change in ({"highlights": ["invented"]}, {"highlights": [{}]}, {"interpretation": "healthy"}, {"hypotheses": [{"host": "pd", "kind": []}]}, {"next_checks": [{"host": "retired", "facet": "resources"}]}, {"next_checks": [{"host": "pd", "facet": "shell"}]}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                report.selection(dict(highlights=[], hypotheses=[], next_checks=[], **{}) | change, built)

    def test_large_reports_split_without_losing_findings(self):
        catalog, request, response = fixture()
        for raw in response["hosts"].values():
            raw["services"] = {f"long-unit-name-{i}.service": {"LoadState": "not-found", "ActiveState": "inactive"} for i in range(30)}
        built = report.build(response, catalog, request); pages = report.render(built)
        self.assertGreater(len(pages), 1)
        self.assertTrue(all(len(p) <= 3700 for p in pages))
        for host in request["hosts"]: self.assertIn(host.upper() + " —", "\n".join(pages))

    def test_inherit_and_all_scope_are_resolved_before_execution(self):
        catalog, request, response = fixture()
        previous = fleet_context.frame("List my VPSs", request, catalog, update=1)
        inherited = fleet.validate_plan(dict(plan(), hosts=["pd"], scope="inherit", focus=["memory"], projects=[]), catalog, previous)
        self.assertEqual(inherited["hosts"], request["hosts"])
        self.assertEqual(fleet.validate_plan(dict(plan(), scope="all"), catalog)["hosts"], request["hosts"])
        with self.assertRaises(ValueError): fleet.validate_plan(dict(plan(), scope="inherit"), catalog, {})


class PipelineContracts(unittest.TestCase):
    setUp = base.FleetLifecycle.setUp
    tearDown = base.FleetLifecycle.tearDown
    owner_turn = base.FleetLifecycle.owner_turn
    start = base.FleetLifecycle.start
    result = base.FleetLifecycle.result
    def test_context_is_structured_and_ends_on_unrelated_owner_turn(self):
        row = self.result(self.start())
        self.owner_turn(2, "А память?")
        context = self.agent._fleet_recent(111, "А память?")
        self.assertEqual(context["hosts"], ["pd"])
        self.assertNotIn("answer", context)
        self.owner_turn(3, "Напомни купить молоко")
        self.owner_turn(4, "А память?")
        self.assertEqual(self.agent._fleet_recent(111, "А память?"), {})

    def test_additional_read_is_bounded_source_bound_and_preserves_first_facets(self):
        self.agent.cfg.task_model_call_limit = 4
        row = self.start(); initial = json.loads(row["payload"])
        first = {"version": protocol.VERSION, "observed_at": protocol.now(), "hosts": {"pd": {"host": "pd", "observed_at": protocol.now(), "resources": {"cpu_percent": 12.5, "cpu_sample_seconds": .2, "memory_used_percent": 45, "disk_used_percent": 34}, "projects": {}}}}
        decision = json.dumps({"highlights": [], "hypotheses": [], "next_checks": [{"host": "pd", "facet": "errors"}]})
        with mock.patch.object(worker_client, "poll", return_value=first), mock.patch.object(fleet.llm, "chat_profile", return_value=decision): self.agent.check_fleet_requests()
        row = self.conn.execute("SELECT * FROM fleet_requests").fetchone(); payload = json.loads(row["payload"])
        self.assertEqual(row["state"], "queued"); self.assertEqual(payload["inspection_step"], 3)
        second = {"version": protocol.VERSION, "observed_at": protocol.now(), "hosts": {"pd": {"host": "pd", "observed_at": protocol.now(), "errors": {"app": {"warning_or_worse": 2, "window_seconds": 3600}}}}}
        with mock.patch.object(worker_client, "poll", return_value=second) as poll, mock.patch.object(fleet.llm, "chat_profile", return_value=decision): self.agent.check_fleet_requests()
        self.assertEqual(poll.call_args.kwargs["step_id"], 3)
        row = self.conn.execute("SELECT * FROM fleet_requests").fetchone(); payload = json.loads(row["payload"])
        self.assertEqual(row["state"], "done")
        self.assertIn("CPU 12.5%", payload["answer"])
        self.assertIn("warnings/errors: 2", payload["answer"])
        self.assertEqual(payload["model_calls"], 3)
        self.assertEqual(payload["result"]["hosts"]["pd"]["_facet_times"]["resources"], first["hosts"]["pd"]["observed_at"])

    def test_arbitrary_synthesis_prose_falls_back_to_actual_measurements(self):
        row = self.start()
        response = {"version": protocol.VERSION, "observed_at": protocol.now(), "hosts": {"pd": {"host": "pd", "observed_at": protocol.now(), "resources": {"available": False}}}}
        with mock.patch.object(worker_client, "poll", return_value=response), mock.patch.object(fleet.llm, "chat_profile", return_value='{"interpretation":"All backups healthy"}'):
            self.agent.check_fleet_requests()
        payload = json.loads(self.conn.execute("SELECT payload FROM fleet_requests").fetchone()[0])
        self.assertIn("Measurements received", payload["answer"])
        self.assertNotIn("All backups healthy", payload["answer"])
        self.assertEqual(self.conn.execute("SELECT state FROM fleet_requests").fetchone()[0], "done")

    def test_partial_page_delivery_is_ambiguous_and_never_replayed(self):
        row = self.start()
        response = {"version": protocol.VERSION, "observed_at": protocol.now(), "hosts": {"pd": {"host": "pd", "observed_at": protocol.now(), "projects": {}}}}
        with mock.patch.object(worker_client, "poll", return_value=response), mock.patch.object(fleet.llm, "chat_profile", side_effect=ValueError), mock.patch.object(report, "render", return_value=["page one", "page two"]), mock.patch.object(self.agent, "reply", side_effect=[{"message_id": 900}, None, {"message_id": 901}]) as send:
            self.agent.check_fleet_requests()
            count = send.call_count
            self.agent.check_fleet_requests()
            self.assertEqual(send.call_count, count)
        row = self.conn.execute("SELECT * FROM fleet_requests").fetchone()
        self.assertEqual(row["state"], "ambiguous")
        self.assertEqual(json.loads(row["payload"])["result_messages"], [900])


if __name__ == "__main__":
    unittest.main()
