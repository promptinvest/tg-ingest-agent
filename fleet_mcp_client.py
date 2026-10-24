"""Optional offline CF-07 facts client; existing internal reads stay independent.

The optional Cara facts path imports this adapter; default remains disabled. All transport
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


def validate_responses(messages, responses, expected, tools=None):
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
    if called.get("isError") is True and called.get("content") == [{"type": "text", "text": "Caller scope denied."}]:
        raise protocol.ScopeDenied("MCP caller scope denied; fallback is not permitted")
    if (set(discovered) != {"resultType", "supportedVersions", "capabilities", "_meta"}
            or discovered["supportedVersions"] != [protocol.PROTOCOL] or discovered["capabilities"] != {"tools": {}}):
        raise protocol.MCPError("MCP discovery differs from reviewed capability manifest")
    if set(listed) != {"resultType", "tools", "_meta"} or listed["tools"] != (tools if tools is not None else [protocol.tool_definition()]):
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
        validate_responses(messages, responses, baseline, tools=protocol.tool_definitions(config))
    except protocol.ScopeDenied:
        raise
    except (ValueError, OSError, subprocess.SubprocessError):
        result["reason"] = "interface_unavailable_or_invalid"
        return result
    result.update(path="mcp", reason="verified_equivalent_facts")
    return result



def reference(cfg, tool, arguments, reader, exchange=None):
    """Optional interface over the same retained read API, never a second job.

    Call this bounded synchronous utility only inside existing asynchronous work,
    never on the owner update/reminder loop. No production integration is enabled
    here. There is no start/retry/submit fallback and no stored client transcript.
    The caller supplies the already trusted shared reader, not an auth callback.
    """
    if not isinstance(reader, protocol.RetainedReferences) or tool not in protocol.REFERENCE_TOOLS:
        raise protocol.MCPError("shared retained reader and known read tool required")
    reader._arguments(tool, arguments)
    if getattr(cfg, "fleet_mcp_enabled", False) is not True:
        return {"path": "internal", "reason": "disabled", "result": reader.read(tool, arguments)}
    path = getattr(cfg, "fleet_mcp_config", "/etc/cara-fleet/mcp-facts.json")
    try:
        config = protocol.read_config(path)
    except (ValueError, OSError):
        return {"path": "internal", "reason": "configuration_unavailable", "result": reader.read(tool, arguments)}
    if config["principal"] != reader.principal or "references" not in config or protocol.reference_policy(config["references"]) != reader.policy:
        raise protocol.ScopeDenied("MCP retained caller scope differs from internal authority")
    baseline = reader.read(tool, arguments)
    messages = [request(1, "server/discover"), request(2, "tools/list"),
                request(3, "tools/call", name=tool, arguments=copy.deepcopy(arguments))]
    try:
        responses = (exchange or (lambda values: stdio_exchange(path, values)))(messages)
        validate_responses(messages, responses, baseline, tools=protocol.tool_definitions(config))
        # Reconcile against the current same logical request before exposing a
        # protocol response. Purged/edited/expired data cannot return from cache.
        current = reader.read(tool, arguments)
        if current != baseline:
            return {"path": "internal", "reason": "reference_changed", "result": current}
    except protocol.ScopeDenied:
        raise
    except (ValueError, OSError, subprocess.SubprocessError):
        return {"path": "internal", "reason": "interface_unavailable_or_invalid", "result": reader.read(tool, arguments)}
    return {"path": "mcp", "reason": "verified_equivalent_retained_read", "result": copy.deepcopy(baseline)}

# The optional facts transport has exactly one active background admission slot.
# This adds no queue, daemon service, transcript, model call or durable record.
_ASYNC_BACKGROUND = threading.BoundedSemaphore(1)


def facts_revision_hash(document):
    """Same revision binding as fleet_async._fleet_facts_revision()."""
    import hashlib
    document = facts.validate(document, allow_fixture=True)
    return hashlib.sha256(facts.canonical({"revision": document["revision"],
        "integrity": document["integrity"]}).encode("utf-8")).hexdigest()


def _facts_binding(value):
    import re
    if not isinstance(value, dict) or set(value) != {"request_id", "source_hash", "facts_hash", "policy_version"}:
        raise protocol.MCPError("invalid asynchronous facts binding")
    if not isinstance(value["request_id"], str) or not re.fullmatch(r"[a-f0-9]{32}", value["request_id"]):
        raise protocol.MCPError("invalid asynchronous request identity")
    for field in ("source_hash", "facts_hash"):
        if not isinstance(value[field], str) or not re.fullmatch(r"[a-f0-9]{64}", value[field]):
            raise protocol.MCPError("invalid asynchronous facts authority")
    if not isinstance(value["policy_version"], str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}/v[1-9][0-9]{0,2}", value["policy_version"]):
        raise protocol.MCPError("invalid asynchronous policy version")
    return dict(value)


def internal_fallback(cfg, document, plan, lang="en"):
    """Equivalent busy-path facts, preserving the same trusted scope checks.

    This performs no stdio exchange and no host probe. In particular a known
    ScopeDenied remains a denial, never a reason to broaden internal scope.
    Only the already-bound local facts obligations can be returned.
    """
    def unavailable(_messages):
        raise protocol.MCPError("background facts slot busy")
    return lookup(cfg, document, plan, lang, exchange=unavailable)


class _FactsConfig:
    """Immutable minimal configuration; never copies the application cfg/secrets."""
    __slots__ = ("_enabled", "_path")

    def __init__(self, cfg):
        path = getattr(cfg, "fleet_mcp_config", "/etc/cara-fleet/mcp-facts.json")
        if not isinstance(path, (str, Path)) or not Path(path).is_absolute() or len(str(path)) > 1024 or any(character in str(path) for character in "\x00\r\n"):
            raise protocol.MCPError("invalid fixed asynchronous MCP configuration")
        object.__setattr__(self, "_enabled", getattr(cfg, "fleet_mcp_enabled", False) is True)
        object.__setattr__(self, "_path", str(path))

    @property
    def fleet_mcp_enabled(self):
        return self._enabled

    @property
    def fleet_mcp_config(self):
        return self._path

    def __setattr__(self, _name, _value):
        raise AttributeError("immutable facts configuration")


class FactsLookupSlot:
    """Owner-thread handle for one bounded, data-only background facts lookup.

    The caller owns durable phase/source/purge authority and supplies its CURRENT
    binding/document at poll. No background callback touches a database or sends
    an answer. Never drop a cancelled handle before settle reports pending_calls=0.
    A busy start contains no facts: internal_fallback applies identical policy
    checks without launching a transport. ScopeDenied from a completed matching
    request propagates explicitly and must never be treated as an outage.
    """
    def __init__(self):
        self._owner = threading.get_ident()
        self._future = None
        self._handle = None
        self._input = None
        self._suppressed_reason = None

    def _owner_thread(self):
        if threading.get_ident() != self._owner:
            raise protocol.MCPError("facts slot requires its owner thread")

    def _clear(self):
        self._future = None
        self._handle = None
        self._input = None
        self._suppressed_reason = None

    def start(self, cfg, document, plan, binding, lang="en"):
        """Capture sanitized immutable data and return without waiting on I/O."""
        from concurrent.futures import Future
        import secrets
        self._owner_thread()
        if self._future is not None:
            return {"status": "busy", "reason": "equivalent_internal_facts_required"}
        bound = _facts_binding(binding)
        if lang not in {"ru", "en"}:
            raise protocol.MCPError("invalid asynchronous facts language")
        config = _FactsConfig(cfg)
        # Validated facts are descriptive data only. JSON strings isolate the
        # thread from later mutation; the entire application cfg is never held.
        facts.validate(document, allow_fixture=True)
        document_json = facts.canonical(document)
        frozen_document = facts.validate(json.loads(document_json), allow_fixture=True)
        protocol.arguments(plan, lang)
        normalized = queries.validate_plan(dict(plan, scope="explicit"), frozen_document)
        if bound["facts_hash"] != facts_revision_hash(frozen_document):
            raise protocol.MCPError("facts binding differs from captured revision")
        snapshot = (config, document_json, facts.canonical(normalized), facts.canonical(bound), lang)
        if not _ASYNC_BACKGROUND.acquire(blocking=False):
            return {"status": "busy", "reason": "equivalent_internal_facts_required"}
        future = Future()
        handle = secrets.token_hex(16)
        self._future, self._handle, self._input = future, handle, snapshot
        self._suppressed_reason = None

        def perform():
            try:
                if not future.set_running_or_notify_cancel():
                    return
                value = lookup(snapshot[0], json.loads(snapshot[1]), json.loads(snapshot[2]), snapshot[4])
                raw = facts.canonical(value)
                if len(raw.encode("utf-8")) > protocol.MAX_RESPONSE_BYTES:
                    raise protocol.MCPError("background facts result exceeds bound")
                future.set_result(raw)
            except protocol.ScopeDenied:
                if not future.done():
                    future.set_exception(protocol.ScopeDenied("MCP caller scope denied; fallback is not permitted"))
            except BaseException:
                # Private exception text/config paths never escape the slot.
                if not future.done():
                    future.set_exception(protocol.MCPError("background facts lookup unavailable"))
            finally:
                _ASYNC_BACKGROUND.release()

        try:
            threading.Thread(target=perform, name="cara-mcp-facts", daemon=True).start()
        except BaseException:
            _ASYNC_BACKGROUND.release()
            self._clear()
            raise protocol.MCPError("background facts admission unavailable") from None
        return {"status": "started", "handle": handle, "binding": bound}

    def _suppress(self, reason):
        self._suppressed_reason = self._suppressed_reason or reason
        self._future.cancel()
        settled = self._future.done()
        if settled:
            try:
                self._future.result()
            except BaseException:
                pass
            self._clear()
        return {"status": "suppressed", "reason": reason, "settled": settled}

    def poll(self, handle, binding, current_document, *, source_current=True, purge_hold=False):
        """Never calls Future.result until done, and never waits/joins a thread."""
        self._owner_thread()
        if self._future is None or handle != self._handle:
            return {"status": "not_current"}
        if type(source_current) is not bool or type(purge_hold) is not bool:
            raise protocol.MCPError("explicit source and purge state required")
        if purge_hold:
            return self._suppress("purge_hold")
        if not source_current:
            return self._suppress("source_no_longer_current")
        if self._suppressed_reason:
            return self._suppress(self._suppressed_reason)
        try:
            current_binding = _facts_binding(binding)
            if facts.canonical(current_binding) != self._input[3] or facts_revision_hash(current_document) != current_binding["facts_hash"]:
                return self._suppress("binding_or_revision_changed")
        except (ValueError, TypeError, KeyError, AttributeError):
            return self._suppress("binding_or_revision_unavailable")
        if not self._future.done():
            return {"status": "pending", "settled": False}
        try:
            result = json.loads(self._future.result())
            # The same current typed obligations are authoritative, including
            # field-specific gaps and formatting. Transport metadata adds no fact.
            normalized = json.loads(self._input[2])
            if (not isinstance(result, dict) or set(result) != {"path", "reason", "facts_integrity", "cells", "pages"}
                    or result["facts_integrity"] != current_document["integrity"]
                    or result["cells"] != queries.cells(current_document, normalized)
                    or result["pages"] != queries.render(current_document, normalized, self._input[4])
                    or (result["path"], result["reason"]) not in {
                        ("internal", "disabled"), ("internal", "configuration_unavailable"),
                        ("internal", "interface_unavailable_or_invalid"), ("mcp", "verified_equivalent_facts")}):
                raise protocol.MCPError("background facts obligations changed")
            return {"status": "ready", "result": copy.deepcopy(result), "settled": True}
        except protocol.ScopeDenied:
            raise
        except (ValueError, OSError, TypeError, KeyError, AttributeError):
            return {"status": "unavailable", "reason": "lookup_unavailable", "settled": True}
        finally:
            self._clear()

    def cancel(self, handle):
        """Suppress publication immediately; retain live work until settled."""
        self._owner_thread()
        if self._future is None or handle != self._handle:
            return {"status": "not_current"}
        return self._suppress("cancelled")

    def settle(self, *, purge_hold=True):
        """Quiesce nonblocking; root lifecycle integration must await settlement."""
        self._owner_thread()
        if type(purge_hold) is not bool:
            raise protocol.MCPError("explicit purge state required")
        if self._future is None:
            return {"status": "settled", "pending_calls": 0}
        result = self._suppress("purge_hold" if purge_hold else "cancelled")
        return {"status": "settled" if result["settled"] else "waiting", "pending_calls": 0 if result["settled"] else 1}