"""Offline monitor contract projections, synthetic identities and no probes."""
import copy
import json
import unittest

import fleet_monitor_adapters as adapters

NOW = "2026-10-24T12:00:00Z"
CAPTURED = "2026-10-24T11:59:30Z"
STOCK_AT = "2026-10-24T11:59:25Z"
HEARTBEAT_AT = "2026-10-24T11:59:20Z"
PROBE_AT = "2026-10-24T11:59:10Z"
STAMP = "20261024T115930000000Z"
IMAGE = "sha256:" + "a" * 64
MANIFEST = "b" * 64
STEP5 = "c" * 64
CANARY = "SYNTHETIC-PRIVATE-CANARY"
BOOT = "00000000-1111-2222-3333-444444444444"


def daily_binding():
    return {"source_id": "observer-alpha", "latest_path": "/fixed/observer/latest-result.json",
        "snapshot_root": "/fixed/observer/snapshots", "image": IMAGE, "manifest_sha256": MANIFEST,
        "step5_receipt_sha256": STEP5, "max_age_seconds": 90, "budget_max_age_seconds": 150}


def daily_sources():
    stock = {cell: index + 2 for index, cell in enumerate(adapters.CELLS)}
    snapshot = {"captured_at": CAPTURED, "collector_at": "2026-10-24T11:59:40Z",
        "read_only": True, "transaction_read_only": True, "transaction_isolation": "repeatable read",
        "stock_as_of": STOCK_AT, "stock": stock, "generation_enabled": True,
        "image": IMAGE, "app_health": "healthy", "postgres_health": "healthy", "stop_reason": None,
        "unfinished_calls": 1, "unfinished_refills": 2,
        "budget_guard": {"active": True, "terminal_outcome": None, "heartbeat": {
            "captured_at": HEARTBEAT_AT, "probe_at": PROBE_AT, "image": IMAGE,
            "step5_receipt_sha256": STEP5, "daily_limit_usd": 17, "outcome": "within_limit"}},
        "profiles": [{"user_id": CANARY}], "jobs": [{"text": CANARY}],
        "assignments": {"private": CANARY}, "journal_events": [{"text": CANARY}],
        "cost_summary": {"private": CANARY}, "runtime_models": {"token": CANARY}}
    latest = {"captured_at": CAPTURED, "status": "observed", "snapshot": STAMP,
        "stock": copy.deepcopy(stock), "read_only": True, "manifest_sha256": MANIFEST,
        "owner_channel_reference": CANARY, "failed_or_unknown_notifications": [CANARY]}
    return latest, snapshot


def daily(latest=None, snapshot=None, binding=None, paths=None):
    base_latest, base_snapshot = daily_sources()
    return adapters.daily_lingo_observer(base_latest if latest is None else latest,
        base_snapshot if snapshot is None else snapshot, binding=daily_binding() if binding is None else binding,
        observed_at=NOW, source_paths=paths or {"latest": "/fixed/observer/latest-result.json",
            "snapshot": "/fixed/observer/snapshots/" + STAMP + ".json"})


def proxy_binding(collector=False, route=True):
    binding = {"source_id": "route-alpha", "path": "/fixed/proxy/latest.json",
        "source_kind": "collector" if collector else "host", "role": "edge-alpha",
        "source_sha256": "d" * 64, "max_age_seconds": 660, "expected_boot_id": BOOT,
        "profiles": {"profile-a": {"mode": "FakeTLS", "metric": "connection_counter"},
                     "profile-b": {"mode": "secure", "metric": "current_connections"}}}
    if route:
        binding["route"] = {"address": "192.0.2.17", "port": 24444, "config_sha256": "e" * 64}
    return binding


def proxy_source():
    return {"schema": 1, "role": "edge-alpha", "source_sha256": "d" * 64, "boot_id": BOOT,
        "started_utc": "2026-10-24T11:59:00Z", "completed_utc": CAPTURED,
        "status": "ok", "fresh": True, "profiles": {
            "profile-a": {"port": 24444, "mode": "FakeTLS", "client_ids": [CANARY],
                "protocol": {"mode": "ee-FakeTLS", "port": 24444, "dc": 2, "seconds": 0.35,
                    "resPQ_nonce_verified": True, "endpoint": CANARY},
                "metrics": {"ok": True, "connection_counter": 12345, "token": CANARY}},
            "profile-b": {"port": 24445, "mode": "secure",
                "protocol": {"mode": "dd-secure", "port": 24445, "dc": 2, "seconds": 0.27,
                    "resPQ_nonce_verified": True}, "metrics": {"ok": True, "current_connections": 3}}},
        "tunnel": {"ok": True, "configured_active_address": "192.0.2.17", "configured_active_port": 24444,
            "mtg_socks_binding_verified": True, "xray_and_fail_closed_unit_active": True,
            "xray_config_sha256": "e" * 64, "private_key": CANARY}, "raw_error": CANARY}


