#!/usr/bin/env python3
"""Daily database backup: a consistent SQLite snapshot, kept locally (rotated)
and copied OFF-BOX — the droplet was wiped once before, and ingest.db is the
single file everything Cara is lives in (notes, journals, memory, her life).

Off-box targets, in order of preference:
  * DO Spaces (STORAGE_BACKEND=spaces + keys) — uploaded via storage.put_object.
  * Telegram (the fleet notify bot + ops chat) — the gzipped snapshot is posted
    as a document, which stores a durable copy in Telegram's cloud. Skipped for
    snapshots over ~45 MB (the bot upload limit is 50 MB).
With neither configured the snapshot is still taken locally and a WARNING is
logged — a local-only copy does not survive a droplet wipe.

Runs as a durable daily job ("maintenance"/"db_backup"): retried on failure,
survives restarts, never blocks the live request path.
"""
import gzip
import sqlite3
from datetime import datetime, timezone

from common import log
import storage
from tg_api import tg_send_document

TG_UPLOAD_LIMIT = 45 * 1024 * 1024  # stay under Telegram's 50 MB bot cap


def backups_dir(cfg):
    return cfg.db_path.parent / "backups"


def snapshot(cfg, conn):
    """Write a consistent gzipped snapshot of the live DB; returns its Path.
    Uses the sqlite3 online-backup API, so WAL/in-flight writes are safe."""
    out_dir = backups_dir(cfg)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    raw = out_dir / f"ingest-{stamp}.db"
    dst = sqlite3.connect(str(raw))
    try:
        conn.backup(dst)
    finally:
        dst.close()
    gz = out_dir / (raw.name + ".gz")
    with open(raw, "rb") as f_in, gzip.open(gz, "wb") as f_out:
        while True:
            chunk = f_in.read(1 << 20)
            if not chunk:
                break
            f_out.write(chunk)
    raw.unlink(missing_ok=True)
    return gz


def rotate(cfg):
    """Keep only the newest cfg.backup_keep local snapshots (name-sortable
    UTC stamps); returns how many stale ones were removed."""
    files = sorted(backups_dir(cfg).glob("ingest-*.db.gz"),
                   key=lambda p: p.name, reverse=True)
    removed = 0
    for stale in files[cfg.backup_keep:]:
        stale.unlink(missing_ok=True)
        removed += 1
    return removed


def offsite(cfg, gz_path):
    """Copy the snapshot off-box. Returns a short description of where it
    went, or '' when no off-box target is configured/possible. Raises
    (StorageError / TelegramError) on a FAILED transfer so the job retries —
    a broken off-box copy must not look green."""
    data = gz_path.read_bytes()
    if storage.backend(cfg) == "spaces":
        key = storage.put_object(
            cfg, storage.object_key(cfg, f"backups/{gz_path.name}"), data)
        return f"spaces:{key}"
    if cfg.fleet_notify_token and cfg.fleet_notify_chat_id:
        if len(data) > TG_UPLOAD_LIMIT:
            log(f"backup too big for the Telegram off-box copy ({len(data)} bytes)")
            return ""
        tg_send_document(cfg.fleet_notify_token, cfg.fleet_notify_chat_id,
                         gz_path.name, data,
                         caption=f"🗄 {cfg.fleet_notify_label} — daily DB backup",
                         content_type="application/gzip")
        return "telegram:fleet"
    return ""


def run(cfg, conn):
    """The daily backup job body; returns a result dict for the job log."""
    gz = snapshot(cfg, conn)
    removed = rotate(cfg)
    where = offsite(cfg, gz)
    if not where:
        log("backup WARNING: no off-box target configured — local snapshot only")
    log(f"db backup done: {gz.name} ({gz.stat().st_size} bytes), "
        f"rotated out {removed}, offsite={where or 'none'}")
    return {"file": gz.name, "bytes": gz.stat().st_size, "offsite": where}
