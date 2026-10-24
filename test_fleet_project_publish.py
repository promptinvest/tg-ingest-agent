"""Isolated Linux fixtures for the manual read-only evidence publisher."""
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest
from unittest.mock import patch

import fleet_project_adapters as adapters
import fleet_project_publish as publisher

NOW = "2026-10-24T12:00:00Z"
PROJECT = "project-alpha"
SOURCE = "source-alpha"
ARCHIVE = "ingest-20261024T110000Z.db.gz"
CANARY = "SYNTHETIC-PRIVATE-CANARY"
SCHEMA = """CREATE TABLE jobs (
 id INTEGER PRIMARY KEY, skill TEXT NOT NULL, action TEXT NOT NULL,
 status TEXT NOT NULL, created_at TEXT NOT NULL, finished_at TEXT,
 result TEXT, error TEXT, payload TEXT, chat_id INTEGER, claimed_at TEXT,
 available_at TEXT, attempts INTEGER DEFAULT 0, max_attempts INTEGER DEFAULT 2);
 CREATE INDEX idx_jobs_skill_status ON jobs(skill,status);
 CREATE TABLE private_data (content TEXT);
"""


def result(action="db_backup", offsite="telegram:fleet", archive=ARCHIVE):
    if action == "db_backup":
        return {"file": archive, "bytes": 12345, "offsite": offsite,
                "offbox_blocked": offsite == "blocked:size", "encrypted_file": archive + ".enc"}
    return {"file": archive + ".enc", "encrypted": True, "bytes": 67890, "tables": 23, "integrity": "ok"}


