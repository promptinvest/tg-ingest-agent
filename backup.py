#!/usr/bin/env python3
"""Daily database backup: a consistent SQLite snapshot, kept locally (rotated)
and copied OFF-BOX after encryption. The droplet was wiped once before, and
ingest.db is the single file everything Cara is lives in.

Off-box targets, in order of preference:
  * DO Spaces (STORAGE_BACKEND=spaces + keys) — uploaded via storage.put_object.
  * Telegram (the fleet notify bot + ops chat) — an AES-256 encrypted snapshot
    is posted as a document. Skipped over ~45 MB (the bot limit is 50 MB).
With neither configured the snapshot is still taken locally and a WARNING is
logged — a local-only copy does not survive a droplet wipe.

Runs as a durable daily job ("maintenance"/"db_backup"): retried on failure,
survives restarts, never blocks the live request path.
"""
import gzip
import os
import sqlite3
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from common import log
import storage
from tg_api import tg_send_document

TG_UPLOAD_LIMIT = 45 * 1024 * 1024  # stay under Telegram's 50 MB bot cap
PBKDF2_ITERATIONS = 200_000


class BackupEncryptionError(RuntimeError):
    pass


def backups_dir(cfg):
    return cfg.db_path.parent / "backups"


def snapshot(cfg, conn):
    """Write a consistent gzipped snapshot of the live DB; returns its Path.
    Uses the sqlite3 online-backup API, so WAL/in-flight writes are safe."""
    out_dir = backups_dir(cfg)
    out_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(out_dir, 0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    raw = out_dir / f"ingest-{stamp}.db"
    fd = os.open(raw, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    os.close(fd)
    dst = sqlite3.connect(str(raw))
    try:
        conn.backup(dst)
    finally:
        dst.close()
    gz = out_dir / (raw.name + ".gz")
    gz_fd = os.open(gz, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with open(raw, "rb") as f_in, os.fdopen(gz_fd, "wb") as gz_raw, \
            gzip.GzipFile(fileobj=gz_raw, mode="wb") as f_out:
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
        Path(str(stale) + ".enc").unlink(missing_ok=True)
        removed += 1
    return removed


def offsite_configured(cfg):
    return (storage.backend(cfg) == "spaces" or
            bool(cfg.fleet_notify_token and cfg.fleet_notify_chat_id))


def encrypt_snapshot(cfg, gz_path):
    """Encrypt a snapshot atomically with the operator-held passphrase file."""
    key_path = Path(cfg.backup_encryption_key_file)
    if not key_path.is_file():
        raise BackupEncryptionError(
            f"backup encryption key is missing: {key_path}")
    enc_path = Path(str(gz_path) + ".enc")
    tmp_path = Path(str(enc_path) + ".tmp")
    try:
        tmp_path.unlink(missing_ok=True)
        tmp_fd = os.open(tmp_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(tmp_fd)
        subprocess.run(
            ["openssl", "enc", "-aes-256-cbc", "-salt", "-pbkdf2", "-iter",
             str(PBKDF2_ITERATIONS), "-in", str(gz_path), "-out", str(tmp_path),
             "-pass", f"file:{key_path}"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        if stat.S_IMODE(tmp_path.stat().st_mode) != 0o600:
            os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, enc_path)
    except (OSError, subprocess.CalledProcessError) as exc:
        tmp_path.unlink(missing_ok=True)
        detail = getattr(exc, "stderr", b"")
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", errors="replace")
        raise BackupEncryptionError(f"backup encryption failed: {str(detail or exc)[:300]}") from exc
    return enc_path


def offsite(cfg, encrypted_path):
    """Copy the snapshot off-box. Returns a short description of where it
    went, or '' when no off-box target is configured/possible. Raises
    (StorageError / TelegramError) on a FAILED transfer so the job retries —
    a broken off-box copy must not look green."""
    if not offsite_configured(cfg):
        return ""
    if not str(encrypted_path).endswith(".db.gz.enc"):
        raise BackupEncryptionError("refusing to send an unencrypted database backup off-box")
    data = encrypted_path.read_bytes()
    if storage.backend(cfg) == "spaces":
        key = storage.put_object(
            cfg, storage.object_key(cfg, f"backups/{encrypted_path.name}"), data)
        return f"spaces:{key}"
    if cfg.fleet_notify_token and cfg.fleet_notify_chat_id:
        if len(data) > TG_UPLOAD_LIMIT:
            log(f"backup too big for the Telegram off-box copy ({len(data)} bytes)")
            return ""
        tg_send_document(cfg.fleet_notify_token, cfg.fleet_notify_chat_id,
                         encrypted_path.name, data,
                         caption=f"🗄 {cfg.fleet_notify_label} — daily DB backup",
                         content_type="application/octet-stream")
        return "telegram:fleet"
    return ""  # unreachable, kept defensive for future target types


def run(cfg, conn):
    """The daily backup job body; returns a result dict for the job log."""
    gz = snapshot(cfg, conn)
    upload_path = encrypt_snapshot(cfg, gz) if offsite_configured(cfg) else gz
    removed = rotate(cfg)
    where = offsite(cfg, upload_path)
    if not where:
        log("backup WARNING: no off-box target configured — local snapshot only")
    log(f"db backup done: {gz.name} ({gz.stat().st_size} bytes), "
        f"rotated out {removed}, offsite={where or 'none'}")
    return {"file": gz.name, "bytes": gz.stat().st_size, "offsite": where,
            "encrypted_file": upload_path.name if upload_path != gz else ""}
