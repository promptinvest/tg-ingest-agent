#!/usr/bin/env python3
"""Strict durable-plan contracts for Cara's chief-of-staff task engine.

Planner output is untrusted. This module validates a bounded plan, verifies
input provenance against the canonical boss message or predecessor contracts,
and returns a persistence-safe representation with bound values removed.
"""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

import common
import tool_broker


PLAN_VERSION = 1
MAX_STEPS = 8
MAX_OBJECTIVE = 300
MAX_PURPOSE = 240
DELIVERABLES = frozenset({"answer", "brief", "comparison", "checklist", "draft"})
STEP_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")
ALLOWED_PLAN_KEYS = frozenset({
    "objective", "deliverable", "steps", "capability_gaps",
})
ALLOWED_STEP_KEYS = frozenset({
    "key", "tool", "input", "bindings", "depends_on", "purpose",
})
ALLOWED_BINDING_KEYS = frozenset({
    "source", "start", "end", "source_hash", "transform",
    "step", "path", "schema", "trust",
})
TRANSFORMS = frozenset({
    "literal", "url", "positive_int", "reminder_title", "reminder_due",
    "reminder_recurrence",
})
FIELD_TRANSFORMS = {
    ("knowledge.read", "note_no"): frozenset({"positive_int"}),
    ("source.fetch", "url"): frozenset({"url"}),
    ("reminder.propose", "title"): frozenset({"reminder_title", "literal"}),
    ("reminder.propose", "due_utc"): frozenset({"reminder_due"}),
    ("reminder.propose", "recurrence"): frozenset({
        "literal", "reminder_recurrence"}),
}

# These patterns deliberately target credential shapes, not generic long text.
# The canonical Telegram message remains primary user state; derived task rows
# must not duplicate recognizable secrets.
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?"
               r"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.I),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|dop_v1_[A-Za-z0-9]{20,})\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
               r"(?:\.[A-Za-z0-9_-]{8,})?\b"),
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)\b(?:api[_ -]?key|access[_ -]?token|password|пароль)\s*[:=]\s*"
               r"[^\s,;]{8,}"),
)


class PlanError(ValueError):
    """Planner output violates the closed task-plan contract."""


def source_hash(text):
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def redact_derived_text(value):
    text = common.scrub_secrets(value)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def contains_recognizable_secret(value):
    text = str(value or "")
    return redact_derived_text(text) != text


def validate_plan(value, canonical_source, *, source_time=None, timezone_offset=0):
    """Validate planner JSON and return a redacted, persistence-safe plan.

    Security-sensitive input values are checked against their binding and then
    replaced by ``{"$bound": field}``; the raw value remains only in the
    canonical Telegram source row. Predecessor bindings are contract-checked
    now and receipt-checked later when the producing step has actually run.
    """
    if not isinstance(value, dict):
        raise PlanError("plan must be an object")
    unknown_plan = set(value) - ALLOWED_PLAN_KEYS
    if unknown_plan:
        raise PlanError(f"unknown plan field(s): {', '.join(sorted(unknown_plan))}")

    objective = _text(value.get("objective"), MAX_OBJECTIVE, "objective")
    deliverable = str(value.get("deliverable") or "").strip()
    if deliverable not in DELIVERABLES:
        raise PlanError("invalid deliverable")
    gaps = value.get("capability_gaps", [])
    if not isinstance(gaps, list) or len(gaps) > MAX_STEPS:
        raise PlanError(f"capability_gaps must contain 0..{MAX_STEPS} entries")
    safe_gaps = []
    for index, gap in enumerate(gaps, start=1):
        safe_gaps.append(_text(gap, MAX_PURPOSE, f"capability_gaps[{index}]"))
    steps = value.get("steps")
    if (not isinstance(steps, list) or len(steps) > MAX_STEPS
            or (not steps and not safe_gaps)):
        raise PlanError(
            f"steps must contain 1..{MAX_STEPS} entries unless capability gaps are explicit")

    canonical_source = str(canonical_source or "")
    canonical_hash = source_hash(canonical_source)
    normalized = {
        "version": PLAN_VERSION,
        "objective": redact_derived_text(objective),
        "deliverable": deliverable,
        "source_hash": canonical_hash,
        "time_context": {
            "source_time": _aware_utc(source_time).isoformat(),
            "timezone_offset": int(timezone_offset),
        },
        "due_at": None,
        "capability_gaps": [
            redact_derived_text(gap) for gap in safe_gaps
        ],
        "steps": [],
    }
    prior = {}
    for ordinal, raw_step in enumerate(steps, start=1):
        step = _validate_step(
            raw_step, ordinal, prior, canonical_source, canonical_hash,
            normalized["time_context"])
        prior[step["key"]] = step
        normalized["steps"].append(step)
        if step.get("task_due_at") and (
                normalized["due_at"] is None
                or step["task_due_at"] < normalized["due_at"]):
            normalized["due_at"] = step["task_due_at"]
    return normalized


