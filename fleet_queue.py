"""Bounded fleet queue/start/cancel receipts; no credentials or executable inputs."""
import hashlib
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import fleet_protocol as protocol
import worker_client

VERSION = "cara-fleet-queue/v1"
QUEUE_SECONDS = 240
EXECUTION_SECONDS = 240
MAX_HOST_TRANSPORTS = 3
READY_FILE = ".fleet-queue-ready.json"
_JOB = re.compile(r"w_[0-9]+_[0-9]+_[a-f0-9]{32}\Z")
_BINDING = ("job_id", "nonce", "task_id", "step_id", "tool", "input_hash",
            "policy_version", "implementation_version")


class QueueError(ValueError):
    pass


class QueueCancelled(QueueError):
    pass


class QueueExpired(QueueError):
    pass


def contract():
    return {"version": VERSION, "protocol_version": protocol.VERSION,
            "queue_timeout_seconds": QUEUE_SECONDS, "execution_timeout_seconds": EXECUTION_SECONDS,
            "max_host_transports": MAX_HOST_TRANSPORTS, "max_per_host": 1,
            "scheduling": "serial-request-fifo", "cancel_location": "requests",
            "started_receipt": "nonce-input-policy-bound", "unknown_execution": "no-replay"}


def _source_hash():
    digest = hashlib.sha256()
    for name in ("fleet_queue.py", "cara_fleet.py", "fleet_protocol.py", "worker_client.py", "fleet_lifecycle.py"):
        digest.update(name.encode("ascii") + b"\0")
        digest.update(Path(__file__).with_name(name).read_bytes())
    return digest.hexdigest()


# Bind the bytes loaded by this process, rather than new files copied over a
# still-running old executor during a guarded release transition.
_IMPLEMENTATION_HASH = _source_hash()


def implementation_hash():
    return _IMPLEMENTATION_HASH


def publish_ready(spool):
    """Executor-only receipt; installation binds this to the verified active PID."""
    spool = Path(spool)
    ready = {"version": VERSION, "contract": contract(), "contract_hash": protocol.digest(contract()),
             "implementation_hash": implementation_hash(), "pid": os.getpid(), "started_at": protocol.now()}
    existing = worker_client._read_regular_nofollow(spool / "results" / READY_FILE, 4096)
    if existing is not None:
        try:
            previous = json.loads(existing)
            if all(previous.get(key) == ready[key] for key in ready if key != "started_at"):
                return previous
        except (ValueError, TypeError, AttributeError):
            pass
    worker_client._atomic_publish(spool / "results", READY_FILE, protocol.canonical(ready).encode())
    return ready


def _spool(cfg):
    return Path(cfg.task_worker_spool)


def readiness(cfg):
    raw = worker_client._read_regular_nofollow(_spool(cfg) / "results" / READY_FILE, 4096)
    if raw is None:
        return None
    try:
        value = json.loads(raw)
        expected = {"version", "contract", "contract_hash", "implementation_hash", "pid", "started_at"}
        if (not isinstance(value, dict) or set(value) != expected or value["version"] != VERSION
                or value["contract"] != contract() or value["contract_hash"] != protocol.digest(contract())
                or value["implementation_hash"] != implementation_hash()
                or type(value["pid"]) is not int or value["pid"] <= 0):
            raise QueueError("fleet executor readiness contract mismatch")
        _moment(value["started_at"])
        return value
    except (ValueError, TypeError, KeyError) as exc:
        raise QueueError("fleet executor readiness unavailable") from exc


def _moment(value):
    if not isinstance(value, str):
        raise QueueError("invalid fleet queue time")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
            raise ValueError("UTC required")
        return parsed
    except (ValueError, OverflowError) as exc:
        raise QueueError("invalid fleet queue time") from exc


def validate_lifecycle(value):
    if (not isinstance(value, dict) or set(value) != {
            "version", "submitted_at", "queue_expires_at", "execution_timeout_seconds"}
            or value.get("version") != VERSION
            or type(value.get("execution_timeout_seconds")) is not int
            or value["execution_timeout_seconds"] != EXECUTION_SECONDS):
        raise QueueError("invalid fleet queue lifecycle")
    submitted, expires = _moment(value["submitted_at"]), _moment(value["queue_expires_at"])
    if (expires - submitted).total_seconds() != QUEUE_SECONDS:
        raise QueueError("fleet queue deadline exceeds policy")
    return dict(value)


def lifecycle(cfg, now=None):
    """Opt in only after the installed executor advertises this exact contract."""
    if readiness(cfg) is None:
        raise QueueError("fleet queue executor is not ready")
    submitted = _moment(now or protocol.now())
    return {"version": VERSION, "submitted_at": submitted.isoformat(),
            "queue_expires_at": (submitted + timedelta(seconds=QUEUE_SECONDS)).isoformat(),
            "execution_timeout_seconds": EXECUTION_SECONDS}


def _job(value):
    if not isinstance(value, str) or not _JOB.fullmatch(value):
        raise QueueError("invalid fleet queue job")
    return value


def binding(envelope):
    return {key: envelope[key] for key in _BINDING}


def _started_path(spool, job_id):
    return Path(spool) / "results" / (_job(job_id) + ".started.json")


