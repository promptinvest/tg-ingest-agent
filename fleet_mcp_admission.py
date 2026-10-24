"""Fixed retained-read admission over the existing fleet ledger.

Trusted protected operator policy supplies identity, DB and scope. Tool arguments
supply only a real owner source and idempotency/format choice. This module never
constructs Agent, loads application environment, calls a model, sends Telegram,
opens privileged transport, or creates another ledger. Owner-loop functions at
the end qualify the same row for the ordinary asynchronous fleet engine.
"""
import copy
import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
from pathlib import Path

import common
import fleet_facts as facts
import fleet_investigation as retained
import fleet_lifecycle
import fleet_protocol as protocol
import tasking

VERSION = "cara-fleet-mcp-admission/v1"
STATE = "admission_queued"
PREPARED = "admission_prepared"
CLIENT_INTENT = "cara-fleet-mcp-client-intent/v1"
CLIENT_KEYS = {"version", "idempotency_key", "input_hash", "prepared_at"}
MAX_BYTES = 524288
POLICY_KEYS = {"version", "capability_hash", "enabled", "database_path", "owner_chat_id", "allowed_hosts", "allowed_projects", "allowed_capabilities", "allowed_facets"}
BINDING_KEYS = {"version", "principal", "policy_hash", "idempotency_hash", "scope", "format"}
SCOPE_KEYS = {"subjects", "hosts", "projects", "capabilities", "facets"}
FORMATS = {"presentation": {"prose", "list", "table", "names"}, "detail": {"concise", "detailed"}, "lang": {"ru", "en"}}
KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
SHA = re.compile(r"[a-f0-9]{64}\Z")


class AdmissionError(ValueError):
    pass


class ScopeDenied(AdmissionError):
    pass


class Conflict(AdmissionError):
    pass


def contract_hash():
    return protocol.digest({"version": VERSION, "retained": retained.VERSION, "state": STATE,
        "prepared_state": PREPARED, "client_intent": {"version": CLIENT_INTENT, "keys": sorted(CLIENT_KEYS)},
        "project_capabilities": protocol.capability_contract(), "policy": sorted(POLICY_KEYS),
        "binding": sorted(BINDING_KEYS), "formats": {key: sorted(items) for key, items in FORMATS.items()}})


def _ids(value, maximum, allowed=None):
    if (not isinstance(value, list) or len(value) > maximum or any(not isinstance(item, str) for item in value)
            or len(set(value)) != len(value) or any(item not in allowed if allowed is not None else not protocol.ID.fullmatch(item) for item in value)):
        raise AdmissionError("invalid read admission allowlist")
    return sorted(value)


def policy(value):
    if (not isinstance(value, dict) or set(value) != POLICY_KEYS or value["version"] != VERSION
            or type(value["enabled"]) is not bool or value["capability_hash"] != contract_hash()
            or type(value["owner_chat_id"]) is not int or not 0 < abs(value["owner_chat_id"]) <= 2**53 - 1):
        raise AdmissionError("unqualified retained-read admission policy")
    path = value["database_path"]
    if (not isinstance(path, str) or not Path(path).is_absolute() or len(path) > 512 or path.startswith(("//", "\\"))
            or ".." in Path(path).parts or re.search(r"[\x00-\x1f\x7f]", path)):
        raise AdmissionError("invalid retained-read database binding")
    result = copy.deepcopy(value)
    result["allowed_hosts"] = _ids(value["allowed_hosts"], 20)
    result["allowed_projects"] = _ids(value["allowed_projects"], 20)
    result["allowed_capabilities"] = _ids(value["allowed_capabilities"], len(protocol.PROJECT_CAPABILITIES), protocol.PROJECT_CAPABILITIES)
    result["allowed_facets"] = _ids(value["allowed_facets"], len(protocol.FACETS), protocol.FACETS)
    return result


def context(config):
    """Normalize config already authenticated by the protected policy reader."""
    if not isinstance(config, dict):
        raise AdmissionError("invalid trusted admission configuration")
    principal, subjects = config.get("principal"), config.get("allowed_subjects")
    if (not isinstance(principal, str) or not facts.ID.fullmatch(principal) or not isinstance(subjects, list)
            or not 1 <= len(subjects) <= 128 or any(not isinstance(item, str) or not facts.ID.fullmatch(item) for item in subjects)
            or len(subjects) != len(set(subjects))):
        raise AdmissionError("invalid retained-read principal binding")
    value = {"version": VERSION, "principal": principal, "subjects": sorted(subjects), "policy": policy(config.get("investigations"))}
    value["policy_hash"] = protocol.digest(value)
    return value


