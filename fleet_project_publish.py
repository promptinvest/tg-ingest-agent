"""Manual root-operator refresh of sanitized project backup evidence.

This is an observation publisher, not a backup runner or application skill. It
reads only the existing maintenance jobs table through a bounded read-only
SQLite connection. A root-owned policy fixes every resource and identity; no
model input, credentials, job errors, payloads, archive contents or commands are
accepted. The existing adapter validates the complete source before installation.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import time
import uuid

import fleet_project_adapters as adapters

POLICY_SCHEMA = "cara-fleet-backup-publisher-policy/v1"
DEFAULT_POLICY = "/etc/cara-fleet-backup-publisher.json"
MAX_POLICY_BYTES = 16384
MAX_RESULT_BYTES = 8192
READ_TIMEOUT_SECONDS = 1.0
ACTIONS = ("db_backup", "backup_verify")
COLUMNS = frozenset({"id", "skill", "action", "status", "created_at", "finished_at", "result"})
FUNCTIONS = frozenset({"substr"})


class PublicationError(ValueError):
    """Unavailable/invalid source; callers must retain the existing publication."""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _unique(items):
    result = {}
    for key, value in items:
        if key in result:
            raise PublicationError("duplicate source field")
        result[key] = value
    return result


def _json(raw, maximum):
    if not isinstance(raw, bytes) or len(raw) > maximum:
        raise PublicationError("source exceeds input bound")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
            parse_constant=lambda _value: (_ for _ in ()).throw(PublicationError("nonfinite source number")))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise PublicationError("invalid source JSON") from None


def _path(value):
    if not isinstance(value, (str, Path)):
        raise PublicationError("fixed local absolute path required")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or str(value).startswith(("//", "\\\\")):
        raise PublicationError("fixed local absolute path required")
    return path


def policy(value):
    fields = {"schema", "database_path", "database_uid", "output_path", "project_id", "source_id", "max_age_seconds"}
    if not isinstance(value, dict) or set(value) != fields or value["schema"] != POLICY_SCHEMA:
        raise PublicationError("invalid fixed publisher policy")
    if type(value["database_uid"]) is not int or not 0 <= value["database_uid"] <= 2**31 - 1:
        raise PublicationError("invalid database owner policy")
    for field in ("project_id", "source_id"):
        if not isinstance(value[field], str) or not adapters.ID.fullmatch(value[field]):
            raise PublicationError("invalid fixed source identity")
    if type(value["max_age_seconds"]) is not int or not 1 <= value["max_age_seconds"] <= 31 * 86400:
        raise PublicationError("invalid evidence age policy")
    database, output = _path(value["database_path"]), _path(value["output_path"])
    if database == output or output in (Path(str(database) + "-wal"), Path(str(database) + "-shm"), Path(str(database) + "-journal")):
        raise PublicationError("source and publication must differ")
    return dict(value, database_path=str(database), output_path=str(output))


def _protected(path, *, directory=False, source_uid=None):
    """No symlinks, special files, hard links or unrelated writable ancestors."""
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        raise PublicationError("protected local operator channel unavailable")
    path = _path(path)
    owners = {0} if source_uid is None else {0, source_uid}
    for component in (*reversed(path.parents), path):
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid not in owners or info.st_mode & 0o022:
            raise PublicationError("untrusted fixed resource ownership")
        if component != path or directory:
            if not stat.S_ISDIR(info.st_mode):
                raise PublicationError("invalid fixed resource directory")
        elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise PublicationError("invalid fixed resource file")
    return path, info


def _identity(info):
    return info.st_dev, info.st_ino, info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode), info.st_nlink


def _sidecars(database, owner):
    result = {}
    for suffix in ("-wal", "-shm", "-journal"):
        path = Path(str(database) + suffix)
        try:
            _path_value, info = _protected(path, source_uid=owner)
        except FileNotFoundError:
            continue
        if info.st_uid != owner:
            raise PublicationError("database sidecar owner differs")
        result[suffix] = _identity(info)
    return result


def _authorize(action, table, column, database, trigger):
    if trigger is not None:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_SELECT:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ:
        allowed = (table == "jobs" and column in COLUMNS) or (table == "sqlite_master" and column in {"name", "type", "sql"})
        return sqlite3.SQLITE_OK if database == "main" and allowed else sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION:
        return sqlite3.SQLITE_OK if (column or table) in FUNCTIONS else sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_TRANSACTION and table in {"BEGIN", "ROLLBACK"}:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _compare_dates(left, right):
    # SQLite date functions round to milliseconds; jobs timestamps retain Python
    # microseconds. This fixed collation preserves exact evidence ordering and
    # rejects malformed timestamps encountered while selecting the latest rows.
    left_date, right_date = adapters._time(left), adapters._time(right)
    return (left_date > right_date) - (left_date < right_date)

def _row(row):
    # IDs are used only to de-duplicate the same selected job, never published.
    if type(row["id"]) is not int or not 1 <= row["id"] <= 2**63 - 1:
        raise PublicationError("invalid maintenance job identity")
    action, status = row["action"], row["status"]
    if action not in ACTIONS or status not in {"done", "failed", "pending", "claimed"}:
        raise PublicationError("invalid maintenance job state")
    result = None
    if status == "done":
        raw = row["result"]
        if not isinstance(raw, str):
            raise PublicationError("completed job lacks result")
        result = _json(raw.encode("utf-8"), MAX_RESULT_BYTES)
        if not isinstance(result, dict):
            raise PublicationError("invalid maintenance result")
        # backup.offsite returns spaces:<object key>. Only its transport kind is
        # evidence here; the entire key is discarded before adapter validation.
        if action == "db_backup" and isinstance(result.get("offsite"), str):
            offsite = result["offsite"]
            if offsite.startswith("spaces:") and len(offsite) > len("spaces:"):
                result["offsite"] = "spaces"
            elif offsite == "spaces":
                raise PublicationError("unsupported original transport result")
    elif status in {"pending", "claimed"} and row["finished_at"] is not None:
        raise PublicationError("unfinished job has completion date")
    # Retry-pending jobs retain an error in jobs.py; we never select or publish
    # it, and never manufacture a finished date from claimed_at/available_at.
    return {"action": action, "status": status, "created_at": row["created_at"],
            "finished_at": row["finished_at"], "result": result}


def collect(config, exported_at=None):
    """One coherent WAL-aware snapshot; missing/invalid queries are NOT emptiness.

    Empty, valid jobs data becomes explicit no_success_recorded via the adapter.
    Missing database/table, malformed rows and deadline failures raise, allowing
    the caller to preserve its last good publication. No immutable=1 is used.
    """
    config = policy(config)
    database, before = _protected(config["database_path"], source_uid=config["database_uid"])
    if before.st_uid != config["database_uid"]:
        raise PublicationError("database owner differs from policy")
    sidecars = _sidecars(database, config["database_uid"])
    descriptor = os.open(database, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    connection = None
    expires = time.monotonic() + READ_TIMEOUT_SECONDS
    try:
        if _identity(os.fstat(descriptor)) != _identity(before):
            raise PublicationError("database changed before open")
        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=0.05)
        connection.row_factory = sqlite3.Row
        if hasattr(connection, "enable_load_extension"):
            connection.enable_load_extension(False)
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        if connection.execute("PRAGMA query_only").fetchone()[0] != 1 or connection.execute("PRAGMA trusted_schema").fetchone()[0] != 0:
            raise PublicationError("read-only database gates unavailable")
        if hasattr(connection, "setlimit"):
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, adapters.MAX_SOURCE_BYTES)
            connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 4096)
        connection.create_collation("cara_utc", _compare_dates)
        connection.set_progress_handler(lambda: int(time.monotonic() >= expires), 100)
        connection.set_authorizer(_authorize)
        connection.execute("BEGIN")
        schema = connection.execute("SELECT type,sql FROM sqlite_schema WHERE name='jobs'").fetchall()
        if len(schema) != 1 or schema[0]["type"] != "table" or not isinstance(schema[0]["sql"], str) or not re.match(r"\s*CREATE\s+TABLE\s", schema[0]["sql"], re.I):
            raise PublicationError("ordinary maintenance jobs table required")
        selected = {}
        for action in ACTIONS:
            for success in (False, True):
                field = "finished_at" if success else "created_at"
                # Missing dates sort first; the fixed collation rejects invalid
                # dates instead of hiding a prior bad row. All SQL is fixed.
                sql = ("SELECT id,action,status,created_at,finished_at,"
                       "CASE WHEN status='done' THEN substr(result,1,?) ELSE NULL END AS result "
                       "FROM jobs WHERE skill='maintenance' AND action=?" +
                       (" AND status='done'" if success else "") +
                       " ORDER BY CASE WHEN " + field + " IS NULL THEN 1 ELSE 0 END DESC,"
                       + field + " COLLATE cara_utc DESC,id DESC LIMIT 1")
                row = connection.execute(sql, (MAX_RESULT_BYTES + 1, action)).fetchone()
                if row is not None:
                    selected[row["id"]] = _row(row)
        if time.monotonic() >= expires:
            raise PublicationError("maintenance read exceeded deadline")
        _after_path, after = _protected(database, source_uid=config["database_uid"])
        current_sidecars = _sidecars(database, config["database_uid"])
        if _identity(before) != _identity(after) or _identity(os.fstat(descriptor)) != _identity(before) or any(suffix in current_sidecars and current_sidecars[suffix] != identity for suffix, identity in sidecars.items()):
            raise PublicationError("database identity changed during read")
        exported_at = exported_at or datetime.now(timezone.utc).isoformat()
        document = {"schema": adapters.BACKUP_SCHEMA, "project_id": config["project_id"], "source_id": config["source_id"],
                    "source_format": adapters.BACKUP_SOURCE, "exported_at": exported_at,
                    "jobs": [selected[key] for key in sorted(selected)]}
        adapters.backup_receipt(document, config["project_id"], config["source_id"], exported_at, config["max_age_seconds"])
        if len(canonical(document)) > adapters.MAX_SOURCE_BYTES:
            raise PublicationError("sanitized maintenance snapshot exceeds bound")
        return document
    except (sqlite3.Error, OSError, ValueError, TypeError, RecursionError, OverflowError):
        raise PublicationError("maintenance observation unavailable") from None
    finally:
        if connection is not None:
            connection.close()
        os.close(descriptor)


def _read_protected(path, maximum):
    path, before = _protected(path)
    if before.st_size > maximum:
        raise PublicationError("protected document exceeds bound")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if _identity(os.fstat(descriptor)) != _identity(before):
            raise PublicationError("protected document changed before read")
        raw = bytearray()
        while len(raw) <= maximum:
            chunk = os.read(descriptor, min(8192, maximum + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        after = os.fstat(descriptor)
        if len(raw) != before.st_size or _identity(after) != _identity(before) or after.st_mtime_ns != before.st_mtime_ns:
            raise PublicationError("protected document changed during read")
        return _json(bytes(raw), maximum)
    finally:
        os.close(descriptor)


def _sync(directory):
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def publish(policy_path=DEFAULT_POLICY):
    """Authenticated manual refresh; no caller boolean grants root authority."""
    if os.name != "posix" or not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise PublicationError("root local operator channel required")
    config = policy(_read_protected(policy_path, MAX_POLICY_BYTES))
    output = _path(config["output_path"])
    _directory, baseline = _protected(output.parent, directory=True)
    # Lock the existing protected directory inode, not a replaceable output or
    # new lock-file ledger. Overlapping operator refreshes fail without writes.
    import fcntl
    directory_fd = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if _identity(os.fstat(directory_fd)) != _identity(baseline):
            raise PublicationError("publication directory changed before lock")
        try:
            fcntl.flock(directory_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PublicationError("publication refresh already active") from None
        return _publish_locked(config, output)
    finally:
        os.close(directory_fd)


def _publish_locked(config, output):
    try:
        _protected(output)
        previous = _read_protected(output, adapters.MAX_SOURCE_BYTES)
        adapters.backup_receipt(previous, config["project_id"], config["source_id"], previous["exported_at"], config["max_age_seconds"])
    except FileNotFoundError:
        previous = None
    document = collect(config)
    if previous is not None and adapters._time(document["exported_at"]) < adapters._time(previous["exported_at"]):
        raise PublicationError("publication capture cannot move backwards")
    body = canonical(document) + b"\n"
    temporary = output.with_name("." + output.name + "." + uuid.uuid4().hex + ".pending")
    descriptor = None
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.fchown(descriptor, 0, 0)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(body); stream.flush(); os.fsync(descriptor)
        os.close(descriptor); descriptor = None
        if _read_protected(temporary, adapters.MAX_SOURCE_BYTES) != document:
            raise PublicationError("publication write verification failed")
        _protected(output.parent, directory=True)
        try:
            current = _read_protected(output, adapters.MAX_SOURCE_BYTES)
        except FileNotFoundError:
            current = None
        if current != previous:
            raise PublicationError("last good publication changed during refresh")
        os.replace(temporary, output)
        _sync(output.parent)
        return {"schema": "cara-fleet-backup-publication/v1", "project_id": config["project_id"], "source_id": config["source_id"],
                "exported_at": document["exported_at"], "receipt_sha256": hashlib.sha256(canonical(document)).hexdigest(),
                "selected_jobs": len(document["jobs"])}
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Manually refresh fixed read-only backup job evidence")
    parser.add_argument("--policy", default=DEFAULT_POLICY)
    args = parser.parse_args(argv)
    try:
        result = publish(args.policy)
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        print('{"status":"unavailable"}', file=sys.stderr)
        return 1
    print(canonical(result).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())