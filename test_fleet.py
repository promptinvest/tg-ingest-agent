"""Fleet provenance, privilege, failure, and conversational regression checks."""
import copy
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import cara_fleet
import common
import fleet
import fleet_host
import fleet_protocol as protocol
import router
import skill_manifest
import store
import worker_client


def inventory():
    return {"version": protocol.VERSION,
        "hosts": {"pd": {"label": "Cara host", "role": "Cara and Daily Lingo", "aliases": ["Кара"],
                         "restart_services": ["cara-worker.service"]}},
        "projects": {"daily-lingo": {"label": "Daily Lingo", "hosts": ["pd"],
                     "recorded_at": "2026-10-21", "notes": "Verified release; no live health inferred."}}}


def plan(operation="inspect"):
    return {"hosts": ["pd"], "facets": ["resources", "services"], "clarification": "",
            "operation": operation, "service": "cara-worker.service" if operation == "restart" else ""}


class FleetContracts(unittest.TestCase):
    def test_inventory_has_no_model_addresses_or_commands(self):
        for field in ("address", "command", "key", "shell"):
            data = inventory()
            data["hosts"]["pd"][field] = "anything"
            with self.subTest(field=field), self.assertRaises(ValueError):
                protocol.inventory(data)

    def test_unknown_project_host_rejected(self):
        data = inventory()
        data["projects"]["daily-lingo"]["hosts"] = ["retired"]
        with self.assertRaises(ValueError):
            protocol.inventory(data)

    def test_unknown_and_duplicate_targets_rejected(self):
        for hosts in (["retired"], ["pd", "pd"], ["127.0.0.1"]):
            candidate = plan()
            candidate["hosts"] = hosts
            with self.subTest(hosts=hosts), self.assertRaises(ValueError):
                fleet.validate_plan(candidate, inventory())

    def test_shell_and_extra_fields_rejected(self):
        for update in ({"command": "rm -rf /"}, {"operation": "shell"}, {"facets": ["exec"]}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                fleet.validate_plan(dict(plan(), **update), inventory())

    def test_restart_target_policy(self):
        for unit in ("ssh.service", "cara-worker.service;id", "daily-lingo.service"):
            with self.subTest(unit=unit), self.assertRaises(ValueError):
                fleet.validate_plan(dict(plan("restart"), service=unit), inventory())

    def test_restarts_require_services_observation(self):
        value = fleet.validate_plan(dict(plan("restart"), facets=["resources"]), inventory())
        self.assertIn("services", value["facets"])

    def test_project_evidence_cannot_be_omitted_by_planner(self):
        value = fleet.validate_plan(dict(plan(), facets=["resources"]), inventory())
        self.assertIn("projects", value["facets"])

    def test_inert_clarification_accepts_empty_read_list(self):
        value = fleet.validate_plan(dict(plan(), hosts=[], facets=[], clarification="Which project?"), inventory())
        self.assertEqual(value["clarification"], "Which project?")
        with self.assertRaises(ValueError):
            protocol.request({"operation":"inspect","hosts":["pd"],"facets":[],"inventory_hash":protocol.digest(inventory())},inventory())

    def test_mismatched_or_stale_results_rejected(self):
        result = {"version": protocol.VERSION, "observed_at": protocol.now(), "hosts": {"pd": {"available": False}}}
        protocol.result(result, ["pd"])
        with self.assertRaises(ValueError):
            protocol.result(result, ["other"])
        result["observed_at"] = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        with self.assertRaises(ValueError):
            protocol.result(result, ["pd"])

    def test_nonfinite_and_deep_outputs_rejected(self):
        for value in (float("nan"), {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": {"i": 0}}}}}}}}}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                protocol.safe_tree(value)

    def test_unknown_citations_refused(self):
        with self.assertRaises(ValueError):
            fleet.render_analysis({"observations": ["invented"], "interpretation": "fine", "gaps": [], "next_steps": []}, {}, "en")

    def test_measurements_rendered_from_receipts(self):
        text = fleet.render_analysis({"observations": ["F1"], "interpretation": "[F1] is a short sample.", "gaps": ["No historical series."], "next_steps": []},
            {"F1": {"field": "CPU", "value": 12.5}}, "en")
        self.assertIn("CPU: 12.5", text)
        self.assertIn("No historical series.", text)

    def test_every_host_retains_evidence(self):
        data = inventory()
        data["hosts"]["other"] = copy.deepcopy(data["hosts"]["pd"])
        data["hosts"]["other"]["label"] = "Other host"
        evidence = fleet.facts({"hosts": {"pd": {"services": {str(i): {} for i in range(24)}}, "other": {"available": False}}}, data)
        self.assertTrue(any("Other host" in v["field"] for v in evidence.values()))

    def test_protocol_requires_inventory_version_and_fresh_approval(self):
        value = {"operation": "restart", "host": "pd", "service": "cara-worker.service",
                 "fingerprint": "a" * 64, "token": "b" * 32,
                 "expires": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
                 "inventory_hash": protocol.digest(inventory())}
        protocol.request(value, inventory())
        for update in ({"inventory_hash": "old"}, {"expires": protocol.now()}, {"token": "bad"}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                protocol.request(dict(value, **update), inventory())

    def test_router_manifest_and_dispatch_are_closed(self):
        skill_manifest.assert_covers(router.ACTIONS)
        self.assertFalse(skill_manifest.get_policy("fleet_investigate")["writes_state"])
        self.assertTrue(skill_manifest.get_policy("fleet_maintain")["requires_confirmation"])

    def test_interpretation_cannot_invent_a_citation(self):
        with self.assertRaises(ValueError):
            fleet.render_analysis({"observations": ["F1"], "interpretation": "[F999] is healthy", "gaps": [], "next_steps": []},
                                  {"F1": {"field": "status", "value": "unknown"}}, "en")

    def test_disabled_feature_is_not_advertised(self):
        labels = skill_manifest.capability_titles("en", SimpleNamespace(fleet_enabled=False, web_search_api_key="", gcal_calendar_id=""))
        self.assertNotIn("Confirmed fleet maintenance", labels)

    def test_read_gateway_rejects_arbitrary_operation_and_facet(self):
        for request in ({"operation": "exec", "command": "id"}, {"operation": "inspect", "facets": ["shell"]}):
            with self.subTest(request=request), self.assertRaises(ValueError):
                fleet_host.run({}, request)

    def test_host_does_not_return_log_messages(self):
        with mock.patch.object(fleet_host, "command", return_value='{"PRIORITY":"3","MESSAGE":"private secret"}\n'):
            out = fleet_host.inspect({"host": "pd", "services": ["cara-worker.service"]}, ["errors"])
        self.assertNotIn("private secret", json.dumps(out))
        self.assertEqual(out["errors"]["cara-worker.service"]["warning_or_worse"], 1)

    def test_manifest_only_returns_allowlisted_fields(self):
        with mock.patch.object(fleet_host, "read", return_value=json.dumps({"source_revision": "abc123", "backup_dir": "secret-path", "token": "private"})):
            value = fleet_host.projects({"projects": {"cara": ["installed"]}})
        self.assertEqual(value, {"cara": {"source_revision": "abc123"}})

    def test_service_drift_prevents_restart(self):
        value = {"operation": "restart", "service": "cara-worker.service", "fingerprint": "old", "token": "b"*32,
                 "expires": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()}
        with mock.patch.object(fleet_host, "fingerprint", return_value="new"), mock.patch.object(fleet_host, "command") as effect:
            with self.assertRaises(ValueError):
                fleet_host.restart({"restart_services": ["cara-worker.service"]}, value)
            effect.assert_not_called()

    def test_executor_never_retries_ambiguous_restart(self):
        request = {"operation": "restart", "host": "pd", "service": "cara-worker.service", "fingerprint": "a"*64,
                   "token": "b"*32, "expires": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
                   "inventory_hash": protocol.digest(inventory())}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(cara_fleet, "remote", side_effect=TimeoutError) as remote:
            first = cara_fleet.execute(inventory(), {}, request, Path(tmp))
            second = cara_fleet.execute(inventory(), {}, request, Path(tmp))
        self.assertEqual(first["outcome"], "ambiguous")
        self.assertEqual(second["outcome"], "ambiguous")
        self.assertEqual(remote.call_count, 1)


class Agent(fleet.FleetMixin):
    def __init__(self, conn, root):
        self.conn = conn
        self.cfg = SimpleNamespace(fleet_enabled=True, fleet_spool=str(root / "spool"),
                                   fleet_inventory=str(root / "inventory.json"), task_cost_limit_usd=0.15)
        self._current_update_id = 1
        self.messages = []
    def reply(self, chat, text, **kwargs):
        self.messages.append((chat, text, kwargs))
        return {"message_id": len(self.messages) + 100}
    def answer_callback(self, *args):
        pass


class FleetLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.conn = store.open_db(self.root / "test.db")
        (self.root / "inventory.json").write_text(protocol.canonical(inventory()))
        self.agent = Agent(self.conn, self.root)
        store.telegram_update_receive(self.conn, {"update_id": 1, "message": {"chat": {"id": 111}, "text": "Check fleet", "message_id": 10}}, chat_id=111)
        store.convo_add(self.conn, 111, "user", "Check fleet", update_id=1, source="boss", tg_message_id=10)
    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()
        common.set_current_trace(None)
    def start(self, operation="inspect"):
        with mock.patch.object(fleet.llm, "chat_profile", return_value=json.dumps(plan(operation))):
            self.agent.do_fleet(111, "en", "Check fleet", allow_maintenance=operation == "restart")
        return self.conn.execute("SELECT * FROM fleet_requests").fetchone()
    def result(self, row):
        payload = json.loads(row["payload"])
        response = {"version": protocol.VERSION, "observed_at": protocol.now(), "hosts": {"pd": {
            "host": "pd", "observed_at": protocol.now(), "services": {"cara-worker.service": {"ActiveState": "active", "fingerprint": "a"*64}}}}}
        with mock.patch.object(worker_client, "poll", return_value=response), mock.patch.object(fleet.llm, "chat_profile", side_effect=ValueError):
            self.agent.check_fleet_requests()
        return self.conn.execute("SELECT * FROM fleet_requests WHERE id=?", (row["id"],)).fetchone()
    def test_read_request_is_async_and_idempotent(self):
        row = self.start()
        self.assertEqual(row["state"], "queued")
        self.start()
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 1)
        files = list((self.root / "spool" / "requests").glob("*.json"))
        self.assertEqual(len(files), 1)
        envelope = json.loads(files[0].read_text())
        self.assertEqual(envelope["input_hash"], protocol.digest(envelope["input"]))
        self.assertEqual(envelope["input"]["inventory_hash"], protocol.digest(inventory()))
    def test_read_route_cannot_propose_write(self):
        with mock.patch.object(fleet.llm, "chat_profile", return_value=json.dumps(plan("restart"))):
            self.agent.do_fleet(111, "en", "Check fleet")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 0)

    def test_invalid_plan_is_repaired_once_before_any_job(self):
        bad = dict(plan(), facets=["arbitrary_command"])
        with mock.patch.object(fleet.llm, "chat_profile", side_effect=[json.dumps(bad), json.dumps(plan())]) as chat:
            self.agent.do_fleet(111, "en", "Check fleet")
        self.assertEqual(chat.call_count, 2)
        row = self.conn.execute("SELECT * FROM fleet_requests").fetchone()
        self.assertEqual(json.loads(row["payload"])["model_calls"], 2)
        self.assertEqual(row["state"], "queued")

    def test_repeated_invalid_plan_never_publishes(self):
        with mock.patch.object(fleet.llm, "chat_profile", return_value='{}') as chat:
            self.agent.do_fleet(111, "en", "Check fleet")
        self.assertEqual(chat.call_count, 2)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 0)

    def test_one_model_call_limit_keeps_measurements_without_synthesis(self):
        self.agent.cfg.task_model_call_limit=1
        row=self.start()
        payload=json.loads(row["payload"])
        response={"version":protocol.VERSION,"observed_at":protocol.now(),"hosts":{"pd":{"host":"pd","observed_at":protocol.now(),"resources":{"available":False}}}}
        with mock.patch.object(worker_client,"poll",return_value=response), mock.patch.object(fleet.llm,"chat_profile") as chat:
            self.agent.check_fleet_requests()
        chat.assert_not_called()
        self.assertEqual(self.conn.execute("SELECT state FROM fleet_requests").fetchone()[0], "done")
    def test_unbound_source_cannot_queue(self):
        self.agent.do_fleet(111, "en", "different text")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 0)
    def test_read_measurements_survive_model_failure(self):
        row = self.result(self.start())
        self.assertEqual(row["state"], "done")
        self.assertIn("Measurements received", json.loads(row["payload"])["answer"])
    def test_maintenance_waits_for_exact_delivered_card(self):
        row = self.result(self.start("restart"))
        self.assertEqual(row["state"], "approval")
        self.assertEqual(len(list((self.root / "spool" / "requests").glob("*.json"))), 0)
        payload = json.loads(row["payload"])
        self.agent.handle_fleet_callback("cb", 111, {"message_id": payload["approval_message"]}, "fl|" + row["id"] + "|y")
        self.assertEqual(self.conn.execute("SELECT state FROM fleet_requests").fetchone()[0], "executing")
        self.agent.handle_fleet_callback("cb", 111, {"message_id": payload["approval_message"]}, "fl|" + row["id"] + "|y")
        self.assertEqual(len(list((self.root / "spool" / "requests").glob("*.json"))), 1)
    def test_wrong_card_or_chat_cannot_execute(self):
        row = self.result(self.start("restart"))
        self.agent.handle_fleet_callback("cb", 222, {"message_id": 102}, "fl|" + row["id"] + "|y")
        self.assertEqual(self.conn.execute("SELECT state FROM fleet_requests").fetchone()[0], "approval")
        self.agent.handle_fleet_callback("cb", 111, {"message_id": 999}, "fl|" + row["id"] + "|y")
        self.assertNotEqual(self.conn.execute("SELECT state FROM fleet_requests").fetchone()[0], "executing")

    def test_interruption_after_restart_publication_keeps_exact_binding(self):
        row = self.result(self.start("restart"))
        payload = json.loads(row["payload"])
        original_submit = fleet.submit
        def publish_then_interrupt(*args, **kwargs):
            original_submit(*args, **kwargs)
            raise SystemExit("interrupted after publish")
        with mock.patch.object(fleet, "submit", side_effect=publish_then_interrupt), self.assertRaises(SystemExit):
            self.agent.handle_fleet_callback("cb", 111, {"message_id": payload["approval_message"]}, "fl|" + row["id"] + "|y")
        current = self.conn.execute("SELECT * FROM fleet_requests").fetchone()
        bound = json.loads(current["payload"])
        self.assertEqual(current["state"], "executing")
        self.assertEqual(bound["operation"]["operation"], "restart")
        self.assertIn("w_1_2_", bound["binding"]["job_id"])
        with mock.patch.object(worker_client, "poll", return_value={"outcome": "succeeded"}) as poll:
            self.agent.check_fleet_requests()
        self.assertEqual(poll.call_args.kwargs["tool"], "fleet.restart")
        self.assertEqual(poll.call_args.kwargs["step_id"], 2)
        self.assertEqual(self.conn.execute("SELECT state FROM fleet_requests").fetchone()[0], "done")

    def test_publish_failure_consumes_card_without_replay(self):
        row = self.result(self.start("restart"))
        payload = json.loads(row["payload"])
        with mock.patch.object(fleet, "submit", side_effect=OSError("spool unavailable")) as publish:
            for _ in range(2):
                self.agent.handle_fleet_callback("cb", 111, {"message_id": payload["approval_message"]}, "fl|" + row["id"] + "|y")
        self.assertEqual(publish.call_count, 1)
        current = self.conn.execute("SELECT * FROM fleet_requests").fetchone()
        self.assertEqual(current["state"], "ambiguous")
        self.assertEqual(json.loads(current["payload"])["operation"]["operation"], "restart")

    def test_explicit_text_reply_binds_fleet_card_with_legacy_pending(self):
        row = self.result(self.start("restart"))
        payload = json.loads(row["payload"])
        with mock.patch.object(store, "pending_get", return_value={"kind": "reminder"}), mock.patch.object(store, "pending_clear") as clear:
            handled = self.agent.resolve_fleet_confirmation_text(111, "en", {"reply_to_message": {"message_id": payload["approval_message"]}}, "yes")
        self.assertTrue(handled)
        clear.assert_not_called()
        self.assertEqual(self.conn.execute("SELECT state FROM fleet_requests").fetchone()[0], "executing")

    def test_unquoted_yes_with_two_confirmation_types_changes_neither(self):
        row = self.result(self.start("restart"))
        with mock.patch.object(store, "pending_get", return_value={"kind": "reminder"}):
            self.assertTrue(self.agent.resolve_fleet_confirmation_text(111, "en", {}, "yes"))
        self.assertEqual(self.conn.execute("SELECT state FROM fleet_requests").fetchone()[0], "approval")
        self.assertEqual(len(list((self.root / "spool" / "requests").glob("*.json"))), 0)

    def test_old_fleet_reply_is_expired_and_other_card_keeps_legacy_flow(self):
        row = self.result(self.start("restart"))
        payload = json.loads(row["payload"])
        self.conn.execute("UPDATE fleet_requests SET state='cancelled'")
        self.conn.commit()
        self.assertTrue(self.agent.resolve_fleet_confirmation_text(111, "en", {"reply_to_message": {"message_id": payload["approval_message"]}}, "yes"))
        self.assertFalse(self.agent.resolve_fleet_confirmation_text(111, "en", {"reply_to_message": {"message_id": 999}}, "yes"))
        self.assertEqual(self.conn.execute("SELECT state FROM fleet_requests").fetchone()[0], "cancelled")
    def test_edited_owner_request_invalidates_approval(self):
        row = self.result(self.start("restart"))
        payload = json.loads(row["payload"])
        self.conn.execute("UPDATE conversation SET text='Do not restart'")
        self.conn.commit()
        self.agent.handle_fleet_callback("cb", 111, {"message_id": payload["approval_message"]}, "fl|" + row["id"] + "|y")
        self.assertNotEqual(self.conn.execute("SELECT state FROM fleet_requests").fetchone()[0], "executing")
    def test_restart_cannot_run_when_feature_disabled(self):
        row = self.result(self.start("restart"))
        payload = json.loads(row["payload"])
        self.agent.cfg.fleet_enabled = False
        self.agent.handle_fleet_callback("cb", 111, {"message_id": payload["approval_message"]}, "fl|" + row["id"] + "|y")
        self.assertEqual(self.conn.execute("SELECT state FROM fleet_requests").fetchone()[0], "approval")
    def test_schema_addition_preserves_existing_rows(self):
        store.kv_set(self.conn, "existing", "keep")
        self.conn.executescript(protocol.SCHEMA)
        self.assertEqual(store.kv_get(self.conn, "existing"), "keep")
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])
    def test_delivery_interruption_is_not_replayed(self):
        row = self.start()
        self.conn.execute("UPDATE fleet_requests SET state='delivering'")
        self.conn.commit()
        before = len(self.agent.messages)
        self.agent.check_fleet_requests()
        self.assertEqual(len(self.agent.messages), before)
        self.assertEqual(self.conn.execute("SELECT state FROM fleet_requests").fetchone()[0], "ambiguous")

    def test_real_spool_receipt_round_trip(self):
        row = self.start()
        config = self.root / "config"
        config.mkdir()
        (config / "inventory.json").write_text(protocol.canonical(inventory()))
        (config / "transport.json").write_text('{}')
        response = {"host": "pd", "observed_at": protocol.now(), "resources": {"available": False}}
        with mock.patch.object(cara_fleet, "remote", return_value=response):
            cara_fleet.run_once(self.root, config)
        with mock.patch.object(fleet.llm, "chat_profile", side_effect=ValueError):
            self.agent.check_fleet_requests()
        current = self.conn.execute("SELECT * FROM fleet_requests WHERE id=?", (row["id"],)).fetchone()
        self.assertEqual(current["state"], "done")
        self.assertEqual(json.loads(current["payload"])["result"]["hosts"]["pd"], response)

    def test_inventory_change_invalidates_delivered_approval(self):
        row = self.result(self.start("restart"))
        payload = json.loads(row["payload"])
        changed = inventory()
        changed["hosts"]["pd"]["role"] = "changed role"
        (self.root / "inventory.json").write_text(protocol.canonical(changed))
        self.agent.handle_fleet_callback("cb", 111, {"message_id": payload["approval_message"]}, "fl|" + row["id"] + "|y")
        self.assertNotEqual(self.conn.execute("SELECT state FROM fleet_requests").fetchone()[0], "executing")

    def test_historical_evidence_is_owner_scoped_and_dated(self):
        self.result(self.start())
        self.assertEqual(fleet.historical_facts(self.conn, 222, ["pd"], inventory()), {})
        evidence = fleet.historical_facts(self.conn, 111, ["pd"], inventory())
        self.assertTrue(evidence)
        self.assertTrue(all("HISTORICAL" in value["field"] for value in evidence.values()))

    def test_router_receives_current_inventory_names_and_roles(self):
        from test_tg_ingest_agent import make_config
        cfg=make_config()
        cfg.fleet_enabled=True
        cfg.fleet_inventory=self.agent.cfg.fleet_inventory
        with mock.patch.object(router.llm,"chat_profile",return_value='{"action":"fleet_investigate","params":{},"confidence":0.99}') as chat:
            result=router.route(cfg,self.conn,111,"Что с российским прокси?",None)
        self.assertEqual(result["action"],"fleet_investigate")
        system=chat.call_args.args[3][0]["content"]
        self.assertIn("Cara host",system)
        self.assertIn("Daily Lingo",system)
        self.assertIn("Generic local-server stats retain vps_stats",system)
        self.assertNotIn("/etc/cara-fleet",system)

    def test_missing_inventory_does_not_break_existing_routes(self):
        from test_tg_ingest_agent import make_config
        cfg=make_config()
        cfg.fleet_enabled=True
        cfg.fleet_inventory=str(self.root/'missing.json')
        with mock.patch.object(router.llm,"chat_profile",return_value='{"action":"reminder_list","params":{},"confidence":0.99}'):
            result=router.route(cfg,self.conn,111,"Покажи напоминания",None)
        self.assertEqual(result["action"],"reminder_list")


if __name__ == "__main__":
    unittest.main()
