"""Pure projections of existing monitor evidence; never collect or refresh it.

Bindings are privileged fixed-resource policy, not model arguments. Raw observer
rows, endpoints, boot IDs, profile ports, errors and configuration hashes are
never copied into project observations. These functions do no I/O or discovery.
"""
import ipaddress
import json
import math
import re
from datetime import datetime, timezone

MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_BYTES = 10000
CELLS = ("nb/A2", "nb/B1", "nb/B2", "fr/A2", "fr/B1", "fr/B2")
ID = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")
STAMP = re.compile(r"\d{8}T\d{12}Z\Z")
BUDGET_OUTCOMES = frozenset({"waiting_first_cycle", "within_limit", "unknown_daily_usage",
    "daily_base_ceiling_reached", "daily_monitor_unavailable"})
ADAPTERS = {
    "daily_lingo_observer": {"implementation_version": "project-daily-lingo-observer/v1",
        "source": "existing-read-only-observer/v1", "maximum_source_bytes": MAX_SOURCE_BYTES,
        "maximum_bytes": MAX_OUTPUT_BYTES, "outputs": ["source_id", "source_observed_at", "collected_at", "max_age_seconds", "budget_max_age_seconds", "fresh",
            "generation", "reserves", "budget_guard", "application", "accepted_release", "target", "gaps"]},
    "authenticated_proxy_route": {"implementation_version": "project-authenticated-proxy-route/v1",
        "source": "existing-mtproto-health/v1", "maximum_source_bytes": MAX_SOURCE_BYTES,
        "maximum_bytes": MAX_OUTPUT_BYTES, "outputs": ["source_id", "source_observed_at", "collected_at", "max_age_seconds",
            "fresh", "health", "profiles", "route", "coverage", "gaps"]},
}


def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise ValueError("invalid monitor document") from None


def _document(value):
    if not isinstance(value, dict) or len(_canonical(value).encode("utf-8")) > MAX_SOURCE_BYTES:
        raise ValueError("invalid or oversized monitor document")
    return value


