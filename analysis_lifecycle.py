"""Erase only new analysis copies when their owner removes a source.

Original owner dialogue, legacy tasks, receipt identities and paid accounting
remain. An explicit erasure marker revokes reuse/delivery authority; it does not
rewrite the historical fact that an earlier answer was delivered.
"""
import json
from datetime import datetime, timezone

SCHEMA = "analysis-erasure/v1"
HOLD_SCHEMA = "analysis-purge/v1"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load(value, default=None):
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def is_erased(task):
    if task is None:
        return False
    plan = _load(task["plan_json"], {})
    marker = plan.get("analysis_erasure") if isinstance(plan, dict) else None
    return isinstance(marker, dict) and marker.get("schema") == SCHEMA


def erased_text(task=None):
    import common
    ru = task is not None and common.detect_lang(task["objective"]) == "ru"
    return "Данные анализа удалены вместе с источником." if ru else "Analysis evidence was removed with its source."


def _authorization(conn):
    row = conn.execute("SELECT value FROM kv WHERE key='task_purge_authorization'").fetchone()
    value = _load(row[0] if row else None, {})
    return value if isinstance(value, dict) else {}


def admission_held(conn):
    value = _authorization(conn)
    hold = value.get("analysis_purge")
    return value.get("phase") == "prepared" and isinstance(hold, dict) and hold.get("schema") == HOLD_SCHEMA


def _tasks(conn):
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='assistant_tasks'").fetchone() is None:
        return []
    return conn.execute(
        "SELECT DISTINCT t.* FROM assistant_tasks t JOIN assistant_task_steps s"
        " ON s.task_id=t.id WHERE s.tool='analysis.reason'").fetchall()


def _references(value):
    """Read code-owned source metadata; prose is never a source identifier."""
    notes, reminders = set(), set()
    if isinstance(value, dict):
        if value.get("kind") == "note" and type(value.get("note_no")) is int:
            notes.add(value["note_no"])
        if value.get("kind") == "reminder":
            pieces = str(value.get("id") or "").split(":")
            if len(pieces) >= 2 and pieces[0] == "reminder" and pieces[1].isdigit():
                reminders.add(int(pieces[1]))
        for child in value.values():
            child_notes, child_reminders = _references(child)
            notes.update(child_notes); reminders.update(child_reminders)
    elif isinstance(value, list):
        for child in value:
            child_notes, child_reminders = _references(child)
            notes.update(child_notes); reminders.update(child_reminders)
    return notes, reminders


def _parent_id(plan):
    state = plan.get("analysis_state") or {}
    context = state.get("context") or {}
    if type(context.get("task_id")) is int:
        return context["task_id"]
    steps = plan.get("steps") or []
    if len(steps) == 1 and isinstance(steps[0], dict):
        value = steps[0].get("input", {}).get("context_task_id")
        if type(value) is int:
            return value
    return None


def affected(conn, *, notes=(), reminders=()):
    """Return new analysis IDs affected by source IDs, including descendants."""
    wanted_notes = set(notes)  # (chat_id, stable note_no)
    wanted_reminders = set(reminders)
    plans, selected = {}, set()
    rows = _tasks(conn)
    for row in rows:
        if is_erased(row):
            continue
        plan = _load(row["plan_json"], {})
        plans[row["id"]] = plan
        note_ids, reminder_ids = _references(plan.get("analysis_state", {}))
        for receipt in conn.execute("SELECT data_json FROM tool_receipts WHERE task_id=?", (row["id"],)):
            a, b = _references(_load(receipt[0], {}))
            note_ids.update(a); reminder_ids.update(b)
        if ({(row["chat_id"], n) for n in note_ids} & wanted_notes
                or reminder_ids & wanted_reminders):
            selected.add(row["id"])
    while True:
        added = {task_id for task_id, plan in plans.items() if _parent_id(plan) in selected}
        if added <= selected:
            break
        selected.update(added)
    return sorted(selected)


def _scope_sources(conn, scope, category=None):
    import store
    ids = []
    if scope in {"category", "journal"}:
        ids = store._messages_in_category(conn, category)
    elif scope == "messages":
        ids = store._non_journal_message_ids(conn)
        if ids is None:
            ids = [row[0] for row in conn.execute("SELECT id FROM messages")]
    notes = []
    for mid in ids:
        row = conn.execute("SELECT chat_id,note_no FROM messages WHERE id=?", (mid,)).fetchone()
        if row is not None and row["note_no"] is not None:
            notes.append((row["chat_id"], row["note_no"]))
    reminders = ([row[0] for row in conn.execute("SELECT id FROM reminders")]
                 if scope == "reminders" else [])
    return notes, reminders


