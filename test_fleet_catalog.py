"""Source-bound requested answers through Cara's catalog and async fleet dispatch."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import common
import fleet
import fleet_catalog
import fleet_context
import fleet_facts
import fleet_protocol as protocol
import fleet_report
import fleet_queries
import llm
import router
import runtime
import store
import tg_ingest_agent
from test_fleet_facts import fixture
from testlib import make_config


def raw(subjects=None, fields=None, presentation="list", scope="explicit"):
    return dict(kind="catalog", subjects=subjects or ["host-a"], scope=scope,
                fields=fields or ["label"], presentation=presentation, detail="concise", clarification="")


class CatalogDispatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.handlers = dict(runtime._HANDLERS)
        self.facts = fixture()
        self.inventory = {"version": protocol.VERSION, "hosts": {}, "projects": {}}
        for subject in self.facts["subjects"]:
            if subject["kind"] == "host" and subject["id"] in {"host-a", "host-b", "host-c"}:
                self.inventory["hosts"][subject["id"]] = dict(label=subject["label"], role="Synthetic host",
                                                               aliases=[], restart_services=[])
            elif subject["kind"] == "project":
                hosts = [r["object_id"] for r in self.facts["relations"] if r["subject_id"] == subject["id"]]
                self.inventory["projects"][subject["id"]] = dict(label=subject["label"], hosts=hosts,
                                                                 recorded_at="2026-10-20", notes="Synthetic placement.")
        self.cfg = make_config(DB_PATH=str(self.root / "agent.db"), MEDIA_DIR=str(self.root / "media"),
                               CARA_FLEET_ENABLED="true", LLM_PROFILES_JSON=json.dumps({
                                   "task_planner": {"primary": "deepseek-4-flash", "fallbacks": [], "max_tokens": 200}}))
        self.cfg.fleet_inventory = str(self.root / "inventory.json")
        self.cfg.fleet_facts = str(self.root / "facts.json")
        self.cfg.fleet_spool = str(self.root / "spool")
        Path(self.cfg.fleet_inventory).write_text(protocol.canonical(self.inventory), encoding="utf-8")
        Path(self.cfg.fleet_facts).write_text(protocol.canonical(self.facts), encoding="utf-8")
        self.agent = tg_ingest_agent.Agent(self.cfg)
        self.agent.fleet_async = True
        self.conn = self.agent.conn
        self.sent = []
        self.patchers = [mock.patch.object(fleet_facts, "load", return_value=self.facts),
                         mock.patch.object(self.agent, "reply", side_effect=self.reply),
                         mock.patch.object(self.agent, "send_chat_action"),
                         mock.patch.object(fleet, "submit", side_effect=AssertionError("catalog must not probe"))]
        for patch in self.patchers:
            patch.start()

    def tearDown(self):
        for call in getattr(self.agent, "_fleet_inference_calls", {}).values():
            if call.future is not None:
                try:
                    call.future.result(timeout=3)
                except Exception:
                    pass
        for patch in reversed(self.patchers):
            patch.stop()
        self.conn.close()
        runtime._HANDLERS.clear()
        runtime._HANDLERS.update(self.handlers)
        common.set_current_trace(None)
        self.tmp.cleanup()

    def reply(self, chat, text, **kwargs):
        self.sent.append(text)
        return {"message_id": 100 + len(self.sent)}

    def turn(self, number, text, dispatch=True):
        update = {"update_id": number, "message": {"message_id": number + 10, "chat": {"id": 111},
                                                    "from": {"id": 111}, "date": int(time.time()), "text": text}}
        store.telegram_update_receive(self.conn, update, chat_id=111)
        store.convo_add(self.conn, 111, "user", text, source="boss", update_id=number, tg_message_id=number+10)
        self.agent._current_update_id = number
        if dispatch:
            self.agent.dispatch(111, update["message"], text)

    def pump(self):
        for _ in range(12):
            self.agent.check_fleet_requests()
            for call in list(getattr(self.agent, "_fleet_inference_calls", {}).values()):
                if call.future is not None:
                    try:
                        call.future.result(timeout=3)
                    except Exception:
                        pass
            row = self.conn.execute("SELECT state FROM fleet_requests ORDER BY created_at DESC LIMIT 1").fetchone()
            if row and row[0] in {"done", "failed", "ambiguous"}:
                return row[0]
        self.fail("catalog phase failed to terminate")

    def completion(self, value):
        return {"choices": [{"message": {"content": json.dumps(value)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 8}}

    def test_known_facts_full_dispatch_needs_no_model_or_executor(self):
        with mock.patch.object(llm, "chat_profile", side_effect=AssertionError("unneeded model")):
            self.turn(1, "Покажи таблицей: сервер, провайдер, IP, проекты.")
        answer = self.sent[-1]
        for value in ("192.0.2.10", "198.51.100.20", "203.0.113.30", "ExampleCloud", "Project Alpha", "Project Beta"):
            self.assertIn(value, answer)
        self.assertIn("неизвестно", answer)
        self.assertNotIn("CPU", answer)
        self.assertIn("|", answer)
        payload = json.loads(self.conn.execute("SELECT payload FROM fleet_requests").fetchone()[0])
        self.assertEqual(len(payload["answer_obligations"]), 12)
        self.assertEqual(payload["model_calls"], 0)
        self.assertFalse((self.root / "spool/requests").exists())

    def test_executor_inventory_outage_does_not_block_known_address(self):
        Path(self.cfg.fleet_inventory).unlink()
        self.turn(1, "What is the recorded address of Example application host?")
        self.assertIn("192.0.2.10", self.sent[-1])
        self.assertNotIn("unavailable", self.sent[-1])

    def test_followup_correction_and_table_preserve_ip_obligation_without_probes(self):
        for number, text in [(1, "Give the address of Example application host."),
                             (2, "Не так отвечаешь на вопрос"), (3, "Put that in a table")]:
            self.turn(number, text)
            self.assertIn("192.0.2.10", self.sent[-1])
        self.assertIn("|", self.sent[-1])
        context = json.loads(self.conn.execute("SELECT payload FROM fleet_requests WHERE source_update=3").fetchone()[0])["context"]
        self.assertEqual(context["original_question"], "Give the address of Example application host.")
        self.assertEqual(context["fields"], ["public_ipv4"])

    def test_project_name_reformat_keeps_both_colocated_names(self):
        self.turn(1, "What projects are hosted on Example application host?")
        self.turn(2, "Just the project names, please.")
        self.assertIn("Project Alpha", self.sent[-1])
        self.assertIn("Project Beta", self.sent[-1])
        self.assertNotIn("Example application host", self.sent[-1])

    def test_unrelated_owner_turn_and_source_edit_end_static_scope(self):
        self.turn(1, "Give the address of Example application host.")
        self.turn(2, "Remind me tomorrow to read", dispatch=False)
        self.turn(3, "Дай IP адреса")
        self.assertNotIn("192.0.2.10", self.sent[-1])
        self.assertIn("сервер", self.sent[-1])
        self.conn.execute("UPDATE conversation SET text='edited' WHERE update_id=3 AND role='user'")
        self.conn.commit()
        self.assertEqual(fleet_context.recent(self.conn, 111, "Дай IP адреса"), {})

    def test_known_facts_bypass_eight_investigation_backpressure(self):
        for n in range(8):
            self.conn.execute("INSERT INTO fleet_requests VALUES(?,?,?,?,?,?,?)", (str(n), 111, n+10, "queued", "{}", protocol.now(), protocol.now()))
        self.conn.commit()
        self.turn(1, "What is the recorded address of Example application host?")
        self.assertIn("192.0.2.10", self.sent[-1])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM fleet_requests WHERE state='queued'").fetchone()[0], 8)

    def test_semantic_planner_uses_local_obligations_and_never_publishes_factual_prose(self):
        question = "Please map the companies responsible for each machine into columns."
        self.turn(1, question, dispatch=False)
        selected = raw([], ["provider"], "table", "all")
        with mock.patch.object(self.agent, "_fleet_try_catalog_direct", return_value=False), \
                mock.patch.object(llm, "transport_chat", return_value=self.completion(selected)):
            self.agent.do_fleet(111, "en", question)
            self.assertEqual(self.pump(), "done")
        answer = self.sent[-1]
        self.assertIn("ExampleCloud", answer)
        self.assertIn("ExampleHost", answer)
        self.assertIn("unknown", answer)
        self.assertIn("|", answer)
        self.assertNotIn("on track", answer)

    def test_invalid_generated_backup_claim_falls_back_to_requested_recorded_address(self):
        question = "Give the recorded address of Example application host."
        self.turn(1, question, dispatch=False)
        invalid = dict(raw(fields=["public_ipv4"]), answer="A CPU sample proves every backup succeeded.")
        with mock.patch.object(self.agent, "_fleet_try_catalog_direct", return_value=False), \
                mock.patch.object(llm, "transport_chat", return_value=self.completion(invalid)) as transport:
            self.agent.do_fleet(111, "en", question)
            self.assertEqual(self.pump(), "done")
            self.assertEqual(transport.call_count, 2)
        self.assertIn("192.0.2.10", self.sent[-1])
        self.assertNotIn("backup", self.sent[-1])
        self.assertNotIn("CPU", self.sent[-1])

    def test_unknown_paid_planner_failure_does_not_repeat_and_still_answers_known_fact(self):
        question = "Give the recorded address of Example application host."
        self.turn(1, question, dispatch=False)
        with mock.patch.object(self.agent, "_fleet_try_catalog_direct", return_value=False), \
                mock.patch.object(llm, "transport_chat", side_effect=TimeoutError("fixture unknown outcome")) as transport:
            self.agent.do_fleet(111, "en", question)
            self.assertEqual(self.pump(), "done")
            self.assertEqual(transport.call_count, 1)
        self.assertIn("192.0.2.10", self.sent[-1])
        self.assertEqual(self.conn.execute("SELECT kind FROM llm_usage").fetchone()[0], "unknown_chat")

    def test_runtime_scope_to_static_field_uses_only_bound_host(self):
        self.turn(1, "Inspect host-a now", dispatch=False)
        context = fleet_context.frame("Inspect host-a now", dict(hosts=["host-a"], facets=["resources"]), self.inventory, update=1)
        payload = dict(question="Inspect host-a now", plan=dict(hosts=["host-a"]), context=context)
        self.conn.execute("INSERT INTO fleet_requests VALUES(?,?,?,?,?,?,?)", ("prior", 111, 1, "done", protocol.canonical(payload), protocol.now(), protocol.now()))
        self.conn.commit()
        self.turn(2, "А IP?")
        self.assertIn("192.0.2.10", self.sent[-1])
        self.assertNotIn("198.51.100.20", self.sent[-1])

    def test_model_plan_and_confirmed_preferences_share_existing_planner_call(self):
        with mock.patch.object(fleet_catalog.persona, "boss_preference_hint", return_value="Direct answers; no long background."):
            messages = self.agent._fleet_planning_messages("Use a table", self.inventory, {}, "en")
        self.assertIn("Direct answers; no long background", messages[0]["content"])
        self.assertIn("explicit detail/table", messages[0]["content"])
        data = json.loads(messages[1]["content"])
        self.assertEqual(data["published_facts"]["integrity"], self.facts["integrity"])
        self.assertNotIn("restart_services", json.dumps(data["published_facts"]))

    def test_last_good_facts_survive_read_failure_without_new_observation_claim(self):
        self.assertEqual(fleet_catalog.document(self.cfg)["integrity"], self.facts["integrity"])
        with mock.patch.object(fleet_facts, "load", side_effect=OSError("fixture outage")):
            self.turn(1, "Give the last recorded outbound address of Example observer host.")
        self.assertIn("203.0.113.31", self.sent[-1])
        self.assertIn("2026-06-01", self.sent[-1])
        self.assertNotIn("freshly", self.sent[-1])


    def retained_runtime(self):
        self.turn(0, "Inspect Example application host resources", dispatch=False)
        plan = dict(hosts=["host-a"], facets=["resources", "projects"], operation="inspect", service="",
                    scope="explicit", projects=[], focus=["overview"], clarification="")
        result = dict(version=protocol.VERSION, observed_at=protocol.now(), hosts={"host-a": dict(host="host-a", observed_at=protocol.now(),
                      resources=dict(cpu_percent=12, cpu_sample_seconds=.2, memory_used_percent=45, disk_used_percent=30), projects={})})
        payload = dict(question="Inspect Example application host resources", source_hash=fleet.tasking.source_hash("Inspect Example application host resources"),
                       plan=plan, result=result, catalog_hash=protocol.digest(self.inventory),
                       context=fleet_context.frame("Inspect Example application host resources", plan, self.inventory, update=0))
        self.conn.execute("INSERT INTO fleet_requests VALUES(?,?,?,?,?,?,?)", ("retained", 111, 0, "done", protocol.canonical(payload), protocol.now(), protocol.now()))
        self.conn.commit()
        return plan, result

    def test_runtime_format_correction_reuses_exact_retained_evidence_without_host_probe(self):
        plan, result = self.retained_runtime()
        question = "Put that evidence in a table, without another check."
        self.turn(1, question, dispatch=False)
        selected = dict(plan, hosts=[], scope="inherit", answer_mode="reformat", presentation="table", detail="concise")
        with mock.patch.object(llm, "transport_chat", return_value=self.completion(selected)):
            self.agent.do_fleet(111, "en", question)
            self.assertEqual(self.pump(), "done")
        answer = self.sent[-1]
        self.assertIn("| Server | Answer |", answer)
        self.assertIn("CPU 12%", answer)
        payload = json.loads(self.conn.execute("SELECT payload FROM fleet_requests WHERE source_update=1").fetchone()[0])
        self.assertTrue(payload["retained_evidence"])
        self.assertEqual(payload["result"], result)
        self.assertEqual(payload["retained_source_update"], 0)
        self.assertEqual(payload["model_calls"], 1)
        self.assertFalse((self.root / "spool/requests").exists())

    def test_runtime_correction_cannot_reuse_edited_prior_owner_source(self):
        plan, _ = self.retained_runtime()
        self.conn.execute("UPDATE conversation SET text='changed' WHERE update_id=0 AND role='user'")
        self.conn.commit()
        question = "Make that shorter"
        self.turn(1, question, dispatch=False)
        selected = dict(plan, hosts=[], scope="inherit", answer_mode="reformat", detail="concise")
        with mock.patch.object(llm, "transport_chat", return_value=self.completion(selected)):
            self.agent.do_fleet(111, "en", question)
            self.assertEqual(self.pump(), "failed")
        self.assertNotIn("CPU 12%", self.sent[-1])
        self.assertFalse((self.root / "spool/requests").exists())

if __name__ == "__main__":
    unittest.main()