def _keys(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        raise ValueError("unsupported monitor binding fields")


def _id(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError("invalid monitor identifier")
    return value


def _hash(value):
    if not isinstance(value, str) or not HASH.fullmatch(value):
        raise ValueError("invalid monitor binding digest")
    return value


def _image(value):
    if not isinstance(value, str) or not value.startswith("sha256:"):
        raise ValueError("invalid monitor image binding")
    _hash(value[7:])
    return value


def _integer(value, low=0, high=2**53-1):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("invalid monitor integer")
    return value


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 2**53-1:
        raise ValueError("invalid monitor number")
    return value


def _bool(value):
    if type(value) is not bool:
        raise ValueError("invalid monitor boolean")
    return value


def _time(value):
    if not isinstance(value, str) or len(value) > 35 or not (value.endswith("Z") or value.endswith("+00:00")):
        raise ValueError("UTC monitor date required")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("invalid monitor date") from None
    if result.utcoffset() != timezone.utc.utcoffset(result):
        raise ValueError("UTC monitor date required")
    return result


def _fresh(value, now, age):
    delta = (now - _time(value)).total_seconds()
    if delta < -30:
        raise ValueError("monitor observation is in the future")
    return delta <= age


def _path(value):
    if not isinstance(value, str) or not value.startswith("/") or len(value) > 1024 or "\\" in value:
        raise ValueError("fixed absolute monitor path required")
    if any(ord(char) < 32 or ord(char) == 127 for char in value) or any(part in {"", ".", ".."} for part in value.split("/")[1:]):
        raise ValueError("invalid fixed monitor path")
    return value


def _base(kind, binding, observed_at):
    _id(binding["source_id"])
    _time(observed_at)
    return {"status": "recorded", "implementation_version": ADAPTERS[kind]["implementation_version"],
            "observed_at": observed_at, "source_id": binding["source_id"], "gaps": {}}


def _bounded(value):
    if len(_canonical(value).encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise ValueError("monitor projection exceeds output limit")
    return value


def _stock(value, partial=False):
    if not isinstance(value, dict) or set(value) - set(CELLS) or (not partial and set(value) != set(CELLS)):
        raise ValueError("invalid observer stock cells")
    return {cell: _integer(value[cell], 0, 1000000) for cell in CELLS if cell in value}


def _release(binding, now):
    """A separately verified root binding, never an inference from a container."""
    value = binding.get("accepted_release")
    if value is None:
        return None, None
    _keys(value, {"version", "revision", "accepted_at", "verification_sha256", "image",
                  "manifest_sha256", "step5_receipt_sha256"}, {"targets"})
    if value["version"] != "verified-project-release/v1" or not isinstance(value["revision"], str) or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", value["revision"]):
        raise ValueError("invalid verified release binding")
    for key in ("verification_sha256", "manifest_sha256", "step5_receipt_sha256"):
        _hash(value[key])
    _image(value["image"])
    accepted = _time(value["accepted_at"])
    if (now is not None and accepted > now) or any(value[key] != binding[key] for key in ("image", "manifest_sha256", "step5_receipt_sha256")):
        raise ValueError("verified release does not bind this observer")
    release = {"revision": value["revision"], "accepted_at": value["accepted_at"],
               "verification_sha256": value["verification_sha256"], "image": value["image"]}
    targets = _stock(value["targets"]) if "targets" in value else None
    return release, targets


def daily_lingo_observer(latest, snapshot, *, binding, observed_at, source_paths):
    """Project the existing observer's result + its exact immutable snapshot.

    The caller reads only bound files. source_paths records which files it read;
    latest.snapshot can select only the observer's fixed timestamp basename.
    Accepted release/targets are absent unless separately verified root policy
    binds that release to the same image, manifest and paused-readiness receipt.
    """
    validate_binding("daily_lingo_observer", binding)
    _keys(source_paths, {"latest", "snapshot"})
    if _path(source_paths["latest"]) != _path(binding["latest_path"]):
        raise ValueError("observer source path mismatch")
    root = _path(binding["snapshot_root"])
    latest = _document(latest); snapshot = _document(snapshot)
    stamp = latest.get("snapshot")
    if not isinstance(stamp, str) or not STAMP.fullmatch(stamp):
        raise ValueError("invalid observer snapshot reference")
    try:
        datetime.strptime(stamp, "%Y%m%dT%H%M%S%fZ")
    except ValueError:
        raise ValueError("invalid observer snapshot reference") from None
    if _path(source_paths["snapshot"]) != root + "/" + stamp + ".json":
        raise ValueError("observer snapshot path mismatch")
    if latest.get("status") != "observed" or latest.get("read_only") is not True or snapshot.get("read_only") is not True or snapshot.get("transaction_read_only") is not True or snapshot.get("transaction_isolation") != "repeatable read":
        raise ValueError("observer read-only proof missing")
    if latest.get("manifest_sha256") != binding["manifest_sha256"] or snapshot.get("image") != binding["image"]:
        raise ValueError("observer release binding mismatch")
    if latest.get("captured_at") != snapshot.get("captured_at"):
        raise ValueError("observer capture binding mismatch")
    now = _time(observed_at); captured = _time(snapshot.get("captured_at"))
    collector_at = _time(snapshot["collector_at"]) if "collector_at" in snapshot else now
    if collector_at < captured or (collector_at - now).total_seconds() > 30:
        raise ValueError("invalid observer collection date")
    fresh = _fresh(snapshot["captured_at"], now, binding["max_age_seconds"])
    stocks = _stock(snapshot.get("stock"), partial=True)
    if stocks != _stock(latest.get("stock"), partial=True):
        raise ValueError("observer stock copy mismatch")
    stock_at = snapshot.get("stock_as_of")
    if _time(stock_at) > captured:
        raise ValueError("observer stock date exceeds capture")
    stock_fresh = _fresh(stock_at, now, binding["max_age_seconds"])
    value = _base("daily_lingo_observer", binding, observed_at)
    value.update(source_observed_at=snapshot["captured_at"], collected_at=snapshot.get("collector_at"),
        max_age_seconds=binding["max_age_seconds"], budget_max_age_seconds=binding["budget_max_age_seconds"], fresh=fresh,
        generation={"enabled": _bool(snapshot.get("generation_enabled")), "as_of": snapshot["captured_at"], "fresh": fresh,
                    "stop_recorded": snapshot.get("stop_reason") is not None},
        reserves={"as_of": stock_at, "fresh": stock_fresh, "cells": stocks},
        budget_guard=None, application={}, accepted_release=None, target=None)
    if not fresh:
        value["gaps"]["observer"] = "stale"
    if not stock_fresh:
        value["gaps"]["reserves"] = "stale"
    if set(stocks) != set(CELLS):
        value["gaps"]["reserve_coverage"] = "partial_coverage"
    for key in ("app_health", "postgres_health"):
        state = snapshot.get(key)
        if state is None:
            value["gaps"][key] = "not_exposed"
        elif isinstance(state, str) and state in {"healthy", "unhealthy", "starting"}:
            value["application"][key] = state
        else:
            raise ValueError("invalid observer application state")
    for key in ("unfinished_calls", "unfinished_refills"):
        if key in snapshot:
            value["application"][key] = _integer(snapshot[key], 0, 1000000)
        else:
            value["gaps"][key] = "not_exposed"
    guard = snapshot.get("budget_guard")
    if guard is None:
        value["gaps"]["budget_guard"] = "not_connected"
    else:
        if not isinstance(guard, dict):
            raise ValueError("invalid observer budget guard")
        active = _bool(guard.get("active")); heartbeat = guard.get("heartbeat")
        terminal = guard.get("terminal_outcome")
        if terminal is not None and (not isinstance(terminal, str) or terminal not in BUDGET_OUTCOMES):
            raise ValueError("unsupported observer terminal outcome")
        if heartbeat is None:
            value["gaps"]["budget_guard"] = "observation_unavailable"
            value["budget_guard"] = {"active": active, "heartbeat_at": None, "probe_at": None,
                "fresh": False, "daily_limit_usd": None, "outcome": None, "terminal_outcome": terminal}
        else:
            if not isinstance(heartbeat, dict) or heartbeat.get("image") != binding["image"] or heartbeat.get("step5_receipt_sha256") != binding["step5_receipt_sha256"]:
                raise ValueError("budget guard release binding mismatch")
            heartbeat_at = heartbeat.get("captured_at"); probe_at = heartbeat.get("probe_at")
            if _time(probe_at) > _time(heartbeat_at) or _time(heartbeat_at) > collector_at:
                raise ValueError("budget guard date exceeds observer collection")
            budget_fresh = _fresh(heartbeat_at, now, binding["budget_max_age_seconds"]) and _fresh(probe_at, now, binding["budget_max_age_seconds"])
            outcome = heartbeat.get("outcome")
            if not isinstance(outcome, str) or outcome not in BUDGET_OUTCOMES:
                raise ValueError("unsupported budget guard outcome")
            limit = _number(heartbeat.get("daily_limit_usd"))
            if limit == 0:
                raise ValueError("invalid budget guard ceiling")
            value["budget_guard"] = {"active": active, "heartbeat_at": heartbeat_at, "probe_at": probe_at,
                "fresh": budget_fresh, "daily_limit_usd": limit, "outcome": outcome, "terminal_outcome": terminal}
            if not budget_fresh:
                value["gaps"]["budget_guard"] = "stale"
            elif not active and terminal is None:
                value["gaps"]["budget_guard"] = "monitor_inactive"
    release, targets = _release(binding, now)
    value["accepted_release"] = release
    if release is None:
        value["gaps"]["accepted_release"] = "not_connected"
    if targets is None:
        value["gaps"]["target"] = "not_connected"
    else:
        value["target"] = {"cells": targets, "verified_at": release["accepted_at"],
                           "verification_sha256": release["verification_sha256"]}
    return validate_observation("daily_lingo_observer", value)


def authenticated_proxy_route(document, *, binding, observed_at, source_path):
    """Read an existing authenticated host receipt or its existing fleet cache.

    Recompute health from protocol, exact route and metric proofs. An outer cache
    timestamp cannot refresh an old host sample. Connection counters remain
    cumulative counters; they never become active-client or throughput claims.
    """
    validate_binding("authenticated_proxy_route", binding)
    if _path(source_path) != binding["path"]:
        raise ValueError("proxy source path mismatch")
    expected = binding["profiles"]
    document = _document(document); now = _time(observed_at); collected_at = None; outer_fresh = True
    if binding["source_kind"] == "collector":
        if type(document.get("schema")) is not int or document["schema"] != 1 or not isinstance(document.get("hosts"), dict):
            raise ValueError("invalid proxy collector")
        collected_at = document.get("collected_utc")
        outer_fresh = _fresh(collected_at, now, binding["max_age_seconds"])
        document = document["hosts"].get(binding["role"])
        if not isinstance(document, dict):
            raise ValueError("proxy collector lacks bound host")
    elif binding["source_kind"] != "host":
        raise ValueError("unsupported proxy source kind")
    if type(document.get("schema")) is not int or document["schema"] != 1 or document.get("role") != binding["role"] or document.get("source_sha256") != binding["source_sha256"]:
        raise ValueError("proxy monitor binding mismatch")
    if "expected_boot_id" in binding:
        boot = binding["expected_boot_id"]
        if not isinstance(boot, str) or not re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", boot) or document.get("boot_id") != boot:
            raise ValueError("proxy boot binding mismatch")
    started = _time(document.get("started_utc")); completed_at = document.get("completed_utc")
    completed = _time(completed_at)
    if started > completed or (collected_at is not None and completed > _time(collected_at)):
        raise ValueError("invalid proxy observation interval")
    source_fresh = _fresh(completed_at, now, binding["max_age_seconds"])
    fresh = outer_fresh and source_fresh
    profiles = document.get("profiles")
    if not isinstance(profiles, dict) or set(profiles) != set(expected):
        raise ValueError("proxy profile scope mismatch")
    value = _base("authenticated_proxy_route", binding, observed_at)
    value.update(source_observed_at=completed_at, collected_at=collected_at, max_age_seconds=binding["max_age_seconds"], fresh=fresh,
                 health="unknown", profiles={}, route=None, coverage="sampled_authenticated_route_only")
    if not outer_fresh:
        value["gaps"]["collector"] = "stale"
    if not source_fresh:
        value["gaps"]["protocol_observation"] = "stale"
    protocols_ok, metrics_ok = True, True
    for name in sorted(expected):
        source = profiles[name]
        if not isinstance(source, dict) or source.get("mode") != expected[name]["mode"]:
            raise ValueError("proxy profile mode binding mismatch")
        protocol, metrics = source.get("protocol"), source.get("metrics")
        if not isinstance(protocol, dict) or type(protocol.get("resPQ_nonce_verified")) is not bool or not isinstance(metrics, dict) or type(metrics.get("ok")) is not bool:
            raise ValueError("invalid proxy observation proof")
        verified = protocol["resPQ_nonce_verified"]
        protocols_ok = protocols_ok and verified
        latency = None
        if verified:
            mode = "ee-FakeTLS" if expected[name]["mode"] == "FakeTLS" else "dd-secure"
            if protocol.get("mode") != mode:
                raise ValueError("proxy protocol mode mismatch")
            latency = _number(protocol.get("seconds"))
        metric = expected[name]["metric"]
        metric_ok = metrics["ok"]
        metric_value = _number(metrics.get(metric)) if metric_ok else None
        metrics_ok = metrics_ok and metric_ok
        value["profiles"][name] = {"mode": expected[name]["mode"], "protocol_verified": verified,
            "latency_seconds": latency, "metric": metric, "metric_value": metric_value,
            "metrics_status": "recorded" if metric_ok else "unavailable"}
        if not metric_ok:
            value["gaps"]["metrics_" + name] = "observation_unavailable"
    route_ok = True
    if "route" in binding:
        route_policy = binding["route"]
        address = route_policy["address"]
        tunnel = document.get("tunnel")
        if not isinstance(tunnel, dict):
            route_ok = False
            value["gaps"]["route"] = "not_exposed"
        else:
            flags = ("ok", "mtg_socks_binding_verified", "xray_and_fail_closed_unit_active")
            for flag in flags:
                _bool(tunnel.get(flag))
            route_ok = all(tunnel[flag] for flag in flags) and tunnel.get("configured_active_address") == address and type(tunnel.get("configured_active_port")) is int and tunnel.get("configured_active_port") == route_policy["port"] and tunnel.get("xray_config_sha256") == route_policy["config_sha256"]
            if not route_ok:
                value["gaps"]["route"] = "binding_or_route_unverified"
        value["route"] = {"status": "verified" if route_ok and protocols_ok and fresh else
            "stale" if not fresh else "unverified", "as_of": completed_at,
            "binding_verified": route_ok, "evidence": "recorded" if isinstance(tunnel, dict) else "not_exposed"}
    else:
        value["gaps"]["route"] = "not_configured"
    if fresh:
        value["health"] = "critical" if not protocols_ok or not route_ok else "ok" if metrics_ok and "route" in binding else "needs_attention"
    return validate_observation("authenticated_proxy_route", value)


def validate_binding(kind, binding):
    """Validate fixed privileged policy for discovery without touching sources.

    The returned object is the supplied policy. It must never be placed in a
    receipt: paths, expected route and private readiness bindings stay local.
    """
    if not isinstance(kind, str) or kind not in ADAPTERS:
        raise ValueError("unknown monitor adapter")
    if kind == "daily_lingo_observer":
        _keys(binding, {"source_id", "latest_path", "snapshot_root", "image", "manifest_sha256",
                        "step5_receipt_sha256", "max_age_seconds", "budget_max_age_seconds"}, {"accepted_release"})
        _id(binding["source_id"]); _path(binding["latest_path"]); _path(binding["snapshot_root"])
        _image(binding["image"]); _hash(binding["manifest_sha256"]); _hash(binding["step5_receipt_sha256"])
        _integer(binding["max_age_seconds"], 1, 90); _integer(binding["budget_max_age_seconds"], 1, 150)
        _release(binding, None)
        return binding
    _keys(binding, {"source_id", "path", "source_kind", "role", "source_sha256", "profiles", "max_age_seconds"},
          {"expected_boot_id", "route"})
    _id(binding["source_id"]); _path(binding["path"]); _id(binding["role"]); _hash(binding["source_sha256"])
    _integer(binding["max_age_seconds"], 1, 660)
    if binding["source_kind"] not in ("host", "collector"):
        raise ValueError("unsupported proxy source kind")
    profiles = binding["profiles"]
    if not isinstance(profiles, dict) or not 1 <= len(profiles) <= 16:
        raise ValueError("invalid proxy profile binding")
    for name, profile in profiles.items():
        _id(name); _keys(profile, {"mode", "metric"})
        _enum(profile["mode"], {"FakeTLS", "secure"})
        _enum(profile["metric"], {"current_connections", "connection_counter"})
    if "expected_boot_id" in binding:
        boot = binding["expected_boot_id"]
        if not isinstance(boot, str) or not re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", boot):
            raise ValueError("invalid proxy boot binding")
    if "route" in binding:
        route = binding["route"]
        _keys(route, {"address", "port", "config_sha256"})
        address = route["address"]
        if not isinstance(address, str) or not 1 <= len(address) <= 253:
            raise ValueError("invalid proxy route binding")
        try:
            ipaddress.ip_address(address)
        except ValueError:
            labels = address.split(".")
            if any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in labels):
                raise ValueError("invalid proxy route binding") from None
        _integer(route["port"], 1, 65535); _hash(route["config_sha256"])
    return binding


def _enum(value, allowed):
    if not isinstance(value, str) or value not in allowed:
        raise ValueError("unsupported monitor state")
    return value


def _projection_release(value, now):
    _keys(value, {"revision", "accepted_at", "verification_sha256", "image"})
    if not isinstance(value["revision"], str) or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", value["revision"]):
        raise ValueError("invalid monitor release revision")
    _hash(value["verification_sha256"]); _image(value["image"])
    if _time(value["accepted_at"]) > now:
        raise ValueError("monitor release acceptance is in the future")


def _validate_daily(value, now):
    age = _integer(value["max_age_seconds"], 1, 90)
    budget_age = _integer(value["budget_max_age_seconds"], 1, 150)
    captured = _time(value["source_observed_at"])
    collected = _time(value["collected_at"]) if value["collected_at"] is not None else now
    if collected < captured or (collected - now).total_seconds() > 30:
        raise ValueError("invalid observer projection collection date")
    fresh = _fresh(value["source_observed_at"], now, age)
    if _bool(value["fresh"]) != fresh:
        raise ValueError("observer projection changed source freshness")
    gaps = {} if fresh else {"observer": "stale"}
    generation = value["generation"]
    _keys(generation, {"enabled", "as_of", "fresh", "stop_recorded"})
    _bool(generation["enabled"]); _bool(generation["stop_recorded"])
    if generation["as_of"] != value["source_observed_at"] or _bool(generation["fresh"]) != fresh:
        raise ValueError("observer generation lost its source date")
    reserves = value["reserves"]
    _keys(reserves, {"as_of", "fresh", "cells"})
    if _time(reserves["as_of"]) > captured:
        raise ValueError("reserve projection date exceeds capture")
    reserve_fresh = _fresh(reserves["as_of"], now, age)
    if _bool(reserves["fresh"]) != reserve_fresh:
        raise ValueError("reserve projection changed source freshness")
    cells = _stock(reserves["cells"], partial=True)
    if not reserve_fresh:
        gaps["reserves"] = "stale"
    if set(cells) != set(CELLS):
        gaps["reserve_coverage"] = "partial_coverage"
    application = value["application"]
    _keys(application, set(), {"app_health", "postgres_health", "unfinished_calls", "unfinished_refills"})
    for key in ("app_health", "postgres_health", "unfinished_calls", "unfinished_refills"):
        if key not in application:
            gaps[key] = "not_exposed"
        elif key.endswith("health"):
            _enum(application[key], {"healthy", "unhealthy", "starting"})
        else:
            _integer(application[key], 0, 1000000)
    guard = value["budget_guard"]
    if guard is None:
        gaps["budget_guard"] = "not_connected"
    else:
        _keys(guard, {"active", "heartbeat_at", "probe_at", "fresh", "daily_limit_usd", "outcome", "terminal_outcome"})
        _bool(guard["active"]); _bool(guard["fresh"])
        terminal = guard["terminal_outcome"]
        if terminal is not None:
            _enum(terminal, BUDGET_OUTCOMES)
        if guard["heartbeat_at"] is None:
            if guard["probe_at"] is not None or guard["daily_limit_usd"] is not None or guard["outcome"] is not None or guard["fresh"]:
                raise ValueError("missing budget heartbeat invented evidence")
            gaps["budget_guard"] = "observation_unavailable"
        else:
            heartbeat = _time(guard["heartbeat_at"]); probe = _time(guard["probe_at"])
            if probe > heartbeat or heartbeat > collected:
                raise ValueError("invalid budget projection observation dates")
            expected_fresh = _fresh(guard["heartbeat_at"], now, budget_age) and _fresh(guard["probe_at"], now, budget_age)
            if guard["fresh"] != expected_fresh or _number(guard["daily_limit_usd"]) == 0:
                raise ValueError("budget projection changed freshness or ceiling")
            _enum(guard["outcome"], BUDGET_OUTCOMES)
            if not expected_fresh:
                gaps["budget_guard"] = "stale"
            elif not guard["active"] and terminal is None:
                gaps["budget_guard"] = "monitor_inactive"
    release = value["accepted_release"]
    if release is None:
        gaps["accepted_release"] = "not_connected"
    else:
        _projection_release(release, now)
    target = value["target"]
    if target is None:
        gaps["target"] = "not_connected"
    else:
        _keys(target, {"cells", "verified_at", "verification_sha256"})
        _stock(target["cells"]); _time(target["verified_at"]); _hash(target["verification_sha256"])
        if release is None or target["verified_at"] != release["accepted_at"] or target["verification_sha256"] != release["verification_sha256"]:
            raise ValueError("reserve target lacks matching release verification")
    if value["gaps"] != gaps:
        raise ValueError("observer projection changed required evidence gaps")


def _validate_proxy(value, now):
    age = _integer(value["max_age_seconds"], 1, 660)
    source_at = _time(value["source_observed_at"])
    source_fresh = _fresh(value["source_observed_at"], now, age)
    collector_fresh = True
    if value["collected_at"] is not None:
        if source_at > _time(value["collected_at"]):
            raise ValueError("proxy projection source is newer than collector")
        collector_fresh = _fresh(value["collected_at"], now, age)
    fresh = source_fresh and collector_fresh
    if _bool(value["fresh"]) != fresh or value["coverage"] != "sampled_authenticated_route_only":
        raise ValueError("proxy projection changed observation coverage")
    gaps = {}
    if not source_fresh:
        gaps["protocol_observation"] = "stale"
    if not collector_fresh:
        gaps["collector"] = "stale"
    profiles = value["profiles"]
    if not isinstance(profiles, dict) or not 1 <= len(profiles) <= 16:
        raise ValueError("invalid proxy projection profiles")
    protocols_ok, metrics_ok = True, True
    for name, profile in profiles.items():
        _id(name)
        _keys(profile, {"mode", "protocol_verified", "latency_seconds", "metric", "metric_value", "metrics_status"})
        _enum(profile["mode"], {"FakeTLS", "secure"})
        verified = _bool(profile["protocol_verified"])
        protocols_ok = protocols_ok and verified
        if verified:
            _number(profile["latency_seconds"])
        elif profile["latency_seconds"] is not None:
            raise ValueError("unverified protocol invented latency evidence")
        _enum(profile["metric"], {"current_connections", "connection_counter"})
        state = _enum(profile["metrics_status"], {"recorded", "unavailable"})
        if state == "recorded":
            _number(profile["metric_value"])
        else:
            if profile["metric_value"] is not None:
                raise ValueError("unavailable proxy metric invented a value")
            gaps["metrics_" + name] = "observation_unavailable"
            metrics_ok = False
    route = value["route"]; route_ok = True
    if route is None:
        gaps["route"] = "not_configured"
    else:
        _keys(route, {"status", "as_of", "binding_verified", "evidence"})
        if route["as_of"] != value["source_observed_at"]:
            raise ValueError("proxy route lost its observation date")
        route_ok = _bool(route["binding_verified"])
        evidence = _enum(route["evidence"], {"recorded", "not_exposed"})
        if evidence == "not_exposed":
            if route_ok:
                raise ValueError("missing proxy route invented verification")
            gaps["route"] = "not_exposed"
        elif not route_ok:
            gaps["route"] = "binding_or_route_unverified"
        expected_state = "verified" if route_ok and protocols_ok and fresh else "stale" if not fresh else "unverified"
        if route["status"] != expected_state:
            raise ValueError("proxy projection changed authenticated route meaning")
    expected_health = "unknown" if not fresh else "critical" if not protocols_ok or not route_ok else "ok" if metrics_ok and route is not None else "needs_attention"
    if value["health"] != expected_health or value["gaps"] != gaps:
        raise ValueError("proxy projection changed health or evidence gaps")


def validate_observation(kind, value):
    """Check a closed sanitized receipt, never reopen or authenticate its source.

    Scope, protected source transport and root-policy binding are gateway duties.
    This API checks typed claims, all required gaps and date/freshness semantics.
    """
    if not isinstance(kind, str) or kind not in ADAPTERS or not isinstance(value, dict):
        raise ValueError("unknown monitor observation")
    base = {"status", "implementation_version", "observed_at"}
    if not base <= set(value) or value["implementation_version"] != ADAPTERS[kind]["implementation_version"]:
        raise ValueError("unsupported monitor observation version")
    now = _time(value["observed_at"])
    state = _enum(value["status"], {"recorded", "not_configured", "unavailable", "not_found"})
    if state != "recorded":
        _keys(value, base | {"reason"})
        if value["reason"] != {"not_configured": "not_configured", "unavailable": "observation_unavailable", "not_found": "resource_not_found"}[state]:
            raise ValueError("invalid monitor failure reason")
        return _bounded(value)
    _keys(value, base | set(ADAPTERS[kind]["outputs"]))
    _id(value["source_id"])
    if not isinstance(value["gaps"], dict):
        raise ValueError("invalid monitor projection gaps")
    if kind == "daily_lingo_observer":
        _validate_daily(value, now)
    else:
        _validate_proxy(value, now)
    return _bounded(value)