def preview(conn, scope, category=None):
    if scope not in {"category", "journal", "messages", "reminders"}:
        return {}
    notes, reminders = _scope_sources(conn, scope, category)
    count = len(affected(conn, notes=notes, reminders=reminders))
    return {"analysis_evidence_copies": count} if count else {}


def _erase(conn, task_id):
    import store
    row = store.assistant_task_get(conn, task_id)
    if row is None or is_erased(row):
        return
    plan = _load(row["plan_json"], {})
    state = plan.get("analysis_state") or {}
    # Exact usage identities remain available while a revoked transport settles.
    phases = [{key: phase[key] for key in ("stage", "nonce", "input_hash", "state", "usage_ids") if key in phase}
              for phase in state.get("phases", []) if isinstance(phase, dict)]
    plan["analysis_state"] = {"schema": "analysis-state/v1", "stage": "erased",
                              "phases": phases, "reads": [], "context": None,
                              "corrections": 0, "unknowns": []}
    plan["analysis_erasure"] = {"schema": SCHEMA, "at": _now()}
    text = erased_text(row)
    active = row["status"] not in {"completed", "failed", "cancelled"}
    conn.execute("UPDATE assistant_tasks SET plan_json=?,final_summary=?,"
                 "status=CASE WHEN ? THEN 'cancelled' ELSE status END,"
                 "completed_at=CASE WHEN ? THEN ? ELSE completed_at END,next_action_at=NULL,updated_at=? WHERE id=?",
                 (_json(plan), text, active, active, _now(), _now(), task_id))
    conn.execute("UPDATE assistant_task_steps SET purpose=?,input_json='{}',bindings_json='{}',"
                 "error=NULL,status=CASE WHEN status IN ('pending','claimed','running','blocked','waiting_approval')"
                 " THEN 'cancelled' ELSE status END,updated_at=? WHERE task_id=?", (text, _now(), task_id))
    conn.execute("UPDATE task_step_attempts SET error=NULL,status=CASE WHEN status IN ('started','waiting')"
                 " THEN 'cancelled' ELSE status END WHERE task_id=?", (task_id,))
    # This explicit purge erasure is not a new successful tool output. Immutable
    # identity/status/version/accounting fields remain; no payload is replayable.
    tombstone = _json({"schema": SCHEMA, "value": {"evidence_removed": True}})
    conn.execute("UPDATE tool_receipts SET summary=?,data_json=?,evidence_json='[]' WHERE task_id=?",
                 (text, tombstone, task_id))
    for delivery in conn.execute("SELECT id,metadata_json FROM reply_outbox").fetchall():
        metadata = _load(delivery["metadata_json"], {})
        if metadata.get("analysis_task_id") == task_id:
            conn.execute("UPDATE reply_outbox SET params_json='{}',metadata_json='{}',result_json=NULL,error=NULL,"
                         "record_conversation=0,state=CASE WHEN state IN ('pending','sending') THEN 'failed' ELSE state END WHERE id=?",
                         (delivery["id"],))


def erase_sources(conn, *, notes=(), reminders=()):
    ids = affected(conn, notes=notes, reminders=reminders)
    for task_id in ids:
        _erase(conn, task_id)
    return len(ids)


def erase_message(conn, message_id):
    row = conn.execute("SELECT chat_id,note_no FROM messages WHERE id=?", (message_id,)).fetchone()
    if row is not None and row["note_no"] is not None:
        return erase_sources(conn, notes=[(row["chat_id"], row["note_no"])])
    return 0


def erase_scope(conn, scope, category=None):
    notes, reminders = _scope_sources(conn, scope, category)
    return erase_sources(conn, notes=notes, reminders=reminders)


def require_quiescence(conn, nonce):
    if not _tasks(conn):
        return
    value = _authorization(conn)
    hold = value.get("analysis_purge") or {}
    if (value.get("phase") != "prepared" or value.get("nonce") != nonce
            or hold.get("schema") != HOLD_SCHEMA or hold.get("state") != "settled"):
        raise ValueError("Analysis transports must settle before all-content deletion")