def _context(value):
    if not isinstance(value, dict) or set(value) != {"version", "principal", "subjects", "policy", "policy_hash"}:
        raise AdmissionError("invalid trusted admission context")
    normalized = context({"principal": value["principal"], "allowed_subjects": value["subjects"], "investigations": value["policy"]})
    if normalized != value or not normalized["policy"]["enabled"]:
        raise ScopeDenied("retained-read admission is disabled or changed")
    return normalized


def load_context(cfg):
    # Only the already configured protected path is read. No app environment,
    # credentials, executable or path supplied by an RPC argument is accepted.
    import fleet_mcp
    return context(fleet_mcp.read_config(cfg.fleet_mcp_config))


class _AdmissionConnection(sqlite3.Connection):
    _admission_transition = False


def connect(value, write=False):
    """Existing same-UID DB channel; SQL-restricted, never claimed OS read-only."""
    value = _context(value)
    path = Path(value["policy"]["database_path"])
    if os.name != "posix" or not hasattr(os, "geteuid") or type(write) is not bool:
        raise AdmissionError("retained-read channel requires Linux")
    facts._no_symlinks(path)
    for item in (path, *path.parents):
        metadata = item.lstat()
        if metadata.st_uid not in {0, os.geteuid()} or (metadata.st_mode & 0o022 and not (item != path and metadata.st_mode & stat.S_ISVTX)):
            raise AdmissionError("unsafe retained-read database ownership")
        if item == path and not stat.S_ISREG(metadata.st_mode):
            raise AdmissionError("retained-read database must already exist")
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            facts._no_symlinks(sidecar)
            info = sidecar.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid not in {0, os.geteuid()} or info.st_mode & 0o022:
                raise AdmissionError("unsafe retained-read database sidecar")
    connection = sqlite3.connect(path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True, timeout=.1, factory=_AdmissionConnection, cached_statements=0)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA busy_timeout=100")
        if not write:
            connection.execute("PRAGMA query_only=ON")
        required = {"id", "chat_id", "source_update", "state", "payload", "created_at", "updated_at"}
        if {row[1] for row in connection.execute("PRAGMA table_info(fleet_requests)")} != required:
            raise AdmissionError("retained-read ledger schema is unavailable")
        if hasattr(connection, "setlimit"):
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_BYTES + 8192)
        import time
        expires = time.monotonic() + .25
        connection.set_progress_handler(lambda: int(time.monotonic() >= expires), 500)
        columns = {"fleet_requests": required, "telegram_updates": {"update_id", "chat_id", "received_at"},
                   "conversation": {"id", "chat_id", "update_id", "role", "source", "text"}, "kv": {"key", "value"}}
        def authorize(action, first, second, _database, _trigger):
            if action == sqlite3.SQLITE_READ:
                return sqlite3.SQLITE_OK if first in columns and (second in columns[first] or second == "") else sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_FUNCTION:
                return sqlite3.SQLITE_OK if second in {"substr", "count"} else sqlite3.SQLITE_DENY
            if write and action == sqlite3.SQLITE_INSERT and first in {"fleet_requests", "fleet_events"}:
                return sqlite3.SQLITE_OK
            if write and action == sqlite3.SQLITE_UPDATE and first == "fleet_requests":
                if second in {"payload", "updated_at"} or second == "state" and connection._admission_transition:
                    return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_OK if action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_TRANSACTION} else sqlite3.SQLITE_DENY
        connection.set_authorizer(authorize)
        return connection
    except BaseException:
        connection.close()
        raise


def is_admitted(payload):
    # Malformed admitted data never downgrades into legacy read authority.
    return isinstance(payload, dict) and "mcp_admission" in payload


def _decode(raw):
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise AdmissionError("duplicate retained admission field")
            result[key] = item
        return result
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_BYTES:
        raise AdmissionError("retained admission exceeds bound")
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(AdmissionError("invalid retained number")))
    if not isinstance(value, dict):
        raise AdmissionError("invalid retained admission payload")
    return value


def _row(connection, value, identity):
    if not isinstance(identity, str) or not protocol.TOKEN.fullmatch(identity):
        raise AdmissionError("invalid retained request identity")
    return connection.execute("SELECT id,chat_id,source_update,state,substr(payload,1,?) AS payload,created_at,updated_at FROM fleet_requests WHERE id=? AND chat_id=?",
                              (MAX_BYTES + 1, identity, value["policy"]["owner_chat_id"])).fetchone()


