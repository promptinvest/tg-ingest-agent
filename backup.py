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
import re
import shutil
import sqlite3
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from common import log
import storage
import store
from tg_api import tg_send_document

TG_UPLOAD_LIMIT = 45 * 1024 * 1024  # stay under Telegram's 50 MB bot cap
# Nearing the cap: warn ONCE (kv-flagged) while the copy still goes through, so
# the off-box copy never stops silently the day the DB crosses the limit.
TG_UPLOAD_WARN = 35 * 1024 * 1024
# offsite() return value when the only off-box target refuses the file (size cap)
# — distinct from '' ("nothing configured"), which is a different warning.
OFFBOX_BLOCKED = "blocked:size"
PBKDF2_ITERATIONS = 200_000


class BackupEncryptionError(RuntimeError):
    pass


class BackupRestoreError(RuntimeError):
    """The newest snapshot could not be taken back to an openable database."""


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
    gz = out_dir / (raw.name + ".gz")
    tmp = Path(str(gz) + ".tmp")
    fd = os.open(raw, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    os.close(fd)
    # The raw snapshot and the half-written archive are ALWAYS removed: a failure
    # mid-gzip used to leave `ingest-<stamp>.db` behind, which rotate() cannot see
    # (it globs only `*.db.gz`) — every failed day leaked a full DB copy until the
    # disk filled. Only a COMPLETE archive gets the rotation-visible name.
    try:
        dst = sqlite3.connect(str(raw))
        try:
            conn.backup(dst)
        finally:
            dst.close()
        tmp.unlink(missing_ok=True)
        gz_fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with open(raw, "rb") as f_in, os.fdopen(gz_fd, "wb") as gz_raw, \
                gzip.GzipFile(fileobj=gz_raw, mode="wb") as f_out:
            while True:
                chunk = f_in.read(1 << 20)
                if not chunk:
                    break
                f_out.write(chunk)
        os.replace(tmp, gz)
    finally:
        raw.unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)  # a no-op after a successful os.replace
    return gz


# Exactly what snapshot() names its own files: ingest-<UTC stamp>.db[.gz[.tmp]].
# Deliberately NOT `ingest-*.db` — the backups dir also holds hand-made
# pre-change copies like `ingest-pre-july15-corrections-<stamp>.db`, and this
# sweep must never mistake an operator's deliberate backup for our garbage.
_OWN_SNAPSHOT = re.compile(r"^ingest-\d{8}T\d{6}Z\.db(?:\.gz(?:\.enc)?\.tmp)?$")

# The finished archive, for retention. Same reason as above: `ingest-*.db.gz`
# also matches hand-made copies like ingest-pre-review-fix-<stamp>.db.gz, which
# then eat slots in backup_keep — on the live box that silently held 5 automated
# snapshots instead of 7 — and would be deletable if a future name sorted lower.
_OWN_ARCHIVE = re.compile(r"^ingest-(\d{8}T\d{6}Z)\.db\.gz$")

# The encrypted form — the only one that ever leaves the box, and therefore the
# one the restore self-check prefers when it exists for the newest stamp.
_OWN_ENCRYPTED = re.compile(r"^ingest-(\d{8}T\d{6}Z)\.db\.gz\.enc$")

# Scratch directory (inside the 0700 backups dir) the restore self-check works
# in. Fixed name on purpose: a run killed mid-check leaves a DECRYPTED copy of
# the database behind, and a deterministic name is one the next sweep can find.
RESTORE_CHECK_DIR = "restore-check"


def sweep_stray(cfg):
    """Remove backup-dir garbage rotation can't see: raw `.db` snapshots and
    half-written `.tmp` archives left by an interrupted run of OUR snapshot().

    Scoped to the machine-generated stamp form on purpose. Anything else in the
    directory — a manually named pre-change copy, its -wal/-shm companions —
    belongs to whoever put it there and is left alone. The live DB lives one
    level up and is never in scope either way.
    """
    out_dir = backups_dir(cfg)
    removed = 0
    # Our own restore-check scratch dir, left by a killed run: it can hold a
    # DECRYPTED database, so it must not wait for next month's check. Safe to do
    # from here — one process, one thread, so no self-check is in flight while a
    # backup job runs. Matched by exact name; an operator's directory is not ours.
    scratch = out_dir / RESTORE_CHECK_DIR
    if scratch.is_dir():
        shutil.rmtree(scratch, ignore_errors=True)
        removed += 1
    for stray in sorted(out_dir.iterdir()):
        if stray.is_file() and _OWN_SNAPSHOT.match(stray.name):
            stray.unlink(missing_ok=True)
            removed += 1
    return removed


