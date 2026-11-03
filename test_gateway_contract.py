"""Provider response validation must preserve unknown reservations and history."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import llm
import store
from testlib import make_config


class GatewayResponseContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.open_db(Path(self.tmp.name) / "usage.db")
        self.cfg = make_config(BUDGET_DAILY_USD="100", BUDGET_MONTHLY_USD="100")
        self.history_id = store.usage_add(
            self.conn, "fixture", "unknown_chat", "historical-model", cost_usd=.0123)
        self.history = self._row(self.history_id)
        self.messages = [{"role": "user", "content": "Synthetic source " * 30}]

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _row(self, row_id):
        return dict(self.conn.execute("SELECT * FROM llm_usage WHERE id=?", (row_id,)).fetchone())

    def _prepared(self):
        return llm.prepare_chat(self.cfg, self.conn, "converse", self.messages,
                                model="deepseek-4-flash", max_tokens=40)

    @staticmethod
    def _response():
        return {"choices": [{"message": {"content": "Synthetic answer"},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10}}

    def _assert_unknown_rejection(self, response):
        prepared = self._prepared()
        before = self._row(prepared["reserved"])
        with self.assertRaises(llm.LLMError) as rejected:
            llm.finish_chat(self.cfg, self.conn, prepared, data=response)
        self.assertTrue(getattr(rejected.exception, "usage_unknown", False))
        self.assertEqual(self._row(prepared["reserved"]), before)
        self.assertEqual(self._row(self.history_id), self.history)
        self.assertGreaterEqual(store.usage_total(self.conn, "day"), self.history["cost_usd"])

    def test_negative_counters_never_release_or_credit_the_reservation(self):
        for key in ("prompt_tokens", "completion_tokens"):
            with self.subTest(key=key):
                response = self._response()
                response["usage"][key] = -1000
                self._assert_unknown_rejection(response)

    def test_invalid_counter_types_and_bounds_keep_usage_unknown(self):
        invalid = (True, False, .5, 1.0, "10", "", [], {},
                   float("nan"), float("inf"), 10_000_001)
        for key in ("prompt_tokens", "completion_tokens"):
            for value in invalid:
                with self.subTest(key=key, value=value):
                    response = self._response()
                    response["usage"][key] = value
                    self._assert_unknown_rejection(response)

    def test_non_object_response_is_a_typed_unknown_outcome(self):
        for response in (None, [], "provider text", 1, False):
            with self.subTest(response=response):
                self._assert_unknown_rejection(response)

    def test_malformed_usage_objects_do_not_become_reported_usage(self):
        for usage in ([], "", "invalid", 1, False):
            with self.subTest(usage=usage):
                response = self._response()
                response["usage"] = usage
                self._assert_unknown_rejection(response)

    def test_malformed_choice_envelopes_keep_the_reservation(self):
        for choices in ({}, "invalid", ["invalid"], [None],
                        [{"message": []}], [{"message": "invalid"}],
                        [{"message": {"content": ["invalid"]}}],
                        [{"message": {"content": 123}}]):
            with self.subTest(choices=choices):
                response = self._response()
                response["choices"] = choices
                self._assert_unknown_rejection(response)

    def test_reported_valid_usage_keeps_existing_price_and_meta(self):
        prepared = self._prepared()
        meta = {}
        self.assertEqual(llm.finish_chat(self.cfg, self.conn, prepared,
                                        data=self._response(), meta=meta), "Synthetic answer")
        row = self._row(prepared["reserved"])
        self.assertEqual((row["kind"], row["tokens_in"], row["tokens_out"]), ("chat", 100, 10))
        self.assertAlmostEqual(row["cost_usd"], llm.chat_cost(
            "deepseek-4-flash", 100, 10, llm.pricing_table(self.cfg)))
        self.assertEqual(meta, {"finish_reason": "stop"})
        self.assertEqual(self._row(self.history_id), self.history)

    def test_missing_null_and_zero_counts_keep_conservative_existing_behavior(self):
        for usage in (None, {}, {"prompt_tokens": None, "completion_tokens": 10},
                      {"prompt_tokens": 100, "completion_tokens": None},
                      {"prompt_tokens": 0, "completion_tokens": 10},
                      {"prompt_tokens": 100, "completion_tokens": 0}):
            with self.subTest(usage=usage):
                prepared = self._prepared()
                before = self._row(prepared["reserved"])
                response = self._response()
                response["usage"] = usage
                self.assertEqual(llm.finish_chat(self.cfg, self.conn, prepared,
                                                data=response), "Synthetic answer")
                row = self._row(prepared["reserved"])
                self.assertEqual(row["kind"], "unknown_chat")
                self.assertGreaterEqual(row["cost_usd"], before["cost_usd"])

    def test_empty_choices_still_meter_valid_billed_usage_before_error(self):
        prepared = self._prepared()
        response = self._response()
        response["choices"] = []
        with self.assertRaises(llm.LLMError):
            llm.finish_chat(self.cfg, self.conn, prepared, data=response)
        row = self._row(prepared["reserved"])
        self.assertEqual((row["kind"], row["tokens_in"], row["tokens_out"]), ("chat", 100, 10))
        self.assertGreater(row["cost_usd"], 0)

    def test_unknown_malformed_outcome_is_not_retried_by_async_policy(self):
        response = self._response()
        response["usage"]["prompt_tokens"] = -1
        with mock.patch.object(llm, "transport_chat", return_value=response) as transport:
            with self.assertRaises(llm.LLMError):
                llm.chat_profile(self.cfg, self.conn, "task", self.messages,
                                 profile="task_synthesis", retry_unknown=False)
        self.assertEqual(transport.call_count, 1)
        rows = self.conn.execute("SELECT kind FROM llm_usage WHERE id != ?",
                                 (self.history_id,)).fetchall()
        self.assertEqual([row["kind"] for row in rows], ["unknown_chat"])
        self.assertEqual(self._row(self.history_id), self.history)

    def test_input_response_is_not_mutated(self):
        prepared = self._prepared()
        response = self._response()
        before = copy.deepcopy(response)
        llm.finish_chat(self.cfg, self.conn, prepared, data=response)
        self.assertEqual(response, before)


if __name__ == "__main__":
    unittest.main()
