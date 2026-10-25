"""Bounded network-only inference transport. Never receives a SQLite connection."""
import contextvars
from contextlib import contextmanager
import json
import queue
import threading
from time import monotonic as _monotonic

deadline = contextvars.ContextVar("inference_deadline", default=None)
wait_callback = contextvars.ContextVar("inference_wait_callback", default=None)
transport_lifecycle = contextvars.ContextVar("inference_transport_lifecycle", default=None)
_slots = threading.BoundedSemaphore(2)
# All asynchronous model users share one background slot, preserving capacity
# for the foreground owner. Release it only after the actual transport exits.
background_slots = threading.BoundedSemaphore(1)


class BackgroundLease:
    """Reserve background capacity until the actual socket thread exits.

    A caller timeout can precede socket completion. The lifecycle callback keeps
    that occupied background capacity from taking the foreground slot again.
    Mocked transports and pre-dispatch failures release on the caller instead.
    """

    def __init__(self, slot=None):
        self.slot = background_slots if slot is None else slot
        self.lock = threading.Lock()
        self.acquired = self.started = self.released = False

    def acquire(self):
        self.acquired = self.slot.acquire(blocking=False)
        return self.acquired

    def _release(self):
        if self.acquired and not self.released:
            self.released = True
            self.slot.release()

    def lifecycle(self, phase):
        with self.lock:
            if phase == "started":
                self.started = True
            elif phase == "finished":
                self._release()

    def release_if_idle(self):
        with self.lock:
            if not self.started:
                self._release()

    def run(self, call, *args, **kwargs):
        token = transport_lifecycle.set(self.lifecycle)
        try:
            return call(*args, **kwargs)
        finally:
            transport_lifecycle.reset(token)
            self.release_if_idle()


@contextmanager
def on_wait(callback):
    """Run a bounded non-inference heartbeat on the calling/SQLite owner thread."""
    token = wait_callback.set(callback)
    try:
        yield
    finally:
        wait_callback.reset(token)


@contextmanager
def turn_budget(seconds=90):
    token = deadline.set(_monotonic() + seconds)
    try:
        yield
    finally:
        deadline.reset(token)


def json_request(opener, request, timeout, error_type):
    started = _monotonic()
    local_end = started + float(timeout)
    turn_end = deadline.get() or float("inf")
    end = min(turn_end, local_end)
    remaining = end - _monotonic()
    socket_timeout = float(timeout) if turn_end >= local_end else remaining
    if remaining <= 0:
        exc = error_type("turn deadline exceeded before sending", transient=True)
        exc.request_not_sent = True
        raise exc
    if not _slots.acquire(blocking=False):
        exc = error_type("previous inference transport is still finishing", transient=True)
        exc.request_not_sent = True
        raise exc
    result = queue.Queue(maxsize=1)
    lifecycle = transport_lifecycle.get()

    def transport():
        try:
            with opener(request, timeout=socket_timeout) as response:
                result.put((True, json.loads(response.read().decode("utf-8"))))
        except Exception as exc:  # propagate the original HTTP/transport error on the DB thread
            result.put((False, exc))
        finally:
            _slots.release()
            if lifecycle is not None:
                lifecycle("finished")

    if lifecycle is not None:
        lifecycle("started")
    try:
        threading.Thread(target=transport, name="inference-transport", daemon=True).start()
    except Exception:
        _slots.release()
        if lifecycle is not None:
            lifecycle("finished")
        raise
    while True:
        remaining = end - _monotonic()
        if remaining <= 0:
            raise error_type("turn deadline exceeded; completion usage unknown", transient=True)
        callback = wait_callback.get()
        try:
            success, value = result.get(timeout=min(0.1, remaining) if callback else remaining)
            break
        except queue.Empty:
            if callback is not None:
                # A heartbeat must never recurse into another model wait.
                token = wait_callback.set(None)
                try:
                    callback()
                finally:
                    wait_callback.reset(token)
    if not success:
        raise value
    return value
