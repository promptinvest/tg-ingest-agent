"""Local action receipts and delivery-only recovery; no historical send backfill.

An interrupted Telegram send is ambiguous, never automatically sent again.  Only
an explicit rejection known not to have delivered may enter the retry queue.
"""
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from functools import wraps
import hashlib
import json
import sqlite3
import uuid

import common
import inference_io
import store
from tg_api import TelegramError

# All application writes use canonical _json. Ignore ordinary JSON spacing as
# well, so valid fixture/operator rows retain the same eligibility. SQLite's
# builtin string functions keep the schema independent of the JSON1 extension.
CONVERSE_PENDING_SQL = (
    "state='sent' AND instr(replace(metadata_json,' ',''),'\"kind\":\"converse_reply\"')>0"
    " AND instr(replace(metadata_json,' ',''),'\"state\":\"pending\"')>0")

SCHEMA = """
CREATE TABLE IF NOT EXISTS action_receipts (
  action_key TEXT PRIMARY KEY,
  source_update INTEGER,
  action TEXT NOT NULL,
  result_json TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reply_outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  delivery_key TEXT NOT NULL UNIQUE,
  action_key TEXT,
  source_update INTEGER,
  method TEXT NOT NULL,
  params_json TEXT NOT NULL,
  record_conversation INTEGER NOT NULL DEFAULT 0,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  state TEXT NOT NULL CHECK(state IN ('pending','sending','sent','failed','ambiguous')),
  attempts INTEGER NOT NULL DEFAULT 0,
  available_at TEXT NOT NULL,
  result_json TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS reply_outbox_due ON reply_outbox(state,available_at,id);
""" + "CREATE INDEX IF NOT EXISTS reply_outbox_converse_pending ON reply_outbox(id) WHERE " + CONVERSE_PENDING_SQL + ";"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def _source_fingerprint(conn, source_update):
    if source_update is None:
        return None
    row = conn.execute("SELECT id,text FROM conversation WHERE update_id=? AND role='user' ORDER BY id DESC LIMIT 1", (source_update,)).fetchone()
    if row:
        return {"kind": "conversation", "id": row["id"], "hash": _digest(row["text"])}
    row = store.telegram_update_get(conn, source_update)
    return {"kind": "update", "id": source_update, "hash": _digest(row["payload"])} if row else None


def _source_current(conn, fingerprint):
    if fingerprint["kind"] == "conversation":
        row = conn.execute("SELECT text FROM conversation WHERE id=?", (fingerprint["id"],)).fetchone()
    else:
        row = conn.execute("SELECT payload FROM telegram_updates WHERE update_id=?", (fingerprint["id"],)).fetchone()
    return row is not None and _digest(row[0]) == fingerprint["hash"]


def _pending_fingerprint(conn, chat_id):
    row = conn.execute("SELECT * FROM pending_actions WHERE chat_id=?", (chat_id,)).fetchone()
    if row is None or row["expires_at"] < _now():
        return None
    return _digest(_json(dict(row)))


def scope_for_update(update_id, text=""):
    return "update:" + str(update_id) + ":" + hashlib.sha256(str(text).encode()).hexdigest()


def receipt(conn, key):
    return conn.execute("SELECT * FROM action_receipts WHERE action_key=?", (key,)).fetchone()


def reminder_delivery_unknown(conn, reminder_id, due_utc):
    for row in conn.execute("SELECT metadata_json FROM reply_outbox WHERE state IN ('sending','ambiguous') AND delivery_key LIKE 'reminder-fire:%'"):
        occurrences = json.loads(row["metadata_json"]).get("reminder_occurrences", [])
        if [reminder_id, due_utc] in occurrences:
            return True
    return False


def caller_finalized(fn):
    """A delivered prompt needs its caller to bind the returned message ID."""
    @wraps(fn)
    def run(agent, *args, **kwargs):
        previous = getattr(agent, "_reply_requires_caller", False)
        agent._reply_requires_caller = True
        try:
            return fn(agent, *args, **kwargs)
        finally:
            agent._reply_requires_caller = previous
    return run