def _validate_step(raw, ordinal, prior, canonical_source, canonical_hash, time_context):
    if not isinstance(raw, dict):
        raise PlanError(f"step {ordinal} must be an object")
    unknown = set(raw) - ALLOWED_STEP_KEYS
    if unknown:
        raise PlanError(f"step {ordinal} unknown field(s): {', '.join(sorted(unknown))}")

    key = str(raw.get("key") or "").strip()
    if not STEP_KEY_RE.fullmatch(key):
        raise PlanError(f"step {ordinal} has invalid key")
    if key in prior:
        raise PlanError(f"duplicate step key: {key}")
    tool_id = str(raw.get("tool") or "").strip()
    spec = tool_broker.get_spec(tool_id)
    if spec is None:
        raise PlanError(f"unknown tool: {tool_id or '(blank)'}")
    purpose = _text(raw.get("purpose"), MAX_PURPOSE, f"{key}.purpose")
    if contains_recognizable_secret(purpose):
        purpose = redact_derived_text(purpose)

    deps = raw.get("depends_on")
    if not isinstance(deps, list) or len(deps) > MAX_STEPS:
        raise PlanError(f"{key}.depends_on must be an array")
    normalized_deps = []
    for dep in deps:
        dep = str(dep or "").strip()
        if dep not in prior:
            raise PlanError(f"{key} depends on missing or future step: {dep}")
        if dep in normalized_deps:
            raise PlanError(f"{key} repeats dependency: {dep}")
        normalized_deps.append(dep)

    try:
        inputs = tool_broker.validate_input(spec, raw.get("input"))
    except tool_broker.ToolInputError as exc:
        raise PlanError(str(exc)) from exc
    bindings = raw.get("bindings", {})
    if not isinstance(bindings, dict):
        raise PlanError(f"{key}.bindings must be an object")
    unknown_bindings = set(bindings) - set(inputs)
    if unknown_bindings:
        raise PlanError(f"{key} binds unknown input(s): {', '.join(sorted(unknown_bindings))}")
    required_bindings = set(spec.bound_inputs)
    if spec.id == "reminder.propose" and inputs.get("recurrence") != "none":
        required_bindings.add("recurrence")
    unexpected_bindings = set(bindings) - required_bindings
    if unexpected_bindings:
        raise PlanError(
            f"{key} provenance is not allowed for: "
            f"{', '.join(sorted(unexpected_bindings))}")
    missing_bindings = required_bindings - set(bindings)
    if missing_bindings:
        raise PlanError(f"{key} missing provenance for: {', '.join(sorted(missing_bindings))}")

    safe_inputs = {}
    safe_bindings = {}
    for field, input_value in inputs.items():
        binding = bindings.get(field)
        if binding is None:
            if contains_recognizable_secret(json.dumps(input_value, ensure_ascii=False)):
                raise PlanError(f"{key}.{field} contains recognizable secret material")
            safe_inputs[field] = input_value
            continue
        safe_bindings[field] = _validate_binding(
            binding, field, input_value, key, spec, prior, normalized_deps,
            canonical_source, canonical_hash, time_context,
        )
        safe_inputs[field] = {"$bound": field}

    _validate_step_references(key, spec, safe_inputs, prior, normalized_deps)
    normalized = {
        "key": key,
        "ordinal": ordinal,
        "tool": spec.id,
        "risk": spec.risk,
        "policy_version": tool_broker.POLICY_VERSION,
        "implementation_version": tool_broker.IMPLEMENTATION_VERSION,
        "input": safe_inputs,
        "bindings": safe_bindings,
        "depends_on": normalized_deps,
        "purpose": purpose,
        "status": "pending",
    }
    if spec.id == "reminder.propose":
        normalized["task_due_at"] = inputs["due_utc"]
    return normalized


def _validate_binding(binding, field, input_value, step_key, spec, prior, deps,
                      canonical_source, canonical_hash, time_context):
    if not isinstance(binding, dict):
        raise PlanError(f"{step_key}.{field} binding must be an object")
    unknown = set(binding) - ALLOWED_BINDING_KEYS
    if unknown:
        raise PlanError(
            f"{step_key}.{field} binding has unknown field(s): {', '.join(sorted(unknown))}")
    source = str(binding.get("source") or "").strip()
    if source == "boss_span":
        return _validate_boss_binding(
            binding, field, input_value, step_key, spec,
            canonical_source, canonical_hash, time_context)
    if source == "step_output":
        return _validate_step_binding(binding, field, step_key, spec, prior, deps)
    raise PlanError(f"{step_key}.{field} binding source is invalid")


