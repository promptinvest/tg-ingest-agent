"""Allowlisted private facts export and root-owned local installation; no transport."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

import fleet_facts as facts

PROJECTION_SCHEMA = "cara-fleet-facts-projection/v1"
REGISTER_SCHEMA = "cara-fleet-source-register/v1"
MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024


def _fields(value, names):
    if not isinstance(value, dict) or set(value) != set(names):
        raise ValueError("invalid private facts projection/register fields")


def _local_path(value):
    if not isinstance(value, (str, Path)):
        raise ValueError("local absolute path required")
    path = Path(value)
    if not path.is_absolute() or str(value).startswith(("\\\\", "//")):
        raise ValueError("local absolute path required")
    return path


def _inputs(projection, register, allow_fixture=False):
    _fields(projection, {"schema", "reviewed", "artifact"})
    _fields(register, {"schema", "sources"})
    if projection["schema"] != PROJECTION_SCHEMA or projection["reviewed"] is not True:
        raise ValueError("explicit reviewed facts projection required")
    if register["schema"] != REGISTER_SCHEMA or not isinstance(register["sources"], list):
        raise ValueError("invalid private source register")
    if not isinstance(projection["artifact"], dict) or "integrity" in projection["artifact"]:
        raise ValueError("projection must contain an unsigned normalized artifact")
    artifact = facts.seal(projection["artifact"])
    facts.validate(artifact, allow_fixture=allow_fixture)
    source_map = {source["id"]: source for source in artifact["sources"]}
    registered = {}
    for source in register["sources"]:
        _fields(source, {"id", "path", "sha256", "checked_at"})
        identity = source["id"]
        if not isinstance(identity, str) or identity not in source_map or identity in registered:
            raise ValueError("unbound or duplicate private source register entry")
        if any(source[field] != source_map[identity][field] for field in ("sha256", "checked_at")):
            raise ValueError("source manifest/register binding mismatch")
        registered[identity] = _local_path(source["path"])
    if set(registered) != set(source_map):
        raise ValueError("every retained source version requires a registered file")
    return artifact, registered


def _fingerprint(path, budget):
    facts._no_symlinks(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        bound = max(0, min(MAX_SOURCE_BYTES, budget[0]))
        if not stat.S_ISREG(before.st_mode) or before.st_size > bound:
            raise ValueError("source file unavailable or exceeds hash limits")
        digest = hashlib.sha256()
        count = 0
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            while True:
                chunk = stream.read(min(65536, bound + 1 - count))
                if not chunk:
                    break
                count += len(chunk)
                budget[0] -= len(chunk)
                if count > bound:
                    raise ValueError("source file exceeds hash limits")
                digest.update(chunk)
        after = os.fstat(descriptor)
        identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
        if identity(before) != identity(after) or count != before.st_size:
            raise ValueError("source changed during verification")
        return digest.hexdigest(), count
    finally:
        os.close(descriptor)


def _status(artifact, registered):
    checks = []
    budget = [MAX_TOTAL_BYTES]
    for source in artifact["sources"]:
        try:
            digest, _count = _fingerprint(registered[source["id"]], budget)
            state = "unchanged" if digest == source["sha256"] else "changed"
        except (OSError, ValueError):
            state = "unavailable"
        checks.append({"source_id": source["id"], "state": state,
                       "checked_at": source["checked_at"]})
    pending = [item["source_id"] for item in checks if item["state"] != "unchanged"]
    return {"revision": artifact["revision"], "ready": not pending,
            "pending_source_changes": pending, "sources": checks}


def source_status(projection, register, allow_fixture=False):
    """Read current source bytes without copying content or inventing evidence dates."""
    artifact, registered = _inputs(projection, register, allow_fixture=allow_fixture)
    return _status(artifact, registered)


def export(projection, register, allow_fixture=False):
    """Reproducible allowlisted export; all dates remain explicit reviewed inputs.

    Historical source versions need retained files in the private register. A
    changed source is pending review, never an automatic update to a known fact.
    """
    artifact, registered = _inputs(projection, register, allow_fixture=allow_fixture)
    if not _status(artifact, registered)["ready"]:
        raise ValueError("pending source changes require reviewed projection update")
    return artifact


def _trusted_path(value, directory=False):
    """Every existing component must be root-owned, non-writable by other users."""
    path = _local_path(value)
    chain = list(reversed(path.parents)) + [path]
    for component in chain:
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("untrusted operator configuration path")
        if component != path or directory:
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError("invalid operator configuration directory")
        elif not stat.S_ISREG(info.st_mode):
            raise ValueError("invalid operator configuration file")
    return path


def install(projection_path, register_path, directory):
    """Host operator entry point: root-owned channel, then forward-only publish.

    No boolean or command-line flag grants authentication. The installer must
    provision the destination before invoking this function; no schedule is added.
    """
    if os.name != "posix" or not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise ValueError("root-owned local operator channel required")
    projection_path = _trusted_path(projection_path)
    register_path = _trusted_path(register_path)
    directory = _trusted_path(directory, directory=True)
    projection, register = facts._read(projection_path), facts._read(register_path)
    artifact, registered = _inputs(projection, register)
    for path in registered.values():
        _trusted_path(path)
    if not _status(artifact, registered)["ready"]:
        raise ValueError("pending source changes require reviewed projection update")
    return facts.publish(artifact, directory, authenticated=True)


def install_export(artifact_path, directory):
    """Install only the sealed export delivered through the trusted local channel.

    Local source hashing/review happens before authenticated operator transfer.
    No private register or raw KB file needs to leave the workstation. Integrity
    is a content binding; root-owned channel policy supplies authentication.
    """
    if os.name != "posix" or not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise ValueError("root-owned local operator channel required")
    artifact_path = _trusted_path(artifact_path)
    directory = _trusted_path(directory, directory=True)
    artifact = facts.load(artifact_path)
    return facts.publish(artifact, directory, authenticated=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("status", "export", "install", "install-export"))
    parser.add_argument("--projection")
    parser.add_argument("--register")
    parser.add_argument("--artifact")
    parser.add_argument("--output")
    parser.add_argument("--directory")
    args = parser.parse_args(argv)
    try:
        if args.operation == "install-export":
            if args.projection or args.register or args.output or not args.artifact or not args.directory:
                raise ValueError("install-export requires only artifact and destination")
            receipt = install_export(args.artifact, args.directory)
            result = {"installed": True, "revision": receipt["revision"],
                      "previous_revision": receipt["previous_revision"]}
        elif not args.projection or not args.register or args.artifact:
            raise ValueError("operation requires local projection and source register")
        elif args.operation == "install":
            if args.output or not args.directory:
                raise ValueError("install requires only its provisioned destination")
            receipt = install(args.projection, args.register, args.directory)
            result = {"installed": True, "revision": receipt["revision"],
                      "previous_revision": receipt["previous_revision"]}
        else:
            if args.directory or (args.operation == "status" and args.output):
                raise ValueError("unexpected output/destination option")
            projection, register = facts._read(args.projection), facts._read(args.register)
            if args.operation == "status":
                result = source_status(projection, register)
                print(json.dumps(result, sort_keys=True))
                return 0 if result["ready"] else 3
            if not args.output:
                raise ValueError("export requires an output file")
            artifact = export(projection, register)
            facts._write_new(_local_path(args.output), facts.canonical(artifact).encode("utf-8"))
            result = {"exported": True, "revision": artifact["revision"]}
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError):
        # Private input paths, source bytes and secret-bearing parser exceptions
        # must not become a console/log attachment.
        print("fleet facts operation refused: invalid input, pending source changes, or unavailable operator channel", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