def proxy(document=None, binding=None, source_path="/fixed/proxy/latest.json"):
    return adapters.authenticated_proxy_route(proxy_source() if document is None else document,
        binding=proxy_binding() if binding is None else binding, observed_at=NOW, source_path=source_path)


class DailyObserverTests(unittest.TestCase):
    def test_actual_observer_contract_preserves_independent_dates_and_ceilings(self):
        value = daily()
        self.assertEqual(value["implementation_version"], "project-daily-lingo-observer/v1")
        self.assertTrue(value["fresh"])
        self.assertEqual(value["source_observed_at"], CAPTURED)
        self.assertEqual(value["reserves"], {"as_of": STOCK_AT, "fresh": True,
            "cells": daily_sources()[1]["stock"]})
        self.assertEqual(value["budget_guard"]["heartbeat_at"], HEARTBEAT_AT)
        self.assertEqual(value["budget_guard"]["probe_at"], PROBE_AT)
        self.assertEqual(value["budget_guard"]["daily_limit_usd"], 17)
        self.assertEqual(value["generation"]["as_of"], CAPTURED)
        self.assertEqual(value["application"]["unfinished_calls"], 1)

    def test_private_user_job_history_paths_and_extra_facts_never_project(self):
        latest, snapshot = daily_sources()
        snapshot.update(target=5, accepted_release=CANARY, stop_reason=CANARY, secret=CANARY)
        value = daily(latest, snapshot)
        text = json.dumps(value)
        for forbidden in (CANARY, "/fixed/", "profiles", "jobs", "journal_events", "runtime_models", "manifest_sha256", "step5_receipt_sha256"):
            self.assertNotIn(forbidden, text)
        self.assertTrue(value["generation"]["stop_recorded"])
        self.assertIsNone(value["target"])
        self.assertIsNone(value["accepted_release"])
        self.assertEqual(value["gaps"]["target"], "not_connected")

    def test_explicit_verified_release_and_nondefault_targets(self):
        binding = daily_binding()
        binding["accepted_release"] = {"version": "verified-project-release/v1", "revision": "f" * 40,
            "accepted_at": "2026-10-23T12:00:00Z", "verification_sha256": "1" * 64,
            "image": IMAGE, "manifest_sha256": MANIFEST, "step5_receipt_sha256": STEP5,
            "targets": {cell: 8 for cell in adapters.CELLS}}
        value = daily(binding=binding)
        self.assertEqual(value["target"]["cells"], {cell: 8 for cell in adapters.CELLS})
        self.assertEqual(value["accepted_release"]["revision"], "f" * 40)
        self.assertNotIn("accepted_release", value["gaps"])
        binding["accepted_release"].pop("targets")
        value = daily(binding=binding)
        self.assertIsNotNone(value["accepted_release"])
        self.assertEqual(value["gaps"]["target"], "not_connected")

    def test_release_image_manifest_step5_and_receipt_are_mandatory(self):
        binding = daily_binding()
        release = {"version": "verified-project-release/v1", "revision": "f" * 40,
            "accepted_at": "2026-10-23T12:00:00Z", "verification_sha256": "1" * 64,
            "image": IMAGE, "manifest_sha256": MANIFEST, "step5_receipt_sha256": STEP5}
        for key in release:
            with self.subTest(key=key), self.assertRaises(ValueError):
                binding["accepted_release"] = dict(release); binding["accepted_release"].pop(key)
                daily(binding=binding)
        for key in ("image", "manifest_sha256", "step5_receipt_sha256"):
            with self.subTest(mismatch=key), self.assertRaises(ValueError):
                binding["accepted_release"] = dict(release)
                binding["accepted_release"][key] = "sha256:" + "9" * 64 if key == "image" else "9" * 64
                daily(binding=binding)

    def test_generation_pause_and_unhealthy_application_are_preserved(self):
        latest, snapshot = daily_sources()
        snapshot.update(generation_enabled=False, app_health="unhealthy", stop_reason="private-reason")
        snapshot["budget_guard"]["active"] = False
        snapshot["budget_guard"]["terminal_outcome"] = "unknown_daily_usage"
        value = daily(latest, snapshot)
        self.assertFalse(value["generation"]["enabled"])
        self.assertEqual(value["application"]["app_health"], "unhealthy")
        self.assertEqual(value["budget_guard"]["terminal_outcome"], "unknown_daily_usage")
        self.assertNotIn("private-reason", json.dumps(value))

    def test_stale_stock_is_not_refreshed_by_fresh_generation_observation(self):
        latest, snapshot = daily_sources(); snapshot["stock_as_of"] = "2026-10-24T11:55:00Z"
        value = daily(latest, snapshot)
        self.assertTrue(value["generation"]["fresh"])
        self.assertFalse(value["reserves"]["fresh"])
        self.assertEqual(value["gaps"]["reserves"], "stale")

    def test_stale_observer_does_not_become_current_from_projection_time(self):
        latest, snapshot = daily_sources()
        latest["captured_at"] = snapshot["captured_at"] = "2026-10-24T11:55:30Z"
        snapshot["stock_as_of"] = "2026-10-24T11:55:25Z"
        value = daily(latest, snapshot)
        self.assertFalse(value["fresh"])
        self.assertEqual(value["gaps"]["observer"], "stale")
        self.assertEqual(value["source_observed_at"], "2026-10-24T11:55:30Z")

    def test_stale_budget_probe_not_hidden_by_fresh_heartbeat(self):
        latest, snapshot = daily_sources()
        snapshot["budget_guard"]["heartbeat"]["probe_at"] = "2026-10-24T11:50:00Z"
        value = daily(latest, snapshot)
        self.assertTrue(value["fresh"])
        self.assertFalse(value["budget_guard"]["fresh"])
        self.assertEqual(value["gaps"]["budget_guard"], "stale")

    def test_heartbeat_can_be_newer_than_db_snapshot_but_not_collection(self):
        latest, snapshot = daily_sources()
        snapshot["budget_guard"]["heartbeat"]["captured_at"] = "2026-10-24T11:59:35Z"
        self.assertTrue(daily(latest, snapshot)["budget_guard"]["fresh"])
        snapshot["budget_guard"]["heartbeat"]["captured_at"] = "2026-10-24T11:59:45Z"
        with self.assertRaises(ValueError):
            daily(latest, snapshot)

    def test_missing_or_inactive_budget_monitor_remains_specific_gap(self):
        latest, snapshot = daily_sources(); snapshot.pop("budget_guard")
        self.assertEqual(daily(latest, snapshot)["gaps"]["budget_guard"], "not_connected")
        snapshot = daily_sources()[1]; snapshot["budget_guard"]["heartbeat"] = None
        self.assertEqual(daily(latest, snapshot)["gaps"]["budget_guard"], "observation_unavailable")
        snapshot = daily_sources()[1]; snapshot["budget_guard"]["active"] = False
        self.assertEqual(daily(latest, snapshot)["gaps"]["budget_guard"], "monitor_inactive")

    def test_budget_bindings_outcome_and_type_fail_closed(self):
        changes = [("image", "sha256:" + "9" * 64), ("step5_receipt_sha256", "9" * 64),
                   ("outcome", CANARY), ("daily_limit_usd", True), ("daily_limit_usd", 0)]
        for key, changed in changes:
            latest, snapshot = daily_sources(); snapshot["budget_guard"]["heartbeat"][key] = changed
            with self.subTest(key=key), self.assertRaises(ValueError):
                daily(latest, snapshot)

    def test_partial_reserves_do_not_gain_missing_cells(self):
        latest, snapshot = daily_sources()
        latest["stock"].pop("fr/B2"); snapshot["stock"].pop("fr/B2")
        value = daily(latest, snapshot)
        self.assertEqual(len(value["reserves"]["cells"]), 5)
        self.assertEqual(value["gaps"]["reserve_coverage"], "partial_coverage")

    def test_fixed_snapshot_paths_and_stamps_refuse_traversal_or_wrong_source(self):
        latest, snapshot = daily_sources()
        for stamp in ("../private", "/private/x", "20261324T115930000000Z", STAMP + "/x"):
            bad = dict(latest); bad["snapshot"] = stamp
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                daily(bad, snapshot)
        for paths in ({"latest": "/other/latest.json", "snapshot": "/fixed/observer/snapshots/" + STAMP + ".json"},
                      {"latest": "/fixed/observer/latest-result.json", "snapshot": "/other/" + STAMP + ".json"}):
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                daily(paths=paths)

    def test_read_only_capture_stock_and_release_consistency_are_required(self):
        for container, key, changed in (("latest", "read_only", False), ("snapshot", "transaction_read_only", False),
                ("snapshot", "transaction_isolation", "read committed"), ("latest", "manifest_sha256", "0"*64),
                ("snapshot", "image", "sha256:" + "0"*64), ("latest", "captured_at", NOW),
                ("snapshot", "stock", {"nb/A2": 99}), ("snapshot", "generation_enabled", 1)):
            latest, snapshot = daily_sources(); (latest if container == "latest" else snapshot)[key] = changed
            with self.subTest(key=key, container=container), self.assertRaises(ValueError):
                daily(latest, snapshot)

    def test_bad_dates_nonfinite_numbers_and_oversize_are_rejected(self):
        for date in ("2026-10-24T12:10:00Z", "2026-10-24T12:00:00", "tomorrow"):
            latest, snapshot = daily_sources(); latest["captured_at"] = snapshot["captured_at"] = date
            with self.subTest(date=date), self.assertRaises(ValueError):
                daily(latest, snapshot)
        latest, snapshot = daily_sources(); snapshot["hidden"] = "x" * adapters.MAX_SOURCE_BYTES
        with self.assertRaises(ValueError):
            daily(latest, snapshot)
        latest, snapshot = daily_sources(); snapshot["budget_guard"]["heartbeat"]["daily_limit_usd"] = float("nan")
        with self.assertRaises(ValueError):
            daily(latest, snapshot)


