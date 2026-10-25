#!/usr/bin/env python3
"""Metadata-only Cara trial observations; never changes application state.

``snapshot`` reads one explicit SQLite file and writes a new exclusive report.
``scheduled`` emits at most one report per six-hour slot, plus the final 72-hour
report. The caller must stop its fixed timer when timer_stop_required is true.
Neither command installs units, probes models, sends messages, or reverts config.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import statistics
import subprocess
import tempfile
import time


SCHEMA = "cara.model-trial-observation/v1"
TRIAL_HOURS = 72
SLOT_HOURS = 6
MAX_ROWS = 200_000
TABLES = frozenset({"llm_usage", "traces", "trace_events", "reply_outbox", "issues"})
READ_COLUMNS = {
    "sqlite_master": frozenset({"name", "type"}),
    "llm_usage": frozenset({"ts", "skill", "kind", "model", "tokens_in", "tokens_out", "cost_usd", "seconds"}),
    "traces": frozenset({"kind", "status", "started_at", "finished_at"}),
    "trace_events": frozenset({"ts", "stage", "skill"}),
    "reply_outbox": frozenset({"state", "created_at"}),
    "issues": frozenset({"ts", "kind", "status"}),
}
SERVICES = ("tg-ingest-agent.service", "cara-worker.service",
            "cara-fleet.service", "whisper-server.service")
STAGES = ("llm.fallback", "llm.failover_served", "llm.failover_failed",
          "llm.length_retry", "llm.all_benched")
ORDINARY_SKILLS = frozenset({
    "router", "ingest", "ask", "converse", "recall_conversation", "media",
    "memory_curator", "memory_consolidate", "task_planner", "task_synthesis",
    "improvement_evaluator", "fleet_planner", "fleet_synthesis", "fleet_scope",
    "fleet_semantics", "fleet_query", "stt", "embed", "review", "reminders",
    "spend", "journals", "knowledge", "learning", "proactive", "task",
})
# Public API identifiers only. Unknown/corrupt metadata never becomes report text.
MODELS = frozenset({
    "deepseek-4-flash", "deepseek-v4-pro", "deepseek-v4-flash", "deepseek-v3.2",
    "openai-gpt-oss-20b", "openai-gpt-oss-120b", "llama-4-maverick", "kimi-k2.6",
    "anthropic-claude-5.5-haiku", "openai-gpt-6-1-sol",
    "claude-haiku-5-5", "claude-sonnet-5-5", "anthropic-claude-haiku-5.5",
    "anthropic-claude-sonnet-5.5", "claude-haiku-5.5", "claude-sonnet-5.5",
    "anthropic-claude-haiku-5-5", "anthropic-claude-sonnet-5-5",
    "openai-gpt-6.1-sol", "gpt-6.1-sol", "BGE-M3", "bge-m3",
    "whisper.cpp-server", "whisper.cpp-local", "whisper-large-v3",
})
USAGE_KINDS = frozenset(base + kind for base in ("", "unknown_", "rejected_")
                        for kind in ("chat", "embed", "stt"))
TRACE_STATUSES = frozenset({"running", "ok", "failed", "suppressed"})
OUTBOX_STATES = ("pending", "sending", "sent", "failed", "ambiguous")
ISSUE_STATUSES = frozenset({"open", "resolved", "closed", "ignored"})
ISSUE_KINDS = frozenset({"llm", "router", "delivery", "error", "model",
                         "budget", "grounding", "task", "fleet", "reminder"})
ACTIVE_STATES = frozenset({"active", "inactive", "failed", "activating",
                          "deactivating", "reloading", "maintenance", "refreshing"})


class ObserverError(Exception):
    """Only fixed public error codes may leave the observer."""


def utc(value):
    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            raise ObserverError("invalid_timestamp") from None
    if result.tzinfo is None:
        raise ObserverError("timestamp_timezone_required")
    return result.astimezone(timezone.utc)


def iso(value):
    return utc(value).isoformat()


def latency(values):
    samples = sorted(float(x) for x in values if isinstance(x, (int, float))
                     and math.isfinite(x) and x >= 0)
    count = len(samples)
    return {"sample_count": count,
            "median_seconds": round(statistics.median(samples), 6) if count else None,
            "p95_seconds": round(samples[math.ceil(.95 * count) - 1], 6)
            if count >= 20 else None,
            "evidence": "sufficient_for_descriptive_p95" if count >= 20
            else "insufficient_samples_for_p95",
            "method": "nearest_rank"}


def cohort(skill):
    skill = str(skill or "")
    if "qualification" in skill or skill.startswith(("qualify_", "model_trial_")):
        return "qualification"
    if skill == "healthcheck":
        return "healthcheck"
    if skill in {"test", "fixture", "probe"} or skill.startswith(("test_", "fixture_", "probe_")):
        return "test"
    return "ordinary" if skill in ORDINARY_SKILLS else "unclassified"


def skill_label(skill):
    return skill if skill in ORDINARY_SKILLS or skill == "healthcheck" else cohort(skill)


def number(value, integer=False):
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    if not math.isfinite(result) or result < 0:
        return None
    if integer:
        return int(result) if result.is_integer() else None
    return result


def readonly_connection(path):
    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise ObserverError("database_not_regular_file")
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA trusted_schema=OFF")
    conn.execute("PRAGMA busy_timeout=1000")
    started = time.monotonic()
    conn.set_progress_handler(lambda: int(time.monotonic() - started > 15), 10_000)

    def authorize(action, first, second, database, source):
        if action == sqlite3.SQLITE_READ:
            return (sqlite3.SQLITE_OK if database == "main" and second in READ_COLUMNS.get(first, ())
                    else sqlite3.SQLITE_DENY)
        if action == sqlite3.SQLITE_FUNCTION:
            return sqlite3.SQLITE_OK if (second or first) == "julianday" else sqlite3.SQLITE_DENY
        if action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_TRANSACTION,
                      sqlite3.SQLITE_RECURSIVE}:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_PRAGMA and first == "table_info" and second in TABLES:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    try:
        conn.set_authorizer(authorize)
        conn.execute("BEGIN")
    except BaseException:
        conn.close()
        raise
    return conn


def inspect_schema(conn):
    result = {}
    for name in sorted(TABLES):
        present = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
        result[name] = {row["name"] for row in conn.execute("PRAGMA table_info(" + name + ")")} if present else set()
    return result


def available(schema, table, required, gaps):
    if not set(required).issubset(schema[table]):
        gaps.append(table + "_missing_or_incompatible")
        return False
    return True


def window_sql(column):
    # SQLite understands timezone offsets and fractional seconds. Comparing raw
    # ISO strings would mishandle differently formatted but equal UTC instants.
    return ("julianday(" + column + ")>=julianday(?) AND julianday(" + column
            + ")<julianday(?)")


def bounded_rows(conn, sql, args=()):
    rows = conn.execute(sql + " LIMIT ?", tuple(args) + (MAX_ROWS + 1,)).fetchall()
    if len(rows) > MAX_ROWS:
        raise ObserverError("observation_row_limit_exceeded")
    return rows


def usage_report(conn, schema, since, until, gaps):
    columns = {"ts", "skill", "kind", "model", "tokens_in", "tokens_out", "cost_usd"}
    if not available(schema, "llm_usage", columns, gaps):
        return None
    seconds = "seconds" if "seconds" in schema["llm_usage"] else "NULL AS seconds"
    if "seconds" not in schema["llm_usage"]:
        gaps.append("llm_usage_latency_unavailable")
    rows = bounded_rows(conn, "SELECT ts,skill,kind,model,tokens_in,tokens_out,cost_usd," + seconds
                        + " FROM llm_usage WHERE " + window_sql("ts"), (since, until))
    groups = {}
    totals = defaultdict(lambda: {"calls": 0, "tokens_in": 0, "tokens_out": 0,
                                 "cost_usd": 0.0, "invalid_numeric_rows": 0})
    ordinary_chat_samples = []
    for row in rows:
        category = cohort(row["skill"])
        kind = row["kind"] if row["kind"] in USAGE_KINDS else "unrecognized"
        accounting = ("conservative_unknown" if kind.startswith("unknown_") else
                      "rejected" if kind.startswith("rejected_") else
                      "settled_known" if kind in {"chat", "embed", "stt"} else "unrecognized")
        model = row["model"] if row["model"] in MODELS else "unrecognized"
        day = utc(row["ts"]).date().isoformat()
        key = (day, category, skill_label(row["skill"]), model, kind, accounting)
        if key not in groups:
            groups[key] = {"day_utc": day, "cohort": category, "skill": key[2],
                           "model": model, "kind": kind, "accounting": accounting,
                           "calls": 0, "tokens_in": 0, "tokens_out": 0,
                           "cost_usd": 0.0, "invalid_numeric_rows": 0, "_samples": []}
        group = groups[key]
        values = [number(row["tokens_in"], True), number(row["tokens_out"], True), number(row["cost_usd"])]
        for target in (group, totals[category], totals["all"]):
            target["calls"] += 1
            if any(value is None for value in values):
                target["invalid_numeric_rows"] += 1
            for name, value in zip(("tokens_in", "tokens_out", "cost_usd"), values):
                if value is not None:
                    target[name] += value
        # STT seconds are audio duration; unknown reservations are not latency.
        sample = number(row["seconds"])
        if kind == "chat" and sample is not None:
            group["_samples"].append(sample)
            if category == "ordinary":
                ordinary_chat_samples.append(sample)
    result = []
    for key in sorted(groups):
        group = groups[key]
        group["cost_usd"] = round(group["cost_usd"], 12)
        group["call_latency"] = latency(group.pop("_samples"))
        result.append(group)
    for value in totals.values():
        value["cost_usd"] = round(value["cost_usd"], 12)
    return {"groups": result, "totals_by_cohort": dict(sorted(totals.items())),
            "ordinary_chat_call_latency": latency(ordinary_chat_samples),
            "cost_semantics": "application_ledger_conservative_estimate_including_unknown_reservations_not_provider_bill",
            "accounting_semantics": "settled_known_means_metered_outcome_not_verified_provider_charge",
            "classification": "explicit_skill_markers_only_unmarked_work_cannot_be_proven_ordinary"}


def other_reports(conn, schema, since, until, now, gaps):
    result = {}
    if available(schema, "trace_events", {"ts", "stage"}, gaps):
        counts = Counter()
        cohorts = defaultdict(Counter)
        skill = "skill" if "skill" in schema["trace_events"] else "NULL AS skill"
        if "skill" not in schema["trace_events"]:
            gaps.append("trace_events_cohort_unavailable")
        for row in bounded_rows(conn, "SELECT stage," + skill + " FROM trace_events WHERE " + window_sql("ts")
                                + " AND stage IN (?,?,?,?,?)", (since, until) + STAGES):
            counts[row["stage"]] += 1
            cohorts[cohort(row["skill"])][row["stage"]] += 1
        result["fallback_events"] = {stage: counts[stage] for stage in STAGES}
        result["fallback_events_by_cohort"] = {category: {stage: values[stage] for stage in STAGES}
                                                for category, values in sorted(cohorts.items())}
        result["fallback_semantics"] = "fallback_is_attempt_event_terminal_served_and_failed_are_separate"
    if available(schema, "traces", {"kind", "status", "started_at", "finished_at"}, gaps):
        statuses = Counter()
        samples = []
        for row in bounded_rows(conn, "SELECT status,started_at,finished_at FROM traces WHERE kind='inbound' AND "
                                + window_sql("started_at"), (since, until)):
            status = row["status"] if row["status"] in TRACE_STATUSES else "other"
            statuses[status] += 1
            if status == "ok" and row["finished_at"]:
                try:
                    elapsed = (utc(row["finished_at"]) - utc(row["started_at"])).total_seconds()
                except ObserverError:
                    continue
                if elapsed >= 0:
                    samples.append(elapsed)
        result["inbound"] = {"counts_by_status": dict(sorted(statuses.items())),
                             "processing_latency": latency(samples),
                             "latency_scope": "processing_only_not_background_completion_or_delivery"}
    if available(schema, "reply_outbox", {"state", "created_at"}, gaps):
        counts = Counter()
        pending_ages = []
        for row in bounded_rows(conn, "SELECT state,created_at FROM reply_outbox"):
            state = row["state"] if row["state"] in OUTBOX_STATES else "other"
            counts[state] += 1
            if state == "pending":
                try:
                    age = (now - utc(row["created_at"])).total_seconds()
                except ObserverError:
                    continue
                if age >= 0:
                    pending_ages.append(age)
        result["outbox_current"] = {"counts_by_state": {state: counts[state] for state in OUTBOX_STATES + ("other",)},
                                    "oldest_pending_age_seconds": max(pending_ages) if pending_ages else None,
                                    "scope": "current_global_snapshot_not_historical_window"}
    if available(schema, "issues", {"ts", "kind", "status"}, gaps):
        counts = Counter()
        for row in bounded_rows(conn, "SELECT kind,status FROM issues WHERE " + window_sql("ts"), (since, until)):
            kind = row["kind"] if row["kind"] in ISSUE_KINDS else "other"
            status = row["status"] if row["status"] in ISSUE_STATUSES else "other"
            counts[(kind, status)] += 1
        result["issues"] = [{"kind": kind, "status": status, "count": count}
                            for (kind, status), count in sorted(counts.items())]
    return result


def service_snapshot(runner=subprocess.run):
    results = []
    for service in SERVICES:
        record = {"service": service, "state": "unavailable", "pid": None, "restarts": None}
        try:
            done = runner(["/usr/bin/systemctl", "show", service, "--no-pager",
                           "--property=ActiveState,MainPID,NRestarts"],
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          text=True, timeout=5, check=False, env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
            if done.returncode == 0 and len(done.stdout) <= 4096:
                props = dict(line.split("=", 1) for line in done.stdout.splitlines() if "=" in line)
                record["state"] = props.get("ActiveState") if props.get("ActiveState") in ACTIVE_STATES else "other"
                for prop, name in (("MainPID", "pid"), ("NRestarts", "restarts")):
                    value = props.get(prop, "")
                    record[name] = int(value) if re.fullmatch(r"[0-9]{1,18}", value) else None
        except (OSError, subprocess.SubprocessError, ValueError, TypeError):
            pass
        results.append(record)
    return results


def snapshot(db, since, until, *, now=None, services=True, runner=subprocess.run, binding_sha256=None):
    now = utc(now or datetime.now(timezone.utc))
    since, until = utc(since), utc(until)
    if since > until or until > now:
        raise ObserverError("invalid_observation_window")
    if binding_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", binding_sha256):
        raise ObserverError("invalid_binding_sha256")
    gaps = []
    try:
        conn = readonly_connection(db)
        try:
            schema = inspect_schema(conn)
            result = {"schema": SCHEMA, "captured_at": iso(now),
                      "since_inclusive": iso(since), "until_exclusive": iso(until),
                      "application_mutations": 0, "model_calls": 0, "messages_sent": 0,
                      "binding_sha256": binding_sha256,
                      "usage": usage_report(conn, schema, iso(since), iso(until), gaps)}
            result.update(other_reports(conn, schema, iso(since), iso(until), now, gaps))
        finally:
            conn.close()
    except (sqlite3.Error, OSError, ValueError):
        raise ObserverError("database_observation_failed") from None
    result["services"] = service_snapshot(runner) if services else []
    result["gaps"] = sorted(set(gaps))
    result["quality_evidence"] = "metadata_cannot_establish_answer_correctness_or_owner_preference"
    result["automatic_reversion"] = False
    return result


def write_unique(path, value):
    """Publish complete metadata atomically; refuse to replace any prior output."""
    path = Path(path)
    raw = (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("ascii")
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".observation-", dir=path.parent)
        with os.fdopen(fd, "wb") as handle:
            os.chmod(temporary, 0o600)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)  # Atomic exclusive publication on the same FS.
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except FileExistsError:
        raise ObserverError("output_already_exists") from None
    except OSError:
        raise ObserverError("output_publication_failed") from None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def scheduled_snapshot(db, trial_start, output_dir, *, now=None, services=True,
                       runner=subprocess.run, binding_sha256=None):
    now, start = utc(now or datetime.now(timezone.utc)), utc(trial_start)
    end = start + timedelta(hours=TRIAL_HOURS)
    if now < start:
        raise ObserverError("trial_not_started")
    final = now >= end
    slot = min(TRIAL_HOURS // SLOT_HOURS, int((now - start).total_seconds() // (SLOT_HOURS * 3600)))
    output = Path(output_dir) / ("final-072h.json" if final else f"observation-{slot * SLOT_HOURS:03d}h.json")
    # Repeated scheduler invocations make no DB/service reads after a slot exists.
    if output.exists():
        return {"status": "already_exists", "final": final, "timer_stop_required": final}
    report = snapshot(db, start, min(now, end), now=now, services=services,
                      runner=runner, binding_sha256=binding_sha256)
    report["schedule"] = {"trial_start": iso(start), "absolute_end": iso(end),
                          "slot_hours": slot * SLOT_HOURS, "final": final,
                          "timer_stop_required": final, "automatic_reversion": False}
    report["review_status"] = ("insufficient_ordinary_traffic" if
        report["usage"] is None or report["usage"]["ordinary_chat_call_latency"]["sample_count"] < 20
        else "descriptive_metadata_ready_manual_quality_review_required")
    write_unique(output, report)
    return {"status": "written", "final": final, "timer_stop_required": final}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("snapshot", "scheduled"):
        command = commands.add_parser(name)
        command.add_argument("--db", required=True)
        command.add_argument("--binding-sha256")
        command.add_argument("--skip-services", action="store_true")
        if name == "snapshot":
            command.add_argument("--since", required=True)
            command.add_argument("--until", required=True)
            command.add_argument("--output", required=True)
        else:
            command.add_argument("--trial-start", required=True)
            command.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "snapshot":
            report = snapshot(args.db, args.since, args.until, services=not args.skip_services,
                              binding_sha256=args.binding_sha256)
            write_unique(args.output, report)
            status = {"status": "written"}
        else:
            status = scheduled_snapshot(args.db, args.trial_start, args.output_dir,
                                        services=not args.skip_services, binding_sha256=args.binding_sha256)
        print(json.dumps(status, sort_keys=True))
        return 0
    except ObserverError as exc:
        # Exception codes above are static: never echo paths, SQL, metadata or bodies.
        print(json.dumps({"status": "failed", "reason": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
