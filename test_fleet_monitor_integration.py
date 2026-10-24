"""Fixed monitor reads, protected source bounds and selected-project business flow."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cara_fleet
import fleet_answers
import fleet_host
import fleet_monitor_adapters as monitors
import fleet_project_adapters as adapters
import fleet_protocol as protocol
import fleet_report
import test_fleet_monitor_adapters as fixture
import test_fleet_project_scope as scoped

KINDS = ("daily_lingo_observer", "authenticated_proxy_route")


def binding(kind, **changes):
    value = fixture.daily_binding() if kind == "daily_lingo_observer" else fixture.proxy_binding()
    value.update(changes)
    return {"adapter": monitors.ADAPTERS[kind]["implementation_version"], **value}


def documents(kind):
    if kind == "daily_lingo_observer":
        latest, snapshot = fixture.daily_sources()
        return {"/fixed/observer/latest-result.json": latest,
                "/fixed/observer/snapshots/" + fixture.STAMP + ".json": snapshot}
    return {"/fixed/proxy/latest.json": fixture.proxy_source()}


class BoundMonitorIntegration(unittest.TestCase):
    def read(self, kind, config=None, values=None, now=fixture.NOW):
        values = documents(kind) if values is None else values
        def source(path, maximum_bytes=adapters.MAX_SOURCE_BYTES):
            self.assertEqual(maximum_bytes, monitors.MAX_SOURCE_BYTES)
            if path not in values:
                raise AssertionError("unapproved fixed source read")
            return copy.deepcopy(values[path])
        with mock.patch.object(adapters, "_read_fixed_json", side_effect=source) as reader:
            result = adapters.read_bound(kind, binding(kind) if config is None else config, "project-alpha", now)
        return result, reader

    def test_monitor_registry_is_explicit_and_independent(self):
        for kind in KINDS:
            self.assertEqual(adapters.ADAPTERS[kind], monitors.ADAPTERS[kind])
            self.assertIsNot(adapters.ADAPTERS[kind], monitors.ADAPTERS[kind])
            self.assertIsNot(adapters.ADAPTERS[kind]["outputs"], monitors.ADAPTERS[kind]["outputs"])
        self.assertEqual(adapters.MAX_SOURCE_BYTES, 65536)

    def test_outer_root_binding_is_exact_and_versioned_before_io(self):
        for kind in KINDS:
            config = binding(kind)
            self.assertEqual(adapters.binding(config, kind), config)
            for change in ({"adapter": "unknown/v99"}, {"command": "id"}, {"project": "project-beta"},
                           {"max_age_seconds": True}, {"path": "../unapproved"}):
                altered = {**config, **change}
                with self.subTest(kind=kind, change=change), mock.patch.object(adapters, "_read_fixed_json") as reader:
                    self.assertEqual(adapters.read_bound(kind, altered, "project-alpha", fixture.NOW)["status"], "unavailable")
                    reader.assert_not_called()

    def test_daily_reads_only_latest_exact_snapshot_and_same_latest_again(self):
        value, reader = self.read("daily_lingo_observer")
        self.assertEqual([call.args[0] for call in reader.call_args_list], [
            "/fixed/observer/latest-result.json", "/fixed/observer/snapshots/" + fixture.STAMP + ".json",
            "/fixed/observer/latest-result.json"])
        self.assertEqual(value["status"], "recorded")
        self.assertEqual(value["source_observed_at"], fixture.CAPTURED)
        self.assertEqual(value["reserves"]["cells"], fixture.daily_sources()[1]["stock"])
        self.assertEqual(value["budget_guard"]["daily_limit_usd"], 17)
        self.assertNotIn(fixture.CANARY, protocol.canonical(value))
        for field in ("profiles", "jobs", "assignments", "journal_events", "runtime_models"):
            self.assertNotIn(field, value)
        self.assertIs(adapters.validate_observation("daily_lingo_observer", value), value)

    def test_proxy_reads_one_bound_receipt_and_preserves_metric_meanings(self):
        value, reader = self.read("authenticated_proxy_route")
        reader.assert_called_once_with("/fixed/proxy/latest.json", maximum_bytes=monitors.MAX_SOURCE_BYTES)
        self.assertEqual(value["health"], "ok")
        self.assertEqual(value["route"]["status"], "verified")
        self.assertEqual(value["profiles"]["profile-a"]["metric"], "connection_counter")
        self.assertEqual(value["profiles"]["profile-a"]["metric_value"], 12345)
        self.assertEqual(value["profiles"]["profile-b"]["metric"], "current_connections")
        self.assertEqual(value["profiles"]["profile-b"]["metric_value"], 3)
        for private in (fixture.CANARY, "192.0.2.17", "24444", fixture.BOOT, "/fixed/"):
            self.assertNotIn(private, protocol.canonical(value))

    def test_collector_is_one_file_and_cannot_refresh_its_old_host_sample(self):
        old = fixture.proxy_source()
        old.update(started_utc="2026-10-24T11:39:00Z", completed_utc="2026-10-24T11:40:00Z")
        source = {"schema": 1, "collected_utc": fixture.NOW,
                  "hosts": {"edge-alpha": old, "unselected-host": {"private": fixture.CANARY}}}
        value, reader = self.read("authenticated_proxy_route", binding("authenticated_proxy_route", source_kind="collector"),
                                  {"/fixed/proxy/latest.json": source})
        self.assertEqual(reader.call_count, 1)
        self.assertEqual(value["health"], "unknown")
        self.assertEqual(value["source_observed_at"], "2026-10-24T11:40:00Z")
        self.assertEqual(value["collected_at"], fixture.NOW)
        self.assertEqual(value["gaps"]["protocol_observation"], "stale")
        self.assertNotIn("unselected-host", protocol.canonical(value))

    def test_malformed_snapshot_reference_never_opens_a_derived_leaf(self):
        for stamp in ("../private", "/etc/private", "20261324T115930000000Z", fixture.STAMP + "/extra", None):
            latest, _snapshot = fixture.daily_sources()
            latest["snapshot"] = stamp
            with self.subTest(stamp=stamp):
                value, reader = self.read("daily_lingo_observer", values={"/fixed/observer/latest-result.json": latest})
                self.assertEqual(value["status"], "unavailable")
                self.assertEqual(reader.call_count, 1)

    def test_atomic_latest_change_produces_gap_without_following_second_snapshot(self):
        latest, snapshot = fixture.daily_sources()
        changed = copy.deepcopy(latest)
        changed["snapshot"] = "20261024T115940000000Z"
        with mock.patch.object(adapters, "_read_fixed_json", side_effect=[latest, snapshot, changed]) as reader:
            value = adapters.read_bound("daily_lingo_observer", binding("daily_lingo_observer"), "project-alpha", fixture.NOW)
        self.assertEqual(value["status"], "unavailable")
        self.assertEqual(reader.call_count, 3)
        self.assertNotIn(changed["snapshot"], protocol.canonical(value))
        self.assertNotIn(fixture.CANARY, protocol.canonical(value))

    def test_missing_file_and_invalid_content_keep_distinct_closed_gap_shapes(self):
        for kind in KINDS:
            absent = adapters.read_bound(kind, None, "project-alpha", fixture.NOW)
            self.assertEqual(absent["status"], "not_configured")
            for error, state in ((FileNotFoundError(), "not_found"), (ValueError("PRIVATE"), "unavailable"),
                                 (OSError("PRIVATE"), "unavailable")):
                with self.subTest(kind=kind, state=state), mock.patch.object(adapters, "_read_fixed_json", side_effect=error):
                    result = adapters.read_bound(kind, binding(kind), "project-alpha", fixture.NOW)
                    self.assertEqual(result["status"], state)
                    self.assertEqual(set(result), {"status", "reason", "implementation_version", "observed_at"})
                    adapters.validate_observation(kind, result)
                    self.assertNotIn("PRIVATE", protocol.canonical(result))

    def test_wrong_monitor_release_source_and_raw_fields_never_become_claims(self):
        for kind in KINDS:
            values = documents(kind)
            if kind == "daily_lingo_observer":
                values["/fixed/observer/latest-result.json"]["manifest_sha256"] = "f" * 64
            else:
                values["/fixed/proxy/latest.json"]["source_sha256"] = "f" * 64
            value, _reader = self.read(kind, values=values)
            self.assertEqual(value["status"], "unavailable")
            self.assertNotIn(fixture.CANARY, protocol.canonical(value))

    def test_shared_payload_validator_rejects_forged_freshness_or_extra_content(self):
        for kind in KINDS:
            value, _reader = self.read(kind)
            outputs = {field: value[field] for field in adapters.ADAPTERS[kind]["outputs"]}
            self.assertIs(adapters.validate_payload(kind, outputs, fixture.NOW), outputs)
            for change in ({"fresh": False}, {"raw": fixture.CANARY}):
                tampered = {**copy.deepcopy(value), **change}
                with self.subTest(kind=kind, change=change), self.assertRaises(ValueError):
                    adapters.validate_observation(kind, tampered)


class SelectedMonitorBusiness(unittest.TestCase):
    def setUp(self):
        self.clock = mock.patch.object(protocol, "now", return_value=fixture.NOW)
        self.age = mock.patch.object(protocol, "age", return_value=0)
        self.clock.start(); self.age.start()
        self.addCleanup(self.clock.stop); self.addCleanup(self.age.stop)

    def policy(self, kind):
        value = scoped.config()
        value["project_adapters"] = {"project-alpha": {"runtime": binding(kind)},
                                    "project-beta": {"runtime": {"adapter": "DO-NOT-SELECT"}}}
        return value

    def pipeline(self, kind, values=None, focus=None):
        selected = scoped.operation(("runtime",))
        values = documents(kind) if values is None else values
        def read(path, maximum_bytes=adapters.MAX_SOURCE_BYTES):
            self.assertEqual(maximum_bytes, monitors.MAX_SOURCE_BYTES)
            if path not in values:
                raise AssertionError("colocated or unrelated source read")
            return copy.deepcopy(values[path])
        def remote(_transport, host, request):
            self.assertEqual(host, "host-a")
            return fleet_host.run(self.policy(kind), request)
        with mock.patch.object(adapters, "_read_fixed_json", side_effect=read) as reader, \
                mock.patch.object(cara_fleet, "remote", side_effect=remote), \
                mock.patch.object(fleet_host, "read", side_effect=AssertionError("manifest read")), \
                mock.patch.object(fleet_host, "command", side_effect=AssertionError("new probe")), \
                mock.patch.object(fleet_host, "unit_state", side_effect=AssertionError("unit inspection")), \
                mock.patch.object(fleet_host, "resources", side_effect=AssertionError("host resources")):
            value = cara_fleet.inspect(scoped.inventory(), {}, selected)
        protocol.result(value, selected["hosts"], read_scope=selected["read_scope"])
        plan = {**selected, "projects": ["project-alpha"], "focus": focus or ["overview"]}
        report = fleet_report.build(value, scoped.inventory(), plan)
        answer = "\n".join(fleet_answers.render(report, None, plan))
        return value, report, answer, reader

    def test_discovery_uses_exact_runtime_versions_without_reading_monitor_files(self):
        for kind in KINDS:
            scope = scoped.operation(("runtime",))["read_scope"]
            with self.subTest(kind=kind), mock.patch.object(adapters, "_read_fixed_json", side_effect=AssertionError("discovery read")):
                value = fleet_host.run(self.policy(kind), {"operation": "capabilities", "read_scope": scope})
                protocol.read_discovery(value, "host-a", scope)
                self.assertEqual(value["capabilities"], {"project-alpha": {"runtime": {
                    "configured": True, "implementation_version": adapters.ADAPTERS[kind]["implementation_version"]}}})

    def test_daily_selected_pipeline_answers_generation_reserves_and_budget_from_dated_evidence(self):
        value, report, answer, reader = self.pipeline("daily_lingo_observer")
        self.assertEqual(reader.call_count, 3)
        state = value["hosts"]["host-a"]["projects"]["project-alpha"]["runtime"]
        self.assertEqual(state["status"], "recorded")
        for fact in ("generation", "nb/A2", "17", "2026-10-24"):
            self.assertIn(fact, answer)
        self.assertNotIn("project-beta", protocol.canonical(value))
        self.assertNotIn("Project Beta", answer)
        self.assertNotIn(fixture.CANARY, protocol.canonical(report))
        self.assertEqual(state["observed_at"], fixture.NOW)
        record = next(item for item in report["observations"].values() if item.get("capability") == "runtime")
        self.assertEqual(record["source"], monitors.ADAPTERS["daily_lingo_observer"]["source"])
        self.assertEqual(record["value"]["source_observed_at"], fixture.CAPTURED)
        self.assertIsNone(state["accepted_release"])
        self.assertIsNone(state["target"])

    def test_proxy_selected_pipeline_keeps_authenticated_route_and_cumulative_counter_meaning(self):
        value, report, answer, reader = self.pipeline("authenticated_proxy_route")
        self.assertEqual(reader.call_count, 1)
        state = value["hosts"]["host-a"]["projects"]["project-alpha"]["runtime"]
        self.assertEqual(state["health"], "ok")
        self.assertEqual(state["coverage"], "sampled_authenticated_route_only")
        for fact in ("12345", "3", "cumulative", "route"):
            self.assertIn(fact, answer)
        for private in ("Project Beta", fixture.CANARY, "192.0.2.17", "/fixed/"):
            self.assertNotIn(private, answer)
        self.assertEqual(next(item for item in report["observations"].values()
                              if item.get("capability") == "runtime")["source"],
                         monitors.ADAPTERS["authenticated_proxy_route"]["source"])

    def test_proxy_load_question_keeps_real_counts_and_specific_unmeasured_load_gaps(self):
        _value, report, answer, _reader = self.pipeline("authenticated_proxy_route", focus=["resources"])
        for fact in ("12345", "3", "cumulative", "CPU/RAM/disk", "throughput", "unique users"):
            self.assertIn(fact, answer)
        self.assertNotIn("project request/traffic and", answer)
        gaps = [row for row in report["observations"].values()
                if row.get("capability") == "project_metrics"]
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["status"], "not_configured")

    def test_stale_source_dates_survive_selected_pipeline_without_fresh_health_claim(self):
        with mock.patch.object(protocol, "now", return_value="2026-10-24T12:20:00Z"):
            for kind in KINDS:
                with self.subTest(kind=kind):
                    value, _report, answer, _reader = self.pipeline(kind)
                    state = value["hosts"]["host-a"]["projects"]["project-alpha"]["runtime"]
                    self.assertFalse(state["fresh"])
                    self.assertEqual(state["source_observed_at"], fixture.CAPTURED)
                    self.assertIn("stale", answer)
                    if kind == "authenticated_proxy_route":
                        self.assertEqual(state["health"], "unknown")


@unittest.skipUnless(os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() == 0,
                     "protected monitor reader requires isolated Linux root stage")
class ProtectedMonitorReads(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="cara-fixed-monitor-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name); self.root.chmod(0o700)
        self.path = self.root / "latest.json"
        self.config = binding("authenticated_proxy_route", path=str(self.path))
        self.write(fixture.proxy_source())

    def write(self, value):
        self.path.write_text(json.dumps(value), encoding="utf-8"); self.path.chmod(0o600)

    def test_monitor_explicit_limit_preserves_legacy_64k_default_and_source_bytes(self):
        raw = fixture.proxy_source(); raw["ignored_source_history"] = "x" * 70000
        self.write(raw); before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            adapters._read_fixed_json(str(self.path))
        self.assertEqual(adapters._read_fixed_json(str(self.path), maximum_bytes=monitors.MAX_SOURCE_BYTES), raw)
        value = adapters.read_bound("authenticated_proxy_route", self.config, "project-alpha", fixture.NOW)
        self.assertEqual(value["status"], "recorded")
        self.assertLess(len(protocol.canonical(value).encode()), monitors.MAX_OUTPUT_BYTES)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(set(self.root.iterdir()), {self.path})

    def test_monitor_sources_over_2mib_and_unqualified_limits_are_rejected(self):
        self.path.write_bytes(b"x" * (monitors.MAX_SOURCE_BYTES + 1))
        self.assertEqual(adapters.read_bound("authenticated_proxy_route", self.config, "project-alpha", fixture.NOW)["status"], "unavailable")
        for bound in (True, 0, -1, monitors.MAX_SOURCE_BYTES + 1, "65536"):
            with self.subTest(bound=bound), self.assertRaises(ValueError):
                adapters._read_fixed_json(str(self.path), maximum_bytes=bound)

    def test_symlink_leaf_or_ancestor_writable_source_and_fifo_fail_closed(self):
        link = self.root / "link.json"; link.symlink_to(self.path)
        child = self.root / "child"; child.mkdir(mode=0o700)
        (child / "source.json").write_bytes(self.path.read_bytes()); (child / "source.json").chmod(0o600)
        linked = self.root / "linked"; linked.symlink_to(child, target_is_directory=True)
        fifo = self.root / "fifo.json"; os.mkfifo(fifo, 0o600)
        for path in (link, linked / "source.json", fifo):
            with self.subTest(path=path.name):
                config = binding("authenticated_proxy_route", path=str(path))
                self.assertEqual(adapters.read_bound("authenticated_proxy_route", config, "project-alpha", fixture.NOW)["status"], "unavailable")
        self.path.chmod(0o660)
        self.assertEqual(adapters.read_bound("authenticated_proxy_route", self.config, "project-alpha", fixture.NOW)["status"], "unavailable")

    def test_duplicate_nonfinite_and_invalid_utf8_monitor_json_remain_unavailable(self):
        for raw in (b'{"schema":1,"schema":1}', b'{"x":NaN}', b'\xff'):
            with self.subTest(raw=raw):
                self.path.write_bytes(raw)
                self.assertEqual(adapters.read_bound("authenticated_proxy_route", self.config, "project-alpha", fixture.NOW)["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
