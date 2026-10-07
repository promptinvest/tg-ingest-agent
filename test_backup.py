#!/usr/bin/env python3
"""Offline unit tests: router, LLM gateway, reminders, spend, texts, memory."""
import gc
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
import weakref
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import action_truth
import boss_model
import common
import converse
import events
import fetch
import ingest
import gcal
import jobs
import journals
import knowledge
import llm
import media
import memory_curator
import pdftext
import persona
import relationship
import runtime
import self_model
import reminders
import review
import router
import skill_manifest
import spend
import storage
import store
import sysinfo
import texts
import tg_api
import trace as tracing


from testlib import make_config, setUpModule, tearDownModule

class BackupAndDiskHardeningTests(unittest.TestCase):
    """WP1 of the 2026-07-24 review (the 'disk-full death spiral', first half):
    a failed backup no longer leaks a raw .db snapshot or a partial archive,
    rotation runs even when encryption fails, an off-box copy blocked by the
    Telegram size cap is loud instead of green, low disk space is announced
    before it kills every write, a terminally failed backup reaches the boss,
    and a job retry waits instead of burning both attempts in one drain pass."""

    def setUp(self):
        import tg_ingest_agent
        self.tmp = tempfile.TemporaryDirectory()
        cfg = make_config(DB_PATH=str(Path(self.tmp.name) / "pd.db"),
                          MEDIA_DIR=str(Path(self.tmp.name) / "m"))
        self.agent = tg_ingest_agent.Agent(cfg)

    def tearDown(self):
        self.agent.conn.close()
        self.tmp.cleanup()

    # -- T1.1 no raw-snapshot leak, no partial archives ------------------------

    def test_failed_snapshot_leaves_no_raw_db_and_no_partial_archive(self):
        # A failure mid-gzip used to leave `ingest-<stamp>.db` behind — invisible
        # to rotate() (it globs `*.db.gz`), so every failed day leaked a full DB
        # copy until the disk filled.
        import backup
        conn, cfg = self.agent.conn, self.agent.cfg
        with mock.patch.object(backup.gzip, "GzipFile",
                               side_effect=OSError("no space left on device")):
            with self.assertRaises(OSError):
                backup.snapshot(cfg, conn)
        left = sorted(p.name for p in backup.backups_dir(cfg).iterdir())
        self.assertEqual(left, [])            # no .db, no .gz, no .tmp
        gz = backup.snapshot(cfg, conn)       # success leaves ONLY the archive
        self.assertEqual(sorted(p.name for p in backup.backups_dir(cfg).iterdir()),
                         [gz.name])

    def test_rotate_sweeps_stray_raw_snapshots_and_tmp_files(self):
        import backup
        cfg = self.agent.cfg
        d = backup.backups_dir(cfg)
        d.mkdir(parents=True, exist_ok=True)
        (d / "ingest-20200101T000000Z.db").write_bytes(b"leaked raw snapshot")
        (d / "ingest-20200101T000000Z.db.gz.tmp").write_bytes(b"half written")
        for i in range(3):
            (d / f"ingest-2026010{i}T000000Z.db.gz").write_bytes(b"x")
        cfg.backup_keep = 2
        removed = backup.rotate(cfg)
        left = sorted(p.name for p in d.iterdir())
        self.assertEqual(removed, 1)          # one stale ARCHIVE pruned
        self.assertEqual(left, ["ingest-20260101T000000Z.db.gz",
                                "ingest-20260102T000000Z.db.gz"])

    def test_rotation_neither_counts_nor_deletes_hand_made_archives(self):
        # Audit finding: 2eefa19 scoped sweep_stray to our own names but left
        # rotate() globbing ingest-*.db.gz. On the live box the two hand-made
        # .gz copies ate 2 of the 7 retention slots, so only 5 automated
        # snapshots survived — and a hand-made name that sorted lower would have
        # been deleted outright.
        import backup
        cfg = self.agent.cfg
        d = backup.backups_dir(cfg)
        d.mkdir(parents=True, exist_ok=True)
        hand_made = ["ingest-pre-review-fix-20260713T104530Z.db.gz",
                     "ingest-pre-review-lifecycle-cleanup-20260713T112217Z.db.gz"]
        for name in hand_made:
            (d / name).write_bytes(b"operator's")
        ours = [f"ingest-2026072{i}T000000Z.db.gz" for i in range(1, 6)]
        for name in ours:
            (d / name).write_bytes(b"x")
        cfg.backup_keep = 3

        removed = backup.rotate(cfg)

        left = sorted(p.name for p in d.iterdir())
        self.assertEqual(removed, 2)                       # 5 ours -> keep 3
        # every hand-made copy survives...
        for name in hand_made:
            self.assertIn(name, left)
        # ...and retention kept a FULL backup_keep of our own, not 3 minus theirs
        self.assertEqual([n for n in left if n.startswith("ingest-2026")],
                         sorted(ours[2:]))

    def test_sweep_removes_a_half_written_encrypted_archive(self):
        # Scope regression from 2eefa19: the pre-fix sweep globbed *.tmp, which
        # covered encrypt_snapshot's ingest-<stamp>.db.gz.enc.tmp. The narrowed
        # pattern stopped matching it, so a crash between encrypt and rename
        # leaked it forever (rotate never sees a .tmp).
        import backup
        cfg = self.agent.cfg
        d = backup.backups_dir(cfg)
        d.mkdir(parents=True, exist_ok=True)
        (d / "ingest-20200101T000000Z.db.gz.enc.tmp").write_bytes(b"half encrypted")
        (d / "ingest-20200101T000000Z.db.gz.tmp").write_bytes(b"half gzipped")
        (d / "ingest-20200101T000000Z.db").write_bytes(b"raw leak")
        (d / "ingest-pre-july15-corrections-20260715T153357Z.db").write_bytes(b"keep")

        self.assertEqual(backup.sweep_stray(cfg), 3)
        self.assertEqual([p.name for p in d.iterdir()],
                         ["ingest-pre-july15-corrections-20260715T153357Z.db"])

    def test_retention_runs_even_when_the_snapshot_itself_fails(self):
        # The nearly-full-disk case this module exists to break: snapshot() is
        # what raises, and rotation used to be skipped, so the disk stayed full.
        import backup
        cfg, conn = self.agent.cfg, self.agent.conn
        d = backup.backups_dir(cfg)
        d.mkdir(parents=True, exist_ok=True)
        for i in range(1, 6):
            (d / f"ingest-2026072{i}T000000Z.db.gz").write_bytes(b"x")
        cfg.backup_keep = 2
        with mock.patch.object(backup, "snapshot",
                               side_effect=sqlite3.OperationalError(
                                   "database or disk is full")):
            with self.assertRaises(sqlite3.OperationalError):
                backup.run(cfg, conn)
        self.assertEqual(len([p for p in d.iterdir() if p.name.endswith(".db.gz")]), 2)

    # -- 2026-07-27 review: rotation vs the current run, error masking,
    #    openssl hangs, and the watchdog inside the job bodies -----------------

    def test_rotation_never_deletes_the_archive_this_run_just_wrote(self):
        # "Newest" is the NAME's UTC stamp and the wall clock is not monotonic:
        # a droplet booting with a skewed RTC (before NTP steps it) stamps
        # "today" in the past, so the just-written archive sorted below the keep
        # window — rotate() deleted the snapshot run() had just taken, and run()
        # then crashed on gz.stat() after a technically successful backup,
        # retrying into the same delete until the job went terminal.
        import backup
        cfg, conn = self.agent.cfg, self.agent.conn
        cfg.backup_keep = 2
        d = backup.backups_dir(cfg)
        d.mkdir(parents=True, exist_ok=True)
        for i in range(1, 4):                      # three archives "from the future"
            (d / f"ingest-2099010{i}T000000Z.db.gz").write_bytes(b"x")
        result = backup.run(cfg, conn)             # today's stamp sorts below them all
        self.assertTrue((d / result["file"]).is_file(), "today's archive must survive")
        self.assertEqual(result["bytes"], (d / result["file"]).stat().st_size)
        # ...and retention still pruned the set it was allowed to touch.
        self.assertEqual(len(list(d.glob("ingest-2099*.db.gz"))), cfg.backup_keep)

    def test_a_failed_snapshots_retention_sweep_never_masks_the_original_error(self):
        # A read-only remount fails snapshot() AND the recovery rotate(); the
        # job row and the boss's backup_failed notice quote what escapes run(),
        # so it must be the diagnosis, not the sweep's own OSError on top of it.
        import backup
        cfg, conn = self.agent.cfg, self.agent.conn
        original = sqlite3.OperationalError("attempt to write a readonly database")
        with mock.patch.object(backup, "snapshot", side_effect=original), \
                mock.patch.object(backup, "rotate",
                                  side_effect=OSError(30, "Read-only file system")):
            with self.assertRaises(sqlite3.OperationalError) as ctx:
                backup.run(cfg, conn)
        self.assertIs(ctx.exception, original)

    def test_openssl_runs_are_time_bounded_and_a_hang_is_a_backup_error(self):
        # Neither openssl invocation had a timeout=: openssl blocking on a
        # stalled volume or a key "file" that blocks on read hung the job body —
        # which emits no ping of its own — until WatchdogSec killed the live
        # assistant mid-backup. A hang is now a job failure with a retry.
        import subprocess as sp
        import backup
        cfg = self.agent.cfg
        key = Path(self.tmp.name) / "backup.key"
        key.write_bytes(b"k" * 32)
        cfg.backup_encryption_key_file = str(key)
        d = backup.backups_dir(cfg)
        d.mkdir(parents=True, exist_ok=True)
        gz = d / "ingest-20260101T000000Z.db.gz"
        gz.write_bytes(b"x")
        calls = []

        def hung_openssl(cmd, **kwargs):
            calls.append(dict(kwargs))
            raise sp.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout") or 0)

        with mock.patch.object(backup.subprocess, "run", side_effect=hung_openssl):
            with self.assertRaises(backup.BackupEncryptionError):
                backup.encrypt_snapshot(cfg, gz)
            with self.assertRaises(backup.BackupRestoreError):
                backup._decrypt_snapshot(cfg, Path(str(gz) + ".enc"),
                                         d / "restored.db.gz")
        # Per CALL, not one merged dict (2026-07-27): the decrypt call's kwargs
        # used to overwrite the encrypt call's, so dropping timeout= from
        # encrypt_snapshot ALONE kept this test green.
        self.assertEqual([c.get("timeout") for c in calls],
                         [backup.OPENSSL_TIMEOUT_SECONDS] * 2)

    def test_backup_job_bodies_ping_the_watchdog_between_phases(self):
        # runtime.drain pings BETWEEN jobs, so a job body with no model calls was
        # one un-pinged span in its entirety — the exact arithmetic the unit's
        # WatchdogSec=900 comment rests on did not hold for the two backup jobs.
        # PLACEMENT is asserted, not a count (2026-07-27): the per-chunk pings
        # inside snapshot()/_gunzip() made a bare `call_count >= 3` hold only by
        # accident of the fixture DB gzipping in one chunk — a larger DB would
        # have masked the removal of every phase ping in run()/verify_restore().
        import backup
        cfg, conn = self.agent.cfg, self.agent.conn
        cfg.fleet_notify_token, cfg.fleet_notify_chat_id = "t", "5"
        d = backup.backups_dir(cfg)
        d.mkdir(parents=True, exist_ok=True)
        gz = d / "ingest-20260101T000000Z.db.gz"
        gz.write_bytes(b"x")
        enc = Path(str(gz) + ".enc")
        seq = []

        def phase(name, result):
            def body(*_args, **_kwargs):
                seq.append(name)
                return result
            return body

        with mock.patch.object(backup.common, "watchdog_ping",
                               side_effect=lambda: seq.append("ping")), \
                mock.patch.object(backup, "snapshot",
                                  side_effect=phase("snapshot", gz)), \
                mock.patch.object(backup, "rotate", side_effect=phase("rotate", 0)), \
                mock.patch.object(backup, "encrypt_snapshot",
                                  side_effect=phase("encrypt", enc)), \
                mock.patch.object(backup, "offsite",
                                  side_effect=phase("offsite", "telegram:fleet")):
            backup.run(cfg, conn)
        # A ping between each adjacent phase pair (rotate is quick file ops and
        # deliberately unpinned — filtered out rather than pinned in place).
        self.assertEqual([e for e in seq if e != "rotate"],
                         ["ping", "snapshot", "ping", "encrypt", "ping", "offsite"])

        enc.write_bytes(b"e")               # newest stamp, encrypted form preferred
        seq.clear()

        def fake_decrypt(_cfg, _src, out_path):
            seq.append("decrypt")
            out_path.write_bytes(b"gz")

        def fake_gunzip(_src, dst, _max_bytes):
            seq.append("gunzip")
            dst.write_bytes(b"db")
            return 2

        def fake_integrity(_path):
            seq.append("integrity")
            return 12

        with mock.patch.object(backup.common, "watchdog_ping",
                               side_effect=lambda: seq.append("ping")), \
                mock.patch.object(backup, "_decrypt_snapshot",
                                  side_effect=fake_decrypt), \
                mock.patch.object(backup, "_gunzip", side_effect=fake_gunzip), \
                mock.patch.object(backup, "integrity_check",
                                  side_effect=fake_integrity):
            result = backup.verify_restore(cfg, conn)
        self.assertEqual(result["integrity"], "ok")
        self.assertEqual(seq, ["ping", "decrypt", "ping", "gunzip",
                               "ping", "integrity"])

    def test_sweep_spares_hand_made_backups(self):
        # Caught on the live box: the backups dir also holds deliberate
        # pre-change copies (ingest-pre-july15-corrections-<stamp>.db, 16 MB,
        # plus -wal/-shm companions). The first sweep matched `ingest-*.db` and
        # would have deleted that operator backup on the next rotation.
        import backup
        cfg = self.agent.cfg
        d = backup.backups_dir(cfg)
        d.mkdir(parents=True, exist_ok=True)
        keep = [
            "ingest-pre-july15-corrections-20260715T153357Z.db",
            "ingest-pre-july15-corrections-20260715T153357Z.db-wal",
            "ingest-pre-july15-corrections-20260715T153357Z.db-shm",
            "ingest-pre-review-fix-20260713T104530Z.db.gz",
            "notes.tmp",                       # not ours either
        ]
        for name in keep:
            (d / name).write_bytes(b"operator's, not ours")
        (d / "ingest-20200101T000000Z.db").write_bytes(b"our leaked raw snapshot")
        (d / "ingest-20200101T000000Z.db.gz.tmp").write_bytes(b"our half-written archive")

        self.assertEqual(backup.sweep_stray(cfg), 2)      # only our two
        left = sorted(p.name for p in d.iterdir())
        self.assertEqual(left, sorted(keep))

    # -- T1.2 retention runs even when encryption fails ------------------------

    def test_rotation_still_prunes_when_encryption_fails(self):
        # run() ordered snapshot -> encrypt -> rotate, so a raised
        # BackupEncryptionError (e.g. a missing key file) skipped retention
        # forever and snapshots accumulated unboundedly.
        import backup
        conn, cfg = self.agent.conn, self.agent.cfg
        cfg.backup_keep = 2
        cfg.fleet_notify_token, cfg.fleet_notify_chat_id = "t", "5"
        d = backup.backups_dir(cfg)
        d.mkdir(parents=True, exist_ok=True)
        for i in range(3):
            (d / f"ingest-2020010{i}T000000Z.db.gz").write_bytes(b"x")
        with mock.patch.object(backup, "encrypt_snapshot",
                               side_effect=backup.BackupEncryptionError("no key file")):
            with self.assertRaises(backup.BackupEncryptionError):
                backup.run(cfg, conn)
        self.assertEqual(len(list(d.glob("ingest-*.db.gz"))), cfg.backup_keep)

    # -- T1.3 an off-box copy blocked by size must be loud ---------------------

    @staticmethod
    def _fake_encrypt(cfg, gz_path, payload=b"ciphertext-payload"):
        enc = Path(str(gz_path) + ".enc")
        enc.write_bytes(payload)
        return enc

    def test_offbox_blocked_by_size_logs_an_issue_and_reports_it(self):
        import backup
        conn, cfg = self.agent.conn, self.agent.cfg
        cfg.fleet_notify_token, cfg.fleet_notify_chat_id = "t", "5"
        with mock.patch.object(backup, "encrypt_snapshot", side_effect=self._fake_encrypt), \
                mock.patch.object(backup, "TG_UPLOAD_LIMIT", 4), \
                mock.patch.object(backup, "tg_send_document") as send:
            result = backup.run(cfg, conn)
        self.assertFalse(send.called)                       # never even attempted
        self.assertTrue(result["offbox_blocked"])
        self.assertEqual(result["offsite"], backup.OFFBOX_BLOCKED)
        kinds = [r["kind"] for r in conn.execute("SELECT kind FROM issues")]
        self.assertEqual(kinds, ["backup_offbox_blocked"])

    def test_offbox_near_the_size_limit_warns_once(self):
        import backup
        conn, cfg = self.agent.conn, self.agent.cfg
        cfg.fleet_notify_token, cfg.fleet_notify_chat_id = "t", "5"
        d = backup.backups_dir(cfg)
        d.mkdir(parents=True, exist_ok=True)
        enc = d / "ingest-20260101T000000Z.db.gz.enc"
        enc.write_bytes(b"x" * 40)
        with mock.patch.object(backup, "TG_UPLOAD_WARN", 10), \
                mock.patch.object(backup, "TG_UPLOAD_LIMIT", 1000), \
                mock.patch.object(backup, "tg_send_document"):
            self.assertEqual(backup.offsite(cfg, enc, conn), "telegram:fleet")
            backup.offsite(cfg, enc, conn)                  # next day, still big
            self.assertEqual(store.kv_get(conn, "backup_size_warned"), "1")
            kinds = [r["kind"] for r in conn.execute("SELECT kind FROM issues")]
            self.assertEqual(kinds, ["backup_offbox_near_limit"])   # one row, not daily spam
            enc.write_bytes(b"x" * 5)                       # shrank back below the warn line
            backup.offsite(cfg, enc, conn)
        self.assertEqual(store.kv_get(conn, "backup_size_warned"), "0")

    # -- T1.4 proactive low-disk alert ----------------------------------------

    def _disk_tick(self, free_gb, total_gb=10):
        gb = 1024 ** 3
        self.agent.last_disk_check = 0           # force the interval gate open
        with mock.patch.object(sysinfo, "collect",
                               return_value={"disk_total": int(total_gb * gb),
                                             "disk_free": int(free_gb * gb)}), \
                mock.patch.object(self.agent, "reply",
                                  return_value={"message_id": 1}) as r:
            self.agent.check_disk_space()
        return r

    def test_disk_alert_fires_once_and_reports_recovery(self):
        conn = self.agent.conn
        self.assertFalse(self._disk_tick(5).called)         # 50% free -> quiet
        self.assertEqual(store.kv_get(conn, "disk_space"), "ok")
        r = self._disk_tick(0.5)                            # 5% free -> alert
        self.assertTrue(r.called)
        self.assertIn("5.0%", r.call_args[0][1])
        self.assertEqual(store.kv_get(conn, "disk_space"), "low")
        self.assertEqual([row["kind"] for row in conn.execute("SELECT kind FROM issues")],
                         ["disk_low"])
        self.assertFalse(self._disk_tick(0.4).called)       # still low -> no repeat
        self.assertFalse(self._disk_tick(1.1).called)       # 11% — inside the margin
        self.assertEqual(store.kv_get(conn, "disk_space"), "low")
        r = self._disk_tick(1.5)                            # 15% -> recovered, one notice
        self.assertTrue(r.called)
        self.assertIn("15.0%", r.call_args[0][1])
        self.assertEqual(store.kv_get(conn, "disk_space"), "ok")

    def test_disk_check_respects_the_interval_and_the_disable_knob(self):
        # Both gates are user-visible contracts, not just "the probe wasn't called":
        # nothing is said to the boss and the durable state is left alone.
        conn = self.agent.conn
        with mock.patch.object(sysinfo, "collect") as c, \
                mock.patch.object(self.agent, "reply") as r:
            self.agent.last_disk_check = time.time()
            self.agent.check_disk_space()
            self.assertFalse(c.called)                      # interval gate closed
            self.agent.last_disk_check = 0
            self.agent.cfg.disk_alert_min_free_pct = 0      # knob disables the monitor
            self.agent.check_disk_space()
            self.assertFalse(c.called)
            self.assertEqual(self.agent.last_disk_check, 0)  # disabled: not even stamped
            self.assertFalse(r.called)
        self.assertIsNone(store.kv_get(conn, "disk_space"))

    def test_disk_check_is_wired_into_the_scheduler_loop(self):
        # The disk tests call check_disk_space() directly, so dropping it from the
        # poll loop would kill the whole feature with the suite still green.
        import tg_ingest_agent
        self.assertIn("check_disk_space", tg_ingest_agent.Agent.SCHEDULER_TICKS)
        for name in tg_ingest_agent.Agent.SCHEDULER_TICKS:
            self.assertTrue(callable(getattr(self.agent, name, None)), name)

    def _fail_backup_job(self, error="disk full", finished_at=None):
        """Terminally fail the newest live db_backup job, the way jobs.fail() does
        (status + error + finished_at)."""
        conn = self.agent.conn
        jid = conn.execute(
            "SELECT id FROM jobs WHERE action = 'db_backup' AND status IN ('pending', 'claimed')"
            " ORDER BY id DESC LIMIT 1").fetchone()["id"]
        conn.execute("UPDATE jobs SET status = 'failed', error = ?, finished_at = ? WHERE id = ?",
                     (error, finished_at or datetime.now(timezone.utc).isoformat(), jid))
        conn.commit()
        return jid

    def test_terminal_backup_failure_alerts_the_boss_once(self):
        # A terminally failed backup left only an issues row nobody reads; the DB
        # is the one thing that cannot be recreated, so it has to be said out loud.
        conn = self.agent.conn
        jobs.add_job(conn, "maintenance", "db_backup")
        self._fail_backup_job("disk full")
        with mock.patch.object(self.agent, "reply", return_value={"message_id": 1}) as r:
            self.agent.check_scheduled_backup()
        self.assertTrue(r.called)
        self.assertIn("disk full", r.call_args[0][1])
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status = 'pending'").fetchone()[0], 0)
        with mock.patch.object(self.agent, "reply", return_value={"message_id": 2}) as r2:
            self.agent.check_scheduled_backup()             # same failed job -> silent
        self.assertFalse(r2.called)
        store.kv_set(conn, "backup_retry_at", "")           # the hold expires
        with mock.patch.object(self.agent, "reply", return_value={"message_id": 3}):
            self.agent.check_scheduled_backup()
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status = 'pending'").fetchone()[0], 1)

    def test_a_stale_failed_backup_is_not_announced_as_todays(self):
        # Failed job rows survive TELEMETRY_RETENTION_DAYS (90), so an unscoped
        # query would open with «сегодняшний бэкап не сделался» quoting a
        # three-week-old error — and park today's real backup behind an hour of
        # backoff for nothing.
        conn = self.agent.conn
        jobs.add_job(conn, "maintenance", "db_backup")
        self._fail_backup_job("ancient",
                              (datetime.now(timezone.utc) - timedelta(days=3)).isoformat())
        with mock.patch.object(self.agent, "reply", return_value={"message_id": 1}) as r:
            self.agent.check_scheduled_backup()
        self.assertFalse(r.called)
        self.assertIsNone(store.kv_get(conn, "backup_retry_at"))
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status = 'pending'").fetchone()[0], 1)

    def test_persistent_backup_failure_is_announced_once_a_day(self):
        # A permanent cause (missing key file) produces a NEW job — and a new id —
        # every BACKUP_RETRY_MINUTES, so id-keyed dedup meant ~20 identical alerts
        # a day, each preceded by a full snapshot+gzip of the DB.
        conn = self.agent.conn
        self.agent.check_scheduled_backup()                 # job A
        self._fail_backup_job("no key file")
        with mock.patch.object(self.agent, "reply", return_value={"message_id": 1}) as r:
            self.agent.check_scheduled_backup()
        self.assertTrue(r.called)
        store.kv_set(conn, "backup_retry_at", "")           # the hour passes
        with mock.patch.object(self.agent, "reply", return_value={"message_id": 2}) as r2:
            self.agent.check_scheduled_backup()             # job B enqueued
            self._fail_backup_job("no key file")            # ... and fails identically
            self.agent.check_scheduled_backup()
        self.assertFalse(r2.called)                         # new id, same day -> silent
        self.assertTrue(store.kv_get(conn, "backup_retry_at"))   # retry still held

    def test_undelivered_backup_notice_is_retried(self):
        # The announced-state stamp used to land BEFORE the send, so a Telegram
        # blip swallowed the only proactive notice for that failure permanently.
        conn = self.agent.conn
        self.agent.check_scheduled_backup()
        self._fail_backup_job("disk full")
        with mock.patch.object(self.agent, "reply", return_value=None) as r:
            self.agent.check_scheduled_backup()
        self.assertTrue(r.called)                           # attempted...
        self.assertIsNone(store.kv_get(conn, "backup_failed_day"))   # ...not announced
        store.kv_set(conn, "backup_notice_retry_at", "")    # the send backoff passes
        with mock.patch.object(self.agent, "reply", return_value={"message_id": 1}) as r2:
            self.agent.check_scheduled_backup()
        self.assertTrue(r2.called)
        self.assertIn("disk full", r2.call_args[0][1])
        self.assertEqual(store.kv_get(conn, "backup_failed_day"),
                         datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    # -- T1.5 retry backoff + backup_day stamped on success --------------------

    def test_job_retry_waits_instead_of_burning_both_attempts_at_once(self):
        conn = self.agent.conn
        jid = jobs.add_job(conn, "maintenance", "db_backup", max_attempts=2)
        job = jobs.claim_next(conn)
        self.assertFalse(jobs.fail(conn, job["id"], "network blip"))   # retry, not terminal
        row = conn.execute("SELECT status, available_at FROM jobs WHERE id = ?",
                           (jid,)).fetchone()
        self.assertEqual(row["status"], "pending")
        self.assertGreater(row["available_at"], datetime.now(timezone.utc).isoformat())
        self.assertIsNone(jobs.claim_next(conn))            # not re-claimed in this pass
        conn.execute("UPDATE jobs SET available_at = ? WHERE id = ?", (store._now(), jid))
        again = jobs.claim_next(conn)
        self.assertEqual(again["id"], jid)
        self.assertTrue(jobs.fail(conn, jid, "still down"))  # budget spent -> terminal

    def test_backup_day_is_stamped_only_after_a_successful_run(self):
        # The kv stamp used to happen at ENQUEUE time, so a failed backup was
        # never retried until the next UTC day.
        import backup
        conn = self.agent.conn
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.agent.check_scheduled_backup()
        self.assertIsNone(store.kv_get(conn, "backup_day"))
        with mock.patch.object(backup, "run", side_effect=RuntimeError("boom")):
            runtime.drain(conn, self.agent)
        self.assertIsNone(store.kv_get(conn, "backup_day"))
        self.assertEqual(runtime.drain(conn, self.agent), 0)   # backoff holds the retry
        conn.execute("UPDATE jobs SET available_at = ?", (store._now(),))
        conn.commit()
        with mock.patch.object(backup, "run", return_value={"file": "f"}) as ok:
            runtime.drain(conn, self.agent)
        self.assertTrue(ok.called)
        self.assertEqual(store.kv_get(conn, "backup_day"), today)
        self.agent.check_scheduled_backup()                 # interval not due
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status = 'pending'").fetchone()[0], 0)


class BackupRestoreSelfCheckTests(unittest.TestCase):
    """T14.1 of the 2026-07-24 review: nothing had ever proved a snapshot could be
    turned back INTO a database — the scheduled job was green once the file was written
    and sent. A monthly durable job now decrypts the newest snapshot with the on-box
    key, gunzips it and runs PRAGMA integrity_check on the result in a scratch dir
    that is always removed."""

    def setUp(self):
        import tg_ingest_agent
        self.tmp = tempfile.TemporaryDirectory()
        cfg = make_config(DB_PATH=str(Path(self.tmp.name) / "pd.db"),
                          MEDIA_DIR=str(Path(self.tmp.name) / "m"))
        self.agent = tg_ingest_agent.Agent(cfg)

    def tearDown(self):
        self.agent.conn.close()
        self.tmp.cleanup()

    def _key_file(self, passphrase="test-passphrase"):
        key = Path(self.tmp.name) / "backup.key"
        key.write_text(passphrase, encoding="utf-8")
        self.agent.cfg.backup_encryption_key_file = key
        return key

    def _archive(self, name, payload):
        """Put a gzip archive with arbitrary contents under our own stamp name."""
        import gzip as gzipmod
        import backup
        d = backup.backups_dir(self.agent.cfg)
        d.mkdir(parents=True, exist_ok=True)
        path = d / name
        path.write_bytes(gzipmod.compress(payload))
        return path

    def test_round_trip_on_a_real_snapshot_reports_ok(self):
        import backup
        cfg, conn = self.agent.cfg, self.agent.conn
        gz = backup.snapshot(cfg, conn)

        result = backup.verify_restore(cfg, conn)

        self.assertEqual(result["file"], gz.name)
        self.assertFalse(result["encrypted"])          # nothing configured off-box
        self.assertEqual(result["integrity"], "ok")
        self.assertGreater(result["tables"], 5)        # a real schema, not an empty file
        self.assertGreater(result["bytes"], 0)
        # No scratch left behind — the decrypted copy is plaintext database.
        self.assertFalse((backup.backups_dir(cfg) / backup.RESTORE_CHECK_DIR).exists())
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM issues").fetchone()[0], 0)

    @unittest.skipUnless(shutil.which("openssl"), "openssl not installed")
    def test_round_trip_through_real_openssl_prefers_the_encrypted_copy(self):
        # The encrypted file is the ONLY one that ever leaves the box, so it is the
        # one worth proving readable — and the decrypt must be the exact inverse of
        # encrypt_snapshot (same cipher, KDF, iteration count) or a real restore is
        # impossible with a green check.
        import backup
        cfg, conn = self.agent.cfg, self.agent.conn
        self._key_file()
        gz = backup.snapshot(cfg, conn)
        enc = backup.encrypt_snapshot(cfg, gz)

        result = backup.verify_restore(cfg, conn)

        self.assertEqual(result["file"], enc.name)
        self.assertTrue(result["encrypted"])
        self.assertEqual(result["integrity"], "ok")
        self.assertFalse((backup.backups_dir(cfg) / backup.RESTORE_CHECK_DIR).exists())

    @unittest.skipUnless(shutil.which("openssl"), "openssl not installed")
    def test_the_wrong_key_is_caught_instead_of_reported_green(self):
        import backup
        cfg, conn = self.agent.cfg, self.agent.conn
        self._key_file("the-real-passphrase")
        backup.encrypt_snapshot(cfg, backup.snapshot(cfg, conn))
        self._key_file("a-different-passphrase")      # the key the box would restore with

        with self.assertRaises(backup.BackupRestoreError):
            backup.verify_restore(cfg, conn)
        self.assertEqual([r["kind"] for r in conn.execute("SELECT kind FROM issues")],
                         ["backup_restore_failed"])
        self.assertFalse((backup.backups_dir(cfg) / backup.RESTORE_CHECK_DIR).exists())

    def test_a_valid_gzip_that_is_not_a_database_fails(self):
        # The check has to OPEN the result: gunzip succeeding proves only that the
        # archive is intact, not that what came out is a database.
        import backup
        conn, cfg = self.agent.conn, self.agent.cfg
        self._archive("ingest-20260101T000000Z.db.gz", b"this is not a database")

        with self.assertRaises(backup.BackupRestoreError) as caught:
            backup.verify_restore(cfg, conn)
        self.assertIn("database", str(caught.exception))
        self.assertEqual([r["kind"] for r in conn.execute("SELECT kind FROM issues")],
                         ["backup_restore_failed"])
        self.assertFalse((backup.backups_dir(cfg) / backup.RESTORE_CHECK_DIR).exists())

    def test_a_valid_database_that_is_not_cara_s_is_not_accepted(self):
        # A perfectly healthy SQLite file passes integrity_check while containing
        # none of her data; a snapshot of Cara's DB always has `messages`.
        import backup
        conn, cfg = self.agent.conn, self.agent.cfg
        other = Path(self.tmp.name) / "other.db"
        stranger = sqlite3.connect(str(other))
        stranger.execute("CREATE TABLE something_else (a)")
        stranger.commit()
        stranger.close()
        self._archive("ingest-20260101T000000Z.db.gz", other.read_bytes())

        with self.assertRaises(backup.BackupRestoreError) as caught:
            backup.verify_restore(cfg, conn)
        self.assertIn("messages", str(caught.exception))

    def test_a_corrupt_archive_fails_loudly(self):
        import backup
        conn, cfg = self.agent.conn, self.agent.cfg
        d = backup.backups_dir(cfg)
        d.mkdir(parents=True, exist_ok=True)
        (d / "ingest-20260101T000000Z.db.gz").write_bytes(b"\x1f\x8b truncated garbage")

        with self.assertRaises(backup.BackupRestoreError):
            backup.verify_restore(cfg, conn)
        self.assertFalse((d / backup.RESTORE_CHECK_DIR).exists())

    def test_no_snapshot_at_all_is_a_failure_not_a_silent_pass(self):
        import backup
        conn, cfg = self.agent.conn, self.agent.cfg
        with self.assertRaises(backup.BackupRestoreError) as caught:
            backup.verify_restore(cfg, conn)
        self.assertIn("no snapshot", str(caught.exception))

    def test_a_missing_key_file_is_named(self):
        import backup
        conn, cfg = self.agent.conn, self.agent.cfg
        d = backup.backups_dir(cfg)
        d.mkdir(parents=True, exist_ok=True)
        (d / "ingest-20260101T000000Z.db.gz.enc").write_bytes(b"ciphertext")
        cfg.backup_encryption_key_file = Path(self.tmp.name) / "absent.key"

        with self.assertRaises(backup.BackupRestoreError) as caught:
            backup.verify_restore(cfg, conn)
        self.assertIn("key is missing", str(caught.exception))

    def test_low_free_disk_refuses_before_writing_anything(self):
        # This check copies AND expands the database next to the backups. On the
        # nearly-full disk WP1 exists to survive, running it would be the thing that
        # finishes the job — so it refuses instead, loudly.
        import backup
        conn, cfg = self.agent.conn, self.agent.cfg
        backup.snapshot(cfg, conn)
        usage = mock.Mock(free=1024)
        with mock.patch.object(backup.shutil, "disk_usage", return_value=usage):
            with self.assertRaises(backup.BackupRestoreError) as caught:
                backup.verify_restore(cfg, conn)
        self.assertIn("free disk", str(caught.exception))
        self.assertFalse((backup.backups_dir(cfg) / backup.RESTORE_CHECK_DIR).exists())

    def test_the_real_reason_survives_a_failed_issue_write(self):
        # The likeliest cause of a failed check is a full disk — which is also what
        # stops the issue row being written. The diagnosis must not be replaced by
        # 'database or disk is full' from the bookkeeping.
        import backup
        conn, cfg = self.agent.conn, self.agent.cfg
        with mock.patch.object(backup.store, "issue_add",
                               side_effect=sqlite3.OperationalError(
                                   "database or disk is full")):
            with self.assertRaises(backup.BackupRestoreError) as caught:
                backup.verify_restore(cfg, conn)
        self.assertIn("no snapshot", str(caught.exception))

    def test_scratch_from_a_killed_run_is_swept_by_the_daily_rotation(self):
        # The scratch dir holds a DECRYPTED database. A process killed mid-check
        # would otherwise leave it sitting in the backups dir until next month.
        import backup
        cfg = self.agent.cfg
        d = backup.backups_dir(cfg)
        scratch = d / backup.RESTORE_CHECK_DIR
        scratch.mkdir(parents=True)
        (scratch / "restored.db").write_bytes(b"plaintext copy of everything")
        (d / "keep-me").mkdir()                        # not ours -> untouched

        self.assertEqual(backup.sweep_stray(cfg), 1)
        self.assertFalse(scratch.exists())
        self.assertTrue((d / "keep-me").is_dir())

    # -- WHICH snapshot gets verified -------------------------------------------

    def test_the_newest_of_several_archives_is_the_one_verified(self):
        # `sorted(...)[-1]` regressing to `[0]` would re-verify the OLDEST archive
        # every month and stay green forever. Every other test here seeds exactly
        # one file, so nothing pinned "newest".
        import backup
        cfg, conn = self.agent.cfg, self.agent.conn
        # January: a REAL snapshot of Cara's DB under an older stamp — verifying
        # THIS one would come back green.
        real = backup.snapshot(cfg, conn)
        real.rename(real.parent / "ingest-20260101T000000Z.db.gz")
        # July: healthy SQLite, but not hers — so the failure names which was opened.
        other = Path(self.tmp.name) / "other.db"
        stranger = sqlite3.connect(str(other))
        stranger.execute("CREATE TABLE something_else (a)")
        stranger.commit()
        stranger.close()
        newest = self._archive("ingest-20260701T000000Z.db.gz", other.read_bytes())

        self.assertEqual(backup.latest_snapshot(cfg), (newest, False))
        with self.assertRaises(backup.BackupRestoreError) as caught:
            backup.verify_restore(cfg, conn)
        self.assertIn("messages", str(caught.exception))   # it opened JULY, not January

    def test_a_stale_encrypted_copy_never_outranks_a_newer_archive(self):
        # `run()` never deletes an .enc after upload and `rotate()` prunes one only
        # together with its .gz — so if off-box config goes away (fleet token
        # cleared, backend switched) up to BACKUP_KEEP stale .enc files outlive it.
        # Preferring ANY .enc over ANY .gz meant the monthly check could report
        # `integrity: ok` for a months-old archive while today's snapshot, the one
        # a real restore would use, was never opened.
        import backup
        cfg, conn = self.agent.cfg, self.agent.conn
        d = backup.backups_dir(cfg)
        d.mkdir(parents=True, exist_ok=True)
        (d / "ingest-20260101T000000Z.db.gz.enc").write_bytes(b"stale ciphertext")
        fresh = backup.snapshot(cfg, conn)                 # today, unencrypted

        self.assertEqual(backup.latest_snapshot(cfg), (fresh, False))
        result = backup.verify_restore(cfg, conn)
        self.assertEqual(result["file"], fresh.name)
        self.assertFalse(result["encrypted"])
        self.assertEqual(result["integrity"], "ok")

    def test_the_encrypted_copy_wins_for_the_stamp_it_belongs_to(self):
        # The rule, stated: newest STAMP, and within that stamp the encrypted form
        # — it is the copy that actually leaves the box.
        import backup
        cfg, conn = self.agent.cfg, self.agent.conn
        gz = backup.snapshot(cfg, conn)
        enc = Path(str(gz) + ".enc")
        enc.write_bytes(b"ciphertext of this very stamp")

        self.assertEqual(backup.latest_snapshot(cfg), (enc, True))

    # -- the write budget --------------------------------------------------------

    def test_the_gunzip_refuses_to_write_past_its_budget(self):
        # The guard that stands between a crafted/corrupt archive and the disk-full
        # spiral WP1 exists to break — and on a real disk `max_bytes` is always
        # large, so nothing reached this branch.
        import backup
        src = self._archive("ingest-20260101T000000Z.db.gz", b"x" * 4096)
        dst = Path(self.tmp.name) / "expanded.db"

        with self.assertRaises(backup.BackupRestoreError) as caught:
            backup._gunzip(src, dst, max_bytes=10)
        self.assertIn("budget", str(caught.exception))

    def test_the_expansion_cap_is_a_budget_not_the_whole_free_disk(self):
        # The cap handed to _gunzip used to be `free - 32 MB`. With 64 GB free that
        # is ~8000x the budget the precheck had just demanded, so the guard could
        # not fire until the expansion had ALREADY taken the filesystem down to the
        # margin — i.e. only after causing the disk-full spiral it exists to
        # prevent, on a 4 GB box shared with a sibling bot.
        import backup
        cfg, conn = self.agent.cfg, self.agent.conn
        backup.snapshot(cfg, conn)
        seen = {}
        real_gunzip = backup._gunzip

        def spy(src, dst, max_bytes):
            seen["cap"] = max_bytes
            return real_gunzip(src, dst, max_bytes)

        huge = mock.Mock(free=64 * 1024 ** 3)
        with mock.patch.object(backup.shutil, "disk_usage", return_value=huge):
            with mock.patch.object(backup, "_gunzip", side_effect=spy):
                self.assertEqual(backup.verify_restore(cfg, conn)["integrity"], "ok")
        self.assertLess(seen["cap"], 64 * 1024 ** 3 // 1000)

    def test_an_archive_expanding_past_the_budget_is_refused_with_disk_to_spare(self):
        # The refusal itself, end to end, on a disk with gigabytes free: the budget
        # is what a restore can LEGITIMATELY produce (the live DB is a few hundred
        # KB here, so the floor applies), not what the filesystem could absorb.
        import backup
        cfg, conn = self.agent.cfg, self.agent.conn
        bomb_bytes = backup.RESTORE_MIN_BUDGET + (4 << 20)
        self._archive("ingest-20260101T000000Z.db.gz", b"\0" * bomb_bytes)

        with self.assertRaises(backup.BackupRestoreError) as caught:
            backup.verify_restore(cfg, conn)
        self.assertIn("budget", str(caught.exception))
        self.assertEqual([r["kind"] for r in conn.execute("SELECT kind FROM issues")],
                         ["backup_restore_failed"])
        self.assertFalse((backup.backups_dir(cfg) / backup.RESTORE_CHECK_DIR).exists())

    def test_the_budget_is_never_tighter_than_a_real_restore(self):
        # The mirror hazard, and the one that bites in PRODUCTION rather than in a
        # bomb: a nearly-empty SQLite file compresses far better than 12x, so a cap
        # of "12x the archive" refuses a perfectly good restore every month. The
        # bound is the LIVE database (a snapshot is a page-for-page copy of it and
        # nothing here VACUUMs, so the file never shrinks).
        import backup
        cfg, conn = self.agent.cfg, self.agent.conn
        gz = backup.snapshot(cfg, conn)
        budget = backup.restore_budget(cfg, gz.stat().st_size)

        result = backup.verify_restore(cfg, conn)
        self.assertEqual(result["integrity"], "ok")
        # This very archive expands past 12x — a 12x-only cap would have refused it.
        self.assertGreater(result["bytes"],
                           gz.stat().st_size * backup.RESTORE_FREE_FACTOR)
        self.assertGreaterEqual(budget, result["bytes"])

    # -- what the scratch dir leaves on disk, and what a failure reports ----------

    def test_the_scratch_copies_are_never_readable_beyond_the_service_user(self):
        # For the length of the check the scratch dir holds a full PLAINTEXT copy of
        # everything Cara is. The chmods were asserted by nothing: dropping them (or
        # letting _gunzip use plain open() under the service umask) would leave a
        # group/world-readable copy and every existing test would still pass.
        import stat as statmod
        import backup
        cfg, conn = self.agent.cfg, self.agent.conn
        backup.snapshot(cfg, conn)
        seen = {}
        real_check = backup.integrity_check

        def spy(db_path):
            scratch = Path(db_path).parent
            seen["dir"] = statmod.S_IMODE(scratch.stat().st_mode)
            seen["gz"] = statmod.S_IMODE((scratch / "restored.db.gz").stat().st_mode)
            seen["db"] = statmod.S_IMODE(Path(db_path).stat().st_mode)
            return real_check(db_path)

        with mock.patch.object(backup, "integrity_check", side_effect=spy):
            backup.verify_restore(cfg, conn)
        self.assertEqual(seen, {"dir": 0o700, "gz": 0o600, "db": 0o600})

    def test_an_out_of_disk_copy_still_leaves_the_documented_signal(self):
        # ENOSPC while copying the archive into the scratch dir is the likeliest
        # real failure of this job — and it is an OSError, not our own type. The
        # handler caught BackupRestoreError alone, so that case propagated with NO
        # `FAILED` log line and NO `backup_restore_failed` issue, leaving only
        # runtime.drain's generic job_failed once the retries ran out.
        import backup
        cfg, conn = self.agent.cfg, self.agent.conn
        backup.snapshot(cfg, conn)

        with mock.patch.object(backup.shutil, "copyfile",
                               side_effect=OSError(28, "No space left on device")):
            with self.assertRaises(OSError):
                backup.verify_restore(cfg, conn)
        self.assertEqual([r["kind"] for r in conn.execute("SELECT kind FROM issues")],
                         ["backup_restore_failed"])
        self.assertIn("No space left", conn.execute(
            "SELECT detail FROM issues").fetchone()[0])
        self.assertFalse((backup.backups_dir(cfg) / backup.RESTORE_CHECK_DIR).exists())

    # -- the halves have to agree, and so does the documented one-liner ----------

    def test_decrypt_is_the_exact_inverse_of_encrypt(self):
        # The only UNSKIPPABLE proof of T14.1's central claim: both round-trip tests
        # need the openssl CLI, so on a host without it the suite went green with
        # nothing comparing the two commands.
        import backup
        cfg = self.agent.cfg
        key = self._key_file()
        gz = self._archive("ingest-20260101T000000Z.db.gz", b"payload")
        calls = []
        with mock.patch.object(backup.subprocess, "run",
                               side_effect=lambda argv, **kw: calls.append(argv)):
            backup.encrypt_snapshot(cfg, gz)
            backup._decrypt_snapshot(cfg, Path(str(gz) + ".enc"),
                                     Path(self.tmp.name) / "out.gz")
        enc_argv, dec_argv = calls

        def without_paths(argv):
            out, i = [], 0
            while i < len(argv):
                if argv[i] in ("-in", "-out"):
                    i += 2
                    continue
                out.append(argv[i])
                i += 1
            return out

        for argv in (enc_argv, dec_argv):
            self.assertEqual(argv[argv.index("-iter") + 1], str(backup.PBKDF2_ITERATIONS))
            self.assertEqual(argv[argv.index("-pass") + 1], f"file:{key}")
        # Identical apart from the direction (-d) and the encrypt-only -salt.
        self.assertEqual([a for a in without_paths(dec_argv) if a != "-d"],
                         [a for a in without_paths(enc_argv) if a != "-salt"])
        self.assertIn("-aes-256-cbc", without_paths(dec_argv))

    def test_the_documented_restore_one_liner_matches_the_code(self):
        # SOLUTION.md §9's command is the single named deliverable of T14.1 and the
        # thing that gets typed at 3 a.m. when the box is gone — and nothing pinned
        # it. Both round-trip tests read PBKDF2_ITERATIONS on the encrypt AND the
        # decrypt side, so raising it to 300_000 (or switching cipher) leaves the
        # whole suite green while the documented command fails with 'bad decrypt'.
        # checkout-only for the same reason as OpsArtifactHardening20260726Tests:
        # docs are not in deploy.sh's FILES and the stage dir holds stale copies.
        import backup
        repo = Path(__file__).resolve().parent
        if not (repo / ".git").exists():
            self.skipTest("not a checkout: SOLUTION.md may be a stale stage-dir copy")
        blocks = [b for b in (repo / "SOLUTION.md").read_text(encoding="utf-8").split("```")
                  if "openssl enc -d" in b]
        self.assertEqual(len(blocks), 1, "exactly one documented restore block")
        block = blocks[0]

        self.assertIn(f"-iter {backup.PBKDF2_ITERATIONS}", block)
        self.assertIn("-aes-256-cbc", block)
        self.assertIn("-pbkdf2", block)
        self.assertIn(f"-pass file:{make_config().backup_encryption_key_file}", block)
        # …and it must not leave a world-readable plaintext database behind, the way
        # a bare `-out /tmp/restore.db` under the operator's 0022 umask did — while
        # the code path deliberately uses 0700/0600 for exactly these files.
        self.assertIn("umask 077", block)
        self.assertIn("rm -rf", block)
        self.assertNotIn("/tmp/restore.db", block)

    # -- the monthly job wiring -------------------------------------------------

    def test_verify_job_runs_once_a_month_and_stamps_only_on_success(self):
        import backup
        conn = self.agent.conn
        self.agent.check_backup_verify()
        self.agent.check_backup_verify()               # idempotent while pending
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE action = 'backup_verify'").fetchone()[0], 1)
        self.assertIn(("maintenance", "backup_verify"), runtime._HANDLERS)

        with mock.patch.object(backup, "verify_restore",
                               side_effect=backup.BackupRestoreError("no snapshot")):
            runtime.drain(conn, self.agent)
        self.assertIsNone(store.kv_get(conn, "backup_verify_month"))   # not done
        self.assertTrue(store.kv_get(conn, "backup_verify_retry_at"))  # but held off
        self.agent.check_backup_verify()
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE action = 'backup_verify'").fetchone()[0], 1)

        store.kv_set(conn, "backup_verify_retry_at", "")               # a day passes
        conn.execute("UPDATE jobs SET available_at = ?", (store._now(),))
        conn.commit()
        with mock.patch.object(backup, "verify_restore",
                               return_value={"integrity": "ok"}) as ok:
            runtime.drain(conn, self.agent)
        self.assertTrue(ok.called)
        # Read the clock AFTER the drain: the stamp is written inside the job body
        # from its own `now`, so capturing the month up front made a run that
        # straddled a month rollover fail for no code reason.
        self.assertEqual(store.kv_get(conn, "backup_verify_month"),
                         datetime.now(timezone.utc).strftime("%Y-%m"))
        self.agent.check_backup_verify()               # done for this month
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE action = 'backup_verify'").fetchone()[0], 1)

    def test_a_previous_month_re_enqueues_the_check(self):
        # The stamp has to ROLL. A wrong format ("%Y"), or one written from the
        # wrong clock, would wedge the monthly check forever while every other
        # assertion stayed green — the check would simply never run again.
        conn = self.agent.conn
        store.kv_set(conn, "backup_verify_month",
                     datetime.now(timezone.utc).strftime("%Y-%m"))
        self.agent.check_backup_verify()
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE action = 'backup_verify'").fetchone()[0], 0)

        last_month = (datetime.now(timezone.utc).replace(day=1)
                      - timedelta(days=1)).strftime("%Y-%m")
        store.kv_set(conn, "backup_verify_month", last_month)
        store.kv_set(conn, "backup_verify_retry_at", "")
        self.agent.check_backup_verify()
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE action = 'backup_verify'").fetchone()[0], 1)

    def test_verify_tick_is_wired_into_the_loop_and_honours_the_backup_switch(self):
        import tg_ingest_agent
        self.assertIn("check_backup_verify", tg_ingest_agent.Agent.SCHEDULER_TICKS)
        self.assertIn(("maintenance", "backup_verify"), jobs.JOB_KINDS)
        self.agent.cfg.backup_enabled = False
        self.agent.check_backup_verify()
        self.assertEqual(self.agent.conn.execute(
            "SELECT COUNT(*) FROM jobs").fetchone()[0], 0)


