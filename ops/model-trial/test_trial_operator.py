"""Synthetic-database and mocked-controller checks; no Linux/service/paid calls."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import trial_config as config
import trial_operator as operator
from test_trial_config import InstalledLLM


def fixture_db(path=":memory:"):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE reply_outbox(id INTEGER PRIMARY KEY, state TEXT);
        CREATE TABLE task_approvals(id INTEGER PRIMARY KEY, status TEXT);
        CREATE TABLE jobs(id INTEGER PRIMARY KEY, status TEXT, result TEXT, payload TEXT,
                          skill TEXT, action TEXT, claimed_at TEXT, created_at TEXT);
        CREATE TABLE telegram_updates(update_id INTEGER PRIMARY KEY, status TEXT);
        CREATE TABLE assistant_task_steps(id INTEGER PRIMARY KEY, status TEXT);
        CREATE TABLE task_step_attempts(id INTEGER PRIMARY KEY, status TEXT);
        CREATE TABLE fleet_requests(id TEXT PRIMARY KEY, state TEXT, payload TEXT);
        CREATE TABLE llm_usage(id INTEGER PRIMARY KEY, kind TEXT, ts TEXT,
                               cost_usd REAL, skill TEXT, model TEXT);
        CREATE TABLE deployment_notifications(id INTEGER PRIMARY KEY, status TEXT);
    """)
    return conn


class IdleGuardTests(unittest.TestCase):
    def setUp(self):
        self.conn = fixture_db()

    def tearDown(self):
        self.conn.close()

    def assert_blocked_without_writes(self):
        before = self.conn.total_changes
        with self.assertRaises((ValueError, sqlite3.OperationalError)):
            operator.idle(self.conn)
        self.assertEqual(self.conn.total_changes, before)

    def test_empty_known_schema_is_idle_and_read_only(self):
        before = self.conn.total_changes
        operator.idle(self.conn)
        self.assertEqual(self.conn.total_changes, before)

    def test_pending_and_sending_reply_deliveries_block(self):
        for state in ("pending", "sending"):
            with self.subTest(state=state):
                self.conn.execute("DELETE FROM reply_outbox")
                self.conn.execute("INSERT INTO reply_outbox(state) VALUES(?)", (state,))
                self.assert_blocked_without_writes()

    def test_executing_approval_blocks_even_without_claimed_status(self):
        self.conn.execute("INSERT INTO task_approvals(status) VALUES('executing')")
        self.assert_blocked_without_writes()

    def test_claimed_background_job_and_started_inference_block(self):
        self.conn.execute("INSERT INTO jobs(status,result) VALUES('claimed',?)",
                          (json.dumps({"background_inference": "started", "reservation_id": 41}),))
        self.assert_blocked_without_writes()

    def test_pending_and_processing_inbox_updates_block(self):
        for status in ("pending", "processing"):
            with self.subTest(status=status):
                self.conn.execute("DELETE FROM telegram_updates")
                self.conn.execute("INSERT INTO telegram_updates(status) VALUES(?)", (status,))
                self.assert_blocked_without_writes()

    def test_active_task_steps_block(self):
        for status in ("claimed", "running", "waiting_worker", "waiting_fleet"):
            with self.subTest(status=status):
                self.conn.execute("DELETE FROM assistant_task_steps")
                self.conn.execute("INSERT INTO assistant_task_steps(status) VALUES(?)", (status,))
                self.assert_blocked_without_writes()

    def test_started_task_effect_attempt_blocks(self):
        self.conn.execute("INSERT INTO task_step_attempts(status) VALUES('started')")
        self.assert_blocked_without_writes()

    def test_fleet_inference_started_blocks(self):
        self.conn.execute("INSERT INTO fleet_requests VALUES('fixture','planning',?)",
                          (json.dumps({"inference": {"state": "started", "reservation_id": 41}}),))
        self.assert_blocked_without_writes()

    def test_current_unknown_paid_inference_blocks(self):
        self.conn.execute("INSERT INTO llm_usage(kind,ts,cost_usd,skill,model) VALUES(?,?,?,?,?)",
                          ("unknown_chat", datetime.now(timezone.utc).isoformat(), .02,
                           "converse", "fixture-model"))
        self.assert_blocked_without_writes()

    def test_historical_unknown_is_preserved_and_not_confused_with_current_inference(self):
        self.conn.execute("INSERT INTO llm_usage(kind,ts,cost_usd,skill,model) VALUES(?,?,?,?,?)",
                          ("unknown_chat", "2000-01-01T00:00:00+00:00", .02,
                           "converse", "fixture-model"))
        before = operator.table_snapshot(self.conn)
        operator.idle(self.conn, "2026-10-25T10:00:00+00:00")
        self.assertEqual(operator.table_snapshot(self.conn), before)

    def test_missing_required_idle_schema_fails_closed(self):
        self.conn.execute("DROP TABLE task_approvals")
        self.assert_blocked_without_writes()


