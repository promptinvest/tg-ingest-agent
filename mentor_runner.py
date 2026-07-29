#!/usr/bin/env python3
"""Networkless candidate-patch runner for Cara Mentor.

Candidate code executes only inside this service's private state tree. The
service has no inference key, Telegram token, deploy key, production DB/media
access, or network namespace. A passing result is a bound test artifact, never
permission to merge, push, install, or deploy.
"""
import hashlib
import json
import os
import re
import resource
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from pathlib import Path

import mentor_protocol as protocol


MAX_PENDING_FILES = 20
MAX_PENDING_BYTES = 2 * 1024 * 1024
MAX_CAPTURE_BYTES = 2 * 1024 * 1024


def _config():
    return {
        "spool": Path(
            os.environ.get("MENTOR_RUNNER_SPOOL")
            or "/var/lib/cara-mentor-runner/spool"),
        "state": Path(
            os.environ.get("MENTOR_RUNNER_STATE")
            or "/var/lib/cara-mentor-runner"),
        "source": Path(
            os.environ.get("MENTOR_SOURCE_DIR")
            or "/opt/cara-mentor-source"),
        "timeout": max(120, min(
            int(os.environ.get("MENTOR_TEST_TIMEOUT_SECONDS") or "600"), 1200)),
    }


def _load_request(path):
    value = protocol.read_regular_json(path, protocol.MAX_RUNNER_REQUEST_BYTES)
    required = {
        "version", "job_id", "nonce", "cycle_uid", "patch", "patch_hash",
        "target_files", "source_build", "source_hash", "proposed_change_hash",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("runner request schema is invalid")
    if (value["version"] != protocol.PROTOCOL_VERSION
            or not protocol.valid_job_id(value["job_id"], "runner")
            or value["nonce"] != value["job_id"][7:]):
        raise ValueError("runner request identity is invalid")
    targets = protocol.validate_target_files(value["target_files"])
    patch = protocol.validate_patch(value["patch"], targets)
    if protocol.digest(patch) != value["patch_hash"]:
        raise ValueError("runner patch hash mismatch")
    protocol.safe_text(value["cycle_uid"], 80)
    protocol.safe_text(value["source_build"], 100)
    protocol.safe_text(value["source_hash"], 64)
    protocol.safe_text(value["proposed_change_hash"], 64)
    return value


def _safe_env(root):
    home = Path(root) / ".home"
    home.mkdir(mode=0o700)
    # The suite must never fall back to live /var/lib paths.  A number of
    # integration tests intentionally call load_config() with only the option
    # under test overridden; give every persistent/spool default a private,
    # runner-owned equivalent so isolation denials do not make the suite
    # non-hermetic.
    db = Path(root) / "tg-ingest-agent" / "ingest.db"
    media = Path(root) / "tg-ingest-agent" / "media"
    artifacts = Path(root) / "tg-ingest-agent" / "task-artifacts"
    worker_spool = Path(root) / "cara-worker" / "spool"
    review_spool = Path(root) / "cara-mentor" / "spool"
    runner_spool = Path(root) / "cara-mentor-runner" / "spool"
    for directory in (
            db.parent, media, artifacts, worker_spool,
            review_spool, runner_spool):
        directory.mkdir(parents=True, exist_ok=True)
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "NO_PROXY": "*",
        "no_proxy": "*",
        "CARA_TEST_RUNTIME_ROOT": str(Path(root) / "test-runtime"),
        "DB_PATH": str(db),
        "MEDIA_DIR": str(media),
        "TASK_ARTIFACTS_DIR": str(artifacts),
        "TASK_WORKER_SPOOL": str(worker_spool),
        "MENTOR_REVIEW_SPOOL": str(review_spool),
        "MENTOR_RUNNER_SPOOL": str(runner_spool),
    }


def _limits():
    resource.setrlimit(resource.RLIMIT_CPU, (540, 540))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))


def _run(argv, *, cwd, env, timeout, candidate=False):
    started = time.monotonic()
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        preexec_fn=_limits if candidate else None,
    )
    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        output, _ = proc.communicate()
        raise TimeoutError(f"candidate command timed out: {argv[0]}")
    finally:
        # A test that backgrounds a child must not survive a successful parent.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if len(output) > MAX_CAPTURE_BYTES:
        output = output[-MAX_CAPTURE_BYTES:]
    return proc.returncode, output.decode("utf-8", errors="replace"), (
        time.monotonic() - started)


def _tests_summary(output):
    ran = re.findall(r"^Ran \d+ tests? in [^\r\n]+$", output, re.MULTILINE)
    outcome = re.findall(
        r"^(?:OK(?: \(skipped=\d+\))?|FAILED \(.*\))$",
        output,
        re.MULTILINE,
    )
    if not ran or not outcome:
        return "test output did not contain a complete unittest summary"
    summary = ran[-1] + "; " + outcome[-1]
    if outcome[-1].startswith("FAILED"):
        cases = re.findall(
            r"^(?:FAIL|ERROR): ([^\r\n]+)$", output, re.MULTILINE)
        if cases:
            summary += "; cases=" + ", ".join(cases[:5])
    return protocol.safe_text(summary[:300], 300)


def _copy_source(source, target):
    def ignore(path, names):
        ignored = {
            name for name in names
            if name in {"__pycache__", ".git"} or name.endswith(".pyc")
        }
        return ignored
    shutil.copytree(source, target, symlinks=False, ignore=ignore)
    for path in target.rglob("*"):
        meta = path.lstat()
        if stat.S_ISLNK(meta.st_mode):
            raise ValueError("Mentor source snapshot contains a symlink")


