"""Pure, offline regressions for measured cache use and frozen tariff estimates."""
import json
import unittest

import cache_usage


DO = "https://inference.do-ai.run/v1"
PRO = "deepseek-v4-pro"
HAIKU = "anthropic-claude-5.5-haiku"
SONNET = "anthropic-claude-sonnet-5.5"
SOL = "openai-gpt-6-1-sol"


class CacheUsageTests(unittest.TestCase):
    def capture(self, usage, model=PRO, **kwargs):
        return cache_usage.capture(model, usage, provider_url=DO, **kwargs)

    def assert_money_unknown(self, result):
        for key in ("provider_cost_usd", "uncached_cost_usd", "savings_usd"):
            self.assertIsNone(result[key], (key, result))

    def test_hosted_inclusive_read_aliases_are_not_added(self):
        result = self.capture({"prompt_tokens": 1000, "completion_tokens": 100,
            "cache_read_input_tokens": 800,
            "prompt_tokens_details": {"cached_tokens": 800}})
        self.assertEqual(result["version"], 1)
        self.assertEqual(result["status"], "known")
        self.assertEqual((result["input_tokens"], result["output_tokens"],
                          result["cached_read_tokens"]), (1000, 100, 800))
        self.assertIsNone(result["cached_write_tokens"])
        self.assertAlmostEqual(result["provider_cost_usd"], .0009744)
        self.assertAlmostEqual(result["uncached_cost_usd"], .002088)
        self.assertAlmostEqual(result["savings_usd"], .0011136)
        self.assertEqual(result["tariff_date"], "2026-10-29")
        self.assertEqual(result["tariff_source"],
            "https://docs.digitalocean.com/products/inference/details/pricing/")

    def test_either_read_alias_and_explicit_zero_are_measured(self):
        for details in ({"cache_read_input_tokens": 0},
                        {"prompt_tokens_details": {"cached_tokens": 0}}):
            with self.subTest(details=details):
                result = self.capture({"prompt_tokens": 0,
                    "completion_tokens": 0, **details})
                self.assertEqual(result["status"], "known")
                self.assertEqual(result["cached_read_tokens"], 0)
                self.assertEqual(result["provider_cost_usd"], 0)
                self.assertEqual(result["savings_usd"], 0)

    def test_missing_usage_and_cache_counter_are_not_zero_hits(self):
        for usage in (None, {}, {"prompt_tokens": 100, "completion_tokens": 20}):
            with self.subTest(usage=usage):
                result = self.capture(usage)
                self.assertEqual(result["status"], "missing")
                self.assertIsNone(result["cached_read_tokens"])
                self.assert_money_unknown(result)

    def test_missing_output_cannot_produce_cost(self):
        result = self.capture({"prompt_tokens": 100, "cache_read_input_tokens": 40})
        self.assertEqual(result["status"], "missing")
        self.assert_money_unknown(result)

    def test_bad_counters_are_invalid_without_exception(self):
        for value in (True, False, -1, 1.2, "20", None, float("nan"),
                      float("inf"), 10 ** 100, [], {}):
            for key in ("prompt_tokens", "completion_tokens", "cache_read_input_tokens"):
                with self.subTest(value=value, key=key):
                    usage = {"prompt_tokens": 100, "completion_tokens": 20,
                             "cache_read_input_tokens": 40, key: value}
                    result = self.capture(usage)
                    self.assertEqual(result["status"], "invalid")
                    self.assert_money_unknown(result)

    def test_malformed_usage_or_nested_details_are_invalid(self):
        for usage in ([], "raw-private-content", 8,
                      {"prompt_tokens": 100, "completion_tokens": 20,
                       "cache_read_input_tokens": 40, "prompt_tokens_details": []},
                      {"prompt_tokens": 100, "completion_tokens": 20,
                       "cache_read_input_tokens": 40, "cache_creation": []}):
            with self.subTest(usage=usage):
                result = self.capture(usage)
                self.assertEqual(result["status"], "invalid")
                self.assert_money_unknown(result)

    def test_conflicting_aliases_and_impossible_inclusive_counts_are_invalid(self):
        for extra in (
            {"prompt_tokens_details": {"cached_tokens": 41}},
            {"cache_read_input_tokens": 101},
            {"cache_created_input_tokens": 61},
            {"cache_created_input_tokens": 20, "cache_creation": {
                "ephemeral_5m_input_tokens": 15, "ephemeral_1h_input_tokens": 10}},
        ):
            with self.subTest(extra=extra):
                result = self.capture({"prompt_tokens": 100, "completion_tokens": 20,
                    "cache_read_input_tokens": 40, **extra})
                self.assertEqual(result["status"], "invalid")
                self.assert_money_unknown(result)

    def test_hosted_cache_writes_are_included_in_normal_input_price(self):
        result = self.capture({"prompt_tokens": 1000, "completion_tokens": 100,
            "cache_read_input_tokens": 400, "cache_created_input_tokens": 600})
        self.assertEqual(result["status"], "known")
        self.assertEqual(result["cached_write_tokens"], 600)
        self.assertAlmostEqual(result["provider_cost_usd"], .0015312)

    def test_anthropic_prompt_counter_excludes_cache_reads_and_writes(self):
        result = self.capture({"prompt_tokens": 100, "completion_tokens": 20,
            "cache_read_input_tokens": 400, "cache_created_input_tokens": 200,
            "cache_creation": {"ephemeral_5m_input_tokens": 150,
                               "ephemeral_1h_input_tokens": 50}}, SONNET)
        self.assertEqual(result["status"], "known")
        self.assertEqual(result["input_tokens"], 700)
        self.assertEqual(result["cached_write_5m_tokens"], 150)
        self.assertEqual(result["cached_write_1h_tokens"], 50)
        self.assertAlmostEqual(result["provider_cost_usd"], .001055)
        self.assertAlmostEqual(result["uncached_cost_usd"], .0016)
        self.assertAlmostEqual(result["savings_usd"], .000545)

    def test_anthropic_write_premium_can_make_net_savings_negative(self):
        result = self.capture({"prompt_tokens": 0, "completion_tokens": 0,
            "cache_read_input_tokens": 0, "cache_created_input_tokens": 1000,
            "cache_creation": {"ephemeral_5m_input_tokens": 0,
                               "ephemeral_1h_input_tokens": 1000}}, SONNET)
        self.assertEqual(result["status"], "known")
        self.assertAlmostEqual(result["provider_cost_usd"], .004)
        self.assertAlmostEqual(result["uncached_cost_usd"], .002)
        self.assertAlmostEqual(result["savings_usd"], -.002)

    def test_anthropic_unknown_write_ttl_preserves_counts_but_not_price(self):
        result = self.capture({"prompt_tokens": 100, "completion_tokens": 20,
            "cache_read_input_tokens": 400, "cache_created_input_tokens": 200}, SONNET)
        self.assertEqual(result["status"], "known")
        self.assertEqual(result["input_tokens"], 700)
        self.assertEqual(result["cached_write_tokens"], 200)
        self.assertEqual(result["reason"], "cache_write_ttl_unknown")
        self.assert_money_unknown(result)

    def test_anthropic_missing_charged_write_count_is_unknown(self):
        result = self.capture({"prompt_tokens": 100, "completion_tokens": 20,
            "cache_read_input_tokens": 400}, SONNET)
        self.assertEqual(result["status"], "missing")
        self.assertIsNone(result["input_tokens"])
        self.assert_money_unknown(result)

    def test_creation_total_can_be_derived_from_complete_ttl_breakdown(self):
        result = self.capture({"prompt_tokens": 100, "completion_tokens": 20,
            "cache_read_input_tokens": 400, "cache_creation": {
                "ephemeral_5m_input_tokens": 150, "ephemeral_1h_input_tokens": 50}}, SONNET)
        self.assertEqual(result["status"], "known")
        self.assertEqual(result["input_tokens"], 700)
        self.assertEqual(result["cached_write_tokens"], 200)
        self.assertAlmostEqual(result["provider_cost_usd"], .001055)

    def test_haiku_price_tier_uses_total_normalized_input(self):
        for prompt, expected_cost in ((0, .001), (1, .0050005)):
            with self.subTest(prompt=prompt):
                result = self.capture({"prompt_tokens": prompt, "completion_tokens": 0,
                    "cache_read_input_tokens": 100000, "cache_created_input_tokens": 0}, HAIKU)
                self.assertEqual(result["status"], "known")
                self.assertEqual(result["input_tokens"], 100000 + prompt)
                self.assertAlmostEqual(result["provider_cost_usd"], expected_cost)

    def test_sol_input_includes_cache_and_uses_context_tier(self):
        for total, expected in ((272000, .318), (272001, .631004)):
            with self.subTest(total=total):
                result = self.capture({"prompt_tokens": total, "completion_tokens": 1000,
                    "cache_read_input_tokens": 140000, "cache_created_input_tokens": 60000}, SOL)
                self.assertEqual(result["status"], "known")
                self.assertEqual(result["input_tokens"], total)
                self.assertAlmostEqual(result["provider_cost_usd"], expected)

    def test_sol_missing_write_count_has_no_cost_estimate(self):
        result = self.capture({"prompt_tokens": 1200, "completion_tokens": 20,
                              "cache_read_input_tokens": 1000}, SOL)
        self.assertEqual(result["status"], "missing")
        self.assert_money_unknown(result)

    def test_active_hosted_model_rates(self):
        cases = {
            "deepseek-4-flash": (.14, .28, .028),
            "deepseek-3.2": (.5, 1.6, .15),
            "kimi-k2.6": (.95, 4, .19),
            "llama-4-maverick": (.25, .87, .05),
            "nemotron-nano-12b-v2-vl": (.2, .6, .04),
            "kimi-k3": (3, 15, .3),
        }
        for model, (uncached, output, read) in cases.items():
            with self.subTest(model=model):
                result = self.capture({"prompt_tokens": 1000000,
                    "completion_tokens": 100000, "cache_read_input_tokens": 500000}, model)
                self.assertEqual(result["status"], "known")
                self.assertAlmostEqual(result["provider_cost_usd"],
                                       uncached * .5 + output * .1 + read * .5)

    def test_gpt_oss_unknown_cached_rate_does_not_invent_discount(self):
        for model, expected_cost in (("openai-gpt-oss-20b", .095),
                                     ("openai-gpt-oss-120b", .17)):
            with self.subTest(model=model):
                usage = {"prompt_tokens": 1000000, "completion_tokens": 100000,
                         "cache_read_input_tokens": 0}
                no_cache = self.capture(usage, model)
                self.assertEqual(no_cache["status"], "known")
                self.assertAlmostEqual(no_cache["provider_cost_usd"], expected_cost)
                with_cache = self.capture({**usage, "cache_read_input_tokens": 400000}, model)
                self.assertEqual(with_cache["status"], "known")
                self.assertEqual(with_cache["cached_read_tokens"], 400000)
                self.assertEqual(with_cache["reason"], "cache_read_rate_unavailable")
                self.assert_money_unknown(with_cache)

    def test_unknown_models_mismatched_identity_and_custom_endpoints_are_unsupported(self):
        usage = {"prompt_tokens": 100, "completion_tokens": 20,
                 "cache_read_input_tokens": 40}
        results = [self.capture(usage, "new-model"),
                   self.capture(usage, response_model="different-model")]
        for url in ("https://example.com/v1", "http://inference.do-ai.run/v1",
                    "https://inference.do-ai.run.evil.example/v1",
                    "https://user:secret@inference.do-ai.run/v1",
                    "https://inference.do-ai.run/v1?key=secret",
                    "https://inference.do-ai.run:8443/v1", None):
            results.append(cache_usage.capture(PRO, usage, provider_url=url))
        for result in results:
            self.assertEqual(result["status"], "unsupported")
            self.assert_money_unknown(result)

    def test_exact_returned_model_and_canonical_url_variants_are_supported(self):
        for url in (DO, DO + "/", "https://INFERENCE.DO-AI.RUN:443/v1"):
            result = cache_usage.capture(PRO, {"prompt_tokens": 100,
                "completion_tokens": 20, "cache_read_input_tokens": 40},
                provider_url=url, response_model=PRO)
            self.assertEqual(result["status"], "known")

    def test_metadata_never_copies_unrecognized_provider_content(self):
        result = self.capture({"prompt_tokens": 100, "completion_tokens": 20,
            "cache_read_input_tokens": 40, "private_body": "secret-prompt",
            "prompt_tokens_details": {"cached_tokens": 40, "raw": "private-note"}})
        encoded = json.dumps(result, allow_nan=False)
        self.assertNotIn("secret-prompt", encoded)
        self.assertNotIn("private-note", encoded)
        self.assertEqual(result["status"], "known")


if __name__ == "__main__":
    unittest.main()