def _cancel_path(spool, job_id):
    return Path(spool) / "requests" / (".cancel-" + _job(job_id) + ".json")


def cancel(cfg, *, job_id, nonce=None):
    _job(job_id)
    expected_nonce = job_id.rsplit("_", 1)[1]
    if nonce is not None and nonce != expected_nonce:
        raise QueueError("fleet cancellation nonce mismatch")
    marker = {"version": VERSION, "job_id": job_id, "nonce": expected_nonce}
    path = _cancel_path(_spool(cfg), job_id)
    worker_client._atomic_publish(path.parent, path.name, protocol.canonical(marker).encode())
    return True


def cancelled(spool, envelope):
    raw = worker_client._read_regular_nofollow(_cancel_path(spool, envelope["job_id"]), 1024)
    if raw is None:
        return False
    try:
        value = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise QueueError("invalid fleet cancellation receipt") from exc
    if value != {"version": VERSION, "job_id": envelope["job_id"], "nonce": envelope["nonce"]}:
        raise QueueError("fleet cancellation binding mismatch")
    return True


def publish_started(spool, envelope, now=None):
    lifecycle_value = validate_lifecycle(envelope["lifecycle"])
    started = _moment(now or protocol.now())
    if started >= _moment(lifecycle_value["queue_expires_at"]):
        raise QueueExpired("fleet queue deadline reached before durable start")
    value = {"version": VERSION, "state": "started", "binding": binding(envelope),
             "lifecycle_hash": protocol.digest(lifecycle_value), "started_at": started.isoformat(),
             "execution_expires_at": (started + timedelta(seconds=EXECUTION_SECONDS)).isoformat()}
    path = _started_path(spool, envelope["job_id"])
    worker_client._atomic_publish(path.parent, path.name, protocol.canonical(value).encode())
    return value


def poll_started(cfg, *, lifecycle, **expected_binding):
    if set(expected_binding) != set(_BINDING):
        raise QueueError("fleet started receipt requires the full binding")
    value = read_started(_spool(cfg), expected_binding, lifecycle)
    return value


def read_started(spool, expected_binding, lifecycle_value):
    raw = worker_client._read_regular_nofollow(_started_path(spool, expected_binding["job_id"]), 4096)
    if raw is None:
        return None
    try:
        value = json.loads(raw)
        if (not isinstance(value, dict) or set(value) != {
                "version", "state", "binding", "lifecycle_hash", "started_at", "execution_expires_at"}
                or value["version"] != VERSION or value["state"] != "started"
                or value["binding"] != expected_binding
                or value["lifecycle_hash"] != protocol.digest(validate_lifecycle(lifecycle_value))
                or (_moment(value["execution_expires_at"]) - _moment(value["started_at"])).total_seconds() != EXECUTION_SECONDS):
            raise QueueError("fleet started receipt binding mismatch")
        started = _moment(value["started_at"])
        if (not _moment(lifecycle_value["submitted_at"]) <= started < _moment(lifecycle_value["queue_expires_at"])
                or (started - datetime.now(timezone.utc)).total_seconds() > 30):
            raise QueueError("fleet started receipt time mismatch")
        return value
    except (ValueError, TypeError, KeyError) as exc:
        raise QueueError("invalid fleet started receipt") from exc


class Control:
    """Executor-owned dispatch guard, shared by its at-most-three host threads."""
    def __init__(self, spool, envelope):
        self.spool, self.envelope = Path(spool), envelope
        self.deadline = time.monotonic() + EXECUTION_SECONDS
        self.effect_dispatched = False

    def check(self):
        # The serial executor acknowledges this global hold only after all
        # already-dispatched transports have returned. Stop every later host
        # dispatch immediately, even before per-job cancel markers arrive.
        hold = self.spool / "requests" / ".purge-all.json"
        if hold.exists() or hold.is_symlink():
            raise QueueCancelled("fleet content purge holds admission")
        if cancelled(self.spool, self.envelope):
            raise QueueCancelled("fleet request cancelled")
        if not (self.spool / "requests" / (self.envelope["job_id"] + ".json")).exists():
            raise QueueCancelled("fleet request removed")
        if time.monotonic() >= self.deadline:
            raise QueueExpired("fleet execution deadline reached")


def queue_state(spool, envelope, now=None):
    value = validate_lifecycle(envelope["lifecycle"])
    current = _moment(now or protocol.now())
    if (_moment(value["submitted_at"]) - current).total_seconds() > 30:
        raise QueueError("fleet queue submission is in the future")
    if cancelled(spool, envelope):
        return "cancelled"
    if current >= _moment(value["queue_expires_at"]):
        return "queue_expired"
    return "queued"


def terminal(envelope, state):
    reply = {key: value for key, value in envelope.items() if key != "input"}
    result = {"state": state}
    if envelope["input"]["operation"] == "restart" and state == "execution_interrupted":
        reply.update(status="ok")
        result = {"outcome": "ambiguous", "reason": "executor interrupted; inspect before another action"}
    else:
        reply.update(status="cancelled" if state == "cancelled" else "error", error=state)
    reply.update(result=result, result_hash=protocol.digest(result))
    return reply
