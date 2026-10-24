"""Owner-loop retained investigation admission over the existing fleet ledger.

Retained reads and their task waiting/accounting adapter share the original
fleet/task rows. No MCP start, second ledger, synthetic owner source, credential
channel, effect or fleet Telegram delivery is introduced.
"""
import hashlib
import json
import math
import re
import sqlite3
from datetime import datetime, timezone

import common
import fleet_lifecycle
import fleet_protocol as protocol
import store
import tasking
import worker_client

VERSION = "cara-fleet-investigation/v1"
HEADER_KEYS = {"version", "delivery_mode", "principal", "chat_id", "source_update", "source_hash",
               "source_span", "caller_key", "input_hash", "request_id", "admitted_at"}
ACTIVE = ("admission_prepared", "admission_queued", "mcp_facts_queued", "mcp_facts_started", "preparing", "queued", "inspection_started", "executing", "ready", "approval_sending", "delivering", "analysis_started") + tuple(
    name + "_" + phase for name in ("planning", "synthesis") for phase in ("queued", "started", "completed", "consumed"))
TERMINAL = {"done", "failed", "cancelled", "ambiguous"}
KEY = re.compile(r"[a-z0-9][a-z0-9_.:-]{0,127}\Z")


class AdmissionError(ValueError):
    pass


class Conflict(AdmissionError):
    pass


def retained(payload):
    # A malformed waiter never downgrades into ordinary Telegram delivery.
    return isinstance(payload, dict) and "external_waiter" in payload


def request_id(chat_id, caller_key, principal="owner"):
    return hashlib.sha256(protocol.canonical([VERSION, principal, chat_id, caller_key]).encode()).hexdigest()[:32]


def _source(agent, chat_id, update):
    if type(chat_id) is not int or chat_id not in getattr(agent.cfg, "allowed_chat_ids", ()) or type(update) is not int or update < 0:
        raise AdmissionError("owner/source identity is unavailable")
    source = store.assistant_task_source(agent.conn, chat_id, update)
    receipt = store.telegram_update_get(agent.conn, update)
    if source is None or receipt is None or not isinstance(source["text"], str) or not 1 <= len(source["text"]) <= 32768:
        raise AdmissionError("a real canonical owner source is required")
    return source["text"], receipt["received_at"]


def _input(header, payload):
    value = {key: header[key] for key in ("version", "delivery_mode", "principal", "chat_id", "source_update", "source_hash", "source_span", "caller_key")} | {
        "question": payload["question"], "lang": payload["lang"]}
    if "task_waiter" in payload:
        value["task_waiter"] = payload["task_waiter"]
    if "mcp_admission" in payload:
        value["mcp_admission"] = payload["mcp_admission"]
    return value


def _validate(agent, row, payload):
    header = payload.get("external_waiter")
    if (not isinstance(header, dict) or set(header) != HEADER_KEYS or header["version"] != VERSION
            or header["delivery_mode"] != "retained" or header["principal"] != "owner"
            or not isinstance(header["caller_key"], str) or not KEY.fullmatch(header["caller_key"])
            or header["chat_id"] != row["chat_id"] or header["source_update"] != row["source_update"]
            or header["request_id"] != row["id"] or header["request_id"] != request_id(row["chat_id"], header["caller_key"])
            or payload.get("allow_maintenance") is not False or payload.get("approval") is not None
            or payload.get("lang") not in {"ru", "en"}):
        raise AdmissionError("retained admission contract mismatch")
    text, _received = _source(agent, row["chat_id"], row["source_update"])
    span = header["source_span"]
    if (not isinstance(span, list) or len(span) != 2 or any(type(item) is not int for item in span)
            or not 0 <= span[0] < span[1] <= len(text)
            or payload.get("question") != text[span[0]:span[1]].strip()
            or header["source_hash"] != tasking.source_hash(text) or payload.get("source_hash") != header["source_hash"]
            or header["input_hash"] != protocol.digest(_input(header, payload))):
        raise AdmissionError("retained owner source or input changed")
    if protocol.age(header["admitted_at"]) < -30:
        raise AdmissionError("retained admission date is invalid")
    return header


