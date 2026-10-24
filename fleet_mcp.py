"""Offline CF-07 modern MCP facts adapter; not installed or enabled by this module.

Pinned protocol: 2026-07-28. References checked October 24, 2026:
https://modelcontextprotocol.io/specification/2026-07-28/basic/index
https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning
https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio
https://modelcontextprotocol.io/specification/2026-07-28/server/discover
https://modelcontextprotocol.io/specification/2026-07-28/server/tools

Local descriptive facts and optional retained source-bound observations are exposed.
Optional source-bound admission writes only the existing request ledger; the
owner loop owns all planning and reads. No protected effects, direct probes, model
calls, Tasks or competing content/job ledger exist here. Self-reported MCP clientInfo
never establishes identity; the launching operator-owned config binds authority.
"""
import argparse
import base64
import hashlib
import sqlite3
from datetime import datetime, timezone, timedelta
import copy
import json
import os
import re
import stat
import sys
import time
from collections import deque
from pathlib import Path

# The client uses Python isolated mode. This code-owned directory is the only
# application import path added; cwd/PYTHONPATH cannot supply adapter modules.
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import fleet_protocol
import fleet_shared_reads
import fleet_facts as facts
import fleet_queries as queries

PROTOCOL = "2026-07-28"
CAPABILITY = "cara-fleet-lookup/v1"
CONFIG_SCHEMA = "cara-fleet-mcp-local/v1"
TOOL = "fleet_lookup"
PREFIX = "io.modelcontextprotocol/"
MAX_REQUEST_BYTES = 65536
MAX_RESPONSE_BYTES = 1048576
SERVER_INFO = {"name": "cara-fleet-facts", "version": "1"}
ARGUMENTS = {"subjects", "fields", "presentation", "detail", "facts_integrity", "lang"}


class MCPError(ValueError):
    pass


class ScopeDenied(MCPError):
    pass


def read_config(path):
    """Read trusted local policy; file identity is never supplied by a tool call."""
    path = Path(path)
    facts._no_symlinks(path)
    metadata = path.stat()
    if os.name != "posix" or metadata.st_uid != 0 or metadata.st_mode & 0o022 or not stat.S_ISREG(metadata.st_mode):
        raise MCPError("MCP policy must be a protected root-owned regular file")
    for parent in path.absolute().parents:
        mode = parent.stat()
        if mode.st_uid != 0 or (mode.st_mode & 0o022 and not mode.st_mode & stat.S_ISVTX):
            raise MCPError("MCP policy ancestry is not trusted")
    value = facts._read(path)
    if not isinstance(value, dict) or not {"schema", "principal", "allowed_subjects", "facts_path"} <= set(value) or set(value) - {"schema", "principal", "allowed_subjects", "facts_path", "references", "investigations"} or value["schema"] != CONFIG_SCHEMA:
        raise MCPError("invalid MCP local policy")
    principal, subjects, source = value["principal"], value["allowed_subjects"], value["facts_path"]
    if (not isinstance(principal, str) or not facts.ID.fullmatch(principal)
            or not isinstance(subjects, list) or not 1 <= len(subjects) <= 128
            or any(not isinstance(identity, str) or not facts.ID.fullmatch(identity) for identity in subjects)
            or len(set(subjects)) != len(subjects)
            or not isinstance(source, str) or not Path(source).is_absolute() or len(source) > 512
            or re.search(r"[\x00-\x1f\x7f]", source)):
        raise MCPError("invalid MCP caller scope or facts configuration")
    if "references" in value:
        reference_policy(value["references"])
    investigation_context(value)
    return value


def investigation_context(config):
    """Optional installed contract; authority comes only from protected config."""
    if "investigations" not in config:
        return None
    import fleet_mcp_admission as admission
    value = admission.context(config)
    if "references" in config:
        reference = reference_policy(config["references"])
        rule = value["policy"]
        if any(reference[key] != rule[key] for key in ("database_path", "owner_chat_id")) or any(set(reference[key]) != set(rule[key]) for key in ("allowed_hosts", "allowed_projects", "allowed_capabilities")):
            raise MCPError("investigation and retained policies conflict")
    return value


