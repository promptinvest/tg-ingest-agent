"""Transport-independent reads of existing canonical fleet investigation records.

No investigation creation, pumping, model/network call, effect, approval,
redelivery or additional ledger exists here. Transport policy may further narrow
this owner-bound primitive. The shared task adapter adds its own source span.
"""
import copy
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone

import common
import fleet_lifecycle
import fleet_protocol as protocol
import store
import tasking

VERSION = "cara-fleet-shared-read/v1"
TOOLS = frozenset({"fleet.status", "fleet.result"})
STATES = frozenset({"preparing", "queued", "inspection_started", "ready", "analysis_started", "delivering", "done", "failed", "ambiguous", "cancelled", "mcp_facts_queued", "mcp_facts_started", "admission_queued", "admission_prepared"} |
                   {name + "_" + phase for name in ("planning", "synthesis") for phase in ("queued", "started", "completed", "consumed")})
MAX_RETAINED_BYTES = 524288
REFERENCE_KEYS = {"version", "source_update", "status", "state", "phase", "created_at", "updated_at", "observed_at",
                  "scope", "deadlines", "result_available", "gap", "pages"}
SCOPE_KEYS = {"hosts", "projects", "capabilities", "kind", "read_scope"}
_SHA = re.compile(r"[a-f0-9]{64}\Z")


class ReferenceError(ValueError):
    pass


class ScopeDenied(ReferenceError):
    pass


