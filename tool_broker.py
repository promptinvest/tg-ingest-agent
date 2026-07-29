#!/usr/bin/env python3
"""Closed tool contracts for Cara's durable assistant tasks.

This module contains the compiled metadata and input-shape boundary used by
the task runner. Execution lives in ``task_runner.py`` (or the isolated
worker), so model output never names a callable or command directly.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

import skill_manifest


RISKS = frozenset({
    "read_only", "network_read", "draft_write", "state_write",
    "external_write", "destructive",
})
POLICY_VERSION = "task-tools/v2"
IMPLEMENTATION_VERSION = "tasking/v2"
TRUST_CLASSES = frozenset({
    "boss", "confirmed_local", "external_untrusted", "model_untrusted",
})


class ToolInputError(ValueError):
    """A planner supplied an input that is outside a tool's closed schema."""


class ToolOutputError(ValueError):
    """A tool returned data outside its compiled receipt schema."""


@dataclass(frozen=True)
class ToolSpec:
    id: str
    title: str
    risk: str
    execution_site: str
    required_inputs: frozenset
    optional_inputs: frozenset
    bound_inputs: frozenset
    output_schema: str
    output_paths: tuple
    output_limit: int
    uses_llm: bool = False
    external_network: bool = False
    writes_state: bool = False
    destructive: bool = False
    requires_confirmation: bool = False
    allowed_proactive: bool = False
    timeout_seconds: int = 15

    @property
    def allowed_inputs(self):
        return self.required_inputs | self.optional_inputs


def _spec(tool_id, title, risk, *, required=(), optional=(), bound=(),
          output_schema, output_paths=(), output_limit=8000, uses_llm=False,
          external_network=False, writes_state=False, destructive=False,
          requires_confirmation=False, execution_site="agent",
          allowed_proactive=False, timeout_seconds=15):
    if risk not in RISKS:
        raise RuntimeError(f"unknown task-tool risk: {risk}")
    if destructive and not writes_state:
        raise RuntimeError(f"destructive task tool must write state: {tool_id}")
    if requires_confirmation and not writes_state:
        raise RuntimeError(f"read-only task tool cannot require confirmation: {tool_id}")
    required = frozenset(required)
    optional = frozenset(optional)
    bound = frozenset(bound)
    if required & optional:
        raise RuntimeError(f"duplicate required/optional inputs: {tool_id}")
    if not bound <= (required | optional):
        raise RuntimeError(f"bound input missing from schema: {tool_id}")
    return ToolSpec(
        id=tool_id,
        title=title,
        risk=risk,
        execution_site=execution_site,
        required_inputs=required,
        optional_inputs=optional,
        bound_inputs=bound,
        output_schema=output_schema,
        output_paths=tuple(output_paths),
        output_limit=int(output_limit),
        uses_llm=bool(uses_llm),
        external_network=bool(external_network),
        writes_state=bool(writes_state),
        destructive=bool(destructive),
        requires_confirmation=requires_confirmation,
        allowed_proactive=bool(allowed_proactive),
        timeout_seconds=int(timeout_seconds),
    )


TOOLS = {
    "knowledge.search": _spec(
        "knowledge.search", "Search saved knowledge", "read_only",
        required=("query",), optional=("limit",),
        output_schema="knowledge.search/v1",
        output_paths=(("results", "confirmed_local"),),
        output_limit=64000,
    ),
    "knowledge.read": _spec(
        "knowledge.read", "Read a saved note", "read_only",
        required=("note_no",), bound=("note_no",),
        output_schema="knowledge.read/v1",
        output_paths=(("note", "confirmed_local"),),
        output_limit=32000,
    ),
    "reminders.read": _spec(
        "reminders.read", "Read active reminders", "read_only",
        output_schema="reminders.read/v1",
        output_paths=(("reminders", "confirmed_local"),),
        output_limit=96000,
    ),
    "source.fetch": _spec(
        "source.fetch", "Read a supplied URL", "network_read",
        required=("url",), bound=("url",),
        output_schema="source.fetch/v1",
        output_paths=(("document", "external_untrusted"),),
        external_network=True, output_limit=64000, timeout_seconds=25,
    ),
    "research.synthesize": _spec(
        "research.synthesize", "Synthesize cited findings", "read_only",
        required=("receipt_steps",), optional=("question",),
        output_schema="research.synthesize/v1",
        output_paths=(("claims", "model_untrusted"),),
        uses_llm=True, output_limit=262144, timeout_seconds=45,
    ),
    "artifact.markdown": _spec(
        "artifact.markdown", "Create a managed Markdown draft", "draft_write",
        required=("content_step",), optional=("title",),
        output_schema="artifact.markdown/v1",
        output_paths=(("artifact_id", "confirmed_local"),),
        writes_state=True,
    ),
    "reminder.propose": _spec(
        "reminder.propose", "Propose a reminder", "state_write",
        required=("title", "due_utc"), optional=("recurrence",),
        bound=("title", "due_utc"),
        output_schema="reminder.propose/v1",
        output_paths=(("reminder_id", "confirmed_local"),),
        writes_state=True, requires_confirmation=True,
    ),
    "worker.echo": _spec(
        "worker.echo", "Sandbox transport check", "read_only",
        required=("text",),
        output_schema="worker.echo/v1",
        output_paths=(("echo", "external_untrusted"),),
        execution_site="worker", output_limit=2000, timeout_seconds=10,
    ),
}