def purge_control(fn):
    """Keep administrative delivery evidence outside its own purge impact."""
    @wraps(fn)
    def run(agent, *args, **kwargs):
        previous = getattr(agent, "_reply_delivery_metadata", None)
        agent._reply_delivery_metadata = {**(previous or {}), "purge_control": True}
        try:
            return fn(agent, *args, **kwargs)
        finally:
            agent._reply_delivery_metadata = previous
    return run


@contextmanager
def local_transaction(conn):
    """Contain existing helper commits in this narrow, network-free action only."""
    if not isinstance(conn, store._CachedConn):
        raise TypeError("action transactions require store.open_db")
    if getattr(conn, "_local_action_transaction", False) or conn.in_transaction:
        raise RuntimeError("action transaction cannot nest an unrelated transaction")
    conn.execute("BEGIN IMMEDIATE")
    conn._local_action_transaction = True
    try:
        yield
        sqlite3.Connection.commit(conn)
    except BaseException:
        sqlite3.Connection.rollback(conn)
        raise
    finally:
        conn._local_action_transaction = False


def mark_completed(agent):
    """A resolved target already satisfies the requested reminder outcome."""
    transaction = getattr(agent, "_reminder_transaction", None)
    if transaction is not None:
        transaction["completed"] = True


def reminder_action(predicate=None):
    """Commit reminder effect, consumed confirmation and outbound intent together."""
    def decorate(fn):
        @wraps(fn)
        def run(agent, *args, **kwargs):
            if predicate is not None and not predicate(agent, *args, **kwargs):
                return fn(agent, *args, **kwargs)
            if getattr(agent, "_reminder_transaction", None) is not None:
                return fn(agent, *args, **kwargs)
            key = getattr(agent, "_reminder_scope", None)
            if key is None:
                update_id = getattr(agent, "_active_update_id", None)
                key = scope_for_update(update_id, "callback") if update_id is not None else "direct:" + uuid.uuid4().hex
            old = receipt(agent.conn, key)
            if old:
                deliver_action(agent, key)
                return json.loads(old["result_json"] or "null")
            callbacks = []
            agent._reminder_transaction = {"key": key, "after_commit": callbacks, "send_sequence": 0}
            try:
                with local_transaction(agent.conn):
                    before_event = agent.conn.execute("SELECT COALESCE(MAX(id),0) FROM reminder_events").fetchone()[0]
                    result = fn(agent, *args, **kwargs)
                    has_effect = (agent._reminder_transaction.get("completed", False)
                                  or agent.conn.execute("SELECT COALESCE(MAX(id),0) FROM reminder_events").fetchone()[0] != before_event)
                    if not has_effect:
                        # Staging a question is not a completed reminder action.
                        # Delayed delivery needs its original caller and current
                        # card binding; never replay a stale clarification alone.
                        fingerprint = _source_fingerprint(agent.conn, getattr(agent, "_active_update_id", None))
                        for row in agent.conn.execute("SELECT id,metadata_json FROM reply_outbox WHERE action_key=?", (key,)).fetchall():
                            metadata = json.loads(row["metadata_json"])
                            metadata["caller_finalization"] = True
                            if fingerprint:
                                metadata["source_fingerprint"] = fingerprint
                            agent.conn.execute("UPDATE reply_outbox SET metadata_json=? WHERE id=?", (_json(metadata), row["id"]))
                    # A resolver returning False has declined this request.  Its
                    # ordinary fallthrough must remain live on restart too.
                    if result is not False and has_effect:
                        agent.conn.execute(
                            "INSERT INTO action_receipts(action_key,source_update,action,result_json,created_at) VALUES(?,?,?,?,?)",
                            (key, getattr(agent, "_active_update_id", None), fn.__name__, _json(result), _now()))
            finally:
                agent._reminder_transaction = None
            deliver_action(agent, key)
            for callback in callbacks:
                callback()
            return result
        return run
    return decorate


