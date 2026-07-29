#!/usr/bin/env python3
"""Unprivileged, registry-only local worker for Cara.

There is intentionally no command, argv, script, import, browser, or network
surface. The first tool is a harmless echo used to prove spool isolation.
"""
import hashlib
import json
import os
import errno
import resource
import re
import select
import signal
import shutil
import stat
import tempfile
import time
from pathlib import Path


MAX_REQUEST_BYTES = 32 * 1024
MAX_RESULT_BYTES = 64 * 1024
MAX_PENDING_FILES = 100
MAX_PENDING_BYTES = 1024 * 1024
TOOLS = frozenset({"worker.echo"})
POLICY_VERSION = "task-tools/v2"
IMPLEMENTATION_VERSION = "tasking/v2"


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _safe_job_id(value):
    text = str(value or "")
    return (
        1 <= len(text) <= 120
        and text.startswith("w_")
        and all(ch.isalnum() or ch == "_" for ch in text)
    )


def _load_request(path):
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ValueError("request is a symlink") from exc
        raise
    try:
        meta = os.fstat(fd)
        if (not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1
                or meta.st_size <= 0 or meta.st_size > MAX_REQUEST_BYTES):
            raise ValueError("request size/type is invalid")
        raw = os.read(fd, meta.st_size + 1)
        if len(raw) != meta.st_size:
            raise ValueError("request changed while being read")
    finally:
        os.close(fd)
    value = json.loads(raw.decode("utf-8"))
    required = {
        "version", "job_id", "nonce", "task_id", "step_id", "tool", "input",
        "input_hash", "policy_version", "implementation_version",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("request schema mismatch")
    if value["version"] != 1 or not _safe_job_id(value["job_id"]):
        raise ValueError("request identity is invalid")
    if value["tool"] not in TOOLS:
        raise ValueError("unknown worker tool")
    if not isinstance(value["task_id"], int) or not isinstance(value["step_id"], int):
        raise ValueError("task binding is invalid")
    if value["task_id"] <= 0 or value["step_id"] <= 0:
        raise ValueError("task binding is invalid")
    nonce = str(value["nonce"])
    if not re.fullmatch(r"[0-9a-f]{32}", nonce):
        raise ValueError("nonce is invalid")
    if value["job_id"] != f"w_{value['task_id']}_{value['step_id']}_{nonce}":
        raise ValueError("job id does not match its bindings")
    if (value["policy_version"] != POLICY_VERSION
            or value["implementation_version"] != IMPLEMENTATION_VERSION):
        raise ValueError("worker policy version mismatch")
    digest = hashlib.sha256(_canonical(value["input"]).encode("utf-8")).hexdigest()
    if value["input_hash"] != digest:
        raise ValueError("worker input hash mismatch")
    return value


def _execute(request, scratch):
    if request["tool"] == "worker.echo":
        value = request["input"]
        if not isinstance(value, dict) or set(value) != {"text"}:
            raise ValueError("worker.echo input schema mismatch")
        text = value["text"]
        if not isinstance(text, str) or not 1 <= len(text) <= 1000:
            raise ValueError("worker.echo text is invalid")
        # Touch only the fresh scratch directory, proving the worker does not
        # need a host path to do its work.
        marker = scratch / "echo.txt"
        marker.write_text(text, encoding="utf-8")
        return {
            "schema": "worker.echo/v1",
            "echo": marker.read_text(encoding="utf-8"),
        }
    raise ValueError("unknown worker tool")


def _execute_isolated(request, scratch, cancel_path, timeout=10):
    """Run one compiled tool in a fresh resource-limited child."""
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        try:
            os.close(read_fd)
            resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
            resource.setrlimit(resource.RLIMIT_AS, (128 * 1024 * 1024,) * 2)
            resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024,) * 2)
            resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
            resource.setrlimit(resource.RLIMIT_NPROC, (8, 8))
            os.chdir(scratch)
            result = {"ok": True, "result": _execute(request, Path("."))}
        except Exception as exc:
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}
        body = _canonical(result).encode("utf-8")
        try:
            os.write(write_fd, body[:MAX_RESULT_BYTES])
        finally:
            os.close(write_fd)
        os._exit(0)
    os.close(write_fd)
    deadline = time.monotonic() + timeout
    chunks = []
    reaped = False
    try:
        while time.monotonic() < deadline:
            if cancel_path.exists():
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
                raise InterruptedError("worker tool cancelled")
            ready, _, _ = select.select([read_fd], [], [], 0.1)
            if ready:
                chunk = os.read(read_fd, 8192)
                if not chunk:
                    break
                chunks.append(chunk)
                if sum(map(len, chunks)) > MAX_RESULT_BYTES:
                    raise ValueError("isolated result exceeded limit")
            # Do not stop merely because the child exited: the pipe can still
            # contain its final bytes. EOF on read_fd is the completion signal.
            if not reaped:
                waited, _ = os.waitpid(pid, os.WNOHANG)
                reaped = waited == pid
        else:
            os.kill(pid, signal.SIGKILL)
            if not reaped:
                os.waitpid(pid, 0)
            raise TimeoutError("worker tool timed out")
        try:
            if not reaped:
                os.waitpid(pid, 0)
                reaped = True
        except ChildProcessError:
            reaped = True
    except BaseException:
        if not reaped:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass
        raise
    finally:
        os.close(read_fd)
    payload = json.loads(b"".join(chunks).decode("utf-8"))
    if not payload.get("ok"):
        raise ValueError(payload.get("error") or "worker tool failed")
    return payload["result"]