def tool_definition():
    return {"name": TOOL, "title": "Published fleet facts",
            "description": "Read selected descriptive facts and recorded relationships; no live observations or effects.",
            "inputSchema": {"type": "object", "additionalProperties": False, "required": sorted(ARGUMENTS),
                "properties": {"subjects": {"type": "array", "minItems": 1, "maxItems": 128, "uniqueItems": True,
                    "items": {"type": "string", "pattern": "^[a-z][a-z0-9_-]{0,63}$"}},
                    "fields": {"type": "array", "minItems": 1, "maxItems": len(queries.FIELDS), "uniqueItems": True,
                               "items": {"type": "string", "enum": list(queries.FIELDS)}},
                    "presentation": {"type": "string", "enum": sorted(queries.PRESENTATIONS)},
                    "detail": {"type": "string", "enum": ["concise", "detailed"]},
                    "facts_integrity": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
                    "lang": {"type": "string", "enum": ["ru", "en"]}}},
            "outputSchema": {"type": "object", "additionalProperties": False,
                "required": ["schema", "revision", "facts_integrity", "plan", "cells"],
                "properties": {"schema": {"const": CAPABILITY}, "revision": {"type": "integer", "minimum": 1},
                               "facts_integrity": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
                               "plan": {"type": "object"}, "cells": {"type": "array", "items": {"type": "object"}}}},
            "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}}


def arguments(plan, lang="en"):
    if not isinstance(plan, dict) or plan.get("kind") != "catalog" or "facts_integrity" not in plan:
        raise MCPError("only bound catalog plans may use MCP facts")
    return {key: copy.deepcopy(plan[key]) for key in ("subjects", "fields", "presentation", "detail", "facts_integrity")} | {"lang": lang}


def lookup(document, value, allowed_subjects):
    """The same shared query validator/cells; allowed scope comes from trusted policy."""
    if not isinstance(value, dict) or set(value) != ARGUMENTS or not isinstance(value["lang"], str) or value["lang"] not in {"ru", "en"}:
        raise MCPError("invalid fleet lookup arguments")
    selected = value["subjects"]
    if not isinstance(selected, list) or any(not isinstance(subject, str) for subject in selected):
        raise MCPError("invalid fleet lookup subject scope")
    if any(subject not in allowed_subjects for subject in selected):
        raise ScopeDenied("fleet lookup is outside the trusted caller scope")
    raw = {key: copy.deepcopy(value[key]) for key in ("subjects", "fields", "presentation", "detail", "facts_integrity")}
    raw.update(kind="catalog", scope="explicit", clarification="")
    plan = queries.validate_plan(raw, document)
    return {"schema": CAPABILITY, "revision": document["revision"], "facts_integrity": document["integrity"],
            "plan": plan, "cells": queries.cells(document, plan)}


def _complete(value):
    return {"resultType": "complete", **value, "_meta": {PREFIX + "serverInfo": SERVER_INFO.copy()}}


def _error(identity, code, message, data=None):
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": identity, "error": error}


REFERENCE_STATUS = "cara-fleet-investigation-status/v1"
REFERENCE_RESULT = "cara-fleet-investigation-result/v1"
REFERENCE_TOOLS = ("investigation_status", "investigation_result", "project_observe")
REFERENCE_STATES = frozenset({"admission_prepared", "admission_queued", "mcp_facts_queued", "mcp_facts_started", "preparing", "queued", "inspection_started", "ready", "analysis_started", "delivering", "done", "failed", "ambiguous", "cancelled"} | {name + "_" + phase for name in ("planning", "synthesis") for phase in ("queued", "started", "completed", "consumed")})
MAX_RETAINED_BYTES = 524288
MAX_REFERENCE_PAGE = 48000
MAX_REFERENCE_CELLS = 6


def reference_policy(value):
    """Trusted local read policy; no protocol argument can impersonate an owner."""
    required = {"database_path", "owner_chat_id", "allowed_hosts", "allowed_projects", "allowed_capabilities", "retention_seconds", "max_age_seconds"}
    if not isinstance(value, dict) or set(value) != required:
        raise MCPError("invalid retained investigation policy")
    if type(value["owner_chat_id"]) is not int or not 0 < abs(value["owner_chat_id"]) <= 2**53-1:
        raise MCPError("invalid retained owner binding")
    path = value["database_path"]
    if not isinstance(path, str) or not Path(path).is_absolute() or len(path) > 512 or path.startswith(("//", "\\\\")) or re.search(r"[\x00-\x1f\x7f]", path) or ".." in Path(path).parts:
        raise MCPError("invalid retained database binding")
    for field, maximum in (("allowed_hosts", 20), ("allowed_projects", 20), ("allowed_capabilities", len(fleet_protocol.PROJECT_CAPABILITIES))):
        items = value[field]
        if not isinstance(items, list) or len(items) > maximum or any(not isinstance(item, str) for item in items) or len(items) != len(set(items)):
            raise MCPError("invalid retained scope policy")
        if field == "allowed_capabilities":
            if any(item not in fleet_protocol.PROJECT_CAPABILITIES for item in items):
                raise MCPError("invalid retained capability policy")
        elif any(not fleet_protocol.ID.fullmatch(item) for item in items):
            raise MCPError("invalid retained subject policy")
    for key, maximum in (("retention_seconds", 31*86400), ("max_age_seconds", 86400)):
        if type(value[key]) is not int or not 1 <= value[key] <= maximum:
            raise MCPError("invalid retained age policy")
    return copy.deepcopy(value)


def reference_definitions():
    """Read existing handles only. Creation, effects and Tasks are not advertised."""
    definitions = []
    for name in REFERENCE_TOOLS:
        properties = {"request_id": {"type": "string", "pattern": "^[a-f0-9]{32}$"}}
        if name != "investigation_status":
            properties.update(cursor={"type": "string", "maxLength": 1024}, max_age_seconds={"type": "integer", "minimum": 1, "maximum": 86400})
        if name == "project_observe":
            properties.update(projects={"type": "array", "minItems": 1, "maxItems": 20, "uniqueItems": True,
                "items": {"type": "string", "pattern": "^[a-z][a-z0-9_-]{0,47}$"}},
                capabilities={"type": "array", "minItems": 1, "maxItems": len(fleet_protocol.PROJECT_CAPABILITIES), "uniqueItems": True,
                "items": {"type": "string", "enum": list(fleet_protocol.PROJECT_CAPABILITIES)}})
        definitions.append({"name": name, "title": {"investigation_status": "Retained investigation status", "investigation_result": "Retained investigation observations", "project_observe": "Selected retained project observations"}[name],
            "description": "Read an existing caller-bound investigation handle. No new request, probe, model call or effect is started. Retained timestamps and freshness gaps remain explicit.",
            "inputSchema": {"type": "object", "additionalProperties": False, "required": sorted(properties), "properties": properties},
            "outputSchema": {"type": "object", "required": ["schema", "request_id"], "properties": {"schema": {"const": REFERENCE_STATUS if name == "investigation_status" else REFERENCE_RESULT}, "request_id": {"type": "string"}}},
            "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}})
    return definitions


INVESTIGATION_TOOLS = ("investigation_start", "investigation_status", "investigation_result", "investigation_cancel")


def investigation_definitions(legacy=False):
    import fleet_mcp_admission as admission
    definitions = []
    for name in INVESTIGATION_TOOLS:
        start = name == "investigation_start"
        properties = ({"source_update": {"type": "integer", "minimum": 0, "maximum": 2**53-1},
            "idempotency_key": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"},
            **{key: {"type": "string", "enum": sorted(values)} for key, values in admission.FORMATS.items()}}
            if start else {"request_id": {"type": "string", "pattern": "^[a-f0-9]{32}$"}})
        required = ["source_update", "idempotency_key"] if start else ["request_id"]
        if legacy and name == "investigation_result":
            properties.update(cursor={"type": "string", "maxLength": 1024}, max_age_seconds={"type": "integer", "minimum": 1, "maximum": 86400})
        output = {"type": "object", "required": ["version", "request_id", "status"], "properties": {
            "version": {"const": admission.VERSION}, "request_id": {"type": "string", "pattern": "^[a-f0-9]{32}$"}, "status": {"enum": ["available", "unavailable"]}}}
        if legacy and name in {"investigation_status", "investigation_result"}:
            output = {"anyOf": [output, next(item["outputSchema"] for item in reference_definitions() if item["name"] == name)]}
        definitions.append({"name": name, "title": name.replace("_", " ").capitalize(),
            "description": ("Admit one existing owner message to the original fleet read workflow. Reuse the same idempotency key after an uncertain response. No question, target, effect or transport argument is accepted." if start else
                "Request cancellation of an existing caller-bound investigation; requested is distinct from settled." if name.endswith("cancel") else
                "Read the original caller-bound investigation ledger and retained answer pages. No planning or probe is started."),
            "inputSchema": {"type": "object", "additionalProperties": False, "required": required, "properties": properties},
            "outputSchema": output,
            "annotations": {"readOnlyHint": name in {"investigation_status", "investigation_result"}, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}})
    return definitions


def investigation_arguments(tool, value):
    """Code-owned base handle API. Never evaluates a source or creates work."""
    import fleet_mcp_admission as admission
    if tool not in INVESTIGATION_TOOLS or not isinstance(value, dict):
        raise MCPError("invalid investigation tool")
    if tool == "investigation_start":
        if (not {"source_update", "idempotency_key"} <= set(value) or set(value) - {"source_update", "idempotency_key", *admission.FORMATS}
                or type(value["source_update"]) is not int or not 0 <= value["source_update"] <= 2**53-1
                or not isinstance(value["idempotency_key"], str) or not admission.KEY.fullmatch(value["idempotency_key"])
                or any(not isinstance(value[key], str) or value[key] not in admission.FORMATS[key] for key in set(value) & set(admission.FORMATS))):
            raise MCPError("invalid source-bound start arguments")
    elif set(value) != {"request_id"} or not isinstance(value["request_id"], str) or not fleet_protocol.TOKEN.fullmatch(value["request_id"]):
        raise MCPError("invalid investigation reference arguments")
    return copy.deepcopy(value)


def tool_definitions(config=None):
    config = config or {}
    context = investigation_context(config)
    if context is not None and context["policy"]["enabled"]:
        return [tool_definition(), *investigation_definitions(legacy="references" in config),
                *([item for item in reference_definitions() if item["name"] == "project_observe"] if "references" in config else [])]
    return [tool_definition()] + (reference_definitions() if config.get("references") is not None else [])


def _reference_time(value):
    if not isinstance(value, str) or len(value) > 35 or not (value.endswith("Z") or value.endswith("+00:00")):
        raise MCPError("invalid retained timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise MCPError("invalid retained timestamp") from None
    if parsed.utcoffset() != timedelta(0):
        raise MCPError("retained timestamp must be UTC")
    return parsed


def _reference_gap(tool, request_id, reason):
    return {"schema": REFERENCE_STATUS if tool == "investigation_status" else REFERENCE_RESULT,
            "request_id": request_id, "status": "unavailable", "reason": reason}


class RetainedReferences:
    """Read the same fleet_requests ledger; never create work or persist copies.

    Production stdio enablement additionally requires read-only WAL/SHM access,
    installed-source qualification and coordinated purge quiescence. This module
    does not install those bindings. immutable=1 is deliberately never used: it
    would hide current WAL updates, edits and deletions from the authority check.
    """
    def __init__(self, policy, principal="cara", clock=None, admission_context=None):
        if not isinstance(principal, str) or not facts.ID.fullmatch(principal):
            raise MCPError("invalid retained principal")
        self.policy, self.principal = reference_policy(policy), principal
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.admission_context = copy.deepcopy(admission_context)

    def _connect(self):
        path = Path(self.policy["database_path"])
        if os.name != "posix" or not hasattr(os, "geteuid"):
            raise MCPError("retained local channel requires Linux")
        facts._no_symlinks(path)
        for item in (path, *path.parents):
            info = item.lstat()
            if info.st_uid not in {0, os.geteuid()} or (info.st_mode & 0o022 and not (item != path and info.st_mode & stat.S_ISVTX)):
                raise MCPError("retained local channel ownership mismatch")
            if item == path and not stat.S_ISREG(info.st_mode):
                raise MCPError("retained local channel is not a regular file")
        # Sidecars are only inspected when already present. Deployment must grant
        # this adapter read-only access to the DB/directory and existing sidecars.
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(str(path) + suffix)
            if sidecar.exists() or sidecar.is_symlink():
                facts._no_symlinks(sidecar)
                info = sidecar.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_uid not in {0, os.geteuid()} or info.st_mode & 0o022:
                    raise MCPError("unsafe retained sidecar")
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.1)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA busy_timeout=100")
            if hasattr(connection, "setlimit"):
                connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_RETAINED_BYTES + 4096)
            expires = time.monotonic() + 0.2
            connection.set_progress_handler(lambda: int(time.monotonic() >= expires), 500)
            # Fixed source tables/columns only, no ATTACH, DDL, extension loading,
            # writes or arbitrary functions even if a future SQL edit is mistaken.
            columns = {"fleet_requests": {"id", "chat_id", "source_update", "state", "payload", "created_at", "updated_at"},
                "kv": {"key", "value"}, "telegram_updates": {"update_id", "chat_id"},
                "conversation": {"id", "chat_id", "update_id", "role", "source", "text"}}
            def authorize(action, first, second, _database, _trigger):
                if action == sqlite3.SQLITE_READ:
                    return sqlite3.SQLITE_OK if first in columns and second in columns[first] else sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_FUNCTION:
                    return sqlite3.SQLITE_OK if second in {"substr", "length"} else sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK if action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_TRANSACTION} else sqlite3.SQLITE_DENY
            connection.set_authorizer(authorize)
            return connection
        except BaseException:
            connection.close()
            raise

    def _arguments(self, tool, value):
        required = {"request_id"} | (set() if tool == "investigation_status" else {"cursor", "max_age_seconds"}) | ({"projects", "capabilities"} if tool == "project_observe" else set())
        if tool not in REFERENCE_TOOLS or not isinstance(value, dict) or set(value) != required or not isinstance(value["request_id"], str) or not re.fullmatch(r"[a-f0-9]{32}", value["request_id"]):
            raise MCPError("invalid retained read arguments")
        if tool != "investigation_status":
            if not isinstance(value["cursor"], str) or len(value["cursor"]) > 1024 or type(value["max_age_seconds"]) is not int or not 1 <= value["max_age_seconds"] <= self.policy["max_age_seconds"]:
                raise MCPError("retained read exceeds freshness policy")
        if tool == "project_observe":
            for field, policy in (("projects", "allowed_projects"), ("capabilities", "allowed_capabilities")):
                items = value[field]
                if not isinstance(items, list) or not items or len(items) > (20 if field == "projects" else len(fleet_protocol.PROJECT_CAPABILITIES)) or any(not isinstance(item, str) for item in items) or len(items) != len(set(items)):
                    raise MCPError("invalid retained selected scope")
                if any(item not in self.policy[policy] for item in items):
                    raise ScopeDenied("retained read exceeds trusted caller scope")
        return value

    def _load(self, request_id):
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            loaded = fleet_shared_reads.canonical_reference(
                connection, self.policy["owner_chat_id"], request_id=request_id)
            if not loaded[2] and "mcp_admission" in loaded[1]:
                if self.admission_context is None:
                    raise ScopeDenied("admitted references require the same trusted admission policy")
                import fleet_mcp_admission as admission
                current = None
                try:
                    current = admission.connect(self.admission_context)
                    if admission.classify(current, self.admission_context, request_id) != "admitted":
                        raise ScopeDenied("admitted reference changed")
                except admission.ScopeDenied:
                    raise ScopeDenied("admitted reference scope denied") from None
                finally:
                    if current is not None:
                        current.close()
            return loaded
        except fleet_shared_reads.ReferenceError:
            raise MCPError("retained canonical source is invalid") from None
        finally:
            connection.close()

    def _scope(self, payload):
        try:
            return fleet_shared_reads.read_scope(payload,
                self.policy["allowed_hosts"], self.policy["allowed_projects"],
                self.policy["allowed_capabilities"])
        except fleet_shared_reads.ScopeDenied:
            raise ScopeDenied("retained read exceeds trusted caller scope") from None
        except ValueError:
            raise MCPError("retained scope is invalid") from None

    def _observations(self, payload, scope):
        # Only current qualified project-read contracts expose retained values.
        # Legacy/unknown schemas remain readable status references, never widened
        # host/project observations or guessed compatibility.
        if scope["read_scope"] is None or scope["kind"] not in {"projects", "all_projects"}:
            raise MCPError("retained project read schema is unavailable")
        if not fleet_protocol.read_contract_compatible(payload["plan"].get("read_contract_hash")):
            raise MCPError("retained capability contract changed")
        raw = payload.get("result")
        required = {"version", "observed_at", "hosts", "read_scope"}
        if not isinstance(raw, dict) or set(raw) != required or raw["version"] != fleet_protocol.VERSION or raw["read_scope"] != scope["read_scope"] or not isinstance(raw["hosts"], dict) or set(raw["hosts"]) != set(scope["hosts"]):
            raise MCPError("retained observation scope mismatch")
        result_time = _reference_time(raw["observed_at"])
        observations = []
        for host in scope["hosts"]:
            observed = raw["hosts"][host]
            if not isinstance(observed, dict) or set(observed) != {"host", "observed_at", "read_scope", "projects"} or observed["host"] != host:
                raise MCPError("retained host scope mismatch")
            host_time = _reference_time(observed["observed_at"])
            selected = fleet_protocol.read_scope(observed["read_scope"])
            if selected["kind"] != scope["kind"] or selected["version"] != scope["read_scope"]["version"] or selected["capabilities"] != scope["capabilities"] or not set(selected["projects"]) <= set(scope["projects"]):
                raise MCPError("retained host project scope mismatch")
            if not isinstance(observed["projects"], dict) or set(observed["projects"]) != set(selected["projects"]):
                raise MCPError("retained project coverage mismatch")
            for project in selected["projects"]:
                capabilities = observed["projects"][project]
                if not isinstance(capabilities, dict) or set(capabilities) != set(scope["capabilities"]):
                    raise MCPError("retained capability coverage mismatch")
                for capability in scope["capabilities"]:
                    value = capabilities[capability]
                    if not isinstance(value, dict):
                        raise MCPError("invalid retained capability object")
                    metadata = fleet_protocol.capability_implementation(capability, value.get("implementation_version"))
                    allowed = {"status", "observed_at", "implementation_version", "reason", *metadata["outputs"]}
                    if not isinstance(value, dict) or not {"status", "observed_at", "implementation_version"} <= set(value) or set(value) - allowed or value["implementation_version"] != metadata["implementation_version"] or not isinstance(value["status"], str) or value["status"] not in fleet_protocol.READ_STATUSES or ("reason" in value and value["reason"] not in fleet_protocol.READ_REASONS):
                        raise MCPError("retained capability version/fields mismatch")
                    _reference_time(value["observed_at"])
                    fleet_protocol._capability_payload(capability, value)
                    fleet_protocol.safe_tree(value)
                    observations.append({"host_id": host, "project_id": project, "capability": capability,
                        "source": "host-gateway", "window_seconds": fleet_protocol.capability_contract()[capability]["window_seconds"],
                        "observation": copy.deepcopy(value)})
            if host_time > result_time + timedelta(seconds=30):
                raise MCPError("retained host date exceeds result date")
        if set(scope["projects"]) != {item["project_id"] for item in observations}:
            raise MCPError("retained selected project coverage is incomplete")
        return observations, raw["observed_at"]

    def read(self, tool, arguments):
        arguments = self._arguments(tool, arguments)
        request_id = arguments["request_id"]
        try:
            row, payload, gap = self._load(request_id)
            if gap:
                return _reference_gap(tool, request_id, gap)
            now = self.clock()
            if not isinstance(now, datetime) or now.utcoffset() != timedelta(0):
                raise MCPError("invalid reference clock")
            created, updated = _reference_time(row["created_at"]), _reference_time(row["updated_at"])
            if updated < created or (updated - now).total_seconds() > 30 or (created - now).total_seconds() > 30:
                raise MCPError("invalid retained request dates")
            retained_until = created + timedelta(seconds=self.policy["retention_seconds"])
            if now >= retained_until:
                return _reference_gap(tool, request_id, "retained_reference_expired")
            if row["state"] not in REFERENCE_STATES:
                raise ScopeDenied("unsupported retained request state")
            scope = self._scope(payload)
            observations, observed_at = [], None
            result_gap = None
            if payload.get("result") is not None and row["state"] not in {"failed", "cancelled", "ambiguous"}:
                try:
                    observations, observed_at = self._observations(payload, scope)
                except (ValueError, TypeError, AttributeError):
                    result_gap = "unsupported_retained_schema"
            else:
                result_gap = "result_not_available"
            revision = fleet_protocol.digest({"id": request_id, "state": row["state"], "payload": payload,
                "created_at": row["created_at"], "updated_at": row["updated_at"]})
            if observed_at is not None and (_reference_time(observed_at) - now).total_seconds() > 30:
                raise MCPError("retained result date is in the future")
            header = {"request_id": request_id, "state": row["state"], "created_at": row["created_at"],
                "updated_at": row["updated_at"], "retained_until": retained_until.isoformat(), "revision": revision}
            if tool == "investigation_status":
                phase = row["state"].split("_", 1)[0] if row["state"].startswith(("planning_", "synthesis_")) else row["state"]
                result = {"schema": REFERENCE_STATUS, **header, "phase": phase, "result_available": result_gap is None,
                    "scope": {key: value for key, value in scope.items() if key != "read_scope"},
                    "gaps": [result_gap] if result_gap else [], "source_hash": payload["source_hash"]}
                phase = payload.get("inference", {})
                if phase:
                    bound = phase.get("binding") if isinstance(phase, dict) else None
                    if not isinstance(bound, dict) or bound.get("request_id") != request_id or bound.get("phase") not in {"planning", "synthesis"} or not isinstance(bound.get("nonce"), str) or not re.fullmatch(r"[a-f0-9]{32}", bound["nonce"]) or phase.get("nonce") != bound["nonce"] or bound.get("source_hash") != payload["source_hash"]:
                        raise MCPError("retained inference identity mismatch")
                    for field in ("source_hash", "input_hash", "inventory_hash", "facts_hash", "capability_hash"):
                        if not isinstance(bound.get(field), str) or not re.fullmatch(r"[a-f0-9]{64}", bound[field]):
                            raise MCPError("retained inference authority mismatch")
                    if not isinstance(bound.get("policy_version"), str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}/v[0-9]{1,3}", bound["policy_version"]):
                        raise MCPError("retained inference policy mismatch")
                    result["phase_identity"] = {field: bound[field] for field in ("request_id", "phase", "nonce", "source_hash", "input_hash", "inventory_hash", "facts_hash", "capability_hash", "policy_version")}
                executor = payload.get("executor_started") or payload.get("executor_lifecycle")
                if isinstance(executor, dict):
                    result["executor"] = {key: executor[key] for key in ("queued_at", "queue_expires_at", "started_at", "execution_expires_at") if key in executor}
                    for value in result["executor"].values():
                        _reference_time(value)
            else:
                if result_gap:
                    return _reference_gap(tool, request_id, result_gap)
                projects = arguments.get("projects", scope["projects"])
                capabilities = arguments.get("capabilities", scope["capabilities"])
                if any(project not in scope["projects"] for project in projects) or any(capability not in scope["capabilities"] for capability in capabilities):
                    raise ScopeDenied("retained selection exceeds original investigation")
                observations = [item for item in observations if item["project_id"] in projects and item["capability"] in capabilities]
                selected_hash = fleet_protocol.digest({"projects": projects, "capabilities": capabilities, "max_age_seconds": arguments["max_age_seconds"]})
                offset = self._cursor(arguments["cursor"], request_id, revision, selected_hash)
                result = {"schema": REFERENCE_RESULT, **header, "result_observed_at": observed_at,
                    "scope": {"hosts": scope["hosts"], "projects": projects, "capabilities": capabilities, "kind": scope["kind"]},
                    "requested_max_age_seconds": arguments["max_age_seconds"], "observations": [], "next_cursor": "",
                    "gaps": ["retained_observations_only"]}
                if offset >= len(observations) and offset != 0:
                    raise MCPError("retained cursor exceeds result")
                for item in observations[offset:offset + MAX_REFERENCE_CELLS]:
                    age = (now - _reference_time(item["observation"]["observed_at"])).total_seconds()
                    if age < -30:
                        raise MCPError("retained observation date is in future")
                    cell = dict(item, freshness="within_window" if age <= arguments["max_age_seconds"] else "stale")
                    candidate = dict(result, observations=result["observations"] + [cell])
                    if len(facts.canonical(candidate).encode()) > MAX_REFERENCE_PAGE:
                        break
                    result["observations"].append(cell)
                end = offset + len(result["observations"])
                if end < len(observations):
                    if end == offset:
                        raise MCPError("one retained observation exceeds response bound")
                    result["next_cursor"] = self._encode_cursor(request_id, revision, selected_hash, end)
            # Reopen the current ledger after construction, never trust a cached
            # transcript/snapshot after source edits, purge or concurrent changes.
            fresh_row, fresh_payload, gap = self._load(request_id)
            if gap:
                return _reference_gap(tool, request_id, gap)
            fresh = fleet_protocol.digest({"id": request_id, "state": fresh_row["state"], "payload": fresh_payload,
                "created_at": fresh_row["created_at"], "updated_at": fresh_row["updated_at"]})
            if fresh != revision:
                return _reference_gap(tool, request_id, "reference_changed")
            if len(facts.canonical(result).encode()) > MAX_REFERENCE_PAGE:
                raise MCPError("retained result exceeds page bound")
            return result
        except ScopeDenied:
            raise
        except (OSError, sqlite3.Error, ValueError, TypeError, AttributeError, UnicodeError, RecursionError):
            return _reference_gap(tool, request_id, "retained_read_unavailable")

    def _encode_cursor(self, request_id, revision, scope_hash, offset):
        value = {"schema": REFERENCE_RESULT, "principal": self.principal, "request_id": request_id,
                 "revision": revision, "scope_hash": scope_hash, "offset": offset}
        return base64.urlsafe_b64encode(facts.canonical(value).encode()).decode().rstrip("=")

    def _cursor(self, value, request_id, revision, scope_hash):
        if not value:
            return 0
        try:
            raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
            cursor = decode(raw)
        except (ValueError, UnicodeError):
            raise MCPError("invalid retained cursor") from None
        expected = {"schema": REFERENCE_RESULT, "principal": self.principal, "request_id": request_id,
                    "revision": revision, "scope_hash": scope_hash}
        if not isinstance(cursor, dict) or set(cursor) != set(expected) | {"offset"} or any(cursor[key] != item for key, item in expected.items()) or type(cursor["offset"]) is not int or not 0 <= cursor["offset"] <= 2400:
            raise MCPError("retained cursor binding changed")
        return cursor["offset"]


def _unique_pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            raise MCPError("duplicate retained JSON key")
        value[key] = item
    return value

class FactsAdapter:
    """One local trusted policy per adapter; protocol names/metadata grant no reads."""
    def __init__(self, loader, allowed_subjects, principal="cara", clock=time.monotonic, limit=120, references=None, investigations=None):
        if (not callable(loader) or not isinstance(principal, str) or not facts.ID.fullmatch(principal)
                or type(limit) is not int or not 1 <= limit <= 240
                or not isinstance(allowed_subjects, (set, frozenset))
                or any(not isinstance(identity, str) or not facts.ID.fullmatch(identity) for identity in allowed_subjects)):
            raise MCPError("invalid trusted MCP adapter construction")
        self.loader, self.allowed_subjects, self.principal = loader, frozenset(allowed_subjects), principal
        self.clock, self.limit, self.calls = clock, limit, deque()
        self.config = {"principal": principal, "allowed_subjects": sorted(self.allowed_subjects)}
        if references is not None:
            self.config["references"] = references
        if investigations is not None:
            self.config["investigations"] = investigations
        self.investigations = investigation_context(self.config)
        self.references = RetainedReferences(references, principal, admission_context=self.investigations) if references is not None else None
        self.tools = tool_definitions(self.config)

    def _investigation(self, tool, arguments):
        import fleet_mcp_admission as admission
        connection = None
        try:
            connection = admission.connect(self.investigations, write=tool in {"investigation_start", "investigation_cancel"})
            if tool in {"investigation_status", "investigation_result", "project_observe"}:
                if not isinstance(arguments.get("request_id"), str):
                    raise MCPError("invalid investigation identity")
                kind = admission.classify(connection, self.investigations, arguments["request_id"])
                if kind == "legacy":
                    if self.references is None:
                        raise ScopeDenied("legacy reference policy is unavailable")
                    legacy = copy.deepcopy(arguments)
                    if tool != "investigation_status":
                        legacy.setdefault("cursor", "")
                        legacy.setdefault("max_age_seconds", self.references.policy["max_age_seconds"])
                    return self.references.read(tool, legacy)
                if tool == "project_observe":
                    if self.references is None:
                        raise ScopeDenied("project reference policy is unavailable")
                    return self.references.read(tool, arguments)
            return {"investigation_start": admission.start, "investigation_status": admission.status,
                    "investigation_result": admission.result, "investigation_cancel": admission.cancel}[tool](connection, self.investigations, arguments)
        except admission.ScopeDenied:
            raise ScopeDenied("investigation caller scope denied") from None
        finally:
            if connection is not None:
                connection.close()

    def handle(self, message):
        identity = message.get("id") if isinstance(message, dict) else None
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            return _error(None, -32600, "Invalid Request")
        if "id" not in message:
            # RPC cancellation never invents cancellation of an application handle.
            # The explicit caller-bound cancellation tool owns that lifecycle.
            return None
        if not ((type(identity) is int and -(2**53-1) <= identity <= 2**53-1)
                or (isinstance(identity, str) and 1 <= len(identity) <= 96 and not re.search(r"[\x00-\x1f\x7f]", identity))):
            return _error(None, -32600, "Invalid Request")
        if set(message) != {"jsonrpc", "id", "method", "params"} or not isinstance(message["params"], dict):
            return _error(identity, -32602, "Invalid params")
        method, params = message["method"], message["params"]
        if method == "initialize":
            return _error(identity, -32601, "Only MCP 2026-07-28 is supported; use server/discover", {"supported": [PROTOCOL]})
        metadata = params.get("_meta")
        if not isinstance(metadata, dict) or not isinstance(metadata.get(PREFIX + "protocolVersion"), str) or not isinstance(metadata.get(PREFIX + "clientCapabilities"), dict):
            return _error(identity, -32602, "Missing or invalid per-request MCP metadata")
        version = metadata[PREFIX + "protocolVersion"]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", version):
            return _error(identity, -32602, "Invalid protocol version")
        if version != PROTOCOL:
            return _error(identity, -32022, "Unsupported protocol version", {"supported": [PROTOCOL], "requested": version})
        now = self.clock()
        while self.calls and now - self.calls[0] >= 60:
            self.calls.popleft()
        if len(self.calls) >= self.limit:
            return _error(identity, -32000, "Local facts request limit reached")
        self.calls.append(now)
        if method in {"server/discover", "ping", "tools/list"}:
            if set(params) != {"_meta"}:
                return _error(identity, -32602, "Invalid params")
            if method == "server/discover":
                result = _complete({"supportedVersions": [PROTOCOL], "capabilities": {"tools": {}}})
            elif method == "tools/list":
                result = _complete({"tools": copy.deepcopy(self.tools)})
            else:
                result = _complete({})
        elif method == "tools/call":
            if set(params) != {"_meta", "name", "arguments"} or params.get("name") not in [tool["name"] for tool in self.tools] or not isinstance(params.get("arguments"), dict):
                return _error(identity, -32602, "Unknown tool or invalid tool-call structure")
            try:
                selected = params["arguments"].get("subjects") if params["name"] == TOOL else None
                if isinstance(selected, list) and all(isinstance(subject, str) for subject in selected) and any(subject not in self.allowed_subjects for subject in selected):
                    raise ScopeDenied("caller scope denied")
                if params["name"] == TOOL:
                    value = lookup(self.loader(), params["arguments"], self.allowed_subjects)
                elif self.investigations is not None and self.investigations["policy"]["enabled"]:
                    value = self._investigation(params["name"], params["arguments"])
                else:
                    value = self.references.read(params["name"], params["arguments"])
                result = _complete({"structuredContent": value,
                                    "content": [{"type": "text", "text": facts.canonical(value)}], "isError": False})
            except ScopeDenied:
                result = _complete({"content": [{"type": "text", "text": "Caller scope denied."}], "isError": True})
            except (ValueError, OSError, TypeError, sqlite3.Error):
                result = _complete({"content": [{"type": "text", "text": "Published facts or requested lookup are unavailable."}], "isError": True})
        else:
            return _error(identity, -32601, "Method not found")
        response = {"jsonrpc": "2.0", "id": identity, "result": result}
        if len(facts.canonical(response).encode("utf-8")) > MAX_RESPONSE_BYTES:
            return _error(identity, -32000, "Facts result exceeds the response bound")
        return response


def decode(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_REQUEST_BYTES:
        raise MCPError("MCP input exceeds framing bound")
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise MCPError("duplicate JSON key")
            result[key] = value
        return result
    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                      parse_constant=lambda _value: (_ for _ in ()).throw(MCPError("nonfinite JSON")))


def serve(adapter, input_stream, output_stream):
    while True:
        line = input_stream.readline(MAX_REQUEST_BYTES + 1)
        if not line:
            return 0
        if len(line) > MAX_REQUEST_BYTES or not line.endswith(b"\n"):
            output_stream.write((facts.canonical(_error(None, -32700, "Invalid or oversized JSON-RPC frame")) + "\n").encode())
            output_stream.flush()
            return 2
        try:
            message = decode(line)
            response = adapter.handle(message)
        except (ValueError, UnicodeError, RecursionError):
            response = _error(None, -32700, "Parse error")
        if response is not None:
            output_stream.write((facts.canonical(response) + "\n").encode("utf-8"))
            output_stream.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline/local read-only fleet facts MCP adapter")
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    try:
        config = read_config(args.config)
        adapter = FactsAdapter(lambda: facts.load(config["facts_path"]), frozenset(config["allowed_subjects"]), config["principal"], references=config.get("references"), investigations=config.get("investigations"))
    except (ValueError, OSError):
        print("MCP local configuration is unavailable.", file=sys.stderr)
        return 2
    return serve(adapter, sys.stdin.buffer, sys.stdout.buffer)


if __name__ == "__main__":
    raise SystemExit(main())

