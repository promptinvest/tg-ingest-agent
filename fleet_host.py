#!/usr/bin/env python3
"""Forced-command host gateway. No shell, arbitrary paths, or arbitrary units."""
import contextlib
import fcntl
import json
import secrets
import stat
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
import fleet_project_adapters as project_adapters

HEALTH_HOSTS = frozenset({"127.0.0.1", "tg.promptinvest.club"})

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


def unit_state(unit, timeout=5):
    text = command(["/usr/bin/systemctl", "show", unit, "--no-pager",
                    "--property=" + ",".join(PROPERTIES)], timeout=timeout)
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
        if parsed.hostname not in HEALTH_HOSTS or parsed.scheme not in {"http", "https"}:
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


def _read_budget(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired("project read", 0)
    return min(5, remaining)


def _adapter_policy(config, project, capability):
    """Only operator configuration selects an implementation or fixed resource."""
    policies = config.get("project_adapters", {})
    if not isinstance(policies, dict):
        raise ValueError("invalid fixed project adapter policy")
    selected = policies.get(project, {})
    if not isinstance(selected, dict):
        raise ValueError("invalid fixed project adapter mapping")
    if capability not in selected:
        return None
    policy = selected[capability]
    kind = protocol.adapter_kind(capability, policy.get("adapter")) if isinstance(policy, dict) else None
    if kind is None or not isinstance(policy, dict) or policy.get("adapter") != project_adapters.ADAPTERS[kind]["implementation_version"]:
        raise ValueError("unsupported fixed project adapter")
    if kind == "daily_lingo_http":
        if set(policy) != {"adapter"}:
            raise ValueError("application adapter cannot supply resources")
    else:
        project_adapters.binding(policy, kind)
    return kind, policy


def _project_version(config, project, capability):
    selected = _adapter_policy(config, project, capability)
    return (project_adapters.ADAPTERS[selected[0]]["implementation_version"] if selected else
            protocol.PROJECT_CAPABILITIES[capability]["implementation_version"])


def _project_gap(config, project, capability, reason):
    try:
        selected = _adapter_policy(config, project, capability)
    except (ValueError, TypeError):
        selected = None
    if selected:
        return {"status": "unavailable", "observed_at": protocol.now(),
                "implementation_version": project_adapters.ADAPTERS[selected[0]]["implementation_version"],
                "reason": "observation_unavailable"}
    return protocol.capability_observation(capability, "unavailable", reason)


def _configured(config, project, capability):
    selected = _adapter_policy(config, project, capability)
    if selected and selected[0] in {"backups", "development", "development_actions", "daily_lingo_observer", "authenticated_proxy_route"}:
        return True
    section = {"release": "projects", "http_health": "health", "containers": "containers", "runtime": "project_services"}.get(capability)
    return bool(section and isinstance(config.get(section), dict) and config[section].get(project))


def discover(config, scope):
    protocol.read_scope(scope)
    return {"host": config["host"], "observed_at": protocol.now(), "read_version": protocol.READ_VERSION,
            "read_scope": scope, "capabilities": {project: {capability: {
                "configured": _configured(config, project, capability),
                "implementation_version": _project_version(config, project, capability)}
                for capability in scope["capabilities"]} for project in scope["projects"]}}


def _project_read(config, project, capability, deadline):
    observation = lambda status, reason=None, **fields: protocol.capability_observation(capability, status, reason, **fields)
    if not _configured(config, project, capability):
        selected = _adapter_policy(config, project, capability)
        if selected:
            return {"status": "not_configured", "reason": "not_configured", "observed_at": protocol.now(),
                    "implementation_version": project_adapters.ADAPTERS[selected[0]]["implementation_version"]}
        return observation("not_configured", "not_configured")
    _read_budget(deadline)
    selected_adapter = _adapter_policy(config, project, capability)
    if selected_adapter and selected_adapter[0] in {"backups", "development", "development_actions", "daily_lingo_observer", "authenticated_proxy_route"}:
        return project_adapters.read_bound(selected_adapter[0], selected_adapter[1], project, protocol.now())
    if capability == "release":
        paths = config["projects"][project]
        if not isinstance(paths, list) or not 1 <= len(paths) <= 4 or any(not isinstance(path, str) for path in paths):
            raise ValueError("invalid fixed project manifest mapping")
        selected = {}
        for path in paths:
            _read_budget(deadline)
            try:
                manifest = json.loads(read(path))
            except FileNotFoundError:
                return observation("not_found", "resource_not_found")
            if not isinstance(manifest, dict):
                raise ValueError("invalid fixed project manifest")
            for key, value in manifest.items():
                field = "release_status" if key == "status" else key
                if field in protocol.PROJECT_CAPABILITIES[capability]["outputs"] and isinstance(value, (str, bool, int)) and len(str(value)) <= 150:
                    selected[field] = value[:12] if field in {"source_revision", "source", "image"} and isinstance(value, str) else value
        return observation("observed", **selected) if selected else observation("unavailable", "observation_unavailable")
    if capability == "http_health":
        url = config["health"][project]
        parsed = urlsplit(url)
        if parsed.hostname not in HEALTH_HOSTS or parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            raise ValueError("health endpoint outside fixed policy")
        if selected_adapter and parsed.hostname != "127.0.0.1":
            raise ValueError("application adapter requires its fixed loopback source")
        def application_projection(response, status):
            raw = response.read(8193)
            if len(raw) > 8192:
                raise ValueError("project health response too large")
            def pairs(items):
                value = {}
                for key, item in items:
                    if key in value:
                        raise ValueError("duplicate application health field")
                    value[key] = item
                return value
            body = json.loads(raw, object_pairs_hook=pairs,
                parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("invalid application number")))
            return {"status": "observed", "observed_at": protocol.now(),
                    "implementation_version": project_adapters.ADAPTERS["daily_lingo_http"]["implementation_version"],
                    **project_adapters.daily_lingo_health(body, status)}
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            with opener.open(url, timeout=_read_budget(deadline)) as response:
                if selected_adapter:
                    return application_projection(response, response.status)
                fields = {"http_status": response.status}
                if parsed.hostname == "127.0.0.1":
                    raw = response.read(8193)
                    if len(raw) > 8192:
                        raise ValueError("project health response too large")
                    body = json.loads(raw)
                    if not isinstance(body, dict):
                        raise ValueError("invalid project health response")
                    fields["health"] = {key: value for key, value in body.items() if key in {"status", "database", "delivery", "intake"} and isinstance(value, (str, bool, int)) and len(str(value)) < 100}
                return observation("observed", **fields)
        except urllib.error.HTTPError as exc:
            if selected_adapter:
                try:
                    return application_projection(exc, exc.code)
                finally:
                    exc.close()
            return observation("observed", http_status=exc.code)
    if capability == "containers":
        names = config["containers"][project]
        if not isinstance(names, list) or not 1 <= len(names) <= 4 or any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", name) for name in names):
            raise ValueError("invalid fixed project container mapping")
        values = {}
        docker = "/usr/bin/docker" if Path("/usr/bin/docker").exists() else "/snap/bin/docker"
        for name in names:
            try:
                state = json.loads(command([docker, "inspect", "--format={{json .State}}", name], timeout=_read_budget(deadline)))
                if not isinstance(state, dict):
                    raise ValueError("invalid container observation")
                selected = {key: state[key] for key in ("Status", "Running", "Restarting", "OOMKilled", "StartedAt") if key in state}
                if isinstance(state.get("Health"), dict):
                    selected["health"] = state["Health"].get("Status", "unknown")
                selected["image_id"] = command([docker, "inspect", "--format={{.Image}}", name], timeout=_read_budget(deadline)).strip().removeprefix("sha256:")[:12]
                values[name] = selected
            except (OSError, ValueError, subprocess.TimeoutExpired):
                values[name] = {"available": False}
        return observation("observed", containers=values)
    if capability == "runtime":
        mapping = config["project_services"][project]
        if isinstance(mapping, list):
            mapping = {unit: "unknown" for unit in mapping}
        if not isinstance(mapping, dict) or not 1 <= len(mapping) <= 8:
            raise ValueError("invalid fixed project unit mapping")
        values = {}
        for unit, expected in mapping.items():
            if not isinstance(unit, str) or not re.fullmatch(r"[A-Za-z0-9_.@-]{1,80}\.(?:service|timer)", unit) or expected not in {"active", "idle", "disabled", "standby", "unknown"}:
                raise ValueError("invalid fixed project unit policy")
            try:
                # Fixed unit_state has a five-second command bound. Check the
                # shared budget before every unit; no unselected unit is read.
                _read_budget(deadline)
                state = unit_state(unit, timeout=_read_budget(deadline))
                state["expected_state"] = expected
                values[unit] = state
            except (OSError, ValueError, subprocess.TimeoutExpired):
                values[unit] = {"available": False, "expected_state": expected}
        return observation("observed", units=values)
    return observation("not_configured", "not_configured")