def enqueue(agent, method, params, *, record=False, history_text=None):
    tx = getattr(agent, "_reminder_transaction", None)
    source = getattr(agent, "_active_update_id", None)
    fixed = getattr(agent, "_reply_delivery_key", None)
    if fixed:
        key = fixed
    elif tx:
        count = tx["send_sequence"]
        tx["send_sequence"] += 1
        key = tx["key"] + ":send:" + str(count)
    elif source is not None:
        index = getattr(agent, "_reply_sequence", 0)
        agent._reply_sequence = index + 1
        key = "update:" + str(source) + ":reply:" + str(index)
    else:
        key = "send:" + uuid.uuid4().hex
    now = _now()
    metadata = dict(getattr(agent, "_reply_delivery_metadata", None) or {})
    if tx is None and source is not None:
        fingerprint = _source_fingerprint(agent.conn, source)
        if fingerprint:
            metadata["source_fingerprint"] = fingerprint
    if metadata.get("kind") != "reminder_expired" and (
            getattr(agent, "_reply_requires_caller", False)
            or (not tx and (params.get("reply_markup") is not None or not record))):
        # Without a typed delayed finalizer, a retry must be driven by the
        # original caller so its card identity/freshness checks still run.
        metadata["caller_finalization"] = True
    if tx and params.get("reply_markup") is not None and method == "sendMessage" and metadata.get("kind") != "reminder_expired":
        fingerprint = _pending_fingerprint(agent.conn, params.get("chat_id"))
        if fingerprint:
            metadata["pending_fingerprint"] = fingerprint
    if record and history_text is not None:
        metadata["history_text"] = history_text
    agent.conn.execute(
        "INSERT OR IGNORE INTO reply_outbox(delivery_key,action_key,source_update,method,params_json,record_conversation,metadata_json,state,available_at,created_at)"
        " VALUES(?,?,?,?,?,?,?,'pending',?,?)",
        (key, tx["key"] if tx else None, source, method, _json(params), int(record),
         _json(metadata), now, now))
    agent.conn.commit()
    return agent.conn.execute("SELECT * FROM reply_outbox WHERE delivery_key=?", (key,)).fetchone()


def _record_sent(conn, row, result):
    """Delivery bookkeeping is atomic; never replay the network for bookkeeping."""
    with local_transaction(conn):
        conn.execute("UPDATE reply_outbox SET state='sent',result_json=?,error=NULL,finished_at=? WHERE id=?",
                     (_json(result), _now(), row["id"]))
        params = json.loads(row["params_json"])
        metadata = json.loads(row["metadata_json"])
        if row["record_conversation"]:
            # convo_add commits, so use the same insert shape without a nested
            # commit: the acknowledgement and its history either both land.
            source = metadata.get("conversation_source")
            if not isinstance(source, str) or source not in store.CONVO_OPERATIONAL_SOURCES:
                source = "boss"
            store.convo_add(conn, params["chat_id"], "bot", metadata.get("history_text", params["text"]),
                            source=source)
        if metadata.get("kind") == "reminder_expired" and store.pending_get(conn, params["chat_id"]) is None:
            store.pending_set(conn, params["chat_id"], "reminder_expired", metadata["payload"], ttl_seconds=24 * 3600)


def _finalize_delivered(agent, row, *, delayed=False):
    """Owner-thread post-delivery work; never part of the local send transaction.

    Curation keeps its existing profile, budget, source evidence and throttle.
    Claim before invoking it: interruption can miss learning, but never repeats
    an ambiguous paid extraction or its state effects automatically.
    """
    row = agent.conn.execute("SELECT * FROM reply_outbox WHERE id=?", (row["id"],)).fetchone()
    if row is None or row["state"] != "sent":
        return
    metadata = json.loads(row["metadata_json"])
    if metadata.get("kind") != "converse_reply":
        return
    # This counter only preserves the public mocked/nonstandard reply seam; it
    # stores no content and never determines durable curation authority.
    agent._converse_delivery_finalization_epoch = getattr(
        agent, "_converse_delivery_finalization_epoch", 0) + 1
    post = metadata.get("converse") or {}
    if post.get("state") != "pending":
        return
    fingerprint = metadata.get("source_fingerprint")
    current = _source_current(agent.conn, fingerprint) if fingerprint else not delayed
    current = current and post.get("source_hash") == _digest(post.get("source_text"))
    post["state"] = "claimed" if current else "source_unavailable"
    metadata["converse"] = post
    agent.conn.execute("UPDATE reply_outbox SET metadata_json=? WHERE id=? AND state='sent'",
                       (_json(metadata), row["id"]))
    agent.conn.commit()
    if not current:
        return
    fields = ("turn_lang", "_active_update_id", "_reminder_scope", "_reply_delivery_metadata",
              "_reply_requires_caller", "_reply_sequence")
    missing = object()
    previous = {name: getattr(agent, name, missing) for name in fields}
    try:
        agent.turn_lang = post["lang"]
        agent._active_update_id = None
        agent._reminder_scope = None
        agent._reply_delivery_metadata = None
        agent._reply_requires_caller = False
        agent._reply_sequence = 0
        heartbeat = getattr(agent, "_inference_wait_tick", None)
        waiting = inference_io.on_wait(heartbeat) if callable(heartbeat) else nullcontext()
        with waiting:
            agent.maybe_curate_conversation(post["chat_id"], lang=post["lang"],
                                            force=bool(post.get("correction")))
        post["state"] = "done"
        metadata["converse"] = post
        agent.conn.execute("UPDATE reply_outbox SET metadata_json=? WHERE id=? AND state='sent'",
                           (_json(metadata), row["id"]))
        agent.conn.commit()
    finally:
        for name, value in previous.items():
            if value is missing:
                if hasattr(agent, name):
                    delattr(agent, name)
            else:
                setattr(agent, name, value)


