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
from datetime import datetime, timedelta, timezone

import store

# Same backoff jobs.py uses, for the same reason it had to learn it: without
# moving `available_at`, both attempts burn inside the SAME drain pass (the row
# is claimable again immediately), so one network blip spends the whole retry
# budget in a second. `claim_next` already filters on `available_at <= ?`.
RETRY_DELAY_SECONDS = 600

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


def reclaim_stale(conn):
    """Recover events left 'claimed' by a crash mid-run (called at startup).

    Mirrors jobs.reclaim_stale exactly — same reason: a claimed row belongs to
    the single in-process runner, and after a restart nobody owns it. claim_next
    only selects 'pending', so without this the row is stuck forever. Requeue
    while retry budget remains (attempts were counted at claim time), terminally
    fail the rest. Returns (requeued, failed).

    Inert today by construction — Stage A records events, it does not claim them
    — which is precisely why it has to exist BEFORE Stage C moves live dispatch
    here, rather than being discovered by a wedged queue afterwards.
    """
    requeued = conn.execute(
        "UPDATE events SET status = 'pending' WHERE status = 'claimed'"
        " AND attempts < max_attempts").rowcount
    failed = conn.execute(
        "UPDATE events SET status = 'failed', finished_at = ?,"
        " error = 'reclaimed: process died mid-run' WHERE status = 'claimed'",
        (_now(),)).rowcount
    conn.commit()
    return requeued, failed


def claim_next(conn, now_iso=None):
    """Claim the highest-priority due, retryable pending event. Returns the
    row dict or None.

    The dict describes the row AFTER the claim: it was built from the SELECT,
    so it reported `status='pending'` and the pre-increment `attempts` — a
    caller deciding "is this the last attempt?" from it was off by one.
    `jobs.claim_next` carries the same patch, so the two queues agree; see it
    for why the claiming UPDATE's rowcount is not checked.
    """
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
    claimed = dict(row)
    claimed.update(status="claimed", claimed_at=now_iso,
                   attempts=(row["attempts"] or 0) + 1)
    return claimed


def complete(conn, event_id, status="done", error=None):
    conn.execute(
        "UPDATE events SET status = ?, finished_at = ?, error = ? WHERE id = ?",
        (status, _now(), error, event_id),
    )
    conn.commit()


def fail(conn, event_id, error=None, retry_delay_seconds=RETRY_DELAY_SECONDS):
    """Mark a claimed event failed; return it to pending (after a backoff) if
    attempts remain, else terminal 'failed'. Returns True when terminal.

    jobs.fail's shape, including the part that matters most: the retry moves
    `available_at` forward. Without it a retry is claimable again in the same
    pass, so a single blip spends the entire retry budget instantly — jobs.py
    paid for that lesson and documents it; landing the twin without it would be
    a parity fix that leaves the expensive half undone.

    `error` is recorded either way — the row already HAD the column and leaving
    it NULL meant a failed event said only "failed", the reason nowhere in the
    database. It stays OPTIONAL here (jobs.fail requires it) because the ONLY
    thing that ever records an event failure today is a `COALESCE`-preserving
    retry: a second, reason-less failure must not blank the first one's reason.
    """
    row = conn.execute("SELECT attempts, max_attempts FROM events WHERE id = ?",
                       (event_id,)).fetchone()
    detail = str(error)[:300] if error is not None else None
    if row and row["attempts"] < row["max_attempts"]:
        retry_at = (datetime.now(timezone.utc)
                    + timedelta(seconds=retry_delay_seconds)).isoformat()
        conn.execute("UPDATE events SET status = 'pending', error = COALESCE(?, error),"
                     " available_at = ? WHERE id = ?", (detail, retry_at, event_id))
        terminal = False
    else:
        conn.execute("UPDATE events SET status = 'failed', finished_at = ?,"
                     " error = COALESCE(?, error) WHERE id = ?",
                     (_now(), detail, event_id))
        terminal = True
    conn.commit()
    return terminal
