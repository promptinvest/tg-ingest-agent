#!/usr/bin/env python3
"""Persistent skill jobs — durable, retryable units of skill work.

Same single-process claim pattern as events.py. A job is claimed, run by a
registered handler, then completed or failed (with retry up to max_attempts).
First real use is the daily memory-curator job (Phase C); the runtime drain
is inert until handlers are registered, so this ships without changing live
behavior.
"""
import json
from datetime import datetime, timezone

import store

# Known durable background jobs (skill, action). Documentation + a test guard
# that every one has a registered handler. The live request/response path is
# deliberately NOT a job — it stays synchronous (P0.4 background-only).
JOB_KINDS = (
    ("memory_curator", "run_memory_curator"),  # daily memory curation
    ("relationship", "run_reflection"),        # daily relationship-storyline reflection
    ("maintenance", "retry_sweep"),            # reprocess pending ingests
    ("maintenance", "media_cleanup"),          # prune orphan media / old exports
    ("maintenance", "pending_expire"),         # drop abandoned pending actions
)


def _now():
    return datetime.now(timezone.utc).isoformat()


def add_job(conn, skill, action, *, chat_id=None, payload=None, trace_id=None,
            event_id=None, available_at=None, priority=100, max_attempts=2):
    cur = conn.execute(
        "INSERT INTO jobs (trace_id, event_id, skill, action, status, priority, available_at,"
        " attempts, max_attempts, chat_id, payload, created_at)"
        " VALUES (?, ?, ?, ?, 'pending', ?, ?, 0, ?, ?, ?, ?)",
        (trace_id, event_id, skill, action, priority, available_at or _now(), max_attempts,
         chat_id, json.dumps(payload, ensure_ascii=False) if payload is not None else None, _now()),
    )
    conn.commit()
    return cur.lastrowid


def has_pending(conn, skill, action):
    return conn.execute(
        "SELECT 1 FROM jobs WHERE skill = ? AND action = ? AND status IN ('pending', 'claimed')"
        " LIMIT 1",
        (skill, action),
    ).fetchone() is not None


def reclaim_stale(conn):
    """Recover jobs left 'claimed' by a crash mid-run (called at startup).

    A claimed row belongs to the single in-process runner; after a restart no
    runner owns it. Without this, the row stays 'claimed' forever: claim_next
    only selects 'pending', and has_pending() counts the zombie — so that job
    kind is never enqueued again (the 'durable jobs survive restart' promise
    silently broken). Requeue while retry budget remains (attempts were already
    counted at claim); terminally fail the rest."""
    requeued = conn.execute(
        "UPDATE jobs SET status = 'pending' WHERE status = 'claimed'"
        " AND attempts < max_attempts").rowcount
    failed = conn.execute(
        "UPDATE jobs SET status = 'failed', finished_at = ?,"
        " error = 'reclaimed: process died mid-run' WHERE status = 'claimed'",
        (_now(),)).rowcount
    conn.commit()
    return requeued, failed


def claim_next(conn, now_iso=None):
    now_iso = now_iso or _now()
    row = conn.execute(
        "SELECT * FROM jobs WHERE status = 'pending' AND available_at <= ?"
        " AND attempts < max_attempts ORDER BY priority ASC, available_at ASC, id ASC LIMIT 1",
        (now_iso,),
    ).fetchone()
    if not row:
        return None
    conn.execute(
        "UPDATE jobs SET status = 'claimed', claimed_at = ?, attempts = attempts + 1"
        " WHERE id = ? AND status = 'pending'",
        (now_iso, row["id"]),
    )
    conn.commit()
    return dict(row)


def complete(conn, job_id, result=None):
    conn.execute(
        "UPDATE jobs SET status = 'done', finished_at = ?, result = ? WHERE id = ?",
        (_now(), json.dumps(result, ensure_ascii=False) if result is not None else None, job_id),
    )
    conn.commit()


def fail(conn, job_id, error):
    """Retry if attempts remain, else terminal 'failed'."""
    row = conn.execute("SELECT attempts, max_attempts FROM jobs WHERE id = ?",
                       (job_id,)).fetchone()
    if row and row["attempts"] < row["max_attempts"]:
        conn.execute("UPDATE jobs SET status = 'pending', error = ? WHERE id = ?",
                     (str(error)[:300], job_id))
        terminal = False
    else:
        conn.execute("UPDATE jobs SET status = 'failed', finished_at = ?, error = ? WHERE id = ?",
                     (_now(), str(error)[:300], job_id))
        terminal = True
    conn.commit()
    return terminal


def payload_of(job):
    raw = job.get("payload")
    try:
        return json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
