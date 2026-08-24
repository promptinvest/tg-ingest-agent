#!/usr/bin/env python3
"""Shared, strict envelopes for Cara's isolated Mentor components."""
import errno
import hashlib
import json
import os
import re
import secrets
import stat
from pathlib import Path


# Runner v1 remains wire-compatible across this reliability release; proposal
# and candidate inference use the independently versioned v2 envelopes.
PROTOCOL_VERSION = 1
INFERENCE_PROTOCOL_VERSION = 2
MAX_REVIEW_REQUEST_BYTES = 48 * 1024
MAX_REVIEW_RESULT_BYTES = 96 * 1024
MAX_CANDIDATE_REQUEST_BYTES = 64 * 1024
MAX_CANDIDATE_RESULT_BYTES = 64 * 1024
MAX_RUNNER_REQUEST_BYTES = 64 * 1024
MAX_RUNNER_RESULT_BYTES = 16 * 1024
MAX_PATCH_BYTES = 40 * 1024
MAX_PATCH_LINES = 600
MAX_TARGET_FILES = 4

# Mentor may suggest changes anywhere, but automatic candidate construction is
# deliberately limited to behavior modules and their tests. Policy, storage,
# deployment, credentials, worker isolation, and tool-broker files always stay
# proposal-only and require ordinary engineering.
PATCHABLE_CODE_FILES = frozenset({
    "converse.py",
    "hermes.py",
    "ingest.py",
    "knowledge.py",
    "media.py",
    "memory_curator.py",
    "notes_svc.py",
    "proactive.py",
    "relationship.py",
    "reminders.py",
    "reminders_svc.py",
    "review.py",
    "router.py",
    "self_model.py",
    "tasks_svc.py",
    "texts.py",
})
PATCHABLE_TEST_FILES = frozenset({
    "test_mentor_candidates.py",
})
PATCHABLE_FILES = PATCHABLE_CODE_FILES | PATCHABLE_TEST_FILES

PROPOSAL_KINDS = frozenset({"prompt", "routing", "tool", "bug", "policy", "model"})
PROPOSAL_RISKS = frozenset({"low", "medium", "high"})
INFERENCE_ERROR_CODES = frozenset({
    "usage_corrupt", "weekly_cap", "http_transient", "http_permanent",
    "timeout", "transport", "response_too_large", "malformed_response",
    "ambiguous", "validation", "internal",
})
RETRYABLE_INFERENCE_ERROR_CODES = frozenset({
    "weekly_cap", "http_transient", "timeout", "transport",
})


class MentorProtocolError(RuntimeError):
    pass


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    if isinstance(value, bytes):
        body = value
    elif isinstance(value, str):
        body = value.encode("utf-8")
    else:
        body = canonical(value).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def safe_text(value, maximum, *, allow_empty=False):
    if not isinstance(value, str):
        raise MentorProtocolError("text value is invalid")
    text = " ".join(value.split()).strip()
    if len(text) > maximum or (not allow_empty and not text):
        raise MentorProtocolError("text value is invalid")
    return text


def new_job_id(prefix):
    if prefix not in {"review", "candidate", "runner"}:
        raise ValueError("invalid mentor job prefix")
    return f"{prefix}_{secrets.token_hex(16)}"


def valid_job_id(value, prefix):
    return bool(re.fullmatch(fr"{re.escape(prefix)}_[0-9a-f]{{32}}", str(value or "")))


def atomic_publish(directory, filename, value):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    body = canonical(value).encode("utf-8")
    temp = directory / f".{filename}.{os.getpid()}.{secrets.token_hex(5)}.tmp"
    flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
             | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
    fd = os.open(temp, flags, 0o640)
    try:
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
        os.replace(temp, directory / filename)
        fsync_dir(directory)
    except BaseException:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def fsync_dir(path):
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def read_regular_json(path, maximum):
    path = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise MentorProtocolError("spool entry is a symlink") from exc
        raise
    try:
        meta = os.fstat(fd)
        if (not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1
                or meta.st_size <= 0 or meta.st_size > maximum):
            raise MentorProtocolError("spool entry size/type is invalid")
        raw = os.read(fd, meta.st_size + 1)
        if len(raw) != meta.st_size:
            raise MentorProtocolError("spool entry changed while read")
    finally:
        os.close(fd)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise MentorProtocolError("spool entry is malformed") from exc