def deliver(agent, row, sender):
    row = agent.conn.execute("SELECT * FROM reply_outbox WHERE id=?", (row["id"],)).fetchone()
    owed = getattr(agent, "_reply_stamp_owed", {})
    if row["id"] in owed:
        original, result = owed[row["id"]]
        if row["state"] != "sending" or row["params_json"] != original["params_json"]:
            del owed[row["id"]]
            return None
        _record_sent(agent.conn, original, result)
        del owed[row["id"]]
        _finalize_delivered(agent, original, delayed=True)
        return result
    if row["state"] == "sent":
        _finalize_delivered(agent, row, delayed=True)
        return json.loads(row["result_json"] or "null")
    if row["state"] != "pending" or row["available_at"] > _now():
        return None
    metadata = json.loads(row["metadata_json"])
    if metadata.get("pending_fingerprint"):
        current = _pending_fingerprint(agent.conn, json.loads(row["params_json"])["chat_id"])
        if current != metadata["pending_fingerprint"]:
            agent.conn.execute("UPDATE reply_outbox SET state='failed',error='confirmation changed before delivery',finished_at=? WHERE id=?", (_now(), row["id"]))
            agent.conn.commit()
            return None
    if metadata.get("source_fingerprint") and not _source_current(agent.conn, metadata["source_fingerprint"]):
        agent.conn.execute("UPDATE reply_outbox SET state='failed',error='source changed before delivery',finished_at=? WHERE id=?", (_now(), row["id"]))
        agent.conn.commit()
        return None
    if metadata.get("kind") == "reminder_expired":
        ids = metadata["payload"]["reminder_ids"]
        if any((current := store.reminder_get(agent.conn, rid)) is None or current["status"] != "expired" for rid in ids):
            agent.conn.execute("UPDATE reply_outbox SET state='failed',error='expiry announcement superseded',finished_at=? WHERE id=?", (_now(), row["id"]))
            agent.conn.commit()
            return None
    agent.conn.execute("UPDATE reply_outbox SET state='sending',attempts=attempts+1 WHERE id=? AND state='pending'", (row["id"],))
    agent.conn.commit()  # durable before the first possible network byte
    try:
        result = sender(agent.cfg.token, row["method"], json.loads(row["params_json"]))
    except TelegramError as exc:
        ambiguous = bool(exc.outcome_unknown)
        retry = not ambiguous and (exc.status in (409, 429) or exc.status is None or (exc.status or 0) >= 500)
        state = "ambiguous" if ambiguous else "pending" if retry and row["attempts"] < 4 else "failed"
        delay = max(30 * 2 ** min(row["attempts"], 5), min(3600, int(exc.retry_after or 0)))
        agent.conn.execute("UPDATE reply_outbox SET state=?,error=?,available_at=?,finished_at=? WHERE id=?",
                           (state, str(exc)[:500], (datetime.now(timezone.utc)+timedelta(seconds=delay)).isoformat(),
                            None if state == "pending" else _now(), row["id"]))
        agent.conn.commit()
        common.log(f"{row['method']} delivery #{row['id']}: {state}: {exc}")
        if ambiguous and not metadata.get("purge_control"):
            store.issue_add(agent.conn, json.loads(row["params_json"]).get("chat_id"), "reply_delivery_ambiguous",
                            f"delivery #{row['id']}: Telegram outcome unknown; no automatic resend")
        return None
    try:
        _record_sent(agent.conn, row, result)
    except sqlite3.Error as exc:
        # We know Telegram acknowledged this send. A local write outage owes
        # only bookkeeping, never another network attempt while this process
        # survives. A crash leaves durable 'sending' explicitly ambiguous.
        if not hasattr(agent, "_reply_stamp_owed"):
            agent._reply_stamp_owed = {}
        agent._reply_stamp_owed[row["id"]] = (dict(row), result)
        exc.reply_network_acknowledged = True
        raise
    try:
        _finalize_delivered(agent, row, delayed=row["attempts"] > 0)
    except sqlite3.Error as exc:
        exc.reply_network_acknowledged = True
        raise
    return result


