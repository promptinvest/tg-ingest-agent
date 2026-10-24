"""Planner example and real task/source fences; no model, gateway or message I/O."""
import copy
import json
import unittest
from unittest import mock

import llm
import store
import task_runner
import tasking
import tasks_svc
import test_fleet_task_bridge as bridge
import tool_broker


SOURCE = ("Investigate the current memory usage on Example application host "
          "and give a concise answer.")
SOURCE_TIME = "2026-10-24T12:00:00+00:00"


def candidate(source=SOURCE, *, start=0, end=None):
    return {"objective": "Answer the complete owner fleet request", "deliverable": "answer",
            "capability_gaps": [], "steps": [{
                "key": "inspect", "tool": "fleet.investigate",
                "input": {"query": source[start:end]},
                "bindings": {"query": {
                    "source": "boss_span", "start": start,
                    "end": len(source) if end is None else end,
                    "source_hash": tasking.source_hash(source), "transform": "literal"}},
                "depends_on": [], "purpose": "Preserve the requested intent, metric and reply format"}]}


class FleetTaskPlannerGuidance(unittest.TestCase):
    def fixture(self, source=SOURCE):
        case = bridge.FleetTaskBridge()
        case.setUp()
        self.addCleanup(case.tearDown)
        case.source(1, source)
        return case

    def assert_no_admission(self, case):
        for table in ("assistant_tasks", "fleet_requests", "tool_receipts", "task_approvals"):
            self.assertEqual(case.conn.execute("SELECT count(*) FROM " + table).fetchone()[0], 0)
        self.assertFalse((case.root / "spool" / "requests").exists())
        self.assertEqual(case.conn.execute("SELECT count(*) FROM llm_usage").fetchone()[0], 0)

    def test_complete_prompt_example_is_real_json_and_validates_without_authority(self):
        for correction in (None, "fleet.investigate: input must be an object"):
            with self.subTest(correction=correction):
                messages = tasks_svc._planner_messages(SOURCE, SOURCE_TIME, 0, correction)
                system = messages[0]["content"]
                example_source, _ = json.JSONDecoder().raw_decode(
                    system.split("FLEET_EXAMPLE_SOURCE=", 1)[1])
                step, _ = json.JSONDecoder().raw_decode(
                    system.split("FLEET_EXAMPLE_STEP=", 1)[1])
                raw = {"objective": "Explain the bound fleet result", "deliverable": "answer",
                       "steps": [step], "capability_gaps": []}
                checked = tasking.validate_plan(raw, example_source, source_time=SOURCE_TIME)
                self.assertEqual(step["input"], {"query": example_source})
                self.assertEqual(step["bindings"]["query"]["start"], 0)
                self.assertEqual(step["bindings"]["query"]["end"], len(example_source))
                self.assertEqual(checked["source_hash"], tasking.source_hash(example_source))
                self.assertEqual(checked["steps"][0]["input"], {"query": {"$bound": "query"}})
                self.assertEqual(checked["steps"][0]["tool"], "fleet.investigate")
                with self.assertRaisesRegex(tasking.PlanError, "source hash mismatch"):
                    tasking.validate_plan(raw, SOURCE, source_time=SOURCE_TIME)
                self.assertIn("SOURCE_LENGTH=91", messages[1]["content"])
                self.assertIn("SOURCE_HASH=" + tasking.source_hash(SOURCE), messages[1]["content"])

    def test_valid_complete_owner_request_queues_once_and_resolves_original_intent(self):
        case = self.fixture()
        with mock.patch.object(llm, "chat_profile", return_value=json.dumps(candidate())) as plan, \
                mock.patch.object(llm, "transport_chat") as transport:
            case.agent.do_task_start(111, "en", SOURCE)
            case.agent.do_task_start(111, "en", SOURCE)
        self.assertEqual(plan.call_count, 1)
        transport.assert_not_called()
        task = store.assistant_task_by_source(case.conn, 111, 1)
        steps = store.assistant_task_steps(case.conn, task["id"])
        self.assertEqual(len(steps), 1)
        self.assertEqual(task["model_calls"], 1)
        self.assertEqual(task_runner.resolve_inputs(case.conn, task, steps[0]), {"query": SOURCE})
        self.assertEqual(json.loads(steps[0]["bindings_json"])["query"]["end"], 91)
        self.assertEqual(json.loads(steps[0]["input_json"]), {"query": {"$bound": "query"}})
        self.assertEqual(case.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 0)
        self.assertEqual(case.conn.execute("SELECT count(*) FROM tool_receipts").fetchone()[0], 0)
        self.assertEqual(case.conn.execute("SELECT count(*) FROM task_approvals").fetchone()[0], 0)

    def test_failed_model_array_and_cut_span_are_rejected_without_task_or_fleet_dispatch(self):
        case = self.fixture()
        invalid = candidate()
        invalid["steps"][0]["input"] = []
        invalid["steps"][0]["bindings"]["query"].update(start=42, end=76)
        before = copy.deepcopy(invalid)
        with self.assertRaisesRegex(tasking.PlanError, "input must be an object"):
            tasking.validate_plan(invalid, SOURCE, source_time=SOURCE_TIME)
        raw = "```json\n" + json.dumps(invalid) + "\n```"
        with mock.patch.object(llm, "chat_profile", return_value=raw) as plan, \
                mock.patch.object(llm, "transport_chat") as transport:
            case.agent.do_task_start(111, "en", SOURCE)
        self.assertEqual(plan.call_count, min(2, case.cfg.task_model_call_limit))
        transport.assert_not_called()
        self.assert_no_admission(case)
        self.assertEqual(invalid, before)
        self.assertIn("can't safely bind", case.sent[-1][1])
        self.assertIn("input must be an object", plan.call_args_list[1].args[3][0]["content"]
                      + plan.call_args_list[1].args[3][1]["content"])

    def test_fixed_object_cannot_hide_an_inexact_source_span(self):
        invalid = candidate()
        invalid["steps"][0]["bindings"]["query"].update(start=42, end=76)
        before = copy.deepcopy(invalid)
        self.assertEqual(SOURCE[42:76], "ample application host and give a ")
        with self.assertRaisesRegex(tasking.PlanError, "does not match its boss span"):
            tasking.validate_plan(invalid, SOURCE, source_time=SOURCE_TIME)
        self.assertEqual(invalid, before)

    def test_provenance_cannot_fill_missing_input_or_replace_owner_authority(self):
        mutations = (
            ("missing query", lambda step: step.update(input={}), "missing input"),
            ("array bindings", lambda step: step.update(bindings=[]), "bindings must be an object"),
            ("missing provenance", lambda step: step.update(bindings={}), "missing provenance"),
            ("wrong source", lambda step: step["bindings"]["query"].update(source_hash="0" * 64), "source hash mismatch"),
            ("predecessor", lambda step: step["bindings"]["query"].update(source="step_output"), "canonical owner span"),
        )
        for label, mutate, reason in mutations:
            with self.subTest(label=label):
                raw = candidate()
                mutate(raw["steps"][0])
                before = copy.deepcopy(raw)
                with self.assertRaisesRegex(tasking.PlanError, reason):
                    tasking.validate_plan(raw, SOURCE, source_time=SOURCE_TIME)
                self.assertEqual(raw, before)

    def test_existing_two_attempt_correction_admits_one_valid_replacement(self):
        case = self.fixture()
        invalid = candidate()
        invalid["steps"][0]["input"] = []
        with mock.patch.object(llm, "chat_profile", side_effect=[json.dumps(invalid), json.dumps(candidate())]) as plan, \
                mock.patch.object(llm, "transport_chat") as transport:
            case.agent.do_task_start(111, "en", SOURCE)
        self.assertEqual(plan.call_count, 2)
        transport.assert_not_called()
        task = store.assistant_task_by_source(case.conn, 111, 1)
        step = store.assistant_task_steps(case.conn, task["id"])[0]
        self.assertEqual(task["model_calls"], 2)
        self.assertEqual(task_runner.resolve_inputs(case.conn, task, step), {"query": SOURCE})
        self.assertEqual(case.conn.execute("SELECT count(*) FROM assistant_tasks").fetchone()[0], 1)
        self.assertEqual(case.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 0)

    def test_mixed_fleet_clause_keeps_independent_reminder_provenance(self):
        due = "2099-01-01T09:00:00+00:00"
        source = SOURCE + "; remind me to call Alice at " + due
        case = self.fixture(source)
        raw = candidate(source, end=len(SOURCE))
        digest = tasking.source_hash(source)
        raw["steps"].append({
            "key": "reminder", "tool": "reminder.propose",
            "input": {"title": "call Alice", "due_utc": due, "recurrence": "none"},
            "bindings": {
                "title": {"source": "boss_span", "start": source.index("call Alice"),
                          "end": source.index("call Alice") + len("call Alice"),
                          "source_hash": digest, "transform": "literal"},
                "due_utc": {"source": "boss_span", "start": source.index(due), "end": len(source),
                            "source_hash": digest, "transform": "reminder_due"}},
            "depends_on": [], "purpose": "Keep the independently confirmed reminder"})
        checked = tasking.validate_plan(raw, source, source_time=SOURCE_TIME)
        self.assertEqual(len(checked["steps"]), 2)
        task, _ = store.assistant_task_create(case.conn, 111, 1, raw)
        fleet_step, reminder_step = store.assistant_task_steps(case.conn, task["id"])
        self.assertEqual(task_runner.resolve_inputs(case.conn, task, fleet_step), {"query": SOURCE})
        self.assertEqual(task_runner.resolve_inputs(case.conn, task, reminder_step),
                         {"title": "call Alice", "due_utc": due, "recurrence": "none"})
        self.assertTrue(tool_broker.get_spec("reminder.propose").requires_confirmation)
        self.assertFalse(tool_broker.get_spec("fleet.investigate").writes_state)
        self.assertEqual(case.conn.execute("SELECT count(*) FROM reminders").fetchone()[0], 0)
        self.assertEqual(case.conn.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 0)
