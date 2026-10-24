"""Offline CF06 typed-preview -> transport/accounting -> deletion continuation."""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import cara_fleet
import fleet_lifecycle as lifecycle
import fleet_lifecycle_svc
import fleet_protocol as protocol
import fleet_queue
import notes_svc
import store
import tasks_svc
import worker_client

CANARY = "SYNTHETIC_FLEET_CONTENT_CANARY"
JOB = "w_10_1_" + "b" * 32


class Agent(fleet_lifecycle_svc.FleetLifecycleMixin, notes_svc.NotesMixin, tasks_svc.TasksMixin):
    def __init__(self, conn, cfg):
        self.conn, self.cfg, self.messages = conn, cfg, []
        self._fleet_inference_calls = {}
    def reply(self, _chat, text, **_kwargs):
        self.messages.append(text)
        return True


class FleetContentHandshakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.spool = self.root / "cara-fleet" / "spool"
        for name in ("requests", "results"):
            (self.spool / name).mkdir(parents=True)
        self.conn = store.open_db(self.root / "state.db")
        self.addCleanup(self.conn.close)
        worker_spool = self.root / "cara-worker" / "spool"
        for name in ("requests", "results", "cancel"):
            (worker_spool / name).mkdir(parents=True)
        self.cfg = SimpleNamespace(fleet_enabled=True, fleet_spool=str(self.spool),
                 task_worker_spool=str(worker_spool), task_artifacts_dir=str(self.root / "tg-ingest-agent" / "task-artifacts"),
                 mentor_review_spool=str(self.root / "cara-mentor" / "spool"),
                 mentor_runner_spool=str(self.root / "cara-mentor-runner" / "spool"))
        self.agent = Agent(self.conn, self.cfg)
        self.conn.execute("INSERT INTO fleet_requests VALUES(?,?,?,?,?,?,?)", ("c" * 32, 1, 10, "inspection_started", json.dumps({"question": CANARY}), protocol.now(), protocol.now()))
        self.conn.execute("INSERT INTO fleet_events(request_id,kind,payload,created_at) VALUES(?,?,?,?)", ("c" * 32, "report", json.dumps({"answer": CANARY}), protocol.now()))
        self.conn.commit()
        fleet_queue.publish_ready(self.spool)

    def authorize(self):
        self.agent.do_purge(1, "en", {"scope": "all"})
        pending = store.pending_get(self.conn, 1)
        self.agent.resolve_purge(1, "en", pending, pending["payload"]["phrase"])
        return lifecycle._authorization(self.conn)["nonce"]

    def request(self):
        body = {"job_id": JOB, "nonce": "b" * 32, "question": CANARY}
        worker_client._atomic_publish(self.spool / "requests", JOB + ".json", protocol.canonical(body).encode())

    def worker_mocks(self):
        stack = mock.patch.multiple(tasks_svc.worker_client, prepare_purge=mock.Mock(), finish_purge=mock.Mock(return_value=True), consume_purge=mock.Mock())
        return stack

    def finish_external(self, nonce):
        self.assertTrue(lifecycle.executor_gate(self.spool))
        with self.worker_mocks():
            self.assertTrue(self.agent.purge_task_external_state(nonce, timeout_seconds=0))

    def test_same_confirmed_counts_continue_without_a_second_phrase(self):
        nonce = self.authorize()
        self.assertTrue(lifecycle.admission_paused(self.conn))
        self.assertEqual(self.conn.execute("SELECT state FROM fleet_requests").fetchone()[0], "cancelled")
        self.assertIn(CANARY, self.conn.execute("SELECT payload FROM fleet_requests").fetchone()[0])
        self.assertTrue(lifecycle.executor_gate(self.spool))
        self.assertTrue(self.agent.check_fleet_content_purge())
        self.assertEqual(lifecycle.db_preview(self.conn), {})
        auth = lifecycle._authorization(self.conn, nonce)
        self.assertEqual(auth["phase"], "db_committed")
        self.assertTrue((self.spool / "requests" / ".purge-all.json").exists())
        self.finish_external(nonce)
        self.assertFalse(lifecycle.admission_paused(self.conn))
        self.assertFalse((self.spool / "requests" / ".purge-all.json").exists())
        self.assertEqual(sum("send exactly this phrase" in text for text in self.agent.messages), 1)

    def test_cancelled_transport_counts_refresh_then_same_nonce_deletes_only_after_phrase(self):
        self.request()
        token_root = self.spool.parent / "consumed"; token_root.mkdir()
        token = token_root / ("d" * 32); token.write_text("e" * 64)
        result = self.spool / "results" / (JOB + ".json"); result.write_text(CANARY)
        nonce = self.authorize()
        self.assertTrue((self.spool / "requests" / (".cancel-" + JOB + ".json")).exists())
        lifecycle.executor_gate(self.spool)
        self.assertFalse(self.agent.check_fleet_content_purge())
        pending = store.pending_get(self.conn, 1)
        self.assertEqual(pending["payload"]["fleet_purge_nonce"], nonce)
        self.assertEqual(pending["payload"]["anticipated"]["fleet_transport_copies"], 3)
        self.assertIn(CANARY, self.conn.execute("SELECT payload FROM fleet_requests").fetchone()[0])
        self.assertIn(CANARY, result.read_text())
        # A changed preview requires a new bound executor ACK, not an old ACK.
        self.agent.resolve_purge(1, "en", pending, pending["payload"]["phrase"])
        self.assertTrue(lifecycle.db_preview(self.conn))
        lifecycle.executor_gate(self.spool)
        self.assertTrue(self.agent.check_fleet_content_purge())
        self.assertTrue(result.exists())  # deletion is post-commit executor-owned
        self.finish_external(nonce)
        self.assertEqual(lifecycle.transport_files(self.cfg), [])
        self.assertEqual(token.read_text(), "e" * 64)

    def test_live_model_handle_must_settle_original_accounting_before_delete(self):
        call = mock.Mock(); call.advance.return_value = False
        self.agent._fleet_inference_calls = {"request": call}
        nonce = self.authorize(); lifecycle.executor_gate(self.spool)
        self.assertFalse(self.agent.check_fleet_content_purge())
        self.assertTrue(lifecycle.db_preview(self.conn))
        self.assertIn("request", self.agent._fleet_inference_calls)
        call.advance.return_value = True
        self.assertTrue(self.agent.check_fleet_content_purge())
        self.assertEqual(self.agent._fleet_inference_calls, {})
        self.assertEqual(lifecycle._authorization(self.conn, nonce)["fleet_quiescence"]["async"], {"status": "settled", "pending_calls": 0})

    def test_malformed_or_pid_source_preview_mismatched_ack_never_deletes(self):
        nonce = self.authorize(); lifecycle.executor_gate(self.spool)
        path = self.spool / "results" / ("purge_" + nonce + ".ack.json")
        original = json.loads(path.read_text())
        for key, value in (("pid", os.getpid()+100), ("nonce", "f"*32), ("preview_hash", "f"*64), ("implementation_hash", "f"*64)):
            bad = dict(original, **{key: value}); path.write_text(protocol.canonical(bad))
            self.assertFalse(self.agent.check_fleet_content_purge())
            self.assertTrue(lifecycle.db_preview(self.conn))
        path.write_text("{broken")
        with self.assertRaises(lifecycle.FleetLifecycleError):
            self.agent.check_fleet_content_purge()
        self.assertTrue(lifecycle.db_preview(self.conn))

    def test_prepared_intent_survives_restart_without_replaying_owner_update(self):
        nonce = self.authorize()
        recovered = Agent(self.conn, self.cfg)
        self.assertFalse(recovered.recover_pending_task_purge(timeout_seconds=0))
        self.assertEqual(lifecycle._authorization(self.conn)["nonce"], nonce)
        lifecycle.executor_gate(self.spool)
        self.assertTrue(recovered.check_fleet_content_purge())
        self.assertEqual(lifecycle.db_preview(self.conn), {})
        self.assertFalse(store.pending_get(self.conn, 1))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM telegram_updates").fetchone()[0], 0)

    def test_expired_renewed_card_cancels_old_jobs_releases_new_admission_preserves_content(self):
        self.request(); nonce = self.authorize(); lifecycle.executor_gate(self.spool)
        self.agent.check_fleet_content_purge()
        store.pending_clear(self.conn, 1)  # existing expiry sweep / explicit discard
        self.assertTrue(self.agent.check_fleet_content_purge())
        self.assertFalse(lifecycle.admission_paused(self.conn))
        self.assertFalse((self.spool / "requests" / (JOB + ".json")).exists())
        self.assertFalse((self.spool / "requests" / ".purge-all.json").exists())
        self.assertIn(CANARY, self.conn.execute("SELECT payload FROM fleet_requests").fetchone()[0])
        self.assertEqual(self.conn.execute("SELECT state FROM fleet_requests").fetchone()[0], "cancelled")

    def test_declining_renewed_phrase_preserves_content_and_does_not_replay_old_jobs(self):
        self.request(); nonce = self.authorize(); lifecycle.executor_gate(self.spool)
        self.agent.check_fleet_content_purge()
        pending = store.pending_get(self.conn, 1)
        self.agent.resolve_purge(1, "en", pending, "cancel")
        self.assertFalse(lifecycle.admission_paused(self.conn))
        self.assertTrue(lifecycle.db_preview(self.conn))
        self.assertFalse((self.spool / "requests" / (JOB + ".json")).exists())

    def test_crash_after_external_done_commit_recovers_held_marker(self):
        nonce = self.authorize(); lifecycle.executor_gate(self.spool); self.agent.check_fleet_content_purge()
        lifecycle.executor_gate(self.spool)
        with mock.patch.object(lifecycle, "release_hold", side_effect=OSError("synthetic interrupted release")):
            with self.assertRaises(OSError):
                self.agent.purge_fleet_external_state(nonce, timeout_seconds=0)
        self.assertEqual(lifecycle._authorization(self.conn)["fleet_phase"], "external_done")
        self.assertTrue((self.spool / "requests" / ".purge-all.json").exists())
        recovered = Agent(self.conn, self.cfg)
        with self.worker_mocks():
            self.assertTrue(recovered.purge_task_external_state(nonce, timeout_seconds=0))
        self.assertFalse((self.spool / "requests" / ".purge-all.json").exists())
        self.assertFalse(lifecycle.admission_paused(self.conn))

    def test_executor_malformed_marker_holds_without_any_dispatch_or_ack(self):
        (self.spool / "requests" / ".purge-all.json").write_text("{malformed")
        with mock.patch.object(cara_fleet, "load", return_value={}), mock.patch.object(cara_fleet.protocol, "inventory", return_value={}), mock.patch.object(cara_fleet, "execute") as dispatch:
            cara_fleet.run_once(root=self.spool.parent, config=self.root / "config")
        dispatch.assert_not_called()
        self.assertFalse(list((self.spool / "results").glob("purge_*.ack.json")))

    def test_real_update_anticipation_continues_after_inbound_done_without_replay(self):
        # Reproduce the real dispatcher ordering, including the still-pending
        # confirmation inbox copy that the original typed flow anticipates.
        store.telegram_update_receive(self.conn, {"update_id": 501, "text": CANARY}, chat_id=1)
        store.convo_add(self.conn, 1, "user", "purge all", update_id=501)
        self.agent._current_update_id = 501
        self.agent.do_purge(1, "en", {"scope": "all"})
        pending = store.pending_get(self.conn, 1)
        self.assertEqual(pending["payload"]["anticipated"]["conversation"], 2)
        self.assertEqual(pending["payload"]["anticipated"]["updates_scrubbed"], 2)
        store.telegram_update_done(self.conn, 501)
        store.telegram_update_receive(self.conn, {"update_id": 502, "text": pending["payload"]["phrase"]}, chat_id=1)
        store.convo_add(self.conn, 1, "user", pending["payload"]["phrase"], update_id=502)
        self.agent._current_update_id = 502
        self.agent.resolve_purge(1, "en", pending, pending["payload"]["phrase"])
        auth = lifecycle._authorization(self.conn)
        self.assertEqual(auth["fleet_owner"]["source_update"], 502)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM conversation").fetchone()[0], 2)
        store.telegram_update_done(self.conn, 502)
        self.agent._current_update_id = None
        lifecycle.executor_gate(self.spool)
        self.assertTrue(self.agent.check_fleet_content_purge())
        rows = self.conn.execute("SELECT update_id,payload,status FROM telegram_updates ORDER BY update_id").fetchall()
        self.assertEqual([tuple(row) for row in rows], [(501, "{}", "done"), (502, "{}", "done")])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM conversation").fetchone()[0], 0)
        self.assertIsNone(store.pending_get(self.conn, 1))
        self.assertEqual(sum("send exactly this phrase" in text for text in self.agent.messages), 1)

    def test_late_result_copy_after_quiescent_ack_renews_exact_preview_preserving_content(self):
        nonce = self.authorize(); lifecycle.executor_gate(self.spool)
        path = self.spool / "results" / (JOB + ".json"); path.write_text(CANARY)
        self.assertFalse(self.agent.check_fleet_content_purge())
        pending = store.pending_get(self.conn, 1)
        self.assertEqual(pending["payload"]["fleet_purge_nonce"], nonce)
        self.assertEqual(pending["payload"]["anticipated"]["fleet_transport_copies"], 1)
        self.assertEqual(path.read_text(), CANARY)
        self.assertTrue(lifecycle.db_preview(self.conn))

    def test_group_writable_executor_ack_is_never_trusted(self):
        nonce = self.authorize(); lifecycle.executor_gate(self.spool)
        path = self.spool / "results" / ("purge_" + nonce + ".ack.json")
        path.chmod(0o660)
        with self.assertRaises(lifecycle.FleetLifecycleError):
            self.agent.check_fleet_content_purge()
        self.assertTrue(lifecycle.db_preview(self.conn))

    def test_disabled_empty_legacy_path_avoids_spool_but_missing_enabled_readiness_fails_closed(self):
        self.conn.execute("DELETE FROM fleet_events"); self.conn.execute("DELETE FROM fleet_requests"); self.conn.commit()
        legacy = Agent(self.conn, SimpleNamespace(fleet_enabled=False))
        self.assertEqual(legacy.fleet_purge_preview(), {})
        self.assertEqual(legacy.prepare_fleet_purge(1, "en", "a"*32, {}), "ready")
        self.cfg.fleet_content_lifecycle = "true"
        with self.assertRaises(lifecycle.FleetLifecycleError):
            self.agent.fleet_purge_preview()
        self.cfg.fleet_content_lifecycle = True
        (self.spool / "results" / fleet_queue.READY_FILE).unlink()
        with self.assertRaises(lifecycle.FleetLifecycleError):
            lifecycle.request_quiescence(self.cfg, "a"*32, {})


if __name__ == "__main__":
    unittest.main()
