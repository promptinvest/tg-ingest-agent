"""Offline CF-07 modern MCP facts adapter; not installed or enabled by this module.

Pinned protocol: 2026-07-28. References checked October 24, 2026:
https://modelcontextprotocol.io/specification/2026-07-28/basic/index
https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning
https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio
https://modelcontextprotocol.io/specification/2026-07-28/server/discover
https://modelcontextprotocol.io/specification/2026-07-28/server/tools

Only local descriptive facts are exposed. No effects, project probes, model calls,
Tasks or competing content/job ledger exist here. Self-reported MCP clientInfo
never establishes identity; the launching operator-owned config binds authority.
"""
import argparse
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
    if not isinstance(value, dict) or set(value) != {"schema", "principal", "allowed_subjects", "facts_path"} or value["schema"] != CONFIG_SCHEMA:
        raise MCPError("invalid MCP local policy")
    principal, subjects, source = value["principal"], value["allowed_subjects"], value["facts_path"]
    if (not isinstance(principal, str) or not facts.ID.fullmatch(principal)
            or not isinstance(subjects, list) or not 1 <= len(subjects) <= 128
            or any(not isinstance(identity, str) or not facts.ID.fullmatch(identity) for identity in subjects)
            or len(set(subjects)) != len(subjects)
            or not isinstance(source, str) or not Path(source).is_absolute() or len(source) > 512
            or re.search(r"[\x00-\x1f\x7f]", source)):
        raise MCPError("invalid MCP caller scope or facts configuration")
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


class FactsAdapter:
    """One local trusted policy per adapter; protocol names/metadata grant no reads."""
    def __init__(self, loader, allowed_subjects, principal="cara", clock=time.monotonic, limit=120):
        if (not callable(loader) or not isinstance(principal, str) or not facts.ID.fullmatch(principal)
                or type(limit) is not int or not 1 <= limit <= 240
                or not isinstance(allowed_subjects, (set, frozenset))
                or any(not isinstance(identity, str) or not facts.ID.fullmatch(identity) for identity in allowed_subjects)):
            raise MCPError("invalid trusted MCP adapter construction")
        self.loader, self.allowed_subjects, self.principal = loader, frozenset(allowed_subjects), principal
        self.clock, self.limit, self.calls = clock, limit, deque()

    def handle(self, message):
        identity = message.get("id") if isinstance(message, dict) else None
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            return _error(None, -32600, "Invalid Request")
        if "id" not in message:
            # There is no durable execution in this adapter. Cancellation requests
            # cause no fabricated lifecycle result and notifications get no reply.
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
                result = _complete({"tools": [tool_definition()]})
            else:
                result = _complete({})
        elif method == "tools/call":
            if set(params) != {"_meta", "name", "arguments"} or params.get("name") != TOOL or not isinstance(params.get("arguments"), dict):
                return _error(identity, -32602, "Unknown tool or invalid tool-call structure")
            try:
                selected = params["arguments"].get("subjects")
                if isinstance(selected, list) and all(isinstance(subject, str) for subject in selected) and any(subject not in self.allowed_subjects for subject in selected):
                    raise ScopeDenied("caller scope denied")
                value = lookup(self.loader(), params["arguments"], self.allowed_subjects)
                result = _complete({"structuredContent": value,
                                    "content": [{"type": "text", "text": facts.canonical(value)}], "isError": False})
            except ScopeDenied:
                result = _complete({"content": [{"type": "text", "text": "Caller scope denied."}], "isError": True})
            except (ValueError, OSError, TypeError):
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
        adapter = FactsAdapter(lambda: facts.load(config["facts_path"]), frozenset(config["allowed_subjects"]), config["principal"])
    except (ValueError, OSError):
        print("MCP local configuration is unavailable.", file=sys.stderr)
        return 2
    return serve(adapter, sys.stdin.buffer, sys.stdout.buffer)


if __name__ == "__main__":
    raise SystemExit(main())

