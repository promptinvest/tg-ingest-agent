#!/usr/bin/env python3
"""Cara-side one-way clients for the Mentor reviewer and candidate runner."""
import json
from pathlib import Path

import mentor_protocol as protocol
import tasking


MAX_PENDING_FILES = 20
MAX_PENDING_BYTES = 2 * 1024 * 1024


class MentorUnavailable(RuntimeError):
    pass


def _spool(root):
    root = Path(root)
    return root / "requests", root / "results"


def _quota(directory):
    try:
        pending = list(Path(directory).glob("*.json"))
        if (len(pending) >= MAX_PENDING_FILES
                or sum(item.stat().st_size for item in pending) >= MAX_PENDING_BYTES):
            raise MentorUnavailable("mentor spool quota is exhausted")
    except OSError as exc:
        raise MentorUnavailable(f"mentor spool quota check failed: {exc}") from exc


def _redacted(value):
    # The task redactor rejects recognizable secrets and truncates derived text.
    # Round-trip JSON so no custom object can cross the service boundary.
    return json.loads(tasking.redact_derived_text(protocol.canonical(value)))


def build_review_request(*, evidence, source_build, source_hash, job_id=None):
    job_id = job_id or protocol.new_job_id("review")
    if not protocol.valid_job_id(job_id, "review"):
        raise MentorUnavailable("mentor review job id is invalid")
    nonce = job_id.removeprefix("review_")
    safe_evidence = _redacted(evidence)
    evidence_hash = protocol.digest(safe_evidence)
    request = {
        "version": protocol.PROTOCOL_VERSION,
        "job_id": job_id,
        "nonce": nonce,
        "evidence": safe_evidence,
        "evidence_hash": evidence_hash,
        "source_build": protocol.safe_text(str(source_build), 100),
        "source_hash": protocol.safe_text(str(source_hash), 64),
    }
    body = protocol.canonical(request).encode("utf-8")
    if len(body) > protocol.MAX_REVIEW_REQUEST_BYTES:
        raise MentorUnavailable("mentor review request is too large")
    return request


def publish_review(cfg, request):
    requests, _ = _spool(cfg.mentor_review_spool)
    _quota(requests)
    job_id = request["job_id"]
    target = requests / f"{job_id}.json"
    if target.exists():
        return {
            "job_id": job_id,
            "nonce": request["nonce"],
            "evidence_hash": request["evidence_hash"],
        }
    try:
        protocol.atomic_publish(requests, f"{job_id}.json", request)
    except OSError as exc:
        raise MentorUnavailable(f"mentor review submit failed: {exc}") from exc
    return {
        "job_id": job_id,
        "nonce": request["nonce"],
        "evidence_hash": request["evidence_hash"],
    }


def submit_review(cfg, *, evidence, source_build, source_hash):
    return publish_review(cfg, build_review_request(
        evidence=evidence, source_build=source_build, source_hash=source_hash))


