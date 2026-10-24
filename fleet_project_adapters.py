"""Fixed, read-only CF-05 projections; configuration is trusted, source text is not.

No network client, shell, log reader, database query or credential discovery lives
here. A host gateway may select a root-owned fixed binding after validating its
project/capability scope. These adapters never create or refresh source receipts.
Cara's backup source is the existing maintenance-job result contract in backup.py
and jobs.py, wrapped by a separately reviewed, sanitized local publisher. The
publisher/account connection and its installation remain separate release work.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from datetime import datetime, timezone

BACKUP_SCHEMA = "cara-fleet-backup-receipt/v1"
DEVELOPMENT_SCHEMA = "cara-fleet-development-snapshot/v1"
BACKUP_SOURCE = "cara-maintenance-jobs/v1"
MAX_SOURCE_BYTES = 65536
MAX_OUTPUT_BYTES = 10000
ID = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
REVISION = re.compile(r"(?:[a-f0-9]{40}|[a-f0-9]{64})\Z")
REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\Z")
GAP_REASONS = frozenset({"not_exposed", "not_connected", "not_configured", "resource_not_found",
    "observation_unavailable", "stale", "no_success_recorded", "latest_attempt_failed",
    "latest_attempt_pending", "latest_backup_not_restore_verified", "partial_coverage", "ci_does_not_cover_head"})
ADAPTERS = {
    "daily_lingo_http": {"implementation_version": "project-daily-lingo-http/v1",
        "outputs": ["http_status", "health", "worker_progress", "gaps"], "source": "application-http",
        "maximum_source_bytes": 8192, "maximum_bytes": MAX_OUTPUT_BYTES},
    "backups": {"implementation_version": "project-backup-receipt/v1",
        "outputs": ["source_id", "receipt_sha256", "exported_at", "max_age_seconds", "backup", "restore", "latest_attempts", "gaps"],
        "source": BACKUP_SOURCE, "maximum_source_bytes": MAX_SOURCE_BYTES, "maximum_bytes": MAX_OUTPUT_BYTES},
    "development": {"implementation_version": "project-development-snapshot/v1",
        "outputs": ["source_id", "receipt_sha256", "exported_at", "max_age_seconds", "repository", "ci", "issues", "gaps"],
        "source": DEVELOPMENT_SCHEMA, "maximum_source_bytes": MAX_SOURCE_BYTES, "maximum_bytes": MAX_OUTPUT_BYTES},
}


def _fields(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError("unsupported project source fields")


def _identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError("invalid project source identifier")
    return value


def _integer(value, lower=0, upper=2**53-1):
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError("invalid project source number")
    return value


def _enum(value, allowed):
    if not isinstance(value, str) or value not in allowed:
        raise ValueError("invalid project source state")
    return value


def _time(value):
    if not isinstance(value, str) or len(value) > 35 or not (value.endswith("Z") or value.endswith("+00:00")):
        raise ValueError("UTC project evidence date required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("invalid project evidence date") from None
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("UTC project evidence date required")
    return parsed


def _bounds(observed_at, max_age_seconds):
    _integer(max_age_seconds, 1, 31 * 86400)
    return _time(observed_at)


def _date(value, exported, now):
    parsed = _time(value)
    if parsed > exported or (parsed - now).total_seconds() > 60:
        raise ValueError("project source date exceeds capture date")
    return parsed


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _bounded(value):
    if len(_canonical(value).encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise ValueError("project projection exceeds output limit")
    return value


def _base(kind, observed_at, status="recorded", reason=None):
    value = {"status": status, "implementation_version": ADAPTERS[kind]["implementation_version"],
             "observed_at": observed_at}
    _time(observed_at)
    if reason is not None:
        value["reason"] = reason
    return value


def daily_lingo_health(body, http_status):
    """Project only fields currently emitted by app/health.py + runtime_health.py.

    Generation, stock, target and budgets are NOT exposed by that endpoint.
    Arbitrary extra fields (including plausible generation/budget values) cannot
    expand this code-owned allowlist. app_env and worker reason text are omitted.
    No inference from container activity, HTTP 200 or process age is added.
    """
    _integer(http_status, 100, 599)
    if not isinstance(body, dict) or len(_canonical(body).encode("utf-8")) > 8192:
        raise ValueError("invalid application health document")
    health, progress = {}, {}
    gaps = {field: "not_exposed" for field in ("generation", "reserves", "target", "day_budget")}
    for field, allowed in (("status", {"ok", "degraded"}), ("database", {"ok", "error"})):
        if field in body:
            health[field] = _enum(body[field], allowed)
        else:
            gaps[field] = "not_exposed"
    workers = body.get("workers", {})
    if not isinstance(workers, dict):
        raise ValueError("invalid application workers")
    for name in ("delivery", "intake"):
        if name not in workers:
            gaps[name] = "not_exposed"
            continue
        worker = workers[name]
        if not isinstance(worker, dict) or "status" not in worker or "seconds_since_progress" not in worker:
            raise ValueError("incomplete application worker")
        health[name] = _enum(worker["status"], {"ok", "starting", "stalled", "failed"})
        progress[name] = _integer(worker["seconds_since_progress"])
    return _bounded({"http_status": http_status, "health": health, "worker_progress": progress, "gaps": gaps})


def _snapshot_header(document, schema, project, source_id, observed_at, max_age_seconds, fields):
    _fields(document, fields)
    _identifier(project); _identifier(source_id)
    if document["schema"] != schema or document["project_id"] != project or document["source_id"] != source_id:
        raise ValueError("project source binding mismatch")
    now = _bounds(observed_at, max_age_seconds)
    exported = _time(document["exported_at"])
    if (exported - now).total_seconds() > 60:
        raise ValueError("project source capture is in the future")
    if len(_canonical(document).encode("utf-8")) > MAX_SOURCE_BYTES:
        raise ValueError("project source exceeds input limit")
    return now, exported


def _archive(value):
    # Existing Cara jobs return basenames, never operator paths. This private
    # matching key is used only to bind restore coverage; it is never returned.
    if not isinstance(value, str) or not re.fullmatch(r"ingest-\d{8}T\d{6}Z\.db\.gz(?:\.enc)?", value):
        raise ValueError("unsupported backup result archive")
    try:
        datetime.strptime(value[7:23], "%Y%m%dT%H%M%SZ")
    except ValueError:
        raise ValueError("invalid backup archive date") from None
    return value.removesuffix(".enc")


def _backup_result(action, result):
    if action == "db_backup":
        _fields(result, {"file", "bytes", "offsite", "offbox_blocked", "encrypted_file"})
        archive = _archive(result["file"])
        _integer(result["bytes"], 1)
        if type(result["offbox_blocked"]) is not bool:
            raise ValueError("invalid backup transport result")
        # Raw object keys/URLs/account identifiers must never enter this source
        # format. The publisher redacts a configured object target to spaces.
        offsite = _enum(result["offsite"], {"", "not-due", "blocked:size", "telegram:fleet", "spaces"})
        if result["encrypted_file"]:
            if _archive(result["encrypted_file"]) != archive or not result["encrypted_file"].endswith(".enc"):
                raise ValueError("backup encryption result does not match archive")
        elif result["encrypted_file"] != "":
            raise ValueError("invalid backup encryption result")
        encrypted = bool(result["encrypted_file"])
        if offsite in {"telegram:fleet", "spaces"} and (not encrypted or result["offbox_blocked"]):
            raise ValueError("inconsistent backup transport result")
        if result["offbox_blocked"] != (offsite == "blocked:size"):
            raise ValueError("inconsistent backup transport blocking result")
        return archive, {"compressed_bytes": result["bytes"], "encrypted": encrypted,
                         "offsite": "delivered" if offsite in {"telegram:fleet", "spaces"} else
                                    "not_due" if offsite == "not-due" else "blocked" if offsite == "blocked:size" else "not_configured"}
    _fields(result, {"file", "encrypted", "bytes", "tables", "integrity"})
    archive = _archive(result["file"])
    if type(result["encrypted"]) is not bool or result["integrity"] != "ok":
        raise ValueError("invalid backup restore result")
    if result["encrypted"] != result["file"].endswith(".enc"):
        raise ValueError("inconsistent backup restore encryption result")
    _integer(result["bytes"], 1); _integer(result["tables"], 1, 4096)
    return archive, {"restored_bytes": result["bytes"], "tables": result["tables"],
                     "encrypted": result["encrypted"], "integrity": "ok"}


def backup_receipt(document, project, source_id, observed_at, max_age_seconds):
    """Read dated job results, retaining failed/newer attempts and restore scope.

    The reviewed publisher selects bounded existing job rows. This parser does
    not read SQLite, inspect archives, run a backup/restore, or prove offsite-key
    recovery. A restore of an older archive cannot verify the newest snapshot.
    """
    now, exported = _snapshot_header(document, BACKUP_SCHEMA, project, source_id, observed_at, max_age_seconds,
        {"schema", "project_id", "source_id", "source_format", "exported_at", "jobs"})
    if document["source_format"] != BACKUP_SOURCE or not isinstance(document["jobs"], list) or len(document["jobs"]) > 8:
        raise ValueError("unsupported backup receipt source")
    groups = {action: [] for action in ("db_backup", "backup_verify")}
    seen = set()
    for row in document["jobs"]:
        _fields(row, {"action", "status", "created_at", "finished_at", "result"})
        action = _enum(row["action"], set(groups))
        status = _enum(row["status"], {"done", "failed", "pending", "claimed"})
        created = _date(row["created_at"], exported, now)
        if (action, created) in seen:
            raise ValueError("ambiguous duplicate backup job")
        seen.add((action, created))
        finished = _date(row["finished_at"], exported, now) if row["finished_at"] is not None else None
        if status in {"done", "failed"}:
            if finished is None or finished < created:
                raise ValueError("backup job lacks a valid completion date")
        elif finished is not None:
            raise ValueError("unfinished backup job has completion date")
        if status == "done":
            archive, value = _backup_result(action, row["result"])
            archive_time = datetime.strptime(archive[7:23], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            if archive_time > finished:
                raise ValueError("backup result predates its archive")
        else:
            if row["result"] is not None:
                raise ValueError("unfinished/failed job carries success result")
            archive, value = None, None
        groups[action].append((created, finished, row, archive, value))
    value = _base("backups", observed_at)
    value.update(source_id=source_id, receipt_sha256=hashlib.sha256(_canonical(document).encode()).hexdigest(),
                 exported_at=document["exported_at"], max_age_seconds=max_age_seconds, backup=None, restore=None, latest_attempts={}, gaps={})
    if (now - exported).total_seconds() > max_age_seconds:
        value["gaps"]["snapshot"] = "stale"
    successes = {}
    for action, field in (("db_backup", "backup"), ("backup_verify", "restore")):
        rows = groups[action]
        if rows:
            latest = max(rows, key=lambda item: item[0])
            row = latest[2]
            value["latest_attempts"][field] = {"status": row["status"], "created_at": row["created_at"], "finished_at": row["finished_at"]}
            if row["status"] != "done":
                value["gaps"][field + "_latest_attempt"] = "latest_attempt_failed" if row["status"] == "failed" else "latest_attempt_pending"
        done = [item for item in rows if item[2]["status"] == "done"]
        if not done:
            value["gaps"][field] = "no_success_recorded"
            continue
        selected = max(done, key=lambda item: item[1])
        successes[field] = selected
        value[field] = {"finished_at": selected[2]["finished_at"], **selected[4]}
        if (now - selected[1]).total_seconds() > max_age_seconds:
            value["gaps"][field] = "stale"
    if value["restore"] is not None:
        match = "backup" in successes and successes["backup"][3] == successes["restore"][3]
        value["restore"]["coverage"] = "latest_recorded_backup" if match else "different_or_unknown_backup"
        if not match:
            value["gaps"]["latest_backup_restore"] = "latest_backup_not_restore_verified"
    elif value["backup"] is not None:
        value["gaps"]["latest_backup_restore"] = "latest_backup_not_restore_verified"
    return _bounded(value)


def _title(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 140 or value != value.strip()
            or re.search(r"[\x00-\x1f\x7f]|(?:https?|file)://|(?:^|\s)[A-Za-z]:[\\/]|(?:^|\s)/(?:etc|root|home|opt|var)/|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?:Bearer\s|(?:token|password|secret|api[_ -]?key)\s*[:=])|(?:gh[pousr]_|github_pat_|sk_live_|AKIA)[A-Za-z0-9_]", value, re.I)):
        raise ValueError("unsafe development title")
    return value


def development_snapshot(document, project, source_id, repository, observed_at, max_age_seconds):
    """Typed repository/CI/backlog observations, never a completion/health verdict.

    Connection/capture states belong to each source. A passing run must cover the
    recorded revision before it can describe that revision. Closed issues and a
    successful CI run alone never establish project completion or deployed code.
    """
    now, exported = _snapshot_header(document, DEVELOPMENT_SCHEMA, project, source_id, observed_at, max_age_seconds,
        {"schema", "project_id", "source_id", "exported_at", "repository", "repository_state", "ci", "issues"})
    if not isinstance(repository, str) or not REPOSITORY.fullmatch(repository) or document["repository"] != repository:
        raise ValueError("development repository binding mismatch")
    value = _base("development", observed_at)
    value.update(source_id=source_id, receipt_sha256=hashlib.sha256(_canonical(document).encode()).hexdigest(),
                 exported_at=document["exported_at"], max_age_seconds=max_age_seconds, repository={"name": repository}, ci={}, issues={}, gaps={})
    if (now - exported).total_seconds() > max_age_seconds:
        value["gaps"]["snapshot"] = "stale"
    head = None
    for key in ("repository", "ci", "issues"):
        source = document["repository_state" if key == "repository" else key]
        required = {"status", "captured_at", "revision", "last_commit_at"} if key == "repository" else {"status", "captured_at", "items", "complete"}
        _fields(source, required)
        state = _enum(source["status"], {"recorded", "not_connected"})
        if state == "not_connected":
            if source["captured_at"] is not None or (key == "repository" and (source["revision"] is not None or source["last_commit_at"] is not None)) or (key != "repository" and (source["items"] != [] or source["complete"] is not False)):
                raise ValueError("unconnected source carries observed data")
            value[key].update(status=state, captured_at=None)
            value["gaps"][key] = "not_connected"
            continue
        captured = _date(source["captured_at"], exported, now)
        if (now - captured).total_seconds() > max_age_seconds:
            value["gaps"][key] = "stale"
        value[key].update(status="recorded", captured_at=source["captured_at"])
        if key == "repository":
            if not isinstance(source["revision"], str) or not REVISION.fullmatch(source["revision"]):
                raise ValueError("invalid repository revision")
            if source["last_commit_at"] is not None:
                _date(source["last_commit_at"], captured, now)
            head = source["revision"]
            value[key].update(revision=head, last_commit_at=source["last_commit_at"])
            continue
        if type(source["complete"]) is not bool or not isinstance(source["items"], list) or len(source["items"]) > (6 if key == "ci" else 12):
            raise ValueError("invalid development coverage")
        items, seen = [], set()
        for item in source["items"]:
            if key == "ci":
                _fields(item, {"run_id", "revision", "status", "conclusion", "started_at", "completed_at"})
                identity = _integer(item["run_id"], 1)
                if not isinstance(item["revision"], str) or not REVISION.fullmatch(item["revision"]):
                    raise ValueError("invalid CI revision")
                _enum(item["status"], {"queued", "in_progress", "completed"})
                if item["status"] == "completed":
                    _enum(item["conclusion"], {"success", "failure", "neutral", "cancelled", "skipped", "timed_out", "action_required", "stale"})
                    if item["completed_at"] is None:
                        raise ValueError("completed CI run has no completion date")
                elif item["conclusion"] is not None or item["completed_at"] is not None:
                    raise ValueError("unfinished CI run carries conclusion")
                started = _date(item["started_at"], captured, now) if item["started_at"] is not None else None
                completed = _date(item["completed_at"], captured, now) if item["completed_at"] is not None else None
                if completed is not None and (started is None or completed < started):
                    raise ValueError("invalid CI execution dates")
            else:
                _fields(item, {"number", "title", "state", "updated_at"})
                identity = _integer(item["number"], 1)
                _title(item["title"]); _enum(item["state"], {"open", "closed"})
                _date(item["updated_at"], captured, now)
            if identity in seen:
                raise ValueError("duplicate development source item")
            seen.add(identity); items.append(copy.deepcopy(item))
        value[key].update(items=items, complete=source["complete"])
        if not source["complete"] and key not in value["gaps"]:
            value["gaps"][key] = "partial_coverage"
    if value["ci"].get("status") == "recorded" and (head is None or not any(item["revision"] == head for item in value["ci"]["items"])):
        value["gaps"]["ci_revision"] = "ci_does_not_cover_head"
    return _bounded(value)


def binding(value, kind):
    """Validate a ROOT configuration entry, never an LLM/tool argument schema."""
    required = {"adapter", "path", "source_id", "max_age_seconds"} | ({"repository"} if kind == "development" else set())
    _fields(value, required)
    if kind not in {"backups", "development"} or value["adapter"] != ADAPTERS[kind]["implementation_version"]:
        raise ValueError("unknown fixed project adapter")
    _identifier(value["source_id"]); _integer(value["max_age_seconds"], 1, 31 * 86400)
    path = value["path"]
    if not isinstance(path, str) or not Path(path).is_absolute() or path.startswith(("//", "\\\\")) or len(path) > 512 or re.search(r"[\x00-\x1f\x7f]", path):
        raise ValueError("fixed local absolute source path required")
    if kind == "development" and (not isinstance(value["repository"], str) or not REPOSITORY.fullmatch(value["repository"])):
        raise ValueError("fixed repository binding required")
    return value


def _read_fixed_json(path):
    """Bounded, root-owned regular file; no FIFOs, symlinks or writable ancestry.

    Descriptor-relative opens bind every checked directory to the actual read.
    A sticky root-owned parent (e.g. /tmp) is acceptable, but every child directory
    and the final file must independently satisfy ownership/mode checks.
    """
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("protected local project channel unavailable")
    path = Path(path)
    if not path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise ValueError("invalid fixed project path")
    descriptors = []
    try:
        descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(descriptor)
        for index, part in enumerate(path.parts[1:]):
            leaf = index == len(path.parts) - 2
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | (0 if leaf else os.O_DIRECTORY)
            descriptor = os.open(part, flags, dir_fd=descriptor)
            descriptors.append(descriptor)
            info = os.fstat(descriptor)
            if info.st_uid != 0 or (info.st_mode & 0o022 and not (not leaf and info.st_mode & stat.S_ISVTX)):
                raise ValueError("untrusted fixed project source")
            if leaf and (not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SOURCE_BYTES):
                raise ValueError("invalid fixed project source file")
        before = os.fstat(descriptor)
        raw = bytearray()
        while len(raw) <= MAX_SOURCE_BYTES:
            chunk = os.read(descriptor, min(8192, MAX_SOURCE_BYTES + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        after = os.fstat(descriptor)
        stamp = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        if len(raw) > MAX_SOURCE_BYTES or len(raw) != before.st_size or stamp(before) != stamp(after):
            raise ValueError("project source changed or exceeded limit")
        def pairs(items):
            value = {}
            for key, item in items:
                if key in value:
                    raise ValueError("duplicate project source key")
                value[key] = item
            return value
        return json.loads(bytes(raw).decode("utf-8"), object_pairs_hook=pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("invalid project source number")))
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def read_bound(kind, config, project, observed_at=None):
    """Read one preselected trusted binding; source failures never expose paths.

    Missing source configuration, missing file and invalid/unavailable content are
    distinct. No fallback can inspect another resource, project or account.
    """
    if kind not in {"backups", "development"}:
        raise ValueError("unsupported fixed project adapter")
    _identifier(project)
    observed_at = observed_at or datetime.now(timezone.utc).isoformat()
    _time(observed_at)
    if config is None:
        return _base(kind, observed_at, "not_configured", "not_configured")
    try:
        policy = binding(config, kind)
        source = _read_fixed_json(policy["path"])
        if kind == "backups":
            return backup_receipt(source, project, policy["source_id"], observed_at, policy["max_age_seconds"])
        return development_snapshot(source, project, policy["source_id"], policy["repository"], observed_at, policy["max_age_seconds"])
    except FileNotFoundError:
        return _base(kind, observed_at, "not_found", "resource_not_found")
    except (OSError, ValueError, TypeError, AttributeError, UnicodeError, RecursionError):
        return _base(kind, observed_at, "unavailable", "observation_unavailable")


def validate_payload(kind, value, observed_at):
    """Shared gateway/receipt payload validator, independent of wire transport.

    The surrounding protocol still validates principal, project scope, capability
    version and current observation age. Receipt hashes bind the canonical JSON
    source document, not arbitrary whitespace in the source file. This function
    never reopens the source and does not treat a content digest as authentication.
    """
    if kind not in ADAPTERS:
        raise ValueError("unknown project adapter payload")
    _fields(value, ADAPTERS[kind]["outputs"])
    now = _time(observed_at)
    if kind == "daily_lingo_http":
        health, progress = value["health"], value["worker_progress"]
        if not isinstance(health, dict) or set(health) - {"status", "database", "delivery", "intake"} or not isinstance(progress, dict) or set(progress) != set(health) & {"delivery", "intake"}:
            raise ValueError("invalid application projection fields")
        body = {field: item for field, item in health.items() if field in {"status", "database"}}
        body["workers"] = {field: {"status": health[field], "seconds_since_progress": progress[field]} for field in progress}
        if daily_lingo_health(body, value["http_status"]) != value:
            raise ValueError("application projection lost a field-specific gap")
        return value
    _identifier(value["source_id"])
    if not isinstance(value["receipt_sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", value["receipt_sha256"]):
        raise ValueError("invalid project receipt digest")
    _integer(value["max_age_seconds"], 1, 31 * 86400)
    exported = _time(value["exported_at"])
    if (exported - now).total_seconds() > 60:
        raise ValueError("project receipt capture is in the future")
    if kind == "development":
        repository = value["repository"]
        if not isinstance(repository, dict) or "name" not in repository:
            raise ValueError("invalid repository projection")
        document = {"schema": DEVELOPMENT_SCHEMA, "project_id": "projection", "source_id": value["source_id"],
                    "exported_at": value["exported_at"], "repository": repository["name"]}
        for key in ("repository", "ci", "issues"):
            source = value[key]
            if not isinstance(source, dict):
                raise ValueError("invalid development projection")
            state = source.get("status")
            keys = {"status", "captured_at"} | ({"name"} if key == "repository" else set())
            if state == "recorded":
                keys |= {"revision", "last_commit_at"} if key == "repository" else {"items", "complete"}
            _fields(source, keys)
            selected = {field: copy.deepcopy(item) for field, item in source.items() if field != "name"}
            if state == "not_connected":
                selected.update({"revision": None, "last_commit_at": None} if key == "repository" else {"items": [], "complete": False})
            document["repository_state" if key == "repository" else key] = selected
        expected = development_snapshot(document, "projection", value["source_id"], repository["name"], observed_at, value["max_age_seconds"])
        fields = {field: expected[field] for field in ADAPTERS[kind]["outputs"]}
        fields["receipt_sha256"] = value["receipt_sha256"]
        if fields != value:
            raise ValueError("development projection changed source meaning or gaps")
        return value
    expected_gaps = {"snapshot": "stale"} if (now - exported).total_seconds() > value["max_age_seconds"] else {}
    latest = value["latest_attempts"]
    if not isinstance(latest, dict) or set(latest) - {"backup", "restore"}:
        raise ValueError("invalid backup attempt coverage")
    for field in ("backup", "restore"):
        if field in latest:
            row = latest[field]
            _fields(row, {"status", "created_at", "finished_at"})
            _enum(row["status"], {"done", "failed", "pending", "claimed"})
            created = _date(row["created_at"], exported, now)
            finished = _date(row["finished_at"], exported, now) if row["finished_at"] is not None else None
            if row["status"] in {"done", "failed"}:
                if finished is None or finished < created:
                    raise ValueError("invalid backup attempt date")
            elif finished is not None:
                raise ValueError("unfinished backup attempt has completion")
            if row["status"] != "done":
                expected_gaps[field + "_latest_attempt"] = "latest_attempt_failed" if row["status"] == "failed" else "latest_attempt_pending"
        item = value[field]
        if item is None:
            if field in latest and latest[field]["status"] == "done":
                raise ValueError("completed backup attempt lost its evidence")
            expected_gaps[field] = "no_success_recorded"
            continue
        if field not in latest:
            raise ValueError("successful backup evidence lost attempt coverage")
        required = {"finished_at", "encrypted", "compressed_bytes", "offsite"} if field == "backup" else {"finished_at", "encrypted", "restored_bytes", "tables", "integrity", "coverage"}
        _fields(item, required)
        if type(item["encrypted"]) is not bool:
            raise ValueError("invalid backup encryption projection")
        completed = _date(item["finished_at"], exported, now)
        _integer(item["compressed_bytes" if field == "backup" else "restored_bytes"], 1)
        if field == "backup":
            _enum(item["offsite"], {"delivered", "not_due", "blocked", "not_configured"})
            if item["offsite"] == "delivered" and item["encrypted"] is not True:
                raise ValueError("unencrypted offsite result")
        else:
            _integer(item["tables"], 1, 4096)
            if item["integrity"] != "ok":
                raise ValueError("unverified restore projection")
            _enum(item["coverage"], {"latest_recorded_backup", "different_or_unknown_backup"})
            if item["coverage"] == "latest_recorded_backup" and value["backup"] is None:
                raise ValueError("restore claims coverage of unknown backup")
        if (now - completed).total_seconds() > value["max_age_seconds"]:
            expected_gaps[field] = "stale"
    if (value["backup"] is not None and value["restore"] is None) or (value["restore"] is not None and value["restore"]["coverage"] != "latest_recorded_backup"):
        expected_gaps["latest_backup_restore"] = "latest_backup_not_restore_verified"
    if value["gaps"] != expected_gaps:
        raise ValueError("backup projection changed evidence gaps")
    return _bounded(value)


def validate_observation(kind, value):
    """Validate one new adapter observation before shared receipt persistence."""
    if kind not in ADAPTERS or not isinstance(value, dict):
        raise ValueError("unknown project adapter observation")
    base = {"status", "implementation_version", "observed_at"}
    if not base <= set(value) or value["implementation_version"] != ADAPTERS[kind]["implementation_version"]:
        raise ValueError("unsupported project adapter observation version")
    _time(value["observed_at"])
    success = "observed" if kind == "daily_lingo_http" else "recorded"
    if value["status"] != success:
        _fields(value, base | {"reason"})
        expected = {"not_configured": "not_configured", "not_found": "resource_not_found", "unavailable": "observation_unavailable"}
        if not isinstance(value["status"], str) or value["status"] not in expected or value["reason"] != expected[value["status"]]:
            raise ValueError("invalid project adapter gap")
        return value
    _fields(value, base | set(ADAPTERS[kind]["outputs"]))
    validate_payload(kind, {field: value[field] for field in ADAPTERS[kind]["outputs"]}, value["observed_at"])
    return _bounded(value)