def get_spec(tool_id):
    return TOOLS.get(str(tool_id or "").strip())


def assert_registry():
    """Fail fast on a policy contradiction in the compiled registry."""
    if set(TOOLS) != {spec.id for spec in TOOLS.values()}:
        raise RuntimeError("task tool registry key/id mismatch")
    if set(TOOLS) != set(skill_manifest.TASK_TOOLS):
        missing = sorted(set(TOOLS) - set(skill_manifest.TASK_TOOLS))
        extra = sorted(set(skill_manifest.TASK_TOOLS) - set(TOOLS))
        raise RuntimeError(
            f"task tool permission manifest mismatch: missing={missing}, extra={extra}")
    for spec in TOOLS.values():
        assert_policy(spec)
        if spec.risk not in RISKS:
            raise RuntimeError(f"unknown risk for {spec.id}")
        if spec.execution_site not in {"agent", "worker"}:
            raise RuntimeError(f"unknown execution site for {spec.id}")
        if spec.writes_state and spec.risk in {"read_only", "network_read"}:
            raise RuntimeError(f"write tool declared read-only: {spec.id}")
        if spec.requires_confirmation and not spec.writes_state:
            raise RuntimeError(f"confirmation without write: {spec.id}")
        if spec.risk in {"state_write", "external_write", "destructive"}:
            if not spec.writes_state or not spec.requires_confirmation:
                raise RuntimeError(f"effect tool lacks confirmation: {spec.id}")
        if not spec.output_schema or not spec.output_schema.endswith("/v1"):
            raise RuntimeError(f"invalid output schema for {spec.id}")
        paths = []
        for path, trust in spec.output_paths:
            if not path or trust not in TRUST_CLASSES:
                raise RuntimeError(f"invalid output contract for {spec.id}")
            paths.append(path)
        if len(paths) != len(set(paths)):
            raise RuntimeError(f"duplicate output path for {spec.id}")


def assert_policy(spec):
    if not isinstance(spec, ToolSpec):
        raise RuntimeError("unknown task tool")
    policy = skill_manifest.get_tool_policy(spec.id)
    for field in (
        "risk", "uses_llm", "external_network", "writes_state", "destructive",
        "requires_confirmation", "allowed_proactive",
    ):
        if policy.get(field) != getattr(spec, field):
            raise RuntimeError(
                f"task tool policy drift for {spec.id}: {field}")
    return True