def begin_all_purge(agent, chat_id, lang, nonce, confirmed, *, in_update=True):
    """Durable hold before cancellation; old empty installations are unchanged."""
    import analysis_engine
    import store
    import tasking
    if not _tasks(agent.conn) and not getattr(agent, "_analysis_calls", {}):
        return True
    value = _authorization(agent.conn)
    if value.get("phase") != "prepared" or value.get("nonce") != nonce:
        raise ValueError("Analysis purge requires the existing typed authorization")
    source_update = getattr(agent, "_current_update_id", None)
    source = (store.assistant_task_source(agent.conn, chat_id, source_update)
              if source_update is not None else None)
    if source_update is not None and source is None:
        raise ValueError("Analysis purge confirmation has no owner source")
    value["analysis_purge"] = {"schema": HOLD_SCHEMA, "state": "waiting",
                               "chat_id": int(chat_id), "lang": lang,
                               "confirmed": dict(confirmed),
                               "source_update": source_update,
                               "source_hash": tasking.source_hash(source["text"]) if source is not None else None,
                               "track_updates": getattr(agent, "_current_update_id", None) is not None}
    store.kv_set(agent.conn, "task_purge_authorization", _json(value))
    if not analysis_engine.prepare_purge(agent, agent.conn):
        return False
    value = _authorization(agent.conn)
    value["analysis_purge"]["state"] = "settled"
    store.kv_set(agent.conn, "task_purge_authorization", _json(value))
    return True


def continue_all_purge(agent):
    """One nonblocking ordinary-loop continuation; no inbound update replay."""
    import analysis_engine
    import store
    import tasking
    from texts import T
    value = _authorization(agent.conn)
    hold = value.get("analysis_purge")
    if value.get("phase") != "prepared" or not isinstance(hold, dict) or hold.get("schema") != HOLD_SCHEMA:
        return False
    if hold.get("state") == "aborting":
        cancel_all_purge(agent)
        return True
    if hold.get("source_update") is not None:
        source = store.assistant_task_source(agent.conn, hold["chat_id"], hold["source_update"])
        if source is None or tasking.source_hash(source["text"]) != hold.get("source_hash"):
            cancel_all_purge(agent)
            return True
    if hold.get("state") == "awaiting_confirmation":
        pending = store.pending_get(agent.conn, hold["chat_id"])
        if not pending or pending["kind"] != "purge":
            cancel_all_purge(agent)
        return True
    if hold.get("state") == "settled" and value.get("fleet_phase"):
        return False  # Existing fleet continuation now owns the same hold.
    if not analysis_engine.prepare_purge(agent, agent.conn):
        return True
    hold["state"] = "settled"
    store.kv_set(agent.conn, "task_purge_authorization", _json(value))
    actual = agent._content_purge_preview("all")
    if actual != hold["confirmed"]:
        anticipated = dict(actual)
        if hold.get("track_updates"):
            anticipated["conversation"] = anticipated.get("conversation", 0) + 1
            anticipated["updates_scrubbed"] = anticipated.get("updates_scrubbed", 0) + 1
        phrase = T(hold["lang"], "purge_phrase_all")
        store.pending_set(agent.conn, hold["chat_id"], "purge", {
            "scope": "all", "category": None, "phrase": phrase,
            "anticipated": anticipated, "analysis_purge_nonce": value["nonce"]}, ttl_seconds=300)
        hold["state"] = "awaiting_confirmation"
        store.kv_set(agent.conn, "task_purge_authorization", _json(value))
        agent.reply(hold["chat_id"], T(hold["lang"], "purge_preview",
                    impact=agent._purge_impact_text(hold["lang"], anticipated), phrase=phrase), record=False)
        return True
    hook = getattr(agent, "prepare_fleet_purge", None)
    if callable(hook) and hook(hold["chat_id"], hold["lang"], value["nonce"], actual) != "ready":
        return True
    agent._complete_content_purge(hold["chat_id"], hold["lang"], "all", None, value["nonce"], scrub_update=False)
    return True


def cancel_all_purge(agent, nonce=None):
    """Release a cancelled local hold only after its existing transports stop."""
    import analysis_engine
    import store
    value = _authorization(agent.conn)
    if nonce is not None and value.get("nonce") != nonce:
        return False
    hold = value.get("analysis_purge")
    if not isinstance(hold, dict):
        return True
    hold["state"] = "aborting"
    store.kv_set(agent.conn, "task_purge_authorization", _json(value))
    if not analysis_engine.prepare_purge(agent, agent.conn):
        return False
    value.pop("analysis_purge", None)
    if value.get("fleet_phase") or value.get("fleet_quiescence"):
        store.kv_set(agent.conn, "task_purge_authorization", _json(value))
        cancel = getattr(agent, "cancel_fleet_purge", None)
        return bool(callable(cancel) and cancel(value["nonce"]))
    store.kv_set(agent.conn, "task_purge_authorization", "")
    return True