def _source(connection, value, update):
    owner = value["policy"]["owner_chat_id"]
    if type(update) is not int or not 0 <= update <= 2**53 - 1:
        raise AdmissionError("invalid canonical owner source")
    receipt = connection.execute("SELECT chat_id,received_at FROM telegram_updates WHERE update_id=?", (update,)).fetchone()
    source = connection.execute("SELECT substr(text,1,32769) AS text FROM conversation WHERE chat_id=? AND update_id=? AND role='user' AND source='boss' ORDER BY id LIMIT 1", (owner, update)).fetchone()
    if receipt is None or receipt["chat_id"] != owner or source is None or not isinstance(source["text"], str) or not 1 <= len(source["text"]) <= 16000:
        raise ScopeDenied("a real canonical owner question is required")
    return source["text"], receipt["received_at"]


def _scope(value):
    rule = value["policy"]
    return {"subjects": list(value["subjects"]), "hosts": list(rule["allowed_hosts"]), "projects": list(rule["allowed_projects"]),
            "capabilities": list(rule["allowed_capabilities"]), "facets": list(rule["allowed_facets"])}


def _caller(principal, digest):
    return "mcp:" + hashlib.sha256(principal.encode()).hexdigest()[:16] + ":" + digest


def _binding(value, digest, style):
    return {"version": VERSION, "principal": value["principal"], "policy_hash": value["policy_hash"],
            "idempotency_hash": digest, "scope": _scope(value), "format": copy.deepcopy(style)}


def _validate(connection, value, row, payload, source_current=True):
    value = _context(value)
    binding, header = payload.get("mcp_admission"), payload.get("external_waiter")
    if (not isinstance(binding, dict) or set(binding) != BINDING_KEYS or binding["version"] != VERSION
            or not isinstance(binding["idempotency_hash"], str) or not SHA.fullmatch(binding["idempotency_hash"])
            or not isinstance(binding["format"], dict) or "lang" not in binding["format"] or set(binding["format"]) - set(FORMATS)
            or any(not isinstance(item, str) or item not in FORMATS[key] for key, item in binding["format"].items())
            or binding != _binding(value, binding["idempotency_hash"], binding["format"])):
        raise ScopeDenied("retained admission principal or policy changed")
    caller = _caller(value["principal"], binding["idempotency_hash"])
    if (not isinstance(header, dict) or set(header) != retained.HEADER_KEYS or header["version"] != retained.VERSION
            or header["principal"] != "owner" or header["delivery_mode"] != "retained" or header["caller_key"] != caller
            or header["chat_id"] != value["policy"]["owner_chat_id"] or row["chat_id"] != header["chat_id"]
            or header["source_update"] != row["source_update"] or header["request_id"] != row["id"]
            or row["id"] != retained.request_id(row["chat_id"], caller) or payload.get("allow_maintenance") is not False
            or payload.get("approval") is not None or payload.get("lang") != binding["format"]["lang"]
            or header["source_hash"] != payload.get("source_hash") or header["input_hash"] != protocol.digest(retained._input(header, payload))):
        raise ScopeDenied("retained admission identity changed")
    if row["state"] == PREPARED and "client_intent" not in payload:
        raise ScopeDenied("prepared admission lacks its durable caller key")
    if "client_intent" in payload:
        _validate_client_intent(row, payload)
    if source_current:
        text, _ = _source(connection, value, row["source_update"])
        if (payload.get("question") != text.strip() or header["source_hash"] != tasking.source_hash(text)
                or header["source_span"] != [0, len(text)]):
            raise ScopeDenied("retained admission source changed")
    if source_current:
        scoped = dict(value, requested_format=binding["format"])
        if payload.get("plan") and _plan(scoped, payload["plan"]) != payload["plan"]:
            raise ScopeDenied("stored answer scope changed")
        if payload.get("operation"):
            _operation(value, payload["operation"])
    return binding


def _unavailable(identity, reason):
    return {"version": VERSION, "request_id": identity, "status": "unavailable", "reason": reason}


def selected_scope(payload):
    plan = payload.get("plan")
    if not isinstance(plan, dict):
        return {"kind": "pending", "hosts": [], "projects": [], "capabilities": [], "facets": []}
    if plan.get("kind") in {"catalog", "clarification", "catalog_gap"}:
        return {"kind": plan["kind"], "subjects": list(plan.get("subjects", [])), "fields": list(plan.get("fields", [])),
                "hosts": [], "projects": [], "capabilities": [], "facets": []}
    selected = plan.get("read_scope", {})
    result = {"kind": selected.get("kind", "legacy"), "hosts": list(plan.get("hosts", [])), "projects": list(plan.get("projects", [])),
              "capabilities": list(selected.get("capabilities", [])), "facets": list(plan.get("facets", []))}
    if plan.get("catalog_plan"):
        result["catalog"] = {"subjects": list(plan["catalog_plan"].get("subjects", [])), "fields": list(plan["catalog_plan"].get("fields", []))}
    return result


