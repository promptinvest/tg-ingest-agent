"""Owner-thread profile policy/accounting with bounded connection-free transport.

The caller persists the canonical fleet request before constructing ProfileCall,
then persists each reservation/phase binding in before_dispatch. Sessions are
in-memory handles, never a second content ledger. A started phase lost on restart
must be reconciled from the durable receipt, never reconstructed and resent.
"""
import contextvars
import json
import re
import sqlite3
import threading
import time
from concurrent.futures import CancelledError, ThreadPoolExecutor

import call_budget
import common
import llm
import inference_io


# Keep one of inference_io's unchanged two global slots for foreground owner
# routing/conversation. Background fleet work was serial before this change.
MAX_ACTIVE_TRANSPORTS = 1
_pool = ThreadPoolExecutor(max_workers=MAX_ACTIVE_TRANSPORTS,
                           thread_name_prefix="fleet-inference")
_slots = inference_io.background_slots
_BINDING_FIELDS = frozenset({
    "request_id", "phase", "nonce", "input_hash", "source_hash",
    "inventory_hash", "facts_hash", "capability_hash", "policy_version",
})


def _transport(context, prepared, lease):
    return context.run(lease.run, llm.transport_chat, prepared)


class StalePhase(ValueError):
    """The canonical request or one of its authority revisions changed."""