@unittest.skipUnless(os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() == 0, "protected operator fixtures require isolated root Linux")
class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="cara-publisher-test-", dir="/root")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.database = self.root / "jobs.sqlite3"
        self.output = self.root / "backups.json"
        self.config = {"schema": publisher.POLICY_SCHEMA, "database_path": str(self.database), "database_uid": os.geteuid(),
                       "output_path": str(self.output), "project_id": PROJECT, "source_id": SOURCE, "max_age_seconds": 86400}
        self.connection = sqlite3.connect(self.database)
        self.connection.executescript(SCHEMA)
        self.connection.execute("INSERT INTO private_data VALUES (?)", (CANARY,))
        self.connection.commit()
        self.addCleanup(self.connection.close)
        self.policy_path = self.root / "publisher.json"
        self.policy_path.write_bytes(publisher.canonical(self.config))
        self.policy_path.chmod(0o600)

    def add(self, action="db_backup", status="done", created="2026-10-24T11:20:00Z", finished="2026-10-24T11:30:00Z", *, raw=None, value=None, skill="maintenance", commit=True):
        if status in {"pending", "claimed"}:
            finished = None
        if raw is None and status == "done":
            raw = json.dumps(value if value is not None else result(action))
        row = self.connection.execute("INSERT INTO jobs(skill,action,status,created_at,finished_at,result,error,payload,chat_id,claimed_at,available_at,attempts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (skill, action, status, created, finished, raw, CANARY, json.dumps({"text": CANARY}), 999, "2026-10-24T11:21:00Z", "2026-10-24T13:00:00Z", 1))
        if commit:
            self.connection.commit()
        return row.lastrowid

    def collect(self, age=None):
        config = dict(self.config)
        if age is not None:
            config["max_age_seconds"] = age
        return publisher.collect(config, NOW)

    def projected(self, document=None, age=86400):
        return adapters.backup_receipt(document or self.collect(), PROJECT, SOURCE, NOW, age)

    def install_previous(self):
        self.add()
        document = self.collect()
        self.output.write_bytes(publisher.canonical(document) + b"\n")
        self.output.chmod(0o600)
        return self.output.read_bytes()

    def test_real_contract_dates_and_verified_archive_coverage(self):
        self.add()
        self.add("backup_verify", created="2026-10-24T11:35:00Z", finished="2026-10-24T11:40:00Z")
        document = self.collect()
        value = self.projected(document)
        self.assertEqual(document["schema"], adapters.BACKUP_SCHEMA)
        self.assertEqual(document["source_format"], adapters.BACKUP_SOURCE)
        self.assertEqual(value["backup"]["finished_at"], "2026-10-24T11:30:00Z")
        self.assertEqual(value["restore"]["coverage"], "latest_recorded_backup")
        self.assertEqual(value["restore"]["integrity"], "ok")
        self.assertEqual(value["gaps"], {})
        self.assertNotIn(CANARY, json.dumps(document))
        self.assertNotIn("chat_id", json.dumps(document))

    def test_same_attempt_and_success_are_deduplicated_by_private_id(self):
        self.add()
        document = self.collect()
        self.assertEqual(len(document["jobs"]), 1)
        self.assertNotIn("id", document["jobs"][0])

    def test_latest_failed_attempt_retains_older_success(self):
        self.add()
        self.add(status="failed", created="2026-10-24T11:45:00Z", finished="2026-10-24T11:46:00Z", raw=CANARY)
        value = self.projected()
        self.assertEqual(value["latest_attempts"]["backup"]["status"], "failed")
        self.assertEqual(value["gaps"]["backup_latest_attempt"], "latest_attempt_failed")
        self.assertEqual(value["backup"]["finished_at"], "2026-10-24T11:30:00Z")
        self.assertIsNone(self.collect()["jobs"][-1]["result"])

    def test_retry_pending_and_claimed_never_fabricate_completion(self):
        self.add()
        for status in ("pending", "claimed"):
            with self.subTest(status=status):
                row_id = self.add(status=status, created="2026-10-24T11:45:00Z", raw=CANARY)
                value = self.projected()
                self.assertEqual(value["latest_attempts"]["backup"]["status"], status)
                self.assertIsNone(value["latest_attempts"]["backup"]["finished_at"])
                self.assertEqual(value["gaps"]["backup_latest_attempt"], "latest_attempt_pending")
                self.assertNotIn(CANARY, json.dumps(self.collect()))
                self.connection.execute("DELETE FROM jobs WHERE id=?", (row_id,)); self.connection.commit()

    def test_latest_success_uses_completion_not_creation(self):
        self.add(created="2026-10-24T11:00:00Z", finished="2026-10-24T11:50:00Z")
        self.add(created="2026-10-24T11:10:00Z", finished="2026-10-24T11:30:00Z")
        value = self.projected()
        self.assertEqual(value["backup"]["finished_at"], "2026-10-24T11:50:00Z")
        self.assertEqual(value["latest_attempts"]["backup"]["created_at"], "2026-10-24T11:10:00Z")

    def test_timestamp_sort_is_chronological_across_utc_spellings(self):
        self.add(created="2026-10-24T11:20:00.100000+00:00", finished="2026-10-24T11:30:00Z")
        self.add(status="failed", created="2026-10-24T11:20:00Z", finished="2026-10-24T11:40:00Z")
        self.assertEqual(self.projected()["latest_attempts"]["backup"]["status"], "done")

    def test_latest_completion_preserves_microsecond_precision(self):
        self.add(created="2026-10-24T11:00:00Z", finished="2026-10-24T11:30:00.000002+00:00")
        self.add(created="2026-10-24T11:10:00Z", finished="2026-10-24T11:30:00.000001Z")
        self.assertEqual(self.projected()["backup"]["finished_at"], "2026-10-24T11:30:00.000002+00:00")
    def test_spaces_object_key_discarded_before_projection(self):
        self.add(value=result(offsite="spaces:private-account/" + CANARY))
        document = self.collect()
        self.assertEqual(document["jobs"][0]["result"]["offsite"], "spaces")
        self.assertEqual(self.projected(document)["backup"]["offsite"], "delivered")
        self.assertNotIn(CANARY, json.dumps(document))
        self.assertNotIn("private-account", json.dumps(document))

    def test_known_offsite_states_remain_distinct(self):
        for original, expected in (("", "not_configured"), ("not-due", "not_due"), ("blocked:size", "blocked"), ("telegram:fleet", "delivered")):
            with self.subTest(original=original):
                self.connection.execute("DELETE FROM jobs"); self.connection.commit()
                self.add(value=result(offsite=original))
                self.assertEqual(self.projected()["backup"]["offsite"], expected)

    def test_only_exact_maintenance_actions_are_selected(self):
        self.add(skill="other", raw=CANARY)
        self.add(action="other_action", raw=CANARY)
        self.add(skill="Maintenance", raw=CANARY)
        document = self.collect()
        self.assertEqual(document["jobs"], [])
        self.assertEqual(self.projected(document)["gaps"], {"backup": "no_success_recorded", "restore": "no_success_recorded"})

    def test_empty_valid_jobs_is_explicit_missing_success(self):
        value = self.projected()
        self.assertIsNone(value["backup"])
        self.assertIsNone(value["restore"])
        self.assertEqual(value["gaps"]["backup"], "no_success_recorded")

    def test_fresh_export_keeps_stale_success_dates(self):
        self.add(created="2026-10-22T11:20:00Z", finished="2026-10-22T11:30:00Z", value=result(archive="ingest-20261022T110000Z.db.gz"))
        value = self.projected()
        self.assertEqual(value["exported_at"], NOW)
        self.assertEqual(value["backup"]["finished_at"], "2026-10-22T11:30:00Z")
        self.assertEqual(value["gaps"]["backup"], "stale")

    def test_restore_of_different_archive_does_not_verify_latest_backup(self):
        self.add()
        self.add("backup_verify", created="2026-10-24T11:35:00Z", finished="2026-10-24T11:40:00Z", value=result("backup_verify", archive="ingest-20261023T110000Z.db.gz"))
        self.assertEqual(self.projected()["gaps"]["latest_backup_restore"], "latest_backup_not_restore_verified")

    def test_wal_commits_visible_and_uncommitted_rows_excluded(self):
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA wal_autocheckpoint=0")
        self.add()
        self.assertTrue(Path(str(self.database) + "-wal").exists())
        self.assertEqual(self.projected()["latest_attempts"]["backup"]["status"], "done")
        self.add(status="failed", created="2026-10-24T11:45:00Z", finished="2026-10-24T11:46:00Z", commit=False)
        self.assertEqual(self.projected()["latest_attempts"]["backup"]["status"], "done")
        self.connection.commit()
        self.assertEqual(self.projected()["latest_attempts"]["backup"]["status"], "failed")

    def test_read_does_not_change_original_database_rows_or_bytes(self):
        self.add()
        before = self.database.read_bytes()
        changes = self.connection.total_changes
        self.collect()
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(self.connection.total_changes, changes)
        self.assertEqual(self.connection.execute("SELECT payload,error FROM jobs").fetchone(), (json.dumps({"text": CANARY}), CANARY))

    def test_authorizer_reads_no_payload_error_user_or_unrelated_table(self):
        self.add()
        reads = []
        original = publisher._authorize
        def recorded(action, table, column, database, trigger):
            if action == sqlite3.SQLITE_READ:
                reads.append((table, column))
            return original(action, table, column, database, trigger)
        with patch.object(publisher, "_authorize", side_effect=recorded):
            self.collect()
        self.assertTrue(reads)
        self.assertTrue(all(table in {"jobs", "sqlite_master"} for table, _column in reads))
        self.assertFalse({"payload", "error", "chat_id", "content"} & {column for _table, column in reads})

    def test_missing_database_is_unavailable_not_empty_success(self):
        with self.assertRaises((OSError, publisher.PublicationError)):
            publisher.collect(dict(self.config, database_path=str(self.root / "missing.sqlite3")), NOW)

    def test_missing_table_and_view_replacement_are_unavailable(self):
        self.connection.execute("DROP TABLE jobs"); self.connection.commit()
        with self.assertRaises(publisher.PublicationError):
            self.collect()
        self.connection.execute("CREATE VIEW jobs AS SELECT content AS result FROM private_data"); self.connection.commit()
        with self.assertRaises(publisher.PublicationError):
            self.collect()

    def test_deadline_failure_is_unavailable(self):
        self.add()
        with patch.object(publisher, "READ_TIMEOUT_SECONDS", -1), self.assertRaises(publisher.PublicationError):
            self.collect()

    def test_locked_database_failure_is_unavailable(self):
        self.connection.execute("BEGIN EXCLUSIVE")
        try:
            with self.assertRaises(publisher.PublicationError):
                self.collect()
        finally:
            self.connection.rollback()

    def test_invalid_result_json_duplicate_nonfinite_and_oversize_refused(self):
        for raw in ('{"file":1,"file":2}', '{"bytes":NaN}', '{"bytes":Infinity}', '{bad', '"' + "x" * (publisher.MAX_RESULT_BYTES + 1) + '"', json.dumps([1, 2])):
            with self.subTest(kind=raw[:20]):
                self.connection.execute("DELETE FROM jobs"); self.connection.commit(); self.add(raw=raw)
                with self.assertRaises(publisher.PublicationError):
                    self.collect()

    def test_unknown_fields_and_targets_are_not_copied(self):
        for mutate in (lambda value: value.update(target="https://" + CANARY), lambda value: value.update(offsite="https://" + CANARY), lambda value: value.update(file="/root/" + CANARY)):
            value = result(); mutate(value)
            self.connection.execute("DELETE FROM jobs"); self.connection.commit(); self.add(value=value)
            with self.assertRaises(publisher.PublicationError) as raised:
                self.collect()
            self.assertNotIn(CANARY, str(raised.exception))

    def test_success_types_future_and_invalid_job_dates_refused(self):
        for sql, parameters in (("UPDATE jobs SET result=?", (json.dumps(dict(result(), bytes=True)),)), ("UPDATE jobs SET created_at=?", ("2026-10-25T12:00:00Z",)), ("UPDATE jobs SET created_at=?", ("invalid",)), ("UPDATE jobs SET finished_at=NULL", ())):
            self.connection.execute("DELETE FROM jobs"); self.connection.commit(); self.add()
            self.connection.execute(sql, parameters); self.connection.commit()
            with self.assertRaises(publisher.PublicationError):
                self.collect()

    def test_failed_or_pending_state_with_invalid_dates_refused(self):
        self.add(status="pending")
        self.connection.execute("UPDATE jobs SET finished_at=?", (NOW,)); self.connection.commit()
        with self.assertRaises(publisher.PublicationError):
            self.collect()
        self.connection.execute("UPDATE jobs SET status='failed',finished_at=NULL"); self.connection.commit()
        with self.assertRaises(publisher.PublicationError):
            self.collect()

    def test_symlink_source_sidecar_and_output_are_rejected(self):
        link = self.root / "linked.sqlite3"; link.symlink_to(self.database)
        with self.assertRaises(publisher.PublicationError):
            publisher.collect(dict(self.config, database_path=str(link)), NOW)
        sidecar = Path(str(self.database) + "-journal"); sidecar.symlink_to(self.policy_path)
        with self.assertRaises(publisher.PublicationError):
            self.collect()
        sidecar.unlink()
        self.output.symlink_to(self.policy_path)
        with self.assertRaises(publisher.PublicationError):
            publisher.publish(self.policy_path)

    def test_wrong_source_owner_hardlink_and_writable_ancestor_refused(self):
        with self.assertRaises(publisher.PublicationError):
            publisher.collect(dict(self.config, database_uid=1), NOW)
        linked = self.root / "hard.sqlite3"; os.link(self.database, linked)
        with self.assertRaises(publisher.PublicationError):
            self.collect()
        linked.unlink()
        self.root.chmod(0o777)
        try:
            with self.assertRaises(publisher.PublicationError):
                self.collect()
        finally:
            self.root.chmod(0o700)

    def test_database_identity_change_during_read_refused(self):
        self.add()
        replacement = self.root / "replacement.sqlite3"
        replacement.write_bytes(self.database.read_bytes())
        original = publisher._protected
        seen = [0]
        def replaced(path, **options):
            if Path(path) == self.database:
                seen[0] += 1
                if seen[0] == 2:
                    os.replace(replacement, self.database)
            return original(path, **options)
        with patch.object(publisher, "_protected", side_effect=replaced), self.assertRaises(publisher.PublicationError):
            self.collect()

    def test_publish_installs_only_sanitized_atomic_root_file(self):
        self.add(value=result(offsite="spaces:private/" + CANARY))
        document = self.collect()
        with patch.object(publisher, "collect", return_value=document):
            receipt = publisher.publish(self.policy_path)
        self.assertEqual(self.output.stat().st_uid, 0)
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o600)
        self.assertEqual(receipt["receipt_sha256"], hashlib.sha256(publisher.canonical(document)).hexdigest())
        self.assertEqual(receipt["selected_jobs"], 1)
        self.assertNotIn(CANARY, self.output.read_text())
        self.assertEqual(list(self.root.glob("*.pending")), [])

    def test_read_or_validation_failure_preserves_last_good_output(self):
        previous = self.install_previous()
        self.add(created="2026-10-24T11:50:00Z", finished="2026-10-24T11:51:00Z", raw="{" + CANARY)
        with self.assertRaises(publisher.PublicationError):
            publisher.publish(self.policy_path)
        self.assertEqual(self.output.read_bytes(), previous)
        self.assertEqual(list(self.root.glob("*.pending")), [])

    def test_atomic_replace_failure_preserves_last_good(self):
        previous = self.install_previous()
        document = self.collect(); document["exported_at"] = "2026-10-24T12:01:00Z"
        with patch.object(publisher, "collect", return_value=document), patch.object(publisher.os, "replace", side_effect=OSError("injected")), self.assertRaises(OSError):
            publisher.publish(self.policy_path)
        self.assertEqual(self.output.read_bytes(), previous)
        self.assertEqual(list(self.root.glob("*.pending")), [])

    def test_capture_cannot_move_backwards(self):
        previous = self.install_previous()
        document = self.collect(); document["exported_at"] = "2026-10-24T11:59:59Z"
        with patch.object(publisher, "collect", return_value=document), self.assertRaises(publisher.PublicationError):
            publisher.publish(self.policy_path)
        self.assertEqual(self.output.read_bytes(), previous)

    def test_concurrent_refresh_cannot_overwrite_changed_publication(self):
        self.install_previous(); document = self.collect()
        original = publisher._read_protected; calls = [0]
        def concurrent(path, maximum):
            value = original(path, maximum)
            if Path(path) == self.output:
                calls[0] += 1
                if calls[0] == 2:
                    return dict(value, exported_at="2026-10-24T12:01:00Z")
            return value
        with patch.object(publisher, "collect", return_value=document), patch.object(publisher, "_read_protected", side_effect=concurrent), self.assertRaises(publisher.PublicationError):
            publisher.publish(self.policy_path)

    def test_contended_directory_lock_preserves_previous_without_query(self):
        import fcntl
        previous = self.install_previous()
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(publisher, "collect") as collect, self.assertRaises(publisher.PublicationError):
                publisher.publish(self.policy_path)
            collect.assert_not_called()
            self.assertEqual(self.output.read_bytes(), previous)
        finally:
            os.close(descriptor)

    def test_failed_refresh_releases_directory_lock(self):
        import fcntl
        self.install_previous()
        with patch.object(publisher, "collect", side_effect=publisher.PublicationError("injected")), self.assertRaises(publisher.PublicationError):
            publisher.publish(self.policy_path)
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(descriptor)
    def test_policy_unknown_fields_duplicate_json_and_writable_policy_refused(self):
        with self.assertRaises(publisher.PublicationError):
            publisher.policy(dict(self.config, shell="not-supported"))
        self.policy_path.write_text('{"schema":1,"schema":2}')
        with self.assertRaises(publisher.PublicationError):
            publisher.publish(self.policy_path)
        self.policy_path.write_bytes(publisher.canonical(self.config)); self.policy_path.chmod(0o666)
        with self.assertRaises(publisher.PublicationError):
            publisher.publish(self.policy_path)

    def test_policy_rejects_source_output_alias_invalid_bounds_and_ids(self):
        for change in ({"output_path": str(self.database)}, {"output_path": str(self.database) + "-wal"}, {"database_uid": True}, {"max_age_seconds": False}, {"project_id": "../private"}, {"database_path": "relative.sqlite3"}):
            with self.subTest(change=list(change)), self.assertRaises(publisher.PublicationError):
                publisher.policy(dict(self.config, **change))

    def test_nonroot_publish_cannot_be_granted_by_policy(self):
        with patch.object(publisher.os, "geteuid", return_value=123), self.assertRaises(publisher.PublicationError):
            publisher.publish(self.policy_path)

    def test_cli_failure_returns_no_paths_results_or_exception_text(self):
        captured = io.StringIO()
        with patch.object(publisher, "publish", side_effect=ValueError(CANARY)), contextlib.redirect_stderr(captured):
            self.assertEqual(publisher.main(["--policy", str(self.policy_path)]), 1)
        self.assertEqual(captured.getvalue(), '{"status":"unavailable"}\n')


class AuthorizerTests(unittest.TestCase):
    def test_unrelated_reads_writes_extensions_attach_and_pragmas_denied(self):
        denied = ((sqlite3.SQLITE_READ, "jobs", "payload", "main", None), (sqlite3.SQLITE_READ, "private_data", "content", "main", None),
                  (sqlite3.SQLITE_READ, "jobs", "result", "temp", None), (sqlite3.SQLITE_UPDATE, "jobs", "status", "main", None),
                  (sqlite3.SQLITE_ATTACH, "private", None, None, None), (sqlite3.SQLITE_FUNCTION, None, "load_extension", None, None),
                  (sqlite3.SQLITE_PRAGMA, "user_version", "1", None, None), (sqlite3.SQLITE_READ, "jobs", "result", "main", "untrusted_view"))
        for arguments in denied:
            self.assertEqual(publisher._authorize(*arguments), sqlite3.SQLITE_DENY)


if __name__ == "__main__":
    unittest.main()