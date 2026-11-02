"""Offline regressions for optional key confirmation and SQLite deletion policy."""
import contextlib
import gzip
import hashlib
import io
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import backup
import store
import tg_ingest_agent
from testlib import make_config


class KeyConfirmationConfigTests(unittest.TestCase):
    def test_unset_confirmation_preserves_the_default(self):
        self.assertEqual(make_config().backup_key_escrow_sha256, "")

    def test_operator_fingerprint_is_normalized(self):
        fingerprint = hashlib.sha256(b"synthetic key").hexdigest()
        cfg = make_config(BACKUP_KEY_ESCROW_SHA256="  " + fingerprint.upper() + "  ")
        self.assertEqual(cfg.backup_key_escrow_sha256, fingerprint)

    def test_invalid_fingerprints_are_rejected_without_echoing_values(self):
        for value in ("invalid-private-value", "a" * 63, "b" * 65,
                      "g" * 64, "sha256:" + "c" * 64):
            with self.subTest(value_length=len(value)):
                with self.assertRaises(ValueError) as caught:
                    make_config(BACKUP_KEY_ESCROW_SHA256=value)
                self.assertIn("BACKUP_KEY_ESCROW_SHA256", str(caught.exception))
                self.assertNotIn(value, str(caught.exception))


class KeyConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.key = self.root / "private-key-name"
        self.key_bytes = b"synthetic-passphrase\n"
        self.key.write_bytes(self.key_bytes)
        self.fingerprint = hashlib.sha256(self.key_bytes).hexdigest()
        self.cfg = make_config(
            DB_PATH=str(self.root / "fixture.db"),
            BACKUP_ENCRYPTION_KEY_FILE=str(self.key),
            BACKUP_KEY_ESCROW_SHA256=self.fingerprint)
        self.conn = store.open_db(self.cfg.db_path)
        self.addCleanup(self.conn.close)

    def assert_confirmation(self, result, status):
        self.assertEqual(result, {
            "status": status,
            "custody_verified": False,
            "offbox_provenance_verified": False,
        })

    def issues(self):
        return self.conn.execute("SELECT kind,detail FROM issues ORDER BY id").fetchall()

    def assert_safe_issue(self, detail):
        self.assertIn("operator-confirmed", detail)
        self.assertIn("unverified", detail)
        for secret in (self.fingerprint, self.key_bytes.decode().strip(),
                       str(self.key), self.key.name):
            self.assertNotIn(secret, detail)
        self.assertLessEqual(len(detail), 300)

    def test_unconfigured_and_legacy_configs_never_open_the_key(self):
        self.cfg.backup_key_escrow_sha256 = ""
        with mock.patch.object(backup.os, "open") as opened:
            self.assertIsNone(backup.verify_key_confirmation(self.cfg, self.conn))
            del self.cfg.backup_key_escrow_sha256
            self.assertIsNone(backup.verify_key_confirmation(self.cfg, self.conn))
        opened.assert_not_called()
        self.assertEqual(self.issues(), [])

    def test_matching_raw_key_bytes_confirm_only_the_fingerprint(self):
        result = backup.verify_key_confirmation(self.cfg, self.conn)
        self.assert_confirmation(result, "operator-confirmation-match")
        self.assertEqual(self.issues(), [])

    def test_newline_is_part_of_the_operator_confirmed_file(self):
        self.key.write_bytes(self.key_bytes.rstrip(b"\n"))
        result = backup.verify_key_confirmation(self.cfg, self.conn)
        self.assert_confirmation(result, "operator-confirmation-mismatch")
        rows = self.issues()
        self.assertEqual([row["kind"] for row in rows], ["backup_key_not_escrowed"])
        self.assert_safe_issue(rows[0]["detail"])

    def test_missing_key_is_an_observation_and_does_not_leak_the_path(self):
        self.key.unlink()
        result = backup.verify_key_confirmation(self.cfg, self.conn)
        self.assert_confirmation(result, "unreadable")
        self.assert_safe_issue(self.issues()[0]["detail"])

    def test_nonregular_and_oversized_keys_are_unreadable(self):
        self.key.unlink()
        self.key.mkdir()
        self.assert_confirmation(backup.verify_key_confirmation(self.cfg), "unreadable")
        self.key.rmdir()
        self.key.write_bytes(b"x" * (backup.BACKUP_KEY_MAX_BYTES + 1))
        self.assert_confirmation(backup.verify_key_confirmation(self.cfg), "unreadable")

    @unittest.skipUnless(os.name == "posix", "POSIX no-follow file boundary")
    def test_symlink_cannot_confirm_a_different_key_path(self):
        target = self.root / "key-target"
        self.key.rename(target)
        self.key.symlink_to(target)
        self.assert_confirmation(backup.verify_key_confirmation(self.cfg), "unreadable")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX nonblocking FIFO boundary")
    def test_fifo_is_rejected_without_waiting_for_a_writer(self):
        self.key.unlink()
        os.mkfifo(self.key)
        # A bounded child also makes a future loss of O_NONBLOCK fail safely.
        script = (
            "import sys; from pathlib import Path; from types import SimpleNamespace; "
            "sys.path.insert(0,sys.argv[1]); import backup; "
            "cfg=SimpleNamespace(backup_encryption_key_file=Path(sys.argv[2]),"
            "backup_key_escrow_sha256=sys.argv[3]); "
            "print(backup.verify_key_confirmation(cfg)['status'])")
        checked = subprocess.run(
            [sys.executable, "-I", "-B", "-c", script,
             str(Path(backup.__file__).resolve().parent), str(self.key), self.fingerprint],
            capture_output=True, text=True, timeout=3, check=True)
        self.assertEqual(checked.stdout.strip(), "unreadable")

    def test_key_read_errors_and_issue_write_errors_cannot_become_restore_failures(self):
        output = io.StringIO()
        with mock.patch.object(backup.os, "open",
                               side_effect=PermissionError("private-key-error-detail")), \
                contextlib.redirect_stdout(output):
            result = backup.verify_key_confirmation(self.cfg, self.conn)
        self.assert_confirmation(result, "unreadable")
        self.assert_safe_issue(self.issues()[0]["detail"])
        self.assertNotIn("private-key-error-detail", output.getvalue())
        self.cfg.backup_key_escrow_sha256 = "0" * 64
        with mock.patch.object(store, "issue_add",
                               side_effect=sqlite3.OperationalError("private-db-error")), \
                contextlib.redirect_stdout(output):
            result = backup.verify_key_confirmation(self.cfg, self.conn)
        self.assert_confirmation(result, "operator-confirmation-mismatch")
        self.assertNotIn("private-db-error", output.getvalue())

    def test_event_buffer_defers_the_issue_until_owner_settlement(self):
        self.cfg.backup_key_escrow_sha256 = "0" * 64
        events = {"issues": [], "kv": []}
        result = backup.verify_key_confirmation(self.cfg, events=events)
        self.assert_confirmation(result, "operator-confirmation-mismatch")
        self.assertEqual(self.issues(), [])
        self.assertEqual(len(events["issues"]), 1)
        self.assertEqual(events["issues"][0][0], "backup_key_not_escrowed")
        self.assert_safe_issue(events["issues"][0][1])
        backup._apply_background_events(self.conn, events)
        self.assertEqual(len(self.issues()), 1)

    def snapshot(self):
        return backup.snapshot(self.cfg, self.conn)

    def test_unconfigured_restore_keeps_the_existing_result_contract(self):
        self.snapshot()
        self.cfg.backup_key_escrow_sha256 = ""
        result = backup.verify_restore(self.cfg, self.conn)
        self.assertEqual(set(result), {"file", "encrypted", "bytes", "tables", "integrity"})
        self.assertEqual(result["integrity"], "ok")
        self.assertEqual(self.issues(), [])

    def test_matching_restore_reports_confirmation_without_custody_claims(self):
        self.snapshot()
        result = backup.verify_restore(self.cfg, self.conn)
        self.assertEqual(result["integrity"], "ok")
        self.assert_confirmation(result["key_confirmation"], "operator-confirmation-match")
        self.assertEqual(self.issues(), [])

    def test_mismatch_and_unreadable_key_leave_valid_local_restore_successful(self):
        self.snapshot()
        self.cfg.backup_key_escrow_sha256 = "0" * 64
        mismatch = backup.verify_restore(self.cfg, self.conn)
        self.assertEqual(mismatch["integrity"], "ok")
        self.assert_confirmation(mismatch["key_confirmation"], "operator-confirmation-mismatch")
        self.key.unlink()
        unreadable = backup.verify_restore(self.cfg, self.conn)
        self.assertEqual(unreadable["integrity"], "ok")
        self.assert_confirmation(unreadable["key_confirmation"], "unreadable")
        self.assertEqual([row["kind"] for row in self.issues()],
                         ["backup_key_not_escrowed", "backup_key_not_escrowed"])
        self.assertFalse((backup.backups_dir(self.cfg) / backup.RESTORE_CHECK_DIR).exists())

    def test_corrupt_restore_preserves_its_original_failure_and_both_observations(self):
        archive = self.snapshot()
        archive.write_bytes(gzip.compress(b"synthetic invalid SQLite payload"))
        self.cfg.backup_key_escrow_sha256 = "0" * 64
        with self.assertRaises(backup.BackupRestoreError) as caught:
            backup.verify_restore(self.cfg, self.conn)
        self.assertIn("database", str(caught.exception))
        self.assertEqual([row["kind"] for row in self.issues()],
                         ["backup_key_not_escrowed", "backup_restore_failed"])
        self.assertFalse((backup.backups_dir(self.cfg) / backup.RESTORE_CHECK_DIR).exists())

    def test_valid_restore_still_stamps_the_existing_month_on_mismatch(self):
        self.snapshot()
        self.cfg.backup_key_escrow_sha256 = "0" * 64
        context = SimpleNamespace(cfg=self.cfg)
        result = tg_ingest_agent.Agent.run_backup_verify(context, self.conn)
        self.assertEqual(result["integrity"], "ok")
        self.assertEqual(store.kv_get(self.conn, "backup_verify_month"),
                         datetime.now(timezone.utc).strftime("%Y-%m"))
        self.assertEqual(store.kv_get(self.conn, "backup_verify_retry_at"), "")
        self.assertIsNone(store.kv_get(self.conn, "backup_day"))
        self.assertIsNone(store.kv_get(self.conn, "backup_offsite_day"))

    def pump(self, session):
        end = time.monotonic() + 3
        while not session.advance() and time.monotonic() < end:
            threading.Event().wait(.002)
        self.assertTrue(session.done, "synthetic background verification did not settle")

    def test_background_restore_settles_confirmation_issues_on_the_owner_thread(self):
        self.snapshot()
        self.cfg.backup_key_escrow_sha256 = "0" * 64
        owner = threading.get_ident()
        observations = []
        original = store.issue_add

        def record(conn, *args, **kwargs):
            observations.append(threading.get_ident())
            return original(conn, *args, **kwargs)

        with mock.patch.object(store, "issue_add", side_effect=record):
            session = backup.start_verify_background(self.cfg, self.conn)
            self.pump(session)
        self.assertIsNone(session.error)
        self.assertEqual(session.result["integrity"], "ok")
        self.assert_confirmation(session.result["key_confirmation"], "operator-confirmation-mismatch")
        self.assertEqual(observations, [owner])

    def test_background_failure_retains_both_issues_and_original_error_on_owner(self):
        archive = self.snapshot()
        archive.write_bytes(gzip.compress(b"synthetic invalid SQLite payload"))
        self.cfg.backup_key_escrow_sha256 = "0" * 64
        owner = threading.get_ident()
        observations = []
        original = store.issue_add

        def record(conn, *args, **kwargs):
            observations.append(threading.get_ident())
            return original(conn, *args, **kwargs)

        with mock.patch.object(store, "issue_add", side_effect=record):
            session = backup.start_verify_background(self.cfg, self.conn)
            self.pump(session)
        self.assertIsInstance(session.error, backup.BackupRestoreError)
        self.assertEqual(observations, [owner, owner])
        self.assertEqual([row["kind"] for row in self.issues()],
                         ["backup_key_not_escrowed", "backup_restore_failed"])


    def test_background_confirmation_issue_write_failure_keeps_monthly_success_stamps(self):
        self.snapshot()
        self.cfg.backup_key_escrow_sha256 = "0" * 64
        context = SimpleNamespace(
            cfg=self.cfg, background_maintenance=True,
            BACKUP_VERIFY_RETRY_HOURS=tg_ingest_agent.Agent.BACKUP_VERIFY_RETRY_HOURS)
        fixed = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
        owner = threading.get_ident()
        observations = []
        original = store.issue_add

        def record(conn, chat_id, kind, detail="", **kwargs):
            observations.append((kind, threading.get_ident()))
            if kind == "backup_key_not_escrowed":
                raise sqlite3.OperationalError("synthetic confirmation issue write failure")
            return original(conn, chat_id, kind, detail, **kwargs)

        with mock.patch.object(store, "issue_add", side_effect=record), \
                mock.patch.object(tg_ingest_agent, "datetime") as clock:
            clock.now.return_value = fixed
            session = tg_ingest_agent.Agent.run_backup_verify(context, self.conn)
            self.pump(session)
        self.assertIsNone(session.error)
        self.assertEqual(session.result["integrity"], "ok")
        self.assert_confirmation(session.result["key_confirmation"], "operator-confirmation-mismatch")
        self.assertEqual(observations, [("backup_key_not_escrowed", owner)])
        self.assertEqual(self.issues(), [])
        self.assertEqual(store.kv_get(self.conn, "backup_verify_month"), "2026-11")
        self.assertEqual(store.kv_get(self.conn, "backup_verify_retry_at"), "")
        self.assertIsNone(store.kv_get(self.conn, "backup_day"))
        self.assertIsNone(store.kv_get(self.conn, "backup_offsite_day"))

    def test_background_confirmation_issue_write_failure_keeps_restore_failure_and_retry_stamp(self):
        archive = self.snapshot()
        archive.write_bytes(gzip.compress(b"synthetic invalid SQLite payload"))
        self.cfg.backup_key_escrow_sha256 = "0" * 64
        context = SimpleNamespace(
            cfg=self.cfg, background_maintenance=True,
            BACKUP_VERIFY_RETRY_HOURS=tg_ingest_agent.Agent.BACKUP_VERIFY_RETRY_HOURS)
        fixed = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
        owner = threading.get_ident()
        observations = []
        original = store.issue_add

        def record(conn, chat_id, kind, detail="", **kwargs):
            observations.append((kind, threading.get_ident()))
            if kind == "backup_key_not_escrowed":
                raise sqlite3.OperationalError("synthetic confirmation issue write failure")
            return original(conn, chat_id, kind, detail, **kwargs)

        with mock.patch.object(store, "issue_add", side_effect=record), \
                mock.patch.object(tg_ingest_agent, "datetime") as clock:
            clock.now.return_value = fixed
            session = tg_ingest_agent.Agent.run_backup_verify(context, self.conn)
            self.pump(session)
        self.assertIsInstance(session.error, backup.BackupRestoreError)
        self.assertIn("database", str(session.error))
        self.assertIsNone(session.result)
        self.assertEqual(observations, [
            ("backup_key_not_escrowed", owner), ("backup_restore_failed", owner)])
        rows = self.issues()
        self.assertEqual([row["kind"] for row in rows], ["backup_restore_failed"])
        self.assertEqual(rows[0]["detail"], str(session.error)[:300])
        self.assertIsNone(store.kv_get(self.conn, "backup_verify_month"))
        expected_retry = fixed + timedelta(hours=context.BACKUP_VERIFY_RETRY_HOURS)
        self.assertEqual(store.kv_get(self.conn, "backup_verify_retry_at"), expected_retry.isoformat())
        self.assertFalse((backup.backups_dir(self.cfg) / backup.RESTORE_CHECK_DIR).exists())



