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


def inspect(catalog, transport, request):
    hosts = {}
    def one(host):
        try:
            value = remote(transport, host, {"operation": "inspect", "facets": request["facets"]})
            if (value.get("host") != host or not -30 <= protocol.age(value.get("observed_at")) <= 90):
                raise ValueError("host identity/freshness mismatch")
            return host, value
        except Exception:
            return host, {"available": False, "reason": "unreachable or observation unavailable"}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for host, value in pool.map(one, request["hosts"]):
            hosts[host] = value
    return {"version": protocol.VERSION, "observed_at": protocol.now(), "hosts": hosts}


def execute(catalog, transport, request, state_root=ROOT):
    protocol.request(request, catalog)
    if request["operation"] == "inspect":
        return inspect(catalog, transport, request)
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
    try:
        return remote(transport, request["host"], {k: v for k, v in request.items()
                      if k not in {"host", "inventory_hash"}})
    except Exception:
        return {"outcome": "ambiguous", "reason": "connection lost; inspect before another action"}


def run_once(root=ROOT, config=CONFIG):
    catalog = protocol.inventory(load(config / "inventory.json"))
    transport = load(config / "transport.json")
    # Transport copies can expire after durable database consumption. Authorization
    # consumption markers remain indefinitely so an effect can never be replayed.
    for old in list((root / "spool" / "results").glob("w_*.json"))[:200]:
        if not (root / "spool" / "requests" / old.name).exists() and time.time() - old.stat().st_mtime > 86400:
            old.unlink()
    for path in sorted((root / "spool" / "requests").glob("w_*.json"))[:20]:
        if not re.fullmatch(r"w_[0-9]+_[0-9]+_[a-f0-9]{32}\.json", path.name):
            continue
        destination = root / "spool" / "results" / path.name
        if destination.exists():
            continue
        try:
            envelope = load(path)
            if (set(envelope) != {"version", "job_id", "nonce", "task_id", "step_id", "tool", "input", "input_hash", "policy_version", "implementation_version"}
                    or envelope["version"] != 1 or envelope["job_id"] + ".json" != path.name
                    or envelope["policy_version"] != protocol.VERSION
                    or envelope["implementation_version"] != protocol.VERSION
                    or envelope["tool"] != "fleet." + envelope["input"].get("operation", "")
                    or protocol.digest(envelope["input"]) != envelope["input_hash"]
                    or not protocol.TOKEN.fullmatch(envelope["nonce"])):
                raise ValueError("invalid executor request")
            result = execute(catalog, transport, envelope["input"], root)
            reply = {k: v for k, v in envelope.items() if k != "input"}
            reply.update(status="ok", result=result, result_hash=protocol.digest(result))
            body = protocol.canonical(reply).encode()
            if len(body) > protocol.MAX_BYTES:
                raise ValueError("result too large")
            worker_client._atomic_publish(destination.parent, destination.name, body)
        except Exception:
            # Do not execute a malformed request; it times out with an honest gap.
            continue


def main():
    while True:
        run_once()
        time.sleep(1)


if __name__ == "__main__":
    main()