def _handle(row, payload):
    handle = {**retained._handle(row, payload), "version": VERSION, "status": "available", "scope": selected_scope(payload)}
    phase = payload.get("mcp_facts", {})
    cancellation = phase.get("cancellation") if isinstance(phase, dict) else None
    # Only the original facts transport's typed owner-loop settlement can refine
    # its intent. Generic model/executor failure keeps the existing requested
    # meaning; a cancellation argument alone never proves work stopped.
    intent = payload.get("client_intent")
    if payload.get("retained_cancel") and row["state"] == "cancelled" and isinstance(intent, dict) and isinstance(intent.get("cancelled_at"), str):
        protocol.age(intent["cancelled_at"])
        handle["cancellation"] = "settled"
    if payload.get("retained_cancel") and isinstance(cancellation, dict):
        if row["state"] == "cancelled" and cancellation.get("state") == "settled" and isinstance(cancellation.get("settled_at"), str):
            protocol.age(cancellation["settled_at"])
            handle["cancellation"] = "settled"
        elif row["state"] == "ambiguous" and cancellation.get("state") == "unknown":
            handle["cancellation"] = "unknown"
    return handle


def classify(connection, value, identity):
    value = _context(value)
    row = _row(connection, value, identity)
    if row is None:
        return "missing"
    payload = _decode(row["payload"])
    if not is_admitted(payload):
        return "legacy"
    if not fleet_lifecycle.admission_paused(connection):
        _validate(connection, value, row, payload)
    return "admitted"


def _validate_client_intent(row, payload):
    intent = payload.get("client_intent")
    if (not isinstance(intent, dict) or not CLIENT_KEYS <= set(intent) or set(intent) - CLIENT_KEYS - {"cancelled_at"}
            or intent["version"] != CLIENT_INTENT or not isinstance(intent["idempotency_key"], str)
            or not KEY.fullmatch(intent["idempotency_key"])
            or hashlib.sha256(intent["idempotency_key"].encode()).hexdigest() != payload["mcp_admission"]["idempotency_hash"]
            or intent["input_hash"] != payload["external_waiter"]["input_hash"]
            or protocol.age(intent["prepared_at"]) < -30):
        raise ScopeDenied("durable client intent changed")
    if "cancelled_at" in intent:
        if row["state"] != "cancelled" or protocol.age(intent["cancelled_at"]) < -30:
            raise ScopeDenied("durable client cancellation changed")
    if row["state"] == PREPARED and (payload.get("model_calls") != 0 or payload.get("cost") != 0
            or set(payload) & {"inference", "operation", "binding", "plan", "report", "result", "answer", "answer_pages"}):
        raise ScopeDenied("prepared client intent cannot contain execution")
    return intent


def _transition(connection, row, payload, target):
    # The restricted channel opens state writes only around these two fixed SQL
    # transitions. No arbitrary SQL/state is accepted and statement caching is off.
    if row["state"] != PREPARED or target not in {STATE, "cancelled"}:
        raise AdmissionError("invalid prepared admission transition")
    statements = {
        STATE: "UPDATE fleet_requests SET state='admission_queued',payload=?,updated_at=? WHERE id=? AND state='admission_prepared'",
        "cancelled": "UPDATE fleet_requests SET state='cancelled',payload=?,updated_at=? WHERE id=? AND state='admission_prepared'"}
    restricted = isinstance(connection, _AdmissionConnection)
    if restricted:
        connection._admission_transition = True
    try:
        changed = connection.execute(statements[target], (protocol.canonical(payload), protocol.now(), row["id"])).rowcount
    finally:
        if restricted:
            connection._admission_transition = False
    if changed != 1:
        raise Conflict("prepared admission changed before transition")


def prepare_start(connection, value, arguments):
    """Persist/recover a caller key in the SAME fleet row before any exchange.

    This trusted client API accepts source/format and an optional explicit key.
    Missing keys are generated only while the source's unique row is locked.
    Prepared rows are inert; only server start promotes them to admission_queued.
    No project read, model call, Telegram send or second ledger is created here.
    """
    return _start(connection, value, arguments, prepared=True)


def start(connection, value, arguments):
    return _start(connection, value, arguments, prepared=False)