def timestamp(value):
    if not isinstance(value, str) or len(value) > 35 or not (value.endswith("Z") or value.endswith("+00:00")):
        raise ReferenceError("invalid retained timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ReferenceError("invalid retained timestamp") from None
    if result.utcoffset() != timedelta(0):
        raise ReferenceError("retained timestamp must be UTC")
    return result


def _unique(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ReferenceError("duplicate retained field")
        result[key] = value
    return result


def canonical_reference(connection, owner_chat_id, *, request_id=None, source_update=None):
    """Return (row, payload, gap), using only the caller's existing connection.

    Callers own connection sandbox/transaction policy. This helper performs only
    fixed bounded SELECTs; it creates no connection, cache or persistent copy.
    """
    if type(owner_chat_id) is not int or (request_id is None) == (source_update is None):
        raise ReferenceError("invalid retained identity")
    if request_id is not None and (not isinstance(request_id, str) or not protocol.TOKEN.fullmatch(request_id)):
        raise ReferenceError("invalid retained request reference")
    if source_update is not None and (type(source_update) is not int or source_update < 0):
        raise ReferenceError("invalid retained source reference")
    if fleet_lifecycle.admission_paused(connection):
        return None, None, "purge_in_progress"
    field, identity = ("id", request_id) if request_id is not None else ("source_update", source_update)
    row = connection.execute("SELECT id,chat_id,source_update,state,substr(payload,1,?) AS payload,created_at,updated_at "
        "FROM fleet_requests WHERE " + field + "=? AND chat_id=?", (MAX_RETAINED_BYTES + 1, identity, owner_chat_id)).fetchone()
    if row is None:
        return None, None, "not_found_or_not_authorized"
    row = dict(row)
    raw = row["payload"]
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_RETAINED_BYTES:
        raise ReferenceError("retained request exceeds source bound")
    payload = json.loads(raw, object_pairs_hook=_unique,
                         parse_constant=lambda _value: (_ for _ in ()).throw(ReferenceError("invalid retained number")))
    if not isinstance(payload, dict):
        raise ReferenceError("invalid retained request")
    update = connection.execute("SELECT chat_id FROM telegram_updates WHERE update_id=?", (row["source_update"],)).fetchone()
    source = connection.execute("SELECT substr(text,1,32769) FROM conversation WHERE chat_id=? AND update_id=? "
        "AND role='user' AND source='boss' ORDER BY id LIMIT 1", (owner_chat_id, row["source_update"])).fetchone()
    if (update is None or update[0] != owner_chat_id or source is None or not isinstance(source[0], str)
            or len(source[0]) > 32768 or hashlib.sha256(source[0].encode()).hexdigest() != payload.get("source_hash")):
        return None, None, "source_no_longer_current"
    return row, payload, None


def read_scope(payload, allowed_hosts=None, allowed_projects=None, allowed_capabilities=None):
    """Original closed scope only; maintenance/cards never become read authority."""
    plan = payload.get("plan", {})
    operation = payload.get("operation")
    if (not isinstance(plan, dict) or plan.get("operation", "inspect") != "inspect"
            or (operation is not None and (not isinstance(operation, dict) or operation.get("operation") != "inspect"))
            or payload.get("allow_maintenance") or payload.get("approval")):
        raise ScopeDenied("effects and maintenance are outside retained read scope")
    hosts, projects = plan.get("hosts", []), plan.get("projects", [])
    for items, allowed in ((hosts, allowed_hosts), (projects, allowed_projects)):
        if (not isinstance(items, list) or len(items) > 20 or any(not isinstance(item, str) or not protocol.ID.fullmatch(item) for item in items)
                or len(set(items)) != len(items) or (allowed is not None and any(item not in allowed for item in items))):
            raise ScopeDenied("retained investigation exceeds approved scope")
    selected = plan.get("read_scope")
    capabilities = []
    if selected is not None:
        protocol.read_scope(selected)
        if selected["projects"] != projects or (allowed_capabilities is not None and any(item not in allowed_capabilities for item in selected["capabilities"])):
            raise ScopeDenied("retained capability exceeds approved scope")
        capabilities = selected["capabilities"]
    return {"hosts": list(hosts), "projects": list(projects), "capabilities": list(capabilities),
            "kind": selected["kind"] if selected is not None else "legacy", "read_scope": copy.deepcopy(selected)}


def metadata(row, payload, now=None):
    now = now or datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.utcoffset() != timedelta(0):
        raise ReferenceError("invalid retained clock")
    created, updated = timestamp(row["created_at"]), timestamp(row["updated_at"])
    if updated < created or max((created - now).total_seconds(), (updated - now).total_seconds()) > 30 or row["state"] not in STATES:
        raise ReferenceError("invalid retained state or dates")
    phase = payload.get("inference")
    if phase:
        bound = phase.get("binding") if isinstance(phase, dict) else None
        if (not isinstance(bound, dict) or bound.get("request_id") != row["id"] or bound.get("phase") not in {"planning", "synthesis"}
                or not isinstance(bound.get("nonce"), str) or not protocol.TOKEN.fullmatch(bound["nonce"])
                or phase.get("nonce") != bound["nonce"] or bound.get("source_hash") != payload["source_hash"]
                or any(not isinstance(bound.get(key), str) or not _SHA.fullmatch(bound[key]) for key in
                       ("source_hash", "input_hash", "inventory_hash", "facts_hash", "capability_hash"))):
            raise ReferenceError("retained inference authority mismatch")
    deadlines = {}
    lifecycle, started = payload.get("executor_lifecycle"), payload.get("executor_started")
    if isinstance(lifecycle, dict):
        for key in ("submitted_at", "queue_expires_at"):
            if key in lifecycle:
                timestamp(lifecycle[key]); deadlines[key] = lifecycle[key]
    if isinstance(started, dict):
        for key in ("started_at", "execution_expires_at"):
            if key in started:
                timestamp(started[key]); deadlines[key] = started[key]
    return {"created_at": row["created_at"], "updated_at": row["updated_at"],
            "phase": row["state"].split("_", 1)[0] if row["state"].startswith(("planning_", "synthesis_")) else row["state"],
            "deadlines": deadlines}


def validate_input(value):
    if not isinstance(value, dict) or "query" not in value or set(value) - {"query", "source_update", "lang"}:
        raise ReferenceError("retained read input fields mismatch")
    query = value["query"]
    if not isinstance(query, str) or not query.strip() or len(query) > 16000:
        raise ReferenceError("retained read query is invalid")
    result = {"query": query.strip(), "lang": value.get("lang", common.detect_lang(query) or "en")}
    if not isinstance(result["lang"], str) or result["lang"] not in {"ru", "en"}:
        raise ReferenceError("retained read language is invalid")
    if "source_update" in value:
        reference = value["source_update"]
        if type(reference) is not int or not 0 <= reference <= 2**53 - 1:
            raise ReferenceError("retained source reference is invalid")
        result["source_update"] = reference
    return result


def _task_source(cfg, conn, task, step, inputs):
    current = store.assistant_task_get(conn, task["id"], task["chat_id"])
    if (current is None or current["status"] not in {"planned", "running"}
            or task["chat_id"] not in getattr(cfg, "allowed_chat_ids", ())
            or current["source_update"] != task["source_update"] or current["source_hash"] != task["source_hash"]
            or step["task_id"] != task["id"] or step["tool"] not in TOOLS or fleet_lifecycle.admission_paused(conn)):
        raise ReferenceError("retained read owner/task is no longer authorized")
    source = store.assistant_task_source(conn, task["chat_id"], task["source_update"])
    binding = json.loads(step["bindings_json"] or "{}").get("query")
    if (source is None or tasking.source_hash(source["text"]) != task["source_hash"]
            or not isinstance(binding, dict) or binding.get("source") != "boss_span" or binding.get("transform") != "literal"
            or binding.get("source_hash") != task["source_hash"]):
        raise ReferenceError("retained read requires the unchanged canonical owner source")
    start, end = binding.get("start"), binding.get("end")
    if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(source["text"])
            or source["text"][start:end].strip() != inputs["query"]):
        raise ReferenceError("retained read source span mismatch")
    return conn.execute("SELECT id FROM conversation WHERE chat_id=? AND update_id=? AND role='user' AND source='boss' ORDER BY id LIMIT 1",
                        (task["chat_id"], task["source_update"])).fetchone()[0]


def _target(conn, task, inputs, source_id):
    if "source_update" in inputs:
        reference = inputs["source_update"]
        # A selected historical numeric reference must be present in the owner
        # source, not invented by a model or copied from a predecessor receipt.
        pattern = r"(?:#\s*|(?:request|investigation|update|source_update)\s*(?:#|=)?\s*)" + str(reference) + r"(?!\d)"
        if not re.search(pattern, inputs["query"], re.I):
            raise ReferenceError("retained reference is not named in the owner query")
        return reference, False
    prior = conn.execute("SELECT update_id FROM conversation WHERE chat_id=? AND role='user' AND source='boss' AND id<? ORDER BY id DESC LIMIT 1",
                         (task["chat_id"], source_id)).fetchone()
    return (prior[0], True) if prior is not None and type(prior[0]) is int else (None, True)


def _pages(payload):
    pages = payload.get("answer_pages")
    if pages is None and isinstance(payload.get("answer"), str):
        pages = [payload["answer"]]
    if (not isinstance(pages, list) or not pages or len(pages) > 16
            or any(not isinstance(page, str) or not page.strip() for page in pages)):
        return [], "result_not_available"
    if sum(map(len, pages)) + len(pages) - 1 > 2800:
        return [], "result_exceeds_shared_reply"
    if any(tasking.redact_derived_text(page) != page for page in pages):
        return [], "sensitive_retained_text"
    return list(pages), None


_GAPS = {
    "no_previous_investigation": ("No immediately preceding owner investigation is available; name an existing reference.", "\u041d\u0435\u0442 \u043f\u0440\u0435\u0434\u044b\u0434\u0443\u0449\u0435\u0439 \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0438; \u0443\u043a\u0430\u0436\u0438 \u043d\u0443\u0436\u043d\u0443\u044e."),
    "not_found_or_not_authorized": ("No matching investigation owned by this chat is available.", "\u041f\u0440\u043e\u0432\u0435\u0440\u043a\u0430 \u0432 \u044d\u0442\u043e\u043c \u0447\u0430\u0442\u0435 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430."),
    "source_no_longer_current": ("The original owner request was edited or removed.", "\u0418\u0441\u0445\u043e\u0434\u043d\u044b\u0439 \u0437\u0430\u043f\u0440\u043e\u0441 \u0438\u0437\u043c\u0435\u043d\u0451\u043d \u0438\u043b\u0438 \u0443\u0434\u0430\u043b\u0451\u043d."),
    "no_current_canonical_context": ("The previous investigation is outside the current conversation context; name its reference.", "\u041f\u0440\u0435\u0434\u044b\u0434\u0443\u0449\u0430\u044f \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0430 \u0443\u0436\u0435 \u0432\u043d\u0435 \u0442\u0435\u043a\u0443\u0449\u0435\u0433\u043e \u043a\u043e\u043d\u0442\u0435\u043a\u0441\u0442\u0430; \u0443\u043a\u0430\u0436\u0438 \u0435\u0451 \u044f\u0432\u043d\u043e."),
    "effects_outside_read_scope": ("This reference concerns maintenance or approval; these tools only read investigations.", "\u042d\u0442\u043e \u043e\u0431\u0441\u043b\u0443\u0436\u0438\u0432\u0430\u043d\u0438\u0435 \u0438\u043b\u0438 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0438\u0435, \u0430 \u043d\u0435 \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0430 \u0434\u043b\u044f \u0447\u0442\u0435\u043d\u0438\u044f."),
    "result_not_available": ("No completed answer is recorded yet.", "\u0413\u043e\u0442\u043e\u0432\u044b\u0439 \u043e\u0442\u0432\u0435\u0442 \u0435\u0449\u0451 \u043d\u0435 \u0441\u043e\u0445\u0440\u0430\u043d\u0451\u043d."),
    "result_exceeds_shared_reply": ("The recorded answer exceeds this task reply limit; use the original result.", "\u0421\u043e\u0445\u0440\u0430\u043d\u0451\u043d\u043d\u044b\u0439 \u043e\u0442\u0432\u0435\u0442 \u043f\u0440\u0435\u0432\u044b\u0448\u0430\u0435\u0442 \u043b\u0438\u043c\u0438\u0442 \u0437\u0430\u0434\u0430\u0447\u0438; \u043e\u0442\u043a\u0440\u043e\u0439 \u0438\u0441\u0445\u043e\u0434\u043d\u044b\u0439 \u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442."),
    "sensitive_retained_text": ("The recorded answer cannot be copied safely by this read adapter.", "\u042d\u0442\u043e\u0442 \u043e\u0442\u0432\u0435\u0442 \u043d\u0435\u043b\u044c\u0437\u044f \u0431\u0435\u0437\u043e\u043f\u0430\u0441\u043d\u043e \u0441\u043a\u043e\u043f\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u044d\u0442\u0438\u043c \u0441\u043f\u043e\u0441\u043e\u0431\u043e\u043c."),
    "retained_channel_unavailable": ("The retained investigation channel is unavailable.", "\u0421\u043e\u0445\u0440\u0430\u043d\u0451\u043d\u043d\u044b\u0435 \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0438 \u0441\u0435\u0439\u0447\u0430\u0441 \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u043d\u044b."),
    "purge_in_progress": ("Content deletion is settling; retained reads are paused.", "\u0427\u0442\u0435\u043d\u0438\u0435 \u043f\u0440\u0438\u043e\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d\u043e \u043d\u0430 \u0432\u0440\u0435\u043c\u044f \u0443\u0434\u0430\u043b\u0435\u043d\u0438\u044f \u0441\u043e\u0434\u0435\u0440\u0436\u0438\u043c\u043e\u0433\u043e.")}


def _gap(reference, reason, lang):
    text = _GAPS[reason][1 if lang == "ru" else 0]
    return {"version": VERSION, "source_update": reference, "status": "unavailable", "state": None, "phase": None,
            "created_at": None, "updated_at": None, "observed_at": None, "scope": None, "deadlines": {},
            "result_available": False, "gap": reason, "pages": [text]}


def read_task(cfg, conn, task, step, inputs):
    inputs = validate_input(inputs)
    source_id = _task_source(cfg, conn, task, step, inputs)
    target, implicit = _target(conn, task, inputs, source_id)
    if target is None:
        return _gap(None, "no_previous_investigation", inputs["lang"])
    try:
        row, payload, gap = canonical_reference(conn, task["chat_id"], source_update=target)
    except sqlite3.Error:
        return _gap(target, "retained_channel_unavailable", inputs["lang"])
    if gap:
        return _gap(target, gap, inputs["lang"])
    if implicit and not -30 <= protocol.age(row["created_at"]) < 7200:
        return _gap(target, "no_current_canonical_context", inputs["lang"])
    try:
        scope = read_scope(payload)
    except ScopeDenied:
        return _gap(target, "effects_outside_read_scope", inputs["lang"])
    meta = metadata(row, payload)
    pages, result_gap = _pages(payload) if row["state"] == "done" else ([], "result_not_available")
    observed_at = payload.get("result", {}).get("observed_at") if isinstance(payload.get("result"), dict) else None
    if observed_at is not None:
        timestamp(observed_at)
    value = {"version": VERSION, "source_update": target, "status": "ok", "state": row["state"],
             **meta, "observed_at": observed_at, "scope": {key: item for key, item in scope.items() if key != "read_scope"},
             "result_available": result_gap is None, "gap": result_gap, "pages": []}
    if step["tool"] == "fleet.result":
        if result_gap:
            value["pages"] = _gap(target, result_gap, inputs["lang"])["pages"]
        else:
            # A replay of the recorded answer is not a fresh observation or a
            # resend of its old Telegram delivery. This is a new owner read.
            heading = (("\u0421\u043e\u0445\u0440\u0430\u043d\u0451\u043d\u043d\u044b\u0439 \u043e\u0442\u0432\u0435\u0442 (" if inputs["lang"] == "ru" else "Recorded answer (") + row["updated_at"]
                       + ("); \u044d\u0442\u043e \u043f\u0440\u0435\u0436\u043d\u0438\u0435 \u043d\u0430\u0431\u043b\u044e\u0434\u0435\u043d\u0438\u044f, \u043d\u0435 \u043d\u043e\u0432\u0430\u044f \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0430." if inputs["lang"] == "ru" else "); retained evidence, not a fresh check."))
            value["pages"] = [heading, *pages]
    else:
        state = row["state"]
        en = {"preparing": "waiting for dispatch", "queued": "waiting for an executor", "inspection_started": "collecting observations",
              "ready": "observations received", "analysis_started": "reviewing observations", "delivering": "delivery is unsettled",
              "done": "completed", "failed": "failed", "ambiguous": "outcome uncertain", "cancelled": "recorded as cancelled"}.get(state)
        if en is None:
            en = "interpreting the request" if state.startswith("planning_") else "reviewing observations"
        if inputs["lang"] == "ru":
            text = "\u041f\u0440\u043e\u0432\u0435\u0440\u043a\u0430: " + ({"done": "\u0437\u0430\u0432\u0435\u0440\u0448\u0435\u043d\u0430", "ambiguous": "\u0438\u0441\u0445\u043e\u0434 \u043d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u0435\u043d", "failed": "\u043e\u0448\u0438\u0431\u043a\u0430", "cancelled": "\u043e\u0442\u043c\u0435\u043d\u0430 \u0437\u0430\u043f\u0438\u0441\u0430\u043d\u0430"}.get(state, "\u0435\u0449\u0451 \u0432 \u0440\u0430\u0431\u043e\u0442\u0435")) + ". " + row["updated_at"] + "."
        else:
            text = "Investigation: " + en + ". Updated " + row["updated_at"] + "."
        if result_gap:
            text += " " + _GAPS[result_gap][1 if inputs["lang"] == "ru" else 0]
        value["pages"] = [text]
    validate_shape(value)
    return value


def evidence(value):
    reference = value["source_update"]
    if value["status"] != "ok" or reference is None:
        return []
    return [{"id": "fleet-reference:" + str(reference), "source": "fleet-request:" + str(reference),
             "label": "Retained owner investigation; " + str(value["updated_at"]), "trust": "confirmed_local"}]


def validate_shape(value):
    if (not isinstance(value, dict) or set(value) != REFERENCE_KEYS or value["version"] != VERSION
            or not isinstance(value["status"], str) or value["status"] not in {"ok", "unavailable"} or type(value["result_available"]) is not bool
            or (value["source_update"] is not None and (type(value["source_update"]) is not int or value["source_update"] < 0))):
        raise ReferenceError("retained read output envelope mismatch")
    if value["status"] == "ok":
        if not isinstance(value["state"], str) or value["state"] not in STATES or not isinstance(value["phase"], str):
            raise ReferenceError("retained read state mismatch")
        for key in ("created_at", "updated_at"):
            timestamp(value[key])
        scope = value["scope"]
        if not isinstance(scope, dict) or set(scope) != SCOPE_KEYS - {"read_scope"}:
            raise ReferenceError("retained read scope mismatch")
        for key in ("hosts", "projects", "capabilities"):
            items = scope[key]
            if (not isinstance(items, list) or len(items) > 20 or any(not isinstance(item, str) for item in items)
                    or len(items) != len(set(items)) or (key == "capabilities" and any(item not in protocol.PROJECT_CAPABILITIES for item in items))
                    or (key != "capabilities" and any(not protocol.ID.fullmatch(item) for item in items))):
                raise ReferenceError("retained read output identities mismatch")
        if not isinstance(scope["kind"], str) or scope["kind"] not in {"legacy", "host", "projects", "all_projects"}:
            raise ReferenceError("retained read output scope kind mismatch")
    elif any(value[key] is not None for key in ("state", "phase", "created_at", "updated_at", "observed_at", "scope")):
        raise ReferenceError("unavailable retained read carries unsupported evidence")
    if value["observed_at"] is not None:
        timestamp(value["observed_at"])
    if (value["gap"] is not None and (not isinstance(value["gap"], str) or not re.fullmatch(r"[a-z_]{1,64}", value["gap"]))) or not isinstance(value["deadlines"], dict):
        raise ReferenceError("retained read gap/deadline mismatch")
    if set(value["deadlines"]) - {"submitted_at", "queue_expires_at", "started_at", "execution_expires_at"}:
        raise ReferenceError("retained read unknown deadline")
    for item in value["deadlines"].values():
        timestamp(item)
    pages = value["pages"]
    if (not isinstance(pages, list) or not pages or any(not isinstance(page, str) or not page for page in pages)
            or len("\n".join(pages)) > 3200 or any(tasking.redact_derived_text(page) != page for page in pages)):
        raise ReferenceError("retained read pages exceed safe reply contract")
    return value


def validate_result(cfg, conn, task, step, inputs, status, summary, data, support, artifact_id, effect_id):
    if (status != "ok" or artifact_id is not None or effect_id is not None or not isinstance(data, dict)
            or set(data) != {"schema", "value"} or data["schema"] != step["tool"] + "/v1"
            or not isinstance(data["value"], dict) or set(data["value"]) != {"reference"}):
        raise ReferenceError("retained read receipt contract mismatch")
    value = validate_shape(data["value"]["reference"])
    if value != read_task(cfg, conn, task, step, inputs) or support != evidence(value) or summary != "\n".join(value["pages"]):
        raise ReferenceError("retained investigation changed before receipt persistence")
    return data


def execute(cfg, conn, task, step, inputs):
    value = read_task(cfg, conn, task, step, inputs)
    return "ok", "\n".join(value["pages"]), {"schema": step["tool"] + "/v1", "value": {"reference": value}}, evidence(value), None, None