class ExplicitSecureDeleteTests(unittest.TestCase):
    def test_open_db_enforces_secure_delete_even_when_connection_default_is_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = sqlite3.connect

            def default_off(*args, **kwargs):
                conn = original(*args, **kwargs)
                conn.execute("PRAGMA secure_delete=OFF")
                return conn

            with mock.patch.object(store.sqlite3, "connect", side_effect=default_off):
                conn = store.open_db(Path(tmp) / "fixture.db")
            try:
                self.assertEqual(conn.execute("PRAGMA secure_delete").fetchone()[0], 1)
            finally:
                conn.close()

    def test_reopen_and_retention_preserve_business_rows_and_failed_recovery_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.db"
            conn = store.open_db(path)
            store.convo_add(conn, 111, "user", "synthetic retained owner history")
            store.usage_add(conn, "test", "chat", "synthetic", cost_usd=.001)
            for update_id in (1, 2, 3):
                store.telegram_update_receive(conn, {"update_id": update_id}, 111)
            store.telegram_update_done(conn, 1)
            store.telegram_update_fail(conn, 2, "synthetic terminal recovery evidence", terminal=True)
            conn.execute("UPDATE telegram_updates SET received_at=?,updated_at=?",
                         ("2000-01-01T00:00:00+00:00", "2000-01-01T00:00:00+00:00"))
            conn.commit()
            retained = {
                table: [tuple(row) for row in conn.execute("SELECT * FROM " + table)]
                for table in ("conversation", "llm_usage")
            }
            failed = tuple(store.telegram_update_get(conn, 2))
            pending = tuple(store.telegram_update_get(conn, 3))
            conn.close()
            conn = store.open_db(path)
            try:
                self.assertEqual(conn.execute("PRAGMA secure_delete").fetchone()[0], 1)
                self.assertEqual(store.prune_telemetry(conn, "2001-01-01T00:00:00+00:00"), 1)
                self.assertIsNone(store.telegram_update_get(conn, 1))
                self.assertEqual(tuple(store.telegram_update_get(conn, 2)), failed)
                self.assertEqual(tuple(store.telegram_update_get(conn, 3)), pending)
                for table, rows in retained.items():
                    self.assertEqual([tuple(row) for row in conn.execute("SELECT * FROM " + table)], rows)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
