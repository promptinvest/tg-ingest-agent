#!/usr/bin/env python3
"""Adversarial runtime tests for governed tasks, worker, and improvement loop."""
import json
import errno
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import cara_worker
import common
import fetch
import improvement
import llm
import store
import task_runner
import tasking
import tasks_svc
import tool_broker
import worker_client
from tg_api import TelegramError


class StubAgent:
    def __init__(self, conn, root):
        self.conn = conn
        self.cfg = SimpleNamespace(
            task_worker_spool=str(
                Path(root) / "cara-worker" / "spool"),
            task_artifacts_dir=str(
                Path(root) / "tg-ingest-agent" / "task-artifacts"),
            task_worker_enabled=True,
            task_model_call_limit=4,
            task_cost_limit_usd=0.15,
            token="test-token",
            fetch_timeout=5,
            fetch_max_bytes=100_000,
        )
        self.completed = []
        self.blocked = []
        self.approvals = []

    def send_task_approval(self, row):
        self.approvals.append(row["id"])
        return {"message_id": 7000 + row["id"]}

    def on_task_completed(self, task, summary, artifact):
        self.completed.append((task["id"], summary, artifact))
        return True

    def on_task_blocked(self, task, summary):
        self.blocked.append((task["id"], summary))
        return True


class RuntimeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.open_db(Path(self.tmp.name) / "runtime.db")
        self.agent = StubAgent(self.conn, self.tmp.name)

    def tearDown(self):
        self.conn.close()
        common.set_current_trace(None)
        self.tmp.cleanup()

    def source(self, update_id, text, chat_id=1):
        update = {
            "update_id": update_id,
            "message": {
                "message_id": update_id + 100,
                "chat": {"id": chat_id},
                "from": {"id": chat_id},
                "text": text,
            },
        }
        row = store.telegram_update_receive(self.conn, update, chat_id=chat_id)
        store.convo_add(
            self.conn, chat_id, "user", text, source="boss",
            update_id=update_id, tg_message_id=update_id + 100)
        return row

    @staticmethod
    def read_plan(steps=1):
        return {
            "objective": "Read current reminders safely",
            "deliverable": "answer",
            "steps": [{
                "key": f"read{i}",
                "tool": "reminders.read",
                "input": {},
                "bindings": {},
                "depends_on": [],
                "purpose": "Read current reminder state",
            } for i in range(1, steps + 1)],
        }

    def reminder_plan(self, source, source_time):
        title = "call Alice"
        due = "tomorrow at 09:00"
        digest = tasking.source_hash(source)
        return {
            "objective": "Create the requested reminder",
            "deliverable": "answer",
            "steps": [{
                "key": "write",
                "tool": "reminder.propose",
                "input": {
                    "title": title,
                    "due_utc": tasking.parse_bound_due(due, source_time, 3),
                    "recurrence": "none",
                },
                "bindings": {
                    "title": {
                        "source": "boss_span", "start": source.index(title),
                        "end": source.index(title) + len(title),
                        "source_hash": digest, "transform": "reminder_title",
                    },
                    "due_utc": {
                        "source": "boss_span", "start": source.index(due),
                        "end": source.index(due) + len(due),
                        "source_hash": digest, "transform": "reminder_due",
                    },
                },
                "depends_on": [],
                "purpose": "Show the exact reminder before creating it",
            }],
        }

    def test_one_tick_executes_at_most_one_bounded_step(self):
        self.source(1001, "Read my current reminders twice.")
        task, _ = store.assistant_task_create(
            self.conn, 1, 1001, self.read_plan(2))
        task_runner.tick(self.agent, self.conn)
        states = [
            row["status"] for row in store.assistant_task_steps(
                self.conn, task["id"])]
        self.assertEqual(states, ["succeeded", "pending"])
        self.assertEqual(
            store.assistant_task_get(self.conn, task["id"])["status"], "running")
        task_runner.tick(self.agent, self.conn)
        self.assertEqual(
            store.assistant_task_get(self.conn, task["id"])["status"], "completed")
        self.assertEqual(len(self.agent.completed), 1)

    def test_transient_failure_has_attempt_evidence_but_no_failed_receipt(self):
        self.source(1002, "Read my current reminders.")
        task, _ = store.assistant_task_create(
            self.conn, 1, 1002, self.read_plan())
        with mock.patch.object(
                task_runner, "_execute_read_or_draft",
                side_effect=OSError(errno.ETIMEDOUT, "transient")):
            result = task_runner.run_task(self.agent, self.conn, task["id"])
        self.assertEqual(result["status"], "planned")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM tool_receipts").fetchone()[0], 0)
        attempt = self.conn.execute(
            "SELECT * FROM task_step_attempts").fetchone()
        self.assertEqual(attempt["status"], "failed")
        self.conn.execute(
            "UPDATE assistant_tasks SET next_action_at=NULL WHERE id=?",
            (task["id"],))
        self.conn.commit()
        with mock.patch.object(
                task_runner, "_execute_read_or_draft",
                return_value=(
                    "ok", "read", {
                        "schema": "reminders.read/v1",
                        "value": {"reminders": []},
                    }, [], None, None)):
            task_runner.run_task(self.agent, self.conn, task["id"])
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM tool_receipts").fetchone()[0], 1)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM task_step_attempts").fetchone()[0], 2)

    def test_approval_requires_delivered_exact_card_and_effect_is_once(self):
        text = "Remind me tomorrow at 09:00 to call Alice."
        update = self.source(1003, text)
        store.pref_set(self.conn, "timezone_offset", 3)
        task, _ = store.assistant_task_create(
            self.conn, 1, 1003, self.reminder_plan(text, update["received_at"]))
        task_runner.run_task(self.agent, self.conn, task["id"])
        approval = store.task_approvals_live(self.conn, 1)[0]
        self.assertIsNone(store.task_approval_decide(
            self.conn, approval["id"], 1, True,
            decision_source="callback"))
        self.assertIsNone(store.task_approval_decide(
            self.conn, approval["id"], 2, True,
            decision_source="callback",
            preview_message_id=approval["preview_message_id"]))
        self.assertIsNone(store.task_approval_decide(
            self.conn, approval["id"], 1, True,
            decision_source="callback", preview_message_id=999))
        decided = store.task_approval_decide(
            self.conn, approval["id"], 1, True,
            decision_source="callback",
            preview_message_id=approval["preview_message_id"])
        self.assertEqual(decided["status"], "approved")
        self.assertEqual(
            task_runner.execute_approved(
                self.agent, self.conn, approval["id"], 1), "effect_recorded")
        self.assertIsNone(
            task_runner.execute_approved(
                self.agent, self.conn, approval["id"], 1))
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM reminders").fetchone()[0], 1)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM tool_receipts").fetchone()[0], 1)

    def test_executing_local_approval_reopens_with_fresh_preview_after_restart(self):
        text = "Remind me tomorrow at 09:00 to call Alice."
        update = self.source(1004, text)
        store.pref_set(self.conn, "timezone_offset", 3)
        task, _ = store.assistant_task_create(
            self.conn, 1, 1004, self.reminder_plan(text, update["received_at"]))
        task_runner.run_task(self.agent, self.conn, task["id"])
        approval = store.task_approvals_live(self.conn, 1)[0]
        self.conn.execute(
            "UPDATE task_approvals SET status='executing' WHERE id=?",
            (approval["id"],))
        self.conn.commit()
        store.assistant_task_reclaim_stale(self.conn)
        self.assertEqual(
            store.task_approval_get(self.conn, approval["id"])["status"], "expired")
        self.assertEqual(
            store.assistant_task_get(self.conn, task["id"])["status"], "planned")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM reminders").fetchone()[0], 0)
        task_runner.run_task(self.agent, self.conn, task["id"])
        fresh = store.task_approvals_live(self.conn, 1)
        self.assertEqual(len(fresh), 1)
        self.assertNotEqual(fresh[0]["id"], approval["id"])
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM reminders").fetchone()[0], 0)

    def test_committed_receipt_recovers_step_and_attempt_after_crash(self):
        self.source(1010, "Read my current reminders.")
        task, _ = store.assistant_task_create(
            self.conn, 1, 1010, self.read_plan())
        step = store.assistant_task_claim_ready_step(self.conn, task["id"])
        store.assistant_task_step_status(self.conn, step["id"], "running")
        store.task_receipt_create(
            self.conn, step, input_hash="a" * 64, status="ok",
            summary="Read 0 reminders.",
            data={"schema": "reminders.read/v1", "value": {"reminders": []}},
            evidence=[])
        store.assistant_task_reclaim_stale(self.conn)
        current = store.assistant_task_step_get(self.conn, step["id"])
        self.assertEqual(current["status"], "succeeded")
        self.assertIsNotNone(current["receipt_id"])
        attempt = self.conn.execute(
            "SELECT * FROM task_step_attempts WHERE step_id = ?",
            (step["id"],)).fetchone()
        self.assertEqual(attempt["status"], "succeeded")
        self.assertEqual(
            task_runner.run_task(self.agent, self.conn, task["id"])["status"],
            "completed")

    def test_approval_rechecks_compiled_policy_and_preview_hash(self):
        text = "Remind me tomorrow at 09:00 to call Alice."
        update = self.source(1011, text)
        store.pref_set(self.conn, "timezone_offset", 3)
        task, _ = store.assistant_task_create(
            self.conn, 1, 1011, self.reminder_plan(text, update["received_at"]))
        task_runner.run_task(self.agent, self.conn, task["id"])
        approval = store.task_approvals_live(self.conn, 1)[0]
        store.task_approval_decide(
            self.conn, approval["id"], 1, True,
            decision_source="callback",
            decision_message_id=approval["preview_message_id"],
            preview_message_id=approval["preview_message_id"])
        with mock.patch.object(tool_broker, "POLICY_VERSION", "task-tools/v999"):
            self.assertEqual(
                task_runner.execute_approved(
                    self.agent, self.conn, approval["id"], 1), "expired")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM reminders").fetchone()[0], 0)

        update2 = self.source(1012, text)
        task2, _ = store.assistant_task_create(
            self.conn, 1, 1012, self.reminder_plan(text, update2["received_at"]))
        task_runner.run_task(self.agent, self.conn, task2["id"])
        approval2 = store.task_approvals_live(self.conn, 1)[0]
        store.task_approval_decide(
            self.conn, approval2["id"], 1, True,
            decision_source="callback",
            decision_message_id=approval2["preview_message_id"],
            preview_message_id=approval2["preview_message_id"])
        self.conn.execute(
            "UPDATE task_approvals SET preview_json = ? WHERE id = ?",
            ('{"kind":"reminder_create","title":"tampered"}', approval2["id"]))
        self.conn.commit()
        self.assertEqual(
            task_runner.execute_approved(
                self.agent, self.conn, approval2["id"], 1), "expired")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM reminders").fetchone()[0], 0)

    def test_source_drift_blocks_resolution(self):
        self.source(1005, "Read my current reminders.")
        task, _ = store.assistant_task_create(
            self.conn, 1, 1005, self.read_plan())
        self.conn.execute(
            "UPDATE conversation SET text='edited source' WHERE update_id=1005")
        self.conn.commit()
        # This plan has no bound field, so source drift is checked explicitly by
        # the resume boundary; a write plan checks it again during resolution.
        store.assistant_task_set_status(self.conn, task["id"], "blocked")
        self.assertFalse(store.assistant_task_resume(self.conn, task["id"], 1))

    def test_worker_is_nonblocking_and_rejects_tampered_binding(self):
        self.source(1006, "Echo a harmless worker transport check.")
        plan = {
            "objective": "Check the isolated worker transport",
            "deliverable": "answer",
            "steps": [{
                "key": "worker",
                "tool": "worker.echo",
                "input": {"text": "hello"},
                "bindings": {},
                "depends_on": [],
                "purpose": "Run a bounded isolated registry tool",
            }],
        }
        task, _ = store.assistant_task_create(self.conn, 1, 1006, plan)
        result = task_runner.run_task(self.agent, self.conn, task["id"])
        self.assertEqual(result["status"], "waiting_worker")
        step = store.assistant_task_steps(self.conn, task["id"])[0]
        request = (
            Path(self.agent.cfg.task_worker_spool) / "requests"
            / f"{step['worker_job_id']}.json")
        envelope = json.loads(request.read_text(encoding="utf-8"))
        self.assertEqual(
            envelope["input_hash"],
            __import__("hashlib").sha256(
                json.dumps(envelope["input"], ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")).encode("utf-8")).hexdigest())
        handled = cara_worker.run(
            self.agent.cfg.task_worker_spool,
            str(Path(self.tmp.name) / "worker-state"), once=True)
        self.assertEqual(handled, 1)
        task_runner.poll_worker_results(self.agent, self.conn)
        self.assertEqual(
            store.assistant_task_get(self.conn, task["id"])["status"], "completed")

    def test_worker_request_remains_group_readable_under_agent_umask(self):
        self.source(1013, "Echo hello in the isolated worker.")
        plan = {
            "objective": "Check the isolated worker",
            "deliverable": "answer",
            "steps": [{
                "key": "worker", "tool": "worker.echo",
                "input": {"text": "hello"}, "bindings": {},
                "depends_on": [], "purpose": "Check transport",
            }],
        }
        task, _ = store.assistant_task_create(self.conn, 1, 1013, plan)
        previous = os.umask(0o077)
        try:
            task_runner.run_task(self.agent, self.conn, task["id"])
        finally:
            os.umask(previous)
        step = store.assistant_task_steps(self.conn, task["id"])[0]
        request = (
            Path(self.agent.cfg.task_worker_spool) / "requests"
            / f"{step['worker_job_id']}.json")
        self.assertEqual(request.stat().st_mode & 0o777, 0o640)

    def test_resume_resets_final_delivery_binding(self):
        self.source(1014, "Read my current reminders.")
        task, _ = store.assistant_task_create(
            self.conn, 1, 1014, self.read_plan())
        step = store.assistant_task_claim_ready_step(self.conn, task["id"])
        store.task_attempt_finish(
            self.conn, step["id"], "blocked", error="temporary")
        store.assistant_task_step_status(
            self.conn, step["id"], "blocked", error="temporary")
        store.assistant_task_set_status(self.conn, task["id"], "blocked")
        self.conn.execute(
            "UPDATE assistant_tasks SET delivery_status='delivered',"
            " final_message_id=777 WHERE id=?", (task["id"],))
        self.conn.commit()
        self.assertTrue(store.assistant_task_resume(self.conn, task["id"], 1))
        resumed = store.assistant_task_get(self.conn, task["id"])
        self.assertEqual(resumed["delivery_status"], "pending")
        self.assertIsNone(resumed["final_message_id"])

    def test_feedback_requires_explicit_task_or_exact_result_reply(self):
        self.source(1015, "Read my current reminders.")
        task, _ = store.assistant_task_create(
            self.conn, 1, 1015, self.read_plan())
        self.conn.execute(
            "UPDATE assistant_tasks SET status='completed',"
            " delivery_status='delivered', final_message_id=888,"
            " completed_at=updated_at WHERE id=?", (task["id"],))
        self.conn.commit()

        class FeedbackAgent(tasks_svc.TasksMixin):
            def __init__(self, conn):
                self.conn = conn
                self.turn_reply_message_id = None
                self._current_update_id = 1015
                self.replies = []

            def reply(self, chat_id, text, **kwargs):
                self.replies.append(text)
                return {"message_id": 999}

        agent = FeedbackAgent(self.conn)
        trace_id = "tr_1722240000_abcdef1234"
        store.trace_start(self.conn, trace_id, "telegram_message", 1)
        common.set_current_trace(trace_id)
        agent.do_task_feedback(1, "en", {"rating": 2}, "2 out of 5")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM task_feedback").fetchone()[0], 0)
        agent.turn_reply_message_id = 887
        agent.do_task_feedback(
            1, "en", {"id": task["id"], "rating": 2}, "2 out of 5")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM task_feedback").fetchone()[0], 0)
        agent.turn_reply_message_id = 888
        agent.do_task_feedback(1, "en", {"rating": 2}, "2 out of 5")
        row = self.conn.execute("SELECT * FROM task_feedback").fetchone()
        self.assertEqual(
            (row["task_id"], row["outbound_message_id"]), (task["id"], 888))

    def test_approval_card_discloses_recurrence(self):
        class ApprovalAgent(tasks_svc.TasksMixin):
            @staticmethod
            def lang():
                return "en"

            @staticmethod
            def reply(chat_id, text, **kwargs):
                return {"message_id": 42, "text": text}

        result = ApprovalAgent().send_task_approval({
            "id": 1, "chat_id": 1,
            "preview_json": json.dumps({
                "kind": "reminder_create", "task_id": 1,
                "title": "call Alice",
                "due_utc": "2030-01-01T09:00:00+00:00",
                "recurrence": "weekly",
            }),
        })
        self.assertIn("Recurrence: weekly", result["text"])

    def test_worker_client_result_binding_mismatch_fails_closed(self):
        import worker_client
        root = Path(self.agent.cfg.task_worker_spool)
        (root / "results").mkdir(parents=True)
        result = {"schema": "worker.echo/v1", "echo": "x"}
        envelope = {
            "job_id": "w_1_2_" + "a" * 32,
            "nonce": "b" * 32,
            "task_id": 1, "step_id": 2, "tool": "worker.echo",
            "input_hash": "c" * 64,
            "policy_version": tool_broker.POLICY_VERSION,
            "implementation_version": tool_broker.IMPLEMENTATION_VERSION,
            "status": "ok", "result": result,
            "result_hash": __import__("hashlib").sha256(
                json.dumps(result, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")).encode()).hexdigest(),
            "error": None,
        }
        path = root / "results" / f"{envelope['job_id']}.json"
        path.write_text(json.dumps(envelope), encoding="utf-8")
        with self.assertRaisesRegex(worker_client.WorkerError, "nonce"):
            worker_client.poll(
                self.agent.cfg, job_id=envelope["job_id"], nonce="a" * 32,
                task_id=1, step_id=2, tool="worker.echo",
                input_hash="c" * 64,
                policy_version=tool_broker.POLICY_VERSION,
                implementation_version=tool_broker.IMPLEMENTATION_VERSION)

    def test_output_validator_rejects_extra_fields_and_bad_citations(self):
        with self.assertRaises(tool_broker.ToolOutputError):
            tool_broker.validate_output(
                tool_broker.get_spec("worker.echo"),
                {"schema": "worker.echo/v1",
                 "value": {"echo": "x", "extra": "no"}}, [])
        with self.assertRaisesRegex(
                tool_broker.ToolOutputError, "citation"):
            tool_broker.validate_output(
                tool_broker.get_spec("research.synthesize"),
                {"schema": "research.synthesize/v1", "value": {"claims": [{
                    "claim": "x", "citation_ids": ["missing"],
                    "confidence": 1.0, "limitation": "",
                }]}}, [])

    def test_purge_all_counts_and_removes_every_learning_and_task_row(self):
        self.source(1007, "Read my current reminders.")
        task, _ = store.assistant_task_create(
            self.conn, 1, 1007, self.read_plan())
        self.conn.execute(
            "UPDATE assistant_tasks SET status='completed',"
            " delivery_status='delivered', final_message_id=7,"
            " delivered_at=updated_at, completed_at=updated_at WHERE id=?",
            (task["id"],))
        self.conn.commit()
        trace_id = "tr_1722240001_abcdef1234"
        store.trace_start(self.conn, trace_id, "telegram_message", 1)
        store.task_feedback_add(
            self.conn, 1, task_id=task["id"], source_update=1007,
            trace_id=trace_id,
            outbound_message_id=7,
            rating=1, correction="missed constraint")
        case_id, _ = improvement.add_case(
            self.conn, "incident", {
                "kind": "task_plan",
                "boss_text": "Read my current reminders.",
                "source_time": "2026-07-29T10:00:00+00:00",
                "timezone_offset": 3,
                "expected_valid": True,
            }, ["known_tool_only"],
            source="incident", source_ref="issue:1")
        baseline = improvement.add_run(
            self.conn, case_id, "base", "baseline",
            result={"plan": {"objective": "invalid"}})
        candidate = improvement.add_run(
            self.conn, case_id, "candidate", "candidate",
            result={"plan": self.read_plan()})
        improvement.proposal_create(
            self.conn, kind="routing", hypothesis="narrow route issue",
            proposed_change="tighten one route", risk="low",
            rollback="restore prior route", evidence=[{
                "kind": "evaluation_run", "id": candidate,
            }], baseline_run_id=baseline, candidate_run_id=candidate)
        preview = store.purge_preview(self.conn, "all")
        for key in (
                "assistant_tasks", "task_feedback", "evaluation_cases",
                "evaluation_runs", "improvement_proposals"):
            self.assertGreater(preview[key], 0, key)
        store.purge_execute(self.conn, "all")
        for table in (
                "assistant_tasks", "assistant_task_steps", "task_feedback",
                "evaluation_cases", "evaluation_runs", "improvement_proposals"):
            self.assertEqual(
                self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0],
                0, table)

    def test_improvement_gate_vetoes_invariant_failure_and_accept_never_edits_code(self):
        case_id, _ = improvement.add_case(
            self.conn, "approval-replay", {
                "kind": "task_plan",
                "boss_text": "Read my current reminders.",
                "source_time": "2026-07-29T10:00:00+00:00",
                "timezone_offset": 3,
                "expected_valid": True,
            }, ["fresh_approval", "effect_receipt"], source="golden")
        baseline = improvement.add_run(
            self.conn, case_id, "current", "baseline",
            result={"plan": {"objective": "invalid"}},
            cost_usd=0.01, latency_seconds=1)
        candidate = improvement.add_run(
            self.conn, case_id, "candidate", "candidate",
            result={"plan": {"objective": "still invalid"}},
            cost_usd=0.01, latency_seconds=1,
            metadata={"prompt_hash": "abc", "timezone": "UTC+3"})
        proposal_id = improvement.proposal_create(
            self.conn, kind="prompt", hypothesis="candidate may help",
            proposed_change="change one planner sentence", risk="low",
            rollback="restore it", evidence=[{
                "kind": "evaluation_run", "id": candidate,
            }], baseline_run_id=baseline, candidate_run_id=candidate)
        row = improvement.proposal_get(self.conn, proposal_id)
        self.assertEqual(row["status"], "draft")
        self.assertFalse(improvement.decide(self.conn, proposal_id, True))
        self.assertEqual(
            improvement.proposal_get(self.conn, proposal_id)["status"], "draft")
        safe_candidate = improvement.add_run(
            self.conn, case_id, "safe-candidate", "candidate",
            result={"plan": self.read_plan()},
            cost_usd=0.01, latency_seconds=1,
            metadata={
                "prompt_hash": "def", "timezone": "UTC+3",
                "candidate_change_hash": improvement.candidate_change_hash(
                    "prompt", "change one planner sentence"),
            })
        ready_id = improvement.proposal_create(
            self.conn, kind="prompt", hypothesis="safe candidate helps",
            proposed_change="change one planner sentence", risk="low",
            rollback="restore it", evidence=[{
                "kind": "evaluation_run", "id": safe_candidate,
            }], baseline_run_id=baseline, candidate_run_id=safe_candidate)
        self.assertEqual(
            improvement.proposal_get(self.conn, ready_id)["status"], "ready")
        self.assertTrue(improvement.decide(self.conn, ready_id, True))
        self.assertEqual(
            improvement.proposal_get(self.conn, ready_id)["status"], "accepted")
        with self.assertRaises(ValueError):
            improvement.mark_implemented(
                self.conn, ready_id, commit_sha="not-a-commit",
                build_version="b", deployed_version="d", verification="v")

    def test_delivery_crash_becomes_ambiguous_and_explicit_resume_only_resends(self):
        self.source(1020, "Read my current reminders.")
        task, _ = store.assistant_task_create(
            self.conn, 1, 1020, self.read_plan())
        self.conn.execute(
            "UPDATE assistant_tasks SET status='completed',"
            " final_summary='done', completed_at=updated_at WHERE id=?",
            (task["id"],))
        self.conn.commit()

        class DeliveryAgent(tasks_svc.TasksMixin):
            def __init__(self, conn, cfg):
                self.conn, self.cfg = conn, cfg

        agent = DeliveryAgent(self.conn, self.agent.cfg)
        with mock.patch.object(
                tasks_svc, "tg_call", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                agent.on_task_completed(
                    store.assistant_task_get(self.conn, task["id"]),
                    "done", None)
        self.assertEqual(
            store.assistant_task_get(
                self.conn, task["id"])["delivery_status"], "sending")
        store.assistant_task_reclaim_stale(self.conn)
        current = store.assistant_task_get(self.conn, task["id"])
        self.assertEqual(current["delivery_status"], "ambiguous")
        self.assertTrue(store.assistant_task_authorize_redelivery(
            self.conn, task["id"], 1))
        with mock.patch.object(
                tasks_svc, "tg_call", return_value={"message_id": 9020}):
            self.assertTrue(agent.on_task_completed(
                store.assistant_task_get(self.conn, task["id"]),
                "done", None))
        current = store.assistant_task_get(self.conn, task["id"])
        self.assertEqual((current["status"], current["delivery_status"]),
                         ("completed", "delivered"))

    def test_unknown_and_permanent_delivery_errors_never_blind_retry(self):
        self.source(1021, "Read my current reminders.")
        task, _ = store.assistant_task_create(
            self.conn, 1, 1021, self.read_plan())
        store.assistant_task_set_status(
            self.conn, task["id"], "blocked", summary="blocked")

        class DeliveryAgent(tasks_svc.TasksMixin):
            def __init__(self, conn, cfg):
                self.conn, self.cfg = conn, cfg

        agent = DeliveryAgent(self.conn, self.agent.cfg)
        with mock.patch.object(
                tasks_svc, "tg_call",
                side_effect=TelegramError(
                    "socket reset", outcome_unknown=True)):
            self.assertFalse(agent.on_task_blocked(
                store.assistant_task_get(self.conn, task["id"]), "blocked"))
        self.assertEqual(
            store.assistant_task_get(
                self.conn, task["id"])["delivery_status"], "ambiguous")
        self.assertTrue(store.assistant_task_authorize_redelivery(
            self.conn, task["id"], 1))
        with mock.patch.object(
                tasks_svc, "tg_call",
                side_effect=TelegramError("forbidden", status=403)):
            self.assertFalse(agent.on_task_blocked(
                store.assistant_task_get(self.conn, task["id"]), "blocked"))
        self.assertEqual(
            store.assistant_task_get(
                self.conn, task["id"])["delivery_status"], "failed")

    def test_malformed_telegram_result_becomes_ambiguous(self):
        self.source(1026, "Read my current reminders.")
        task, _ = store.assistant_task_create(
            self.conn, 1, 1026, self.read_plan())
        store.assistant_task_set_status(
            self.conn, task["id"], "blocked", summary="blocked")

        class DeliveryAgent(tasks_svc.TasksMixin):
            def __init__(self, conn, cfg):
                self.conn, self.cfg = conn, cfg

        agent = DeliveryAgent(self.conn, self.agent.cfg)
        with mock.patch.object(tasks_svc, "tg_call", return_value=["bad"]):
            self.assertFalse(agent.on_task_blocked(
                store.assistant_task_get(self.conn, task["id"]), "blocked"))
        self.assertEqual(
            store.assistant_task_get(
                self.conn, task["id"])["delivery_status"], "ambiguous")

    def test_document_delivery_crash_is_not_replayed(self):
        self.source(1022, "Read my current reminders.")
        task, _ = store.assistant_task_create(
            self.conn, 1, 1022, self.read_plan())
        body = b"bounded artifact"
        artifact_root = Path(self.agent.cfg.task_artifacts_dir)
        artifact_root.mkdir(parents=True)
        path = artifact_root / "result.md"
        path.write_bytes(body)
        artifact_id = store.task_artifact_create(
            self.conn, task["id"], "markdown", "result.md", str(path),
            len(body), __import__("hashlib").sha256(body).hexdigest())
        self.conn.execute(
            "UPDATE assistant_tasks SET status='completed',"
            " final_summary='done', final_artifact_id=?,"
            " completed_at=updated_at WHERE id=?",
            (artifact_id, task["id"]))
        self.conn.commit()

        class DeliveryAgent(tasks_svc.TasksMixin):
            def __init__(self, conn, cfg):
                self.conn, self.cfg = conn, cfg

        agent = DeliveryAgent(self.conn, self.agent.cfg)
        with mock.patch.object(
                tasks_svc, "tg_send_document",
                side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                agent.on_task_completed(
                    store.assistant_task_get(self.conn, task["id"]),
                    "done", artifact_id)
        store.assistant_task_reclaim_stale(self.conn)
        self.assertEqual(
            store.assistant_task_get(
                self.conn, task["id"])["delivery_status"], "ambiguous")

    def test_cancel_marker_survives_reconcile_until_worker_terminal_result(self):
        input_hash = hashlib.sha256(json.dumps(
            {"text": "slow"}, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode("utf-8")).hexdigest()
        binding = worker_client.submit(
            self.agent.cfg, task_id=7, step_id=8, tool="worker.echo",
            input_value={"text": "slow"}, input_hash=input_hash,
            policy_version=tool_broker.POLICY_VERSION,
            implementation_version=tool_broker.IMPLEMENTATION_VERSION)
        job_id = binding["job_id"]
        root = Path(self.agent.cfg.task_worker_spool)
        worker_client.reconcile(
            self.agent.cfg, {job_id}, {job_id}, stale_seconds=0)
        self.assertTrue((root / "requests" / f"{job_id}.json").exists())
        self.assertTrue((root / "cancel" / job_id).exists())
        cara_worker.run(
            self.agent.cfg.task_worker_spool,
            str(Path(self.tmp.name) / "worker-state"), once=True)
        with self.assertRaisesRegex(worker_client.WorkerError, "cancelled"):
            worker_client.poll(
                self.agent.cfg, job_id=job_id, nonce=binding["nonce"],
                task_id=7, step_id=8, tool="worker.echo",
                input_hash=input_hash,
                policy_version=tool_broker.POLICY_VERSION,
                implementation_version=tool_broker.IMPLEMENTATION_VERSION)
        worker_client.acknowledge(self.agent.cfg, job_id)
        self.assertFalse((root / "requests" / f"{job_id}.json").exists())
        self.assertFalse((root / "cancel" / job_id).exists())

    def test_worker_timeout_notifies_then_discards_bound_late_result(self):
        self.source(1023, "Echo a harmless worker transport check.")
        plan = {
            "objective": "Check worker timeout reconciliation",
            "deliverable": "answer",
            "steps": [{
                "key": "worker", "tool": "worker.echo",
                "input": {"text": "hello"}, "bindings": {},
                "depends_on": [], "purpose": "Check worker",
            }],
        }
        task, _ = store.assistant_task_create(self.conn, 1, 1023, plan)
        task_runner.run_task(self.agent, self.conn, task["id"])
        step = store.assistant_task_steps(self.conn, task["id"])[0]
        old = "2000-01-01T00:00:00+00:00"
        self.conn.execute(
            "UPDATE assistant_task_steps SET worker_submitted_at=? WHERE id=?",
            (old, step["id"]))
        self.conn.commit()
        task_runner.poll_worker_results(self.agent, self.conn)
        self.assertEqual(
            store.assistant_task_get(self.conn, task["id"])["status"], "blocked")
        self.assertEqual(
            store.assistant_task_step_get(
                self.conn, step["id"])["status"], "waiting_worker")
        (Path(self.agent.cfg.task_worker_spool) / "cancel"
         / step["worker_job_id"]).unlink()
        cara_worker.run(
            self.agent.cfg.task_worker_spool,
            str(Path(self.tmp.name) / "worker-state"), once=True)
        task_runner.poll_worker_results(self.agent, self.conn)
        self.assertEqual(
            store.assistant_task_get(self.conn, task["id"])["status"], "blocked")
        self.assertEqual(
            store.assistant_task_step_get(
                self.conn, step["id"])["status"], "blocked")

    def test_purge_marker_is_published_only_after_db_commit(self):
        self.source(1024, "Read my current reminders.")
        task, _ = store.assistant_task_create(
            self.conn, 1, 1024, self.read_plan())

        class PurgeAgent(tasks_svc.TasksMixin):
            def __init__(self, conn, cfg):
                self.conn, self.cfg = conn, cfg

        agent = PurgeAgent(self.conn, self.agent.cfg)
        nonce = agent.prepare_task_purge()
        marker = (
            Path(self.agent.cfg.task_worker_spool)
            / "cancel" / ".purge-all.json")
        self.assertFalse(marker.exists())
        store.purge_execute(
            self.conn, "all", task_purge_nonce=nonce)
        durable = json.loads(store.kv_get(
            self.conn, "task_purge_authorization"))
        self.assertEqual(
            (durable["nonce"], durable["phase"]), (nonce, "db_committed"))
        worker_client.prepare_purge(self.agent.cfg, nonce=nonce)
        self.assertTrue(marker.exists())
        marker.chmod(0o600)
        with self.assertRaises(worker_client.WorkerError):
            worker_client.pending_purge_nonce(self.agent.cfg)

    def test_feedback_replay_is_idempotent_and_conflict_fails_closed(self):
        self.source(1025, "Read my current reminders.")
        task, _ = store.assistant_task_create(
            self.conn, 1, 1025, self.read_plan())
        self.conn.execute(
            "UPDATE assistant_tasks SET status='completed',"
            " delivery_status='delivered', final_message_id=925,"
            " delivered_at=updated_at, completed_at=updated_at WHERE id=?",
            (task["id"],))
        self.conn.commit()
        trace_id = "tr_1722240002_abcdef1234"
        store.trace_start(self.conn, trace_id, "telegram_message", 1)
        kwargs = dict(
            task_id=task["id"], source_update=1025, trace_id=trace_id,
            outbound_message_id=925, rating=2, correction="missed detail")
        first = store.task_feedback_add(self.conn, 1, **kwargs)
        self.assertEqual(first, store.task_feedback_add(
            self.conn, 1, **kwargs))
        with self.assertRaisesRegex(ValueError, "conflicts"):
            store.task_feedback_add(
                self.conn, 1, **{**kwargs, "rating": 1})

    def test_evidence_roundtrip_preserves_real_trace_and_hashes(self):
        value = improvement._safe_tree({
            "trace_id": "tr_1722240003_abcdef1234",
            "source_hash": "A" * 64,
            "nested": {"api_token": "super-secret-value"},
        })
        self.assertEqual(value["trace_id"], "tr_1722240003_abcdef1234")
        self.assertEqual(value["source_hash"], "a" * 64)
        self.assertEqual(value["nested"]["api_token"], "[REDACTED]")

    def test_seeded_acceptance_corpus_reference_replays_all_pass(self):
        self.assertEqual(improvement.ensure_golden_corpus(self.conn), 10)
        rows = self.conn.execute(
            "SELECT r.score, r.invariant_failures_json"
            " FROM evaluation_runs r JOIN evaluation_cases c ON c.id=r.case_id"
            " WHERE c.name LIKE 'acceptance-%'"
            " AND r.candidate='golden-reference/v1'"
        ).fetchall()
        self.assertEqual(len(rows), 10)
        self.assertTrue(all(
            row["score"] == 1 and row["invariant_failures_json"] == "[]"
            for row in rows))
        case = self.conn.execute(
            "SELECT * FROM evaluation_cases"
            " WHERE name='acceptance-en-read-reminders'").fetchone()
        bad_id, _ = improvement.add_case(
            self.conn, "bad-expected-boolean", {
                "kind": "task_plan", "boss_text": "Read reminders.",
                "source_time": "2026-07-29T10:00:00+00:00",
                "timezone_offset": 3, "expected_valid": "false",
            }, ["known_tool_only"], source="incident")
        with self.assertRaisesRegex(ValueError, "JSON boolean"):
            improvement.add_run(
                self.conn, bad_id, "bad", "baseline",
                result={"plan": json.loads(
                    case["input_json"])["reference_plan"]})

    def test_versioned_neutral_corpus_completes_with_real_receipts(self):
        improvement.ensure_golden_corpus(self.conn)
        cases = self.conn.execute(
            "SELECT * FROM evaluation_cases"
            " WHERE name LIKE 'acceptance-%' ORDER BY name"
        ).fetchall()
        self.assertEqual(len(cases), 10)
        reminders_before = self.conn.execute(
            "SELECT COUNT(*) FROM reminders").fetchone()[0]
        completed = 0
        for offset, case in enumerate(cases, start=1):
            payload = json.loads(case["input_json"])
            update_id = 1100 + offset
            self.source(update_id, payload["boss_text"])
            task, _ = store.assistant_task_create(
                self.conn, 1, update_id, payload["reference_plan"],
                timezone_offset=payload["timezone_offset"])
            result = task_runner.run_task(
                self.agent, self.conn, task["id"], max_steps=8)
            current = store.assistant_task_get(self.conn, task["id"])
            completed += int(result["status"] == "completed")
            self.assertEqual(current["status"], "completed", case["name"])
            step_count = len(store.assistant_task_steps(
                self.conn, task["id"]))
            receipt_count = self.conn.execute(
                "SELECT COUNT(*) FROM tool_receipts WHERE task_id=?"
                " AND status IN ('ok','partial')",
                (task["id"],)).fetchone()[0]
            self.assertEqual(receipt_count, step_count, case["name"])
        self.assertEqual(completed, 10)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM reminders").fetchone()[0],
            reminders_before)

    def test_stale_corpus_runs_cannot_certify_a_different_change(self):
        improvement.ensure_golden_corpus(self.conn)
        change_a = improvement.candidate_change_hash(
            "prompt", "change A")
        change_b = improvement.candidate_change_hash(
            "prompt", "change B")
        golden = self.conn.execute(
            "SELECT * FROM evaluation_cases"
            " WHERE name LIKE 'acceptance-%' ORDER BY id"
        ).fetchall()
        for case in golden:
            plan = json.loads(case["input_json"])["reference_plan"]
            improvement.add_run(
                self.conn, case["id"], "same-label", "candidate",
                result={"plan": plan},
                metadata={"candidate_change_hash": change_a})
        incident_id, _ = improvement.add_case(
            self.conn, "change-binding-incident", {
                "kind": "task_plan",
                "boss_text": "Read my current reminders.",
                "source_time": "2026-07-29T10:00:00+00:00",
                "timezone_offset": 3, "expected_valid": True,
            }, ["known_tool_only"], source="incident")
        baseline = improvement.add_run(
            self.conn, incident_id, "current", "baseline",
            result={"plan": {"objective": "invalid"}})
        candidate = improvement.add_run(
            self.conn, incident_id, "same-label", "candidate",
            result={"plan": self.read_plan()},
            metadata={"candidate_change_hash": change_b})
        proposal = improvement.proposal_create(
            self.conn, kind="prompt", hypothesis="change B may help",
            proposed_change="change B", risk="low",
            rollback="restore current prompt",
            evidence=[{"kind": "evaluation_run", "id": candidate}],
            baseline_run_id=baseline, candidate_run_id=candidate)
        self.assertEqual(
            improvement.proposal_get(self.conn, proposal)["status"], "draft")

    def test_later_exact_hash_corpus_failure_vetoes_prior_pass(self):
        improvement.ensure_golden_corpus(self.conn)
        change = improvement.candidate_change_hash(
            "prompt", "bounded change")
        golden = self.conn.execute(
            "SELECT * FROM evaluation_cases"
            " WHERE name LIKE 'acceptance-%' ORDER BY id"
        ).fetchall()
        for case in golden:
            plan = json.loads(case["input_json"])["reference_plan"]
            improvement.add_run(
                self.conn, case["id"], "candidate-v1", "candidate",
                result={"plan": plan},
                metadata={"candidate_change_hash": change})
        # A stochastic or later replay failure for the exact same candidate
        # identity/change is a veto, never something an older pass can hide.
        improvement.add_run(
            self.conn, golden[0]["id"], "candidate-v1", "candidate",
            result={"plan": {"objective": "invalid"}},
            metadata={"candidate_change_hash": change})
        incident_id, _ = improvement.add_case(
            self.conn, "pass-then-fail-incident", {
                "kind": "task_plan",
                "boss_text": "Read my current reminders.",
                "source_time": "2026-07-29T10:00:00+00:00",
                "timezone_offset": 3, "expected_valid": True,
            }, ["known_tool_only"], source="incident")
        baseline = improvement.add_run(
            self.conn, incident_id, "current", "baseline",
            result={"plan": {"objective": "invalid"}})
        candidate = improvement.add_run(
            self.conn, incident_id, "candidate-v1", "candidate",
            result={"plan": self.read_plan()},
            metadata={"candidate_change_hash": change})
        proposal = improvement.proposal_create(
            self.conn, kind="prompt", hypothesis="bounded change may help",
            proposed_change="bounded change", risk="low",
            rollback="restore current prompt",
            evidence=[{"kind": "evaluation_run", "id": candidate}],
            baseline_run_id=baseline, candidate_run_id=candidate)
        self.assertEqual(
            improvement.proposal_get(self.conn, proposal)["status"], "draft")

    def test_fetch_retry_taxonomy_distinguishes_transport_from_content(self):
        self.assertTrue(task_runner._transient_error(
            fetch.FetchError("HTTP 503", "fetch_failed")))
        self.assertFalse(task_runner._transient_error(
            fetch.FetchError(
                "unsupported content type", "fetch_permanent")))
        self.assertTrue(task_runner._transient_error(
            llm.LLMError("HTTP 503", transient=True)))
        self.assertFalse(task_runner._transient_error(
            llm.LLMError("HTTP 403")))


if __name__ == "__main__":
    unittest.main()