def inspect_scoped(config, facets, scope):
    protocol.read_scope(scope)
    if scope["kind"] == "projects" and facets != ["projects"]:
        raise ValueError("project scope cannot inspect host-wide metadata")
    if scope["kind"] == "host" and "projects" in facets:
        raise ValueError("host scope cannot inspect project metadata")
    if scope["kind"] != "host" and "projects" not in facets:
        raise ValueError("project facet missing")
    # Infrastructure observations are only collected on explicit host/all scope.
    out = inspect(config, [facet for facet in facets if facet != "projects"])
    out["read_scope"] = scope
    if "projects" in facets:
        deadline = time.monotonic() + 30
        out["projects"] = {}
        for project in scope["projects"]:
            out["projects"][project] = {}
            for capability in scope["capabilities"]:
                try:
                    value = _project_read(config, project, capability, deadline)
                except subprocess.TimeoutExpired:
                    value = _project_gap(config, project, capability, "time_limit")
                except (OSError, ValueError, TypeError, AttributeError, RecursionError):
                    value = _project_gap(config, project, capability, "observation_unavailable")
                out["projects"][project][capability] = value
    return protocol.scoped_host_result(out, config["host"], scope, facets)

RESOURCE_VERSION = "cara-fleet-resource/v1"


def _resource_directory():
    """Open the fixed ledger through protected no-follow directory descriptors."""
    path = Path(LEDGER)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("invalid resource ledger")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for index, part in enumerate(path.parts[1:]):
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            except FileNotFoundError:
                if index != len(path.parts) - 2:
                    raise
                os.mkdir(part, 0o700, dir_fd=descriptor)
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            info = os.fstat(descriptor)
            if (info.st_uid not in {0, os.geteuid()} or info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX):
                raise ValueError("untrusted resource ledger")
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise ValueError("untrusted resource ledger")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _resource_file(directory, name, flags, mode=0o600):
    descriptor = os.open(name, flags | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, mode, dir_fd=directory)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.geteuid() or info.st_mode & 0o022):
            raise ValueError("untrusted resource evidence")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _resource_json(directory, name):
    try:
        descriptor = _resource_file(directory, name, os.O_RDONLY)
    except FileNotFoundError:
        return None
    try:
        raw = os.read(descriptor, 16385)
        if len(raw) > 16384:
            raise ValueError("resource evidence exceeds bound")
        def pairs(items):
            value = {}
            for key, item in items:
                if key in value:
                    raise ValueError("duplicate resource evidence")
                value[key] = item
            return value
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
            parse_constant=lambda _item: (_ for _ in ()).throw(ValueError("nonfinite resource evidence")))
        if not isinstance(value, dict):
            raise ValueError("invalid resource evidence")
        return value
    finally:
        os.close(descriptor)