def _start(connection, value, arguments, *, prepared):
    value = _context(value)
    required = {"source_update"} if prepared else {"source_update", "idempotency_key"}
    if (not isinstance(arguments, dict) or not required <= set(arguments)
            or set(arguments) - {"source_update", "idempotency_key", *FORMATS}
            or "idempotency_key" in arguments and (not isinstance(arguments["idempotency_key"], str) or not KEY.fullmatch(arguments["idempotency_key"]))):
        raise AdmissionError("invalid source-bound investigation start")
    for key in set(arguments) & set(FORMATS):
        if not isinstance(arguments[key], str) or arguments[key] not in FORMATS[key]:
            raise AdmissionError("invalid requested investigation format")
    text, received = _source(connection, value, arguments["source_update"])
    style = {key: arguments[key] for key in FORMATS if key in arguments}
    style.setdefault("lang", common.detect_lang(text) or "en")
    identity = None
    try:
        connection.execute("BEGIN IMMEDIATE")
        if fleet_lifecycle.admission_paused(connection):
            connection.rollback()
            gap = _unavailable(identity, "purge_in_progress")
            # A caller-supplied key still determines its honest reconciliation ID.
            if "idempotency_key" in arguments:
                digest = hashlib.sha256(arguments["idempotency_key"].encode()).hexdigest()
                gap["request_id"] = retained.request_id(value["policy"]["owner_chat_id"], _caller(value["principal"], digest))
            return {"handle": gap, "arguments": None} if prepared else gap
        live_text, live_received = _source(connection, value, arguments["source_update"])
        if live_text != text or live_received != received:
            raise ScopeDenied("canonical owner source changed before admission")
        selected_key = arguments.get("idempotency_key")
        if prepared and selected_key is None:
            existing_source = connection.execute("SELECT * FROM fleet_requests WHERE chat_id=? AND source_update=?",
                (value["policy"]["owner_chat_id"], arguments["source_update"])).fetchone()
            if existing_source is not None:
                old = _decode(existing_source["payload"])
                _validate(connection, value, existing_source, old)
                selected_key = _validate_client_intent(existing_source, old)["idempotency_key"]
                saved_style = old["mcp_admission"]["format"]
                if any(arguments[key] != saved_style.get(key) for key in FORMATS if key in arguments):
                    raise Conflict("prepared caller format changed")
                style = copy.deepcopy(saved_style)
            else:
                selected_key = secrets.token_hex(16)
        digest = hashlib.sha256(selected_key.encode()).hexdigest()
        caller = _caller(value["principal"], digest)
        identity = retained.request_id(value["policy"]["owner_chat_id"], caller)
        binding = _binding(value, digest, style)
        header = {"version": retained.VERSION, "delivery_mode": "retained", "principal": "owner", "chat_id": value["policy"]["owner_chat_id"],
                  "source_update": arguments["source_update"], "source_hash": tasking.source_hash(text), "source_span": [0, len(text)],
                  "caller_key": caller, "request_id": identity, "admitted_at": protocol.now()}
        payload = {"version": 2, "question": text.strip(), "source_hash": header["source_hash"], "lang": style["lang"],
                   "allow_maintenance": False, "model_calls": 0, "cost": 0, "mcp_admission": binding, "external_waiter": header}
        header["input_hash"] = protocol.digest(retained._input(header, payload))
        matches = connection.execute("SELECT * FROM fleet_requests WHERE id=? OR (chat_id=? AND source_update=?)",
            (identity, header["chat_id"], header["source_update"])).fetchall()
        if matches:
            if len(matches) != 1 or matches[0]["id"] != identity:
                raise Conflict("existing owner request cannot be repurposed")
            row = matches[0]
            old = _decode(row["payload"])
            _validate(connection, value, row, old)
            if old["external_waiter"]["input_hash"] != header["input_hash"]:
                raise Conflict("caller key or investigation input changed")
            if prepared and "client_intent" not in old:
                old["client_intent"] = {"version": CLIENT_INTENT, "idempotency_key": selected_key,
                    "input_hash": header["input_hash"], "prepared_at": protocol.now()}
                connection.execute("UPDATE fleet_requests SET payload=?,updated_at=? WHERE id=?", (protocol.canonical(old), protocol.now(), identity))
            if not prepared and row["state"] == PREPARED and not old.get("retained_cancel"):
                if not -30 <= protocol.age(received) < 7200:
                    raise ScopeDenied("prepared source expired before server admission")
                _transition(connection, row, old, STATE)
            connection.commit()
            handle = _handle(_row(connection, value, identity), old)
        else:
            if not -30 <= protocol.age(received) < 7200:
                raise ScopeDenied("new investigation source is outside its admission window")
            active = (PREPARED, STATE, *retained.ACTIVE)
            if connection.execute("SELECT count(*) FROM fleet_requests WHERE state IN (" + ",".join("?" for _ in active) + ")", active).fetchone()[0] >= 8:
                raise AdmissionError("existing investigation capacity is occupied")
            if prepared:
                payload["client_intent"] = {"version": CLIENT_INTENT, "idempotency_key": selected_key,
                    "input_hash": header["input_hash"], "prepared_at": protocol.now()}
            stamp = protocol.now(); encoded = protocol.canonical(payload)
            connection.execute("INSERT INTO fleet_requests VALUES(?,?,?,?,?,?,?)", (identity, header["chat_id"], header["source_update"], PREPARED if prepared else STATE, encoded, stamp, stamp))
            connection.execute("INSERT INTO fleet_events(request_id,kind,payload,created_at) VALUES(?,?,?,?)", (identity,
                "mcp_client_intent_prepared" if prepared else "mcp_read_admission_requested", encoded, stamp))
            connection.commit()
            handle = _handle(_row(connection, value, identity), payload)
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
    return {"handle": handle, "arguments": {"source_update": arguments["source_update"], "idempotency_key": selected_key, **style}} if prepared else handle


