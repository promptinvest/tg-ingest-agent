#!/usr/bin/env python3
"""Forced-command host gateway. No shell, arbitrary paths, or arbitrary units."""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from pathlib import Path

import fleet_protocol as protocol

CONFIG = Path("/etc/cara-fleet-host.json")
LEDGER = Path("/var/lib/cara-fleet-host")
PROPERTIES = ("LoadState", "ActiveState", "SubState", "Type", "NRestarts", "InvocationID",
              "ExecMainStartTimestampMonotonic", "MainPID", "MemoryCurrent",
              "CPUUsageNSec", "Result")


def command(args, timeout=5):
    completed = subprocess.run(args, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               timeout=timeout, check=False, env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
    if len(completed.stdout) > 65536 or completed.returncode:
        raise ValueError("host observation unavailable")
    return completed.stdout.decode("utf-8", "replace")


def read(path, limit=65536):
    with open(path, encoding="utf-8") as handle:
        text = handle.read(limit + 1)
    if len(text) > limit:
        raise ValueError("observation too large")
    return text


def unit_state(unit):
    text = command(["/usr/bin/systemctl", "show", unit, "--no-pager",
                    "--property=" + ",".join(PROPERTIES)])
    values = {}
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key in PROPERTIES and re.fullmatch(r"[A-Za-z0-9_.@:/ -]{0,100}", value):
            values[key] = int(value) if value.isdigit() else value
    if not {"ActiveState", "InvocationID"} <= set(values):
        raise ValueError("missing service observation")
    return values


def fingerprint(unit, state=None):
    state = state if state is not None else unit_state(unit)
    return protocol.digest({"boot": read("/proc/sys/kernel/random/boot_id").strip(),
                            "unit": unit, "invocation": state["InvocationID"],
                            "started": state.get("ExecMainStartTimestampMonotonic")})


def resources():
    def cpu():
        return [int(v) for v in read("/proc/stat").splitlines()[0].split()[1:9]]
    def processes():
        values = {}
        for item in Path("/proc").iterdir():
            if not item.name.isdigit():
                continue
            try:
                text = read(item / "stat", 8192)
                head, _, tail = text.rpartition(")")
                fields = tail.split()
                name = re.sub(r"[^A-Za-z0-9_.+-]", "-", head.partition("(")[2])[:20]
                values[item.name] = (name, int(fields[11])+int(fields[12]), int(fields[21])*os.sysconf("SC_PAGE_SIZE"))
            except (OSError, ValueError, IndexError):
                continue
        return values
    first_processes = processes()
    started = time.monotonic()
    first = cpu()
    time.sleep(0.2)
    second = cpu()
    elapsed = time.monotonic() - started
    process_rows = []
    for pid, (name, ticks, rss) in processes().items():
        delta = max(0, ticks-first_processes.get(pid, (name, ticks, rss))[1])
        process_rows.append({"name": name, "pid": int(pid), "rss_bytes": rss,
                             "cpu_one_core_percent": round(delta/os.sysconf("SC_CLK_TCK")/elapsed*100, 2)})
    total = sum(b - a for a, b in zip(first, second))
    idle = second[3] - first[3] + second[4] - first[4]
    memory = {}
    for line in read("/proc/meminfo").splitlines():
        key, _, val = line.partition(":")
        if key in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
            memory[key] = int(val.split()[0]) * 1024
    fs = os.statvfs("/")
    return {"cpu_percent": round(100 * (total - idle) / total, 2) if total > 0 else None,
            "cpu_sample_seconds": round(elapsed, 3), "cpus": os.cpu_count(),
            "load": [float(v) for v in read("/proc/loadavg").split()[:3]],
            "memory_bytes": memory,
            "memory_used_percent": round(100*(memory["MemTotal"]-memory["MemAvailable"])/memory["MemTotal"], 2),
            "disk_used_percent": round(100*(fs.f_blocks-fs.f_bavail)/fs.f_blocks, 2),
            "disk_total": fs.f_blocks * fs.f_frsize,
            "disk_available": fs.f_bavail * fs.f_frsize,
            "uptime_seconds": float(read("/proc/uptime").split()[0]),
            "top_cpu": sorted(process_rows, key=lambda p: p["cpu_one_core_percent"], reverse=True)[:5],
            "top_memory": sorted(process_rows, key=lambda p: p["rss_bytes"], reverse=True)[:5]}


def projects(config):
    values = {}
    for name, files in config.get("projects", {}).items():
        values[name] = {}
        for path in files:
            try:
                manifest = json.loads(read(path))
                selected = {k: (v[:12] if k in {"source_revision", "source", "image"} and isinstance(v, str) else v) for k, v in manifest.items() if k in {
                    "schema", "source_revision", "source", "build_version", "image",
                    "installed_at", "verified_at", "status", "policy_version"}
                    and isinstance(v, (str, bool, int)) and len(str(v)) <= 150}
                values[name].update(selected)
            except (OSError, ValueError, TypeError, AttributeError):
                values[name]["manifest_available"] = False
    for name, names in config.get("containers", {}).items():
        project = values.setdefault(name, {})
        project["containers"] = {}
        docker = "/usr/bin/docker" if Path("/usr/bin/docker").exists() else "/snap/bin/docker"
        for container in names:
            try:
                state = json.loads(command([docker, "inspect", "--format={{json .State}}", container]))
                safe = {k: state[k] for k in ("Status", "Running", "Restarting", "OOMKilled", "StartedAt") if k in state}
                if isinstance(state.get("Health"), dict):
                    safe["health"] = state["Health"].get("Status", "unknown")
                safe["image_id"] = command([docker, "inspect", "--format={{.Image}}", container]).strip().removeprefix("sha256:")[:12]
                project["containers"][container] = safe
            except (OSError, ValueError, subprocess.TimeoutExpired):
                project["containers"][container] = {"available": False}
    for name, url in config.get("health", {}).items():
        project = values.setdefault(name, {})
        parsed = urlsplit(url)
        if parsed.hostname not in {"127.0.0.1", "tg.promptinvest.club"} or parsed.scheme not in {"http", "https"}:
            raise ValueError("health endpoint outside policy")
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            with opener.open(url, timeout=5) as response:
                project["http_status"] = response.status
                if parsed.hostname == "127.0.0.1":
                    body = json.loads(response.read(8193))
                    project["health"] = {k: v for k, v in body.items() if k in {"status", "database", "delivery", "intake"}
                                         and isinstance(v, (str, bool, int)) and len(str(v)) < 100}
        except urllib.error.HTTPError as exc:
            project["http_status"] = exc.code
        except (OSError, ValueError, AttributeError):
            project["health_available"] = False
    return values


def inspect(config, facets):
    out = {"observed_at": protocol.now(), "host": config["host"]}
    if "resources" in facets:
        try:
            out["resources"] = resources()
        except (OSError, ValueError):
            out["resources"] = {"available": False}
    if "services" in facets:
        out["services"] = {}
        for unit in config["services"]:
            try:
                state = unit_state(unit)
                state["fingerprint"] = fingerprint(unit, state)
                out["services"][unit] = state
            except (OSError, ValueError, subprocess.TimeoutExpired):
                out["services"][unit] = {"available": False}
    if "projects" in facets:
        out["projects"] = projects(config)
    if "errors" in facets:
        out["errors"] = {}
        for unit in config["services"][:8]:
            try:
                # Deliberately omit MESSAGE: user content and secrets can appear
                # in application logs. Counts are lower bounds at the 200-row cap.
                text = command(["/usr/bin/journalctl", "-u", unit, "--since=-1h",
                                "--no-pager", "-n", "200", "-o", "json",
                                "--output-fields=PRIORITY"])
                rows = [json.loads(line) for line in text.splitlines() if line]
                out["errors"][unit] = {"window_seconds": 3600,
                    "sampled": len(rows), "capped": len(rows) >= 200,
                    "warning_or_worse": sum(str(r.get("PRIORITY", "6")) in {"0", "1", "2", "3", "4"} for r in rows)}
            except (OSError, ValueError, subprocess.TimeoutExpired):
                out["errors"][unit] = {"available": False}
    return out


def restart(config, request):
    if set(request) != {"operation", "service", "fingerprint", "token", "expires"}:
        raise ValueError("invalid restart fields")
    unit = request["service"]
    token = request["token"]
    if unit not in config.get("restart_services", []) or not protocol.TOKEN.fullmatch(str(token)):
        raise ValueError("restart is outside host policy")
    if not -610 <= protocol.age(request["expires"]) < 0:
        raise ValueError("authorization expired")
    if fingerprint(unit) != request["fingerprint"]:
        raise ValueError("service changed; fresh approval required")
    LEDGER.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = LEDGER / (token + ".json")
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return {"outcome": "ambiguous", "reason": "authorization already consumed"}
    with os.fdopen(fd, "w") as handle:
        handle.write(protocol.canonical({"state": "executing", "at": protocol.now(), "service": unit}))
        handle.flush()
        os.fsync(handle.fileno())
    directory = os.open(LEDGER, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    try:
        command(["/usr/bin/systemctl", "restart", unit], timeout=20)
        state = unit_state(unit)
        result = {"outcome": "succeeded" if state.get("ActiveState") == "active" else "failed",
                  "service": unit, "state": state}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        result = {"outcome": "ambiguous", "service": unit}
    # The consumed marker remains durable even if this final write is interrupted.
    with open(path, "w") as handle:
        handle.write(protocol.canonical(result))
        handle.flush()
        os.fsync(handle.fileno())
    return result


def run(config, request):
    if request.get("operation") == "inspect":
        if set(request) != {"operation", "facets"} or not isinstance(request["facets"], list) or not set(request["facets"]) <= protocol.FACETS:
            raise ValueError("invalid inspection")
        return inspect(config, request["facets"])
    if request.get("operation") == "restart":
        return restart(config, request)
    raise ValueError("unknown host operation")


def main():
    try:
        config = json.loads(read(CONFIG))
        incoming = sys.stdin.buffer.read(4097)
        if len(incoming) > 4096:
            raise ValueError("request too large")
        result = run(config, json.loads(incoming))
        protocol.safe_tree(result)
        print(protocol.canonical(result))
    except Exception:
        # No command, environment, path, or protected error details leave the host.
        print('{"available":false,"reason":"host operation refused or unavailable"}')
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
