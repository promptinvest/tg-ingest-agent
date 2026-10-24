"""Versioned project reads retain business scope across protocol/gateway/report."""
import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

import cara_fleet
import fleet_host
import fleet_protocol as protocol
import fleet_report


def inventory():
    return {"version": protocol.VERSION,
            "hosts": {host: {"label": "Example " + host, "role": "Project Alpha and Project Beta",
                              "aliases": [], "restart_services": []} for host in ("host-a", "host-b")},
            "projects": {"project-alpha": {"label": "Project Alpha", "hosts": ["host-a", "host-b"], "recorded_at": "2026-10-20", "notes": "Alpha recorded deployment."},
                         "project-beta": {"label": "Project Beta", "hosts": ["host-a"], "recorded_at": "2026-10-20", "notes": "Beta private project record."}}}


def config(host="host-a"):
    return {"host": host, "services": ["unrelated.service"],
            "projects": {"project-alpha": ["/fixed/alpha.json"], "project-beta": ["/fixed/beta.json"]},
            "containers": {"project-alpha": ["alpha-app"], "project-beta": ["beta-app"]},
            "health": {"project-alpha": "http://127.0.0.1:8000/health", "project-beta": "http://127.0.0.1:8001/health"},
            "project_services": {"project-alpha": {"alpha-job.service": "idle"}, "project-beta": {"beta.service": "active"}}}


def operation(capabilities=("release",), hosts=("host-a",), projects=("project-alpha",), kind="projects"):
    return protocol.scoped_inspection(inventory(), hosts, projects, capabilities, kind)


def manifest(path):
    if path not in {"/fixed/alpha.json", "/fixed/beta.json"}:
        raise AssertionError("unexpected fixed file read")
    return json.dumps({"build_version": "alpha-build" if "alpha" in path else "beta-build", "source_revision": "a" * 64,
                       "status": "verified", "environment": "RAW_SECRET_CANARY"})


def gateway(request, host="host-a"):
    return fleet_host.run(config(host), {key: value for key, value in request.items() if key not in {"hosts", "inventory_hash"}})