def _reference(connection, value, arguments, source_current=True):
    value = _context(value)
    if not isinstance(arguments, dict) or set(arguments) != {"request_id"}:
        raise AdmissionError("invalid admitted reference")
    identity = arguments["request_id"]
    row = _row(connection, value, identity)
    if fleet_lifecycle.admission_paused(connection):
        return value, None, None, _unavailable(identity, "purge_in_progress")
    if row is None:
        return value, None, None, _unavailable(identity, "not_found_or_not_authorized")
    payload = _decode(row["payload"])
    _validate(connection, value, row, payload, source_current=source_current)
    return value, row, payload, None


def status(connection, value, arguments):
    _value, row, payload, gap = _reference(connection, value, arguments)
    return gap or _handle(row, payload)


def result(connection, value, arguments):
    _value, row, payload, gap = _reference(connection, value, arguments)
    if gap:
        return gap
    handle = _handle(row, payload)
    if not handle["result_available"]:
        return {**handle, "answer_pages": [], "gap": "unknown_outcome" if row["state"] == "ambiguous" or payload.get("inference", {}).get("usage_unknown") else "result_not_available"}
    pages = payload.get("answer_pages", [payload.get("answer", "")])
    if (not isinstance(pages, list) or not 1 <= len(pages) <= 32 or any(not isinstance(page, str) or not page or len(page) > 3700 for page in pages)
            or len(protocol.canonical(pages).encode()) > MAX_BYTES):
        raise AdmissionError("retained answer pages are invalid")
    fresh = _row(connection, _value, row["id"])
    if fresh is None or fleet_lifecycle.admission_paused(connection):
        return _unavailable(row["id"], "purge_in_progress" if fleet_lifecycle.admission_paused(connection) else "not_found_or_not_authorized")
    current_payload = _decode(fresh["payload"])
    _validate(connection, _value, fresh, current_payload)
    if fresh["state"] != row["state"] or fresh["updated_at"] != row["updated_at"] or current_payload != payload:
        return _unavailable(row["id"], "reference_changed")
    return {**handle, "answer_pages": list(pages), "gap": "", "observed_at": payload.get("result", {}).get("observed_at") if isinstance(payload.get("result"), dict) else None,
            "facts_integrity": payload.get("facts_integrity")}


def _cancellation_handle(row, payload):
    handle = _handle(row, payload)
    handle.pop("scope", None)
    return handle


def cancellation_status(connection, value, arguments):
    """Read cancellation progress after source edits; never return old content."""
    _value, row, payload, gap = _reference(connection, value, arguments, source_current=False)
    return gap or _cancellation_handle(row, payload)


def cancel(connection, value, arguments):
    try:
        connection.execute("BEGIN IMMEDIATE")
        value, row, payload, gap = _reference(connection, value, arguments, source_current=False)
        if gap:
            connection.rollback()
            return gap
        if row["state"] not in retained.TERMINAL and not payload.get("retained_cancel"):
            payload["retained_cancel"] = {"requested_at": protocol.now()}
            encoded = protocol.canonical(payload)
            if row["state"] == PREPARED:
                payload["client_intent"]["cancelled_at"] = protocol.now()
                encoded = protocol.canonical(payload)
                _transition(connection, row, payload, "cancelled")
            else:
                connection.execute("UPDATE fleet_requests SET payload=?,updated_at=? WHERE id=?", (encoded, protocol.now(), row["id"]))
            connection.execute("INSERT INTO fleet_events(request_id,kind,payload,created_at) VALUES(?,?,?,?)", (row["id"], "mcp_read_cancel_requested", encoded, protocol.now()))
        connection.commit()
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
    return _cancellation_handle(_row(connection, value, row["id"]), payload)