def _publish_result(results_dir, filename, envelope):
    body = _canonical(envelope).encode("utf-8")
    if len(body) > MAX_RESULT_BYTES:
        raise ValueError("result size exceeded")
    target = results_dir / filename
    temp = results_dir / f".{filename}.{os.getpid()}.tmp"
    with open(temp, "xb") as handle:
        os.chmod(temp, 0o640)
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, target)


def process_one(request_path, results_dir, cancel_dir, scratch_root):
    request = None
    try:
        request = _load_request(request_path)
        job_id = request["job_id"]
        if (cancel_dir / job_id).exists():
            status, result, error = "cancelled", None, "cancelled before execution"
        else:
            scratch = Path(tempfile.mkdtemp(prefix=job_id + "-", dir=scratch_root))
            try:
                try:
                    result = _execute_isolated(
                        request, scratch, cancel_dir / job_id)
                    status, error = "ok", None
                except InterruptedError:
                    status, result, error = "cancelled", None, "cancelled"
                except Exception as exc:
                    status, result = "error", None
                    error = f"{type(exc).__name__}: {exc}"[:300]
            finally:
                shutil.rmtree(scratch, ignore_errors=True)
        envelope = {
            key: request[key]
            for key in (
                "job_id", "nonce", "task_id", "step_id", "tool", "input_hash",
                "policy_version", "implementation_version",
            )
        }
        envelope.update(
            status=status,
            result=result,
            result_hash=hashlib.sha256(_canonical(result).encode("utf-8")).hexdigest(),
            error=error,
        )
    except Exception as exc:  # a bad spool file must not stop the worker
        # A rejection marker with the same bounded basename makes this invalid
        # request non-runnable without pretending it has a verified identity.
        print(f"cara-worker rejected {request_path.name}: {exc}", flush=True)
        _publish_result(results_dir, request_path.name, {
            "version": 1, "status": "rejected",
            "error": f"{type(exc).__name__}: {exc}"[:300],
        })
        return True
    _publish_result(results_dir, f"{request['job_id']}.json", envelope)
    return True


