#!/usr/bin/env python3
"""Verified deployment manifests and idempotent Telegram delivery receipts."""
import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import store
from common import log
from tg_api import TelegramError, tg_call


MANIFEST_SCHEMA = "cara.deployment/v1"
MANIFEST_NAME = "DEPLOYMENT.json"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, 0o644)
        os.replace(temp, path)
        directory = os.open(
            path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def write_installed(path, *, build_version, source_revision, source_dirty,
                    test_summary, backup_dir):
    installed_at = _now()
    deployment_id = hashlib.sha256(
        (str(build_version) + "\0" + str(source_revision) + "\0"
         + installed_at).encode("utf-8") + os.urandom(16)
    ).hexdigest()[:24]
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "deployment_id": deployment_id,
        "build_version": _text(build_version, 80),
        "source_revision": _text(source_revision or "unknown", 80),
        "source_dirty": bool(source_dirty),
        "test_summary": _text(test_summary, 300),
        "verification_summary": "",
        "backup_dir": _text(backup_dir, 500),
        "status": "installed",
        "installed_at": installed_at,
        "verified_at": None,
    }
    _atomic_json(path, manifest)
    return manifest


def mark_verified(path, verification_summary):
    manifest = _load(path)
    if manifest["status"] not in {"installed", "verified"}:
        raise ValueError("deployment manifest has invalid status")
    manifest["status"] = "verified"
    manifest["verification_summary"] = _text(verification_summary, 500)
    manifest["verified_at"] = _now()
    _atomic_json(path, manifest)
    return manifest


def load_verified(path, build_version):
    try:
        manifest = _load(path)
    except (OSError, ValueError, TypeError):
        return None
    if (manifest.get("status") != "verified"
            or manifest.get("build_version") != str(build_version or "").strip()):
        return None
    return manifest


def verify_manifest(path, build_version):
    manifest = _load(path)
    if manifest["build_version"] != str(build_version or "").strip():
        raise ValueError("deployment manifest build does not match VERSION")
    return manifest


def announce(conn, cfg, manifest_path, build_version):
    """Send once. A crash/transport ambiguity is latched and never retried."""
    manifest = load_verified(manifest_path, build_version)
    if manifest is None:
        return "not_ready"
    token = str(getattr(cfg, "fleet_notify_token", "") or "").strip()
    chat_id = str(getattr(cfg, "fleet_notify_chat_id", "") or "").strip()
    label = str(
        getattr(cfg, "fleet_notify_label", "tg-ingest-agent (Cara)")
        or "tg-ingest-agent (Cara)").strip()
    text = format_message(label, manifest)
    destination = (
        hashlib.sha256(chat_id.encode("utf-8")).hexdigest()[:20]
        if chat_id else "unconfigured")
    row = store.deployment_notification_prepare(
        conn, manifest, text, destination)
    if row["status"] in {"sent", "ambiguous", "failed"}:
        return row["status"]
    if row["status"] == "sending":
        store.deployment_notification_finish(
            conn, row["id"], "ambiguous",
            error="process restarted while Telegram delivery outcome was unknown")
        return "ambiguous"
    row = store.deployment_notification_claim(conn, row["id"])
    if row is None:
        current = store.deployment_notification_get(
            conn, manifest["deployment_id"])
        return current["status"] if current else "missing"
    if not token or not chat_id:
        store.deployment_notification_finish(
            conn, row["id"], "failed",
            error="FLEET_NOTIFY_BOT_TOKEN/CHAT_ID not configured")
        log("fleet deploy notice failed: notification credentials not configured")
        return "failed"
    try:
        result = tg_call(token, "sendMessage", {
            "chat_id": chat_id,
            "text": text,
        })
    except TelegramError as exc:
        status = "ambiguous" if exc.outcome_unknown else "failed"
        store.deployment_notification_finish(
            conn, row["id"], status, error=str(exc))
        log(f"fleet deploy notice {status}: {exc}")
        return status
    message_id = result.get("message_id") if isinstance(result, dict) else None
    if not isinstance(message_id, int) or message_id <= 0:
        store.deployment_notification_finish(
            conn, row["id"], "ambiguous",
            error="Telegram acknowledged delivery without a valid message id")
        return "ambiguous"
    store.deployment_notification_finish(
        conn, row["id"], "sent", message_id=message_id)
    store.kv_set(conn, "deployed_version", manifest["build_version"])
    return "sent"


def format_message(label, manifest):
    revision = manifest["source_revision"]
    if manifest.get("source_dirty"):
        revision += " + local changes"
    return "\n".join([
        f"✅ {label} — deployment verified",
        f"Receipt: {manifest['deployment_id']}",
        f"Build: {manifest['build_version']}",
        f"Source: {revision}",
        f"Tests: {manifest['test_summary']}",
        f"Verification: {manifest['verification_summary']}",
        f"Rollback backup: {manifest['backup_dir']}",
    ])[:4000]


def _load(path):
    raw = Path(path).read_bytes()
    if len(raw) > 16 * 1024:
        raise ValueError("deployment manifest is too large")
    value = json.loads(raw.decode("utf-8"))
    required = {
        "schema", "deployment_id", "build_version", "source_revision", "source_dirty",
        "test_summary", "verification_summary", "backup_dir", "status",
        "installed_at", "verified_at",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("deployment manifest fields are invalid")
    if value["schema"] != MANIFEST_SCHEMA or type(value["source_dirty"]) is not bool:
        raise ValueError("deployment manifest schema is invalid")
    for key, limit in (
        ("deployment_id", 80),
        ("build_version", 80), ("source_revision", 80),
        ("test_summary", 300), ("verification_summary", 500),
        ("backup_dir", 500), ("installed_at", 80),
    ):
        _text(value[key], limit, allow_empty=key == "verification_summary")
    if value["verified_at"] is not None:
        _text(value["verified_at"], 80)
    if value["status"] not in {"installed", "verified"}:
        raise ValueError("deployment manifest status is invalid")
    return value


def _text(value, maximum, allow_empty=False):
    if not isinstance(value, str):
        raise ValueError("deployment manifest text is invalid")
    value = " ".join(value.split()).strip()
    if len(value) > maximum or (not allow_empty and not value):
        raise ValueError("deployment manifest text is invalid")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    installed = sub.add_parser("write-installed")
    installed.add_argument("--manifest", required=True)
    installed.add_argument("--build-version", required=True)
    installed.add_argument("--source-revision", required=True)
    installed.add_argument("--source-dirty", default="false")
    installed.add_argument("--test-summary", required=True)
    installed.add_argument("--backup-dir", required=True)
    verified = sub.add_parser("mark-verified")
    verified.add_argument("--manifest", required=True)
    verified.add_argument("--verification-summary", required=True)
    check = sub.add_parser("verify-manifest")
    check.add_argument("--manifest", required=True)
    check.add_argument("--build-file", required=True)
    args = parser.parse_args(argv)
    if args.command == "write-installed":
        write_installed(
            args.manifest,
            build_version=args.build_version,
            source_revision=args.source_revision,
            source_dirty=str(args.source_dirty).strip().lower()
            in {"1", "true", "yes"},
            test_summary=args.test_summary,
            backup_dir=args.backup_dir,
        )
        return 0
    if args.command == "mark-verified":
        mark_verified(args.manifest, args.verification_summary)
        return 0
    if args.command == "verify-manifest":
        build = Path(args.build_file).read_text(encoding="utf-8").strip()
        verify_manifest(args.manifest, build)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