def _runtime_context(agent, row, payload):
    value = _context(load_context(agent.cfg))
    if not getattr(agent.cfg, "fleet_enabled", False) or row["chat_id"] not in getattr(agent.cfg, "allowed_chat_ids", ()) or fleet_lifecycle.admission_paused(agent.conn):
        raise ScopeDenied("runtime retained-read authority is unavailable")
    actual = next((item[2] for item in agent.conn.execute("PRAGMA database_list") if item[1] == "main"), "")
    if not actual or Path(actual).resolve() != Path(value["policy"]["database_path"]).resolve():
        raise ScopeDenied("runtime database binding changed")
    _validate(agent.conn, value, row, payload)
    return value


def _subset(items, allowed):
    return isinstance(items, list) and all(isinstance(item, str) and item in allowed for item in items) and len(items) == len(set(items))


def _plan(value, plan):
    plan = copy.deepcopy(plan)
    rule = value["policy"]
    if not isinstance(plan, dict) or plan.get("operation", "inspect") != "inspect":
        raise ScopeDenied("read admission cannot authorize maintenance")
    if plan.get("kind") in {"catalog", "clarification", "catalog_gap"}:
        if not _subset(plan.get("subjects", []), value["subjects"]):
            raise ScopeDenied("catalog subject exceeds admitted scope")
    elif not plan.get("clarification"):
        if not _subset(plan.get("hosts", []), rule["allowed_hosts"]) or not _subset(plan.get("projects", []), rule["allowed_projects"]):
            raise ScopeDenied("runtime target exceeds admitted scope")
        selected = plan.get("read_scope")
        if selected is None:
            if plan.get("projects"):
                raise ScopeDenied("project admission requires a qualified selected read")
            # The legacy validator adds project context to host requests. This
            # new restricted interface always selects the explicit host read.
            selected = {"version": protocol.READ_VERSION, "kind": "host", "projects": [], "capabilities": []}
            plan["facets"] = [item for item in plan.get("facets", []) if item != "projects"]
        selected = copy.deepcopy(protocol.read_scope(selected))
        if (not _subset(plan.get("facets", []), rule["allowed_facets"]) or not plan.get("facets")
                or not _subset(selected["projects"], rule["allowed_projects"])
                or not _subset(selected["capabilities"], rule["allowed_capabilities"])):
            raise ScopeDenied("observation exceeds admitted capabilities")
        if selected["kind"] == "host" and ("projects" in plan["facets"] or plan.get("projects")):
            raise ScopeDenied("host admission cannot collect project context")
        if selected["kind"] == "projects" and (plan["facets"] != ["projects"] or set(plan.get("projects", [])) != set(selected["projects"])):
            raise ScopeDenied("project admission cannot collect host context")
        plan["read_scope"] = selected
        plan["read_contract_hash"] = protocol.digest(protocol.capability_contract())
    if plan.get("clarification"):
        plan["clarification"] = ("\u0423\u0442\u043e\u0447\u043d\u0438\u0442\u0435 \u0434\u043e\u0441\u0442\u0443\u043f\u043d\u044b\u0439 \u0441\u0435\u0440\u0432\u0435\u0440 \u0438\u043b\u0438 \u043f\u0440\u043e\u0435\u043a\u0442 \u0438 \u043d\u0443\u0436\u043d\u044b\u0435 \u0441\u0432\u0435\u0434\u0435\u043d\u0438\u044f." if value.get("requested_format", {}).get("lang") == "ru" else "Please name an allowed host or project and the facts you need.")
    if plan.get("catalog_plan"):
        plan["catalog_plan"] = _plan(value, plan["catalog_plan"])
    for key in ("presentation", "detail"):
        if key in value.get("requested_format", {}):
            plan[key] = value["requested_format"][key]
    return plan


def validate_plan(agent, row, payload, plan):
    if not is_admitted(payload):
        return plan
    value = _runtime_context(agent, row, payload)
    value["requested_format"] = payload["mcp_admission"]["format"]
    return _plan(value, plan)


def current(agent, row, payload):
    if not is_admitted(payload):
        return True
    try:
        value = _runtime_context(agent, row, payload)
        if payload.get("retained_cancel"):
            return False
        if payload.get("plan"):
            value["requested_format"] = payload["mcp_admission"]["format"]
            if _plan(value, payload["plan"]) != payload["plan"]:
                return False
        if payload.get("operation"):
            _operation(value, payload["operation"])
        return True
    except (ValueError, TypeError, KeyError, OSError):
        return False


