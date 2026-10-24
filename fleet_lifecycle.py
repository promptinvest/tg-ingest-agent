"""Fleet content deletion boundary for the existing typed scope=all purge.

Questions, answers, context, approval content, reports, events and transport
copies are purgeable content. Existing budget/preferences, operator config/facts,
readiness, release/checked-backup evidence and consumed effect-token ledgers stay.
Consumed markers remain indefinitely so content deletion cannot authorize replay.
Existing backup rotation/off-box recovery is unchanged; deleting live content does
not recall older backups. No background retention policy is introduced here.

The caller must verify the executor-owned quiescence channel and async accounting
before recording its receipt. Receipt shape or an owner/model boolean alone is
not authentication. Until that integration is present, deletion fails closed.
"""
import copy
import json
import os
from pathlib import Path
import re
import stat

import fleet_protocol as protocol

VERSION = "cara-fleet-content-lifecycle/v1"
PREVIEW_KEYS = frozenset({"fleet_requests", "fleet_events", "fleet_context_bindings", "fleet_transport_copies"})
_JOB = r"w_[0-9]+_[0-9]+_[a-f0-9]{32}"
_CONTENT_FILE = re.compile(r"(?:" + _JOB + r"(?:\.started)?\.json|\.cancel-" + _JOB + r"\.json|\.(?:" + _JOB + r"(?:\.started)?\.json|\.cancel-" + _JOB + r"\.json)\.[0-9]+\.[a-f0-9]{12}\.tmp)\Z")
_COORDINATION_FILE = re.compile(r"(?:\.fleet-queue-ready\.json|\.purge-all\.json|purge_[a-f0-9]{32}\.ack\.json)\Z")


class FleetLifecycleError(ValueError):
    pass


