"""Typed fleet purge counts, quiescence/accounting guards and recovery fixtures."""
import gzip
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import backup
import fleet_lifecycle as lifecycle
import notes_svc
import store
import tasks_svc


NONCE = "a" * 32
JOB = "w_10_1_" + "b" * 32
SECRET = "SYNTHETIC_OWNER_CONTENT_CANARY"


class PurgeAgent(notes_svc.NotesMixin, tasks_svc.TasksMixin):
    def __init__(self, conn, cfg):
        self.conn, self.cfg, self.messages = conn, cfg, []
    def reply(self, _chat, text, **_kwargs):
        self.messages.append(text)
        return True


class FleetLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "state.db"
        self.conn = store.open_db(self.path)
        self.addCleanup(self.conn.close)
        self.spool = self.root / "cara-fleet" / "spool"
        for name in ("requests", "results"):
            (self.spool / name).mkdir(parents=True)
        self.cfg = SimpleNamespace(fleet_spool=str(self.spool), db_path=self.path)
        self.agent = PurgeAgent(self.conn, self.cfg)

    def seed(self):
        self.conn.execute("INSERT INTO fleet_requests VALUES(?,?,?,?,?,?,?)",
                          ("c" * 32, 1, 10, "queued", json.dumps({"question": SECRET, "answer": SECRET, "context": SECRET, "approval": SECRET}), "2026-10-24", "2026-10-24"))
        self.conn.execute("INSERT INTO fleet_events(request_id,kind,payload,created_at) VALUES(?,?,?,?)",
                          ("c" * 32, "report", json.dumps({"report": SECRET}), "2026-10-24"))
        store.kv_set(self.conn, "fleet_local_view:1", "2026-10-24T08:00:00Z")
        self.conn.commit()

    def authorized(self, external=False):
        self.agent.prepare_task_purge()
        auth = json.loads(store.kv_get(self.conn, "task_purge_authorization"))
        nonce = auth["nonce"]
        lifecycle.begin_quiescence(self.conn, nonce)
        executor = {"version": "cara-fleet-queue/v1", "action": "purge_content", "nonce": nonce,
                    "status": "quiesced", "implementation_hash": "d" * 64, "pid": 123}
        counts = lifecycle.preview(self.conn, self.cfg) if external else lifecycle.db_preview(self.conn)
        lifecycle.record_quiescence(self.conn, nonce, executor, {"status": "settled", "pending_calls": 0}, counts)
        return nonce, counts, executor

    def test_empty_database_adds_no_fleet_preview_keys_or_copy(self):
        self.assertEqual(lifecycle.db_preview(self.conn), {})
        self.assertEqual(lifecycle.preview_external(self.cfg), {})
        self.assertTrue(not (set(store.purge_preview(self.conn, "all")) & lifecycle.PREVIEW_KEYS))
        self.assertEqual(self.agent._purge_impact_text("en", {"scope": "all", "messages": 1}), "  • 1 messages")

    def test_all_preview_counts_every_fleet_table_and_context_binding(self):
        self.seed()
        self.assertEqual(lifecycle.db_preview(self.conn), {"fleet_requests": 1, "fleet_events": 1, "fleet_context_bindings": 1})
        info = store.purge_preview(self.conn, "all")
        for key in lifecycle.db_preview(self.conn):
            self.assertEqual(info[key], 1)
        for scope in ("messages", "stats", "reminders", "issues", "category"):
            self.assertFalse(set(store.purge_preview(self.conn, scope, "Example")) & lifecycle.PREVIEW_KEYS)

    def test_scope_all_refuses_before_any_content_mutation_without_receipt(self):
        self.seed()
        store.convo_add(self.conn, 1, "user", SECRET)
        before = store.purge_preview(self.conn, "all")
        for nonce in (None, NONCE):
            with self.subTest(nonce=nonce), self.assertRaises(lifecycle.FleetLifecycleError):
                store.purge_execute(self.conn, "all", task_purge_nonce=nonce)
            self.assertEqual(store.purge_preview(self.conn, "all"), before)
            self.assertIn(SECRET, self.conn.execute("SELECT payload FROM fleet_requests").fetchone()[0])

    def test_prepare_and_unsettled_or_wrong_nonce_cannot_authorize_deletion(self):
        self.seed()
        nonce = self.agent.prepare_task_purge()
        lifecycle.begin_quiescence(self.conn, nonce)
        executor = {"version": "cara-fleet-queue/v1", "action": "purge_content", "nonce": nonce,
                    "status": "quiesced", "implementation_hash": "d" * 64, "pid": 123}
        with self.assertRaises(lifecycle.FleetLifecycleError):
            lifecycle.record_quiescence(self.conn, nonce, executor, {"status": "waiting", "pending_calls": 1}, lifecycle.db_preview(self.conn))
        executor["nonce"] = "f" * 32
        with self.assertRaises(lifecycle.FleetLifecycleError):
            lifecycle.record_quiescence(self.conn, nonce, executor, {"status": "settled", "pending_calls": 0}, lifecycle.db_preview(self.conn))
        with self.assertRaises(lifecycle.FleetLifecycleError):
            store.purge_execute(self.conn, "all", task_purge_nonce=nonce)

    def test_confirmed_purge_deletes_payload_events_and_context_but_preserves_budget_prefs(self):
        self.seed()
        store.usage_add(self.conn, "fleet_planner", "chat", "fixture-model", 1, 1, cost_usd=0.01)
        store.pref_set(self.conn, "owner_name", "Example Owner")
        nonce, counts, _ = self.authorized()
        preview = store.purge_preview(self.conn, "all")
        info, _ = store.purge_execute(self.conn, "all", task_purge_nonce=nonce)
        self.assertEqual(info, preview)
        self.assertEqual(lifecycle.db_preview(self.conn), {})
        self.assertEqual(store.pref_get(self.conn, "owner_name"), "Example Owner")
        self.assertEqual(store.usage_total(self.conn, "month"), 0.01)
        auth = json.loads(store.kv_get(self.conn, "task_purge_authorization"))
        self.assertEqual(auth["phase"], "db_committed")
        self.assertEqual(auth["fleet_quiescence"]["preview"], counts)
        self.assertTrue(lifecycle.admission_paused(self.conn))

    def test_new_event_after_preview_requires_new_confirmation_with_content_intact(self):
        self.seed()
        nonce, _, _ = self.authorized()
        self.conn.execute("INSERT INTO fleet_events(request_id,kind,payload,created_at) VALUES(?,?,?,?)",
                          ("c" * 32, "late", SECRET, "2026-10-24"))
        self.conn.commit()
        with self.assertRaises(lifecycle.FleetLifecycleError):
            store.purge_execute(self.conn, "all", task_purge_nonce=nonce)
        self.assertEqual(lifecycle.db_preview(self.conn)["fleet_events"], 2)
        self.assertIn(SECRET, self.conn.execute("SELECT payload FROM fleet_requests").fetchone()[0])

    def test_other_purge_scopes_preserve_fleet_content(self):
        self.seed()
        original = lifecycle.db_preview(self.conn)
        for scope in ("messages", "stats", "reminders", "issues"):
            store.purge_execute(self.conn, scope)
            self.assertEqual(lifecycle.db_preview(self.conn), original)

    def test_spool_counts_include_request_result_started_cancel_and_temporary_copies(self):
        names = [("requests", JOB + ".json"), ("results", JOB + ".json"),
                 ("results", JOB + ".started.json"), ("requests", ".cancel-" + JOB + ".json"),
                 ("requests", "." + JOB + ".json.123." + "e" * 12 + ".tmp"),
                 ("requests", "..cancel-" + JOB + ".json.123." + "e" * 12 + ".tmp")]
        for directory, name in names:
            (self.spool / directory / name).write_text(SECRET)
        readiness = self.spool / "results" / ".fleet-queue-ready.json"
        readiness.write_text('{}')
        consumed = self.spool.parent / "consumed"
        consumed.mkdir(); token = consumed / ("f" * 32); token.write_text("digest-only")
        self.assertEqual(lifecycle.preview_external(self.cfg), {"fleet_transport_copies": len(names)})
        self.assertNotIn(readiness, lifecycle.transport_files(self.cfg))
        self.assertTrue(token.exists())
        self.assertTrue(readiness.exists())

    def test_unknown_spool_entries_symlinks_and_non_dedicated_paths_fail_closed(self):
        unknown = self.spool / "results" / "unknown-private.txt"
        unknown.write_text(SECRET)
        with self.assertRaises(lifecycle.FleetLifecycleError):
            lifecycle.preview_external(self.cfg)
        unknown.unlink()
        link = self.spool / "results" / (JOB + ".json")
        target = self.root / "outside.txt"; target.write_text(SECRET)
        link.symlink_to(target)
        with self.assertRaises(lifecycle.FleetLifecycleError):
            lifecycle.preview_external(self.cfg)
        with self.assertRaises(lifecycle.FleetLifecycleError):
            lifecycle.preview_external(SimpleNamespace(fleet_spool=str(self.root)))
        self.assertTrue(target.exists())

    def test_typed_preview_includes_spool_files_and_refreshes_on_new_copy(self):
        self.seed()
        (self.spool / "requests" / (JOB + ".json")).write_text(SECRET)
        self.agent.fleet_purge_preview = lambda: lifecycle.preview_external(self.cfg)
        self.agent.do_purge(1, "en", {"scope": "all"})
        pending = store.pending_get(self.conn, 1)
        self.assertEqual(pending["payload"]["anticipated"]["fleet_transport_copies"], 1)
        self.assertIn("fleet transport files", self.agent.messages[-1])
        self.assertIn("consumed authorization markers", self.agent.messages[-1])
        (self.spool / "results" / (JOB + ".json")).write_text(SECRET)
        self.agent.resolve_purge(1, "en", pending, pending["payload"]["phrase"])
        renewed = store.pending_get(self.conn, 1)
        self.assertEqual(renewed["payload"]["anticipated"]["fleet_transport_copies"], 2)
        self.assertEqual(lifecycle.db_preview(self.conn)["fleet_requests"], 1)

    def test_typed_purge_without_handshake_refuses_and_preserves_content(self):
        self.seed()
        self.agent.do_purge(1, "en", {"scope": "all"})
        pending = store.pending_get(self.conn, 1)
        self.agent.resolve_purge(1, "en", pending, pending["payload"]["phrase"])
        self.assertIn("Purge was not started", self.agent.messages[-1])
        self.assertEqual(lifecycle.db_preview(self.conn)["fleet_requests"], 1)

    def test_pending_external_cleanup_cannot_erase_durable_purge_intent(self):
        self.seed()
        nonce, _, _ = self.authorized()
        store.purge_execute(self.conn, "all", task_purge_nonce=nonce)
        with mock.patch.object(tasks_svc.worker_client, "prepare_purge") as task_worker:
            self.assertFalse(self.agent.purge_task_external_state(nonce))
        task_worker.assert_not_called()
        self.assertEqual(json.loads(store.kv_get(self.conn, "task_purge_authorization"))["nonce"], nonce)

    def test_async_cancel_retains_live_handle_until_accounting_settles(self):
        call = mock.Mock()
        call.advance.side_effect = [False, True]
        agent = SimpleNamespace(_fleet_inference_calls={"request-a": call})
        self.assertEqual(lifecycle.settle_async_calls(agent), {"status": "waiting", "pending_calls": 1})
        self.assertIn("request-a", agent._fleet_inference_calls)
        self.assertEqual(lifecycle.settle_async_calls(agent), {"status": "settled", "pending_calls": 0})
        self.assertEqual(agent._fleet_inference_calls, {})
        self.assertEqual(call.cancel.call_count, 2)

    def test_accounting_error_cannot_drop_transport_or_forge_settled_receipt(self):
        call = mock.Mock(); call.advance.side_effect = sqlite3.OperationalError("synthetic database unavailable")
        agent = SimpleNamespace(_fleet_inference_calls={"request-a": call})
        with self.assertRaises(sqlite3.OperationalError):
            lifecycle.settle_async_calls(agent)
        self.assertIn("request-a", agent._fleet_inference_calls)

    def test_backup_retains_original_content_and_nonce_under_existing_policy(self):
        self.seed()
        nonce, _, _ = self.authorized()
        archive = backup.snapshot(self.cfg, self.conn)
        restored_path = self.root / "restored.db"
        restored_path.write_bytes(gzip.decompress(archive.read_bytes()))
        restored = sqlite3.connect(restored_path)
        self.addCleanup(restored.close)
        self.assertEqual(restored.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertIn(SECRET, restored.execute("SELECT payload FROM fleet_requests").fetchone()[0])
        self.assertEqual(json.loads(restored.execute("SELECT value FROM kv WHERE key='task_purge_authorization'").fetchone()[0])["nonce"], nonce)
        store.purge_execute(self.conn, "all", task_purge_nonce=nonce)
        self.assertTrue(archive.exists())
        self.assertEqual(restored.execute("SELECT COUNT(*) FROM fleet_events").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