def current(agent, row, payload):
    """Phase admission/consumption guard, independent of model/transport data."""
    try:
        if not getattr(agent.cfg, "fleet_enabled", False) or fleet_lifecycle.admission_paused(agent.conn):
            return False
        header = _validate(agent, row, payload)
        fresh = agent.conn.execute("SELECT state,payload FROM fleet_requests WHERE id=?", (row["id"],)).fetchone()
        if fresh is None or fresh["state"] in TERMINAL:
            return False
        saved = json.loads(fresh["payload"])
        if saved.get("external_waiter") != header or saved.get("retained_cancel"):
            return False
        if payload.get("retained_cancel") or not task_current(agent, row, payload):
            return False
        if "mcp_admission" in payload:
            import fleet_mcp_admission
            if not fleet_mcp_admission.current(agent, row, payload):
                return False
        plan = payload.get("plan", {})
        operation = payload.get("operation", {})
        return (isinstance(plan, dict) and plan.get("operation", "inspect") == "inspect"
                and isinstance(operation, dict) and operation.get("operation", "inspect") == "inspect")
    except (ValueError, TypeError, KeyError, OSError):
        return False


def authority_denied(error):
    # Recognize shared/transport authority exception ancestry without importing
    # an interface module into the business API. This only refuses work.
    return error is not None and any(cls.__name__ in {"ScopeDenied", "AdmissionError", "Conflict", "PermissionError"}
                                     for cls in type(error).__mro__)


def fallback_allowed(agent, row, payload, error=None):
    # CatalogMixin uses its notify flag to permit a local grounded fallback.
    # Retained mode may use that answer, while the base failure path still
    # suppresses all Telegram. Denial is never an interface-failure fallback.
    if not retained(payload):
        return True
    return (not authority_denied(error) and not str(payload.get("inference", {}).get("error", "")).startswith("ScopeDenied")
            and current(agent, row, payload))


def _handle(row, payload):
    return {"version": VERSION, "request_id": row["id"], "source_update": row["source_update"],
            "state": row["state"], "created_at": row["created_at"], "updated_at": row["updated_at"],
            "delivery_mode": "retained", "result_available": row["state"] == "done" and payload.get("retained_delivery", {}).get("state") == "available",
            "cancellation": "requested" if payload.get("retained_cancel") else "none"}


def start(agent, *, chat_id, source_update, caller_key, source_span=None, lang=None, principal="owner", _task_waiter=None):
    """Persist one real-source read intent and return the same handle on retry.

    Only the canonical owner principal is qualified. The task adapter supplies
    its code-owned waiter; delegated interface principals remain unqualified.
    """
    import fleet
    if principal != "owner" or not isinstance(caller_key, str) or not KEY.fullmatch(caller_key):
        raise AdmissionError("unqualified principal or caller identity")
    text, received_at = _source(agent, chat_id, source_update)
    span = list(source_span) if isinstance(source_span, (list, tuple)) else [0, len(text)] if source_span is None else None
    if (not isinstance(span, list) or len(span) != 2 or any(type(item) is not int for item in span)
            or not 0 <= span[0] < span[1] <= len(text)):
        raise AdmissionError("invalid canonical source span")
    question = text[span[0]:span[1]].strip()
    selected_lang = lang if lang is not None else common.detect_lang(question) or "en"
    if not question or len(question) > 16000 or not isinstance(selected_lang, str) or selected_lang not in {"ru", "en"}:
        raise AdmissionError("invalid retained question or language")
    identity = request_id(chat_id, caller_key)
    header = {"version": VERSION, "delivery_mode": "retained", "principal": "owner", "chat_id": chat_id,
        "source_update": source_update, "source_hash": tasking.source_hash(text), "source_span": span,
        "caller_key": caller_key, "request_id": identity, "admitted_at": protocol.now()}
    expected = {"question": question, "lang": selected_lang}
    if _task_waiter is not None:
        expected["task_waiter"] = _task_waiter
    header["input_hash"] = protocol.digest(_input(header, expected))
    # The unique request ID and existing (chat,source_update) constraint are the
    # idempotency boundary. No lookup limited to recent history or second ledger.
    try:
        agent.conn.execute("BEGIN IMMEDIATE")
        existing = agent.conn.execute("SELECT * FROM fleet_requests WHERE id=? OR (chat_id=? AND source_update=?)",
                                      (identity, chat_id, source_update)).fetchall()
        if existing:
            if len(existing) != 1:
                raise Conflict("caller identity conflicts with an existing investigation")
            row = existing[0]
            payload = json.loads(row["payload"])
            old = _validate(agent, row, payload) if retained(payload) else None
            if old is None or row["id"] != identity or old["input_hash"] != header["input_hash"] or old["caller_key"] != caller_key:
                raise Conflict("existing owner request cannot be repurposed")
            agent.conn.commit()
            return _handle(row, payload)
        if (not getattr(agent.cfg, "fleet_enabled", False) or not getattr(agent, "fleet_async", False)
                or fleet_lifecycle.admission_paused(agent.conn) or not -30 <= protocol.age(received_at) < 7200):
            raise AdmissionError("new retained admission is unavailable")
        count = agent.conn.execute("SELECT count(*) FROM fleet_requests WHERE state IN (" + ",".join("?" for _ in ACTIVE) + ")", ACTIVE).fetchone()[0]
        if count >= 8:
            raise AdmissionError("existing investigation capacity is occupied")
        if _task_waiter is not None and not task_current(agent, {"id": identity, "chat_id": chat_id, "source_update": source_update}, expected):
            raise AdmissionError("task waiting authority is no longer current")
        inventory = fleet.catalog(agent.cfg)
        previous = agent._fleet_recent(chat_id, text)
        agent._fleet_begin_async(chat_id, selected_lang, question, None, False, source_update, inventory, previous,
                                 external_waiter=header, task_waiter=_task_waiter)
    except BaseException:
        if agent.conn.in_transaction:
            agent.conn.rollback()
        raise
    row = agent.conn.execute("SELECT * FROM fleet_requests WHERE id=?", (identity,)).fetchone()
    if row is None:
        raise AdmissionError("retained intent was not recorded")
    return _handle(row, json.loads(row["payload"]))


