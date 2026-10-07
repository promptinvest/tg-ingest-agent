"""Bounded network-only inference transport. Never receives a SQLite connection."""
import contextvars
from contextlib import contextmanager
import json
import queue
import threading
from time import monotonic as _monotonic

deadline = contextvars.ContextVar("inference_deadline", default=None)
_slots = threading.BoundedSemaphore(2)


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

    def transport():
        try:
            with opener(request, timeout=socket_timeout) as response:
                result.put((True, json.loads(response.read().decode("utf-8"))))
        except Exception as exc:  # propagate the original HTTP/transport error on the DB thread
            result.put((False, exc))
        finally:
            _slots.release()

    threading.Thread(target=transport, name="inference-transport", daemon=True).start()
    try:
        success, value = result.get(timeout=max(0.001, end - _monotonic()))
    except queue.Empty as exc:
        raise error_type("turn deadline exceeded; completion usage unknown", transient=True) from exc
    if not success:
        raise value
    return value
