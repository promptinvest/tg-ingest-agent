"""Enabled Agent MCP facts path; synthetic transport, real owner/lifecycle flow."""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import threading
import time
import unittest
from unittest import mock

import common
import fleet
import fleet_catalog
import fleet_facts
import fleet_investigation
import fleet_lifecycle as lifecycle
import fleet_lifecycle_svc
import fleet_mcp
import fleet_mcp_client as client
import fleet_protocol as protocol
import fleet_queries as queries
import fleet_queue
import llm
import router
import store
import tasks_svc
import tg_ingest_agent as entry
import test_fleet_catalog as catalog_tests
import test_fleet_mcp as mcp_tests
from test_fleet_queries import planned
from testlib import make_config

QUESTION = "Покажи таблицей: сервер, провайдер, IP, проекты."


class EnabledFactsIntegration(unittest.TestCase):
    reply = catalog_tests.CatalogDispatch.reply
    turn = catalog_tests.CatalogDispatch.turn
    completion = catalog_tests.CatalogDispatch.completion

    def setUp(self):
        catalog_tests.CatalogDispatch.setUp(self)
        self.cfg.fleet_mcp_enabled = True
        self.cfg.fleet_mcp_config = "/etc/example-facts/mcp.json"
        self.config = mcp_tests.configured(self.facts)
        self.server = mcp_tests.adapter(self.facts)
        self.entered = threading.Event()
        self.release = None
        self.exchanges = 0
        def exchange(_path, messages):
            self.exchanges += 1
            self.entered.set()
            if self.release is not None and not self.release.wait(3):
                raise AssertionError("fixture release missing")
            return mcp_tests.exchange(self.server, messages)
        self.patchers.extend([mock.patch.object(fleet_mcp, "read_config", side_effect=lambda _path: copy.deepcopy(self.config)),
                              mock.patch.object(client, "stdio_exchange", side_effect=exchange),
                              mock.patch.object(llm, "chat_profile", side_effect=AssertionError("no extra model for known facts"))])
        for patcher in self.patchers[-3:]:
            patcher.start()

    def tearDown(self):
        if self.release:
            self.release.set()
        slot = getattr(self.agent, "_fleet_mcp_facts_slot", None)
        if slot:
            end = time.monotonic() + 3
            while slot.settle()["pending_calls"] and time.monotonic() < end:
                time.sleep(0.001)
            self.assertEqual(slot.settle()["pending_calls"], 0)
        catalog_tests.CatalogDispatch.tearDown(self)

    def row(self, update=1):
        return self.conn.execute("SELECT * FROM fleet_requests WHERE source_update=?", (update,)).fetchone()

    def payload(self, update=1):
        return json.loads(self.row(update)["payload"])

    def pump(self, update=1):
        end = time.monotonic() + 2
        while time.monotonic() < end:
            self.agent.check_fleet_requests()
            row = self.row(update)
            if row and row["state"] in {"done", "failed", "cancelled", "ambiguous"}:
                return row["state"]
            time.sleep(0.001)
        self.fail("facts phase failed to terminate")

    def wait_transport(self):
        slot = self.agent._fleet_mcp_facts_slot
        end = time.monotonic() + 2
        while not slot._future.done() and time.monotonic() < end:
            time.sleep(0.001)
        self.assertTrue(slot._future.done())
        time.sleep(0.001)

    def admit(self, update=1, text=QUESTION, plan=None):
        if plan is None:
            self.turn(update, text)
        else:
            self.turn(update, text, dispatch=False)
            with mock.patch.object(fleet_catalog, "route_plan", return_value=plan):
                self.agent.do_fleet(111, "en", text)
        self.assertEqual(self.row(update)["state"], "mcp_facts_queued")

    def block(self):
        self.release = threading.Event()
        self.admit()
        self.agent.check_fleet_requests()
        self.assertTrue(self.entered.wait(1))
        self.assertEqual(self.row()["state"], "mcp_facts_started")

    def test_full_dispatch_delivers_named_requested_table_with_zero_ssh_or_extra_llm(self):
        self.admit()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.pump(), "done")
        answer = self.sent[-1]
        for value in ("192.0.2.10", "198.51.100.20", "203.0.113.30", "ExampleCloud", "Project Alpha", "Project Beta", "неизвестно", "|"):
            self.assertIn(value, answer)
        payload = self.payload()
        self.assertEqual(payload["model_calls"], 0)
        self.assertEqual(len(payload["answer_obligations"]), 12)
        self.assertEqual(payload["mcp_facts"]["outcome"], "mcp")
        self.assertEqual(self.exchanges, 1)
        self.assertFalse((self.root / "spool/requests").exists())
        self.assertNotIn('"cells"', answer)
        for _ in range(3):
            self.agent.check_fleet_requests()
        self.assertEqual(self.exchanges, 1)
        self.assertEqual(len(self.sent), 1)

    def test_existing_semantic_planner_uses_one_accounted_call_then_async_facts(self):
        question = "Could you describe the provider organisation for my northern machine?"
        semantic = catalog_tests.raw(["host-a"], ["provider"], presentation="table")
        self.turn(1, question, dispatch=False)
        with mock.patch.object(self.agent, "_fleet_try_catalog_direct", return_value=False), \
                mock.patch.object(llm, "transport_chat", return_value=self.completion(semantic)) as model:
            self.agent.do_fleet(111, "en", question)
            self.assertEqual(self.pump(), "done")
        self.assertEqual(model.call_count, 1)
        self.assertEqual(self.payload()["model_calls"], 1)
        self.assertEqual(self.exchanges, 1)
        self.assertEqual(self.payload()["mcp_facts"]["outcome"], "mcp")
        self.assertIn("ExampleCloud", self.sent[-1])
        self.assertIn("|", self.sent[-1])
    def test_durable_source_revision_policy_intent_precedes_any_transport(self):
        self.release = threading.Event(); self.admit()
        payload = self.payload(); phase = payload["mcp_facts"]
        self.assertEqual(self.exchanges, 0)
        self.assertEqual(phase["binding"], {"request_id": self.row()["id"], "source_hash": payload["source_hash"],
            "facts_hash": client.facts_revision_hash(self.facts), "policy_version": fleet_catalog.MCP_FACTS_VERSION})
        self.assertEqual(phase["plan_hash"], protocol.digest(payload["plan"]))
        self.agent.check_fleet_requests(); self.assertTrue(self.entered.wait(1))
        started = self.payload()["mcp_facts"]
        self.assertIn("started_at", started)
        self.assertEqual(started["nonce"], phase["nonce"])
        self.assertEqual(self.sent, [])
        self.assertEqual(self.agent._pending_work_poll_timeout(), 2)

    def test_process_restart_queued_phase_uses_internal_facts_without_resubmit(self):
        self.admit()
        self.agent._fleet_mcp_queued_nonces = {}
        self.assertEqual(self.pump(), "done")
        self.assertEqual(self.exchanges, 0)
        self.assertTrue(self.payload()["mcp_facts"]["recovered_without_resubmit"])
        self.assertEqual(self.payload()["mcp_facts"]["outcome"], "internal")

    def test_process_restart_started_phase_uses_internal_without_duplicate_delivery(self):
        self.admit(); row, payload = self.row(), self.payload()
        payload["mcp_facts"]["started_at"] = protocol.now()
        self.agent._fleet_save(row, "mcp_facts_started", payload, "fixture_crash_after_intent")
        self.agent._fleet_mcp_queued_nonces = {}
        self.assertEqual(self.pump(), "done")
        self.assertEqual(self.exchanges, 0)
        self.assertEqual(len(self.sent), 1)
        self.agent.check_fleet_requests()
        self.assertEqual(len(self.sent), 1)

    def test_delivery_interrupted_state_never_replays_or_resubmits(self):
        self.admit()
        self.agent._fleet_save(self.row(), "delivering", self.payload(), "fixture_interrupted_delivery")
        self.agent.check_fleet_requests()
        self.assertEqual(self.row()["state"], "ambiguous")
        self.assertEqual(self.exchanges, 0)
        self.assertEqual(self.sent, [])

    def test_duplicate_owner_update_cannot_create_second_transport(self):
        self.admit(); self.assertEqual(self.pump(), "done")
        self.turn(1, QUESTION)
        self.agent.check_fleet_requests()
        self.assertEqual(self.exchanges, 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM fleet_requests").fetchone()[0], 1)
        self.assertEqual(len(self.sent), 1)

    def test_source_edit_before_dispatch_cancels_without_transport(self):
        self.admit()
        self.conn.execute("UPDATE conversation SET text='Changed owner source' WHERE update_id=1"); self.conn.commit()
        self.assertEqual(self.pump(), "cancelled")
        self.assertEqual(self.exchanges, 0)
        self.assertEqual(self.sent, [])

    def test_source_edit_or_deletion_during_transport_prevents_late_pages(self):
        self.block()
        self.conn.execute("DELETE FROM conversation WHERE update_id=1"); self.conn.commit()
        self.agent.check_fleet_requests()
        self.assertEqual(self.row()["state"], "cancelled")
        self.assertIsNotNone(self.agent._fleet_mcp_facts_pending)
        self.assertEqual(self.agent._pending_work_poll_timeout(), 2)
        self.release.set(); self.wait_transport()
        self.agent.check_fleet_requests()
        self.assertIsNone(self.agent._fleet_mcp_facts_pending)
        self.assertEqual(self.sent, [])

    def test_removed_canonical_request_cannot_be_recreated_by_background_completion(self):
        self.block()
        self.conn.execute("DELETE FROM fleet_events"); self.conn.execute("DELETE FROM fleet_requests"); self.conn.commit()
        self.agent.check_fleet_requests(); self.release.set(); self.wait_transport()
        self.agent.check_fleet_requests()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM fleet_requests").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM fleet_events").fetchone()[0], 0)
        self.assertEqual(self.sent, [])

    def test_current_facts_revision_change_suppresses_old_answer(self):
        self.block()
        changed = copy.deepcopy(self.facts); changed["revision"] += 1; changed = fleet_facts.seal(changed)
        with mock.patch.object(fleet_facts, "load", return_value=changed):
            self.agent.check_fleet_requests()
            self.assertEqual(self.row()["state"], "failed")
            self.release.set(); self.wait_transport(); self.agent.check_fleet_requests()
        self.assertEqual(self.sent, [])

    def test_enabled_configuration_change_suppresses_inflight_result(self):
        self.block()
        self.cfg.fleet_mcp_config = "/etc/example-facts/changed.json"
        self.agent.check_fleet_requests()
        self.assertEqual(self.row()["state"], "failed")
        self.release.set(); self.wait_transport(); self.agent.check_fleet_requests()
        self.assertEqual(self.sent, [])

    def test_completed_result_rechecks_current_scope_before_publication(self):
        self.admit(); self.agent.check_fleet_requests(); self.wait_transport()
        self.config["allowed_subjects"] = ["project-alpha"]
        self.assertEqual(self.pump(), "failed")
        self.assertEqual(self.payload()["mcp_facts"]["outcome"], "scope_denied")
        self.assertTrue(self.sent)
        self.assertFalse(any("192.0.2.10" in value for value in self.sent))

    def test_local_and_remote_scope_denial_never_fallback_to_facts(self):
        self.config["allowed_subjects"] = ["project-alpha"]
        self.admit(); self.assertEqual(self.pump(), "failed")
        self.assertEqual(self.exchanges, 0)
        self.assertFalse(any("192.0.2.10" in value for value in self.sent))

    def test_remote_denial_is_not_downgraded_to_interface_outage(self):
        self.server = mcp_tests.adapter(self.facts, allowed=[])
        self.admit(); self.assertEqual(self.pump(), "failed")
        self.assertEqual(self.exchanges, 1)
        self.assertEqual(self.payload()["mcp_facts"]["outcome"], "scope_denied")
        self.assertFalse(any("192.0.2.10" in value for value in self.sent))

    def test_transport_outage_delivers_equivalent_internal_business_answer(self):
        with mock.patch.object(client, "stdio_exchange", side_effect=OSError("fixture offline")):
            self.admit(); self.assertEqual(self.pump(), "done")
        self.assertEqual(self.payload()["mcp_facts"]["outcome"], "internal")
        self.assertIn("192.0.2.10", self.sent[-1])
        self.assertIn("Project Alpha", self.sent[-1])

    def test_busy_second_request_uses_same_internal_obligations_while_first_stays_bound(self):
        self.block()
        first_pending = dict(self.agent._fleet_mcp_facts_pending)
        second = planned(self.facts, ["host-a"], ["public_ipv4"])
        self.admit(2, "IP for Example North", second)
        self.agent.check_fleet_requests()
        self.assertEqual(self.row(2)["state"], "done")
        self.assertEqual(self.agent._fleet_mcp_facts_pending, first_pending)
        self.assertEqual(self.row(1)["state"], "mcp_facts_started")
        self.assertIn("192.0.2.10", self.sent[-1])
        self.assertEqual(self.exchanges, 1)
        self.release.set(); self.assertEqual(self.pump(1), "done")
        self.assertEqual(self.exchanges, 1)

    def test_busy_denied_second_request_cannot_drop_first_live_handle(self):
        first = planned(self.facts, ["host-a"], ["public_ipv4"])
        self.release = threading.Event(); self.admit(1, "IP for Example North", first)
        self.agent.check_fleet_requests(); self.assertTrue(self.entered.wait(1))
        first_pending = dict(self.agent._fleet_mcp_facts_pending)
        self.config["allowed_subjects"] = ["host-a"]
        second = planned(self.facts, ["host-c"], ["provider"])
        self.admit(2, "Provider for Example South", second)
        self.agent.check_fleet_requests()
        self.assertEqual(self.row(2)["state"], "failed")
        self.assertEqual(self.agent._fleet_mcp_facts_pending, first_pending)
        self.assertFalse(self.release.is_set())
        self.release.set(); self.assertEqual(self.pump(1), "done")

    def test_lifecycle_settlement_waits_without_blocking_or_late_pages(self):
        self.block()
        began = time.monotonic()
        self.assertEqual(self.agent._fleet_settle_content_calls(), {"status": "waiting", "pending_calls": 1})
        self.assertLess(time.monotonic() - began, 0.25)
        self.assertIsNotNone(self.agent._fleet_mcp_facts_pending)
        self.release.set(); self.wait_transport()
        self.assertEqual(self.agent._fleet_settle_content_calls(), {"status": "settled", "pending_calls": 0})
        self.assertIsNone(self.agent._fleet_mcp_facts_pending)
        self.assertEqual(self.sent, [])

    def test_actual_typed_purge_waits_for_facts_work_and_deletes_existing_counts_only(self):
        spool = self.root / "cara-fleet" / "spool"
        for name in ("requests", "results"):
            (spool/name).mkdir(parents=True)
        self.cfg.fleet_spool = str(spool); fleet_queue.publish_ready(spool)
        self.cfg.task_artifacts_dir = self.root / "tg-ingest-agent" / "task-artifacts"
        self.cfg.task_worker_spool = str(self.root / "cara-worker" / "spool")
        self.cfg.mentor_review_spool = str(self.root / "cara-mentor" / "spool")
        self.cfg.mentor_runner_spool = str(self.root / "cara-mentor-runner" / "spool")
        self.block(); self.agent._current_update_id = None
        original = lifecycle.db_preview(self.conn)
        self.agent.do_purge(111, "en", {"scope": "all"})
        pending = store.pending_get(self.conn, 111)
        for field, count in original.items():
            self.assertEqual(pending["payload"]["anticipated"][field], count)
        self.agent.resolve_purge(111, "en", pending, pending["payload"]["phrase"])
        auth = lifecycle._authorization(self.conn); self.assertEqual(auth["phase"], "prepared")
        self.assertEqual(self.row()["state"], "cancelled")
        self.assertTrue(lifecycle.executor_gate(spool))
        self.assertFalse(self.agent.check_fleet_content_purge())
        self.assertTrue(lifecycle.db_preview(self.conn))
        self.release.set(); self.wait_transport()
        self.assertTrue(self.agent.check_fleet_content_purge())
        self.assertEqual(lifecycle.db_preview(self.conn), {})
        self.assertEqual(lifecycle._authorization(self.conn)["fleet_quiescence"]["async"], {"status": "settled", "pending_calls": 0})
        self.agent.check_fleet_requests()
        self.assertFalse(any("192.0.2.10" in value for value in self.sent))

    def test_production_loop_handles_owner_stats_and_due_reminder_during_blocked_stdio(self):
        self.agent.last_sweep = time.time(); self.release = threading.Event(); polls = []
        first = {"update_id": 101, "message": {"chat": {"id": 111}, "from": {"id": 111}, "message_id": 201,
                  "date": int(time.time()), "text": QUESTION}}
        second = {"update_id": 102, "message": {"chat": {"id": 111}, "from": {"id": 111}, "message_id": 202,
                   "date": int(time.time()), "text": "/stats"}}
        def poll(_token, method, params=None, timeout=None):
            if method != "getUpdates":
                return {}
            polls.append(params["timeout"])
            if len(polls) == 1:
                return [first]
            if len(polls) == 2:
                self.assertTrue(self.entered.wait(1))
                store.reminder_add(self.conn, 111, "fixture reminder during MCP", (datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat())
                return [second]
            self.assertFalse(self.release.is_set()); self.agent.stop = True
            return []
        def route(_cfg, _conn, _chat, text, _pending, **_kwargs):
            return {"action": "stats" if text == "/stats" else "fleet_investigate", "params": {}, "confidence": 1, "risk": "read"}
        with mock.patch.object(entry, "tg_call", side_effect=poll), mock.patch.object(router, "route", side_effect=route), \
                mock.patch.object(entry.Agent, "SCHEDULER_TICKS", ("check_assistant_tasks", "fire_due_reminders")), \
                mock.patch.object(self.agent, "announce_deploy_if_changed"), mock.patch.object(self.agent, "replay_pending_updates"), \
                mock.patch.object(self.agent, "_recent_boss_msg", return_value=None):
            self.agent.run()
        self.assertEqual(polls, [50, 2, 2])
        self.assertEqual(store.telegram_update_get(self.conn, 102)["status"], "done")
        self.assertTrue(any("fixture reminder during MCP" in value for value in self.sent))
        self.assertEqual(self.row(101)["state"], "mcp_facts_started")
        self.assertFalse(self.release.is_set())

    def test_disabled_default_preserves_immediate_internal_catalog_behavior(self):
        self.cfg.fleet_mcp_enabled = False
        self.turn(1, QUESTION)
        self.assertEqual(self.row()["state"], "done")
        self.assertEqual(self.exchanges, 0)
        self.assertNotIn("mcp_facts", self.payload())
        self.assertIn("192.0.2.10", self.sent[-1])


class MCPConfigTests(unittest.TestCase):
    def test_disabled_default_and_fixed_config_path(self):
        cfg = make_config()
        self.assertIs(cfg.fleet_mcp_enabled, False)
        self.assertEqual(cfg.fleet_mcp_config, "/etc/cara-fleet/mcp-facts.json")
        self.assertIs(make_config(CARA_FLEET_MCP_ENABLED="true").fleet_mcp_enabled, True)

    def test_invalid_flag_and_untrusted_path_forms_rejected(self):
        for value in ("1", "yes", "maybe", ""):
            with self.subTest(value=value), self.assertRaises(SystemExit):
                make_config(CARA_FLEET_MCP_ENABLED=value)
        for path in ("relative.json", "https://example.invalid/config", "//remote/config", "/etc/../private/config", "/etc/example\nprivate"):
            with self.subTest(path=path), self.assertRaises(SystemExit):
                make_config(CARA_FLEET_MCP_CONFIG=path)

    def test_dedicated_states_join_existing_pending_and_purge_selectors(self):
        import fleet_shared_reads
        for state in fleet_catalog.MCP_FACTS_STATES:
            self.assertIn(state, fleet.PENDING_STATES)
            self.assertIn(state, fleet_shared_reads.STATES)
            self.assertIn(state, fleet_lifecycle_svc._ACTIVE)


if __name__ == "__main__":
    unittest.main()