def _process_purge_marker(marker, requests_dir, results_dir, cancel_dir):
    """Delete the complete spool at a worker quiescence boundary."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(marker, flags)
    try:
        meta = os.fstat(fd)
        if (not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1
                or meta.st_size <= 0 or meta.st_size > 1024):
            raise ValueError("purge marker size/type is invalid")
        raw = os.read(fd, meta.st_size + 1)
        if len(raw) != meta.st_size:
            raise ValueError("purge marker changed while reading")
    finally:
        os.close(fd)
    value = json.loads(raw.decode("utf-8"))
    if (not isinstance(value, dict)
            or set(value) != {"version", "action", "nonce"}
            or value["version"] != 1
            or value["action"] != "purge_results"
            or not re.fullmatch(r"[0-9a-f]{32}", str(value["nonce"]))):
        raise ValueError("purge marker schema mismatch")
    # run() is single-job-at-a-time, so reaching this function proves that no
    # isolated child remains. The worker deletes only its result directory;
    # Cara retains ownership of requests/cancel and clears them while this
    # global marker keeps the worker quiescent.
    for path in results_dir.iterdir():
        meta = path.lstat()
        if stat.S_ISREG(meta.st_mode):
            path.unlink(missing_ok=True)
    ack = {
        "version": 1, "action": "purge_results",
        "nonce": value["nonce"], "status": "ok",
    }
    target = results_dir / f"purge_{value['nonce']}.ack.json"
    temp = results_dir / f".purge_{value['nonce']}.{os.getpid()}.tmp"
    body = _canonical(ack).encode("utf-8")
    with open(temp, "xb") as handle:
        os.chmod(temp, 0o640)
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, target)
    return True


def run(spool, state, once=False):
    root = Path(spool)
    requests, results, cancel = root / "requests", root / "results", root / "cancel"
    scratch = Path(state) / "scratch"
    for path in (requests, results, cancel, scratch):
        path.mkdir(parents=True, exist_ok=True)
    # No scratch survives a worker restart.
    for path in scratch.iterdir():
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
    while True:
        purge_marker = cancel / ".purge-all.json"
        if purge_marker.exists():
            try:
                _process_purge_marker(
                    purge_marker, requests, results, cancel)
            except Exception as exc:
                print(f"cara-worker purge marker rejected: {exc}", flush=True)
            if once:
                return 0
            time.sleep(0.2)
            continue
        # Cara removes its one-way request only after accepting a result. The
        # worker owns results and prunes acknowledged/stale envelopes itself.
        now = time.time()
        for result in results.glob("w_*.json"):
            request = requests / result.name
            try:
                if not request.exists() and now - result.stat().st_mtime > 1:
                    result.unlink(missing_ok=True)
            except OSError:
                pass
        for ack in results.glob("purge_*.ack.json"):
            nonce = ack.name.removeprefix("purge_").removesuffix(".ack.json")
            try:
                if (not purge_marker.exists()
                        and now - ack.stat().st_mtime > 1):
                    ack.unlink(missing_ok=True)
            except OSError:
                pass
        pending = sorted(requests.glob("w_*.json"))
        try:
            pending_bytes = sum(path.stat().st_size for path in pending)
        except OSError:
            pending_bytes = MAX_PENDING_BYTES + 1
        if len(pending) > MAX_PENDING_FILES or pending_bytes > MAX_PENDING_BYTES:
            print("cara-worker spool quota exceeded; refusing new work", flush=True)
            if once:
                return 0
            time.sleep(1)
            continue
        handled = 0
        for path in pending[:20]:
            if (results / path.name).exists():
                continue
            try:
                handled += int(process_one(path, results, cancel, scratch))
            except Exception as exc:
                print(f"cara-worker failed {path.name}: {exc}", flush=True)
        if once:
            return handled
        time.sleep(0.2)


if __name__ == "__main__":
    raise SystemExit(run(
        os.environ.get("CARA_WORKER_SPOOL", "/var/lib/cara-worker/spool"),
        os.environ.get("CARA_WORKER_STATE", "/var/lib/cara-worker"),
        os.environ.get("CARA_WORKER_ONCE") == "1",
    ))
