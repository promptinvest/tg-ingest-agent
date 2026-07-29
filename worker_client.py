#!/usr/bin/env python3
"""Non-blocking, bounded Unix-spool client for Cara's local worker."""
import errno
import hashlib
import json
import os
import re
import secrets
import stat
import time
from pathlib import Path

import tasking


MAX_RESULT_BYTES = 64 * 1024
MAX_PENDING_FILES = 100
MAX_PENDING_BYTES = 1024 * 1024


class WorkerError(RuntimeError):
    pass


class WorkerUnavailable(WorkerError):
    pass


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _dirs(cfg):
    root = Path(cfg.task_worker_spool)
    return root / "requests", root / "results", root / "cancel"


def _fsync_dir(path):
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_publish(directory, name, body):
    directory.mkdir(parents=True, exist_ok=True)
    temp_name = f".{name}.{os.getpid()}.{secrets.token_hex(6)}.tmp"
    temp = directory / temp_name
    flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
             | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
    fd = os.open(temp, flags, 0o640)
    try:
        # The agent service intentionally runs with UMask=0077.  chmod after
        # creation is therefore required for the cara-worker-spool group to
        # read requests/cancellation markers; the containing directories are
        # setgid so the narrow spool group is inherited.
        os.fchmod(fd, 0o640)
        view = memoryview(body)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short spool write")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.replace(temp, directory / name)
        _fsync_dir(directory)
    except BaseException:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _read_regular_nofollow(path, maximum):
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise WorkerError("worker result is a symlink") from exc
        raise
    try:
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1:
            raise WorkerError("worker result is not a single-link regular file")
        if meta.st_size <= 0 or meta.st_size > maximum:
            raise WorkerError("worker result size is invalid")
        chunks, remaining = [], meta.st_size
        while remaining:
            chunk = os.read(fd, min(remaining, 8192))
            if not chunk:
                raise WorkerError("worker result was truncated")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(fd, 1):
            raise WorkerError("worker result grew while being read")
        return b"".join(chunks)
    finally:
        os.close(fd)


def submit(cfg, *, task_id, step_id, tool, input_value, input_hash,
           policy_version, implementation_version):
    """Publish one request and return its durable binding; never wait."""
    requests, _, _ = _dirs(cfg)
    try:
        pending = list(requests.glob("w_*.json")) if requests.exists() else []
        if (len(pending) >= MAX_PENDING_FILES
                or sum(path.stat().st_size for path in pending) >= MAX_PENDING_BYTES):
            raise WorkerUnavailable("worker spool quota is exhausted")
    except OSError as exc:
        raise WorkerUnavailable(f"worker spool quota check failed: {exc}") from exc
    nonce = secrets.token_hex(16)
    job_id = f"w_{task_id}_{step_id}_{nonce}"
    safe_input = json.loads(tasking.redact_derived_text(_canonical(input_value)))
    request = {
        "version": 1,
        "job_id": job_id,
        "nonce": nonce,
        "task_id": int(task_id),
        "step_id": int(step_id),
        "tool": str(tool),
        "input": safe_input,
        "input_hash": str(input_hash),
        "policy_version": str(policy_version),
        "implementation_version": str(implementation_version),
    }
    body = _canonical(request).encode("utf-8")
    if len(body) > 32 * 1024:
        raise WorkerError("worker request is too large")
    try:
        _atomic_publish(requests, f"{job_id}.json", body)
    except OSError as exc:
        raise WorkerUnavailable(f"worker request publish failed: {exc}") from exc
    return {"job_id": job_id, "nonce": nonce}


def poll(cfg, *, job_id, nonce, task_id, step_id, tool, input_hash,
         policy_version, implementation_version):
    """Return verified result, None while pending, or raise on bad output."""
    _, results, _ = _dirs(cfg)
    try:
        raw = _read_regular_nofollow(results / f"{job_id}.json", MAX_RESULT_BYTES)
    except OSError as exc:
        raise WorkerUnavailable(f"worker result unavailable: {exc}") from exc
    if raw is None:
        return None
    try:
        envelope = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise WorkerError("worker result is malformed") from exc
    for key, expected in (
        ("job_id", job_id),
        ("nonce", nonce),
        ("task_id", int(task_id)),
        ("step_id", int(step_id)),
        ("tool", str(tool)),
        ("input_hash", str(input_hash)),
        ("policy_version", str(policy_version)),
        ("implementation_version", str(implementation_version)),
    ):
        if envelope.get(key) != expected:
            raise WorkerError(f"worker result binding mismatch: {key}")
    result = envelope.get("result")
    expected_hash = hashlib.sha256(_canonical(result).encode("utf-8")).hexdigest()
    if envelope.get("result_hash") != expected_hash:
        raise WorkerError("worker result hash mismatch")
    if envelope.get("status") == "cancelled":
        raise WorkerError("worker job was cancelled")
    if envelope.get("status") != "ok":
        raise WorkerError(str(envelope.get("error") or "worker tool failed")[:200])
    return result


def acknowledge(cfg, job_id, *, consume_cancel=True):
    """Acknowledge a worker boundary after a result proves the child ended."""
    requests, _, cancel = _dirs(cfg)
    ok = True
    paths = [requests / f"{job_id}.json"]
    if consume_cancel:
        paths.append(cancel / job_id)
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            ok = False
    return ok