class AuthenticatedProxyTests(unittest.TestCase):
    def test_actual_host_protocol_route_and_metric_contract(self):
        value = proxy()
        self.assertEqual(value["health"], "ok")
        self.assertTrue(value["fresh"])
        self.assertEqual(value["source_observed_at"], CAPTURED)
        self.assertEqual(value["route"], {"status": "verified", "as_of": CAPTURED,
            "binding_verified": True, "evidence": "recorded"})
        self.assertEqual(value["profiles"]["profile-a"]["metric"], "connection_counter")
        self.assertEqual(value["profiles"]["profile-a"]["metric_value"], 12345)
        self.assertEqual(value["profiles"]["profile-b"]["metric"], "current_connections")
        self.assertEqual(value["profiles"]["profile-b"]["metric_value"], 3)
        self.assertEqual(value["coverage"], "sampled_authenticated_route_only")

    def test_no_private_endpoints_keys_ports_boot_ids_or_raw_errors_escape(self):
        text = json.dumps(proxy())
        for forbidden in (CANARY, "192.0.2.17", "24444", "24445", BOOT, "/fixed/", "client_ids",
                          "private_key", "xray_config_sha256", "source_sha256", "endpoint", "raw_error"):
            self.assertNotIn(forbidden, text)
        self.assertNotIn("throughput", text)
        self.assertNotIn("active_clients", text)

    def test_protocol_failure_overrides_producer_ok_and_tcp_success(self):
        source = proxy_source()
        source["profiles"]["profile-b"]["protocol"] = {"resPQ_nonce_verified": False, "tcp_ok": True, "error": CANARY}
        value = proxy(source)
        self.assertEqual(value["health"], "critical")
        self.assertEqual(value["route"]["status"], "unverified")
        self.assertFalse(value["profiles"]["profile-b"]["protocol_verified"])
        self.assertNotIn(CANARY, json.dumps(value))

    def test_failed_metrics_are_unavailable_not_zero_or_route_failure(self):
        source = proxy_source(); source["profiles"]["profile-b"]["metrics"] = {"ok": False, "error": CANARY}
        value = proxy(source)
        self.assertEqual(value["health"], "needs_attention")
        self.assertEqual(value["route"]["status"], "verified")
        self.assertIsNone(value["profiles"]["profile-b"]["metric_value"])
        self.assertEqual(value["gaps"]["metrics_profile-b"], "observation_unavailable")

    def test_wrong_or_absent_exact_route_never_reports_verified(self):
        for key, changed in (("configured_active_address", "192.0.2.18"), ("configured_active_port", 24445),
                ("xray_config_sha256", "0" * 64), ("mtg_socks_binding_verified", False),
                ("xray_and_fail_closed_unit_active", False), ("ok", False)):
            source = proxy_source(); source["tunnel"][key] = changed
            with self.subTest(key=key):
                value = proxy(source)
                self.assertEqual(value["health"], "critical")
                self.assertEqual(value["route"]["status"], "unverified")
        source = proxy_source(); source.pop("tunnel")
        value = proxy(source)
        self.assertEqual(value["gaps"]["route"], "not_exposed")
        self.assertEqual(value["route"]["status"], "unverified")

    def test_older_monitor_without_route_address_has_precise_gap(self):
        source = proxy_source(); source["tunnel"].pop("configured_active_address")
        value = proxy(source)
        self.assertEqual(value["route"]["status"], "unverified")
        self.assertEqual(value["gaps"]["route"], "binding_or_route_unverified")

    def test_unconfigured_route_does_not_invent_proof(self):
        value = proxy(binding=proxy_binding(route=False))
        self.assertIsNone(value["route"])
        self.assertEqual(value["gaps"]["route"], "not_configured")
        self.assertEqual(value["health"], "needs_attention")

    def test_stale_source_cannot_be_healthy_despite_producer_fresh_true(self):
        source = proxy_source(); source["started_utc"] = "2026-10-24T11:40:00Z"; source["completed_utc"] = "2026-10-24T11:41:00Z"
        value = proxy(source)
        self.assertFalse(value["fresh"])
        self.assertEqual(value["health"], "unknown")
        self.assertEqual(value["route"]["status"], "stale")

    def test_fresh_collector_cannot_refresh_old_host_observation(self):
        source = proxy_source(); source["started_utc"] = "2026-10-24T11:40:00Z"; source["completed_utc"] = "2026-10-24T11:41:00Z"
        value = proxy({"schema": 1, "collected_utc": NOW, "hosts": {"edge-alpha": source,
            "unrelated": {"secret": CANARY}}}, binding=proxy_binding(collector=True))
        self.assertEqual(value["collected_at"], NOW)
        self.assertEqual(value["health"], "unknown")
        self.assertEqual(value["gaps"]["protocol_observation"], "stale")
        self.assertNotIn(CANARY, json.dumps(value))

    def test_stale_collector_does_not_establish_current_health(self):
        source = proxy_source(); source["started_utc"] = "2026-10-24T11:40:00Z"; source["completed_utc"] = "2026-10-24T11:41:00Z"
        value = proxy({"schema": 1, "collected_utc": "2026-10-24T11:42:00Z", "hosts": {"edge-alpha": source}},
            binding=proxy_binding(collector=True))
        self.assertEqual(value["gaps"]["collector"], "stale")
        self.assertEqual(value["health"], "unknown")

    def test_collector_timestamp_and_host_scope_are_independently_bound(self):
        value = proxy({"schema": 1, "collected_utc": "2026-10-24T11:59:45Z", "hosts": {"edge-alpha": proxy_source()}},
            binding=proxy_binding(collector=True))
        self.assertEqual(value["source_observed_at"], CAPTURED)
        self.assertEqual(value["collected_at"], "2026-10-24T11:59:45Z")
        self.assertEqual(value["health"], "ok")
        for document in ({"schema": 1, "collected_utc": NOW, "hosts": {}},
                         {"schema": 1, "collected_utc": "2026-10-24T11:59:15Z", "hosts": {"edge-alpha": proxy_source()}}):
            with self.subTest(document=document), self.assertRaises(ValueError):
                proxy(document, binding=proxy_binding(collector=True))

    def test_profile_role_source_boot_and_mode_mismatches_fail_closed(self):
        for key, changed in (("role", "other-edge"), ("source_sha256", "9" * 64), ("boot_id", "9" * 36), ("schema", True)):
            source = proxy_source(); source[key] = changed
            with self.subTest(key=key), self.assertRaises(ValueError):
                proxy(source)
        source = proxy_source(); source["profiles"].pop("profile-b")
        with self.assertRaises(ValueError):
            proxy(source)
        source = proxy_source(); source["profiles"]["unrelated"] = copy.deepcopy(source["profiles"]["profile-a"])
        with self.assertRaises(ValueError):
            proxy(source)
        source = proxy_source(); source["profiles"]["profile-a"]["protocol"]["mode"] = "dd-secure"
        with self.assertRaises(ValueError):
            proxy(source)

    def test_no_arbitrary_paths_or_fields_enter_root_policy(self):
        with self.assertRaises(ValueError):
            proxy(source_path="/other/private.json")
        for extra in ("command", "url", "environment"):
            binding = proxy_binding(); binding[extra] = CANARY
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                proxy(binding=binding)

    def test_invalid_dates_and_metric_numbers_fail_closed(self):
        for completed in ("2026-10-24T12:01:00Z", "not-a-date", "2026-10-24T11:58:00Z"):
            source = proxy_source(); source["completed_utc"] = completed
            with self.subTest(completed=completed), self.assertRaises(ValueError):
                proxy(source)
        for number in (True, -1, float("inf"), "123"):
            source = proxy_source(); source["profiles"]["profile-a"]["metrics"]["connection_counter"] = number
            with self.subTest(number=str(number)), self.assertRaises(ValueError):
                proxy(source)

    def test_inputs_are_unchanged_by_projection(self):
        latest, snapshot = daily_sources(); binding = daily_binding()
        before = copy.deepcopy((latest, snapshot, binding)); daily(latest, snapshot, binding)
        self.assertEqual((latest, snapshot, binding), before)
        source = proxy_source(); binding = proxy_binding(); before = copy.deepcopy((source, binding))
        proxy(source, binding); self.assertEqual((source, binding), before)