def _resource_write(directory, name, value):
    """Publish non-content resource state durably without replacing its lock inode."""
    temporary = "." + name + ".tmp." + secrets.token_hex(8)
    descriptor = _resource_file(directory, temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(protocol.canonical(value).encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass


def _resource_consumed(directory, token):
    try:
        descriptor = _resource_file(directory, token + ".json", os.O_RDONLY)
    except FileNotFoundError:
        return False
    os.close(descriptor)
    return True


def _resource_completed(receipt, unit):
    """Only the gateway's closed durable final-result shapes settle a hold."""
    if not isinstance(receipt, dict) or receipt.get("service") != unit:
        return False
    if set(receipt) == {"outcome", "service", "reason"}:
        return receipt["outcome"] == "failed" and receipt["reason"] == "authorization_expired"
    if set(receipt) != {"outcome", "service", "state"} or receipt["outcome"] not in {"succeeded", "failed"}:
        return False
    state = receipt["state"]
    if (not isinstance(state, dict) or not {"ActiveState", "InvocationID"} <= set(state)
            or not set(state) <= set(PROPERTIES) or not isinstance(state["ActiveState"], str)
            or not state["ActiveState"]):
        return False
    for value in state.values():
        if (type(value) is not int or value < 0) and (not isinstance(value, str)
                or re.fullmatch(r"[A-Za-z0-9_.@:/ -]{0,100}", value) is None):
            return False
    return receipt["outcome"] == ("succeeded" if state["ActiveState"] == "active" else "failed")


def _resource_unsettled(ledger, resources, name, unit):
    try:
        value = _resource_json(resources, name)
        if value is None:
            return False
        if (set(value) != {"version", "service", "token", "state", "at"}
                or value["version"] != RESOURCE_VERSION or value["service"] != unit
                or not isinstance(value["token"], str) or not protocol.TOKEN.fullmatch(value["token"])
                or value["state"] not in {"executing", "unknown", "settled"}):
            return True
        protocol.age(value["at"])
        if value["state"] == "settled":
            return False
        receipt = _resource_json(ledger, value["token"] + ".json")
        if _resource_completed(receipt, unit):
            # A completed durable outcome can reconcile a crash after its write.
            # A new fingerprint or elapsed lease alone cannot establish settlement.
            _resource_write(resources, name, dict(value, state="settled", at=protocol.now()))
            return False
        return True
    except (OSError, ValueError, UnicodeError, TypeError):
        return True


@contextlib.contextmanager
def _restart_resource_lock(unit):
    ledger = _resource_directory()
    resources = lock = None
    try:
        try:
            os.mkdir("resources", 0o700, dir_fd=ledger)
        except FileExistsError:
            pass
        resources = os.open("resources", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=ledger)
        info = os.fstat(resources)
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise ValueError("untrusted resource lock directory")
        identity = protocol.digest({"operation": "restart", "service": unit})
        lock = _resource_file(resources, identity + ".lock", os.O_RDWR | os.O_CREAT)
        acquired = True
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            acquired = False
        yield ledger, resources, identity + ".json", acquired
    finally:
        if lock is not None:
            os.close(lock)
        if resources is not None:
            os.close(resources)
        os.close(ledger)


def _restart_under_lock(unit, request, ledger, resources, resource_name):
    token = request["token"]
    if _resource_consumed(ledger, token):
        return {"outcome": "ambiguous", "reason": "authorization already consumed"}
    if _resource_unsettled(ledger, resources, resource_name, unit):
        return {"outcome": "failed", "service": unit, "reason": "resource_unknown"}
    if fingerprint(unit) != request["fingerprint"]:
        raise ValueError("service changed; fresh approval required")
    if not -610 <= protocol.age(request["expires"]) < 0:
        raise ValueError("authorization expired")
    hold = {"version": RESOURCE_VERSION, "service": unit, "token": token, "state": "executing", "at": protocol.now()}
    _resource_write(resources, resource_name, hold)
    try:
        descriptor = _resource_file(ledger, token + ".json", os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        _resource_write(resources, resource_name, dict(hold, state="settled", at=protocol.now()))
        return {"outcome": "ambiguous", "reason": "authorization already consumed"}
    with os.fdopen(descriptor, "w") as handle:
        handle.write(protocol.canonical({"state": "executing", "at": protocol.now(), "service": unit}))
        handle.flush()
        os.fsync(handle.fileno())
    os.fsync(ledger)
    try:
        if not -610 <= protocol.age(request["expires"]) < 0:
            result = {"outcome": "failed", "service": unit, "reason": "authorization_expired"}
        else:
            command(["/usr/bin/systemctl", "restart", unit], timeout=20)
            state = unit_state(unit)
            result = {"outcome": "succeeded" if state.get("ActiveState") == "active" else "failed",
                      "service": unit, "state": state}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        result = {"outcome": "ambiguous", "service": unit}
    descriptor = _resource_file(ledger, token + ".json", os.O_WRONLY)
    with os.fdopen(descriptor, "w") as handle:
        handle.truncate(0)
        handle.write(protocol.canonical(result))
        handle.flush()
        os.fsync(handle.fileno())
    _resource_write(resources, resource_name, dict(hold,
        state="settled" if result["outcome"] in {"succeeded", "failed"} else "unknown", at=protocol.now()))
    return result


def restart(config, request):
    if set(request) != {"operation", "service", "fingerprint", "token", "expires"}:
        raise ValueError("invalid restart fields")
    unit, token = request["service"], request["token"]
    if (request["operation"] != "restart" or not isinstance(unit, str) or unit not in config.get("restart_services", [])
            or not isinstance(token, str) or not protocol.TOKEN.fullmatch(token)
            or not isinstance(request["fingerprint"], str) or not re.fullmatch(r"[a-f0-9]{64}", request["fingerprint"])):
        raise ValueError("restart is outside host policy")
    if not -610 <= protocol.age(request["expires"]) < 0:
        raise ValueError("authorization expired")
    with _restart_resource_lock(unit) as (ledger, resources, name, acquired):
        if not acquired:
            if _resource_consumed(ledger, token):
                return {"outcome": "ambiguous", "reason": "authorization already consumed"}
            return {"outcome": "failed", "service": unit, "reason": "resource_busy"}
        return _restart_under_lock(unit, request, ledger, resources, name)

def run(config, request):
    if request.get("operation") == "capabilities":
        if set(request) != {"operation", "read_scope"}:
            raise ValueError("invalid capability discovery")
        return discover(config, request["read_scope"])
    if request.get("operation") == "inspect":
        if set(request) not in ({"operation", "facets"}, {"operation", "facets", "read_scope"}) or not isinstance(request["facets"], list) or not set(request["facets"]) <= protocol.FACETS:
            raise ValueError("invalid inspection")
        if "read_scope" in request:
            return inspect_scoped(config, request["facets"], request["read_scope"])
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
        protocol.transport_value(result)
        print(protocol.canonical(result))
    except Exception:
        # No command, environment, path, or protected error details leave the host.
        print('{"available":false,"reason":"host operation refused or unavailable"}')
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