def status(agent, *, chat_id, source_update, caller_key, principal="owner"):
    if principal != "owner" or not isinstance(caller_key, str) or not KEY.fullmatch(caller_key):
        raise AdmissionError("unqualified retained reference")
    row = agent.conn.execute("SELECT * FROM fleet_requests WHERE id=? AND chat_id=? AND source_update=?",
        (request_id(chat_id, caller_key), chat_id, source_update)).fetchone()
    if row is None or fleet_lifecycle.admission_paused(agent.conn):
        return None
    payload = json.loads(row["payload"])
    _validate(agent, row, payload)
    return _handle(row, payload)


def result(agent, **reference):
    handle = status(agent, **reference)
    if handle is None or not handle["result_available"]:
        return None
    row = agent.conn.execute("SELECT * FROM fleet_requests WHERE id=?", (handle["request_id"],)).fetchone()
    if row is None:
        return None
    payload = json.loads(row["payload"])
    _validate(agent, row, payload)
    if row["state"] != "done" or fleet_lifecycle.admission_paused(agent.conn):
        return None
    pages = payload.get("answer_pages", [payload.get("answer", "")])
    if not isinstance(pages, list) or not pages or any(not isinstance(page, str) or not page for page in pages):
        raise AdmissionError("retained result has no complete answer")
    return {**handle, "pages": list(pages), "observed_at": payload.get("result", {}).get("observed_at") if isinstance(payload.get("result"), dict) else None,
            "facts_integrity": payload.get("facts_integrity"), "model_calls": payload.get("model_calls", 0), "cost": payload.get("cost", 0)}


def retain(agent, row, payload):
    """Complete in the original ledger without any Telegram delivery boundary."""
    import fleet
    if not current(agent, row, payload):
        raise AdmissionError("retained result authority is no longer current")
    pages = payload.get("answer_pages", [payload.get("answer", "")])
    if not isinstance(pages, list) or not pages or any(not isinstance(page, str) or not page for page in pages):
        raise AdmissionError("retained result has no complete answer")
    payload["answer_pages"] = list(pages)
    payload["answer"] = "\n\n".join(pages)
    payload["retained_delivery"] = {"state": "available", "recorded_at": protocol.now(), "telegram_attempts": 0}
    if payload.get("binding"):
        worker_client.acknowledge(fleet.spool(agent.cfg), payload["binding"]["job_id"])
    agent._fleet_save(row, "done", payload, "retained_result_recorded")