def rotate(cfg):
    """Keep only the newest cfg.backup_keep local snapshots (name-sortable
    UTC stamps); returns how many stale ones were removed.

    Counts and prunes ONLY snapshots this module made. A hand-made copy in the
    same directory is neither deleted nor counted against backup_keep.
    """
    sweep_stray(cfg)
    files = sorted((p for p in backups_dir(cfg).iterdir()
                    if p.is_file() and _OWN_ARCHIVE.match(p.name)),
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


def _size_alert(conn, size):
    """One issue row when the encrypted snapshot nears the Telegram cap — not one
    per day: a kv flag holds the announced state (budget-notice pattern) and is
    cleared once the size drops back."""
    if conn is None:
        return
    warned = store.kv_get(conn, "backup_size_warned") == "1"
    if size > TG_UPLOAD_WARN:
        if not warned:
            store.issue_add(conn, None, "backup_offbox_near_limit",
                            f"encrypted snapshot {size} bytes — nearing the "
                            f"{TG_UPLOAD_LIMIT} byte Telegram off-box limit")
            store.kv_set(conn, "backup_size_warned", "1")
    elif warned:
        store.kv_set(conn, "backup_size_warned", "0")


def offsite(cfg, encrypted_path, conn=None):
    """Copy the snapshot off-box. Returns a short description of where it
    went, OFFBOX_BLOCKED when the only target refuses it, or '' when no off-box
    target is configured/possible. Raises (StorageError / TelegramError) on a
    FAILED transfer so the job retries — a broken off-box copy must not look
    green."""
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
        # Growing past the bot cap silently ended the ONLY off-box copy while the
        # job stayed green. Log an issue (it surfaces in the issues report / weekly
        # review) and report the blocked state to the caller.
        if len(data) > TG_UPLOAD_LIMIT:
            log(f"backup too big for the Telegram off-box copy ({len(data)} bytes)")
            if conn is not None:
                store.issue_add(conn, None, "backup_offbox_blocked",
                                f"encrypted snapshot {len(data)} bytes exceeds the "
                                f"{TG_UPLOAD_LIMIT} byte Telegram limit — no off-box copy")
            return OFFBOX_BLOCKED
        _size_alert(conn, len(data))
        tg_send_document(cfg.fleet_notify_token, cfg.fleet_notify_chat_id,
                         encrypted_path.name, data,
                         caption=f"🗄 {cfg.fleet_notify_label} — daily DB backup",
                         content_type="application/octet-stream")
        return "telegram:fleet"
    return ""  # unreachable, kept defensive for future target types


def run(cfg, conn):
    """The daily backup job body; returns a result dict for the job log."""
    try:
        gz = snapshot(cfg, conn)
    except Exception:
        # Retention must not depend on THIS attempt succeeding. On a nearly full
        # disk snapshot() is precisely what fails, and skipping rotation there
        # leaves the disk full — the feedback loop this module exists to break.
        rotate(cfg)
        raise
    # Rotate BEFORE encryption/off-box: a raised BackupEncryptionError used to skip
    # retention entirely, so a broken key file grew the backups dir without bound.
    removed = rotate(cfg)
    upload_path = encrypt_snapshot(cfg, gz) if offsite_configured(cfg) else gz
    where = offsite(cfg, upload_path, conn)
    blocked = where == OFFBOX_BLOCKED
    if blocked:
        log("backup WARNING: the encrypted snapshot is too big for the only "
            "off-box target — local snapshot only")
    elif not where:
        log("backup WARNING: no off-box target configured — local snapshot only")
    log(f"db backup done: {gz.name} ({gz.stat().st_size} bytes), "
        f"rotated out {removed}, offsite={where or 'none'}")
    return {"file": gz.name, "bytes": gz.stat().st_size, "offsite": where,
            "offbox_blocked": blocked,
            "encrypted_file": upload_path.name if upload_path != gz else ""}


# -- restore self-check -------------------------------------------------------
# Until now nothing ever proved a snapshot could be turned back into a database:
# the job was green when the file was WRITTEN and SENT. Once a month the newest
# snapshot goes through the real recovery path — decrypt with the on-box key,
# gunzip, open, PRAGMA integrity_check — in a scratch dir that is always removed.
#
# Honest about what this does NOT prove: the key file lives on the same droplet
# as the database it protects, so a green check says "archive and key agree
# here", not "the off-box copy is openable after this box is gone". That second
# property needs an OFF-BOX copy of the key file, which is an operator decision
# and is deliberately not automated (the key is never printed, moved or copied
# by this module).

# The scratch copies (the decrypted .gz plus the expanded .db) land next to the
# backups. Refuse to start unless the disk can hold them with room to spare —
# this check must never become the thing that fills the disk it exists to guard.
#
# The EXPANSION BUDGET is what the gunzip is allowed to write, and it is derived
# from what a legitimate restore can possibly produce, not from free space. A
# snapshot is a page-for-page copy of the live database (the online-backup API,
# and nothing in this codebase ever VACUUMs, so the file never shrinks) — the
# live file is therefore a hard upper bound on the restored size of any snapshot,
# and 4x it is already generous. The archive multiple and the floor cover a fresh
# or tiny database, where a multiple of almost nothing would be an absurdly tight
# cap; note that a nearly-empty SQLite file compresses FAR better than 12x, so
# "12x the archive" alone would refuse perfectly good restores.
RESTORE_LIVE_FACTOR = 4
RESTORE_FREE_FACTOR = 12          # gzipped SQLite expands ~5-10x; leave headroom
RESTORE_MIN_BUDGET = 8 * 1024 * 1024
RESTORE_FREE_MARGIN = 32 * 1024 * 1024


def restore_budget(cfg, src_size):
    """Most bytes the restore of a `src_size` archive may legitimately write.

    The live size counts the `-wal` file too: in WAL mode the main file can be far
    smaller than the logical database until a checkpoint lands, and a snapshot
    (taken through the online-backup API) contains everything — so measuring the
    main file alone would set a bound BELOW what a healthy restore produces."""
    live = 0
    for part in (cfg.db_path, Path(str(cfg.db_path) + "-wal")):
        try:
            live += part.stat().st_size
        except OSError:      # missing on a fresh box / checkpointed away
            pass
    return max(live * RESTORE_LIVE_FACTOR, src_size * RESTORE_FREE_FACTOR,
               RESTORE_MIN_BUDGET)


def latest_snapshot(cfg):
    """`(path, encrypted)` for the newest snapshot THIS module made, or
    `(None, False)`.

    Newest by STAMP across both forms; the encrypted copy wins only when it is a
    copy of THAT stamp. Preferring any `.enc` over any `.gz` was wrong: `run()`
    never deletes an `.enc` after upload and `rotate()` only prunes one together
    with its `.gz`, so if off-box config goes away (fleet token cleared, backend
    switched) up to `backup_keep` stale `.enc` files outlive it — and the monthly
    check would keep reporting `integrity: ok` for a week-old archive while
    today's snapshot was never opened. "The newest snapshot" has to mean it."""
    out_dir = backups_dir(cfg)
    if not out_dir.is_dir():
        return None, False
    best_stamp, best = "", (None, False)
    for p in out_dir.iterdir():
        if not p.is_file():
            continue
        for pattern, encrypted in ((_OWN_ENCRYPTED, True), (_OWN_ARCHIVE, False)):
            found = pattern.match(p.name)
            if not found:
                continue
            stamp = found.group(1)
            if stamp > best_stamp or (stamp == best_stamp and encrypted and not best[1]):
                best_stamp, best = stamp, (p, encrypted)
            break
    return best


def _decrypt_snapshot(cfg, enc_path, out_path):
    """The exact inverse of encrypt_snapshot — same cipher, KDF and iteration
    count, so the one-liner documented in SOLUTION.md §9 is what runs here."""
    key_path = Path(cfg.backup_encryption_key_file)
    if not key_path.is_file():
        raise BackupRestoreError(f"backup encryption key is missing: {key_path}")
    try:
        subprocess.run(
            ["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter",
             str(PBKDF2_ITERATIONS), "-in", str(enc_path), "-out", str(out_path),
             "-pass", f"file:{key_path}"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", b"")
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", errors="replace")
        raise BackupRestoreError(
            f"decrypt failed: {str(detail or exc)[:200]}") from exc


def _gunzip(src, dst, max_bytes):
    """Expand `src` into `dst`, refusing to write more than `max_bytes` — a
    corrupt archive must not fill the disk on the way to being diagnosed."""
    written = 0
    with gzip.open(src, "rb") as f_in:
        fd = os.open(dst, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as f_out:
            while True:
                chunk = f_in.read(1 << 20)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise BackupRestoreError(
                        f"restored database exceeds the free-space budget "
                        f"({max_bytes} bytes) — archive corrupt?")
                f_out.write(chunk)
    return written


def integrity_check(db_path):
    """Open the restored file READ-ONLY — the check must not MODIFY the artifact
    it is judging — and run the real PRAGMA. Returns the table count; raises
    otherwise.

    Read-only is NOT the same as touching nothing: `snapshot()` copies the live
    WAL database with the sqlite3 backup API, so the restored file carries the
    WAL format bytes, and even a `mode=ro` connection to a WAL database needs the
    `-shm` wal-index — which SQLite creates in the CONTAINING DIRECTORY. The
    scratch dir must therefore stay writable (it is rmtree'd on every path); a
    future sandbox tightening that made it read-only would report a perfectly
    good backup as unrestorable."""
    try:
        conn = sqlite3.connect(f"{Path(db_path).as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise BackupRestoreError(f"restored file will not open: {exc}") from exc
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        verdict = (row[0] if row else "") or ""
        if verdict != "ok":
            raise BackupRestoreError(
                f"integrity_check on the restored database: {verdict[:200]}")
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")]
    except sqlite3.DatabaseError as exc:
        raise BackupRestoreError(
            f"restored file is not a readable database: {exc}") from exc
    finally:
        conn.close()
    # A file can be a perfectly valid EMPTY database. Cara's has messages.
    if "messages" not in tables:
        raise BackupRestoreError(
            "restored database has no 'messages' table — not Cara's database")
    return len(tables)


def verify_restore(cfg, conn=None):
    """Prove the newest snapshot restores. Returns a result dict for the job log;
    raises when it does not — ANY failure (BackupRestoreError, or an OSError from
    the scratch copy) is logged and recorded as a `backup_restore_failed` issue
    first. Every scratch file is removed on every path."""
    out_dir = backups_dir(cfg)
    scratch = out_dir / RESTORE_CHECK_DIR
    try:
        src, encrypted = latest_snapshot(cfg)
        if src is None:
            raise BackupRestoreError(
                "no snapshot to verify — the daily backup has never produced one")
        # ONE number bounds both halves of this: the disk precheck and the gunzip.
        # The first version passed `free - MARGIN` to the gunzip, so on the live
        # box (~68 GB free, a tens-of-MB archive) the write cap was ~400x the
        # budget the precheck had just demanded — the guard could not fire until
        # the expansion had ALREADY taken the filesystem down to 32 MB free, i.e.
        # only after causing the disk-full spiral it exists to prevent, on a 4 GB
        # box shared with a sibling bot.
        src_size = src.stat().st_size
        max_bytes = restore_budget(cfg, src_size)
        # Room for the expansion, the archive copy beside it, and a margin. `free`
        # is sampled here, before either file exists.
        needed = max_bytes + src_size + RESTORE_FREE_MARGIN
        free = shutil.disk_usage(out_dir).free
        if free < needed:
            raise BackupRestoreError(
                f"not enough free disk to restore-check {src.name}: {free} free, "
                f"{needed} needed")
        shutil.rmtree(scratch, ignore_errors=True)  # leftovers from a killed run
        scratch.mkdir(parents=True)
        os.chmod(scratch, 0o700)
        gz_path = scratch / "restored.db.gz"
        if encrypted:
            _decrypt_snapshot(cfg, src, gz_path)
        else:
            shutil.copyfile(src, gz_path)
        os.chmod(gz_path, 0o600)
        db_path = scratch / "restored.db"
        try:
            written = _gunzip(gz_path, db_path, max_bytes)
        except BackupRestoreError:
            raise
        except (OSError, EOFError) as exc:  # BadGzipFile is an OSError
            raise BackupRestoreError(f"gunzip failed: {exc}") from exc
        tables = integrity_check(db_path)
        log(f"backup restore self-check OK: {src.name} -> {written} bytes, "
            f"{tables} tables")
        return {"file": src.name, "encrypted": encrypted, "bytes": written,
                "tables": tables, "integrity": "ok"}
    except Exception as exc:  # noqa: BLE001 — every failure leaves the documented signal
        # Deliberately NOT `except BackupRestoreError`. The setup steps
        # (disk_usage, rmtree/mkdir/chmod, the copyfile of the archive) raise
        # OSError, and ENOSPC during that copy is the single most likely real
        # failure of this job — exactly the case it exists for. Catching only our
        # own type let that one through with no `FAILED` log line and no
        # `backup_restore_failed` issue, leaving runtime.drain's generic
        # `job_failed` as the only trace, and only after the retries ran out.
        detail = str(exc) or repr(exc)
        log(f"backup restore self-check FAILED: {detail}")
        if conn is not None:
            try:
                store.issue_add(conn, None, "backup_restore_failed", str(exc)[:300])
            except Exception as issue_exc:  # noqa: BLE001
                # The likeliest reason this check failed at all is a full disk, and
                # that is also what stops the issue row being written. Recording the
                # attempt must not replace the diagnosis with 'database or disk is
                # full' — the caller needs the ORIGINAL reason.
                log(f"could not record the restore-check failure: {issue_exc!r}")
        raise
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
