"""Native-call budget and unknown-outcome regressions without real transports."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import llm
import store
import task_runner
from testlib import make_config


class TaskSynthesisBudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = store.open_db(Path(self.tmp.name) / "tasks.db")
        self.addCleanup(self.conn.close)
        self.cfg = make_config()
        update = {"update_id": 4100, "message": {"message_id": 410,
                  "chat": {"id": 111}, "from": {"id": 111}, "text": "Compare my reminders"}}
        store.telegram_update_receive(self.conn, update, 111)
        store.convo_add(self.conn, 111, "user", "Compare my reminders", source="boss", update_id=4100)
        self.task, _ = store.assistant_task_create(self.conn, 111, 4100, {
            "objective": "Compare my reminders", "deliverable": "answer", "steps": [{
                "key": "read", "tool": "reminders.read", "input": {}, "bindings": {},
                "depends_on": [], "purpose": "Read existing reminders"}, {
                "key": "synthesize", "tool": "research.synthesize",
                "input": {"receipt_steps": ["read"]}, "bindings": {},
                "depends_on": ["read"], "purpose": "Compare recorded facts"}]})
        self.step = store.assistant_task_steps(self.conn, self.task["id"])[1]
        self.profile = {"task_synthesis": {
            "primary": "deepseek-4-flash", "fallbacks": ["deepseek-v4-pro"],
            "max_tokens": 640, "json_required": True}}
        self.receipts = [("read", {"tool": "reminders.read", "summary": "Synthetic reminder",
            "data_json": "{}", "evidence_json": json.dumps([{"id": "e1", "label": "Reminder"}])})]
        self.valid = json.dumps({"claims": [{"claim": "A reminder exists.", "citation_ids": ["e1"],
            "confidence": .8, "limitation": ""}], "recommendation": {"text": "Review its timing.",
            "citation_ids": ["e1"], "confidence": .8, "tradeoffs": []},
            "conflicts": [], "unknowns": []})

    def result(self, text):
        return {"choices": [{"message": {"content": text}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    def synthesize(self):
        with mock.patch.object(task_runner, "_prior_receipts", return_value=self.receipts), \
                mock.patch.object(llm, "profiles", return_value=self.profile), \
                mock.patch.object(llm.time, "sleep"):
            return task_runner._synthesize(self.cfg, self.conn,
                store.assistant_task_get(self.conn, self.task["id"]), self.step,
                {"receipt_steps": ["read"]})

    def test_remaining_one_native_call_cannot_expand_into_fallback_calls(self):
        self.conn.execute("UPDATE assistant_tasks SET model_calls=3 WHERE id=?", (self.task["id"],))
        self.conn.commit()
        with mock.patch.object(llm, "transport_chat", side_effect=[self.result("not JSON"), self.result(self.valid)]) as send:
            with self.assertRaises(task_runner.TaskBlocked):
                self.synthesize()
        self.assertEqual(send.call_count, 1)
        self.assertEqual(store.assistant_task_get(self.conn, self.task["id"])["model_calls"], 4)

    def test_reservation_above_remaining_task_cost_never_reaches_transport(self):
        self.conn.execute("UPDATE assistant_tasks SET task_cost_usd=.149999999 WHERE id=?", (self.task["id"],))
        self.conn.commit()
        with mock.patch.object(llm, "transport_chat", return_value=self.result(self.valid)) as send:
            with self.assertRaises(task_runner.TaskBlocked):
                self.synthesize()
        send.assert_not_called()
        usage = self.conn.execute("SELECT kind,cost_usd FROM llm_usage").fetchall()
        self.assertEqual([tuple(r) for r in usage], [("rejected_chat", 0)])
        row = store.assistant_task_get(self.conn, self.task["id"])
        self.assertEqual(row["model_calls"], 0)
        self.assertAlmostEqual(row["task_cost_usd"], .149999999)

    def test_unknown_native_outcome_stops_failover_and_restart_replacement(self):
        with mock.patch.object(llm, "transport_chat", side_effect=llm.LLMError("HTTP 503", transient=True)) as send:
            with self.assertRaises(llm.LLMError):
                self.synthesize()
            self.assertEqual(send.call_count, 1)
            self.conn.close()
            self.conn = store.open_db(Path(self.tmp.name) / "tasks.db")
            self.addCleanup(self.conn.close)
            with self.assertRaises(task_runner.TaskBlocked):
                self.synthesize()
            self.assertEqual(send.call_count, 1)
        self.assertEqual(self.conn.execute("SELECT kind FROM llm_usage").fetchone()[0], "unknown_chat")
        self.assertEqual(store.assistant_task_get(self.conn, self.task["id"])["model_calls"], 1)

    def test_safe_fallback_counts_each_call_and_excludes_unrelated_usage(self):
        def transport(prepared):
            if prepared["model"] == "deepseek-4-flash":
                store.usage_add(self.conn, "unrelated", "chat", "deepseek-4-flash", cost_usd=.02)
                return self.result("not JSON")
            return self.result(self.valid)
        with mock.patch.object(llm, "transport_chat", side_effect=transport) as send:
            self.assertEqual(self.synthesize()[0], "ok")
        self.assertEqual(send.call_count, 2)
        row = store.assistant_task_get(self.conn, self.task["id"])
        self.assertEqual(row["model_calls"], 2)
        native_cost = self.conn.execute("SELECT SUM(cost_usd) FROM llm_usage WHERE skill='task_synthesis'").fetchone()[0]
        self.assertAlmostEqual(row["task_cost_usd"], native_cost)

    def test_success_without_provider_usage_cannot_trigger_replacement(self):
        result = self.result(self.valid)
        result.pop("usage")
        with mock.patch.object(llm, "transport_chat", return_value=result) as send:
            with self.assertRaises(task_runner.TaskBlocked):
                self.synthesize()
            with self.assertRaises(task_runner.TaskBlocked):
                self.synthesize()
        self.assertEqual(send.call_count, 1)
        self.assertEqual(self.conn.execute("SELECT kind FROM llm_usage").fetchone()[0], "unknown_chat")

    def test_dispatch_hook_interrupt_settles_only_its_unused_reservation(self):
        historical = store.usage_add(self.conn, "older", "unknown_chat", "deepseek-4-flash", cost_usd=.01)
        with mock.patch.object(llm, "transport_chat") as send:
            with self.assertRaises(KeyboardInterrupt):
                llm.chat_profile(self.cfg, self.conn, "task_synthesis",
                    [{"role": "user", "content": "Synthetic request"}],
                    profile="task_synthesis", before_dispatch=mock.Mock(side_effect=KeyboardInterrupt))
        send.assert_not_called()
        self.assertEqual([tuple(r) for r in self.conn.execute("SELECT kind,cost_usd FROM llm_usage ORDER BY id")],
                         [("unknown_chat", .01), ("rejected_chat", 0)])
        self.assertEqual(self.conn.execute("SELECT kind FROM llm_usage WHERE id=?", (historical,)).fetchone()[0], "unknown_chat")

    def test_default_profile_keeps_the_original_synchronous_chat_path(self):
        with mock.patch.object(llm, "chat", return_value=self.valid) as chat, \
                mock.patch.object(llm, "prepare_chat", side_effect=AssertionError("guarded path entered")):
            self.assertEqual(llm.chat_profile(self.cfg, self.conn, "ordinary",
                [{"role": "user", "content": "Synthetic request"}], profile="converse"), self.valid)
        self.assertEqual(chat.call_count, 1)


if __name__ == "__main__":
    unittest.main()