def cancel(agent, *, chat_id, source_update, caller_key, principal="owner"):
    """Request cancellation; do not pretend an already-started read stopped."""
    if principal != "owner" or chat_id not in getattr(agent.cfg, "allowed_chat_ids", ()) or not isinstance(caller_key, str) or not KEY.fullmatch(caller_key):
        raise AdmissionError("retained cancellation is not owner-bound")
    row = agent.conn.execute("SELECT * FROM fleet_requests WHERE id=? AND chat_id=? AND source_update=?",
        (request_id(chat_id, caller_key), chat_id, source_update)).fetchone()
    if row is None or fleet_lifecycle.admission_paused(agent.conn):
        return None
    payload = json.loads(row["payload"])
    header = payload.get("external_waiter")
    if not isinstance(header, dict) or header.get("caller_key") != caller_key or header.get("principal") != principal:
        raise Conflict("retained cancellation binding changed")
    if row["state"] in TERMINAL:
        return _handle(row, payload)
    payload.setdefault("retained_cancel", {"requested_at": protocol.now()})
    agent._fleet_save(row, row["state"], payload, "retained_cancel_requested")
    agent._fleet_cancel_executor(row, payload, "retained_caller_cancelled")
    call = getattr(agent, "_fleet_inference_calls", {}).get(row["id"])
    if call is not None:
        call.cancel()
    # Only definitely unsent work receives a confirmed local cancelled state.
    if row["state"] == "planning_queued" and not payload.get("inference", {}).get("attempts") and (call is None or call.future is None):
        agent._fleet_save(row, "cancelled", payload, "retained_unsent_cancelled")
    fresh = agent.conn.execute("SELECT * FROM fleet_requests WHERE id=?", (row["id"],)).fetchone()
    return _handle(fresh, json.loads(fresh["payload"]))


def account_discarded(agent, row, payload):
    """Record settled usage on a terminal retained row without reviving content."""
    phase = payload.get("inference")
    if retained(payload) and isinstance(phase, dict) and not phase.get("accounted"):
        phase["cost"] = agent._fleet_phase_cost(phase)
        payload["cost"] = payload.get("cost", 0) + phase["cost"]
        phase["accounted"] = True
        save_accounted(agent, row, row["state"], payload, "retained_discard_accounted")


TASK_VERSION = "cara-fleet-task-wait/v1"
TASK_KEYS = {"version", "task_id", "step_id", "step_key", "idempotency_key", "input_hash", "policy_version", "implementation_version", "request_id"}
TASK_OUTPUT_KEYS = {"version", "request_id", "source_update", "state", "observed_at", "pages", "gap"}


def validate_task_input(value):
    if not isinstance(value, dict) or set(value) != {"query"}:
        raise AdmissionError("fleet.investigate requires one owner-bound query")
    query = value["query"]
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 16000:
        raise AdmissionError("fleet.investigate query is invalid")
    return {"query": query.strip()}


def task_caller(task, step):
    return "task:" + str(task["id"]) + ":step:" + str(step["id"])


def _task_binding(task, step, inputs):
    import tool_broker
    return {"version": TASK_VERSION, "task_id": task["id"], "step_id": step["id"],
            "step_key": step["step_key"], "idempotency_key": step["idempotency_key"],
            "input_hash": protocol.digest(inputs), "policy_version": tool_broker.POLICY_VERSION,
            "implementation_version": tool_broker.IMPLEMENTATION_VERSION,
            "request_id": request_id(task["chat_id"], task_caller(task, step))}


def task_reference_current(agent, task, step):
    """Verify a code-owned wait reference even when admission left no fleet row."""
    try:
        import task_runner
        import tool_broker
        if task is None or step is None or step["tool"] != "fleet.investigate":
            return False
        tool_broker.assert_policy(tool_broker.get_spec(step["tool"]))
        task_runner._source_for_task(agent.conn, task)
        inputs = task_runner.resolve_inputs(agent.conn, task, step)
        waiter = _task_binding(task, step, inputs)
        binding = json.loads(step["bindings_json"])["query"]
        return (task["chat_id"] in getattr(agent.cfg, "allowed_chat_ids", ())
                and binding.get("source") == "boss_span" and binding.get("transform") == "literal"
                and step["worker_job_id"] == waiter["request_id"]
                and step["worker_nonce"] == task_caller(task, step)
                and step["worker_input_hash"] == waiter["input_hash"]
                and step["policy_version"] == waiter["policy_version"]
                and step["implementation_version"] == waiter["implementation_version"])
    except (ValueError, TypeError, KeyError, OSError, RuntimeError):
        return False


