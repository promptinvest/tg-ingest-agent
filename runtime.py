#!/usr/bin/env python3
"""Job runner: drains due jobs to registered handlers on the poll-loop tick.

Handlers register via @handler("skill", "action"). Inert until something
registers (no behavior change at ship). Each drained job runs under its own
trace; failures retry per jobs.fail and are logged as issues when terminal.
"""
import common
import json
import queue
import threading
import time
from datetime import datetime, timezone
import jobs
import store
import trace as tracing
from common import log

_HANDLERS = {}
_BACKGROUND_SLOT = threading.BoundedSemaphore(1)


class PendingJob:
    """Owner-thread session returned by a handler instead of a blocking result."""

    done = False
    result = None
    error = None

    def advance(self):
        raise NotImplementedError


class BackgroundJob(PendingJob):
    """One connection-free file/transport worker; settlement stays on its owner.

    There is no worker queue: even a stuck transport consumes at most one daemon
    thread, and later jobs wait without creating threads or touching files. Work
    must receive plain captured inputs, never the poll loop's SQLite connection.
    Existing transport timeouts and backup bounds remain in force.
    """

    def __init__(self, work, *, finish=None, failed=None):
        self.work, self.finish, self.failed = work, finish, failed
        self.owner_thread = threading.get_ident()
        self.outcome = queue.Queue(maxsize=1)
        self.started = False
        self.done, self.result, self.error = False, None, None

    def _run(self):
        try:
            outcome = (True, self.work())
        except Exception as exc:
            outcome = (False, exc)
        finally:
            _BACKGROUND_SLOT.release()
        self.outcome.put(outcome)

    def advance(self):
        if threading.get_ident() != self.owner_thread:
            raise RuntimeError("background job settlement requires the owner thread")
        if self.done:
            return True
        if not self.started:
            if not _BACKGROUND_SLOT.acquire(blocking=False):
                return False
            self.started = True
            try:
                threading.Thread(target=self._run, name="cara-maintenance", daemon=True).start()
            except Exception as exc:
                _BACKGROUND_SLOT.release()
                self.done, self.error = True, exc
                return True
        try:
            success, value = self.outcome.get_nowait()
        except queue.Empty:
            return False
        # Callbacks cannot be replayed if a later SQLite operation fails. The
        # durable job remains claimed and normal startup recovery owns that case.
        self.done = True
        try:
            if success:
                self.result = self.finish(value) if self.finish else value
            else:
                if self.failed:
                    self.failed(value)
                self.error = value
        except Exception as exc:
            self.error = exc
        return True


class StaleBackgroundJob(RuntimeError):
    """The durable job no longer authorizes this inference phase."""

    background_no_retry = True