def _validate_boss_binding(binding, field, input_value, step_key, spec,
                           canonical_source, canonical_hash, time_context):
    if binding.get("source_hash") != canonical_hash:
        raise PlanError(f"{step_key}.{field} source hash mismatch")
    start = binding.get("start")
    end = binding.get("end")
    if isinstance(start, bool) or isinstance(end, bool):
        raise PlanError(f"{step_key}.{field} span must use integer offsets")
    if not isinstance(start, int) or not isinstance(end, int):
        raise PlanError(f"{step_key}.{field} span must use integer offsets")
    if start < 0 or end <= start or end > len(canonical_source):
        raise PlanError(f"{step_key}.{field} span is out of range")
    transform = str(binding.get("transform") or "literal").strip()
    if transform not in TRANSFORMS:
        raise PlanError(f"{step_key}.{field} transform is invalid")
    allowed = FIELD_TRANSFORMS.get((spec.id, field), frozenset({"literal"}))
    if transform not in allowed:
        raise PlanError(
            f"{step_key}.{field} transform {transform!r} is not allowed")
    selected = canonical_source[start:end]
    if spec.writes_state and contains_recognizable_secret(selected):
        raise PlanError(
            f"{step_key}.{field} contains secret material that cannot be "
            "safely duplicated into an approval preview")
    _verify_bound_literal(
        step_key, field, input_value, selected, transform, time_context)
    return {
        "source": "boss_span",
        "start": start,
        "end": end,
        "source_hash": canonical_hash,
        "transform": transform,
    }


def _verify_bound_literal(step_key, field, value, selected, transform, time_context):
    selected = selected.strip()
    if not selected:
        raise PlanError(f"{step_key}.{field} span is blank")
    if transform in {"literal", "url"}:
        if str(value).strip() != selected:
            raise PlanError(f"{step_key}.{field} does not match its boss span")
    elif transform == "positive_int":
        match = re.fullmatch(r"\s*#?\s*(\d+)\s*", selected)
        try:
            expected = int(value)
        except (TypeError, ValueError) as exc:
            raise PlanError(f"{step_key}.{field} must be a positive integer") from exc
        if match is None or int(match.group(1)) != expected:
            raise PlanError(f"{step_key}.{field} does not match its numbered boss span")
    elif transform == "reminder_title":
        if str(value).strip().casefold() != selected.casefold():
            raise PlanError(f"{step_key}.{field} title does not exactly match its boss span")
    elif transform == "reminder_due":
        try:
            selected_due = parse_bound_due(
                selected, time_context["source_time"],
                time_context["timezone_offset"])
        except (TypeError, ValueError) as exc:
            raise PlanError(f"{step_key}.{field} boss time is not deterministic") from exc
        if selected_due != str(value).strip():
            raise PlanError(f"{step_key}.{field} does not match its boss span")
    elif transform == "reminder_recurrence":
        selected_recurrence = parse_bound_recurrence(selected)
        if selected_recurrence != str(value).strip().lower():
            raise PlanError(f"{step_key}.{field} recurrence does not match its boss span")