def send(agent, method, params, sender, *, record=False, history_text=None):
    try:
        row = enqueue(agent, method, params, record=record, history_text=history_text)
        if getattr(agent, "_reminder_transaction", None) is not None:
            return None
        return deliver(agent, row, sender)
    except sqlite3.Error as exc:
        # Enqueuing now precedes transport. The alarm sweep must distinguish a
        # failed pre-send write from its original delivered-but-unstamped case.
        if not getattr(exc, "reply_network_acknowledged", False):
            exc.reply_request_not_sent = True
        raise


def deliver_action(agent, key):
    # The entry point is installed as agent.py and may run as __main__. Resolve
    # its live transport through the instance, preserving the patchable seam
    # without importing the source filename or creating a second entry module.
    for row in agent.conn.execute("SELECT * FROM reply_outbox WHERE action_key=? ORDER BY id", (key,)).fetchall():
        deliver(agent, row, agent._action_delivery_sender())


def recover(conn):
    rows = conn.execute("SELECT id,params_json,metadata_json FROM reply_outbox WHERE state='sending'").fetchall()
    with conn:
        conn.execute("UPDATE reply_outbox SET state='ambiguous',error='process stopped during send; no automatic resend',finished_at=? WHERE state='sending'", (_now(),))
    for row in rows:
        if json.loads(row["metadata_json"]).get("purge_control"):
            continue
        store.issue_add(conn, json.loads(row["params_json"]).get("chat_id"), "reply_delivery_ambiguous",
                        f"delivery #{row['id']}: interrupted send; no automatic resend")


def drain(agent, sender, *, limit=1):
    for delivery_id, (row, result) in list(getattr(agent, "_reply_stamp_owed", {}).items()):
        current = agent.conn.execute("SELECT state,params_json FROM reply_outbox WHERE id=?", (delivery_id,)).fetchone()
        if current and current["state"] == "sending" and current["params_json"] == row["params_json"]:
            _record_sent(agent.conn, row, result)
            _finalize_delivered(agent, row, delayed=True)
        del agent._reply_stamp_owed[delivery_id]
    # An acknowledged send can precede a DB outage while claiming curation.
    # Only still-pending typed finalizers resume; claimed ones never auto-run.
    sent_rows = agent.conn.execute("SELECT * FROM reply_outbox WHERE " + CONVERSE_PENDING_SQL + " ORDER BY id LIMIT 1")
    for row in sent_rows:
        metadata = json.loads(row["metadata_json"])
        if (metadata.get("kind") == "converse_reply"
                and (metadata.get("converse") or {}).get("state") == "pending"):
            _finalize_delivered(agent, row, delayed=True)
            break  # one eligible finalizer per tick; unrelated rows do not starve it
    rows = agent.conn.execute("SELECT * FROM reply_outbox WHERE state='pending' AND available_at<=? ORDER BY id", (_now(),))
    attempted = 0
    for row in rows:
        if attempted >= limit:
            break
        if json.loads(row["metadata_json"]).get("caller_finalization"):
            continue
        deliver(agent, row, sender)
        current = agent.conn.execute("SELECT attempts FROM reply_outbox WHERE id=?", (row["id"],)).fetchone()
        if current and current["attempts"] > row["attempts"]:
            attempted += 1
    return attempted