def task_current(agent, row, payload):
    """Check only fixed durable task authority, never model-supplied targets."""
    waiter = payload.get("task_waiter")
    if waiter is None:
        return True
    try:
        import task_runner
        import tool_broker
        if (not isinstance(waiter, dict) or set(waiter) != TASK_KEYS or waiter["version"] != TASK_VERSION
                or type(waiter["task_id"]) is not int or type(waiter["step_id"]) is not int):
            return False
        task = store.assistant_task_get(agent.conn, waiter["task_id"])
        step = store.assistant_task_step_get(agent.conn, waiter["step_id"], waiter["task_id"])
        if (not task_reference_current(agent, task, step) or task["status"] not in {"planned", "running", "waiting_approval"}
                or step["status"] != "waiting_fleet" or step["tool"] != "fleet.investigate"
                or task["chat_id"] != row["chat_id"] or task["source_update"] != row["source_update"]
                or waiter["request_id"] != row["id"] or step["worker_job_id"] != row["id"]
                or step["worker_nonce"] != task_caller(task, step)
                or step["policy_version"] != tool_broker.POLICY_VERSION
                or step["implementation_version"] != tool_broker.IMPLEMENTATION_VERSION):
            return False
        tool_broker.assert_policy(tool_broker.get_spec(step["tool"]))
        task_runner._source_for_task(agent.conn, task)
        inputs = task_runner.resolve_inputs(agent.conn, task, step)
        binding = _task_binding(task, step, inputs)
        provenance = json.loads(step["bindings_json"])["query"]
        return (binding == waiter and step["worker_input_hash"] == binding["input_hash"]
                and provenance.get("source") == "boss_span" and provenance.get("transform") == "literal"
                and inputs["query"] == payload.get("question")
                and (not payload.get("external_waiter") or payload["external_waiter"]["source_span"] == [provenance["start"], provenance["end"]]))
    except (ValueError, TypeError, KeyError, OSError, task_runner.TaskBlocked, RuntimeError):
        return False


def start_task(agent, conn, task, step, inputs):
    """Persist the external wait before admission; never re-admit on a poll."""
    import task_runner
    if fleet_lifecycle.admission_paused(conn):
        raise AdmissionError("task investigation admission is paused")
    task_runner._source_for_task(conn, task)
    inputs = validate_task_input(inputs)
    binding = json.loads(step["bindings_json"]).get("query", {})
    if binding.get("source") != "boss_span" or binding.get("transform") != "literal":
        raise AdmissionError("investigation must use a literal canonical owner span")
    waiter = _task_binding(task, step, inputs)
    caller = task_caller(task, step)
    existing = step["worker_job_id"]
    if existing is not None and (existing != waiter["request_id"] or step["worker_nonce"] != caller or step["worker_input_hash"] != waiter["input_hash"]):
        raise Conflict("stored task investigation reference changed")
    try:
        conn.execute("BEGIN IMMEDIATE")
        changed = conn.execute("UPDATE assistant_task_steps SET status='waiting_fleet',worker_job_id=?,worker_nonce=?,worker_input_hash=?,worker_submitted_at=COALESCE(worker_submitted_at,?),updated_at=? WHERE id=? AND status='claimed'",
            (waiter["request_id"], caller, waiter["input_hash"], protocol.now(), protocol.now(), step["id"])).rowcount
        if changed != 1:
            raise AdmissionError("task step is no longer claimable")
        store.task_attempt_finish(conn, step["id"], "waiting", input_hash=waiter["input_hash"], commit=False)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    if existing is not None:
        # A resumed step reconciles its original request, including unknown
        # terminal outcomes. Missing evidence never authorizes another start.
        return waiter["request_id"]
    try:
        start(agent, chat_id=task["chat_id"], source_update=task["source_update"], caller_key=caller,
              source_span=[binding["start"], binding["end"]], _task_waiter=waiter)
    except (AdmissionError, ValueError, OSError, ConnectionError):
        # The reference was durably stored before entry. The ordinary poll
        # reconciles a committed intent or reports a precise admission gap.
        pass
    return waiter["request_id"]


