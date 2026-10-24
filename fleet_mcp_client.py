"""Optional offline CF-07 facts client; existing internal reads stay independent.

Nothing imports or enables this adapter in Cara's production flow. All transport
configuration is trusted operator configuration; none comes from model arguments.
Only the reviewed fixed facts tool/schema is accepted. Server instructions and
self-reported implementation identity never enter Cara's action set or prompts.
"""
import copy
import json
import os
import queue
import stat
import subprocess
import sys
import threading
from pathlib import Path

import fleet_facts as facts
import fleet_mcp as protocol
import fleet_queries as queries

_TRANSPORT = threading.BoundedSemaphore(1)

SAFE_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
            "PYTHONIOENCODING": "utf-8", "TZ": "UTC"}


def request(identity, method, **params):
    return {"jsonrpc": "2.0", "id": identity, "method": method,
            "params": {**params, "_meta": {protocol.PREFIX + "protocolVersion": protocol.PROTOCOL,
                protocol.PREFIX + "clientInfo": {"name": "cara-facts-client", "version": "1"},
                protocol.PREFIX + "clientCapabilities": {}}}}


def _stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=0.3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=0.3)


def stdio_exchange(config_path, messages, timeout=3.0):
    if not _TRANSPORT.acquire(blocking=False):
        raise protocol.MCPError("MCP facts transport busy; retain equivalent internal lookup")
    try:
        return _stdio_exchange(config_path, messages, timeout)
    finally:
        _TRANSPORT.release()