def _candidate(cfg, request):
    build = (cfg["source"] / "VERSION").read_text(encoding="utf-8").strip()
    source_hash = (
        cfg["source"] / "SOURCE_HASH").read_text(encoding="utf-8").strip()
    if build != request["source_build"] or source_hash != request["source_hash"]:
        raise ValueError("runner source snapshot changed")
    scratch = Path(tempfile.mkdtemp(
        prefix=request["job_id"] + "-", dir=cfg["state"] / "scratch"))
    started = time.monotonic()
    try:
        work = scratch / "source"
        _copy_source(cfg["source"], work)
        patch_file = scratch / "candidate.patch"
        patch_file.write_text(request["patch"], encoding="utf-8")
        env = _safe_env(scratch)
        code, output, _ = _run(
            ["/usr/bin/git", "apply", "--check", str(patch_file)],
            cwd=work, env=env, timeout=30)
        if code:
            return {
                "status": "failed",
                "tests_summary": "patch rejected by git apply --check",
                "branch": "",
                "commit": "",
                "duration_seconds": round(time.monotonic() - started, 3),
                "error": "candidate patch did not apply",
            }
        code, output, _ = _run(
            ["/usr/bin/git", "apply", str(patch_file)],
            cwd=work, env=env, timeout=30)
        if code:
            raise RuntimeError("candidate patch apply failed after check")
        code, output, _ = _run(
            ["/usr/bin/python3", "-m", "compileall", "-q", "."],
            cwd=work, env=env, timeout=120, candidate=True)
        if code:
            return {
                "status": "failed",
                "tests_summary": "candidate compileall failed",
                "branch": "",
                "commit": "",
                "duration_seconds": round(time.monotonic() - started, 3),
                "error": "candidate failed syntax compilation",
            }
        code, output, _ = _run(
            ["/usr/bin/python3", "-m", "unittest", "discover", "-p", "test_*.py"],
            cwd=work, env=env, timeout=cfg["timeout"], candidate=True)
        summary = _tests_summary(output)
        if code:
            return {
                "status": "failed",
                "tests_summary": summary,
                "branch": "",
                "commit": "",
                "duration_seconds": round(time.monotonic() - started, 3),
                "error": "candidate full discovery suite failed; output_digest="
                + hashlib.sha256(output.encode("utf-8")).hexdigest(),
            }
        branch = "mentor/" + request["cycle_uid"]
        commands = (
            ["/usr/bin/git", "init", "-q"],
            ["/usr/bin/git", "config", "user.name", "Cara Mentor"],
            ["/usr/bin/git", "config", "user.email", "mentor@localhost"],
            ["/usr/bin/git", "checkout", "-q", "-b", branch],
            ["/usr/bin/git", "add", "--"] + sorted(protocol.PATCHABLE_FILES),
            ["/usr/bin/git", "commit", "-q", "-m", "mentor candidate"],
        )
        for argv in commands:
            code, git_output, _ = _run(
                argv, cwd=work, env=env, timeout=60)
            if code:
                raise RuntimeError(
                    f"candidate branch creation failed: {Path(argv[0]).name}")
        code, commit, _ = _run(
            ["/usr/bin/git", "rev-parse", "HEAD"],
            cwd=work, env=env, timeout=30)
        if code or not re.fullmatch(r"[0-9a-f]{40}\s*", commit):
            raise RuntimeError("candidate commit identity is invalid")
        return {
            "status": "passed",
            "tests_summary": summary,
            "branch": branch,
            "commit": commit.strip(),
            "duration_seconds": round(time.monotonic() - started, 3),
            "error": None,
        }
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _result(request, candidate):
    payload = {
        key: candidate[key]
        for key in (
            "status", "tests_summary", "branch", "commit",
            "duration_seconds", "error",
        )
    }
    value = {
        key: request[key]
        for key in (
            "version", "job_id", "nonce", "cycle_uid", "patch_hash",
            "source_build", "source_hash", "proposed_change_hash",
        )
    }
    value.update(payload, result_hash=protocol.digest(payload))
    return value


def process_one(cfg, request_path, results):
    request = None
    try:
        request = _load_request(request_path)
        candidate = _candidate(cfg, request)
    except Exception as exc:
        if request is None:
            print(f"mentor-runner rejected {request_path.name}: {exc}", flush=True)
            return False
        candidate = {
            "status": "failed",
            "tests_summary": "runner rejected candidate",
            "branch": "",
            "commit": "",
            "duration_seconds": 0.0,
            "error": protocol.safe_text(
                f"{type(exc).__name__}: {exc}"[:300], 300),
        }
    protocol.atomic_publish(results, request_path.name, _result(request, candidate))
    return True


def run(once=False):
    cfg = _config()
    requests = cfg["spool"] / "requests"
    results = cfg["spool"] / "results"
    scratch = cfg["state"] / "scratch"
    for path in (requests, results, scratch):
        path.mkdir(parents=True, exist_ok=True)
    for path in scratch.iterdir():
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
    while True:
        now = time.time()
        for result in results.glob("runner_*.json"):
            request = requests / result.name
            try:
                if not request.exists() and now - result.stat().st_mtime > 1:
                    result.unlink(missing_ok=True)
            except OSError:
                pass
        pending = sorted(requests.glob("runner_*.json"))
        try:
            size = sum(item.stat().st_size for item in pending)
        except OSError:
            size = MAX_PENDING_BYTES + 1
        if len(pending) > MAX_PENDING_FILES or size > MAX_PENDING_BYTES:
            print("mentor-runner spool quota exceeded", flush=True)
            pending = []
        handled = 0
        for path in pending[:1]:
            if (results / path.name).exists():
                continue
            handled += int(process_one(cfg, path, results))
        if once:
            return handled
        time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(run(os.environ.get("MENTOR_RUNNER_ONCE") == "1"))
