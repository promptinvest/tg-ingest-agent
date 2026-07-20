#!/usr/bin/env python3
"""Persistent work events — an audit + queue primitive.

Single-process, single-threaded poll loop, so no distributed locking: claim
is a plain SELECT-then-UPDATE. Timestamps are ISO-8601 UTC strings (Cara's
convention); ISO UTC compares correctly with <= for "due" queries.

Stage A of the runtime rollout (spec §29.8): events are recorded for
observability and the claim/retry layer is tested; live dispatch is NOT yet
moved here (that is Stage C). jobs.py reuses the same claim pattern.
"""
import json
from datetime import datetime, timezone

import store

KINDS = (
    "telegram_message_received", "telegram_album_ready", "pending_action_reply",
    "reminder_due", "proactive_tick", "memory_curator_daily", "weekly_digest_due",
    "export_requested", "retry_failed_job",
)


def _now():
    return datetime.now(timezone.utc).isoformat()


def add_event(conn, kind, *, chat_id=None, payload=None, trace_id=None,
              available_at=None, priority=100, max_attempts=3, status="pending"):
    cur = conn.execute(
        "INSERT INTO events (trace_id, kind, status, priority, available_at, attempts,"
        " max_attempts, chat_id, payload, created_at) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?)",
        (trace_id, kind, status, priority, available_at or _now(), max_attempts, chat_id,
         json.dumps(payload, ensure_ascii=False) if payload is not None else None, _now()),
    )
    conn.commit()
    return cur.lastrowid


def record_done(conn, kind, *, chat_id=None, payload=None, trace_id=None, status="done", error=None):
    """Shorthand for an already-completed observability event."""
    eid = add_event(conn, kind, chat_id=chat_id, payload=payload, trace_id=trace_id, status=status)
    conn.execute("UPDATE events SET finished_at = ?, error = ? WHERE id = ?", (_now(), error, eid))
    conn.commit()
    if status == "done":
        store.note_outcomes_from_event(conn, eid)
    return eid


def claim_next(conn, now_iso=None):
    """Claim the highest-priority due, retryable pending event. Returns the
    row dict or None."""
    now_iso = now_iso or _now()
    row = conn.execute(
        "SELECT * FROM events WHERE status = 'pending' AND available_at <= ?"
        " AND attempts < max_attempts ORDER BY priority ASC, available_at ASC, id ASC LIMIT 1",
        (now_iso,),
    ).fetchone()
    if not row:
        return None
    conn.execute(
        "UPDATE events SET status = 'claimed', claimed_at = ?, attempts = attempts + 1"
        " WHERE id = ? AND status = 'pending'",
        (now_iso, row["id"]),
    )
    conn.commit()
    return dict(row)


def complete(conn, event_id, status="done", error=None):
    conn.execute(
        "UPDATE events SET status = ?, finished_at = ?, error = ? WHERE id = ?",
        (status, _now(), error, event_id),
    )
    conn.commit()


def fail(conn, event_id):
    """Mark a claimed event failed; return it to pending if attempts remain,
    else terminal 'failed'."""
    row = conn.execute("SELECT attempts, max_attempts FROM events WHERE id = ?",
                       (event_id,)).fetchone()
    if row and row["attempts"] < row["max_attempts"]:
        conn.execute("UPDATE events SET status = 'pending' WHERE id = ?", (event_id,))
    else:
        conn.execute("UPDATE events SET status = 'failed', finished_at = ? WHERE id = ?",
                     (_now(), event_id))
    conn.commit()