def _nonce(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise FleetLifecycleError("invalid fleet purge nonce")
    return value


def _authorization(conn, nonce=None):
    row = conn.execute("SELECT value FROM kv WHERE key='task_purge_authorization'").fetchone()
    try:
        value = json.loads((row[0] if row else None) or "null")
    except (TypeError, ValueError):
        raise FleetLifecycleError("invalid durable purge authorization") from None
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("phase") not in {"prepared", "db_committed"}:
        raise FleetLifecycleError("invalid durable purge phase")
    _nonce(value.get("nonce"))
    if nonce is not None and value["nonce"] != _nonce(nonce):
        raise FleetLifecycleError("fleet purge authorization nonce mismatch")
    return value


def _save_authorization(conn, value):
    conn.execute("INSERT INTO kv(key,value) VALUES('task_purge_authorization',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (protocol.canonical(value),))


def db_preview(conn):
    """Exact conditional counts; empty legacy databases retain their old preview."""
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    out = {}
    for table in ("fleet_requests", "fleet_events"):
        if table in tables:
            count = conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
            if count:
                out[table] = count
    count = conn.execute("SELECT COUNT(*) FROM kv WHERE key LIKE 'fleet\\_local\\_view:%' ESCAPE '\\'").fetchone()[0]
    if count:
        out["fleet_context_bindings"] = count
    return out


def transport_files(cfg):
    """List only dedicated, no-symlink spool copies; never enter token/config roots.

    Readiness and nonce-only purge coordination are operational residue. Unknown
    entries, links and unexpected directories block the purge rather than being
    silently ignored or removed. No source bytes are loaded during preview.
    """
    configured = getattr(cfg, "fleet_spool", None)
    if not configured:
        return []
    root = Path(configured)
    if not root.is_absolute() or tuple(root.parts[-2:]) != ("cara-fleet", "spool"):
        raise FleetLifecycleError("fleet purge requires its dedicated spool")
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise FleetLifecycleError("fleet purge refuses symlink ancestry")
    copies = []
    for name in ("requests", "results"):
        directory = root / name
        if not directory.exists():
            continue
        if directory.is_symlink() or not directory.is_dir():
            raise FleetLifecycleError("invalid fleet spool directory")
        entries = list(directory.iterdir())
        if len(entries) > 1000:
            raise FleetLifecycleError("fleet purge preview exceeds spool bound")
        for path in entries:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise FleetLifecycleError("unsafe fleet spool entry")
            coordination = path.name
            if coordination.startswith(".") and re.fullmatch(r"\..+\.[0-9]+\.[a-f0-9]{12}\.tmp", coordination):
                coordination = coordination[1:].rsplit(".", 3)[0]
            if _COORDINATION_FILE.fullmatch(coordination):
                continue
            if not _CONTENT_FILE.fullmatch(path.name):
                raise FleetLifecycleError("unrecognized fleet spool entry")
            copies.append(path)
    return sorted(copies)


def preview_external(cfg):
    count = len(transport_files(cfg))
    return {"fleet_transport_copies": count} if count else {}


def preview(conn, cfg):
    return dict(db_preview(conn), **preview_external(cfg))


def _counts(value):
    if not isinstance(value, dict) or set(value) - PREVIEW_KEYS or any(type(count) is not int or count <= 0 for count in value.values()):
        raise FleetLifecycleError("invalid fleet content preview counts")
    return dict(value)


def begin_quiescence(conn, nonce):
    """Persist intent before publishing the executor marker; deletes no content."""
    value = _authorization(conn, nonce)
    if value is None or value["phase"] != "prepared":
        raise FleetLifecycleError("fleet quiescence requires prepared typed purge")
    value["fleet_phase"] = "quiescing"
    _save_authorization(conn, value)
    conn.commit()


def _receipt(value, nonce):
    expected = {"version", "nonce", "executor", "async", "preview", "preview_hash"}
    if not isinstance(value, dict) or set(value) != expected or value["version"] != VERSION or value["nonce"] != _nonce(nonce):
        raise FleetLifecycleError("invalid fleet quiescence receipt")
    counts = _counts(value["preview"])
    if value["preview_hash"] != protocol.digest(counts):
        raise FleetLifecycleError("fleet purge preview binding mismatch")
    executor = value["executor"]
    if (not isinstance(executor, dict) or set(executor) != {"version", "action", "nonce", "status", "implementation_hash", "pid"}
            or executor["version"] != "cara-fleet-queue/v1" or executor["action"] != "purge_content"
            or executor["nonce"] != nonce or executor["status"] != "quiesced"
            or not isinstance(executor["implementation_hash"], str) or not re.fullmatch(r"[a-f0-9]{64}", executor["implementation_hash"])
            or type(executor["pid"]) is not int or executor["pid"] <= 0):
        raise FleetLifecycleError("executor has not acknowledged fleet quiescence")
    if value["async"] != {"status": "settled", "pending_calls": 0}:
        raise FleetLifecycleError("fleet inference accounting is not settled")
    return copy.deepcopy(value)


def record_quiescence(conn, nonce, executor_receipt, async_receipt, confirmed_preview):
    """Record already authenticated receipts; the external channel is caller-owned."""
    authorization = _authorization(conn, nonce)
    if authorization is None or authorization["phase"] != "prepared":
        raise FleetLifecycleError("fleet receipt requires prepared typed purge")
    counts = _counts(confirmed_preview)
    receipt = _receipt({"version": VERSION, "nonce": nonce, "executor": executor_receipt,
                        "async": async_receipt, "preview": counts, "preview_hash": protocol.digest(counts)}, nonce)
    if {key: count for key, count in counts.items() if key != "fleet_transport_copies"} != db_preview(conn):
        raise FleetLifecycleError("fleet content changed; typed preview must be renewed")
    authorization.update(fleet_phase="quiesced", fleet_quiescence=receipt)
    _save_authorization(conn, authorization)
    conn.commit()
    return receipt


def require_quiescence(conn, nonce=None):
    counts = db_preview(conn)
    if nonce is not None:
        _nonce(nonce)
    authorization = _authorization(conn, nonce) if nonce is not None else None
    needed = bool(counts or (authorization and (authorization.get("fleet_phase") or authorization.get("fleet_quiescence"))))
    if not needed:
        return None
    if authorization is None or authorization["phase"] != "prepared" or authorization.get("fleet_phase") != "quiesced":
        raise FleetLifecycleError("fleet content purge requires acknowledged quiescence")
    receipt = _receipt(authorization.get("fleet_quiescence"), authorization["nonce"])
    if {key: count for key, count in receipt["preview"].items() if key != "fleet_transport_copies"} != counts:
        raise FleetLifecycleError("fleet content changed after confirmed preview")
    return receipt


def delete_db_content(conn, nonce=None):
    """Join the caller's transaction; never commit partial all-content deletion."""
    counts = db_preview(conn)
    require_quiescence(conn, nonce)
    if counts.get("fleet_events"):
        conn.execute("DELETE FROM fleet_events")
    if counts.get("fleet_requests"):
        conn.execute("DELETE FROM fleet_requests")
    conn.execute("DELETE FROM kv WHERE key LIKE 'fleet\\_local\\_view:%' ESCAPE '\\'")
    return counts


def committed_authorization(conn, nonce):
    value = _authorization(conn, nonce)
    if value is None:
        return {"nonce": _nonce(nonce), "phase": "db_committed"}
    if value.get("fleet_quiescence"):
        _receipt(value["fleet_quiescence"], nonce)
        value["fleet_phase"] = "db_committed"
    value["phase"] = "db_committed"
    return value


def needs_external_cleanup(conn, nonce=None):
    value = _authorization(conn, nonce)
    return bool(value and (value.get("fleet_phase") or value.get("fleet_quiescence")))


def admission_paused(conn):
    value = _authorization(conn)
    return bool(value and value["phase"] in {"prepared", "db_committed"})


def settle_async_calls(agent):
    """Nonblocking owner-thread accounting; never drop a dispatched live handle."""
    calls = getattr(agent, "_fleet_inference_calls", {})
    for identity, call in list(calls.items()):
        call.cancel()
        if call.advance():
            calls.pop(identity, None)
    pending = len(calls)
    return {"status": "waiting" if pending else "settled", "pending_calls": pending}


# These channel helpers use the installed, separated request/result ownership.
# They never accept a caller's authentication boolean or a resource path.
def _spool(cfg):
    transport_files(cfg)
    return Path(cfg.fleet_spool)


def _json_file(path, maximum=4096):
    import worker_client
    raw = worker_client._read_regular_nofollow(path, maximum)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError, UnicodeError):
        raise FleetLifecycleError("malformed fleet purge coordination") from None


def _channel_ready(cfg):
    import fleet_queue
    from types import SimpleNamespace
    spool = _spool(cfg)
    ready = fleet_queue.readiness(SimpleNamespace(task_worker_spool=str(spool)))
    if ready is None:
        raise FleetLifecycleError("fleet executor readiness unavailable")
    path = spool / "results" / fleet_queue.READY_FILE
    meta, directory = path.lstat(), path.parent.lstat()
    if meta.st_uid != directory.st_uid or meta.st_mode & 0o022 or directory.st_mode & 0o022:
        raise FleetLifecycleError("fleet executor channel ownership mismatch")
    return ready


def _marker(value):
    if (not isinstance(value, dict) or set(value) != {"version", "action", "nonce", "phase", "implementation_hash", "pid", "preview_hash"}
            or value["version"] != "cara-fleet-queue/v1" or value["action"] != "purge_content"
            or value["phase"] not in {"quiesce", "delete"}
            or type(value["pid"]) is not int or value["pid"] <= 0
            or any(not isinstance(value[key], str) or not re.fullmatch(r"[a-f0-9]{64}", value[key]) for key in ("implementation_hash", "preview_hash"))):
        raise FleetLifecycleError("invalid fleet purge marker")
    _nonce(value["nonce"])
    return value


def request_quiescence(cfg, nonce, counts, phase="quiesce"):
    """Publish only on the dedicated app-owned channel, bound to loaded executor."""
    import worker_client
    ready, spool = _channel_ready(cfg), _spool(cfg)
    path = spool / "requests" / ".purge-all.json"
    previous = _json_file(path)
    if previous is not None and _marker(previous)["nonce"] != _nonce(nonce):
        raise FleetLifecycleError("another fleet purge holds admission")
    value = _marker({"version": "cara-fleet-queue/v1", "action": "purge_content", "nonce": _nonce(nonce),
                     "phase": phase, "implementation_hash": ready["implementation_hash"], "pid": ready["pid"],
                     "preview_hash": protocol.digest(_counts(counts))})
    worker_client._atomic_publish(path.parent, path.name, protocol.canonical(value).encode())
    return value


def executor_ack(cfg, nonce, counts, status="quiesced"):
    """Validate executor-only file ownership plus nonce/PID/source/preview binding."""
    ready, spool = _channel_ready(cfg), _spool(cfg)
    marker = _json_file(spool / "requests" / ".purge-all.json")
    if marker is None:
        return None
    marker = _marker(marker)
    expected = {"version": "cara-fleet-queue/v1", "action": "purge_content", "nonce": _nonce(nonce),
                "status": status, "implementation_hash": ready["implementation_hash"], "pid": ready["pid"],
                "preview_hash": protocol.digest(_counts(counts))}
    if (marker["nonce"] != nonce or marker["pid"] != ready["pid"] or marker["implementation_hash"] != ready["implementation_hash"]
            or marker["preview_hash"] != expected["preview_hash"] or marker["phase"] != ("delete" if status == "deleted" else "quiesce")):
        return None
    path = spool / "results" / ("purge_" + nonce + ".ack.json")
    value = _json_file(path)
    if value is None:
        return None
    meta = path.lstat()
    if meta.st_uid != (spool / "results").lstat().st_uid or meta.st_mode & 0o022:
        raise FleetLifecycleError("fleet purge acknowledgement ownership mismatch")
    if value != expected:
        return None
    # The authenticated preview binding is checked here; the durable receipt
    # retains its canonical preview/hash separately for backward compatibility.
    return {key: item for key, item in value.items() if key != "preview_hash"}


def executor_gate(spool):
    """Serial executor safe point: hold all admission; acknowledge after transports.

    This runs before claims and between requests, never inside an active dispatch.
    Quiescence preserves content. Deletion touches only executor-owned result
    copies, after the app's already-committed all-content authorization phase.
    An unavailable/malformed/mismatched marker holds admission without an ACK.
    """
    import fleet_queue
    import worker_client
    from types import SimpleNamespace
    spool = Path(spool)
    path = spool / "requests" / ".purge-all.json"
    if not path.exists() and not path.is_symlink():
        return False
    try:
        cfg = SimpleNamespace(fleet_spool=str(spool))
        value = _marker(_json_file(path))
        if value["pid"] != os.getpid() or value["implementation_hash"] != fleet_queue.implementation_hash():
            return True
        copies = transport_files(cfg)
        if value["phase"] == "delete":
            for copy_path in copies:
                if copy_path.parent.name == "results":
                    copy_path.unlink(missing_ok=True)
            worker_client._fsync_dir(spool / "results")
        ack = {key: item for key, item in value.items() if key != "phase"}
        ack["status"] = "deleted" if value["phase"] == "delete" else "quiesced"
        worker_client._atomic_publish(spool / "results", "purge_" + value["nonce"] + ".ack.json", protocol.canonical(ack).encode())
    except (ValueError, OSError, worker_client.WorkerError):
        pass
    return True


def cancel_spooled_requests(cfg):
    """Cancel validated bindings before DB terminal state; preserve all content."""
    import fleet_queue
    import worker_client
    from types import SimpleNamespace
    spool = _spool(cfg)
    client = SimpleNamespace(task_worker_spool=str(spool))
    for path in transport_files(cfg):
        if path.parent.name != "requests" or not re.fullmatch(_JOB + r"\.json", path.name):
            continue
        value = _json_file(path, protocol.MAX_BYTES)
        if (not isinstance(value, dict) or not isinstance(value.get("job_id"), str) or value["job_id"] + ".json" != path.name
                or not isinstance(value.get("nonce"), str) or not protocol.TOKEN.fullmatch(value["nonce"])):
            raise FleetLifecycleError("invalid fleet request cancellation binding")
        fleet_queue.cancel(client, job_id=value["job_id"], nonce=value["nonce"])


def clear_app_copies(cfg):
    """Only while the executor hold is acknowledged; never remove result evidence."""
    import worker_client
    spool = _spool(cfg)
    for path in transport_files(cfg):
        if path.parent.name == "requests":
            path.unlink(missing_ok=True)
    worker_client._fsync_dir(spool / "requests")


def release_hold(cfg, nonce):
    """Consume this hold only after app copies are gone; old requests cannot replay."""
    import worker_client
    spool = _spool(cfg)
    path = spool / "requests" / ".purge-all.json"
    value = _json_file(path)
    if value is None:
        return True
    if _marker(value)["nonce"] != _nonce(nonce):
        raise FleetLifecycleError("fleet purge hold nonce mismatch")
    if any(path.parent.name == "requests" for path in transport_files(cfg)):
        raise FleetLifecycleError("fleet requests remain under purge hold")
    path.unlink()
    worker_client._fsync_dir(path.parent)
    return True