def _operation(value, operation):
    rule = value["policy"]
    if (not isinstance(operation, dict) or operation.get("operation") != "inspect"
            or not _subset(operation.get("hosts", []), rule["allowed_hosts"])
            or not _subset(operation.get("facets", []), rule["allowed_facets"]) or "read_scope" not in operation):
        raise ScopeDenied("executor operation exceeds admitted read scope")
    selected = protocol.read_scope(operation["read_scope"])
    if (not _subset(selected["projects"], rule["allowed_projects"]) or not _subset(selected["capabilities"], rule["allowed_capabilities"])
            or selected["kind"] == "host" and "projects" in operation["facets"]
            or selected["kind"] == "projects" and operation["facets"] != ["projects"]):
        raise ScopeDenied("executor operation expands admitted observations")
    return operation


def validate_operation(agent, row, payload, operation):
    if not is_admitted(payload):
        return operation
    value = _runtime_context(agent, row, payload)
    return _operation(value, operation)


def planning_hint(payload):
    if not is_admitted(payload):
        return ""
    bound = payload["mcp_admission"]
    return "\nThis retained read is limited by code-owned policy: " + protocol.canonical({"scope": bound["scope"], "requested_format": bound["format"]}) + ". Use only these IDs/facets/capabilities. A host read has read_scope kind host, no projects or project capability. A selected project read has only the projects facet. No maintenance."


def pump(agent):
    """Owner-loop only: bind original phases on existing admitted rows, no sends."""
    if not getattr(agent.cfg, "fleet_enabled", False) or not getattr(agent, "fleet_async", False) or fleet_lifecycle.admission_paused(agent.conn):
        return 0
    import fleet
    handled = 0
    for selected in agent.conn.execute("SELECT id FROM fleet_requests WHERE state=? ORDER BY created_at LIMIT 8", (PREPARED,)).fetchall():
        row, payload = None, None
        try:
            agent.conn.execute("BEGIN IMMEDIATE")
            row = agent.conn.execute("SELECT * FROM fleet_requests WHERE id=? AND state=?", (selected["id"], PREPARED)).fetchone()
            if row is None:
                agent.conn.commit()
                continue
            payload = _decode(row["payload"])
            value = _runtime_context(agent, row, payload)
            _text, received = _source(agent.conn, value, row["source_update"])
            if payload.get("retained_cancel") or not -30 <= protocol.age(received) < 7200:
                raise ScopeDenied("prepared admission was cancelled or expired")
            agent.conn.commit()
        except (ValueError, TypeError, KeyError, OSError) as exc:
            if agent.conn.in_transaction:
                agent.conn.rollback()
            if row is not None and payload is not None:
                payload["inference_gap"] = type(exc).__name__
                agent._fleet_save(row, "failed", payload, "mcp_prepared_intent_fenced_no_dispatch")
            handled += 1
        except BaseException:
            if agent.conn.in_transaction:
                agent.conn.rollback()
            raise
    rows = agent.conn.execute("SELECT id FROM fleet_requests WHERE state=? ORDER BY created_at LIMIT 8", (STATE,)).fetchall()
    for selected in rows:
        row, payload = None, None
        try:
            agent.conn.execute("BEGIN IMMEDIATE")
            row = agent.conn.execute("SELECT * FROM fleet_requests WHERE id=? AND state=?", (selected["id"], STATE)).fetchone()
            if row is None:
                agent.conn.commit()
                continue
            payload = _decode(row["payload"])
            value = _runtime_context(agent, row, payload)
            if payload.get("retained_cancel"):
                agent._fleet_save(row, "cancelled", payload, "mcp_unsent_read_cancelled")
                handled += 1
                continue
            _text, received = _source(agent.conn, value, row["source_update"])
            if not -30 <= protocol.age(received) < 7200:
                raise ScopeDenied("canonical source expired before phase admission")
            inventory = fleet.catalog(agent.cfg)
            previous = agent._fleet_recent(row["chat_id"], payload["question"])
            if previous:
                try:
                    _plan(value, previous)
                except ValueError:
                    previous = {}
            payload.update(catalog_hash=protocol.digest(inventory), prior_scope=previous, planning_attempt=1, source_message=None, trace_id=None)
            agent._fleet_queue_phase(row, payload, "planning", inventory)
            handled += 1
        except (ValueError, TypeError, KeyError, OSError) as exc:
            if agent.conn.in_transaction:
                agent.conn.rollback()
            if row is not None and payload is not None:
                payload["inference_gap"] = type(exc).__name__
                agent._fleet_save(row, "failed", payload, "mcp_read_admission_refused")
            handled += 1
        except BaseException:
            if agent.conn.in_transaction:
                agent.conn.rollback()
            raise
    return handled
