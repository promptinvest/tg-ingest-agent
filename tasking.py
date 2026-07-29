#!/usr/bin/env python3
"""Strict durable-plan contracts for Cara's chief-of-staff task engine.

Planner output is untrusted. This module validates a bounded plan, verifies
input provenance against the canonical boss message or predecessor contracts,
and returns a persistence-safe representation with bound values removed.
"""
import hashlib
import json
import re

import common
import tool_broker


PLAN_VERSION = 1
MAX_STEPS = 8
MAX_OBJECTIVE = 300
MAX_PURPOSE = 240
DELIVERABLES = frozenset({"answer", "brief", "comparison", "checklist", "draft"})
STEP_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")
ALLOWED_PLAN_KEYS = frozenset({"objective", "deliverable", "steps"})
ALLOWED_STEP_KEYS = frozenset({
    "key", "tool", "input", "bindings", "depends_on", "purpose",
})
ALLOWED_BINDING_KEYS = frozenset({
    "source", "start", "end", "source_hash", "transform",
    "step", "path", "schema", "trust",
})
TRANSFORMS = frozenset({
    "literal", "url", "positive_int", "reminder_title", "reminder_due",
})
FIELD_TRANSFORMS = {
    ("knowledge.read", "note_no"): frozenset({"positive_int"}),
    ("source.fetch", "url"): frozenset({"url"}),
    ("reminder.propose", "title"): frozenset({"reminder_title", "literal"}),
    ("reminder.propose", "due_utc"): frozenset({"reminder_due"}),
    ("reminder.propose", "recurrence"): frozenset({"literal"}),
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


def validate_plan(value, canonical_source):
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
    steps = value.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise PlanError(f"steps must contain 1..{MAX_STEPS} entries")

    canonical_source = str(canonical_source or "")
    canonical_hash = source_hash(canonical_source)
    normalized = {
        "version": PLAN_VERSION,
        "objective": redact_derived_text(objective),
        "deliverable": deliverable,
        "source_hash": canonical_hash,
        "steps": [],
    }
    prior = {}
    for ordinal, raw_step in enumerate(steps, start=1):
        step = _validate_step(raw_step, ordinal, prior, canonical_source, canonical_hash)
        prior[step["key"]] = step
        normalized["steps"].append(step)
    return normalized


def _validate_step(raw, ordinal, prior, canonical_source, canonical_hash):
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
            canonical_source, canonical_hash,
        )
        safe_inputs[field] = {"$bound": field}

    _validate_step_references(key, spec, safe_inputs, prior, normalized_deps)
    return {
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


def _validate_binding(binding, field, input_value, step_key, spec, prior, deps,
                      canonical_source, canonical_hash):
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
            canonical_source, canonical_hash)
    if source == "step_output":
        return _validate_step_binding(binding, field, step_key, spec, prior, deps)
    raise PlanError(f"{step_key}.{field} binding source is invalid")


def _validate_boss_binding(binding, field, input_value, step_key, spec,
                           canonical_source, canonical_hash):
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
    _verify_bound_literal(step_key, field, input_value, selected, transform)
    return {
        "source": "boss_span",
        "start": start,
        "end": end,
        "source_hash": canonical_hash,
        "transform": transform,
    }


def _verify_bound_literal(step_key, field, value, selected, transform):
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
        if str(value).strip().casefold() not in selected.casefold():
            raise PlanError(f"{step_key}.{field} title is not present in its boss span")
    elif transform == "reminder_due":
        # The execution resolver re-runs deterministic date parsing and compares
        # the resulting UTC value. At plan time we can still require that the
        # planner bound a real, non-empty boss span instead of invented text.
        if len(selected) > 120:
            raise PlanError(f"{step_key}.{field} due span is too large")


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