def _settle_task_usage(agent, payload):
    """Reconcile each original usage ID once, including conservative unknowns."""
    waiter = payload.get("task_waiter")
    usages = payload.get("task_usage", {})
    if waiter is None or not usages:
        return
    delta = 0.0
    for identity, prior in usages.items():
        usage = agent.conn.execute("SELECT cost_usd FROM llm_usage WHERE id=?", (int(identity),)).fetchone()
        if usage is None:
            continue  # Missing usage cannot release an accepted reservation.
        actual = float(usage["cost_usd"])
        if not math.isfinite(actual) or actual < 0:
            raise AdmissionError("invalid task investigation usage")
        delta += actual - prior
        usages[identity] = actual
    agent.conn.execute("UPDATE assistant_tasks SET task_cost_usd=MAX(0,task_cost_usd+?),updated_at=? WHERE id=?",
                       (delta, protocol.now(), waiter["task_id"]))


def dispatch_bound(agent, row, payload, receipt):
    """One task debit and fleet dispatch receipt, atomically before transport."""
    phase = payload["inference"]
    if "task_waiter" not in payload:
        phase["attempts"].append(receipt)
        agent._fleet_save(row, phase["name"] + "_started", payload, "inference_dispatch_bound")
        return
    try:
        agent.conn.execute("BEGIN IMMEDIATE")
        if not task_current(agent, row, payload):
            raise AdmissionError("task investigation authority changed before dispatch")
        _settle_task_usage(agent, payload)
        usage = agent.conn.execute("SELECT cost_usd FROM llm_usage WHERE id=?", (receipt["reservation_id"],)).fetchone()
        cost = float(usage["cost_usd"]) if usage else -1
        if not math.isfinite(cost) or cost < 0:
            raise AdmissionError("task investigation reservation is unavailable")
        if payload.get("cost", 0) + agent._fleet_phase_cost(phase) + cost > agent.cfg.task_cost_limit_usd + 1e-12:
            raise AdmissionError("investigation reservation exceeds its budget")
        usages = payload.setdefault("task_usage", {})
        if str(receipt["reservation_id"]) in usages:
            raise Conflict("task investigation reservation was already admitted")
        changed = agent.conn.execute("UPDATE assistant_tasks SET model_calls=model_calls+1,task_cost_usd=task_cost_usd+?,updated_at=? WHERE id=? AND status IN ('planned','running','waiting_approval') AND model_calls<? AND task_cost_usd+?<=?",
            (cost, protocol.now(), payload["task_waiter"]["task_id"], agent.cfg.task_model_call_limit, cost, agent.cfg.task_cost_limit_usd + 1e-12)).rowcount
        if changed != 1:
            raise AdmissionError("task model-call or reservation budget is exhausted")
        usages[str(receipt["reservation_id"])] = cost
        phase["attempts"].append(receipt)
        agent._fleet_save(row, phase["name"] + "_started", payload, "inference_dispatch_bound")
    except BaseException:
        agent.conn.rollback()
        raise


def save_accounted(agent, row, state, payload, event):
    if "task_waiter" not in payload:
        agent._fleet_save(row, state, payload, event)
        return
    try:
        agent.conn.execute("BEGIN IMMEDIATE")
        _settle_task_usage(agent, payload)
        agent._fleet_save(row, state, payload, event)
    except BaseException:
        agent.conn.rollback()
        raise


def reconcile_task_usage(agent, row, payload):
    usages = payload.get("task_usage", {})
    if "task_waiter" not in payload or not usages:
        return
    changed = any((usage := agent.conn.execute("SELECT cost_usd FROM llm_usage WHERE id=?", (int(identity),)).fetchone()) is not None
                  and float(usage["cost_usd"]) != prior for identity, prior in usages.items())
    if changed:
        save_accounted(agent, row, row["state"], payload, "task_usage_reconciled")


def cancel_task(agent, task, step):
    if step["tool"] != "fleet.investigate" or not step["worker_job_id"]:
        return None
    return cancel(agent, chat_id=task["chat_id"], source_update=task["source_update"], caller_key=task_caller(task, step))


def validate_task_output(value):
    if (not isinstance(value, dict) or set(value) != TASK_OUTPUT_KEYS or value["version"] != TASK_VERSION
            or not isinstance(value["request_id"], str) or not protocol.TOKEN.fullmatch(value["request_id"])
            or type(value["source_update"]) is not int or value["source_update"] < 0
            or not isinstance(value["state"], str) or value["state"] not in TERMINAL
            or not isinstance(value["gap"], str) or value["gap"] not in {"", "admission_unavailable", "investigation_failed", "unknown_outcome", "investigation_cancelled"}
            or not isinstance(value["pages"], list) or not 1 <= len(value["pages"]) <= 32
            or any(not isinstance(page, str) or not page or len(page) > 3500 for page in value["pages"])):
        raise AdmissionError("task investigation output contract mismatch")
    if value["observed_at"] is not None:
        import fleet_shared_reads
        fleet_shared_reads.timestamp(value["observed_at"])
    return value