def poll_review(cfg, *, job_id, nonce, evidence_hash, source_build, source_hash):
    if not protocol.valid_job_id(job_id, "review") or nonce != job_id[7:]:
        raise MentorUnavailable("mentor review binding is invalid")
    _, results = _spool(cfg.mentor_review_spool)
    try:
        value = protocol.read_regular_json(
            results / f"{job_id}.json", protocol.MAX_REVIEW_RESULT_BYTES)
    except OSError as exc:
        raise MentorUnavailable(f"mentor review result unavailable: {exc}") from exc
    if value is None:
        return None
    required = {
        "version", "job_id", "nonce", "evidence_hash", "source_build",
        "source_hash", "status", "proposal", "candidate", "error",
        "result_hash",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise MentorUnavailable("mentor review result schema is invalid")
    for key, expected in (
        ("version", protocol.PROTOCOL_VERSION),
        ("job_id", job_id),
        ("nonce", nonce),
        ("evidence_hash", evidence_hash),
        ("source_build", str(source_build)),
        ("source_hash", str(source_hash)),
    ):
        if value.get(key) != expected:
            raise MentorUnavailable(f"mentor review binding mismatch: {key}")
    payload = {
        "status": value["status"],
        "proposal": value["proposal"],
        "candidate": value["candidate"],
        "error": value["error"],
    }
    if value["result_hash"] != protocol.digest(payload):
        raise MentorUnavailable("mentor review result hash mismatch")
    if value["status"] != "ok":
        raise MentorUnavailable(
            protocol.safe_text(str(value["error"] or "mentor review failed"), 300))
    proposal = protocol.validate_proposal(value["proposal"])
    candidate = value["candidate"]
    if proposal["target_files"]:
        if not isinstance(candidate, dict) or set(candidate) != {
                "patch", "patch_hash", "target_files"}:
            raise MentorUnavailable("mentor candidate is missing")
        targets = protocol.validate_target_files(candidate["target_files"])
        if targets != proposal["target_files"]:
            raise MentorUnavailable("mentor candidate targets changed after proposal")
        patch = protocol.validate_patch(candidate["patch"], targets)
        if candidate["patch_hash"] != protocol.digest(patch):
            raise MentorUnavailable("mentor candidate patch hash mismatch")
        candidate = {
            "patch": patch,
            "patch_hash": candidate["patch_hash"],
            "target_files": targets,
        }
    elif candidate is not None:
        raise MentorUnavailable("proposal-only review returned an executable candidate")
    return {"proposal": proposal, "candidate": candidate}


def build_runner_request(*, cycle_uid, patch, patch_hash, target_files,
                         source_build, source_hash, proposed_change_hash,
                         job_id=None):
    targets = protocol.validate_target_files(target_files)
    patch = protocol.validate_patch(patch, targets)
    if protocol.digest(patch) != patch_hash:
        raise MentorUnavailable("runner patch binding is invalid")
    job_id = job_id or protocol.new_job_id("runner")
    if not protocol.valid_job_id(job_id, "runner"):
        raise MentorUnavailable("mentor runner job id is invalid")
    nonce = job_id.removeprefix("runner_")
    request = {
        "version": protocol.PROTOCOL_VERSION,
        "job_id": job_id,
        "nonce": nonce,
        "cycle_uid": protocol.safe_text(cycle_uid, 80),
        "patch": patch,
        "patch_hash": patch_hash,
        "target_files": targets,
        "source_build": protocol.safe_text(str(source_build), 100),
        "source_hash": protocol.safe_text(str(source_hash), 64),
        "proposed_change_hash": protocol.safe_text(proposed_change_hash, 64),
    }
    body = protocol.canonical(request).encode("utf-8")
    if len(body) > protocol.MAX_RUNNER_REQUEST_BYTES:
        raise MentorUnavailable("mentor runner request is too large")
    return request


def publish_runner(cfg, request):
    requests, _ = _spool(cfg.mentor_runner_spool)
    _quota(requests)
    job_id = request["job_id"]
    target = requests / f"{job_id}.json"
    if target.exists():
        return {"job_id": job_id, "nonce": request["nonce"]}
    try:
        protocol.atomic_publish(requests, f"{job_id}.json", request)
    except OSError as exc:
        raise MentorUnavailable(f"mentor runner submit failed: {exc}") from exc
    return {"job_id": job_id, "nonce": request["nonce"]}


def submit_runner(cfg, *, cycle_uid, patch, patch_hash, target_files,
                  source_build, source_hash, proposed_change_hash):
    return publish_runner(cfg, build_runner_request(
        cycle_uid=cycle_uid, patch=patch, patch_hash=patch_hash,
        target_files=target_files, source_build=source_build,
        source_hash=source_hash,
        proposed_change_hash=proposed_change_hash))


def poll_runner(cfg, *, job_id, nonce, cycle_uid, patch_hash,
                source_build, source_hash, proposed_change_hash):
    if not protocol.valid_job_id(job_id, "runner") or nonce != job_id[7:]:
        raise MentorUnavailable("mentor runner binding is invalid")
    _, results = _spool(cfg.mentor_runner_spool)
    try:
        value = protocol.read_regular_json(
            results / f"{job_id}.json", protocol.MAX_RUNNER_RESULT_BYTES)
    except OSError as exc:
        raise MentorUnavailable(f"mentor runner result unavailable: {exc}") from exc
    if value is None:
        return None
    required = {
        "version", "job_id", "nonce", "cycle_uid", "patch_hash",
        "source_build", "source_hash", "proposed_change_hash", "status",
        "tests_summary", "branch", "commit", "duration_seconds", "error",
        "result_hash",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise MentorUnavailable("mentor runner result schema is invalid")
    for key, expected in (
        ("version", protocol.PROTOCOL_VERSION),
        ("job_id", job_id),
        ("nonce", nonce),
        ("cycle_uid", cycle_uid),
        ("patch_hash", patch_hash),
        ("source_build", str(source_build)),
        ("source_hash", str(source_hash)),
        ("proposed_change_hash", proposed_change_hash),
    ):
        if value.get(key) != expected:
            raise MentorUnavailable(f"mentor runner binding mismatch: {key}")
    payload = {
        "status": value["status"],
        "tests_summary": value["tests_summary"],
        "branch": value["branch"],
        "commit": value["commit"],
        "duration_seconds": value["duration_seconds"],
        "error": value["error"],
    }
    if value["result_hash"] != protocol.digest(payload):
        raise MentorUnavailable("mentor runner result hash mismatch")
    return payload


def acknowledge(root, job_id):
    requests, _ = _spool(root)
    try:
        (requests / f"{job_id}.json").unlink(missing_ok=True)
        return True
    except OSError:
        return False


def abandon_all_requests(root, expected_tail):
    """Drop only Cara-owned Mentor requests after the committed scope=all purge."""
    requests, _ = _spool(root)
    tail = tuple(requests.parts[-len(expected_tail):])
    if tail != tuple(expected_tail) or requests.is_symlink():
        raise MentorUnavailable("refused unsafe Mentor request purge root")
    if not requests.exists():
        return True
    for path in requests.iterdir():
        if path.is_dir() or path.is_symlink() or path.suffix != ".json":
            raise MentorUnavailable("unexpected Mentor request spool entry")
        path.unlink()
    return not any(requests.iterdir())