def validate_target_files(value):
    if (not isinstance(value, list)
            or not 2 <= len(value) <= MAX_TARGET_FILES
            or len(set(value)) != len(value)):
        raise MentorProtocolError("candidate target_files are invalid")
    targets = []
    for item in value:
        if not isinstance(item, str) or item not in PATCHABLE_FILES:
            raise MentorProtocolError("candidate target file is not allowlisted")
        targets.append(item)
    if not any(item in PATCHABLE_CODE_FILES for item in targets):
        raise MentorProtocolError("candidate must change an allowlisted code file")
    if not any(item in PATCHABLE_TEST_FILES for item in targets):
        raise MentorProtocolError("candidate must include an adversarial test file")
    return targets


def validate_patch(patch, target_files):
    if not isinstance(patch, str):
        raise MentorProtocolError("candidate patch is invalid")
    patch = patch.replace("\r\n", "\n")
    if (not patch.strip() or len(patch.encode("utf-8")) > MAX_PATCH_BYTES
            or "\x00" in patch or len(patch.splitlines()) > MAX_PATCH_LINES):
        raise MentorProtocolError("candidate patch is too large")
    lowered = patch.casefold()
    forbidden = (
        "new file mode", "deleted file mode", "rename from", "rename to",
        "binary files", "git binary patch", "submodule",
    )
    if any(marker in lowered for marker in forbidden):
        raise MentorProtocolError("candidate patch changes file topology")
    targets = set(validate_target_files(target_files))
    seen = []
    for line in patch.splitlines():
        match = re.fullmatch(r"diff --git a/([^ ]+) b/([^ ]+)", line)
        if not match:
            continue
        left, right = match.groups()
        if (left != right or left not in targets or "\\" in left
                or left.startswith("/") or ".." in Path(left).parts):
            raise MentorProtocolError("candidate patch path is invalid")
        seen.append(left)
    if set(seen) != targets or len(seen) != len(targets):
        raise MentorProtocolError("candidate patch targets do not match its binding")
    for line in patch.splitlines():
        if line.startswith("--- ") and line[4:] not in {f"a/{item}" for item in targets}:
            raise MentorProtocolError("candidate old path is invalid")
        if line.startswith("+++ ") and line[4:] not in {f"b/{item}" for item in targets}:
            raise MentorProtocolError("candidate new path is invalid")
    added = "\n".join(
        line[1:] for line in patch.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ).casefold()
    dangerous = (
        "import os", "from os import", "import pathlib", "from pathlib import",
        "import shutil", "from shutil import", "import subprocess",
        "from subprocess import", "import socket", "from socket import",
        "import urllib", "from urllib import", "import http.client",
        "import requests", "import ctypes", "import pickle", "import marshal",
        "os.", "pathlib.", "shutil.", "subprocess.", "socket.", "urlopen(",
        "open(", "eval(", "exec(", "__import__(", "compile(",
        "telegram_bot_token", "do_model_access_key", "fleet_notify_",
        "/etc/", "/root/", "/var/lib/",
    )
    if any(token in added for token in dangerous):
        raise MentorProtocolError(
            "candidate patch adds filesystem, process, network, or secret access")
    return patch


def validate_proposal(value):
    required = {
        "kind", "hypothesis", "proposed_change", "risk", "rollback",
        "target_files",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise MentorProtocolError("mentor proposal schema is invalid")
    if value["kind"] not in PROPOSAL_KINDS or value["risk"] not in PROPOSAL_RISKS:
        raise MentorProtocolError("mentor proposal kind/risk is invalid")
    proposal = {
        "kind": value["kind"],
        "hypothesis": safe_text(value["hypothesis"], 1200),
        "proposed_change": safe_text(value["proposed_change"], 3000),
        "risk": value["risk"],
        "rollback": safe_text(value["rollback"], 1200),
        "target_files": [],
    }
    targets = value["target_files"]
    if value["risk"] == "high" or value["kind"] in {"policy", "model", "tool"}:
        if targets not in ([], None):
            raise MentorProtocolError("high-risk proposal cannot request a candidate patch")
    else:
        proposal["target_files"] = validate_target_files(targets)
    return proposal