def validate_input(spec, value):
    """Return a normalized input dict or raise ToolInputError."""
    if not isinstance(spec, ToolSpec):
        raise ToolInputError("unknown tool")
    if not isinstance(value, dict):
        raise ToolInputError(f"{spec.id}: input must be an object")
    keys = set(value)
    unknown = keys - spec.allowed_inputs
    missing = spec.required_inputs - keys
    if unknown:
        raise ToolInputError(f"{spec.id}: unknown input(s): {', '.join(sorted(unknown))}")
    if missing:
        raise ToolInputError(f"{spec.id}: missing input(s): {', '.join(sorted(missing))}")

    out = dict(value)
    if spec.id == "knowledge.search":
        query = _bounded_text(out.get("query"), 500, "query")
        limit = _bounded_int(out.get("limit", 5), 1, 8, "limit")
        out = {"query": query, "limit": limit}
    elif spec.id == "knowledge.read":
        out = {"note_no": _bounded_int(out.get("note_no"), 1, 2_000_000_000,
                                        "note_no")}
    elif spec.id == "reminders.read":
        out = {}
    elif spec.id == "source.fetch":
        url = _bounded_text(out.get("url"), 2048, "url")
        try:
            parsed = urlsplit(url)
            hostname = parsed.hostname
        except ValueError as exc:
            raise ToolInputError("source.fetch: malformed URL") from exc
        if parsed.scheme.lower() not in {"http", "https"} or not hostname:
            raise ToolInputError("source.fetch: url must be absolute HTTP(S)")
        if parsed.username or parsed.password:
            raise ToolInputError("source.fetch: credentials in URL are forbidden")
        out = {"url": url}
    elif spec.id == "research.synthesize":
        steps = out.get("receipt_steps")
        if not isinstance(steps, list) or not 1 <= len(steps) <= 8:
            raise ToolInputError("research.synthesize: receipt_steps must contain 1..8 keys")
        normalized = []
        for item in steps:
            key = _step_key(item)
            if key in normalized:
                raise ToolInputError("research.synthesize: duplicate receipt step")
            normalized.append(key)
        out = {"receipt_steps": normalized}
        if "question" in value:
            out["question"] = _bounded_text(value.get("question"), 500, "question")
    elif spec.id == "artifact.markdown":
        out = {"content_step": _step_key(out.get("content_step"))}
        if "title" in value:
            out["title"] = _bounded_text(value.get("title"), 120, "title")
    elif spec.id == "reminder.propose":
        due_raw = _bounded_text(out.get("due_utc"), 80, "due_utc")
        try:
            due = datetime.fromisoformat(due_raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ToolInputError("reminder.propose: due_utc must be RFC3339") from exc
        if due.tzinfo is None:
            raise ToolInputError("reminder.propose: due_utc must include a timezone")
        out = {
            "title": _bounded_text(out.get("title"), 200, "title"),
            "due_utc": due.astimezone(timezone.utc).isoformat(),
            "recurrence": str(out.get("recurrence") or "none").strip().lower(),
        }
        if out["recurrence"] not in {"none", "daily", "weekly"}:
            raise ToolInputError("reminder.propose: invalid recurrence")
    elif spec.id == "worker.echo":
        out = {"text": _bounded_text(out.get("text"), 1000, "text")}
    else:  # Registry additions must add a validator in the same change.
        raise ToolInputError(f"{spec.id}: no input validator")
    return out


def validate_output(spec, data, evidence):
    """Validate post-redaction receipt data and evidence recursively."""
    if not isinstance(spec, ToolSpec):
        raise ToolOutputError("unknown tool")
    _bounded_json(data, spec.output_limit)
    _bounded_json(evidence, spec.output_limit)
    if not isinstance(data, dict) or set(data) != {"schema", "value"}:
        raise ToolOutputError(f"{spec.id}: output envelope mismatch")
    if data["schema"] != spec.output_schema or not isinstance(data["value"], dict):
        raise ToolOutputError(f"{spec.id}: output schema mismatch")
    value = data["value"]
    expected = {path for path, _ in spec.output_paths}
    if set(value) != expected:
        raise ToolOutputError(f"{spec.id}: output fields mismatch")
    if spec.id == "knowledge.search":
        rows = value["results"]
        if not isinstance(rows, list) or len(rows) > 8:
            raise ToolOutputError("knowledge.search: invalid results")
        for row in rows:
            _exact_dict(row, {"note_no", "category", "text"})
            if not isinstance(row["note_no"], int):
                raise ToolOutputError("knowledge.search: invalid note number")
            _text_output(row["category"], 200)
            _text_output(row["text"], 1200)
    elif spec.id == "knowledge.read":
        row = value["note"]
        _exact_dict(row, {"note_no", "category", "text"})
        if not isinstance(row["note_no"], int):
            raise ToolOutputError("knowledge.read: invalid note number")
        _text_output(row["category"], 200)
        _text_output(row["text"], 5000)
    elif spec.id == "reminders.read":
        rows = value["reminders"]
        if not isinstance(rows, list) or len(rows) > 50:
            raise ToolOutputError("reminders.read: invalid reminders")
        for row in rows:
            _exact_dict(row, {"id", "title", "due_utc", "recurrence"})
            if not isinstance(row["id"], int):
                raise ToolOutputError("reminders.read: invalid id")
            _text_output(row["title"], 200)
            _text_output(row["due_utc"], 80)
            if row["recurrence"] not in {"none", "daily", "weekly"}:
                raise ToolOutputError("reminders.read: invalid recurrence")
    elif spec.id == "source.fetch":
        row = value["document"]
        _exact_dict(row, {"url", "title", "text"})
        _text_output(row["url"], 2048)
        _text_output(row["title"], 300, allow_empty=True)
        _text_output(row["text"], 12000)
    elif spec.id == "research.synthesize":
        claims = value["claims"]
        if not isinstance(claims, list) or not 1 <= len(claims) <= 20:
            raise ToolOutputError("research.synthesize: invalid claims")
        evidence_ids = {
            item["id"] for item in evidence if isinstance(item, dict) and item.get("id")
        }
        for claim in claims:
            _exact_dict(claim, {"claim", "citation_ids", "confidence", "limitation"})
            _text_output(claim["claim"], 1000)
            _text_output(claim["limitation"], 500, allow_empty=True)
            if not isinstance(claim["confidence"], (int, float)):
                raise ToolOutputError("research.synthesize: invalid confidence")
            citations = claim["citation_ids"]
            if (not isinstance(citations, list) or not citations
                    or len(citations) > 20
                    or any(not isinstance(cid, str) or cid not in evidence_ids
                           for cid in citations)):
                raise ToolOutputError("research.synthesize: invalid citation lineage")
    elif spec.id in {"artifact.markdown", "reminder.propose"}:
        key = "artifact_id" if spec.id == "artifact.markdown" else "reminder_id"
        if not isinstance(value[key], int) or value[key] <= 0:
            raise ToolOutputError(f"{spec.id}: invalid effect id")
    elif spec.id == "worker.echo":
        _text_output(value["echo"], 1000)
    else:
        raise ToolOutputError(f"{spec.id}: output validator missing")
    if not isinstance(evidence, list) or len(evidence) > 100:
        raise ToolOutputError(f"{spec.id}: invalid evidence list")
    for item in evidence:
        _exact_dict(item, {"id", "source", "label", "trust"})
        _text_output(item["id"], 240)
        _text_output(item["source"], 500)
        _text_output(item["label"], 240, allow_empty=True)
        if item["trust"] not in TRUST_CLASSES:
            raise ToolOutputError(f"{spec.id}: invalid evidence trust")
    return data, evidence


def _bounded_json(value, maximum):
    nodes = 0
    stack = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > 1000 or depth > 10:
            raise ToolOutputError("output is too deep or complex")
        if isinstance(current, dict):
            if len(current) > 100:
                raise ToolOutputError("output object is too large")
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            if len(current) > 100:
                raise ToolOutputError("output array is too large")
            stack.extend((item, depth + 1) for item in current)
        elif current is not None and not isinstance(current, (str, int, float, bool)):
            raise ToolOutputError("output contains an unsupported type")
    import json
    if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > maximum:
        raise ToolOutputError("output exceeds byte limit")


def _exact_dict(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ToolOutputError("output object fields mismatch")


def _text_output(value, maximum, allow_empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not allow_empty and not value):
        raise ToolOutputError("output text is invalid")


def _bounded_text(value, maximum, name):
    if not isinstance(value, str):
        raise ToolInputError(f"{name} must be text")
    value = " ".join(value.split()).strip()
    if not value or len(value) > maximum:
        raise ToolInputError(f"{name} must contain 1..{maximum} characters")
    return value


def _bounded_int(value, minimum, maximum, name):
    if isinstance(value, bool):
        raise ToolInputError(f"{name} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ToolInputError(f"{name} must be an integer") from exc
    if number < minimum or number > maximum:
        raise ToolInputError(f"{name} is out of range")
    return number


def _step_key(value):
    value = str(value or "").strip()
    if not value or len(value) > 32:
        raise ToolInputError("invalid step key")
    if not value[0].isalpha() or not all(ch.isalnum() or ch == "_" for ch in value):
        raise ToolInputError("invalid step key")
    return value


assert_registry()
