"""Offline request and accounting contracts for the replacement vision model."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import cache_usage
import llm
import store
from testlib import make_config


LUNA = "openai-gpt-6-luna"


class LunaVisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = store.open_db(Path(self.tmp.name) / "test.db")
        self.addCleanup(self.conn.close)
        self.cfg = make_config()
        self.messages = [{"role": "user", "content": [
            {"type": "text", "text": "Describe only the visible content."},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
        ]}]

    def prepare(self, model, cap=160):
        with mock.patch.object(llm, "urlopen", side_effect=AssertionError("offline")):
            return llm.prepare_chat(self.cfg, self.conn, "vision", self.messages,
                max_tokens=cap, model=model, temperature=0, timeout=90)

    def test_luna_uses_no_reasoning_and_preserves_each_native_limit(self):
        for cap in (400, 160, 1600):
            with self.subTest(cap=cap):
                prepared = self.prepare(LUNA, cap)
                self.assertEqual(json.loads(prepared["request"].data), {
                    "model": LUNA, "messages": self.messages,
                    "max_completion_tokens": cap, "temperature": 0,
                    "stream": False, "reasoning_effort": "none"})
                self.assertEqual(prepared["timeout"], 90)
                self.assertEqual(prepared["messages"], self.messages)

    def test_existing_models_keep_their_exact_wire_bytes(self):
        for model, temperature in (("deepseek-v4.1-flash-1017", 0),
                ("openai-gpt-oss-120b", 0), ("kimi-k2.6", 1),
                ("anthropic-claude-5.5-haiku", 0), ("openai-gpt-6-1-sol", 0)):
            with self.subTest(model=model):
                prepared = self.prepare(model)
                self.assertEqual(prepared["request"].data, json.dumps({
                    "model": model, "messages": self.messages, "max_tokens": 160,
                    "temperature": temperature, "stream": False}).encode("utf-8"))

    def test_luna_guard_rate_covers_both_published_context_tiers(self):
        self.assertEqual(llm.pricing_table(self.cfg)[LUNA], (.20, .75))
        prepared = self.prepare(LUNA)
        row = self.conn.execute("SELECT * FROM llm_usage ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(row["model"], LUNA)
        self.assertTrue(row["kind"].startswith("unknown_"))
        self.assertGreater(row["cost_usd"], 0)
        self.assertIsNotNone(prepared["reserved"])

    def capture(self, total, **overrides):
        usage = {"prompt_tokens": total, "completion_tokens": 1000,
            "cache_read_input_tokens": 140000, "cache_created_input_tokens": 60000}
        usage.update(overrides)
        return cache_usage.capture(LUNA, usage,
            provider_url="https://inference.do-ai.run/v1", response_model=LUNA)

    def test_luna_measured_tariff_uses_the_exact_context_boundary(self):
        for total, expected in ((272000, .0166), (272001, .0329502)):
            with self.subTest(total=total):
                result = self.capture(total)
                self.assertEqual(result["status"], "known")
                self.assertAlmostEqual(result["provider_cost_usd"], expected, places=10)
                self.assertEqual(result["tariff_date"], "2026-10-30")

    def test_missing_luna_cache_write_counter_is_unknown(self):
        result = cache_usage.capture(LUNA, {"prompt_tokens": 1000,
            "completion_tokens": 20, "cache_read_input_tokens": 0},
            provider_url="https://inference.do-ai.run/v1")
        self.assertEqual(result["status"], "missing")
        self.assertIsNone(result["cached_write_tokens"])
        self.assertIsNone(result["provider_cost_usd"])

    def test_luna_conflicting_cache_categories_fail_closed(self):
        result = self.capture(272000, cache_read_input_tokens=272001)
        self.assertEqual(result["status"], "invalid")
        self.assertIsNone(result["provider_cost_usd"])


if __name__ == "__main__":
    unittest.main()
