#!/usr/bin/env python3
"""Install a new root-owned fleet gateway; preserve every original service/file."""
import hashlib
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def run(args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, **kwargs)


def fingerprint(paths):
    output = {}
    for pattern in paths:
        import glob
        for name in glob.glob(pattern):
            path = Path(name)
            if path.is_file():
                info = path.stat()
                output[name] = [hashlib.sha256(path.read_bytes()).hexdigest(), info.st_mode & 0o7777, info.st_uid, info.st_gid]
    return output


def services(units):
    values = {}
    for unit in units:
        result = run(["systemctl", "show", unit, "--property=Type,ActiveState,MainPID,InvocationID,NRestarts"], text=True)
        state = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if state.get("Type") != "oneshot" and state.get("ActiveState") == "active":
            values[unit] = state
    return values


def install(payload):
    if os.geteuid() != 0:
        raise ValueError("root required")
    config = payload["config"]
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,47}", config["host"]):
        raise ValueError("invalid host")
    for unit in config["services"]:
        if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,100}\.(service|timer)", unit):
            raise ValueError("invalid unit")
    if not set(config["restart_services"]) <= set(config["services"]):
        raise ValueError("invalid restart policy")
    if Path("/etc/cara-fleet-host.json").exists():
        raise ValueError("handler already installed; inspect its receipt instead of replaying")
    public = payload["public_key"].strip()
    if not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/=]+(?: [A-Za-z0-9_-]+)?", public):
        raise ValueError("invalid public key")
    source = payload["source_ip"]
    if source not in {"10.10.0.2", "192.241.137.239", "157.230.186.51"}:
        raise ValueError("unexpected source restriction")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    evidence = Path("/root/cara-fleet-host-20261023") / stamp
    evidence.mkdir(parents=True, mode=0o700)
    evidence.chmod(0o700)
    before_files = fingerprint(payload["protected_files"])
    before_services = services(config["services"])
    (evidence / "before.json").write_text(json.dumps({"files": before_files, "services": before_services}, sort_keys=True))
    try:
        pwd.getpwnam("cara-fleet")
    except KeyError:
        run(["useradd", "--system", "--home-dir", "/var/lib/cara-fleet-access", "--shell", "/bin/sh", "cara-fleet"])
    else:
        raise ValueError("existing account requires explicit inspection")
    home = Path("/var/lib/cara-fleet-access")
    home.mkdir(mode=0o755)
    (home / ".ssh").mkdir(mode=0o755)
    code = Path("/usr/local/lib/cara-fleet")
    code.mkdir(parents=True, mode=0o755)
    if code.is_symlink():
        raise ValueError("unexpected code symlink")
    for name in ("fleet_protocol.py", "fleet_host.py", "fleet_relay.py"):
        body = payload["modules"][name].encode()
        if hashlib.sha256(body).hexdigest() != payload["hashes"][name]:
            raise ValueError("source checksum mismatch")
        path = code / name
        path.write_bytes(body)
        path.chmod(0o644)
    gateway = '#!/bin/sh\nset -eu\ncase "${SSH_ORIGINAL_COMMAND:-}" in\nfleet-v1) exec /usr/bin/sudo -n /usr/bin/python3 /usr/local/lib/cara-fleet/fleet_host.py ;;\n'
    if config["host"] == "exit":
        gateway += 'fleet-rus-relay) exec /usr/bin/python3 /usr/local/lib/cara-fleet/fleet_relay.py ;;\n'
    gateway += '*) exit 64 ;;\nesac\n'
    (code / "gateway.sh").write_text(gateway)
    (code / "gateway.sh").chmod(0o755)
    policy = Path("/etc/cara-fleet-host.json")
    policy.write_text(json.dumps(config, sort_keys=True))
    policy.chmod(0o600)
    sudo = "cara-fleet ALL=(root) NOPASSWD: /usr/bin/python3 /usr/local/lib/cara-fleet/fleet_host.py\n"
    candidate = evidence / "sudoers"
    candidate.write_text(sudo)
    candidate.chmod(0o440)
    run(["visudo", "-cf", str(candidate)])
    shutil.copyfile(candidate, "/etc/sudoers.d/cara-fleet")
    Path("/etc/sudoers.d/cara-fleet").chmod(0o440)
    authorized = home / ".ssh" / "authorized_keys"
    authorized.write_text(f'from="{source}",restrict,command="/usr/local/lib/cara-fleet/gateway.sh" {public}\n')
    authorized.chmod(0o644)
    run(["/usr/sbin/sshd", "-t"])
    probe = run(["runuser", "-u", "cara-fleet", "--", "sudo", "-n", "/usr/bin/python3", str(code / "fleet_host.py")],
                input=b'{"operation":"inspect","facets":["resources"]}', timeout=15)
    result = json.loads(probe.stdout)
    if result.get("host") != config["host"] or "cpu_percent" not in result.get("resources", {}):
        raise ValueError("restricted handler probe failed")
    after_files = fingerprint(payload["protected_files"])
    after_services = services(config["services"])
    if before_files != after_files or any(after_services.get(k) != v for k, v in before_services.items()):
        raise ValueError("original file/service preservation failed")
    receipt = {"host": config["host"], "accepted_at": datetime.now(timezone.utc).isoformat(),
               "source_hashes": payload["hashes"], "policy_sha256": hashlib.sha256(policy.read_bytes()).hexdigest(),
               "original_files_preserved": len(before_files), "original_services_preserved": len(before_services),
               "restricted_read_probe": True, "application_restarts": 0, "evidence": str(evidence)}
    (evidence / "accepted.json").write_text(json.dumps(receipt, sort_keys=True))
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    install(json.loads(sys.stdin.read()))