def _aware_utc(value):
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(
                str(value or "").strip().replace("Z", "+00:00"))
        except ValueError:
            parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_bound_due(value, source_time, timezone_offset):
    """Resolve a deliberately narrow, replayable boss-authored time phrase."""
    text = " ".join(str(value or "").casefold().replace(",", " ").split())
    text = text.strip(" .!?")
    try:
        parsed = datetime.fromisoformat(text.replace("z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is not None:
        if parsed.tzinfo is None:
            raise ValueError("absolute task time needs a timezone")
        parsed = parsed.astimezone(timezone.utc)
        if source_time is None or parsed <= _aware_utc(source_time):
            raise ValueError("absolute task time must be after the source message")
        return parsed.isoformat()

    offset = timedelta(hours=int(timezone_offset))
    local_base = _aware_utc(source_time) + offset
    clock = r"([01]?\d|2[0-3])(?:[:.]([0-5]\d))?\s*(am|pm)?"
    day_patterns = (
        (r"(?:day after tomorrow)(?:\s+at)?\s+" + clock, 2, False),
        (r"послезавтра(?:\s+в)?\s+" + clock, 2, False),
        (r"tomorrow(?:\s+at)?\s+" + clock, 1, False),
        (r"завтра(?:\s+в)?\s+" + clock, 1, False),
        (r"today(?:\s+at)?\s+" + clock, 0, True),
        (r"сегодня(?:\s+в)?\s+" + clock, 0, True),
    )
    match = None
    day_delta = None
    explicit_today = False
    for pattern, delta, is_today in day_patterns:
        match = re.fullmatch(pattern, text)
        if match:
            day_delta, explicit_today = delta, is_today
            break

    weekdays = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
        "понедельник": 0, "вторник": 1, "среду": 2, "четверг": 3,
        "пятницу": 4, "субботу": 5, "воскресенье": 6,
    }
    weekday = None
    explicit_next = bool(re.match(
        r"^(?:next\s+|(?:в\s+)?следующ(?:ий|ую)\s+)", text))
    if match is None:
        weekday_names = "|".join(map(re.escape, weekdays))
        match = re.fullmatch(
            rf"(?:(?:every|next)\s+)?({weekday_names})(?:\s+at)?\s+{clock}",
            text)
        if match is None:
            match = re.fullmatch(
                rf"(?:кажд(?:ый|ую)\s+|(?:в\s+)?следующ(?:ий|ую)\s+|в\s+)?"
                rf"({weekday_names})(?:\s+в)?\s+{clock}",
                text)
        if match is not None:
            weekday = weekdays[match.group(1)]
            # The weekday name is capture 1, clock begins at capture 2.
            hour_group = 2
        else:
            hour_group = 1
    else:
        hour_group = 1

    if match is None:
        match = re.fullmatch(r"(?:at|в)?\s*" + clock, text)
        hour_group = 1
    if match is None:
        raise ValueError("unsupported or ambiguous time phrase")
    hour = int(match.group(hour_group))
    minute = int(match.group(hour_group + 1) or 0)
    suffix = match.group(hour_group + 2)
    if suffix and not 1 <= hour <= 12:
        raise ValueError("12-hour clock must use 1..12")
    if suffix == "pm" and hour < 12:
        hour += 12
    elif suffix == "am" and hour == 12:
        hour = 0
    if weekday is not None:
        day_delta = (weekday - local_base.weekday()) % 7
        if explicit_next and day_delta == 0:
            day_delta = 7
    elif day_delta is None:
        day_delta = 0
    local_due = (local_base + timedelta(days=day_delta)).replace(
        hour=hour, minute=minute, second=0, microsecond=0)
    if explicit_today and local_due <= local_base:
        raise ValueError("explicit today time is already past")
    if weekday is not None and local_due <= local_base:
        local_due += timedelta(days=7)
    elif day_delta == 0 and local_due <= local_base:
        local_due += timedelta(days=1)
    return (local_due - offset).astimezone(timezone.utc).isoformat()


def parse_bound_recurrence(value):
    text = " ".join(str(value or "").casefold().split())
    if re.search(r"\b(?:every day|daily|каждый день|ежедневно)\b", text):
        return "daily"
    if re.search(
            r"\b(?:every week|weekly|каждую неделю|еженедельно|"
            r"every (?:mon|tues|wednes|thurs|fri|satur|sun)day|"
            r"кажд(?:ый|ую) (?:понедельник|вторник|среду|четверг|пятницу|"
            r"субботу|воскресенье))\b", text):
        return "weekly"
    if text in {"none", "once", "one time", "один раз", "разово"}:
        return "none"
    raise ValueError("unsupported recurrence")


def _validate_step_binding(binding, field, step_key, spec, prior, deps):
    predecessor = str(binding.get("step") or "").strip()
    if predecessor not in prior or predecessor not in deps:
        raise PlanError(f"{step_key}.{field} must bind a direct predecessor")
    path = str(binding.get("path") or "").strip()
    schema = str(binding.get("schema") or "").strip()
    trust = str(binding.get("trust") or "").strip()
    producer = tool_broker.get_spec(prior[predecessor]["tool"])
    if schema != producer.output_schema:
        raise PlanError(f"{step_key}.{field} predecessor schema mismatch")
    contract = {p: t for p, t in producer.output_paths}
    if path not in contract or trust != contract[path]:
        raise PlanError(f"{step_key}.{field} predecessor output contract mismatch")
    if trust not in {"boss", "confirmed_local"} and (
            spec.writes_state or field in {"url", "note_no"}):
        raise PlanError(f"{step_key}.{field} cannot trust untrusted output for this field")
    return {
        "source": "step_output",
        "step": predecessor,
        "path": path,
        "schema": schema,
        "trust": trust,
    }


def _validate_step_references(key, spec, inputs, prior, deps):
    if spec.id == "research.synthesize":
        for predecessor in inputs["receipt_steps"]:
            if predecessor not in prior or predecessor not in deps:
                raise PlanError(
                    f"{key}.receipt_steps must name direct predecessor steps")
    elif spec.id == "artifact.markdown":
        predecessor = inputs["content_step"]
        if predecessor not in prior or predecessor not in deps:
            raise PlanError(f"{key}.content_step must name a direct predecessor step")


def _text(value, maximum, name):
    if not isinstance(value, str):
        raise PlanError(f"{name} must be text")
    value = " ".join(value.split()).strip()
    if not value or len(value) > maximum:
        raise PlanError(f"{name} must contain 1..{maximum} characters")
    return value
