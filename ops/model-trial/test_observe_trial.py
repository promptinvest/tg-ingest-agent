"""Synthetic-only observer regressions; no Cara import, network or live service."""
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import observe_trial as observer


PRIVATE = "SECRET_OWNER_MESSAGE_NEVER_EXPORT_THIS"
START = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)


@contextmanager
def database(path):
    conn = sqlite3.connect(path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "private.sqlite"
        with database(self.db) as conn:
            conn.executescript("""
                CREATE TABLE llm_usage(ts TEXT,skill TEXT,kind TEXT,model TEXT,
                    tokens_in INTEGER,tokens_out INTEGER,cost_usd REAL,seconds REAL,
                    trace_id TEXT,private_body TEXT);
                CREATE TABLE traces(kind TEXT,status TEXT,started_at TEXT,finished_at TEXT,
                    chat_id INTEGER,summary TEXT);
                CREATE TABLE trace_events(ts TEXT,stage TEXT,message TEXT,data TEXT,skill TEXT);
                CREATE TABLE reply_outbox(state TEXT,created_at TEXT,params_json TEXT);
                CREATE TABLE issues(ts TEXT,kind TEXT,status TEXT,detail TEXT,context TEXT);
                CREATE TABLE conversation(text TEXT);
            """)
            conn.execute("INSERT INTO conversation VALUES(?)", (PRIVATE,))
            conn.execute("INSERT INTO traces VALUES('inbound','ok',?,?,123,?)",
                         (observer.iso(START), observer.iso(START + timedelta(seconds=5)), PRIVATE))
            for stage in observer.STAGES:
                conn.execute("INSERT INTO trace_events VALUES(?,?,?,?, 'converse')", (observer.iso(START), stage, PRIVATE, PRIVATE))
            conn.execute("INSERT INTO reply_outbox VALUES('pending',?,?)", (observer.iso(START), PRIVATE))
            conn.execute("INSERT INTO issues VALUES(?, 'llm','open',?,?)", (observer.iso(START), PRIVATE, PRIVATE))

    def tearDown(self):
        self.temp.cleanup()

    def add_usage(self, skill="converse", kind="chat", model="claude-sonnet-5-5", count=1, seconds=2):
        with database(self.db) as conn:
            conn.executemany("INSERT INTO llm_usage VALUES(?,?,?,?,?,?,?,?,?,?)",
                [(observer.iso(START), skill, kind, model, 100, 20, .01, seconds, PRIVATE, PRIVATE)] * count)

    def observe(self, **kw):
        return observer.snapshot(self.db, START, START + timedelta(hours=1),
                                 now=START + timedelta(hours=2), services=False, **kw)

    def test_read_only_database_and_files_remain_exact(self):
        self.add_usage()
        before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.root.iterdir()}
        report = self.observe()
        after = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.root.iterdir()}
        self.assertEqual(before, after)
        self.assertEqual(report["application_mutations"], 0)
        conn = observer.readonly_connection(self.db)
        try:
            for statement in ("DELETE FROM conversation", "CREATE TABLE injected(x)",
                              "PRAGMA query_only=OFF", "ATTACH ':memory:' AS extra",
                              "SELECT text FROM conversation", "SELECT private_body FROM llm_usage",
                              "SELECT message FROM trace_events", "SELECT params_json FROM reply_outbox"):
                with self.subTest(statement=statement), self.assertRaises(sqlite3.DatabaseError):
                    conn.execute(statement)
        finally:
            conn.close()

    def test_private_columns_and_unrecognized_metadata_are_never_exported(self):
        self.add_usage(skill=PRIVATE, kind=PRIVATE, model=PRIVATE)
        with database(self.db) as conn:
            conn.execute("INSERT INTO issues VALUES(?,?,?,?,?)", (observer.iso(START), PRIVATE, PRIVATE, PRIVATE, PRIVATE))
        result = json.dumps(self.observe())
        self.assertNotIn(PRIVATE, result)
        self.assertNotIn(str(self.db), result)
        self.assertNotIn("private_body", result)
        self.assertIn("unrecognized", result)

    def test_cohorts_accounting_and_latency_withholding(self):
        self.add_usage(count=19)
        self.add_usage(skill="healthcheck", seconds=.01)
        self.add_usage(skill="assistant_qualification_deadbeef", seconds=.02)
        self.add_usage(skill="test_fixture", seconds=.03)
        self.add_usage(kind="unknown_chat", seconds=999)
        self.add_usage(kind="rejected_chat", seconds=888)
        self.add_usage(skill="stt", kind="stt", model="whisper.cpp-server", seconds=600)
        report = self.observe()["usage"]
        self.assertEqual(set(report["totals_by_cohort"]), {"all", "ordinary", "healthcheck", "qualification", "test"})
        self.assertEqual(report["ordinary_chat_call_latency"]["sample_count"], 19)
        self.assertIsNone(report["ordinary_chat_call_latency"]["p95_seconds"])
        self.assertEqual(report["ordinary_chat_call_latency"]["median_seconds"], 2)
        unknown = [group for group in report["groups"] if group["accounting"] == "conservative_unknown"]
        self.assertEqual(unknown[0]["cost_usd"], .01)
        self.assertEqual(unknown[0]["call_latency"]["sample_count"], 0)
        self.add_usage(seconds=3)
        self.assertEqual(self.observe()["usage"]["ordinary_chat_call_latency"]["p95_seconds"], 2)

    def test_window_offsets_and_exclusive_until(self):
        self.add_usage()
        with database(self.db) as conn:
            conn.execute("INSERT INTO llm_usage VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("2026-10-25T16:00:00+03:00", "converse", "chat", "deepseek-4-flash", 1, 1, .01, 1, PRIVATE, PRIVATE))
            conn.execute("UPDATE llm_usage SET ts='2026-10-25T15:00:00+03:00' WHERE model='claude-sonnet-5-5'")
        self.assertEqual(self.observe()["usage"]["totals_by_cohort"]["all"]["calls"], 1)

    def test_metadata_counts_and_processing_latency_scope(self):
        result = self.observe()
        self.assertEqual(result["fallback_events"]["llm.failover_served"], 1)
        self.assertEqual(result["fallback_events"]["llm.failover_failed"], 1)
        self.assertEqual(result["fallback_events_by_cohort"]["ordinary"]["llm.failover_failed"], 1)
        self.assertEqual(result["outbox_current"]["oldest_pending_age_seconds"], 7200)
        self.assertEqual(result["inbound"]["processing_latency"]["median_seconds"], 5)
        self.assertIn("not_background", result["inbound"]["latency_scope"])

    def test_missing_tables_or_columns_produce_fixed_gaps(self):
        with database(self.db) as conn:
            conn.execute("DROP TABLE llm_usage")
            conn.execute("DROP TABLE traces")
            conn.execute("CREATE TABLE traces(unrelated TEXT)")
        result = self.observe()
        self.assertIsNone(result["usage"])
        self.assertEqual(result["gaps"], ["llm_usage_missing_or_incompatible", "traces_missing_or_incompatible"])

    def test_services_use_only_fixed_commands_and_sanitize_values(self):
        commands = []

        def runner(command, **options):
            commands.append(command)
            self.assertNotIn("shell", options)
            self.assertEqual(options["stderr"], subprocess.DEVNULL)
            self.assertEqual(options["timeout"], 5)
            return subprocess.CompletedProcess(command, 0, "ActiveState=active\nMainPID=123\nNRestarts=0\nIgnored=" + PRIVATE)

        result = observer.service_snapshot(runner)
        self.assertEqual([command[2] for command in commands], list(observer.SERVICES))
        self.assertTrue(all(command[0] == "/usr/bin/systemctl" for command in commands))
        self.assertEqual(result[0]["pid"], 123)
        self.assertNotIn(PRIVATE, json.dumps(result))

    def test_exact_trial_ids_and_conservative_cost_wording(self):
        for model in ("anthropic-claude-5.5-haiku", "anthropic-claude-sonnet-5.5", "openai-gpt-6-1-sol"):
            self.add_usage(model=model)
        usage = self.observe()["usage"]
        self.assertEqual({group["model"] for group in usage["groups"]},
            {"anthropic-claude-5.5-haiku", "anthropic-claude-sonnet-5.5", "openai-gpt-6-1-sol"})
        self.assertIn("not_provider_bill", usage["cost_semantics"])

    def test_live_wal_is_read_without_database_or_wal_changes(self):
        writer = sqlite3.connect(self.db)
        try:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("INSERT INTO llm_usage VALUES(?,?,?,?,?,?,?,?,?,?)",
                (observer.iso(START), "converse", "chat", "deepseek-4-flash", 1, 1, .01, 1, PRIVATE, PRIVATE))
            writer.commit()
            files = [self.db, Path(str(self.db) + "-wal")]
            before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
            self.assertEqual(self.observe()["usage"]["totals_by_cohort"]["all"]["calls"], 1)
            after = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
            self.assertEqual(before, after)
        finally:
            writer.close()

    def test_binding_and_future_window_are_rejected(self):
        with self.assertRaisesRegex(observer.ObserverError, "invalid_binding_sha256"):
            self.observe(binding_sha256=PRIVATE)
        with self.assertRaisesRegex(observer.ObserverError, "invalid_observation_window"):
            observer.snapshot(self.db, START, START + timedelta(hours=3), now=START, services=False)

    def test_atomic_unique_output_never_overwrites(self):
        path = self.root / "report.json"
        observer.write_unique(path, self.observe())
        original = path.read_bytes()
        with self.assertRaisesRegex(observer.ObserverError, "output_already_exists"):
            observer.write_unique(path, {"replacement": True})
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(self.root.glob(".observation-*")), [])

    def test_absolute_72_hour_final_and_idempotent_schedule(self):
        self.add_usage()
        end = START + timedelta(hours=72)
        result = observer.scheduled_snapshot(self.db, START, self.root, now=end + timedelta(hours=1), services=False)
        self.assertTrue(result["timer_stop_required"])
        report = json.loads((self.root / "final-072h.json").read_text())
        self.assertEqual(report["until_exclusive"], observer.iso(end))
        self.assertEqual(report["schedule"]["absolute_end"], observer.iso(end))
        self.assertEqual(report["review_status"], "insufficient_ordinary_traffic")
        self.assertFalse(report["automatic_reversion"])
        with patch.object(observer, "snapshot", side_effect=AssertionError("must not read again")):
            repeated = observer.scheduled_snapshot(self.db, START, self.root, now=end + timedelta(days=5), services=False)
        self.assertEqual(repeated["status"], "already_exists")

    def test_slot_idempotence_and_not_started_deadline(self):
        with self.assertRaisesRegex(observer.ObserverError, "trial_not_started"):
            observer.scheduled_snapshot(self.db, START, self.root, now=START - timedelta(seconds=1), services=False)
        result = observer.scheduled_snapshot(self.db, START, self.root, now=START + timedelta(hours=6), services=False)
        self.assertFalse(result["final"])
        self.assertTrue((self.root / "observation-006h.json").exists())
        with patch.object(observer, "snapshot", side_effect=AssertionError("duplicate slot")):
            observer.scheduled_snapshot(self.db, START, self.root, now=START + timedelta(hours=7), services=False)

    def test_cli_errors_do_not_disclose_paths(self):
        from contextlib import redirect_stdout
        import io
        out = io.StringIO()
        with redirect_stdout(out):
            status = observer.main(["snapshot", "--db", str(self.root / PRIVATE),
                "--since", observer.iso(START), "--until", observer.iso(START),
                "--output", str(self.root / "not-written.json"), "--skip-services"])
        self.assertEqual(status, 2)
        self.assertNotIn(PRIVATE, out.getvalue())
        self.assertFalse((self.root / "not-written.json").exists())


if __name__ == "__main__":
    unittest.main()