class MonitorBindingTests(unittest.TestCase):
    def test_both_policies_validate_for_discovery_without_source_data(self):
        for kind, binding in (("daily_lingo_observer", daily_binding()),
                              ("authenticated_proxy_route", proxy_binding()),
                              ("authenticated_proxy_route", proxy_binding(collector=True))):
            before = copy.deepcopy(binding)
            self.assertIs(adapters.validate_binding(kind, binding), binding)
            self.assertEqual(binding, before)

    def test_discovery_rejects_missing_unknown_and_wrong_typed_policy_fields(self):
        for kind, binding in (("daily_lingo_observer", daily_binding()), ("authenticated_proxy_route", proxy_binding())):
            for key in binding:
                if key in {"expected_boot_id", "route"}:
                    continue
                bad = copy.deepcopy(binding); bad.pop(key)
                with self.subTest(kind=kind, missing=key), self.assertRaises(ValueError):
                    adapters.validate_binding(kind, bad)
            bad = copy.deepcopy(binding); bad["command"] = "SYNTHETIC-NOT-EXECUTABLE"
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                adapters.validate_binding(kind, bad)
        for kind in ("shell", None, []):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                adapters.validate_binding(kind, {})

    def test_policy_paths_and_monitor_age_caps_are_closed(self):
        for path in ("relative.json", "/fixed/../private.json", "/fixed//private.json", "/fixed/\x7f.json", "https://example.invalid"):
            binding = proxy_binding(); binding["path"] = path
            with self.subTest(path=path), self.assertRaises(ValueError):
                adapters.validate_binding("authenticated_proxy_route", binding)
        for kind, field, number in (("daily_lingo_observer", "max_age_seconds", 91),
                ("daily_lingo_observer", "budget_max_age_seconds", 151),
                ("authenticated_proxy_route", "max_age_seconds", 661),
                ("authenticated_proxy_route", "max_age_seconds", True)):
            binding = daily_binding() if kind == "daily_lingo_observer" else proxy_binding()
            binding[field] = number
            with self.subTest(kind=kind, field=field, number=number), self.assertRaises(ValueError):
                adapters.validate_binding(kind, binding)

    def test_discovery_validates_exact_release_dates_targets_and_proof_binding(self):
        binding = daily_binding()
        release = {"version": "verified-project-release/v1", "revision": "f" * 40,
            "accepted_at": "2026-10-23T12:00:00Z", "verification_sha256": "1" * 64,
            "image": IMAGE, "manifest_sha256": MANIFEST, "step5_receipt_sha256": STEP5,
            "targets": {cell: 8 for cell in adapters.CELLS}}
        binding["accepted_release"] = copy.deepcopy(release)
        self.assertIs(adapters.validate_binding("daily_lingo_observer", binding), binding)
        for key, changed in (("accepted_at", "yesterday"), ("verification_sha256", CANARY),
                ("manifest_sha256", "9" * 64), ("targets", {"nb/A2": 8}),
                ("targets", {cell: True for cell in adapters.CELLS}), ("command", CANARY)):
            binding["accepted_release"] = copy.deepcopy(release); binding["accepted_release"][key] = changed
            with self.subTest(key=key), self.assertRaises(ValueError):
                adapters.validate_binding("daily_lingo_observer", binding)

    def test_route_profile_and_boot_policy_are_not_arbitrary_endpoint_arguments(self):
        for key, changed in (("address", "https://example.invalid/private"), ("address", "private@host"),
                ("port", True), ("port", 70000), ("config_sha256", CANARY), ("secret", CANARY)):
            binding = proxy_binding(); binding["route"][key] = changed
            with self.subTest(key=key), self.assertRaises(ValueError):
                adapters.validate_binding("authenticated_proxy_route", binding)
        for field, changed in (("profiles", {}), ("expected_boot_id", CANARY), ("source_kind", "network")):
            binding = proxy_binding(); binding[field] = changed
            with self.subTest(field=field), self.assertRaises(ValueError):
                adapters.validate_binding("authenticated_proxy_route", binding)
        for field, changed in (("mode", "TCP"), ("metric", "active_clients"), ("endpoint", CANARY)):
            binding = proxy_binding(); binding["profiles"]["profile-a"][field] = changed
            with self.subTest(field=field), self.assertRaises(ValueError):
                adapters.validate_binding("authenticated_proxy_route", binding)

    def test_validation_and_projection_do_not_open_or_collect_sources(self):
        from unittest.mock import patch
        with patch("builtins.open", side_effect=AssertionError("source I/O forbidden")):
            adapters.validate_binding("daily_lingo_observer", daily_binding())
            adapters.validate_binding("authenticated_proxy_route", proxy_binding())
            self.assertTrue(daily()["fresh"])
            self.assertTrue(proxy()["fresh"])


