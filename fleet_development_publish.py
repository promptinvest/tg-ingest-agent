"""Manual fixed-repository development export and authenticated local installation.

No network client, credentials, scheduler, model or application write capability
lives here. The operator supplies bounded GitHub GET captures from the existing
connector. Digests bind that reviewed input; they are not GitHub signatures or a
caller authentication flag. Only the protected root-owned install channel grants
publication authority. Raw responses are discarded before export.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import uuid

import fleet_project_adapters as adapters
import fleet_project_publish as protected

POLICY_SCHEMA = "cara-fleet-development-publisher-policy/v1"
CAPTURE_SCHEMA = "cara-fleet-github-capture/v1"
EXPORT_SCHEMA = "cara-fleet-development-export/v1"
DEFAULT_POLICY = "/etc/cara-fleet-development-publisher.json"
MAX_CAPTURE_BYTES = 512 * 1024
MAX_RESPONSE_BYTES = 128 * 1024
MAX_POLICY_BYTES = 16384
MAX_CAPTURE_SECONDS = 120
SHA = re.compile(r"[a-f0-9]{64}\Z")
REVISION = re.compile(r"[a-f0-9]{40}\Z")


class PublicationError(ValueError):
    """Invalid or unavailable evidence; do not replace the last good source."""


class PublicationAmbiguous(PublicationError):
    """Atomic replacement happened but durable confirmation failed; inspect it."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _fields(value, names):
    if not isinstance(value, dict) or set(value) != set(names):
        raise PublicationError("unsupported development publication fields")


def _mapping(value):
    if not isinstance(value, dict):
        raise PublicationError("GitHub source object required")
    return value


def _hash(value):
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise PublicationError("invalid development source digest")
    return value


def _keys(config):
    return {"repository", "branch_before", "commit", "actions", "branch_after"} | {
        "issue-" + str(number) for number in config["issue_numbers"]}