def task_evidence(identity):
    return [{"id": "fleet:" + identity, "source": "fleet:" + identity,
             "label": "Retained read-only investigation", "trust": "confirmed_local"}]


def safe_task_receipt(data, evidence, *, request_id):
    """Exempt only the task's exact typed reference from generic redaction.

    Arbitrary long strings, pages and labels retain the legacy secret filter.
    The caller revalidates source/result inside its receipt transaction; the
    store supplies the original code-owned step reference independently.
    """
    if (not isinstance(data, dict) or set(data) != {"schema", "value"} or data["schema"] != "fleet.investigate/v1"
            or not isinstance(data["value"], dict) or set(data["value"]) != {"investigation"}):
        raise AdmissionError("task investigation safe receipt envelope mismatch")
    value = validate_task_output(data["value"]["investigation"])
    if (not isinstance(request_id, str) or not protocol.TOKEN.fullmatch(request_id)
            or value["request_id"] != request_id or evidence != task_evidence(request_id)):
        raise AdmissionError("task investigation receipt reference changed")
    def checked(item, path=()):
        if path == ("value", "investigation", "request_id"):
            return request_id
        if isinstance(item, str):
            if tasking.redact_derived_text(item) != item:
                raise AdmissionError("task investigation receipt contains secret-shaped free text")
            return item
        if isinstance(item, dict):
            return {key: checked(child, path + (key,)) for key, child in item.items()}
        if isinstance(item, list):
            return [checked(child, path + (index,)) for index, child in enumerate(item)]
        return item
    return checked(data), task_evidence(request_id)


def task_answer_pages(pages):
    """Re-page existing qualified answers without raising task output limits.

    Original short pages retain their exact representation. Oversize catalog
    pages become contiguous chunks of the same rendered text; their full source
    remains in the retained row for exact managed-document delivery.
    """
    if (not isinstance(pages, list) or not 1 <= len(pages) <= 32
            or any(not isinstance(page, str) or not page or len(page) > 3700 for page in pages)):
        raise AdmissionError("retained answer exceeds qualified source page bounds")
    cleaned = [tasking.redact_derived_text(page) for page in pages]
    if all(len(page) <= 3500 for page in cleaned):
        return cleaned
    text = "\n".join(cleaned)
    if len(text) > 32 * 3500:
        raise AdmissionError("full investigation answer exceeds existing task page capacity")
    return [text[offset:offset + 3500] for offset in range(0, len(text), 3500)]


def task_outcome(agent, task, step, row, payload):
    """Build code-owned receipt data only after the source-bound work settles."""
    gap, pages, observed = "", None, None
    if row is None:
        state, gap = "failed", "admission_unavailable"
    else:
        _validate(agent, row, payload)
        if not task_current(agent, row, payload):
            raise AdmissionError("task investigation binding changed before receipt")
        state = row["state"]
        if state == "done":
            value = result(agent, chat_id=task["chat_id"], source_update=task["source_update"], caller_key=task_caller(task, step))
            if value is None:
                raise AdmissionError("task investigation result is no longer available")
            pages, observed = value["pages"], value["observed_at"]
        else:
            gap = "unknown_outcome" if state == "ambiguous" or payload.get("inference", {}).get("usage_unknown") else "investigation_cancelled" if state == "cancelled" else "investigation_failed"
    if pages is None:
        pages = [{"admission_unavailable": "The read-only investigation was not admitted; no new attempt was started.",
                  "unknown_outcome": "The investigation was interrupted; its outcome is unknown. No paid call was replayed.",
                  "investigation_cancelled": "The investigation was cancelled; no completed answer is available.",
                  "investigation_failed": "The investigation ended without a reliable answer; no completed result is available."}[gap]]
    value = {"version": TASK_VERSION, "request_id": step["worker_job_id"], "source_update": task["source_update"],
             "state": state, "observed_at": observed, "pages": task_answer_pages(pages), "gap": gap}
    validate_task_output(value)
    evidence = task_evidence(step["worker_job_id"])
    return ("partial" if gap else "ok", "Read-only investigation result recorded.",
            {"schema": "fleet.investigate/v1", "value": {"investigation": value}}, evidence, None, None)