def reconcile(cfg, referenced_job_ids, terminal_job_ids=(), stale_seconds=300):
    """Remove agent-owned requests that can no longer produce a live receipt."""
    requests, results, _ = _dirs(cfg)
    referenced = {str(item) for item in referenced_job_ids if item}
    terminal = {str(item) for item in terminal_job_ids if item}
    removed = 0
    now = time.time()
    try:
        pending = list(requests.glob("w_*.json")) if requests.exists() else []
    except OSError:
        return 0
    for path in pending:
        job_id = path.name.removesuffix(".json")
        stale_orphan = False
        if job_id not in referenced:
            try:
                stale_orphan = now - path.stat().st_mtime >= stale_seconds
            except OSError:
                continue
        if job_id in terminal or stale_orphan:
            result_exists = (results / path.name).exists()
            if not result_exists:
                # Keep both request and cancellation marker durable until the
                # worker publishes a terminal envelope. Removing either here
                # lets an already-running child miss cancellation.
                request_cancel(cfg, job_id)
                continue
            removed += int(acknowledge(cfg, job_id, consume_cancel=True))
    return removed


def request_cancel(cfg, job_id):
    """Publish a no-follow cancel marker in Cara's one-way directory."""
    _, _, cancel = _dirs(cfg)
    try:
        _atomic_publish(cancel, str(job_id), b"cancel\n")
        return True
    except FileExistsError:
        return True
    except OSError:
        return False


def prepare_purge(cfg, nonce=None):
    """Durably request deletion of worker-owned results before DB purge."""
    _, results, cancel = _dirs(cfg)
    marker = cancel / ".purge-all.json"
    try:
        raw = _read_regular_nofollow(marker, 1024)
        if raw is not None:
            value = json.loads(raw.decode("utf-8"))
            if (isinstance(value, dict) and value.get("version") == 1
                    and value.get("action") == "purge_results"
                    and isinstance(value.get("nonce"), str)):
                if nonce is not None and value["nonce"] != nonce:
                    raise WorkerError("worker purge marker conflicts with durable intent")
                return value["nonce"]
            raise WorkerError("existing worker purge marker is invalid")
        nonce = nonce or secrets.token_hex(16)
        if not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{32}", nonce):
            raise WorkerError("worker purge nonce is invalid")
        body = _canonical({
            "version": 1, "action": "purge_results", "nonce": nonce,
        }).encode("utf-8")
        _atomic_publish(cancel, marker.name, body)
        return nonce
    except (OSError, ValueError) as exc:
        raise WorkerUnavailable(f"cannot create worker purge marker: {exc}") from exc


def pending_purge_nonce(cfg):
    """Read only an app-owned, single-link, exact-mode purge marker."""
    _, _, cancel = _dirs(cfg)
    marker = cancel / ".purge-all.json"
    try:
        meta = marker.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise WorkerUnavailable(f"cannot inspect worker purge marker: {exc}") from exc
    if (not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1
            or meta.st_uid != os.geteuid()
            or stat.S_IMODE(meta.st_mode) != 0o640):
        raise WorkerError("worker purge marker ownership or mode is invalid")
    raw = _read_regular_nofollow(marker, 1024)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (AttributeError, UnicodeDecodeError, ValueError) as exc:
        raise WorkerError("worker purge marker is malformed") from exc
    if (not isinstance(value, dict)
            or set(value) != {"version", "action", "nonce"}
            or value["version"] != 1 or value["action"] != "purge_results"
            or not re.fullmatch(r"[0-9a-f]{32}", str(value["nonce"]))):
        raise WorkerError("worker purge marker schema mismatch")
    return value["nonce"]


def abort_purge(cfg):
    """Remove only a marker already validated by pending_purge_nonce."""
    _, _, cancel = _dirs(cfg)
    pending_purge_nonce(cfg)
    try:
        (cancel / ".purge-all.json").unlink(missing_ok=True)
        return True
    except OSError:
        return False


def finish_purge(cfg, nonce, timeout_seconds=5):
    """Wait for the worker-owned result purge; keep its quiescence marker."""
    _, results, cancel = _dirs(cfg)
    ack = results / f"purge_{nonce}.ack.json"
    deadline = time.monotonic() + max(0.1, float(timeout_seconds))
    while time.monotonic() < deadline:
        try:
            raw = _read_regular_nofollow(ack, 1024)
        except OSError as exc:
            raise WorkerUnavailable(f"worker purge acknowledgement failed: {exc}") from exc
        if raw is not None:
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise WorkerError("worker purge acknowledgement is malformed") from exc
            if value != {"version": 1, "action": "purge_results", "nonce": nonce,
                         "status": "ok"}:
                raise WorkerError("worker purge acknowledgement binding mismatch")
            return True
        time.sleep(0.1)
    return False


def consume_purge(cfg, nonce):
    """Release worker quiescence after Cara has cleared its own spool half."""
    pending = pending_purge_nonce(cfg)
    if pending != nonce:
        raise WorkerError("worker purge marker no longer matches durable intent")
    _, _, cancel = _dirs(cfg)
    try:
        (cancel / ".purge-all.json").unlink()
        _fsync_dir(cancel)
        return True
    except OSError as exc:
        raise WorkerUnavailable(
            f"cannot consume worker purge marker: {exc}") from exc
