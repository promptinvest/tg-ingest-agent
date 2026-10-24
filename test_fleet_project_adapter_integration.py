"""CF05 root-bound adapters retain business evidence across gateway/receipts/replies."""
import copy
import io
import json
from types import SimpleNamespace
import unittest
from unittest import mock
import urllib.error

import cara_fleet
import fleet_answers
import fleet_host
import fleet_project_adapters as adapters
import fleet_protocol as protocol
import fleet_report
import test_fleet_project_adapters as fixture
import test_fleet_project_scope as scoped


class Response:
    def __init__(self, body, status=200):
        self.body, self.status, self.closed = body, status, False
    def read(self, maximum):
        if maximum != 8193:
            raise AssertionError("unbounded health read")
        return self.body
    def __enter__(self):
        return self
    def __exit__(self, *_args):
        self.closed = True


def config():
    value = scoped.config()
    value["project_adapters"] = {"project-alpha": {
        "http_health": {"adapter": adapters.ADAPTERS["daily_lingo_http"]["implementation_version"]},
        "backups": fixture.config(path="/protected/alpha-backup.json"),
        "development": fixture.config("development", path="/protected/alpha-development.json")},
        "project-beta": {"backups": fixture.config(path="/protected/BETA-MUST-NOT-READ.json")}}
    return value


def operation(capabilities=("backups", "development")):
    return protocol.scoped_inspection(scoped.inventory(), ["host-a"], ["project-alpha"], list(capabilities))


class ProjectAdapterIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.clock = mock.patch.object(protocol, "now", return_value=fixture.NOW)
        self.age = mock.patch.object(protocol, "age", return_value=0)
        self.clock.start(); self.age.start()
        self.addCleanup(self.clock.stop); self.addCleanup(self.age.stop)
        self.documents = {"/protected/alpha-backup.json": fixture.backup_document(),
                          "/protected/alpha-development.json": fixture.development_document()}

    def gateway(self, request, policy=None):
        return fleet_host.run(policy or config(), request)

    def full_read(self, request=None):
        request = request or operation()
        def remote(_transport, host, value):
            self.assertEqual(host, "host-a")
            return self.gateway(value)
        with mock.patch.object(adapters, "_read_fixed_json", side_effect=lambda path: copy.deepcopy(self.documents[path])) as reader, \
                mock.patch.object(cara_fleet, "remote", side_effect=remote), \
                mock.patch.object(fleet_host, "read", side_effect=AssertionError("unselected manifest")), \
                mock.patch.object(fleet_host, "command", side_effect=AssertionError("unselected command")), \
                mock.patch.object(fleet_host, "resources", side_effect=AssertionError("host resources")):
            value = cara_fleet.inspect(scoped.inventory(), {}, request)
        self.assertEqual({call.args[0] for call in reader.call_args_list}, set(self.documents))
        protocol.result(value, request["hosts"], read_scope=request["read_scope"])
        return value

    def plan(self, request, focus=("backups", "progress")):
        return {**request, "projects": ["project-alpha"], "focus": list(focus)}

    def test_registered_new_versions_are_explicit_alternatives_to_legacy(self):
        contract = protocol.capability_contract()
        for capability, kind in (("http_health", "daily_lingo_http"), ("backups", "backups"), ("development", "development")):
            old = protocol.PROJECT_CAPABILITIES[capability]["implementation_version"]
            new = adapters.ADAPTERS[kind]["implementation_version"]
            self.assertEqual(set(contract[capability]["supported_implementations"]), {old, new})
            self.assertEqual(protocol.capability_implementation(capability, new)["outputs"], adapters.ADAPTERS[kind]["outputs"])
            with self.assertRaises(ValueError):
                protocol.capability_implementation(capability, "unknown/v99")
        self.assertEqual(protocol.PROJECT_CAPABILITIES["http_health"]["implementation_version"], "project-http-health/v1")
        contract["development"]["supported_implementations"][adapters.ADAPTERS["development"]["implementation_version"]].append("environment")
        self.assertNotIn("environment", protocol.capability_contract()["development"]["supported_implementations"][adapters.ADAPTERS["development"]["implementation_version"]])

    def test_discovery_has_no_source_io_and_declares_exact_selected_versions(self):
        request = operation()
        with mock.patch.object(adapters, "_read_fixed_json", side_effect=AssertionError("discovery read")), \
                mock.patch.object(fleet_host, "command", side_effect=AssertionError("discovery command")):
            value = self.gateway({"operation": "capabilities", "read_scope": request["read_scope"]})
        protocol.read_discovery(value, "host-a", request["read_scope"])
        self.assertEqual(set(value["capabilities"]), {"project-alpha"})
        for capability in ("backups", "development"):
            self.assertEqual(value["capabilities"]["project-alpha"][capability], {"configured": True,
                             "implementation_version": adapters.ADAPTERS[capability]["implementation_version"]})

    def test_selected_backup_development_reads_are_dated_and_never_collect_colocated_data(self):
        value = self.full_read()
        data = value["hosts"]["host-a"]["projects"]["project-alpha"]
        self.assertEqual(data["backups"]["status"], "recorded")
        self.assertEqual(data["development"]["status"], "recorded")
        self.assertEqual(data["backups"]["observed_at"], fixture.NOW)
        self.assertEqual(data["backups"]["backup"]["finished_at"], fixture.BACKUP_AT)
        self.assertEqual(data["development"]["exported_at"], fixture.EXPORTED)
        self.assertNotIn("project-beta", protocol.canonical(value))
        self.assertNotIn("/protected", protocol.canonical(value))
        self.assertNotIn(fixture.ARCHIVE, protocol.canonical(value))

    def test_business_reply_keeps_newest_failed_attempt_older_success_and_restore_scope(self):
        self.documents["/protected/alpha-backup.json"]["jobs"].append(fixture.job(status="failed", created="2026-10-24T11:45:00Z", finished="2026-10-24T11:50:00Z"))
        request = operation(); value = self.full_read(request)
        report = fleet_report.build(value, scoped.inventory(), self.plan(request))
        answer = "\n".join(fleet_answers.render(report, None, self.plan(request)))
        for fact in ("Dated backup results", "successful backup 2026-10-24 11:30:00 UTC", "restore verification 2026-10-24 11:40:00 UTC",
                     "latest attempt backup=failed", "latest attempt failed", "latest recorded backup", "source exported 2026-10-24 11:55:00 UTC", "read 2026-10-24 12:00:00 UTC"):
            self.assertIn(fact, answer)
        self.assertNotIn("fresh backup results are not connected", answer)
        self.assertNotIn("backup is healthy", answer)
        records = [item for item in report["observations"].values() if item.get("capability") == "backups"]
        self.assertEqual(records[0]["source"], adapters.BACKUP_SOURCE)
        self.assertEqual(records[0]["source_exported_at"], fixture.EXPORTED)
        self.assertEqual(records[0]["observed_at"], fixture.NOW)

    def test_development_reply_keeps_revision_ci_issue_names_and_partial_coverage(self):
        request = operation(); value = self.full_read(request)
        report = fleet_report.build(value, scoped.inventory(), self.plan(request))
        answer = "\n".join(fleet_answers.render(report, None, self.plan(request)))
        for fact in (fixture.REPO, fixture.REVISION, "#101 success", "#7 Example / Project Alpha: keep useful names (open)", "partial coverage"):
            self.assertIn(fact, answer)
        self.assertNotIn("development complete", answer)
        self.assertNotIn("repository, CI and issues are not connected", answer)
        self.assertIn("Example / Project Alpha", "\n".join(fleet_report.render(report, lang="ru")))

    def test_ci_other_revision_and_old_restore_are_explicit_evidence_gaps(self):
        self.documents["/protected/alpha-development.json"]["ci"]["items"][0]["revision"] = "b" * 40
        self.documents["/protected/alpha-backup.json"]["jobs"].append(fixture.job(created="2026-10-24T11:45:00Z", finished="2026-10-24T11:50:00Z", archive="ingest-20261024T114500Z.db.gz"))
        request = operation(); report = fleet_report.build(self.full_read(request), scoped.inventory(), self.plan(request))
        answer = "\n".join(fleet_report.render(report))
        self.assertIn("CI does not cover recorded revision", answer)
        self.assertIn("different/unknown backup", answer)
        self.assertIn("latest recorded backup lacks matching restore verification", answer)

    def test_unbound_backups_development_keep_original_gap_contract(self):
        request = operation()
        with mock.patch.object(adapters, "_read_fixed_json", side_effect=AssertionError("unbound IO")):
            value = self.gateway({"operation": "inspect", "facets": ["projects"], "read_scope": request["read_scope"]}, scoped.config())
        for capability, observation in value["projects"]["project-alpha"].items():
            self.assertEqual(observation["status"], "not_configured")
            self.assertEqual(observation["implementation_version"], protocol.PROJECT_CAPABILITIES[capability]["implementation_version"])

    def health(self, body, *, status=200, policy=None, error=False):
        request = operation(("http_health",))
        response = Response(body if isinstance(body, bytes) else json.dumps(body).encode(), status)
        opener = SimpleNamespace(open=mock.Mock(return_value=response))
        if error:
            opener.open.side_effect = urllib.error.HTTPError("http://127.0.0.1:8000/health", status, "fixture", {}, io.BytesIO(response.body))
        with mock.patch.object(fleet_host.urllib.request, "build_opener", return_value=opener):
            value = self.gateway({"operation": "inspect", "facets": ["projects"], "read_scope": request["read_scope"]}, policy)
        return value["projects"]["project-alpha"]["http_health"], opener

    def test_nested_application_health_retains_failure_and_unexposed_operational_fields(self):
        state, opener = self.health({"status": "degraded", "database": "ok", "app_env": "SYNTHETIC_PRIVATE_ENV",
            "workers": {"delivery": {"status": "ok", "seconds_since_progress": 8}, "intake": {"status": "failed", "seconds_since_progress": 5, "reason": "SYNTHETIC_PRIVATE_REASON"}}, "day_budget": 999})
        self.assertEqual(state["implementation_version"], adapters.ADAPTERS["daily_lingo_http"]["implementation_version"])
        self.assertEqual(state["health"]["intake"], "failed")
        self.assertEqual(state["worker_progress"], {"delivery": 8, "intake": 5})
        for key in ("generation", "reserves", "target", "day_budget"):
            self.assertEqual(state["gaps"][key], "not_exposed")
        self.assertNotIn("SYNTHETIC_PRIVATE", protocol.canonical(state))
        self.assertIn("Application health failure", fleet_report._project_summary("http_health", state)[2])
        opener.open.assert_called_once_with("http://127.0.0.1:8000/health", timeout=mock.ANY)

    def test_http503_still_preserves_typed_nested_health_and_worker_failure(self):
        state, _ = self.health({"status": "degraded", "database": "error", "workers": {"intake": {"status": "stalled", "seconds_since_progress": 60}}}, status=503, error=True)
        self.assertEqual(state["http_status"], 503)
        self.assertEqual(state["health"]["intake"], "stalled")
        self.assertIn("Application health failure", fleet_report._project_summary("http_health", state)[2])

    def test_generic_health_keeps_legacy_flat_schema_and_public_non_json_endpoint(self):
        state, _ = self.health({"status": "ok", "database": "ok", "workers": {"intake": {"status": "failed", "seconds_since_progress": 9}}}, policy=scoped.config())
        self.assertEqual(state["implementation_version"], "project-http-health/v1")
        self.assertEqual(state["health"], {"status": "ok", "database": "ok"})
        self.assertNotIn("worker_progress", state)
        policy = scoped.config(); policy["health"]["project-alpha"] = "https://public.example.invalid/"
        with mock.patch.object(fleet_host, "HEALTH_HOSTS", frozenset({"127.0.0.1", "public.example.invalid"})):
            state, _ = self.health(b"not-json", policy=policy)
        self.assertEqual(state["status"], "observed")
        self.assertEqual(state["http_status"], 200)
        self.assertNotIn("health", state)

    def test_application_optin_cannot_expand_hosts_resources_or_raw_json_schema(self):
        policy = config(); policy["health"]["project-alpha"] = "https://public.example.invalid/"
        with mock.patch.object(fleet_host, "HEALTH_HOSTS", frozenset({"127.0.0.1", "public.example.invalid"})), \
                mock.patch.object(fleet_host.urllib.request, "build_opener", side_effect=AssertionError("wrong application source")):
            state, _unused = None, None
            result = self.gateway({"operation": "inspect", "facets": ["projects"], "read_scope": operation(("http_health",))["read_scope"]}, policy)
        self.assertEqual(result["projects"]["project-alpha"]["http_health"]["status"], "unavailable")
        for raw in (b'{"status":"ok","status":"degraded"}', b'{"status":NaN}', b'x'*8193):
            state, _ = self.health(raw)
            self.assertEqual(state["status"], "unavailable")
            self.assertEqual(state["implementation_version"], adapters.ADAPTERS["daily_lingo_http"]["implementation_version"])

    def test_output_version_swap_is_rejected_after_discovery_without_legacy_fallback(self):
        request = operation(("http_health",))
        calls = []
        def remote(_transport, _host, incoming):
            calls.append(incoming["operation"])
            if incoming["operation"] == "capabilities":
                return self.gateway(incoming)
            # This is a valid old HTTP observation, but contradicts the exact
            # new implementation the same gateway just advertised.
            return self.gateway(incoming, scoped.config())
        with mock.patch.object(cara_fleet, "remote", side_effect=remote), \
                mock.patch.object(fleet_host.urllib.request, "build_opener", return_value=SimpleNamespace(open=mock.Mock(return_value=Response(b'{"status":"ok"}')))):
            value = cara_fleet.inspect(scoped.inventory(), {}, request)
        self.assertEqual(calls, ["capabilities", "inspect"])
        state = value["hosts"]["host-a"]["projects"]["project-alpha"]["http_health"]
        self.assertEqual(state["status"], "unavailable")
        self.assertEqual(state["implementation_version"], adapters.ADAPTERS["daily_lingo_http"]["implementation_version"])
        self.assertNotIn("health", state)

    def test_qualified_nested_result_passes_new_envelope_and_legacy_depth_stays_eight(self):
        request = operation(); value = self.full_read(request)
        with self.assertRaises(ValueError):
            protocol.safe_tree(value)  # old aggregate depth cannot hold CI items
        self.assertIs(protocol.result(value, request["hosts"], read_scope=request["read_scope"]), value)
        nested = 0
        for _ in range(9):
            nested = {"value": nested}
        with self.assertRaises(ValueError):
            protocol.transport_value(nested)
        bad = copy.deepcopy(value)
        state = bad["hosts"]["host-a"]["projects"]["project-alpha"]["development"]
        state["issues"]["items"][0]["environment"] = "SYNTHETIC_SECRET"
        with self.assertRaises(ValueError):
            protocol.result(bad, request["hosts"], read_scope=request["read_scope"])

    def test_project_load_question_gets_project_metrics_gap_without_host_cpu_substitution(self):
        request = operation(("runtime",))
        scope = request["read_scope"]
        state = protocol.capability_observation("runtime", "observed", units={"alpha-job.service": {"LoadState": "loaded", "ActiveState": "inactive", "Type": "oneshot", "Result": "success", "expected_state": "idle"}})
        value = {"version": protocol.VERSION, "observed_at": fixture.NOW, "read_scope": scope, "hosts": {"host-a": {"host": "host-a", "observed_at": fixture.NOW, "read_scope": scope, "projects": {"project-alpha": {"runtime": state}}}}}
        plan = self.plan(request, ("cpu",)); report = fleet_report.build(value, scoped.inventory(), plan)
        answer = "\n".join(fleet_answers.render(report, None, plan))
        self.assertIn("project request/traffic and CPU/RAM/disk usage metrics are not connected", answer)
        self.assertIn("host totals do not establish this project's load", answer)
        self.assertNotIn("CPU 0", answer)
        self.assertFalse(any(item["status"] == "observed" and item["field"].startswith("resources") for item in report["observations"].values()))

    def test_scoped_remote_accepts_bounded_large_typed_payload_legacy_keeps10000(self):
        projects = ["project-" + letter for letter in "abcd"]
        scope = {"version": protocol.READ_VERSION, "kind": "projects", "projects": projects, "capabilities": ["development"]}
        document = fixture.development_document()
        document["issues"]["items"] = [{"number": i+1, "title": "Example ticket " + "x"*120, "state": "open", "updated_at": "2026-10-24T11:20:00Z"} for i in range(12)]
        observations = {}
        for project in projects:
            source = copy.deepcopy(document); source["project_id"] = project
            observations[project] = {"development": adapters.development_snapshot(source, project, fixture.SOURCE, fixture.REPO, fixture.NOW, 3600)}
        value = {"host": "host-a", "observed_at": fixture.NOW, "read_scope": scope, "projects": observations}
        raw = protocol.canonical(value).encode()
        self.assertGreater(len(raw), 10000); self.assertLess(len(raw), 60000)
        transport = {"host-a": {"address": "192.0.2.10", "port": 22, "key": "/etc/cara-fleet/fixture-key", "known_hosts": "/etc/cara-fleet/fixture-hosts"}}
        completed = SimpleNamespace(returncode=0, stdout=raw)
        with mock.patch.object(cara_fleet.subprocess, "run", return_value=completed):
            self.assertEqual(cara_fleet.remote(transport, "host-a", {"operation": "inspect", "facets": ["projects"], "read_scope": scope}), value)
            with self.assertRaises(ValueError):
                cara_fleet.remote(transport, "host-a", {"operation": "inspect", "facets": ["projects"]})
        with mock.patch.object(cara_fleet.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=b'x'*60001)):
            with self.assertRaises(ValueError):
                cara_fleet.remote(transport, "host-a", {"operation": "inspect", "facets": ["projects"], "read_scope": scope})


if __name__ == "__main__":
    unittest.main()
