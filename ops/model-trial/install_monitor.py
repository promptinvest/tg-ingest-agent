#!/usr/bin/env python3
"""Fresh-exclusive, fixed-name 72-hour Cara trial monitor installation.

No command installs application code, sends a message, probes a model or changes
the model configuration. The tick wrapper only observes metadata and may stop its
own fixed timer after the absolute deadline. Importing this module is inert.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile


ROOT = Path("/root/cara-model-trial-20261025/attempt3")
DATABASE = Path("/var/lib/tg-ingest-agent/ingest.db")
SOURCE = "ee1a319ce53ddc941ebf4b760c084abd865afd19"
SERVICE = "cara-model-trial-20261025-observer.service"
TIMER = "cara-model-trial-20261025-observer.timer"
UNIT_DIR = Path("/etc/systemd/system")
TRIAL_HOURS = 72
SAMPLE_HOURS = tuple(range(6, 73, 6))
HEX64 = re.compile(r"[0-9a-f]{64}")


class MonitorError(Exception):
    """Static error codes, safe to include in metadata-only receipts."""


def require(condition, code):
    if not condition:
        raise MonitorError(code)


def timestamp(value):
    try:
        value = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise MonitorError("invalid_timestamp") from None
    require(value.tzinfo is not None, "timezone_required")
    return value.astimezone(timezone.utc)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def unique_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate_json_member")
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(MonitorError("nonfinite_json")))
    except (ValueError, UnicodeError, TypeError):
        raise MonitorError("invalid_json") from None
    require(isinstance(value, dict), "json_object_required")
    return value


def fixed_root(value):
    require(str(value) == str(ROOT), "unapproved_trial_root")
    return ROOT


def protected(path, directory=False):
    info = path.lstat()
    require(not path.is_symlink(), "symlink_refused")
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode), "unexpected_file_type")
    require(info.st_uid == 0 and info.st_gid == 0 and not stat.S_IMODE(info.st_mode) & 0o022,
            "protected_ownership_required")
    if not directory:
        require(info.st_size <= 512_000, "receipt_size_limit")
    return info


def validate_binding(root, accepted, prepared):
    fixed_root(root)
    require(accepted.get("schema") == "cara.model-trial-acceptance/v1" and accepted.get("status") == "accepted",
            "accepted_trial_required")
    require(prepared.get("schema") == "cara.model-trial-prepared/v1", "prepared_trial_required")
    identity = accepted.get("identity")
    require(isinstance(identity, dict) and identity.get("source_revision") == SOURCE
            and identity == prepared.get("identity"), "runtime_identity_mismatch")
    require(isinstance(identity.get("sha256"), str) and HEX64.fullmatch(identity["sha256"]), "native_manifest_hash_required")
    for key in ("before_env_sha256", "candidate_env_sha256"):
        require(isinstance(accepted.get(key), str) and HEX64.fullmatch(accepted[key])
                and accepted[key] == prepared.get(key), "environment_binding_mismatch")
    require(accepted["before_env_sha256"] != accepted["candidate_env_sha256"], "distinct_trial_environment_required")
    require(accepted.get("database_restored") is False and accepted.get("code_installed") is False
            and accepted.get("operator_messages_sent") == 0, "trial_scope_mismatch")
    require(accepted.get("budget_limits_usd") == prepared.get("budget_limits_usd") == [2.0, 12.0], "budget_binding_mismatch")
    require(accepted.get("runtime_hashes") == prepared.get("runtime_hashes")
            and isinstance(accepted.get("runtime_hashes"), dict)
            and "llm.py" in accepted["runtime_hashes"]
            and all(isinstance(value, str) and HEX64.fullmatch(value) for value in accepted["runtime_hashes"].values()),
            "runtime_hashes_mismatch")
    require(accepted.get("stopped_tables_preserved") == len(prepared.get("tables", {})) == 56,
            "table_preservation_binding_mismatch")
    require(prepared.get("database_path") == str(DATABASE), "unapproved_database_path")
    start, end = timestamp(accepted.get("at")), timestamp(accepted.get("observation_until"))
    require(end == start + timedelta(hours=TRIAL_HOURS), "exact_72_hour_period_required")
    return start, end


def calendar_dates(start):
    """Ceil to whole seconds: the final invocation cannot precede the deadline."""
    start = timestamp(start)
    result = []
    for hours in SAMPLE_HOURS:
        moment = start + timedelta(hours=hours)
        if moment.microsecond:
            moment = moment.replace(microsecond=0) + timedelta(seconds=1)
        result.append(moment.strftime("%Y-%m-%d %H:%M:%S UTC"))
    return result


def make_plan(root, accepted_raw, prepared_raw, wrapper_raw, observer_raw):
    accepted, prepared = unique_json(accepted_raw), unique_json(prepared_raw)
    start, end = validate_binding(root, accepted, prepared)
    manifest = {
        "schema": "cara.model-trial-monitor/v1", "trial_root": str(ROOT),
        "accepted_sha256": sha(accepted_raw), "prepared_sha256": sha(prepared_raw),
        "trial_start": start.isoformat(), "trial_end": end.isoformat(),
        "database": str(DATABASE), "service": SERVICE, "timer": TIMER,
        "wrapper_sha256": sha(wrapper_raw), "observer_sha256": sha(observer_raw),
        "runtime_source": SOURCE, "candidate_env_sha256": accepted["candidate_env_sha256"],
        "calendar_dates": calendar_dates(start), "automatic_reversion": False,
        "model_calls": 0, "messages_sent": 0,
    }
    manifest_raw = canonical(manifest)
    monitor = ROOT / "monitor"
    output = ROOT / "observations"
    # Every variable in ExecStart is validated fixed ASCII or an ISO timestamp.
    command = (f"/usr/bin/python3 -I -B {monitor}/install_monitor.py tick --root {ROOT} "
               f"--manifest-sha256 {sha(manifest_raw)} --accepted-sha256 {sha(accepted_raw)} "
               f"--trial-start {start.isoformat()} --trial-end {end.isoformat()}")
    service = ("[Unit]\nDescription=Cara bounded metadata-only model trial observation\n\n"
               "[Service]\nType=oneshot\nUser=root\nGroup=root\nUMask=0077\n"
               f"ExecStart={command}\n"
               "Environment=PYTHONDONTWRITEBYTECODE=1\nNoNewPrivileges=yes\n"
               "CapabilityBoundingSet=\nProtectSystem=strict\nProtectHome=read-only\n"
               "PrivateTmp=yes\nPrivateDevices=yes\nRestrictAddressFamilies=AF_UNIX\n"
               "ProtectKernelTunables=yes\nProtectKernelModules=yes\nProtectControlGroups=yes\n"
               "ProtectClock=yes\nRestrictSUIDSGID=yes\nLockPersonality=yes\n"
               f"ReadOnlyPaths={DATABASE.parent} {ROOT}\nReadWritePaths={output}\n"
               "TimeoutStartSec=60\nMemoryMax=256M\nTasksMax=16\nNice=10\nIOSchedulingClass=idle\n"
               "StandardOutput=journal\nStandardError=journal\n")
    timer = ("[Unit]\nDescription=Cara absolute 72-hour model trial observation schedule\n\n"
             "[Timer]\n" + "".join("OnCalendar=" + moment + "\n" for moment in manifest["calendar_dates"])
             + f"Unit={SERVICE}\nPersistent=true\nAccuracySec=1s\nRandomizedDelaySec=0\n\n"
             "[Install]\nWantedBy=timers.target\n")
    return {"manifest": manifest, "manifest_raw": manifest_raw,
            "service_raw": service.encode("ascii"), "timer_raw": timer.encode("ascii"),
            "wrapper_raw": wrapper_raw, "observer_raw": observer_raw}


def atomic_exclusive(path, raw, mode=0o600):
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".trial-monitor-", dir=path.parent)
        with os.fdopen(fd, "wb") as stream:
            os.chmod(temporary, mode)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        if os.name != "nt":
            fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        if temporary is not None:
            os.unlink(temporary)


def systemctl(arguments, runner=subprocess.run):
    allowed = (["daemon-reload"], ["enable", "--now", TIMER], ["stop", TIMER])
    require(arguments in allowed, "unapproved_systemctl_action")
    result = runner(["/usr/bin/systemctl", *arguments], stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=15, check=False, env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
    require(result.returncode == 0, "fixed_systemctl_action_failed")


def stop_timer(unit=TIMER, runner=subprocess.run):
    require(unit == TIMER, "unapproved_timer_unit")
    systemctl(["stop", TIMER], runner)


def fresh_units():
    for directory in (UNIT_DIR, Path("/run/systemd/system"), Path("/usr/lib/systemd/system"), Path("/lib/systemd/system")):
        for name in (SERVICE, TIMER):
            require(not (directory / name).exists() and not (directory / name).is_symlink()
                    and not (directory / (name + ".d")).exists()
                    and not (directory / (name + ".d")).is_symlink(), "monitor_unit_already_exists")
    enabled = UNIT_DIR / "timers.target.wants" / TIMER
    require(not enabled.exists() and not enabled.is_symlink(), "monitor_timer_already_enabled")


def install(root, *, dry_run=False, now=None, runner=subprocess.run):
    root = fixed_root(root)
    protected(root, directory=True)
    for path in (root / "accepted.json", root / "prepared.json"):
        protected(path)
    source = Path(__file__).resolve().parent
    plan = make_plan(root, (root / "accepted.json").read_bytes(), (root / "prepared.json").read_bytes(),
                     (source / "install_monitor.py").read_bytes(), (source / "observe_trial.py").read_bytes())
    current = timestamp(now or datetime.now(timezone.utc))
    require(timestamp(plan["manifest"]["trial_start"]) <= current < timestamp(plan["manifest"]["trial_end"]),
            "monitor_install_outside_trial_window")
    summary = {"status": "dry_run" if dry_run else "installed", "service": SERVICE, "timer": TIMER,
               "manifest_sha256": sha(plan["manifest_raw"]), "service_sha256": sha(plan["service_raw"]),
               "timer_sha256": sha(plan["timer_raw"]), "calendar_dates": plan["manifest"]["calendar_dates"],
               "automatic_reversion": False, "model_calls": 0, "messages_sent": 0}
    if dry_run:
        return summary
    require(hasattr(os, "geteuid") and os.geteuid() == 0, "root_install_required")
    fresh_units()
    monitor, output = root / "monitor", root / "observations"
    require(not monitor.exists(), "monitor_directory_already_exists")
    monitor.mkdir(mode=0o700)
    if output.exists():
        protected(output, directory=True)
    else:
        output.mkdir(mode=0o700)
    for name, raw in (("install_monitor.py", plan["wrapper_raw"]), ("observe_trial.py", plan["observer_raw"]),
                      ("manifest.json", plan["manifest_raw"])):
        atomic_exclusive(monitor / name, raw)
    atomic_exclusive(UNIT_DIR / SERVICE, plan["service_raw"], 0o644)
    atomic_exclusive(UNIT_DIR / TIMER, plan["timer_raw"], 0o644)
    systemctl(["daemon-reload"], runner)
    systemctl(["enable", "--now", TIMER], runner)
    atomic_exclusive(monitor / "installed.json", canonical(summary))
    return summary


def load_observer(path, expected_sha256):
    require(sha(path.read_bytes()) == expected_sha256, "observer_code_binding_mismatch")
    spec = importlib.util.spec_from_file_location("cara_trial_metadata_observer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tick(root, manifest_sha256, accepted_sha256, trial_start, trial_end, *, now=None,
         runner=subprocess.run, observer_loader=load_observer):
    root = fixed_root(root)
    start, end = timestamp(trial_start), timestamp(trial_end)
    require(end == start + timedelta(hours=TRIAL_HOURS), "exact_72_hour_period_required")
    require(HEX64.fullmatch(manifest_sha256 or "") and HEX64.fullmatch(accepted_sha256 or ""), "invalid_pinned_hash")
    current = timestamp(now or datetime.now(timezone.utc))
    result = None
    try:
        protected(root, directory=True)
        monitor = root / "monitor"
        for path in (monitor,):
            protected(path, directory=True)
        for path in (monitor / "manifest.json", monitor / "install_monitor.py", monitor / "observe_trial.py",
                     root / "accepted.json", root / "prepared.json"):
            protected(path)
        manifest_raw = (monitor / "manifest.json").read_bytes()
        require(sha(manifest_raw) == manifest_sha256, "monitor_manifest_binding_mismatch")
        manifest = unique_json(manifest_raw)
        require(manifest.get("schema") == "cara.model-trial-monitor/v1" and manifest.get("trial_root") == str(root)
                and manifest.get("service") == SERVICE and manifest.get("timer") == TIMER
                and manifest.get("database") == str(DATABASE), "monitor_scope_mismatch")
        require(manifest.get("trial_start") == start.isoformat() and manifest.get("trial_end") == end.isoformat()
                and manifest.get("calendar_dates") == calendar_dates(start), "monitor_schedule_binding_mismatch")
        accepted_raw, prepared_raw = (root / "accepted.json").read_bytes(), (root / "prepared.json").read_bytes()
        require(sha(accepted_raw) == accepted_sha256 == manifest["accepted_sha256"]
                and sha(prepared_raw) == manifest["prepared_sha256"], "trial_receipt_binding_mismatch")
        require(validate_binding(root, unique_json(accepted_raw), unique_json(prepared_raw)) == (start, end),
                "trial_period_binding_mismatch")
        require(sha((monitor / "install_monitor.py").read_bytes()) == manifest["wrapper_sha256"], "wrapper_code_binding_mismatch")
        observer = observer_loader(monitor / "observe_trial.py", manifest["observer_sha256"])
        result = observer.scheduled_snapshot(DATABASE, start, root / "observations", now=current,
                                             binding_sha256=accepted_sha256)
    finally:
        # This decision uses the unit-pinned original timestamps, so even altered
        # receipts or a failing observer cannot extend the deadline.
        if current >= end:
            stop_timer(runner=runner)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("install", "tick"):
        command = subparsers.add_parser(name)
        command.add_argument("--root", required=True)
        if name == "install":
            command.add_argument("--dry-run", action="store_true")
        else:
            command.add_argument("--manifest-sha256", required=True)
            command.add_argument("--accepted-sha256", required=True)
            command.add_argument("--trial-start", required=True)
            command.add_argument("--trial-end", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            result = install(args.root, dry_run=args.dry_run)
        else:
            result = tick(args.root, args.manifest_sha256, args.accepted_sha256, args.trial_start, args.trial_end)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception:
        # An unexpected observer exception may include a local path or malformed
        # source metadata. The wrapper never copies such text into the journal.
        print(json.dumps({"status": "failed", "reason": "monitor_operation_failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