class MonitorObservationTests(unittest.TestCase):
    def test_both_closed_recorded_observations_round_trip(self):
        for kind, value in (("daily_lingo_observer", daily()), ("authenticated_proxy_route", proxy())):
            before = copy.deepcopy(value)
            self.assertIs(adapters.validate_observation(kind, value), value)
            self.assertEqual(value, before)
            self.assertEqual(set(value), {"status", "implementation_version", "observed_at"} | set(adapters.ADAPTERS[kind]["outputs"]))

    def test_terminal_gap_observations_are_exact_code_owned_shapes(self):
        for kind in adapters.ADAPTERS:
            for status, reason in (("not_configured", "not_configured"), ("unavailable", "observation_unavailable"),
                                   ("not_found", "resource_not_found")):
                value = {"status": status, "reason": reason, "implementation_version": adapters.ADAPTERS[kind]["implementation_version"],
                    "observed_at": NOW}
                self.assertIs(adapters.validate_observation(kind, value), value)
                for field, changed in (("reason", CANARY), ("path", "/private/source.json"), ("status", "ok")):
                    bad = dict(value); bad[field] = changed
                    with self.subTest(kind=kind, status=status, field=field), self.assertRaises(ValueError):
                        adapters.validate_observation(kind, bad)

    def test_unknown_private_fields_are_rejected_at_every_nested_level(self):
        for kind, original, paths in (("daily_lingo_observer", daily(),
                ((), ("generation",), ("reserves",), ("application",), ("budget_guard",))),
                ("authenticated_proxy_route", proxy(), ((), ("route",), ("profiles", "profile-a")))):
            for path in paths:
                value = copy.deepcopy(original); target = value
                for field in path:
                    target = target[field]
                target["private_endpoint"] = CANARY
                with self.subTest(kind=kind, path=path), self.assertRaises(ValueError):
                    adapters.validate_observation(kind, value)

    def test_missing_required_outputs_and_nested_fields_fail_closed(self):
        for kind, original in (("daily_lingo_observer", daily()), ("authenticated_proxy_route", proxy())):
            for key in original:
                value = copy.deepcopy(original); value.pop(key)
                with self.subTest(kind=kind, key=key), self.assertRaises(ValueError):
                    adapters.validate_observation(kind, value)
        for path in (("generation", "as_of"), ("reserves", "cells"), ("budget_guard", "daily_limit_usd")):
            value = daily(); value[path[0]].pop(path[1])
            with self.subTest(path=path), self.assertRaises(ValueError):
                adapters.validate_observation("daily_lingo_observer", value)
        value = proxy(); value["profiles"]["profile-a"].pop("metric")
        with self.assertRaises(ValueError):
            adapters.validate_observation("authenticated_proxy_route", value)

    def test_freshness_and_age_flags_cannot_be_rewritten(self):
        for kind, original in (("daily_lingo_observer", daily()), ("authenticated_proxy_route", proxy())):
            for field, changed in (("fresh", False), ("fresh", 1), ("max_age_seconds", True),
                                   ("source_observed_at", "2026-10-24T12:10:00Z")):
                value = copy.deepcopy(original); value[field] = changed
                with self.subTest(kind=kind, field=field), self.assertRaises(ValueError):
                    adapters.validate_observation(kind, value)
        for field in ("generation", "reserves", "budget_guard"):
            value = daily(); value[field]["fresh"] = False
            with self.subTest(field=field), self.assertRaises(ValueError):
                adapters.validate_observation("daily_lingo_observer", value)

    def test_required_gaps_cannot_be_deleted_renamed_or_replaced_by_text(self):
        for kind, original in (("daily_lingo_observer", daily()),
                ("authenticated_proxy_route", proxy(binding=proxy_binding(route=False)))):
            for changed in ({}, {"extra": CANARY}, CANARY):
                value = copy.deepcopy(original); value["gaps"] = changed
                with self.subTest(kind=kind, gaps=changed), self.assertRaises(ValueError):
                    adapters.validate_observation(kind, value)

    def test_daily_generation_reserve_and_budget_dates_stay_independent(self):
        for field, key, changed in (("generation", "as_of", STOCK_AT), ("reserves", "as_of", NOW),
                ("budget_guard", "heartbeat_at", "2026-10-24T11:59:50Z"),
                ("budget_guard", "probe_at", NOW), ("budget_guard", "daily_limit_usd", True)):
            value = daily(); value[field][key] = changed
            with self.subTest(field=field, key=key), self.assertRaises(ValueError):
                adapters.validate_observation("daily_lingo_observer", value)
        value = daily(); value["collected_at"] = "2026-10-24T11:59:00Z"
        with self.assertRaises(ValueError):
            adapters.validate_observation("daily_lingo_observer", value)

    def test_wire_release_and_target_cannot_be_inferred_or_lose_proofs(self):
        value = daily(); value["target"] = {"cells": {cell: 5 for cell in adapters.CELLS},
            "verified_at": CAPTURED, "verification_sha256": "1" * 64}
        value["gaps"].pop("target")
        with self.assertRaises(ValueError):
            adapters.validate_observation("daily_lingo_observer", value)
        release = {"revision": "f" * 40, "accepted_at": "2026-10-23T12:00:00Z",
            "verification_sha256": "1" * 64, "image": IMAGE}
        for key, changed in (("accepted_at", "2026-10-25T12:00:00Z"), ("verification_sha256", CANARY), ("endpoint", CANARY)):
            value = daily(); value["accepted_release"] = dict(release); value["accepted_release"][key] = changed
            value["gaps"].pop("accepted_release")
            with self.subTest(key=key), self.assertRaises(ValueError):
                adapters.validate_observation("daily_lingo_observer", value)

    def test_proxy_health_route_proof_and_counter_meaning_are_closed(self):
        for path, changed in ((("health",), "critical"), (("route", "status"), "unverified"),
                (("route", "binding_verified"), False), (("route", "evidence"), "not_exposed"),
                (("profiles", "profile-a", "metric"), "active_clients"),
                (("profiles", "profile-a", "metrics_status"), "ok"), (("coverage",), "all_mobile_clients")):
            value = proxy(); target = value
            for field in path[:-1]:
                target = target[field]
            target[path[-1]] = changed
            with self.subTest(path=path), self.assertRaises(ValueError):
                adapters.validate_observation("authenticated_proxy_route", value)
        value = proxy(binding=proxy_binding(route=False)); value["health"] = "ok"
        with self.assertRaises(ValueError):
            adapters.validate_observation("authenticated_proxy_route", value)

    def test_stale_source_or_failed_metric_cannot_gain_healthy_wire_claim(self):
        source = proxy_source(); source["started_utc"] = "2026-10-24T11:40:00Z"; source["completed_utc"] = "2026-10-24T11:41:00Z"
        value = proxy(source); value["health"] = "ok"
        with self.assertRaises(ValueError):
            adapters.validate_observation("authenticated_proxy_route", value)
        source = proxy_source(); source["profiles"]["profile-b"]["metrics"] = {"ok": False}
        value = proxy(source); value["profiles"]["profile-b"]["metric_value"] = 0
        with self.assertRaises(ValueError):
            adapters.validate_observation("authenticated_proxy_route", value)


if __name__ == "__main__":
    unittest.main()