class PreservationTests(unittest.TestCase):
    def test_row_snapshot_covers_blob_rows_duplicates_schema_and_sequences_without_bodies(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.executescript("CREATE TABLE source(id INTEGER PRIMARY KEY AUTOINCREMENT,text TEXT,raw BLOB);")
        conn.executemany("INSERT INTO source(text,raw) VALUES(?,?)",
                         [("private owner fixture", b"\x00\xff"), ("private owner fixture", b"\x00\xff")])
        before = conn.total_changes
        snapshot = operator.table_snapshot(conn)
        self.assertEqual(conn.total_changes, before)
        self.assertEqual(snapshot["source"]["rows"], 2)
        self.assertEqual(snapshot["source"]["columns"], ["id", "text", "raw"])
        self.assertIn("sqlite_sequence", snapshot)
        self.assertNotIn("private owner fixture", json.dumps(snapshot))
        conn.execute("UPDATE source SET raw=? WHERE id=1", (b"changed",))
        self.assertNotEqual(operator.table_snapshot(conn)["source"]["rows_sha256"],
                            snapshot["source"]["rows_sha256"])

    def test_prepare_rejects_incomplete_accepted_protected_map_before_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            env = base / "production.env"
            env.write_bytes(b"DO_CHAT_MODEL=deepseek-4-flash\nROUTER_MODEL=deepseek-4-flash\n")
            pins = base / "protected.json"
            pins.write_text("{}")
            conn = fixture_db()
            self.addCleanup(conn.close)

            @contextmanager
            def readonly(_path):
                yield conn

            def publish(path, value, raw=False):
                Path(path).write_bytes(value if raw else operator.canonical(value))

            common = SimpleNamespace(load_config=lambda _env: SimpleNamespace(db_path=base / "db.sqlite"))
            llm = SimpleNamespace(default_profiles=InstalledLLM.default_profiles,
                                  budget_limits=lambda _cfg, _conn: (2.0, 12.0))
            with mock.patch.object(operator, "ENV", env), \
                    mock.patch.object(operator, "EXPECTED_ENV", operator.digest(env.read_bytes())), \
                    mock.patch.object(operator.os, "geteuid", return_value=0, create=True), \
                    mock.patch.object(operator, "native_identity", return_value={"source_revision": operator.EXPECTED_SOURCE}), \
                    mock.patch.object(operator, "readonly", readonly), \
                    mock.patch.object(operator, "publish", publish), \
                    mock.patch.object(operator, "runtime_hashes", return_value={}), \
                    mock.patch.object(operator, "services", return_value={
                        operator.MAIN: {"ActiveEnterTimestamp": "Sun 2026-10-25 10:00:00 UTC"}}), \
                    mock.patch.object(operator, "backup_db") as backup, \
                    mock.patch.dict("sys.modules", {"common": common, "llm": llm}):
                with self.assertRaises(ValueError):
                    operator.prepare(base / "trial", base / "source", pins)
                backup.assert_not_called()


class ControllerContainmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "trial"
        self.root.mkdir()
        self.env_path = self.base / "production.env"
        self.original_raw = b"DO_CHAT_MODEL=deepseek-4-flash\nROUTER_MODEL=deepseek-4-flash\nUNRELATED=original\n"
        self.old = config.parse_env_bytes(self.original_raw)
        self.candidate_raw = config.candidate_env_bytes(self.original_raw, InstalledLLM)
        self.candidate = config.parse_env_bytes(self.candidate_raw)
        self.env_path.write_bytes(self.original_raw)
        (self.root / "original.env").write_bytes(self.original_raw)
        (self.root / "candidate.env").write_bytes(self.candidate_raw)
        self.identity = {"source_revision": operator.EXPECTED_SOURCE, "deployment_id": "fixture"}
        self.source = self.base / "source"
        self.source.mkdir()
        (self.source / "tg_ingest_agent.py").write_bytes(b"# accepted synthetic runtime source\n")
        self.runtime = {"agent.py": operator.digest((self.source / "tg_ingest_agent.py").read_bytes())}
        self.initial = {
            "identity": self.identity, "source": str(self.source),
            "before_env_sha256": operator.digest(self.original_raw),
            "candidate_env_sha256": operator.digest(self.candidate_raw),
            "runtime_hashes": self.runtime, "protected": {}, "budget_limits_usd": [2.0, 12.0],
            "notice_sha256": operator.digest(operator.canonical([])),
        }
        (self.root / "prepared.json").write_text(json.dumps(self.initial))
        self.proof = self.base / "qualification.json"
        stamp = datetime.now(timezone.utc).isoformat()
        self.proof.write_text(json.dumps({
            "schema": "cara.model-trial-qualified/v1",
            "status": "passed", "candidate_env_sha256": self.initial["candidate_env_sha256"],
            "at": stamp, "started_at": stamp, "finished_at": stamp,
            "source_revision": operator.EXPECTED_SOURCE,
            "runtime_hashes": self.runtime, "unknown_usage_ids": [],
            "accounted_cost_usd": .05, "qualification_ceiling_usd": 5.0,
            "manual_quality_passed": True, "functional_cases_passed": 24,
            "functional_cases_total": 24,
        }))
        package = {name: operator.digest((Path(operator.__file__).parent / name).read_bytes())
                   for name in ("trial_operator.py", "trial_config.py")}
        (self.base / "operator-files.json").write_text(json.dumps(package))
        self.package_hash = operator.digest(operator.canonical(package))
        self.conn = fixture_db()
        self.addCleanup(self.conn.close)
        self.main_active = True
        self.commands = []
        self.replacements = 0
        self.fail_after_replace = False
        self.fail_verification = False

        @contextmanager
        def readonly(_path):
            yield self.conn

        def services():
            state = {unit: {"ActiveState": "active", "MainPID": str(100 + index),
                            "NRestarts": "0", "ActiveEnterTimestamp": (
                                datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%a %Y-%m-%d %H:%M:%S UTC")}
                     for index, unit in enumerate((operator.MAIN, *operator.PEERS))}
            if not self.main_active:
                state[operator.MAIN].update(ActiveState="inactive", MainPID="0")
            return state

        def publish(path, value, raw=False):
            path = Path(path)
            if path.exists():
                raise FileExistsError(path)
            path.write_bytes(value if raw else operator.canonical(value))

        def run(args, log=None, timeout=120):
            self.commands.append(list(args))
            if args[:2] == ["/usr/bin/systemctl", "stop"]:
                self.main_active = False
            elif args[:2] == ["/usr/bin/systemctl", "start"]:
                self.main_active = True
            elif args[0] == "/usr/bin/bash" and self.fail_verification:
                self.env_path.write_bytes(self.env_path.read_bytes() + b"LATER=preserve-later-edit\n")
                raise ValueError("synthetic installed verification failed")
            if log is not None:
                publish(log, b"synthetic operation\n", raw=True)
            return ""

        def replace_env(raw, expected):
            self.assertEqual(operator.digest(self.env_path.read_bytes()), expected)
            self.replacements += 1
            self.env_path.write_bytes(raw)
            if self.fail_after_replace and self.replacements == 1:
                self.env_path.write_bytes(raw + b"LATER=preserve-later-edit\n")
                raise OSError("synthetic directory fsync failed after atomic replacement")

        def loaded_models(_pid, expected):
            loaded = config.parse_env_bytes(self.env_path.read_bytes())
            for key in config.MODEL_KEYS:
                self.assertEqual(loaded.get(key), expected.get(key))
            return {key: operator.digest((loaded.get(key) or "").encode()) for key in config.MODEL_KEYS}

        common = SimpleNamespace(load_config=lambda _env: SimpleNamespace(db_path=self.base / "db.sqlite"))
        self.real_verify_preserved = operator.verify_preserved
        patches = [
            mock.patch.object(operator, "ENV", self.env_path),
            mock.patch.object(operator, "readonly", readonly),
            mock.patch.object(operator, "services", services),
            mock.patch.object(operator, "publish", publish),
            mock.patch.object(operator, "run", run),
            mock.patch.object(operator, "replace_env", replace_env),
            mock.patch.object(operator, "loaded_models", loaded_models),
            mock.patch.object(operator, "backup_db"),
            mock.patch.object(operator, "verify_preserved"),
            mock.patch.object(operator, "native_identity", return_value=self.identity),
            mock.patch.object(operator, "runtime_hashes", return_value=self.runtime),
            mock.patch.object(operator.time, "sleep"),
            mock.patch.dict("sys.modules", {"common": common}),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def assert_original_models_and_later_edit(self):
        current = config.parse_env_bytes(self.env_path.read_bytes())
        for key in config.MODEL_KEYS:
            self.assertEqual(current.get(key), self.old.get(key))
        self.assertEqual(current["LATER"], "preserve-later-edit")
        self.assertTrue(self.main_active)
        self.assertFalse((self.root / "accepted.json").exists())
        self.assertTrue((self.root / "contained-original-models.json").exists())

    def test_exception_after_atomic_replace_restores_only_model_keys(self):
        self.fail_after_replace = True
        before = operator.table_snapshot(self.conn)
        with self.assertRaises(OSError):
            operator.activate(self.root, self.proof, self.package_hash)
        self.assert_original_models_and_later_edit()
        self.assertEqual(operator.table_snapshot(self.conn), before)

    def test_installed_verification_failure_restores_only_model_keys(self):
        self.fail_verification = True
        with self.assertRaises(ValueError):
            operator.activate(self.root, self.proof, self.package_hash)
        self.assert_original_models_and_later_edit()

    def test_revert_conflict_stops_before_any_service_command(self):
        (self.root / "accepted.json").write_text("{}")
        self.env_path.write_bytes(config.update_env_bytes(self.candidate_raw,
                                                        {"DO_CHAT_MODEL": "owner-changed-model"}))
        with self.assertRaises(ValueError):
            operator.revert(self.root)
        self.assertEqual(self.commands, [])
        self.assertTrue(self.main_active)

    def test_revert_preserves_later_unrelated_edits_and_every_database_row(self):
        (self.root / "accepted.json").write_text("{}")
        self.env_path.write_bytes(self.candidate_raw + b"LATER=preserve-later-edit\n")
        before = operator.table_snapshot(self.conn)
        operator.revert(self.root)
        current = config.parse_env_bytes(self.env_path.read_bytes())
        self.assertEqual(current, dict(self.old, LATER="preserve-later-edit"))
        self.assertEqual(operator.table_snapshot(self.conn), before)
        self.assertTrue(self.main_active)
        self.assertTrue((self.root / "reverted.json").exists())

    def test_reversion_backup_error_resumes_candidate_without_restoring_database(self):
        (self.root / "accepted.json").write_text("{}")
        self.env_path.write_bytes(self.candidate_raw)
        before = operator.table_snapshot(self.conn)
        with mock.patch.object(operator, "backup_db", side_effect=OSError("synthetic backup I/O failure")):
            with self.assertRaises(OSError):
                operator.revert(self.root)
        self.assertTrue(self.main_active)
        self.assertEqual(self.env_path.read_bytes(), self.candidate_raw)
        self.assertEqual(operator.table_snapshot(self.conn), before)
        self.assertTrue((self.root / "reversion-failed.json").exists())
        self.assertTrue((self.root / "contained-reversion-environment.json").exists())
        self.assertEqual(self.commands, [["/usr/bin/systemctl", "stop", operator.MAIN],
                                         ["/usr/bin/systemctl", "start", operator.MAIN]])

    def test_reversion_post_cas_error_resumes_actual_original_models_and_keeps_later_edit(self):
        (self.root / "accepted.json").write_text("{}")
        self.env_path.write_bytes(self.candidate_raw)
        self.fail_after_replace = True
        before = operator.table_snapshot(self.conn)
        with self.assertRaises(OSError):
            operator.revert(self.root)
        current = config.parse_env_bytes(self.env_path.read_bytes())
        self.assertEqual(current, dict(self.old, LATER="preserve-later-edit"))
        self.assertEqual(self.replacements, 1)  # Do not undo a successful model CAS.
        self.assertTrue(self.main_active)
        self.assertEqual(operator.table_snapshot(self.conn), before)
        self.assertTrue((self.root / "contained-reversion-environment.json").exists())

    def test_failure_receipt_and_recovery_log_errors_do_not_suppress_activation_resume(self):
        original_publish = operator.publish
        rejected = []

        def publish(path, value, raw=False):
            name = Path(path).name
            if name in {"activation-failed.json", "contain-start.log", "contained-original-models.json"}:
                rejected.append(name)
                raise OSError("synthetic evidence filesystem full")
            return original_publish(path, value, raw=raw)

        with mock.patch.object(operator, "publish", publish), \
                mock.patch.object(operator, "backup_db", side_effect=OSError("synthetic backup failure")):
            with self.assertRaises(OSError):
                operator.activate(self.root, self.proof, self.package_hash)
        self.assertEqual(set(rejected), {"activation-failed.json", "contain-start.log", "contained-original-models.json"})
        self.assertTrue(self.main_active)
        self.assertEqual(self.env_path.read_bytes(), self.original_raw)
        self.assertEqual(self.commands, [["/usr/bin/systemctl", "stop", operator.MAIN],
                                         ["/usr/bin/systemctl", "start", operator.MAIN]])

    def test_failure_receipt_and_recovery_log_errors_do_not_suppress_reversion_resume(self):
        (self.root / "accepted.json").write_text("{}")
        self.env_path.write_bytes(self.candidate_raw)
        original_publish = operator.publish
        rejected = []

        def publish(path, value, raw=False):
            name = Path(path).name
            if name in {"reversion-failed.json", "revert-contain-start.log", "contained-reversion-environment.json"}:
                rejected.append(name)
                raise OSError("synthetic evidence filesystem full")
            return original_publish(path, value, raw=raw)

        with mock.patch.object(operator, "publish", publish), \
                mock.patch.object(operator, "backup_db", side_effect=OSError("synthetic backup failure")):
            with self.assertRaises(OSError):
                operator.revert(self.root)
        self.assertEqual(set(rejected), {"reversion-failed.json", "revert-contain-start.log", "contained-reversion-environment.json"})
        self.assertTrue(self.main_active)
        self.assertEqual(self.env_path.read_bytes(), self.candidate_raw)
        self.assertEqual(self.commands, [["/usr/bin/systemctl", "stop", operator.MAIN],
                                         ["/usr/bin/systemctl", "start", operator.MAIN]])

    def test_prestop_verification_failure_does_not_stop_or_restart_main(self):
        with mock.patch.object(operator, "verify_preserved", side_effect=ValueError("synthetic protected drift")):
            with self.assertRaises(ValueError):
                operator.activate(self.root, self.proof, self.package_hash)
        self.assertEqual(self.commands, [])
        self.assertTrue(self.main_active)
        self.assertEqual(self.env_path.read_bytes(), self.original_raw)
        self.assertTrue((self.root / "activation-failed.json").exists())

    def test_activation_failure_with_unknown_model_drift_does_not_force_restore_or_start(self):
        def unsafe_replace(_raw, _expected):
            self.env_path.write_bytes(config.update_env_bytes(self.original_raw,
                                                              {"DO_CHAT_MODEL": "unreviewed-model"}))
            raise OSError("synthetic model drift during failed replacement")

        with mock.patch.object(operator, "replace_env", unsafe_replace):
            with self.assertRaises(ValueError):
                operator.activate(self.root, self.proof, self.package_hash)
        self.assertFalse(self.main_active)
        self.assertEqual(config.parse_env_bytes(self.env_path.read_bytes())["DO_CHAT_MODEL"], "unreviewed-model")
        self.assertEqual(self.commands, [["/usr/bin/systemctl", "stop", operator.MAIN]])
        self.assertTrue((self.root / "containment-failed.json").exists())

    def test_reversion_failure_with_unknown_model_drift_does_not_force_restore_or_start(self):
        (self.root / "accepted.json").write_text("{}")
        self.env_path.write_bytes(self.candidate_raw)

        def backup_failure(*_args):
            self.env_path.write_bytes(config.update_env_bytes(self.candidate_raw,
                                                              {"DO_CHAT_MODEL": "unreviewed-model"}))
            raise OSError("synthetic model drift during failed backup")

        with mock.patch.object(operator, "backup_db", backup_failure):
            with self.assertRaises(ValueError):
                operator.revert(self.root)
        self.assertFalse(self.main_active)
        self.assertEqual(config.parse_env_bytes(self.env_path.read_bytes())["DO_CHAT_MODEL"], "unreviewed-model")
        self.assertEqual(self.commands, [["/usr/bin/systemctl", "stop", operator.MAIN]])
        self.assertTrue((self.root / "reversion-containment-failed.json").exists())

    def test_qualification_source_drift_rejects_before_service_commands(self):
        proof = json.loads(self.proof.read_text())
        proof["source_revision"] = "different-source"
        self.proof.write_text(json.dumps(proof))
        with self.assertRaises(ValueError):
            operator.activate(self.root, self.proof, self.package_hash)
        self.assertEqual(self.commands, [])

    def test_stale_qualification_rejects_before_service_commands(self):
        proof = json.loads(self.proof.read_text())
        proof["at"] = "2000-01-01T00:00:00+00:00"
        self.proof.write_text(json.dumps(proof))
        with self.assertRaises(ValueError):
            operator.activate(self.root, self.proof, self.package_hash)
        self.assertEqual(self.commands, [])

    def test_incomplete_unaccounted_or_overbudget_qualification_never_stops_service(self):
        good = json.loads(self.proof.read_text())
        failures = [
            {"manual_quality_passed": False},
            {"functional_cases_passed": 23},
            {"functional_cases_passed": 23, "functional_cases_total": 23},
            {"unknown_usage_ids": [41]},
            {"accounted_cost_usd": 5.01},
            {"qualification_ceiling_usd": 6.0},
        ]
        for change in failures:
            self.proof.write_text(json.dumps(dict(good, **change)))
            with self.subTest(change=change), self.assertRaises(ValueError):
                operator.activate(self.root, self.proof, self.package_hash)
            self.assertEqual(self.commands, [])
            self.assertFalse((self.root / "activate.STARTED").exists())

    def test_successful_activation_restarts_only_main_once_and_preserves_database(self):
        original_read = Path.read_text

        def read_text(path, *args, **kwargs):
            if str(path).replace("\\", "/").startswith("/proc/"):
                return "Name:\tsynthetic-agent\nUid:\t1000\t1000\t1000\t1000\n"
            return original_read(path, *args, **kwargs)

        before = operator.table_snapshot(self.conn)
        with mock.patch.object(Path, "read_text", read_text), mock.patch("builtins.print"):
            operator.activate(self.root, self.proof, self.package_hash)
        self.assertEqual(config.parse_env_bytes(self.env_path.read_bytes()), self.candidate)
        self.assertEqual(operator.table_snapshot(self.conn), before)
        service_commands = [command for command in self.commands if command[0] == "/usr/bin/systemctl"]
        self.assertEqual(service_commands, [["/usr/bin/systemctl", "stop", operator.MAIN],
                                            ["/usr/bin/systemctl", "start", operator.MAIN]])
        receipt = json.loads((self.root / "accepted.json").read_text())
        self.assertEqual(receipt["status"], "accepted")
        self.assertFalse(receipt["database_restored"])
        self.assertFalse(receipt["code_installed"])
        self.assertEqual(receipt["budget_limits_usd"], [2.0, 12.0])
        self.assertTrue(self.main_active)

    def test_package_file_drift_rejects_before_service_commands(self):
        package = json.loads((self.base / "operator-files.json").read_text())
        package["trial_config.py"] = "0" * 64
        (self.base / "operator-files.json").write_text(json.dumps(package))
        with self.assertRaises(ValueError):
            operator.activate(self.root, self.proof, self.package_hash)
        self.assertEqual(self.commands, [])

    def test_original_env_drift_rejects_before_service_commands(self):
        self.initial["protected"] = {str(self.env_path): {
            "sha256": operator.digest(self.original_raw), "uid": 0, "gid": 0, "mode": 0o600}}
        (self.root / "prepared.json").write_text(json.dumps(self.initial))
        self.env_path.write_bytes(self.original_raw + b"OWNER_EDIT=preserve\n")

        def snapshot(_manifest):
            value = dict(self.initial["protected"][str(self.env_path)])
            value["sha256"] = operator.digest(self.env_path.read_bytes())
            return {str(self.env_path): value}

        with mock.patch.object(operator, "verify_preserved", self.real_verify_preserved), \
                mock.patch.object(operator, "protected_snapshot", snapshot):
            with self.assertRaises(ValueError):
                operator.activate(self.root, self.proof, self.package_hash)
        self.assertEqual(self.commands, [])

    def test_candidate_env_drift_rejects_before_service_commands(self):
        (self.root / "candidate.env").write_bytes(self.candidate_raw + b"INJECTED=unapproved\n")
        with self.assertRaises(ValueError):
            operator.activate(self.root, self.proof, self.package_hash)
        self.assertEqual(self.commands, [])

    def test_prepared_source_drift_rejects_before_service_commands(self):
        (self.source / "tg_ingest_agent.py").write_bytes(b"# later modified source\n")
        # Any service command is itself the regression; do not continue into a
        # fake successful activation that would inspect the workstation's /proc.
        with mock.patch.object(operator, "run", side_effect=AssertionError("source guard must precede service commands")):
            with self.assertRaises(ValueError):
                operator.activate(self.root, self.proof, self.package_hash)
        self.assertEqual(self.commands, [])

    def test_revert_rejects_modified_original_model_artifact_before_service_commands(self):
        (self.root / "accepted.json").write_text("{}")
        self.env_path.write_bytes(self.candidate_raw)
        (self.root / "original.env").write_bytes(config.update_env_bytes(
            self.original_raw, {"DO_CHAT_MODEL": "unrecorded-original-model"}))
        with self.assertRaises(ValueError):
            operator.revert(self.root)
        self.assertEqual(self.commands, [])

    def test_revert_rejects_modified_candidate_artifact_even_if_current_matches_it(self):
        (self.root / "accepted.json").write_text("{}")
        modified = config.update_env_bytes(self.candidate_raw,
                                           {"DO_CHAT_MODEL": "unrecorded-candidate-model"})
        (self.root / "candidate.env").write_bytes(modified)
        self.env_path.write_bytes(modified)
        with self.assertRaises(ValueError):
            operator.revert(self.root)
        self.assertEqual(self.commands, [])


if __name__ == "__main__":
    unittest.main()
