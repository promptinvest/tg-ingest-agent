#!/usr/bin/env python3
"""Additive trial continuation contracts. Imports and plan generation are inert.

The original trial/operator/monitor receipts are immutable. The caller stops only
the original fixed observer timer at the quiescent source boundary, installs the
separately qualified companion, and retains the original absolute deadline.
This module never changes a service, environment, database, grant or application.
Host mutation is deliberately confined to the separately reviewed release backend.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re

TRIAL_ROOT = "/root/cara-model-trial-20261025/attempt4"
TRIAL_ACCEPTANCE_SHA256 = "e67a3251c6591197dabab561a52307ea6436480da92cb1a429a19bbaa9f68c1e"
OLD_SOURCE = "ee1a319ce53ddc941ebf4b760c084abd865afd19"
OLD_ENV_SHA256 = "e1ae7f6c65e4076a1a6fb93a87ecf200eb59745f048183acbbea389cfa64b733"
TRIAL_ENV_SHA256 = "46d9c31a9dd24770b51f9830a6150ce407700912a04304abf1c153c29d1f9db5"
ORIGINAL_TIMER = "cara-model-trial-20261025-observer.timer"
SERVICE = "cara-model-trial-20261025-continuation-observer.service"
TIMER = "cara-model-trial-20261025-continuation-observer.timer"
DATABASE = "/var/lib/tg-ingest-agent/ingest.db"
APP = "/opt/tg-ingest-agent"
ENV = "/etc/tg-ingest-agent.env"
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
RUNTIME_NAME = re.compile(r"[a-z_][a-z_0-9-]*\.(?:py|sh)\Z")
MODEL_KEYS = frozenset({"DO_CHAT_MODEL", "ROUTER_MODEL", "LLM_PROFILES_JSON", "PRICING_JSON"})
REFERENCE_HASHES = {
    "trial_config.py": "d6c5fea9bb52316113d824342886272029be8f7778e79d7dda029b412cfdd7f8",
    "observe_trial.py": "9a53501ed1871c1a85ad511edaf9dff3a6282e1d3c2e40b23ad52e52254a0ca9",
}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def digest(value):
    return sha(canonical(value))


def decode(raw):
    require(isinstance(raw, bytes) and len(raw) <= 8 * 1024 * 1024, "bounded receipt bytes required")
    def unique(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate receipt key")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite receipt")))


def utc(value):
    require(isinstance(value, str) and len(value) <= 35 and value.endswith(("Z", "+00:00")), "UTC timestamp required")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.utcoffset() == timedelta(0), "UTC timestamp required")
    return result


def pin(value):
    require(isinstance(value, str) and HEX64.fullmatch(value), "SHA256 required")
    return value


def native_identity(value):
    require(isinstance(value, dict) and set(value) == {"sha256", "deployment_id", "source_revision", "build_version"}, "closed native identity required")
    pin(value["sha256"])
    require(isinstance(value["source_revision"], str) and HEX40.fullmatch(value["source_revision"]), "source revision required")
    require(re.fullmatch(r"[0-9a-f]{24}", value["deployment_id"] or ""), "deployment identity required")
    require(re.fullmatch(r"[0-9a-f]{12}", value["build_version"] or ""), "build identity required")
    return value


def runtime_map(value):
    require(isinstance(value, dict) and 91 <= len(value) <= 128, "complete reviewed installed runtime map required")
    require({"agent.py", "llm.py", "common.py", "verify-cara-runtime.sh"} <= set(value), "runtime map omits core files")
    for name, expected in value.items():
        require(isinstance(name, str) and RUNTIME_NAME.fullmatch(name), "invalid runtime file name")
        pin(expected)
    return value


def protected_map(value):
    require(isinstance(value, dict) and 78 <= len(value) <= 256 and ENV in value, "complete protected map required")
    for name, item in value.items():
        require(isinstance(name, str) and name.startswith("/") and "\x00" not in name and ".." not in Path(name).parts, "invalid protected path")
        require(isinstance(item, dict) and set(item) == {"sha256", "uid", "gid", "mode"}, "closed protected identity required")
        pin(item["sha256"])
        require(all(type(item[key]) is int and item[key] >= 0 for key in ("uid", "gid", "mode")), "invalid protected metadata")
        require(item["mode"] <= 0o7777 and not item["mode"] & 0o022, "protected file writable by other principal")
    return value


def original_trial(accepted_raw, prepared_raw, *, expected_acceptance_sha256=TRIAL_ACCEPTANCE_SHA256):
    # The explicit hash parameter is a pure fixture seam. Host callers MUST use
    # the fixed default; no CLI can select a different original trial.
    require(sha(accepted_raw) == expected_acceptance_sha256, "original trial acceptance changed")
    accepted, prepared = decode(accepted_raw), decode(prepared_raw)
    require(accepted.get("schema") == "cara.model-trial-acceptance/v1" and accepted.get("status") == "accepted", "accepted original trial required")
    require(prepared.get("schema") == "cara.model-trial-prepared/v1", "prepared original trial required")
    identity = native_identity(accepted.get("identity"))
    require(identity == prepared.get("identity") and identity["source_revision"] == OLD_SOURCE, "original runtime differs")
    for key, expected in (("before_env_sha256", OLD_ENV_SHA256), ("candidate_env_sha256", TRIAL_ENV_SHA256)):
        require(accepted.get(key) == prepared.get(key) == expected, "original model environment differs")
    require(accepted.get("budget_limits_usd") == prepared.get("budget_limits_usd") == [2.0, 12.0], "original budgets differ")
    require(accepted.get("code_installed") is False and accepted.get("database_restored") is False and accepted.get("operator_messages_sent") == 0, "original scope differs")
    require(accepted.get("runtime_hashes") == prepared.get("runtime_hashes"), "original runtime maps differ")
    runtime_map(accepted.get("runtime_hashes"))
    require(accepted.get("stopped_tables_preserved") == len(prepared.get("tables", {})) == 56, "original56 schema proof differs")
    require(prepared.get("database_path") == DATABASE, "original database path differs")
    start, end = utc(accepted.get("at")), utc(accepted.get("observation_until"))
    require(end == start + timedelta(hours=72), "original exact72h deadline required")
    return accepted, prepared, start, end


def validate_boundary(value, accepted, start, end):
    require(isinstance(value, dict) and set(value) == {"schema", "at", "trial_acceptance_sha256", "old_identity", "old_runtime_sha256", "main_stopped", "snapshot_sha256", "original_timer"}, "closed quiescent boundary required")
    require(value["schema"] == "cara.model-trial-runtime-boundary/v1" and value["trial_acceptance_sha256"] == TRIAL_ACCEPTANCE_SHA256, "boundary original trial binding differs")
    require(value["old_identity"] == accepted["identity"] and value["old_runtime_sha256"] == digest(accepted["runtime_hashes"]), "boundary old runtime differs")
    require(value["main_stopped"] is True and value["original_timer"] == ORIGINAL_TIMER, "quiescent fixed observer handoff required")
    pin(value["snapshot_sha256"])
    require(start <= utc(value["at"]) < end, "boundary outside original trial")
    return value


def validate_new_runtime(value, boundary, end):
    required = {"schema", "status", "at", "segment_started_at", "release_acceptance_sha256", "release_authorization_sha256", "identity", "runtime_hashes", "protected", "model_key_sha256s"}
    require(isinstance(value, dict) and set(value) == required, "closed new-runtime acceptance binding required")
    require(value["schema"] == "cara.capability-trial-runtime/v1" and value["status"] == "accepted", "new accepted runtime required")
    pin(value["release_acceptance_sha256"]); pin(value["release_authorization_sha256"])
    identity = native_identity(value["identity"])
    require(identity["source_revision"] != OLD_SOURCE and identity["deployment_id"] != boundary["old_identity"]["deployment_id"], "new runtime identity required")
    runtime_map(value["runtime_hashes"]); protected_map(value["protected"])
    require(value["protected"][ENV]["sha256"] == TRIAL_ENV_SHA256, "selected trial environment must remain exact during code release")
    require(isinstance(value["model_key_sha256s"], dict) and set(value["model_key_sha256s"]) == MODEL_KEYS, "all loaded model keys required")
    for expected in value["model_key_sha256s"].values():
        pin(expected)
    require(utc(boundary["at"]) <= utc(value["segment_started_at"]) <= utc(value["at"]) < end, "new runtime dates outside original trial")
    return value


def make_continuation(accepted_raw, prepared_raw, boundary_raw, new_runtime_raw, *, expected_acceptance_sha256=TRIAL_ACCEPTANCE_SHA256):
    accepted, prepared, start, end = original_trial(accepted_raw, prepared_raw, expected_acceptance_sha256=expected_acceptance_sha256)
    boundary = validate_boundary(decode(boundary_raw), accepted, start, end)
    new = validate_new_runtime(decode(new_runtime_raw), boundary, end)
    return {"schema": "cara.model-trial-continuation/v1", "trial_root": TRIAL_ROOT,
            "trial_acceptance_sha256": TRIAL_ACCEPTANCE_SHA256,
            "prepared_sha256": sha(prepared_raw), "boundary_sha256": sha(boundary_raw),
            "new_runtime_sha256": digest(new), "new_runtime_receipt_sha256": sha(new_runtime_raw), "trial_start": start.isoformat(),
            "trial_end": end.isoformat(), "old_runtime": accepted["identity"],
            "old_segment_until": boundary["at"], "new_segment_from": new["segment_started_at"],
            "new_runtime": new, "original_timer": ORIGINAL_TIMER, "service": SERVICE,
            "timer": TIMER, "model_configuration_unchanged": True,
            "automatic_reversion": False, "messages_sent": 0, "model_calls": 0,
            "original_cumulative_reports_after_boundary": "cross_runtime_not_model_only_evidence"}


def validate_continuation(value):
    require(isinstance(value, dict) and value.get("schema") == "cara.model-trial-continuation/v1", "continuation receipt required")
    require(value.get("trial_root") == TRIAL_ROOT and value.get("trial_acceptance_sha256") == TRIAL_ACCEPTANCE_SHA256, "continuation original binding differs")
    start, end = utc(value.get("trial_start")), utc(value.get("trial_end"))
    require(end == start + timedelta(hours=72), "cannot extend trial deadline")
    require(value.get("original_timer") == ORIGINAL_TIMER and value.get("service") == SERVICE and value.get("timer") == TIMER, "fixed observer units required")
    for key in ("prepared_sha256", "boundary_sha256", "new_runtime_sha256", "new_runtime_receipt_sha256"):
        pin(value.get(key))
    require(digest(value.get("new_runtime")) == value["new_runtime_sha256"], "new runtime receipt changed")
    require(start <= utc(value.get("old_segment_until")) <= utc(value.get("new_segment_from")) < end, "invalid runtime windows")
    require(value.get("automatic_reversion") is False and value.get("messages_sent") == value.get("model_calls") == 0 and value.get("model_configuration_unchanged") is True, "continuation scope differs")
    return start, end


def remaining_dates(continuation, now):
    start, end = validate_continuation(continuation)
    current = utc(now)
    require(start <= current < end, "no installation after absolute deadline")
    dates = []
    for hours in range(6, 73, 6):
        moment = start + timedelta(hours=hours)
        if moment.microsecond:
            moment = moment.replace(microsecond=0) + timedelta(seconds=1)
        if moment > current:
            dates.append(moment.strftime("%Y-%m-%d %H:%M:%S UTC"))
    require(dates, "no remaining original timer dates")
    return dates


def segment_window(continuation, now):
    start, end = validate_continuation(continuation)
    current = utc(now)
    segment = utc(continuation["new_segment_from"])
    require(current >= segment, "new runtime segment has not started")
    return segment.isoformat(), min(current, end).isoformat(), current >= end


def load_reference(name):
    require(name in REFERENCE_HASHES, "unknown frozen reference")
    path = Path(__file__).resolve().parent / "reference" / name
    require(sha(path.read_bytes()) == REFERENCE_HASHES[name], "frozen reference changed")
    spec = importlib.util.spec_from_file_location("cara_capability_frozen_" + name[:-3], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def model_key_hashes(env):
    return {key: sha((env.get(key) or "").encode("utf-8")) for key in sorted(MODEL_KEYS)}


def merge_revert(current_raw, original_raw, candidate_raw):
    """Only the four original trial keys may change; source/data are untouched."""
    helper = load_reference("trial_config.py")
    old, candidate, current = map(helper.parse_env_bytes, (original_raw, candidate_raw, current_raw))
    result = helper.revert_env_bytes(current_raw, old, candidate)
    after = helper.parse_env_bytes(result)
    changed = {key for key in set(current) | set(after) if current.get(key) != after.get(key)}
    require(changed <= MODEL_KEYS, "revert changed unrelated configuration")
    return result


def verify_revert_preflight(value, continuation, *, now):
    validate_continuation(continuation)
    required = {"schema", "at", "until", "continuation_sha256", "identity", "runtime_hashes", "protected", "main_process", "peers", "environment_sha256", "model_key_sha256s", "table_schema_sha256", "tables", "idle"}
    require(isinstance(value, dict) and set(value) == required and value["schema"] == "cara.model-trial-new-runtime-revert-preflight/v1", "closed new-runtime revert preflight required")
    require(value["continuation_sha256"] == digest(continuation), "revert continuation binding differs")
    new = continuation["new_runtime"]
    require(value["identity"] == new["identity"] and value["runtime_hashes"] == new["runtime_hashes"], "revert requires accepted new runtime")
    before, after = new["protected"], protected_map(value["protected"])
    require(set(before) == set(after), "protected coverage changed")
    require(all(after[path] == expected for path, expected in before.items() if path != ENV), "unrelated protected drift requires review")
    require({k: after[ENV][k] for k in ("uid", "gid", "mode")} == {k: before[ENV][k] for k in ("uid", "gid", "mode")}, "environment ownership changed")
    require(after[ENV]["sha256"] == value["environment_sha256"], "fresh environment hash differs")
    require(value["model_key_sha256s"] == new["model_key_sha256s"], "trial model keys changed; refuse overwrite")
    process = value["main_process"]
    require(isinstance(process, dict) and set(process) == {"pid", "start_ticks", "uid", "active_enter"} and all(type(process[k]) is int and process[k] > 0 for k in ("pid", "start_ticks", "uid")) and process["pid"] > 1, "actual nonroot PID binding required")
    utc(process["active_enter"])
    require(value["tables"] == 56 and value["idle"] is True, "fresh56-table idle proof required")
    pin(value["table_schema_sha256"]); pin(value["environment_sha256"])
    require(utc(value["at"]) <= utc(now) <= utc(value["until"]) and timedelta(0) < utc(value["until"]) - utc(value["at"]) <= timedelta(minutes=35), "fresh bounded revert window required")
    return value