class InferenceJob(PendingJob):
    """Drive yielded profile requests with policy and accounting on the owner.

    The outer generator owns memory reads/writes. Only transport_chat receives
    prepared plain inputs in the bounded worker. Durable phase markers prevent
    unknown paid attempts being automatically repeated after a process restart.
    """

    def __init__(self, cfg, conn, steps, *, finish=None):
        self.cfg, self.conn, self.steps, self.finish = cfg, conn, steps, finish
        self.owner_thread = threading.get_ident()
        self.job_id = None
        self.done, self.result, self.error = False, None, None
        self.request = self.profile = self.operation = None
        self.worker = self.prepared = None
        self.retry_at = None
        self.operation_deadline = None
        self.started = False
        self.unsent_reservation = None
        self.unsent_needs_rollback = False

    def _phase(self, phase):
        if self.job_id is None:
            raise StaleBackgroundJob("inference maintenance requires a durable job binding")
        marker = {"background_inference": phase,
                  "reservation_id": self.prepared["reserved"]}
        changed = self.conn.execute("UPDATE jobs SET result=? WHERE id=? AND status='claimed'",
                                    (json.dumps(marker), self.job_id)).rowcount
        if changed != 1:
            self.conn.rollback()
            raise StaleBackgroundJob("inference maintenance job is no longer claimed")
        self.conn.commit()

    def _settle_unsent(self):
        if self.unsent_reservation is not None:
            import call_budget
            if self.unsent_needs_rollback:
                self.conn.rollback()
                self.unsent_needs_rollback = False
            call_budget.settle(self.conn, self.unsent_reservation, "rejected_chat")
            self.unsent_reservation = None

    def _settled_phase(self):
        try:
            self._phase("settled")
        except StaleBackgroundJob as exc:
            self.worker = None
            self.done, self.error = True, exc
            return False
        # SQLite errors keep this exact consumed outcome for idempotent retry.
        self.worker = None
        return True

    def _outer(self, value=None, error=None):
        try:
            if not self.started:
                self.started = True
                self.request = next(self.steps)
            elif error is not None:
                self.request = self.steps.throw(error)
            else:
                self.request = self.steps.send(value)
            self.profile = self.operation = None
        except StopIteration as finished:
            self.done = True
            try:
                self.result = self.finish(finished.value) if self.finish else finished.value
            except Exception as exc:
                self.error = exc
        except Exception as exc:
            self.done, self.error = True, exc

    def _profile(self, value=None, error=None, *, first=False):
        try:
            self.operation = (next(self.profile) if first else
                              self.profile.throw(error) if error is not None else
                              self.profile.send(value))
            self.operation_deadline = (time.monotonic() + float(self.operation["options"]["timeout"])
                                       if self.operation["kind"] == "chat" else None)
        except StopIteration as finished:
            self._outer(finished.value)
        except Exception as exc:
            self._outer(error=exc)

    def advance(self):
        import llm
        import inference_io
        if threading.get_ident() != self.owner_thread:
            raise RuntimeError("inference maintenance requires the owner thread")
        self._settle_unsent()
        if self.done:
            return True
        if self.worker is not None:
            if not self.worker.advance():
                return False
            worker, prepared = self.worker, self.prepared
            try:
                value = llm.finish_chat(self.cfg, self.conn, prepared,
                                        data=worker.result, error=worker.error,
                                        meta=self.operation["options"]["meta"])
            except llm.LLMError as exc:
                if getattr(exc, "usage_unknown", False):
                    self.worker = None
                    self.done, self.error = True, exc
                    return True
                if not self._settled_phase():
                    return True
                self._profile(error=exc)
            else:
                if not self._settled_phase():
                    return True
                self._profile(value)
            if self.done:
                return True
        if not self.started:
            self._outer()
            if self.done:
                return True
        lease = None
        if self.profile is None:
            # Initial background backpressure spends neither money nor the
            # profile's unchanged 90-second execution budget.
            lease = inference_io.BackgroundLease()
            if not lease.acquire():
                return False
            self.profile = llm.profile_steps(
                self.cfg, self.conn, "memory_curator", self.request["messages"],
                profile=self.request["profile"], retry_unknown=False)
            self._profile(first=True)
            if self.done or self.profile is None:
                lease.release_if_idle()
                return self.done
        if self.operation["kind"] == "delay":
            if lease is not None:
                lease.release_if_idle()
            if self.retry_at is None:
                self.retry_at = time.monotonic() + self.operation["seconds"]
            if time.monotonic() < self.retry_at:
                return False
            self.retry_at = None
            self._profile()
            return self.done
        options = dict(self.operation["options"])
        meta = options.pop("meta")
        if lease is None:
            lease = inference_io.BackgroundLease()
            if not lease.acquire():
                return False
        remaining = self.operation_deadline - time.monotonic()
        if remaining <= 0:
            lease.release_if_idle()
            error = llm.LLMError("turn deadline exceeded before background dispatch", transient=True)
            error.request_not_sent = True
            # Local queue expiry is not a provider/model failure. End this
            # unsent session without benching models or resetting profile time.
            self.done, self.error = True, error
            return True
        # Retry/failover options were yielded under the same profile budget.
        # Time waiting for shared capacity cannot become a fresh full timeout.
        options["timeout"] = min(float(options["timeout"]), remaining)
        try:
            try:
                self.prepared = llm.prepare_chat(
                    self.cfg, self.conn, "memory_curator", self.request["messages"], meta=meta, **options)
            except llm.LLMError as exc:
                lease.release_if_idle()
                self._profile(error=exc)
                return self.done
            try:
                self._phase("started")
            except Exception as exc:
                # No transport can precede this durable binding. Contain the
                # session BEFORE cleanup: even another SQLite outage must never
                # return to prepare_chat and reserve or dispatch a second call.
                lease.release_if_idle()
                exc.background_no_retry = True
                self.done, self.error = True, exc
                self.unsent_reservation = self.prepared["reserved"]
                self.unsent_needs_rollback = True
                self._settle_unsent()
                return True
            prepared = self.prepared
            self.worker = BackgroundJob(lambda: lease.run(llm.transport_chat, prepared))
            self.worker.advance()
            if self.worker.done and self.worker.error is not None:
                lease.release_if_idle()
                error = llm.LLMError("background transport did not start", transient=True)
                error.request_not_sent = True
                self.worker.error = error
        except Exception:
            lease.release_if_idle()
            raise
        if self.worker is None:
            lease.release_if_idle()
        return False


