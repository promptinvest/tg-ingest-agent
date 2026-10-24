#!/usr/bin/env python3
"""Independent fleet executor. Owns SSH identities, never Cara's database/token."""
import concurrent.futures
import json
import os
import re
import subprocess
import time
from pathlib import Path

import fleet_protocol as protocol
import fleet_queue
import fleet_lifecycle
import worker_client

ROOT = Path("/var/lib/cara-fleet")
CONFIG = Path("/etc/cara-fleet")


def load(path):
    raw = worker_client._read_regular_nofollow(path, 40000)
    if raw is None:
        raise ValueError("fleet configuration missing")
    return json.loads(raw)


def remote(transport, host, request):
    endpoint = transport[host]
    if (set(endpoint) not in ({"address", "port", "key", "known_hosts"}, {"address", "port", "key", "known_hosts", "via"})
            or not re.fullmatch(r"[0-9.]{7,15}", endpoint["address"])
            or type(endpoint["port"]) is not int or not 1 <= endpoint["port"] <= 65535):
        raise ValueError("invalid operator endpoint")
    for name in ("key", "known_hosts"):
        if not re.fullmatch(r"/etc/cara-fleet/[a-z0-9_-]+", endpoint[name]):
            raise ValueError("invalid operator credential reference")
    args = ["/usr/bin/ssh", "-T", "-i", endpoint["key"], "-p", str(endpoint["port"]),
            "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
            "-o", "StrictHostKeyChecking=yes", "-o", "ClearAllForwardings=yes",
            "-o", "UserKnownHostsFile=" + endpoint["known_hosts"],
            "cara-fleet@" + endpoint["address"], "fleet-v1"]
    if "via" in endpoint:
        if host != "rus" or endpoint["via"] != "exit":
            raise ValueError("unknown relay")
        via = transport["exit"]
        # All values are operator-owned, validated credential references and a
        # fixed peer. No model field enters the SSH proxy command.
        if (via["address"] != "138.197.230.115" or via["port"] != 49323
                or not re.fullmatch(r"/etc/cara-fleet/[a-z0-9_-]+", via["key"])
                or not re.fullmatch(r"/etc/cara-fleet/[a-z0-9_-]+", via["known_hosts"])):
            raise ValueError("relay policy mismatch")
        proxy = ("/usr/bin/ssh -T -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes "
                 "-o ConnectTimeout=8 -i " + via["key"] + " -p 49323 -o UserKnownHostsFile="
                 + via["known_hosts"] + " cara-fleet@138.197.230.115 fleet-rus-relay")
        args[1:1] = ["-o", "ProxyCommand=" + proxy]
    done = subprocess.run(args, input=protocol.canonical(request).encode(),
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          timeout=55, check=False,
                          env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
    if done.returncode or len(done.stdout) > 10000:
        raise ValueError("host unavailable or operation refused")
    value = json.loads(done.stdout)
    protocol.safe_tree(value)
    return value


def inspect(catalog, transport, request, control=None):
    def observe(host, operation):
        if control is not None:
            control.check()
        return remote(transport, host, operation)
    if "read_scope" in request:
        protocol.request(request, catalog)
        scope = request["read_scope"]
        hosts = {}
        def scoped_one(host):
            selected = protocol.scope_for_host(scope, catalog, host)
            try:
                discovery = observe(host, {"operation": "capabilities", "read_scope": selected})
                protocol.read_discovery(discovery, host, selected)
                value = observe(host, {"operation": "inspect", "facets": request["facets"], "read_scope": selected})
                protocol.scoped_host_result(value, host, selected, request["facets"])
                return host, value
            except (fleet_queue.QueueCancelled, fleet_queue.QueueExpired):
                raise
            except Exception:
                # An old/refusing gateway is a scoped capability gap. Never retry
                # as a legacy inspection, which would collect colocated projects.
                value = {"host": host, "observed_at": protocol.now(), "read_scope": selected}
                for facet in request["facets"]:
                    value[facet] = ({project: {capability: protocol.capability_observation(capability, "unavailable", "gateway_contract_unavailable")
                                              for capability in selected["capabilities"]} for project in selected["projects"]}
                                    if facet == "projects" else {"available": False})
                return host, value
        with concurrent.futures.ThreadPoolExecutor(max_workers=fleet_queue.MAX_HOST_TRANSPORTS) as pool:
            for host, value in pool.map(scoped_one, request["hosts"]):
                hosts[host] = value
        return {"version": protocol.VERSION, "observed_at": protocol.now(), "hosts": hosts, "read_scope": scope}
    hosts = {}
    def one(host):
        try:
            value = observe(host, {"operation": "inspect", "facets": request["facets"]})
            if (value.get("host") != host or not -30 <= protocol.age(value.get("observed_at")) <= 90):
                raise ValueError("host identity/freshness mismatch")
            return host, value
        except (fleet_queue.QueueCancelled, fleet_queue.QueueExpired):
            raise
        except Exception:
            return host, {"available": False, "reason": "unreachable or observation unavailable"}
    with concurrent.futures.ThreadPoolExecutor(max_workers=fleet_queue.MAX_HOST_TRANSPORTS) as pool:
        for host, value in pool.map(one, request["hosts"]):
            hosts[host] = value
    return {"version": protocol.VERSION, "observed_at": protocol.now(), "hosts": hosts}


def execute(catalog, transport, request, state_root=ROOT, control=None):
    protocol.request(request, catalog)
    if control is not None:
        control.check()
    if request["operation"] == "inspect":
        if control is None:
            return inspect(catalog, transport, request)
        return inspect(catalog, transport, request, control=control)
    consumed = state_root / "consumed"
    consumed.mkdir(mode=0o700, parents=True, exist_ok=True)
    marker = consumed / request["token"]
    try:
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return {"outcome": "ambiguous", "reason": "authorization already consumed"}
    with os.fdopen(fd, "w") as handle:
        handle.write(protocol.digest(request))
        handle.flush()
        os.fsync(handle.fileno())
    worker_client._fsync_dir(consumed)
    if control is not None:
        control.check()
        control.effect_dispatched = True
    try:
        return remote(transport, request["host"], {k: v for k, v in request.items()
                      if k not in {"host", "inventory_hash"}})
    except Exception:
        return {"outcome": "ambiguous", "reason": "connection lost; inspect before another action"}


def _queue_order(path):
    try:
        return path.lstat().st_mtime_ns, path.name
    except OSError:
        return float("inf"), path.name


def run_once(root=ROOT, config=CONFIG):
    catalog = protocol.inventory(load(config / "inventory.json"))
    transport = load(config / "transport.json")
    spool = root / "spool"
    fleet_queue.publish_ready(spool)
    if fleet_lifecycle.executor_gate(spool):
        return
    # A started sidecar belongs to the ORIGINAL request. Keep it while that
    # request exists, even when the sidecar itself is more than one day old.
    for old in list((spool / "results").glob("w_*.json"))[:200]:
        request_name = old.name.replace(".started.json", ".json")
        if not (spool / "requests" / request_name).exists() and time.time() - old.stat().st_mtime > 86400:
            old.unlink()
    paths = sorted((spool / "requests").glob("w_*.json"), key=_queue_order)[:20]
    for path in paths:
        if fleet_lifecycle.executor_gate(spool):
            return
        if not re.fullmatch(r"w_[0-9]+_[0-9]+_[a-f0-9]{32}\.json", path.name):
            continue
        destination = spool / "results" / path.name
        if destination.exists():
            continue
        try:
            envelope = load(path)
            fields = {"version", "job_id", "nonce", "task_id", "step_id", "tool", "input", "input_hash", "policy_version", "implementation_version"}
            if (set(envelope) not in (fields, fields | {"lifecycle"})
                    or envelope["version"] != 1 or envelope["job_id"] + ".json" != path.name
                    or envelope["policy_version"] != protocol.VERSION
                    or envelope["implementation_version"] != protocol.VERSION
                    or envelope["tool"] != "fleet." + envelope["input"].get("operation", "")
                    or protocol.digest(envelope["input"]) != envelope["input_hash"]
                    or not protocol.TOKEN.fullmatch(envelope["nonce"])):
                raise ValueError("invalid executor request")
            control = None
            reply = None
            if "lifecycle" in envelope:
                protocol.request(envelope["input"], catalog)
                fleet_queue.validate_lifecycle(envelope["lifecycle"])
                started = fleet_queue.read_started(spool, fleet_queue.binding(envelope), envelope["lifecycle"])
                if started is not None:
                    # A previous executor stopped after durable start. Never
                    # reconstruct and run that unknown operation a second time.
                    reply = fleet_queue.terminal(envelope, "execution_interrupted")
                else:
                    state = fleet_queue.queue_state(spool, envelope)
                    if state != "queued":
                        reply = fleet_queue.terminal(envelope, state)
                    else:
                        control = fleet_queue.Control(spool, envelope)
                        fleet_queue.publish_started(spool, envelope)
            if "lifecycle" not in envelope and fleet_queue.cancelled(spool, envelope):
                # Legacy envelopes keep their original wire contract, but a
                # nonce-bound cancellation must prevent any new dispatch too.
                reply = fleet_queue.terminal(envelope, "cancelled")
            if reply is None:
                try:
                    if control is None:
                        result = execute(catalog, transport, envelope["input"], root)
                    else:
                        result = execute(catalog, transport, envelope["input"], root, control=control)
                        # A dispatched maintenance operation reports its actual
                        # outcome; a late cancel cannot truthfully undo it.
                        if not control.effect_dispatched:
                            control.check()
                    reply = {k: v for k, v in envelope.items() if k != "input"}
                    reply.update(status="ok", result=result, result_hash=protocol.digest(result))
                except fleet_queue.QueueCancelled:
                    reply = fleet_queue.terminal(envelope, "cancelled")
                except fleet_queue.QueueExpired:
                    reply = fleet_queue.terminal(envelope, "execution_expired")
            body = protocol.canonical(reply).encode()
            if len(body) > protocol.MAX_BYTES:
                raise ValueError("result too large")
            worker_client._atomic_publish(destination.parent, destination.name, body)
        except Exception:
            # Malformed requests/receipts never run or get silently rewritten.
            # The owner records a bounded unavailable/timeout and does not replay.
            continue


def main():
    while True:
        run_once()
        time.sleep(1)


if __name__ == "__main__":
    main()
