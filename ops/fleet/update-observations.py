#!/usr/bin/env python3
"""Source-bound, one-use observation gateway update; no service/key changes."""
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def units(config):
    out = {}
    for unit in config["services"]:
        text = subprocess.check_output(["systemctl", "show", unit, "--property=LoadState,ActiveState,Type,MainPID,InvocationID,NRestarts"], text=True)
        value = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
        if value.get("Type") != "oneshot" and value.get("ActiveState") == "active":
            out[unit] = value
    return out


def update(payload):
    if os.geteuid() != 0:
        raise ValueError("root required")
    gateway = Path("/usr/local/lib/cara-fleet/fleet_host.py")
    policy = Path("/etc/cara-fleet-host.json")
    if gateway.is_symlink() or policy.is_symlink() or sha(gateway) != payload["before_gateway"] or sha(policy) != payload["before_policy"]:
        raise ValueError("gateway/policy changed; inspect before proceeding")
    body = payload["source"].encode()
    if hashlib.sha256(body).hexdigest() != payload["source_sha256"]:
        raise ValueError("gateway source mismatch")
    ast.parse(body)
    config = json.loads(policy.read_text())
    root = Path("/root/cara-fleet-report-20261023") / payload["source_sha256"][:12]
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    root.chmod(0o700)
    shutil.copyfile(gateway, root / "gateway-before.py")
    shutil.copyfile(policy, root / "policy-before.json")
    (root / "policy-before.json").chmod(0o600)
    protected = [Path("/etc/sudoers.d/cara-fleet"), Path("/var/lib/cara-fleet-access/.ssh/authorized_keys"), gateway.parent / "gateway.sh", gateway.parent / "fleet_protocol.py", gateway.parent / "fleet_relay.py"]
    before = {str(p): sha(p) for p in protected}
    original_units = units(config)
    if config["host"] == "pi":
        mapping = {"pi-prod-health.timer": "pi-prod-health-watch.timer", "pi-prod-backup.timer": "pi-prod-encrypted-backup.timer"}
        if not set(mapping) <= set(config["services"]):
            raise ValueError("unexpected PI policy")
        config["services"] = [mapping.get(u, u) for u in config["services"]]
        for name in mapping.values():
            loaded = subprocess.check_output(["systemctl", "show", name, "-p", "LoadState", "--value"], text=True).strip()
            if loaded != "loaded": raise ValueError("replacement PI unit absent")
        candidate = root / "policy-after.json"
        candidate.write_text(json.dumps(config, sort_keys=True)); candidate.chmod(0o600)
        shutil.copyfile(candidate, policy)
    candidate = gateway.with_name("fleet_host.py.pending")
    with candidate.open("xb") as handle:
        handle.write(body); handle.flush(); os.fsync(handle.fileno())
    candidate.chmod(0o644); os.replace(candidate, gateway)
    probe = subprocess.run(["runuser", "-u", "cara-fleet", "--", "sudo", "-n", "/usr/bin/python3", str(gateway)], input=b'{"operation":"inspect","facets":["services"]}', capture_output=True, timeout=70, check=True)
    result = json.loads(probe.stdout)
    assert result["host"] == config["host"]
    assert all(v.get("LoadState") == "loaded" for v in result["services"].values()), "configured unit not loaded"
    assert {str(p): sha(p) for p in protected} == before
    assert all(units(config).get(k) == v for k, v in original_units.items())
    receipt = {"host": config["host"], "source_sha256": sha(gateway), "policy_sha256": sha(policy), "original_keys_gateway_protocol_preserved": True, "original_active_units_preserved": len(original_units), "all_probe_units_loaded": True, "service_restarts": 0, "evidence": str(root)}
    (root / "accepted.json").write_text(json.dumps(receipt, sort_keys=True))
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    update(json.load(sys.stdin))