def has_pending(ctx):
    return bool(getattr(ctx, "_runtime_pending_job", None))


def _failed(conn, job, tid, exc):
    if getattr(exc, "usage_unknown", False) or getattr(exc, "background_no_retry", False):
        reason = ("background inference outcome unknown; automatic retry withheld"
                  if getattr(exc, "usage_unknown", False) else
                  "background inference phase binding failed; request not repeated")
        conn.execute("UPDATE jobs SET status='failed', finished_at=?, error=? WHERE id=?",
                     (datetime.now(timezone.utc).isoformat(),
                      reason, job["id"]))
        conn.commit()
        terminal = True
    else:
        terminal = jobs.fail(conn, job["id"], repr(exc))
    log(f"job {job['skill']}/{job['action']} #{job['id']} failed: {exc!r}"
        + (" (terminal)" if terminal else " (will retry)"),
        level="err" if terminal else "warning")
    if terminal:
        store.issue_add(conn, job.get("chat_id"), "job_failed",
                        f"{job['skill']}/{job['action']}: {exc}")
    tracing.finish(conn, tid, "failed", repr(exc)[:200])


def reclaim_stale(conn):
    """Contain dispatched inference with unknown outcomes before ordinary recovery."""
    contained = 0
    for row in conn.execute("SELECT id,result FROM jobs WHERE status='claimed'").fetchall():
        try:
            marker = json.loads(row["result"] or "null")
        except (TypeError, ValueError):
            continue
        if isinstance(marker, dict) and marker.get("background_inference") == "started":
            conn.execute("UPDATE jobs SET status='failed',finished_at=?,error=? WHERE id=?",
                         (datetime.now(timezone.utc).isoformat(),
                          "background inference interrupted; usage unknown; automatic retry withheld", row["id"]))
            contained += 1
    conn.commit()
    requeued, failed = jobs.reclaim_stale(conn)
    return requeued, failed + contained


def poll(conn, ctx):
    """Advance one background job without waiting, applying results on this thread."""
    pending = getattr(ctx, "_runtime_pending_job", None)
    if pending is None:
        return 0
    job, tid, session = pending
    previous_trace = common.current_trace()
    common.set_current_trace(tid)
    try:
        if not session.advance():
            return 0
        # Keep the consumed session until durable settlement succeeds, so a
        # transient SQLite error cannot re-dispatch already-finished work.
        if session.error is not None:
            _failed(conn, job, tid, session.error)
        else:
            jobs.complete(conn, job["id"], session.result if isinstance(session.result, dict) else None)
            tracing.finish(conn, tid, "ok")
        ctx._runtime_pending_job = None
        return 1
    finally:
        common.set_current_trace(previous_trace)


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

    Synchronous handlers keep their existing behavior. A PendingJob is pumped
    separately on every poll tick, with all persistence on the creating thread.
    While it is active no second maintenance job is claimed, preserving backup
    retention/restore serialization and preventing an unbounded work queue."""
    poll(conn, ctx)
    if has_pending(ctx):
        return 0
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
            if isinstance(result, PendingJob):
                if ctx is None:
                    raise ValueError("background jobs require an owner context")
                ctx._runtime_pending_job = (job, tid, result)
                if isinstance(result, InferenceJob):
                    result.job_id = job["id"]
                common.set_current_trace(None)
                poll(conn, ctx)
                break
            jobs.complete(conn, job["id"], result if isinstance(result, dict) else None)
            tracing.finish(conn, tid, "ok")
        except Exception as exc:  # noqa: BLE001 — a bad job must not kill the loop
            if has_pending(ctx):
                # A bound session still owns cleanup/accounting. Let normal DB
                # containment retry that session rather than requeueing its job
                # behind its back and opening a second dispatch path.
                raise
            _failed(conn, job, tid, exc)
    return processed