def _stdio_exchange(config_path, messages, timeout=3.0):
    """Fixed local adapter, isolated interpreter/env, bounded pipes and deadline."""
    if os.name != "posix" or not isinstance(timeout, (int, float)) or type(timeout) is bool or not 0.1 <= timeout <= 5:
        raise protocol.MCPError("unsupported bounded local MCP transport")
    protocol.read_config(config_path)
    adapter = Path(__file__).with_name("fleet_mcp.py").absolute()
    facts._no_symlinks(adapter)
    metadata = adapter.stat()
    if metadata.st_uid != 0 or metadata.st_mode & 0o022 or not stat.S_ISREG(metadata.st_mode):
        raise protocol.MCPError("MCP adapter program is not trusted")
    body = b"".join((facts.canonical(message) + "\n").encode("utf-8") for message in messages)
    if len(messages) != 3 or len(body) > protocol.MAX_REQUEST_BYTES:
        raise protocol.MCPError("invalid bounded MCP exchange")
    process = subprocess.Popen([sys.executable, "-I", str(adapter), "--config", str(config_path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        env=SAFE_ENV.copy(), cwd="/", close_fds=True, start_new_session=True)
    result = queue.Queue(maxsize=1)
    def write():
        try:
            process.stdin.write(body)
            process.stdin.flush()
        except (OSError, ValueError):
            pass
        finally:
            try:
                process.stdin.close()
            except (OSError, ValueError):
                pass
    def read():
        output = bytearray()
        try:
            while True:
                chunk = process.stdout.read(min(65536, protocol.MAX_RESPONSE_BYTES + 1 - len(output)))
                if not chunk:
                    result.put(bytes(output))
                    return
                output.extend(chunk)
                if len(output) > protocol.MAX_RESPONSE_BYTES:
                    result.put(protocol.MCPError("MCP output exceeds the response bound"))
                    return
        except (OSError, ValueError):
            result.put(protocol.MCPError("MCP output unavailable"))
    writer = threading.Thread(target=write, name="mcp-facts-write", daemon=True)
    reader = threading.Thread(target=read, name="mcp-facts-read", daemon=True)
    writer.start()
    reader.start()
    try:
        try:
            output = result.get(timeout=timeout)
        except queue.Empty:
            raise protocol.MCPError("MCP facts deadline expired") from None
        if isinstance(output, Exception):
            raise output
        process.wait(timeout=0.3)
        if process.returncode != 0:
            raise protocol.MCPError("MCP facts process failed")
        lines = output.splitlines(keepends=True)
        if len(lines) != 3 or any(not line.endswith(b"\n") for line in lines):
            raise protocol.MCPError("MCP response framing mismatch")
        # Responses have a larger bound than requests; parser duplicate/nonfinite
        # handling is reused only after each complete response is bounded here.
        def pairs(items):
            value = {}
            for key, item in items:
                if key in value:
                    raise protocol.MCPError("duplicate MCP response JSON key")
                value[key] = item
            return value
        return [json.loads(line.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=lambda _value: (_ for _ in ()).throw(protocol.MCPError("nonfinite MCP response")))
                for line in lines]
    finally:
        _stop(process)
        writer.join(timeout=0.3)
        reader.join(timeout=0.3)
        process.stdout.close()


def validate_responses(messages, responses, expected):
    if not isinstance(responses, list) or len(responses) != len(messages):
        raise protocol.MCPError("MCP response coverage mismatch")
    results = []
    for message, response in zip(messages, responses):
        if (not isinstance(response, dict) or set(response) != {"jsonrpc", "id", "result"}
                or response["jsonrpc"] != "2.0" or response["id"] != message["id"]
                or not isinstance(response["result"], dict) or response["result"].get("resultType") != "complete"):
            raise protocol.MCPError("MCP correlation/version/result mismatch")
        results.append(response["result"])
    discovered, listed, called = results
    if (set(discovered) != {"resultType", "supportedVersions", "capabilities", "_meta"}
            or discovered["supportedVersions"] != [protocol.PROTOCOL] or discovered["capabilities"] != {"tools": {}}):
        raise protocol.MCPError("MCP discovery differs from reviewed capability manifest")
    if set(listed) != {"resultType", "tools", "_meta"} or listed["tools"] != [protocol.tool_definition()]:
        raise protocol.MCPError("MCP tool/schema differs from reviewed capability manifest")
    if (set(called) != {"resultType", "content", "structuredContent", "isError", "_meta"}
            or called["isError"] is not False or called["structuredContent"] != expected
            or called["content"] != [{"type": "text", "text": facts.canonical(expected)}]):
        raise protocol.MCPError("MCP facts omit or change requested grounded obligations")
    for result in results:
        if result["_meta"] != {protocol.PREFIX + "serverInfo": protocol.SERVER_INFO}:
            raise protocol.MCPError("MCP result metadata differs from the reviewed manifest")
    return copy.deepcopy(called["structuredContent"])


def lookup(cfg, document, plan, lang="en", exchange=None):
    """Return owner-facing pages with equivalent local facts on interface failure.

    Enabling MCP is a later explicit integration/release step. A scope denial is
    never bypassed by fallback. Interface errors use the exact same already-bound
    local obligations; no host probe, synthesis call, retry or ledger is created.
    """
    document = facts.validate(document, allow_fixture=True)
    lang = "ru" if str(lang).lower().startswith("ru") else "en"
    arguments = protocol.arguments(plan, lang)
    raw = dict(plan, scope="explicit")
    normalized = queries.validate_plan(raw, document)
    baseline = {"schema": protocol.CAPABILITY, "revision": document["revision"],
                "facts_integrity": document["integrity"], "plan": normalized,
                "cells": queries.cells(document, normalized)}
    pages = queries.render(document, normalized, lang)
    result = {"path": "internal", "reason": "disabled", "facts_integrity": document["integrity"],
              "cells": copy.deepcopy(baseline["cells"]), "pages": pages}
    if getattr(cfg, "fleet_mcp_enabled", False) is not True:
        return result
    path = getattr(cfg, "fleet_mcp_config", "/etc/cara-fleet/mcp-facts.json")
    try:
        config = protocol.read_config(path)
    except (ValueError, OSError):
        result["reason"] = "configuration_unavailable"
        return result
    if any(subject not in config["allowed_subjects"] for subject in normalized["subjects"]):
        raise protocol.ScopeDenied("MCP caller scope denied")
    # No tool-selected path or subject may change the reviewed source of facts.
    try:
        configured_document = facts.load(config["facts_path"])
        if configured_document["integrity"] != document["integrity"]:
            raise protocol.MCPError("MCP facts revision differs from local obligations")
        messages = [request(1, "server/discover"), request(2, "tools/list"),
                    request(3, "tools/call", name=protocol.TOOL, arguments=arguments)]
        responses = (exchange or (lambda values: stdio_exchange(path, values)))(messages)
        validate_responses(messages, responses, baseline)
    except (ValueError, OSError, subprocess.SubprocessError):
        result["reason"] = "interface_unavailable_or_invalid"
        return result
    result.update(path="mcp", reason="verified_equivalent_facts")
    return result

