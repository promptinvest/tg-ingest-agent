"""Offline regressions for responsive maintenance and explicit recovery rehearsal."""
import hashlib
import json
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import backup
import call_budget
import jobs
import inference_io
import llm
import memory_curator
import runtime
import store
import tg_ingest_agent
from testlib import make_config


class BackgroundMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_config(DB_PATH=str(Path(self.tmp.name) / "test.db"))
        self.conn = store.open_db(self.cfg.db_path)
        self.ctx = SimpleNamespace()
        self.saved_handlers = dict(runtime._HANDLERS)
        self.releases = []

    def tearDown(self):
        for release in self.releases:
            release.set()
        end = time.monotonic() + 3
        while runtime.has_pending(self.ctx) and time.monotonic() < end:
            runtime.poll(self.conn, self.ctx)
            threading.Event().wait(.002)
        runtime._HANDLERS.clear()
        runtime._HANDLERS.update(self.saved_handlers)
        self.conn.close()
        self.tmp.cleanup()

    def pump(self):
        end = time.monotonic() + 3
        while runtime.has_pending(self.ctx) and time.monotonic() < end:
            runtime.poll(self.conn, self.ctx)
            threading.Event().wait(.002)
        self.assertFalse(runtime.has_pending(self.ctx), "background result never settled")

    def block(self):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)

        def work():
            entered.set()
            if not release.wait(3):
                raise RuntimeError("fixture worker release missing")
            return {"finished": True}
        return work, entered, release

    def test_blocked_backup_does_not_block_owner_database_or_claim_another_job(self):
        work, entered, release = self.block()
        settled = []
        owner = threading.get_ident()
        runtime.register("test", "slow", lambda *args: runtime.BackgroundJob(
            work, finish=lambda result: settled.append(threading.get_ident()) or result))
        jid = jobs.add_job(self.conn, "test", "slow")
        other = jobs.add_job(self.conn, "test", "slow")
        self.assertEqual(runtime.drain(self.conn, self.ctx), 1)
        self.assertTrue(entered.wait(1))
        # This is owner work while the maintenance worker is held indefinitely.
        store.convo_add(self.conn, 1, "user", "Покажи напоминания")
        self.assertEqual(self.conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()[0], "claimed")
        self.assertEqual(runtime.drain(self.conn, self.ctx), 0)
        self.assertEqual(self.conn.execute("SELECT status FROM jobs WHERE id=?", (other,)).fetchone()[0], "pending")
        self.assertEqual(settled, [])
        release.set()
        self.pump()
        self.assertEqual(settled, [owner])
        self.assertEqual(self.conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()[0], "done")

    def test_worker_failure_retains_existing_retry_backoff_and_owner_issue(self):
        def work():
            raise RuntimeError("fixture backup failed")
        runtime.register("test", "failure", lambda *args: runtime.BackgroundJob(work))
        jid = jobs.add_job(self.conn, "test", "failure", max_attempts=1)
        runtime.drain(self.conn, self.ctx)
        self.pump()
        row = self.conn.execute("SELECT status,error FROM jobs WHERE id=?", (jid,)).fetchone()
        self.assertEqual(row["status"], "failed")
        self.assertIn("fixture backup failed", row["error"])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM issues WHERE kind='job_failed'").fetchone()[0], 1)

    def test_background_snapshot_uses_its_own_readonly_connection_and_owner_settlement(self):
        owner = threading.get_ident()
        observed = []

        def run(cfg, source, **options):
            observed.append((threading.get_ident(), source.execute("PRAGMA query_only").fetchone()[0]))
            with self.assertRaises(sqlite3.OperationalError):
                source.execute("INSERT INTO kv(key,value) VALUES('forbidden','worker')")
            options["events"]["issues"].append(("backup_test", "worker outcome"))
            return {"file": "fixture", "offsite": "not-due"}

        settled = []
        with mock.patch.object(backup, "run", side_effect=run):
            session = backup.start_background(self.cfg, self.conn, offsite_due=False,
                finish=lambda value: settled.append(threading.get_ident()) or value)
            runtime.register("test", "backup", lambda *args: session)
            jobs.add_job(self.conn, "test", "backup")
            runtime.drain(self.conn, self.ctx)
            self.pump()
        self.assertNotEqual(observed[0][0], owner)
        self.assertEqual(observed[0][1], 1)
        self.assertEqual(settled, [owner])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM issues WHERE kind='backup_test'").fetchone()[0], 1)

    def test_background_verify_failure_records_original_issue_on_owner(self):
        def verify(cfg, **options):
            options["events"]["issues"].append(("backup_restore_failed", "bad archive"))
            raise backup.BackupRestoreError("bad archive")
        failures = []
        with mock.patch.object(backup, "verify_restore", side_effect=verify):
            runtime.register("test", "verify", lambda *args: backup.start_verify_background(
                self.cfg, self.conn, failed=lambda error: failures.append(threading.get_ident())))
            jobs.add_job(self.conn, "test", "verify", max_attempts=1)
            runtime.drain(self.conn, self.ctx)
            self.pump()
        self.assertEqual(failures, [threading.get_ident()])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM issues WHERE kind='backup_restore_failed'").fetchone()[0], 1)

    def _inference(self, transport):
        def steps():
            reply = yield {"messages": [{"role": "user", "content": "fixture"}],
                           "profile": "memory_consolidate"}
            return {"reply": reply}

        def prepare(cfg, conn, skill, messages, **options):
            reservation = store.usage_add(conn, skill, "unknown_chat", "fixture", cost_usd=.01)
            return {"reserved": reservation, "messages": messages}

        def finish(cfg, conn, prepared, data=None, error=None, **options):
            self.assertEqual(threading.get_ident(), owner)
            if error:
                wrapped = llm.LLMError("fixture ambiguous completion")
                wrapped.usage_unknown = True
                raise wrapped
            call_budget.settle(conn, prepared["reserved"], "chat", cost_usd=.005)
            return data

        owner = threading.get_ident()
        runtime.register("test", "inference", lambda *args: runtime.InferenceJob(
            self.cfg, self.conn, steps()))
        jid = jobs.add_job(self.conn, "test", "inference")
        with mock.patch.object(llm, "prepare_chat", side_effect=prepare), \
                mock.patch.object(llm, "finish_chat", side_effect=finish), \
                mock.patch.object(llm, "transport_chat", side_effect=transport) as send:
            runtime.drain(self.conn, self.ctx)
            self.pump()
        return jid, send.call_count

    def test_inference_transport_and_accounting_use_different_threads(self):
        owner = threading.get_ident()
        threads = []
        jid, calls = self._inference(lambda prepared: threads.append(threading.get_ident()) or "{}")
        self.assertEqual(calls, 1)
        self.assertNotEqual(threads, [owner])
        self.assertEqual(self.conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()[0], "done")
        self.assertEqual(self.conn.execute("SELECT kind FROM llm_usage").fetchone()[0], "chat")

    def test_ambiguous_inference_is_terminal_without_profile_or_job_retry(self):
        def transport(prepared):
            raise TimeoutError("fixture completion lost")
        jid, calls = self._inference(transport)
        self.assertEqual(calls, 1)
        self.assertEqual(self.conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()[0], "failed")
        self.assertEqual(self.conn.execute("SELECT kind FROM llm_usage").fetchone()[0], "unknown_chat")
        self.assertEqual(runtime.reclaim_stale(self.conn), (0, 0))

    def test_shared_background_capacity_blocks_before_reserving_memory_cost(self):
        def steps():
            yield {"messages": [], "profile": "memory_consolidate"}
        lease = inference_io.BackgroundLease()
        self.assertTrue(lease.acquire())
        try:
            session = runtime.InferenceJob(self.cfg, self.conn, steps())
            session.job_id = 1
            with mock.patch.object(llm, "prepare_chat") as prepare:
                self.assertFalse(session.advance())
            prepare.assert_not_called()
            self.assertEqual(self.conn.execute("SELECT count(*) FROM llm_usage").fetchone()[0], 0)
        finally:
            lease.release_if_idle()

    def test_timed_out_physical_transport_keeps_background_slot_until_socket_finishes(self):
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()
        self.releases.append(release)

        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                closed.set()
            def read(self):
                entered.set()
                release.wait(3)
                return b"{}"

        lease = inference_io.BackgroundLease()
        self.assertTrue(lease.acquire())
        try:
            with self.assertRaises(llm.LLMError):
                lease.run(inference_io.json_request, lambda *a, **k: Response(),
                          object(), .02, llm.LLMError)
            self.assertTrue(entered.is_set())
            self.assertFalse(inference_io.background_slots.acquire(blocking=False))
        finally:
            release.set()
            self.assertTrue(closed.wait(1))
        end = time.monotonic() + 1
        acquired = False
        while not acquired and time.monotonic() < end:
            acquired = inference_io.background_slots.acquire(blocking=False)
            if not acquired:
                threading.Event().wait(.002)
        self.assertTrue(acquired)
        inference_io.background_slots.release()

    def test_restart_contains_only_dispatched_unknown_inference(self):
        unknown = jobs.add_job(self.conn, "test", "unknown")
        ordinary = jobs.add_job(self.conn, "test", "ordinary")
        jobs.claim_next(self.conn)
        jobs.claim_next(self.conn)
        self.conn.execute("UPDATE jobs SET result=? WHERE id=?", (
            json.dumps({"background_inference": "started", "reservation_id": 7}), unknown))
        self.conn.commit()
        self.assertEqual(runtime.reclaim_stale(self.conn), (1, 1))
        self.assertEqual(self.conn.execute("SELECT status FROM jobs WHERE id=?", (unknown,)).fetchone()[0], "failed")
        self.assertEqual(self.conn.execute("SELECT status FROM jobs WHERE id=?", (ordinary,)).fetchone()[0], "pending")

    def test_definite_error_phase_write_failure_keeps_consumed_transport(self):
        session = runtime.InferenceJob(self.cfg, self.conn, iter(()))
        session.started, session.job_id = True, 7
        session.prepared = {"reserved": 9}
        session.operation = {"kind": "chat", "options": {"meta": {}}}
        worker = runtime.BackgroundJob(lambda: None)
        worker.started = worker.done = True
        worker.error = RuntimeError("definite rejected request")
        session.worker = worker
        definite = llm.LLMError("fixture definite rejection")
        with mock.patch.object(llm, "finish_chat", side_effect=definite), \
                mock.patch.object(session, "_phase", side_effect=sqlite3.OperationalError("busy")), \
                mock.patch.object(llm, "prepare_chat") as prepare:
            with self.assertRaises(sqlite3.OperationalError):
                session.advance()
        self.assertIs(session.worker, worker)
        prepare.assert_not_called()

    def _unsent_session(self):
        def steps():
            yield {"messages": [{"role": "user", "content": "fixture"}],
                   "profile": "memory_consolidate"}
        return runtime.InferenceJob(self.cfg, self.conn, steps())

    def _reserve_for_test(self, cfg, conn, skill, messages, **options):
        reservation = store.usage_add(conn, skill, "unknown_chat", "fixture", cost_usd=.01)
        return {"reserved": reservation, "messages": messages}

    def test_phase_start_write_failure_releases_unsent_reservation_without_redispatch(self):
        session = self._unsent_session()
        runtime.register("test", "unsent", lambda *args: session)
        jid = jobs.add_job(self.conn, "test", "unsent")
        with mock.patch.object(llm, "prepare_chat", side_effect=self._reserve_for_test) as prepare, \
                mock.patch.object(session, "_phase", side_effect=sqlite3.OperationalError("fixture phase write outage")), \
                mock.patch.object(llm, "transport_chat") as transport:
            self.assertEqual(runtime.drain(self.conn, self.ctx), 1)
            self.assertEqual(runtime.poll(self.conn, self.ctx), 0)
            self.assertEqual(runtime.drain(self.conn, self.ctx), 0)
        self.assertEqual(prepare.call_count, 1)
        transport.assert_not_called()
        self.assertFalse(runtime.has_pending(self.ctx))
        self.assertEqual(self.conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()[0], "failed")
        rows = self.conn.execute("SELECT kind,cost_usd FROM llm_usage").fetchall()
        self.assertEqual([tuple(row) for row in rows], [("rejected_chat", 0)])

    def test_phase_start_cleanup_outage_keeps_job_claimed_and_never_prepares_again(self):
        session = self._unsent_session()
        runtime.register("test", "unsent-cleanup", lambda *args: session)
        jid = jobs.add_job(self.conn, "test", "unsent-cleanup")
        with mock.patch.object(llm, "prepare_chat", side_effect=self._reserve_for_test) as prepare, \
                mock.patch.object(session, "_phase", side_effect=sqlite3.OperationalError("fixture phase write outage")), \
                mock.patch.object(llm, "transport_chat") as transport:
            with mock.patch.object(call_budget, "settle", side_effect=sqlite3.OperationalError("fixture cleanup outage")):
                with self.assertRaises(sqlite3.Error):
                    runtime.drain(self.conn, self.ctx)
                self.assertTrue(session.done)
                self.assertTrue(runtime.has_pending(self.ctx))
                self.assertEqual(self.conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()[0], "claimed")
                with self.assertRaises(sqlite3.Error):
                    runtime.poll(self.conn, self.ctx)
            self.assertEqual(runtime.poll(self.conn, self.ctx), 1)
            self.assertEqual(prepare.call_count, 1)
        transport.assert_not_called()
        self.assertFalse(runtime.has_pending(self.ctx))
        self.assertEqual(self.conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()[0], "failed")
        self.assertEqual(self.conn.execute("SELECT kind FROM llm_usage").fetchone()[0], "rejected_chat")

    def test_unclaimed_job_cannot_authorize_a_prepared_transport(self):
        session = self._unsent_session()
        session.job_id = jobs.add_job(self.conn, "test", "not-claimed")
        with mock.patch.object(llm, "prepare_chat", side_effect=self._reserve_for_test) as prepare, \
                mock.patch.object(llm, "transport_chat") as transport:
            self.assertTrue(session.advance())
            self.assertTrue(session.advance())
        self.assertIsInstance(session.error, runtime.StaleBackgroundJob)
        self.assertEqual(prepare.call_count, 1)
        transport.assert_not_called()
        self.assertEqual(self.conn.execute("SELECT kind FROM llm_usage").fetchone()[0], "rejected_chat")
        self.assertIsNone(self.conn.execute("SELECT result FROM jobs WHERE id=?", (session.job_id,)).fetchone()[0])

    def test_background_queue_wait_does_not_start_initial_profile_deadline(self):
        session = self._unsent_session()
        session.job_id = jobs.add_job(self.conn, "test", "queued-profile")
        lease = inference_io.BackgroundLease()
        self.assertTrue(lease.acquire())
        clock = {"now": 100.0}
        original = llm.profile_steps
        try:
            with mock.patch.object(runtime.time, "monotonic", lambda: clock["now"]), \
                    mock.patch.object(llm, "profile_steps", wraps=original) as profiles, \
                    mock.patch.object(llm, "prepare_chat", side_effect=self._reserve_for_test) as prepare, \
                    mock.patch.object(session, "_phase", side_effect=sqlite3.OperationalError("fixture stop before dispatch")), \
                    mock.patch.object(llm, "transport_chat") as transport:
                self.assertFalse(session.advance())
                clock["now"] += 1000
                self.assertFalse(session.advance())
                profiles.assert_not_called()
                prepare.assert_not_called()
                lease.release_if_idle()
                self.assertTrue(session.advance())
                self.assertEqual(profiles.call_count, 1)
                timeout = float(llm.profiles(self.cfg)["memory_consolidate"]["timeout"])
                self.assertEqual(prepare.call_args.kwargs["timeout"], timeout)
                transport.assert_not_called()
        finally:
            lease.release_if_idle()

    def test_expired_queued_retry_keeps_original_profile_budget_and_never_dispatches(self):
        session = self._unsent_session()
        session.job_id = jobs.add_job(self.conn, "test", "expired-retry")
        self.cfg.llm_profiles_json = json.dumps({"memory_consolidate": {
            "primary": "fixture-primary", "fallbacks": ["fixture-fallback"],
            "max_tokens": 100, "json_required": True, "timeout": 60}})
        clock = {"now": 100.0}
        lease = inference_io.BackgroundLease()
        self.assertTrue(lease.acquire())
        try:
            with mock.patch.object(runtime.time, "monotonic", lambda: clock["now"]), \
                    mock.patch.object(llm, "prepare_chat") as prepare, \
                    mock.patch.object(llm, "transport_chat") as transport:
                session._outer()
                session.profile = llm.profile_steps(self.cfg, self.conn, "memory_curator",
                    session.request["messages"], profile="memory_consolidate", retry_unknown=False)
                session._profile(first=True)
                self.assertEqual(session.operation_deadline, 160)
                # A definite first-attempt rejection chooses the fallback under
                # the SAME 90-second profile budget, with only 50 seconds left.
                clock["now"] = 140
                session._profile(error=llm.LLMError("HTTP 400 fixture rejection"))
                self.assertEqual(session.operation_deadline, 190)
                self.assertFalse(session.advance())
                clock["now"] = 195
                lease.release_if_idle()
                self.assertTrue(session.advance())
                clock["now"] = 196
                self.assertTrue(session.advance())
                clock["now"] = 197
                self.assertTrue(session.advance())
                self.assertTrue(session.error.request_not_sent)
                prepare.assert_not_called()
                transport.assert_not_called()
                self.assertEqual(self.conn.execute("SELECT count(*) FROM llm_usage").fetchone()[0], 0)
        finally:
            lease.release_if_idle()

    def test_waiting_retry_subtracts_queued_time_from_prepared_timeout(self):
        session = self._unsent_session()
        session.job_id = jobs.add_job(self.conn, "test", "shorter-retry")
        clock = {"now": 100.0}
        with mock.patch.object(runtime.time, "monotonic", lambda: clock["now"]), \
                mock.patch.object(llm, "prepare_chat", side_effect=self._reserve_for_test) as prepare, \
                mock.patch.object(session, "_phase", side_effect=sqlite3.OperationalError("fixture stop before dispatch")), \
                mock.patch.object(llm, "transport_chat") as transport:
            session._outer()
            session.profile = llm.profile_steps(self.cfg, self.conn, "memory_curator",
                session.request["messages"], profile="memory_consolidate", retry_unknown=False)
            session._profile(first=True)
            initial_timeout = session.operation["options"]["timeout"]
            clock["now"] += 20
            self.assertTrue(session.advance())
            self.assertEqual(prepare.call_args.kwargs["timeout"], initial_timeout - 20)
            transport.assert_not_called()

    def test_async_memory_verdict_cannot_demote_facts_changed_during_transport(self):
        for index in range(8):
            store.boss_add(self.conn, "personal_fact", f"fixture memory {index}", status="inferred", confidence=.7)
        steps = memory_curator.consolidate_steps(self.conn, self.cfg)
        request = next(steps)
        self.assertEqual(request["profile"], "memory_consolidate")
        rows = self.conn.execute("SELECT id FROM boss_profile_items ORDER BY id").fetchall()
        self.conn.execute("UPDATE boss_profile_items SET status='confirmed' WHERE id=?", (rows[1][0],))
        self.conn.commit()
        with self.assertRaises(memory_curator.StaleConsolidation):
            steps.send(json.dumps({"groups": [{"keep": rows[0][0], "drop": [rows[1][0]]}]}))
        self.assertEqual(self.conn.execute("SELECT status FROM boss_profile_items WHERE id=?", (rows[1][0],)).fetchone()[0], "confirmed")

    def test_real_agent_backup_allows_owner_turn_and_due_alarm_before_success_stamp(self):
        self.conn.close()
        self.cfg = make_config(ALLOWED_CHAT_IDS="1", DB_PATH=str(self.cfg.db_path),
                               MEDIA_DIR=str(Path(self.tmp.name) / "media"))
        self.cfg.fleet_notify_token, self.cfg.fleet_notify_chat_id = "fixture", "1"
        agent = tg_ingest_agent.Agent(self.cfg)
        agent.background_maintenance = True
        self.conn, self.ctx = agent.conn, agent
        work, entered, release = self.block()
        sends = []

        def upload(*args, **kwargs):
            work()
            return "telegram:fleet"

        def telegram(token, method, params, **kwargs):
            sends.append(method)
            return {"message_id": len(sends) + 500}

        with mock.patch.object(backup, "encrypt_snapshot", side_effect=lambda cfg, gz: Path(str(gz) + ".enc")), \
                mock.patch.object(backup, "offsite", side_effect=upload), \
                mock.patch.object(tg_ingest_agent, "tg_call", side_effect=telegram):
            jid = jobs.add_job(self.conn, "maintenance", "db_backup")
            runtime.drain(self.conn, agent)
            self.assertTrue(entered.wait(1))
            self.assertFalse(store.kv_get(self.conn, "backup_day"))
            rid = store.reminder_add(self.conn, 1, "fixture alarm", "2020-01-01T00:00:00+00:00")
            agent.fire_due_reminders()
            self.assertTrue(store.reminder_get(self.conn, rid)["last_fired_at"])
            update = {"update_id": 95001, "message": {"message_id": 9001,
                "chat": {"id": 1}, "from": {"id": 1}, "text": "/stats"}}
            agent.process_update_batch([update])
            self.assertEqual(store.telegram_update_get(self.conn, 95001)["status"], "done")
            self.assertFalse(store.kv_get(self.conn, "backup_day"))
            self.assertEqual(self.conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()[0], "claimed")
            release.set()
            self.pump()
        self.assertTrue(store.kv_get(self.conn, "backup_day"))
        self.assertTrue(store.kv_get(self.conn, "backup_offsite_day"))
        self.assertEqual(self.conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()[0], "done")

    def test_real_agent_async_consolidation_stamps_only_after_owner_settlement(self):
        self.conn.close()
        agent = tg_ingest_agent.Agent(self.cfg)
        agent.background_maintenance = True
        self.conn, self.ctx = agent.conn, agent
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        for index in range(8):
            store.boss_add(self.conn, "personal_fact", f"fixture memory {index}", status="inferred", confidence=.7)

        def transport(prepared):
            entered.set()
            release.wait(3)
            return {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 2}}

        with mock.patch.object(llm, "transport_chat", side_effect=transport):
            jid = jobs.add_job(self.conn, "memory_curator", "consolidate")
            runtime.drain(self.conn, agent)
            self.assertTrue(entered.wait(1))
            self.assertIsInstance(agent._runtime_pending_job[2], runtime.InferenceJob)
            self.assertFalse(store.kv_get(self.conn, "memory_consolidate_at"))
            release.set()
            self.pump()
        self.assertTrue(store.kv_get(self.conn, "memory_consolidate_at"))
        self.assertEqual(self.conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()[0], "done")


class IndependentRecoveryTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("openssl"), "openssl is required for actual recovery rehearsal")
    def test_exact_supplied_artifact_restores_without_live_database_or_secret_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = make_config(DB_PATH=str(root / "test.db"),
                              BACKUP_ENCRYPTION_KEY_FILE=str(root / "independent.key"))
            cfg.backup_encryption_key_file.write_text("synthetic test passphrase\n", encoding="utf-8")
            conn = store.open_db(cfg.db_path)
            try:
                artifact = backup.encrypt_snapshot(cfg, backup.snapshot(cfg, conn))
            finally:
                conn.close()
            original = artifact.read_bytes()
            digest = hashlib.sha256(original).hexdigest()
            # There is no live database to consult during this rehearsal.
            cfg.db_path.unlink()
            result = backup.verify_independent_recovery(artifact,
                cfg.backup_encryption_key_file, digest, max_bytes=8 * 1024 * 1024)
            self.assertEqual(result["integrity"], "ok")
            self.assertFalse(result["offbox_provenance_verified"])
            self.assertEqual(artifact.read_bytes(), original)
            self.assertEqual(cfg.backup_encryption_key_file.read_text(encoding="utf-8"), "synthetic test passphrase\n")
            self.assertFalse(cfg.db_path.exists())
            with self.assertRaises(backup.BackupRestoreError):
                backup.verify_independent_recovery(artifact, cfg.backup_encryption_key_file, "0" * 64)

    def test_missing_explicit_digest_or_inputs_refuse_before_decryption(self):
        with mock.patch.object(backup, "_decrypt_snapshot") as decrypt:
            for digest in (None, "", "invalid"):
                with self.assertRaises(backup.BackupRestoreError):
                    backup.verify_independent_recovery("absent.db.gz.enc", "absent.key", digest)
        decrypt.assert_not_called()
