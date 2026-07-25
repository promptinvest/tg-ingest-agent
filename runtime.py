#!/usr/bin/env python3
"""Job runner: drains due jobs to registered handlers on the poll-loop tick.

Handlers register via @handler("skill", "action"). Inert until something
registers (no behavior change at ship). Each drained job runs under its own
trace; failures retry per jobs.fail and are logged as issues when terminal.
"""
import common
import jobs
import store
import trace as tracing
from common import log

_HANDLERS = {}


def handler(skill, action):
    def deco(fn):
        _HANDLERS[(skill, action)] = fn
        return fn
    return deco


def register(skill, action, fn):
    _HANDLERS[(skill, action)] = fn


def drain(conn, ctx, *, max_jobs=5):
    """Run up to max_jobs due jobs. ctx is passed to handlers (the Agent).
    Returns the number of jobs processed.

    One drain runs up to max_jobs jobs on the poll loop's only thread, and a single
    job (the memory consolidation, the encrypted backup) can be minutes long — so
    the systemd watchdog is pinged BETWEEN jobs; the whole drain is never one
    un-pinged span."""
    processed = 0
    while processed < max_jobs:
        common.watchdog_ping()
        job = jobs.claim_next(conn)
        if not job:
            break
        processed += 1
        fn = _HANDLERS.get((job["skill"], job["action"]))
        tid = tracing.start(conn, "job", job.get("chat_id"), prefix="job")
        if fn is None:
            log(f"no handler for job {job['skill']}/{job['action']} #{job['id']}")
            jobs.fail(conn, job["id"], "no handler registered")
            tracing.finish(conn, tid, "failed", "no handler")
            continue
        try:
            result = fn(ctx, conn, jobs.payload_of(job), job)
            jobs.complete(conn, job["id"], result if isinstance(result, dict) else None)
            tracing.finish(conn, tid, "ok")
        except Exception as exc:  # noqa: BLE001 — a bad job must not kill the loop
            terminal = jobs.fail(conn, job["id"], repr(exc))
            log(f"job {job['skill']}/{job['action']} #{job['id']} failed: {exc!r}"
                + (" (terminal)" if terminal else " (will retry)"))
            if terminal:
                store.issue_add(conn, job.get("chat_id"), "job_failed",
                                f"{job['skill']}/{job['action']}: {exc}")
            tracing.finish(conn, tid, "failed", repr(exc)[:200])
    return processed