def policy(value):
    """Capture-specific operator policy, including approved source/revision hashes."""
    _fields(value, {"schema", "project_id", "source_id", "repository", "ref", "revision",
                    "capture_sha256", "source_sha256s", "output_path", "max_age_seconds", "issue_numbers"})
    if value["schema"] != POLICY_SCHEMA:
        raise PublicationError("unsupported development publisher policy")
    for key in ("project_id", "source_id"):
        adapters._identifier(value[key])
    if not isinstance(value["repository"], str) or not adapters.REPOSITORY.fullmatch(value["repository"]):
        raise PublicationError("invalid fixed public repository")
    if not isinstance(value["ref"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value["ref"]) or ".." in value["ref"]:
        raise PublicationError("fixed simple branch reference required")
    if not isinstance(value["revision"], str) or not REVISION.fullmatch(value["revision"]):
        raise PublicationError("fixed GitHub revision required")
    _hash(value["capture_sha256"])
    numbers = value["issue_numbers"]
    if (not isinstance(numbers, list) or not 1 <= len(numbers) <= 12
            or any(type(number) is not int or not 1 <= number <= 2**31-1 for number in numbers)
            or numbers != sorted(set(numbers))):
        raise PublicationError("fixed ordered issue scope required")
    if not isinstance(value["source_sha256s"], dict) or set(value["source_sha256s"]) != _keys(value):
        raise PublicationError("every fixed capture source requires a digest")
    for item in value["source_sha256s"].values():
        _hash(item)
    path = value["output_path"]
    if (not isinstance(path, str) or not PurePosixPath(path).is_absolute() or path.startswith("//")
            or ".." in PurePosixPath(path).parts or len(path) > 512 or re.search(r"[\x00-\x1f\x7f]", path)):
        raise PublicationError("fixed local Linux output path required")
    adapters._integer(value["max_age_seconds"], 1, 31 * 86400)
    return json.loads(canonical(value))


def _record(record, url, key, config, exported):
    _fields(record, {"url", "captured_at", "sha256", "value"})
    if record["url"] != url or not isinstance(record["value"], dict):
        raise PublicationError("fixed GitHub GET source mismatch")
    captured = adapters._date(record["captured_at"], exported, exported)
    if len(canonical(record["value"])) > MAX_RESPONSE_BYTES:
        raise PublicationError("GitHub capture response exceeds bound")
    if digest(record["value"]) != _hash(record["sha256"]) or record["sha256"] != config["source_sha256s"][key]:
        raise PublicationError("GitHub capture source hash mismatch")
    return record["value"], captured


def export_capture(capture, config, exported_at=None):
    """Pure bounded projection; usable in the operator workspace on any platform."""
    config = policy(config)
    _fields(capture, {"schema", "project_id", "source_id", "repository", "branch_before",
                      "commit", "actions", "issues", "branch_after"})
    if len(canonical(capture)) > MAX_CAPTURE_BYTES or digest(capture) != config["capture_sha256"]:
        raise PublicationError("capture digest or bound mismatch")
    if (capture["schema"] != CAPTURE_SCHEMA or capture["project_id"] != config["project_id"]
            or capture["source_id"] != config["source_id"]):
        raise PublicationError("capture project/source mismatch")
    exported_at = exported_at or datetime.now(timezone.utc).isoformat()
    exported = adapters._time(exported_at)
    base = "https://api.github.com/repos/" + config["repository"]
    records = {}
    def read(key, entry, suffix):
        value, timestamp = _record(entry, base + suffix, key, config, exported)
        records[key] = {"sha256": entry["sha256"], "captured_at": entry["captured_at"]}
        return value, timestamp
    repository, repository_at = read("repository", capture["repository"], "")
    if repository.get("full_name") != config["repository"] or repository.get("private") is not False or repository.get("url") != base:
        raise PublicationError("exact approved public repository required")
    before, before_at = read("branch_before", capture["branch_before"], "/branches/" + config["ref"])
    after, after_at = read("branch_after", capture["branch_after"], "/branches/" + config["ref"])
    for branch in (before, after):
        if branch.get("name") != config["ref"] or _mapping(branch.get("commit")).get("sha") != config["revision"]:
            raise PublicationError("branch changed or revision differs from policy")
    if not repository_at <= before_at <= after_at or (after_at-repository_at).total_seconds() > MAX_CAPTURE_SECONDS:
        raise PublicationError("capture ordering/window invalid")
    commit, commit_at = read("commit", capture["commit"], "/git/commits/" + config["revision"])
    if commit.get("sha") != config["revision"] or commit.get("url") != base + "/git/commits/" + config["revision"]:
        raise PublicationError("fixed commit source mismatch")
    commit_date = _mapping(commit.get("committer")).get("date")
    adapters._date(commit_date, commit_at, exported)
    actions, actions_at = read("actions", capture["actions"],
        "/actions/runs?head_sha=" + config["revision"] + "&per_page=6&page=1")
    rows, total = actions.get("workflow_runs"), actions.get("total_count")
    if (not isinstance(rows, list) or len(rows) > 6 or type(total) is not int
            or not len(rows) <= total <= 2**53-1):
        raise PublicationError("bounded workflow run collection required")
    runs = []
    for row in rows:
        if (not isinstance(row, dict) or not {"id", "head_sha", "status", "conclusion", "run_started_at", "updated_at", "repository"} <= set(row)
                or row.get("head_sha") != config["revision"]
                or _mapping(row.get("repository")).get("full_name") != config["repository"]):
            raise PublicationError("workflow run repository/revision mismatch")
        runs.append({"run_id": row.get("id"), "revision": row.get("head_sha"),
            "status": row.get("status"), "conclusion": row.get("conclusion"),
            "started_at": row.get("run_started_at"), "updated_at": row.get("updated_at")})
    issues = capture["issues"]
    if not isinstance(issues, list) or len(issues) != len(config["issue_numbers"]):
        raise PublicationError("exact fixed issue captures required")
    issue_rows, issue_dates = [], []
    for entry, number in zip(issues, config["issue_numbers"]):
        item, timestamp = read("issue-" + str(number), entry, "/issues/" + str(number))
        if (type(item.get("number")) is not int or item["number"] != number
                or item.get("repository_url") != base or "pull_request" in item):
            raise PublicationError("fixed issue identity mismatch")
        issue_rows.append({"number": number, "title": item.get("title"), "state": item.get("state"),
                           "updated_at": item.get("updated_at")})
        issue_dates.append(timestamp)
    for key, record in records.items():
        if key != "repository" and not before_at <= adapters._time(record["captured_at"]) <= after_at:
            raise PublicationError("source capture falls outside stable branch window")
    document = {"schema": adapters.DEVELOPMENT_ACTIONS_SCHEMA, "project_id": config["project_id"],
        "source_id": config["source_id"], "repository": config["repository"], "exported_at": exported_at,
        "repository_state": {"status": "recorded", "captured_at": capture["branch_after"]["captured_at"],
            "revision": config["revision"], "last_commit_at": commit_date},
        "ci": {"source_format": adapters.ACTIONS_SOURCE, "status": "recorded",
            "captured_at": capture["actions"]["captured_at"], "items": runs, "complete": False},
        "issues": {"status": "recorded", "captured_at": max(issue_dates).isoformat(),
                   "items": issue_rows, "complete": False}}
    adapters.development_actions_snapshot(document, config["project_id"], config["source_id"],
                                         config["repository"], exported_at, config["max_age_seconds"])
    provenance = {"policy_sha256": digest(config), "capture_sha256": config["capture_sha256"],
                  "repository": config["repository"], "ref": config["ref"], "revision": config["revision"],
                  "sources": records}
    artifact = {"schema": EXPORT_SCHEMA, "document": document, "provenance": provenance}
    artifact["integrity"] = digest(artifact)
    if len(canonical(artifact)) > adapters.MAX_SOURCE_BYTES:
        raise PublicationError("sanitized development export exceeds bound")
    return artifact


def validate_export(artifact, config):
    config = policy(config)
    _fields(artifact, {"schema", "document", "provenance", "integrity"})
    if (artifact["schema"] != EXPORT_SCHEMA or len(canonical(artifact)) > adapters.MAX_SOURCE_BYTES
            or _hash(artifact["integrity"]) != digest({key: value for key, value in artifact.items() if key != "integrity"})):
        raise PublicationError("development export integrity mismatch")
    source = artifact["provenance"]
    _fields(source, {"policy_sha256", "capture_sha256", "repository", "ref", "revision", "sources"})
    for key in ("repository", "ref", "revision", "capture_sha256"):
        if source[key] != config[key]:
            raise PublicationError("development export policy/source mismatch")
    if source["policy_sha256"] != digest(config) or not isinstance(source["sources"], dict) or set(source["sources"]) != _keys(config):
        raise PublicationError("development export policy digest mismatch")
    document = artifact["document"]
    _fields(document, {"schema", "project_id", "source_id", "repository", "exported_at", "repository_state", "ci", "issues"})
    projected = adapters.development_actions_snapshot(document, config["project_id"], config["source_id"],
                              config["repository"], document["exported_at"], config["max_age_seconds"])
    if (projected["repository"].get("revision") != config["revision"] or set(item["number"] for item in document["issues"]["items"]) != set(config["issue_numbers"])
            or document["ci"]["status"] != "recorded" or document["issues"]["status"] != "recorded"
            or document["ci"]["complete"] is not False or document["issues"]["complete"] is not False):
        raise PublicationError("development export revision/coverage mismatch")
    dates = {}
    for key, record in source["sources"].items():
        _fields(record, {"sha256", "captured_at"})
        if _hash(record["sha256"]) != config["source_sha256s"][key]:
            raise PublicationError("development export source hash mismatch")
        dates[key] = adapters._date(record["captured_at"], adapters._time(document["exported_at"]), adapters._time(document["exported_at"]))
    if (not dates["repository"] <= dates["branch_before"] <= dates["branch_after"]
            or (dates["branch_after"]-dates["repository"]).total_seconds() > MAX_CAPTURE_SECONDS
            or any(not dates["branch_before"] <= value <= dates["branch_after"] for key,value in dates.items() if key != "repository")
            or projected["repository"]["captured_at"] != source["sources"]["branch_after"]["captured_at"]
            or document["ci"]["captured_at"] != source["sources"]["actions"]["captured_at"]
            or adapters._time(document["issues"]["captured_at"]) != max(dates["issue-"+str(number)] for number in config["issue_numbers"])):
        raise PublicationError("development source capture date mismatch")
    if any(item["revision"] != config["revision"] for item in document["ci"]["items"]):
        raise PublicationError("CI does not belong to the captured head")
    return document


def _read_local(path, maximum):
    path = Path(path)
    if path.is_symlink():
        raise PublicationError("regular local input required")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > maximum:
            raise PublicationError("bounded regular local input required")
        raw = bytearray()
        while len(raw) <= maximum:
            chunk = os.read(descriptor, min(8192, maximum + 1-len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        after = os.fstat(descriptor)
        if len(raw) != before.st_size or (before.st_dev,before.st_ino,before.st_mtime_ns) != (after.st_dev,after.st_ino,after.st_mtime_ns):
            raise PublicationError("local input changed during read")
        return protected._json(bytes(raw), maximum)
    finally:
        os.close(descriptor)


def install_export(policy_path, artifact_path):
    """Only root-owned inputs install; hash checks never replace authentication."""
    if os.name != "posix" or not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise PublicationError("root local operator channel required")
    config = policy(protected._read_protected(policy_path, MAX_POLICY_BYTES))
    artifact = protected._read_protected(artifact_path, adapters.MAX_SOURCE_BYTES)
    document = validate_export(artifact, config)
    # Current reading time cannot refresh the capture's historical dates.
    observed = datetime.now(timezone.utc).isoformat()
    adapters.development_actions_snapshot(document, config["project_id"], config["source_id"],
                                         config["repository"], observed, config["max_age_seconds"])
    output = Path(config["output_path"])
    if output in {Path(policy_path), Path(artifact_path)}:
        raise PublicationError("publication must not replace operator inputs")
    _, parent = protected._protected(output.parent, directory=True)
    import fcntl
    directory = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary = "." + output.name + "." + uuid.uuid4().hex + ".pending"
    replaced = False
    try:
        if protected._identity(os.fstat(directory)) != protected._identity(parent):
            raise PublicationError("publication parent changed")
        try:
            fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PublicationError("publication already active") from None
        previous_info = None
        try:
            _, previous_info = protected._protected(output)
            previous = protected._read_protected(output, adapters.MAX_SOURCE_BYTES)
            adapters.development_actions_snapshot(previous, config["project_id"], config["source_id"],
                config["repository"], previous["exported_at"], config["max_age_seconds"])
        except FileNotFoundError:
            previous = None
        if previous is not None:
            for key in ("repository_state", "ci", "issues"):
                if adapters._time(document[key]["captured_at"]) < adapters._time(previous[key]["captured_at"]):
                    raise PublicationError("source capture cannot move backwards")
            incoming, prior = adapters._time(document["exported_at"]), adapters._time(previous["exported_at"])
            if incoming < prior or incoming == prior and document != previous:
                raise PublicationError("publication date rollback/conflict")
        mode, group = (stat.S_IMODE(previous_info.st_mode), previous_info.st_gid) if previous_info is not None else (0o600, 0)
        if mode & 0o7111:
            raise PublicationError("publication must be a non-executable data file")
        body = canonical(document) + b"\n"
        receipt = {"schema": "cara-fleet-development-publication/v1", "project_id": config["project_id"],
            "source_id": config["source_id"], "repository": config["repository"], "revision": config["revision"],
            "exported_at": document["exported_at"], "document_sha256": digest(document),
            "file_sha256": hashlib.sha256(body).hexdigest(), "export_integrity": artifact["integrity"],
            "capture_sha256": config["capture_sha256"], "policy_sha256": digest(config)}
        if previous == document:
            return dict(receipt, status="unchanged")
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        with os.fdopen(descriptor, "wb") as handle:
            os.fchown(handle.fileno(), 0, group); os.fchmod(handle.fileno(), mode)
            handle.write(body); handle.flush(); os.fsync(handle.fileno())
        if protected._read_protected(output.parent / temporary, adapters.MAX_SOURCE_BYTES) != document:
            raise PublicationError("publication write verification failed")
        _, current_parent = protected._protected(output.parent, directory=True)
        if protected._identity(current_parent) != protected._identity(parent):
            raise PublicationError("publication parent changed")
        try:
            current = protected._read_protected(output, adapters.MAX_SOURCE_BYTES)
        except FileNotFoundError:
            current = None
        if current != previous:
            raise PublicationError("last good publication changed during install")
        if previous_info is not None:
            _, current_info = protected._protected(output)
            if protected._identity(current_info) != protected._identity(previous_info):
                raise PublicationError("last good publication metadata changed")
        os.replace(temporary, output.name, src_dir_fd=directory, dst_dir_fd=directory)
        replaced = True
        os.fsync(directory)
        return dict(receipt, status="installed", installed_at=observed)
    except OSError:
        if replaced:
            raise PublicationAmbiguous("replacement requires durable confirmation") from None
        raise
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass
        os.close(directory)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Manual fixed public GitHub development evidence")
    parser.add_argument("operation", choices=("export", "install-export"))
    parser.add_argument("--policy", default=DEFAULT_POLICY)
    parser.add_argument("--capture")
    parser.add_argument("--artifact")
    args = parser.parse_args(argv)
    try:
        if args.operation == "export":
            if not args.capture or args.artifact:
                raise PublicationError("export requires only a capture")
            result = export_capture(_read_local(args.capture, MAX_CAPTURE_BYTES), _read_local(args.policy, MAX_POLICY_BYTES))
        else:
            if not args.artifact or args.capture:
                raise PublicationError("install-export requires only an artifact")
            result = install_export(args.policy, args.artifact)
    except PublicationAmbiguous:
        print('{"status":"ambiguous","action":"inspect_current_publication"}', file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        print('{"status":"unavailable"}', file=sys.stderr)
        return 1
    print(canonical(result).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
