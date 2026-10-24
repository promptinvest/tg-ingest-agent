"""CF-04 asynchronous gateway accounting, responsiveness and no-replay checks."""
import io
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from unittest import mock

import call_budget
import common
import fleet_inference as inference
import llm
import store
from testlib import make_config


def body(content='{"ok":true}', finish="stop"):
    return {"choices": [{"message": {"content": content}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


def binding(number="1"):
    return {"request_id": "fixture-" + number, "phase": "planning", "nonce": number * 32,
            "input_hash": number * 64, "source_hash": "2" * 64,
            "inventory_hash": "3" * 64, "facts_hash": "4" * 64,
            "capability_hash": "5" * 64, "policy_version": "fixture-policy/v1"}


class AsyncProfileGateway(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.open_db(Path(self.tmp.name) / "inference.db")
        self.cfg = make_config(LLM_PROFILES_JSON=json.dumps({"task_planner": {
            "primary": "deepseek-4-flash", "fallbacks": ["deepseek-v4-pro"],
            "max_tokens": 80, "timeout": 90, "json_required": True}}))
        self.current = True
        self.receipts, self.calls, self.release_events = [], [], []
        common.set_current_trace(None)

    def tearDown(self):
        for event in self.release_events:
            event.set()
        # Only transport futures are joined here; never advance policy after a
        # patch has ended, which could accidentally dispatch an unscripted call.
        for call in self.calls:
            if call.future is not None:
                try:
                    call.future.result(timeout=3)
                except Exception:
                    pass
        self.conn.close()
        common.set_current_trace(None)
        self.tmp.cleanup()

    def call(self, **options):
        defaults = dict(
            profile="task_planner", binding=binding(),
            before_dispatch=lambda phase, receipt: self.receipts.append((phase, receipt)),
            is_current=lambda phase: self.current)
        defaults.update(options)
        call = inference.ProfileCall(
            self.cfg, self.conn, "fleet_planner",
            [{"role": "user", "content": "A synthetic question"}], **defaults)
        self.calls.append(call)
        return call

    @staticmethod
    def wait_transport(call):
        if call.future is not None:
            try:
                call.future.result(timeout=3)
            except Exception:
                pass

    def finish(self, call):
        for _ in range(10):
            if call.done:
                return
            self.wait_transport(call)
            call.advance()
        self.fail("fixture profile did not finish within its bounded phases")

    def test_blocked_transport_keeps_owner_and_accounting_on_creating_thread(self):
        entered, release = threading.Event(), threading.Event()
        self.release_events.append(release)
        owner, transport_threads, accounting_threads = threading.get_ident(), [], []
        reserve, settle = call_budget.reserve, call_budget.settle

        def transport(prepared):
            transport_threads.append(threading.get_ident())
            self.assertNotIn("conn", prepared)
            entered.set()
            if not release.wait(3):
                raise AssertionError("fixture transport was never released")
            return body()

        def reserve_on_owner(*args, **kwargs):
            accounting_threads.append(threading.get_ident())
            return reserve(*args, **kwargs)

        def settle_on_owner(*args, **kwargs):
            accounting_threads.append(threading.get_ident())
            return settle(*args, **kwargs)

        call = self.call()
        with mock.patch.object(llm, "transport_chat", side_effect=transport), \
                mock.patch.object(call_budget, "reserve", side_effect=reserve_on_owner), \
                mock.patch.object(call_budget, "settle", side_effect=settle_on_owner):
            self.assertFalse(call.advance())
            self.assertTrue(entered.wait(2))
            self.assertFalse(call.advance())
            # Owner messages/reminder writes can proceed while the fake model
            # remains blocked. The adapter did not wait or move SQLite work.
            store.kv_set(self.conn, "fixture-owner-update", "handled")
            store.reminder_add(self.conn, 111, "fixture due reminder", "2026-10-24T09:00:00+00:00")
            self.assertEqual(store.kv_get(self.conn, "fixture-owner-update"), "handled")
            release.set()
            self.finish(call)
        self.assertIsNone(call.error)
        self.assertEqual(call.result, '{"ok":true}')
        self.assertEqual(accounting_threads, [owner, owner])
        self.assertTrue(all(thread != owner for thread in transport_threads))
        self.assertEqual(self.conn.execute("SELECT kind FROM llm_usage").fetchone()[0], "chat")
        self.assertEqual(len(self.receipts), 1)
        self.assertEqual(self.receipts[0][0], call.binding)
        self.assertEqual(self.receipts[0][1]["reservation_id"], call.usage_ids[0])

    def test_one_background_transport_preserves_backpressure_without_starting_queue_deadline(self):
        release = threading.Event()
        self.release_events.append(release)
        entered = [threading.Event()]
        counter_lock, started = threading.Lock(), []
        clock = {"now": 100.0}

        def transport(prepared):
            with counter_lock:
                index = len(started)
                started.append(prepared["timeout"])
            if index < 1:
                entered[index].set()
                if not release.wait(3):
                    raise AssertionError("fixture queue was never released")
            return body()

        calls = [self.call(binding=binding(str(index))) for index in range(1, 4)]
        with mock.patch.object(llm, "transport_chat", side_effect=transport), \
                mock.patch.object(inference.time, "monotonic", lambda: clock["now"]):
            for call in calls:
                self.assertFalse(call.advance())
            self.assertTrue(all(event.wait(2) for event in entered))
            self.assertEqual(len(started), 1)
            self.assertEqual(len(calls[1].usage_ids), 0)
            self.assertEqual(len(calls[2].usage_ids), 0)
            self.assertIsNone(calls[2].steps)
            self.assertEqual(calls[2].state, "queued")
            clock["now"] += 1_000
            release.set()
            for call in calls[:2]:
                self.finish(call)
            self.finish(calls[2])
        self.assertIsNone(calls[2].error)
        self.assertEqual(started, [90, 90, 90])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM llm_usage").fetchone()[0], 3)

    def test_unknown_paid_transport_stops_without_retry_or_fallback(self):
        call = self.call()
        with mock.patch.object(llm, "transport_chat", side_effect=TimeoutError("fixture timeout")) as send:
            self.finish(call)
            for _ in range(3):
                self.assertTrue(call.advance())
        self.assertIsInstance(call.error, llm.LLMError)
        self.assertTrue(call.error.usage_unknown)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(len(call.usage_ids), 1)
        row = self.conn.execute("SELECT kind,cost_usd FROM llm_usage").fetchone()
        self.assertEqual(row["kind"], "unknown_chat")
        self.assertGreater(row["cost_usd"], 0)

    def test_definite_rejection_retains_existing_profile_fallback(self):
        rejected = HTTPError("https://fixture.invalid", 403, "denied", {}, io.BytesIO(b"fixture private body"))
        call = self.call()
        with mock.patch.object(llm, "transport_chat", side_effect=[rejected, body()]) as send:
            self.finish(call)
        self.assertIsNone(call.error)
        models = [json.loads(item.args[0]["request"].data)["model"] for item in send.call_args_list]
        self.assertEqual(models, ["deepseek-4-flash", "deepseek-v4-pro"])
        rows = self.conn.execute("SELECT kind,cost_usd FROM llm_usage ORDER BY id").fetchall()
        self.assertEqual((rows[0]["kind"], rows[0]["cost_usd"]), ("rejected_chat", 0))
        self.assertEqual(rows[1]["kind"], "chat")
        self.assertTrue(store.cooldown_active(self.conn, "task_planner", "deepseek-4-flash"))

    def test_structured_length_retry_uses_same_profile_and_existing_cap(self):
        call = self.call()
        with mock.patch.object(llm, "transport_chat", side_effect=[body('{"ok":', "length"), body()]) as send:
            self.finish(call)
        self.assertIsNone(call.error)
        requests = [json.loads(item.args[0]["request"].data) for item in send.call_args_list]
        self.assertEqual([request["max_tokens"] for request in requests], [80, 160])
        self.assertEqual([request["model"] for request in requests], ["deepseek-4-flash"] * 2)
        self.assertFalse(store.cooldown_active(self.conn, "task_planner", "deepseek-4-flash"))

    def test_not_sent_transient_retry_is_a_clock_deadline_not_an_owner_sleep(self):
        error = llm.LLMError("timeout before sending", transient=True)
        error.request_not_sent = True
        clock = {"now": 100.0}
        call = self.call()
        with mock.patch.object(llm, "transport_chat", side_effect=[error, body()]) as send, \
                mock.patch.object(inference.time, "monotonic", lambda: clock["now"]), \
                mock.patch.object(inference.time, "sleep", side_effect=AssertionError("owner slept")):
            call.advance()
            self.wait_transport(call)
            self.assertFalse(call.advance())
            self.assertEqual(call.state, "retry_wait")
            self.assertEqual(send.call_count, 1)
            clock["now"] += 0.8
            self.finish(call)
        self.assertIsNone(call.error)
        self.assertEqual(send.call_count, 2)

    def test_stale_queued_phase_and_cancelled_queue_never_reserve_or_send(self):
        stale, cancelled = self.call(), self.call(binding=binding("2"))
        self.current = False
        self.assertTrue(cancelled.cancel())
        with mock.patch.object(llm, "transport_chat") as send:
            self.assertTrue(stale.advance())
            self.assertTrue(cancelled.advance())
        self.assertIsInstance(stale.error, inference.StalePhase)
        self.assertEqual(cancelled.state, "cancelled")
        self.assertEqual(stale.usage_ids + cancelled.usage_ids, ())
        send.assert_not_called()

    def test_revision_changes_after_reservation_release_unsent_usage(self):
        def record_then_change(phase, receipt):
            self.receipts.append((phase, receipt))
            self.current = False
        call = self.call(before_dispatch=record_then_change)
        with mock.patch.object(llm, "transport_chat") as send:
            self.assertTrue(call.advance())
        self.assertIsInstance(call.error, inference.StalePhase)
        send.assert_not_called()
        self.assertEqual(call.cost(), 0)
        self.assertEqual(self.conn.execute("SELECT kind FROM llm_usage").fetchone()[0], "rejected_chat")

    def test_late_stale_completion_is_metered_once_without_content_acceptance(self):
        call = self.call()
        with mock.patch.object(llm, "transport_chat", return_value=body()) as send:
            call.advance()
            self.wait_transport(call)
            self.current = False
            self.assertTrue(call.advance())
            self.assertTrue(call.advance())
        self.assertIsInstance(call.error, inference.StalePhase)
        self.assertIsNone(call.result)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(self.conn.execute("SELECT kind FROM llm_usage").fetchone()[0], "chat")

    def test_unconfirmed_inflight_cancel_discards_result_after_settlement(self):
        entered, release = threading.Event(), threading.Event()
        self.release_events.append(release)
        def transport(prepared):
            entered.set()
            if not release.wait(3):
                raise AssertionError("fixture cancellation was never released")
            return body()
        call = self.call()
        with mock.patch.object(llm, "transport_chat", side_effect=transport):
            call.advance()
            self.assertTrue(entered.wait(2))
            self.assertFalse(call.cancel())
            self.assertFalse(call.done)
            release.set()
            self.finish(call)
        self.assertEqual(call.state, "cancelled")
        self.assertIsNone(call.result)
        self.assertEqual(self.conn.execute("SELECT kind FROM llm_usage").fetchone()[0], "chat")

    def test_cost_is_phase_local_and_excludes_other_concurrent_owner_usage(self):
        call = self.call()
        with mock.patch.object(llm, "transport_chat", return_value=body()):
            call.advance()
            store.usage_add(self.conn, "unrelated", "chat", "deepseek-4-flash", cost_usd=0.25)
            self.finish(call)
        self.assertAlmostEqual(call.cost(), llm.chat_cost("deepseek-4-flash", 10, 5, llm.pricing_table(self.cfg)))
        self.assertAlmostEqual(store.usage_total(self.conn, "day"), 0.25 + call.cost())

    def test_binding_snapshot_budget_stop_and_thread_ownership(self):
        original = binding()
        call = self.call(binding=original)
        original["nonce"] = "changed"
        self.assertNotEqual(call.binding["nonce"], original["nonce"])
        self.cfg.budget_daily_usd = 0.001
        store.usage_add(self.conn, "fixture", "chat", "deepseek-4-flash", cost_usd=1)
        with mock.patch.object(llm, "transport_chat") as send:
            self.assertTrue(call.advance())
        self.assertIsInstance(call.error, llm.BudgetExceeded)
        send.assert_not_called()
        errors = []
        def wrong_thread():
            try:
                call.advance()
            except RuntimeError as exc:
                errors.append(str(exc))
        thread = threading.Thread(target=wrong_thread)
        thread.start()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)

    def test_receipt_failure_is_unsent_and_error_summary_does_not_echo_content(self):
        def fail_receipt(phase, receipt):
            raise OSError("HTTP 503 private fixture content must not leave the session")
        call = self.call(before_dispatch=fail_receipt)
        with mock.patch.object(llm, "transport_chat") as send:
            self.assertTrue(call.advance())
            self.assertTrue(call.advance())
        send.assert_not_called()
        self.assertEqual(call.cost(), 0)
        self.assertEqual(call.error_summary, "OSError HTTP 503")

    def test_settlement_database_failure_reuses_same_result_without_new_send(self):
        call = self.call()
        settle = call_budget.settle
        failed = {"once": False}
        def fail_once(*args, **kwargs):
            if not failed["once"]:
                failed["once"] = True
                raise sqlite3.OperationalError("fixture database temporarily unavailable")
            return settle(*args, **kwargs)
        with mock.patch.object(llm, "transport_chat", return_value=body()) as send, \
                mock.patch.object(call_budget, "settle", side_effect=fail_once):
            call.advance()
            self.wait_transport(call)
            original = call.future
            with self.assertRaises(sqlite3.OperationalError):
                call.advance()
            self.assertIs(call.future, original)
            self.assertFalse(call.done)
            self.assertTrue(call.advance())
        self.assertIsNone(call.error)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(len(call.usage_ids), 1)
        self.assertEqual(self.conn.execute("SELECT kind FROM llm_usage").fetchone()[0], "chat")

    def test_authority_read_failure_after_completion_cannot_resubmit(self):
        failing = {"value": False}
        def current(phase):
            if failing["value"]:
                raise sqlite3.OperationalError("fixture authority temporarily unavailable")
            return True
        call = self.call(is_current=current)
        with mock.patch.object(llm, "transport_chat", return_value=body()) as send:
            call.advance()
            self.wait_transport(call)
            failing["value"] = True
            with self.assertRaises(sqlite3.OperationalError):
                call.advance()
            self.assertTrue(call.advance())
        self.assertTrue(call.done)
        self.assertIsNone(call.result)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(len(call.usage_ids), 1)

    def test_prepare_database_failure_never_repeats_a_reservation(self):
        call = self.call()
        error = sqlite3.OperationalError("fixture persistence unavailable")
        with mock.patch.object(llm, "prepare_chat", side_effect=error) as prepare, \
                mock.patch.object(llm, "transport_chat") as send:
            with self.assertRaises(sqlite3.OperationalError):
                call.advance()
            self.assertTrue(call.advance())
        self.assertIs(call.error, error)
        self.assertEqual(prepare.call_count, 1)
        send.assert_not_called()

    def test_source_trace_is_bound_during_accounting_and_restored_after_pump(self):
        common.set_current_trace("fixture-source-trace")
        call = self.call()
        common.set_current_trace("fixture-other-turn")
        seen = []
        def record(phase, receipt):
            seen.append(common.current_trace())
        call.before_dispatch = record
        with mock.patch.object(llm, "transport_chat", return_value=body()):
            call.advance()
            self.assertEqual(common.current_trace(), "fixture-other-turn")
            self.finish(call)
        self.assertEqual(seen, ["fixture-source-trace"])
        self.assertEqual(common.current_trace(), "fixture-other-turn")
        self.assertEqual(self.conn.execute("SELECT trace_id FROM llm_usage").fetchone()[0],
                         "fixture-source-trace")

    def test_foreground_model_succeeds_while_background_uses_real_global_transport(self):
        entered, release = threading.Event(), threading.Event()
        self.release_events.append(release)
        foreground_seen = []

        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self):
                return json.dumps(body()).encode("utf-8")

        def opener(request, timeout=None):
            messages = json.loads(request.data)["messages"]
            if messages[0]["content"] == "Foreground owner question":
                foreground_seen.append(True)
                return Response()
            entered.set()
            if not release.wait(3):
                raise AssertionError("fixture background transport was not released")
            return Response()

        background = self.call()
        queued = self.call(binding=binding("2"))
        # No llm transport or inference_io semaphore is mocked: the background
        # request occupies one actual global slot while owner chat uses the other.
        with mock.patch.object(llm, "urlopen", side_effect=opener):
            self.assertFalse(background.advance())
            self.assertTrue(entered.wait(2))
            self.assertFalse(queued.advance())
            self.assertEqual(queued.usage_ids, ())
            answer = llm.chat(self.cfg, self.conn, "converse",
                              [{"role": "user", "content": "Foreground owner question"}],
                              model="deepseek-4-flash", max_tokens=40)
            self.assertEqual(answer, '{"ok":true}')
            self.assertEqual(foreground_seen, [True])
            self.assertFalse(background.advance())
            self.assertFalse(release.is_set())
            release.set()
            self.finish(background)
            self.finish(queued)
        self.assertIsNone(background.error)
        self.assertIsNone(queued.error)
        self.assertEqual(inference.MAX_ACTIVE_TRANSPORTS, 1)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM llm_usage WHERE kind='chat'").fetchone()[0], 3)