class ProfileCall:
    """Pump with advance() on the creating thread; it never waits for a model.

    is_current checks durable source/phase/revision authority. before_dispatch
    durably records the exact binding and reservation before any paid request.
    Both callbacks run on the owner thread. Transport receives no connection.
    A false cancel() return means transport cancellation is unconfirmed; still
    advance the session to settle usage and discard its eventual content.
    """
    def __init__(self, cfg, conn, skill, messages, *, profile, binding,
                 before_dispatch, is_current, max_tokens=None, json_required=None):
        if (not isinstance(binding, dict) or set(binding) != _BINDING_FIELDS
                or any(not isinstance(value, str) or not 1 <= len(value) <= 128
                       for value in binding.values())):
            raise ValueError("invalid inference phase binding")
        if not callable(before_dispatch) or not callable(is_current):
            raise ValueError("durable inference callbacks are required")
        self.cfg, self.conn, self.skill = cfg, conn, skill
        # Snapshot inputs so later edits cannot change an already-bound prompt.
        self.messages = json.loads(json.dumps(messages, ensure_ascii=False))
        self.profile, self.max_tokens = profile, max_tokens
        self.json_required = json_required
        self._binding = dict(binding)
        self.before_dispatch, self.is_current = before_dispatch, is_current
        self.owner_thread = threading.get_ident()
        self.trace_id = common.current_trace()
        self.steps = None
        self.operation = None
        self.future = None
        self.prepared = None
        self.retry_at = None
        self.cancel_requested = False
        self.done = False
        self.result = None
        self.error = None
        self.state = "queued"
        self._usage_ids = []

    @property
    def binding(self):
        return dict(self._binding)

    @property
    def usage_ids(self):
        return tuple(self._usage_ids)

    @property
    def error_summary(self):
        if self.error is None:
            return ""
        status = re.search(r"\bHTTP\s+(\d{3})\b", str(self.error), re.I)
        return type(self.error).__name__ + (" HTTP " + status.group(1) if status else "")

    def _owner(self):
        if threading.get_ident() != self.owner_thread:
            raise RuntimeError("inference policy/accounting requires the owner thread")

    def cost(self):
        self._owner()
        if not self._usage_ids:
            return 0.0
        placeholders = ",".join("?" for _ in self._usage_ids)
        return self.conn.execute(
            "SELECT COALESCE(SUM(cost_usd),0) FROM llm_usage WHERE id IN ("
            + placeholders + ")", self._usage_ids).fetchone()[0]

    def cancel(self):
        self._owner()
        if self.done:
            return self.state == "cancelled"
        self.cancel_requested = True
        if self.future is not None:
            return self.future.cancel()
        self._stop(CancelledError(), state="cancelled")
        return True

    def _current(self):
        try:
            return bool(self.is_current(self.binding))
        except Exception as exc:
            # A failed authority check is not permission to reconstruct or
            # resend an already-consumed transport outcome on the next tick.
            self._stop(error=exc)
            raise

    def _stop(self, error=None, result=None, state=None):
        self.done, self.error, self.result = True, error, result
        self.state = state or ("failed" if error is not None else "completed")
        if self.steps is not None:
            self.steps.close()
        self.operation = None
        return True

    def _step(self, result=None, error=None, first=False):
        try:
            if first:
                self.operation = next(self.steps)
            elif error is not None:
                self.operation = self.steps.throw(error)
            else:
                self.operation = self.steps.send(result)
        except StopIteration as completed:
            self._stop(result=completed.value)
        except Exception as exc:
            self._stop(error=exc)

    def advance(self):
        """Return done without waiting; SQLite and callbacks stay on this thread."""
        self._owner()
        if self.done:
            return True
        previous_trace = common.current_trace()
        common.set_current_trace(self.trace_id)
        try:
            return self._advance()
        finally:
            common.set_current_trace(previous_trace)

    def _advance(self):
        if self.future is not None:
            if not self.future.done():
                return False
            data, error = None, None
            try:
                data = self.future.result()
            except CancelledError:
                error = llm.LLMError("cancelled before transport started")
                error.request_not_sent = True
            except Exception as exc:
                error = exc
            try:
                result = llm.finish_chat(
                    self.cfg, self.conn, self.prepared, data=data, error=error,
                    meta=self.operation["options"]["meta"])
            except llm.LLMError as exc:
                result, error = None, exc
            except sqlite3.Error:
                raise
            except Exception as exc:
                self.future = None
                return self._stop(error=exc)
            # A failed SQLite settlement propagates to the caller's normal DB
            # containment, keeping the same future for an idempotent retry.
            self.future = None
            if self.cancel_requested:
                return self._stop(CancelledError(), state="cancelled")
            if not self._current():
                return self._stop(StalePhase("inference authority changed"))
            self._step(result=result, error=error)
            if self.done:
                return True

        if self.cancel_requested:
            return self._stop(CancelledError(), state="cancelled")
        if not self._current():
            return self._stop(StalePhase("inference authority changed"))
        if self.operation is not None and self.operation["kind"] == "delay":
            if self.retry_at is None:
                self.retry_at = time.monotonic() + self.operation["seconds"]
            if time.monotonic() < self.retry_at:
                self.state = "retry_wait"
                return False
            self.retry_at = None
            self._step()
            if self.done:
                return True

        # Backpressure precedes the first profile step and reservation: queued
        # work spends nothing and its execution deadline has not started yet.
        lease = inference_io.BackgroundLease(_slots)
        if not lease.acquire():
            return False
        dispatched = False
        try:
            if self.steps is None:
                self.steps = llm.profile_steps(
                    self.cfg, self.conn, self.skill, self.messages,
                    profile=self.profile, max_tokens=self.max_tokens,
                    json_required=self.json_required, retry_unknown=False)
                self._step(first=True)
                if self.done:
                    return True
            options = dict(self.operation["options"])
            meta = options.pop("meta")
            try:
                self.prepared = llm.prepare_chat(
                    self.cfg, self.conn, self.skill, self.messages, meta=meta, **options)
            except llm.LLMError as exc:
                self._step(error=exc)
                return self.done
            except Exception as exc:
                # A prepare-side database failure must not turn into another
                # reservation on the next tick. No transport was dispatched.
                self._stop(error=exc)
                raise
            self._usage_ids.append(self.prepared["reserved"])
            receipt = {"reservation_id": self.prepared["reserved"],
                       "attempt": len(self._usage_ids), "model": self.prepared["model"],
                       "skill": self.skill, "timeout": self.prepared["timeout"]}
            try:
                self.before_dispatch(self.binding, receipt)
                if not self._current():
                    raise StalePhase("inference authority changed before dispatch")
                context = contextvars.copy_context()
                self.future = _pool.submit(_transport, context, self.prepared, lease)
                dispatched = True
            except Exception as exc:
                # Nothing left this process. Release only this reservation; do
                # not retry a phase whose durable dispatch bookkeeping failed.
                self._stop(error=exc)
                call_budget.settle(self.conn, self.prepared["reserved"], "rejected_chat")
                return True
            self.future.add_done_callback(
                lambda future: lease.release_if_idle() if future.cancelled() else None)
            self.state = "started"
            return False
        finally:
            if not dispatched:
                lease.release_if_idle()