class ProjectScopeTests(unittest.TestCase):
    def test_selected_scope_carries_only_closed_ids_and_capabilities(self):
        request = operation(("release", "http_health"))
        self.assertEqual(request["facets"], ["projects"])
        self.assertEqual(request["read_scope"]["projects"], ["project-alpha"])
        self.assertEqual(protocol.request(request, inventory()), request)
        contract = protocol.capability_contract()
        self.assertTrue(all(value["inputs"] == [] for value in contract.values()))
        contract["release"]["outputs"].append("environment")
        self.assertNotIn("environment", protocol.capability_contract()["release"]["outputs"])

    def test_unknown_ids_versions_fields_and_host_broadening_fail_closed(self):
        for update in ({"projects": ["unknown-project"]}, {"projects": ["/etc/private"]},
                       {"capabilities": ["arbitrary_shell"]}, {"version": "unsupported/v99"},
                       {"url": "https://example.test/private"}, {"projects": ["project-alpha", "project-alpha"]}):
            request = operation()
            request["read_scope"].update(update)
            with self.subTest(update=update), self.assertRaises(ValueError):
                protocol.request(request, inventory())
        for facets in (["projects", "services"], ["projects", "resources"], ["projects", "errors"]):
            request = operation()
            request["facets"] = facets
            with self.subTest(facets=facets), self.assertRaises(ValueError):
                protocol.request(request, inventory())
        with self.assertRaises(ValueError):
            operation(hosts=["host-b"], projects=["project-beta"])

    def test_selected_release_does_not_read_colocated_files_or_other_facets(self):
        request = operation()
        with mock.patch.object(fleet_host, "read", side_effect=manifest) as read, \
                mock.patch.object(fleet_host, "resources") as resources, \
                mock.patch.object(fleet_host, "unit_state") as units, \
                mock.patch.object(fleet_host, "command") as command:
            result = gateway(request)
        read.assert_called_once_with("/fixed/alpha.json")
        resources.assert_not_called(); units.assert_not_called(); command.assert_not_called()
        self.assertEqual(set(result["projects"]), {"project-alpha"})
        state = result["projects"]["project-alpha"]["release"]
        self.assertEqual(state["status"], "observed")
        self.assertEqual(state["release_status"], "verified")
        self.assertEqual(state["source_revision"], "a" * 12)
        self.assertNotIn("RAW_SECRET_CANARY", protocol.canonical(result))
        self.assertNotIn("project-beta", protocol.canonical(result))

    def test_discovery_is_selected_and_does_not_execute_a_probe(self):
        scope = operation()["read_scope"]
        with mock.patch.object(fleet_host, "read") as read, mock.patch.object(fleet_host, "command") as command:
            result = fleet_host.run(config(), {"operation": "capabilities", "read_scope": scope})
        read.assert_not_called(); command.assert_not_called()
        protocol.read_discovery(result, "host-a", scope)
        self.assertEqual(set(result["capabilities"]), {"project-alpha"})
        self.assertNotIn("project-beta", protocol.canonical(result))

    def test_executor_propagates_scope_and_multi_host_project_coverage(self):
        request = operation(hosts=["host-a", "host-b"])
        calls = []
        def remote(_transport, host, incoming):
            calls.append((host, incoming))
            return fleet_host.run(config(host), incoming)
        with mock.patch.object(cara_fleet, "remote", side_effect=remote), mock.patch.object(fleet_host, "read", side_effect=manifest):
            result = cara_fleet.execute(inventory(), {}, request)
        protocol.result(result, request["hosts"], read_scope=request["read_scope"])
        self.assertEqual(set(result["hosts"]), {"host-a", "host-b"})
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(incoming["read_scope"]["projects"] == ["project-alpha"] for _, incoming in calls))
        self.assertNotIn("project-beta", protocol.canonical(result))

    def test_old_gateway_yields_precise_gap_without_legacy_fallback(self):
        request = operation()
        with mock.patch.object(cara_fleet, "remote", side_effect=ValueError("old gateway")) as remote:
            result = cara_fleet.execute(inventory(), {}, request)
        self.assertEqual(remote.call_count, 1)
        self.assertEqual(remote.call_args.args[2]["operation"], "capabilities")
        state = result["hosts"]["host-a"]["projects"]["project-alpha"]["release"]
        self.assertEqual(state["status"], "unavailable")
        self.assertEqual(state["reason"], "gateway_contract_unavailable")
        protocol.result(result, request["hosts"], read_scope=request["read_scope"])

    def test_foreign_gateway_project_is_not_retained_in_receipt(self):
        request = operation()
        discovery = fleet_host.discover(config(), request["read_scope"])
        with mock.patch.object(fleet_host, "read", side_effect=manifest):
            broad = gateway(request)
        broad["projects"]["project-beta"] = {"release": protocol.capability_observation("release", "observed", build_version="BETA_CANARY")}
        with mock.patch.object(cara_fleet, "remote", side_effect=[discovery, broad]):
            result = cara_fleet.execute(inventory(), {}, request)
        self.assertNotIn("project-beta", protocol.canonical(result))
        self.assertNotIn("BETA_CANARY", protocol.canonical(result))

    def test_host_infrastructure_scope_and_all_projects_are_distinct(self):
        host_request = protocol.scoped_inspection(inventory(), ["host-a"], [], [], "host", facets=["services"])
        with mock.patch.object(fleet_host, "unit_state", return_value={"LoadState": "loaded", "ActiveState": "active", "InvocationID": "x"}), \
                mock.patch.object(fleet_host, "fingerprint", return_value="a" * 64), \
                mock.patch.object(fleet_host, "projects") as projects:
            host_result = gateway(host_request)
        projects.assert_not_called()
        self.assertIn("services", host_result)
        self.assertNotIn("projects", host_result)
        all_request = operation(projects=["project-alpha", "project-beta"], kind="all_projects")
        with mock.patch.object(fleet_host, "read", side_effect=manifest) as read:
            result = gateway(all_request)
        self.assertEqual(set(result["projects"]), {"project-alpha", "project-beta"})
        self.assertEqual(read.call_count, 2)
        with self.assertRaises(ValueError):
            operation(projects=["project-alpha"], kind="all_projects")

    def test_missing_and_unavailable_and_unconfigured_are_distinct(self):
        request = operation()
        with mock.patch.object(fleet_host, "read", side_effect=FileNotFoundError):
            missing = gateway(request)
        with mock.patch.object(fleet_host, "read", side_effect=ValueError):
            unavailable = gateway(request)
        no_probe = config(); no_probe["projects"] = {}
        unconfigured = fleet_host.run(no_probe, {"operation": "inspect", "facets": ["projects"], "read_scope": request["read_scope"]})
        self.assertEqual([value["projects"]["project-alpha"]["release"]["status"] for value in (missing, unavailable, unconfigured)],
                         ["not_found", "unavailable", "not_configured"])

    def test_backup_and_development_gaps_never_read_timers_or_repositories(self):
        request = operation(["backups", "development"])
        with mock.patch.object(fleet_host, "read") as read, mock.patch.object(fleet_host, "command") as command:
            raw = gateway(request)
        read.assert_not_called(); command.assert_not_called()
        result = {"version": protocol.VERSION, "observed_at": protocol.now(), "hosts": {"host-a": raw}, "read_scope": request["read_scope"]}
        report = fleet_report.build(result, inventory(), dict(request, focus=["backups", "progress"]))
        answer = "\n".join(fleet_report.render(report))
        self.assertIn("verified backup results: not connected", answer)
        self.assertIn("repository, CI and issues: not connected", answer)
        self.assertNotIn("backup succeeded", answer)
        self.assertNotIn("Project Beta", protocol.canonical(report))

    def test_application_failure_remains_visible_despite_running_container(self):
        request = operation(["containers", "http_health"])
        response = mock.MagicMock()
        response.__enter__.return_value = SimpleNamespace(status=503, read=lambda _n: b'{"status":"failed","database":false,"environment":"RAW_SECRET_CANARY"}')
        opener = mock.Mock(); opener.open.return_value = response
        def command(args, timeout=5):
            self.assertEqual(args[-1], "alpha-app")
            return json.dumps({"Status": "running", "Running": True, "Environment": "RAW_SECRET_CANARY"}) if ".State" in args[-2] else "a" * 64
        with mock.patch.object(fleet_host.urllib.request, "build_opener", return_value=opener), mock.patch.object(fleet_host, "command", side_effect=command):
            raw = gateway(request)
        result = {"version": protocol.VERSION, "observed_at": protocol.now(), "hosts": {"host-a": raw}, "read_scope": request["read_scope"]}
        protocol.result(result, request["hosts"], read_scope=request["read_scope"])
        report = fleet_report.build(result, inventory(), request)
        answer = "\n".join(fleet_report.render(report))
        self.assertIn("Application health failure", answer)
        self.assertIn("running 1/1 observed", answer)
        self.assertNotIn("RAW_SECRET_CANARY", protocol.canonical(result))
        self.assertNotIn("Project Beta", protocol.canonical(report))

    def test_scoped_runtime_distinguishes_expected_idle_missing_unavailable_failed(self):
        request = operation(["runtime"])
        policy = config()
        policy["project_services"]["project-alpha"] = {unit: "unknown" for unit in ("idle.service", "missing.service", "unknown.service", "failed.service")}
        states = {"idle.service": {"LoadState": "loaded", "ActiveState": "inactive", "Type": "oneshot", "Result": "success"},
                  "missing.service": {"LoadState": "not-found", "ActiveState": "inactive"},
                  "unknown.service": {"ActiveState": "inactive"},
                  "failed.service": {"LoadState": "loaded", "ActiveState": "failed", "Result": "exit-code"}}
        with mock.patch.object(fleet_host, "unit_state", side_effect=lambda unit, timeout=5: dict(states[unit])) as units:
            raw = fleet_host.run(policy, {"operation": "inspect", "facets": ["projects"], "read_scope": request["read_scope"]})
        self.assertEqual(units.call_count, 4)
        result = {"version": protocol.VERSION, "observed_at": protocol.now(), "hosts": {"host-a": raw}, "read_scope": request["read_scope"]}
        answer = "\n".join(fleet_report.render(fleet_report.build(result, inventory(), request)))
        self.assertIn("idle.service: expected idle", answer)
        self.assertIn("missing.service: not found", answer)
        self.assertIn("unknown.service: unavailable", answer)
        self.assertIn("failed.service: failed", answer)
        self.assertNotIn("idle.service: failed", answer)

    def test_final_report_and_optional_checks_retain_project_scope(self):
        request = operation()
        with mock.patch.object(fleet_host, "read", side_effect=manifest):
            raw = gateway(request)
        result = {"version": protocol.VERSION, "observed_at": protocol.now(), "hosts": {"host-a": raw}, "read_scope": request["read_scope"]}
        report = fleet_report.build(result, inventory(), request)
        self.assertEqual(set(report["records"]), {"project-alpha"})
        self.assertNotIn("Beta private", protocol.canonical(report))
        decision = fleet_report.selection({"highlights": [], "hypotheses": [], "next_checks": [{"host": "host-a", "facet": "services"}, {"host": "host-a", "facet": "projects"}]}, report)
        self.assertEqual(decision["next_checks"], [{"host": "host-a", "facet": "projects"}])
        self.assertTrue(all(item.get("project") == "project-alpha" for item in report["observations"].values()))

    def test_stale_or_arbitrary_capability_output_fails_validation(self):
        request = operation()
        with mock.patch.object(fleet_host, "read", side_effect=manifest):
            raw = gateway(request)
        for field, value in (("environment", "RAW_SECRET_CANARY"), ("observed_at", (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()),
                             ("build_version", {"path": "/arbitrary/private"})):
            invalid = copy.deepcopy(raw)
            invalid["projects"]["project-alpha"]["release"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                protocol.scoped_host_result(invalid, "host-a", request["read_scope"], ["projects"])

    def test_time_budget_exhaustion_never_starts_manifest_read(self):
        request = operation()
        with mock.patch.object(fleet_host.time, "monotonic", side_effect=[0, 31]), mock.patch.object(fleet_host, "read") as read:
            raw = gateway(request)
        read.assert_not_called()
        self.assertEqual(raw["projects"]["project-alpha"]["release"]["reason"], "time_limit")

    def test_legacy_envelopes_and_gateway_dispatch_remain_compatible(self):
        request = {"operation": "inspect", "hosts": ["host-a"], "facets": ["projects"], "inventory_hash": protocol.digest(inventory())}
        legacy = {"host": "host-a", "observed_at": protocol.now(), "projects": {"project-alpha": {"build_version": "legacy"}}}
        with mock.patch.object(cara_fleet, "remote", return_value=legacy) as remote:
            result = cara_fleet.execute(inventory(), {}, request)
        self.assertEqual(remote.call_count, 1)
        self.assertEqual(remote.call_args.args[2], {"operation": "inspect", "facets": ["projects"]})
        self.assertEqual(set(result), {"version", "observed_at", "hosts"})
        protocol.result(result, ["host-a"])
        with mock.patch.object(fleet_host, "projects", return_value={"project-alpha": {"build_version": "legacy"}}) as probe:
            self.assertEqual(fleet_host.run(config(), {"operation": "inspect", "facets": ["projects"]})["projects"], legacy["projects"])
        probe.assert_called_once_with(config())
        restart = {"operation": "restart", "service": "fixture.service"}
        with mock.patch.object(fleet_host, "restart", return_value={"outcome": "unchanged"}) as effect:
            self.assertEqual(fleet_host.run(config(), restart), {"outcome": "unchanged"})
        effect.assert_called_once_with(config(), restart)


if __name__ == "__main__":
    unittest.main()
