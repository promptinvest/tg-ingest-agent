#!/usr/bin/env python3
"""Persistent work events — an audit + queue primitive.

Single-process, single-threaded poll loop, so no distributed locking: claim
is a plain SELECT-then-UPDATE. Timestamps are ISO-8601 UTC strings (Cara's
convention); ISO UTC compares correctly with <= for "due" queries.

Stage A of the runtime rollout (spec §29.8) records events for observability.
Stage C (live dispatch through this queue) was RETIRED 2026-09-08 (ADR-0019): the
claim/complete/fail/reclaim layer duplicated the durable inbox and nothing ever
claimed an event, so it is gone; `record_done` stays for the note-outcome mirror.
jobs.py keeps its own claim pattern.
"""
import json
from datetime import datetime, timedelta, timezone

import store

# Same backoff jobs.py uses, for the same reason it had to learn it: without
# moving `available_at`, both attempts burn inside the SAME drain pass (the row
# is claimable again immediately), so one network blip spends the whole retry
# budget in a second. `claim_next` already filters on `available_at <= ?`.

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
