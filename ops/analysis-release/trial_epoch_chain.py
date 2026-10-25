"""Pure source-epoch chronology for a pinned two-source trial continuation.

The host caller authenticates the prior receipt and accepted runtime separately.
This module performs no file, environment, database, network or service access.
It never grants tools, approves a release, extends a trial, or triggers reversion.
"""
from copy import deepcopy
from datetime import datetime, timedelta
import re


def require(value, reason):
    if not value:
        raise ValueError(reason)


def utc(value):
    require(isinstance(value, str) and len(value) <= 35 and value.endswith(("Z", "+00:00")), "UTC timestamp required")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.utcoffset() == timedelta(0), "UTC timestamp required")
    return result


def identity(value):
    require(isinstance(value, dict) and set(value) == {"sha256", "deployment_id", "source_revision", "build_version"}, "closed native identity required")
    for key, width in (("sha256", 64), ("deployment_id", 24), ("source_revision", 40), ("build_version", 12)):
        require(isinstance(value[key], str) and re.fullmatch("[0-9a-f]{" + str(width) + "}", value[key]), "exact native identity required")
    return value


def append_third_epoch(prior, boundary_at, latest_identity, latest_started_at):
    """Append exactly one source; preserve every prior window and known pause."""
    require(isinstance(prior, dict) and prior.get("schema") == "cara.model-trial-continuation/v1", "authenticated two-source continuation required")
    start, end = utc(prior["trial_start"]), utc(prior["trial_end"])
    require(end - start == timedelta(hours=72), "original72h period required")
    old_until, current_from = utc(prior["old_segment_until"]), utc(prior["new_segment_from"])
    boundary, latest_from = utc(boundary_at), utc(latest_started_at)
    require(start <= old_until <= current_from < boundary <= latest_from < end, "ordered non-overlapping source epochs required")
    first = identity(prior["old_runtime"]); second = identity(prior["new_runtime"]["identity"]); third = identity(latest_identity)
    require(len({item["source_revision"] for item in (first, second, third)}) == 3 and len({item["deployment_id"] for item in (first, second, third)}) == 3, "three distinct native sources and deployments required")
    pauses = prior.get("recorded_paused_intervals", [])
    require(isinstance(pauses, list) and len(pauses) <= 4, "bounded prior pause list required")
    previous = current_from
    for pause in pauses:
        require(isinstance(pause, dict) and set(pause) == {"from_inclusive", "until_exclusive", "reason"} and pause["reason"] == "forward_paused_after_runtime_verifier_path_failure", "closed prior pause required")
        left, right = utc(pause["from_inclusive"]), utc(pause["until_exclusive"])
        require(previous <= left < right <= boundary, "ordered prior pause inside closing source epoch required")
        previous = right
    return {
        "epochs": [
            {"ordinal": 1, "identity": deepcopy(first), "from_inclusive": prior["trial_start"], "until_exclusive": prior["old_segment_until"], "recorded_paused_intervals": []},
            {"ordinal": 2, "identity": deepcopy(second), "from_inclusive": prior["new_segment_from"], "until_exclusive": boundary_at, "recorded_paused_intervals": deepcopy(pauses)},
            {"ordinal": 3, "identity": deepcopy(third), "from_inclusive": latest_started_at, "until_exclusive": None, "recorded_paused_intervals": []},
        ],
        "transition_gaps": [
            {"from_inclusive": prior["old_segment_until"], "until_exclusive": prior["new_segment_from"]},
            {"from_inclusive": boundary_at, "until_exclusive": latest_started_at},
        ],
    }


def remaining_original_dates(start_text, end_text, now_text):
    start, end, current = utc(start_text), utc(end_text), utc(now_text)
    require(end == start + timedelta(hours=72) and start <= current < end, "remaining original72h period required")
    dates = []
    for hours in range(6, 73, 6):
        moment = start + timedelta(hours=hours)
        if moment.microsecond:
            moment = moment.replace(microsecond=0) + timedelta(seconds=1)
        if moment > current:
            dates.append(moment.strftime("%Y-%m-%d %H:%M:%S UTC"))
    require(bool(dates), "no remaining absolute calendar date")
    return dates
