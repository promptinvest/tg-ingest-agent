"""Offline regressions for the November 2026 DigitalOcean model retirement."""
import json
import unittest

import cache_usage
import llm
from testlib import make_config


FLASH = "deepseek-v4.1-flash-1017"
VISION = "deepseek-v4.1-flash"
OSS = "openai-gpt-oss-120b"
DO = "https://inference.do-ai.run/v1"


class ModelRetirementTests(unittest.TestCase):
    def test_fresh_configuration_uses_supported_default_without_enabling_vision(self):
        cfg = make_config()
        self.assertEqual(cfg.do_model, FLASH)
        self.assertEqual(cfg.router_model, FLASH)
        self.assertEqual(cfg.vision_model, "")
        self.assertEqual(cfg.embedding_model, "BGE-M3")
        self.assertFalse(cfg.mentor_enabled)

    def test_default_fallbacks_preserve_profile_limits_and_pro_primary(self):
        cfg = make_config()
        profiles = llm.profiles(cfg)
        self.assertEqual(profiles["router_fast"], {
            "primary": FLASH, "fallbacks": ["deepseek-v4-pro", OSS],
            "max_tokens": 400, "json_required": True, "timeout": 30,
        })
        self.assertEqual(profiles["converse_warm"], {
            "primary": FLASH, "fallbacks": [OSS], "max_tokens": 320,
            "json_required": False, "temperature": .7, "timeout": 60,
        })
        for name, cap in (("task_planner", 1600), ("task_synthesis", 1200),
                          ("improvement_evaluator", 1000)):
            with self.subTest(profile=name):
                self.assertEqual(profiles[name], {
                    "primary": "deepseek-v4-pro", "fallbacks": [FLASH],
                    "max_tokens": cap, "json_required": True, "timeout": 90,
                })
        retired = {"deepseek-4-flash", "openai-gpt-oss-20b", "llama-4-maverick"}
        self.assertFalse(retired.intersection(llm.configured_models(cfg)))
        self.assertEqual(llm.unpriced_models(cfg), [])

    def test_explicit_current_primary_profiles_and_controls_remain_exact(self):
        primary_models = {
            "router_fast": "anthropic-claude-5.5-haiku",
            "ingest_balanced": "anthropic-claude-5.5-haiku",
            "memory_curator": "anthropic-claude-5.5-haiku",
            "ask_grounded": "anthropic-claude-sonnet-5.5",
            "memory_consolidate": "anthropic-claude-sonnet-5.5",
            "task_planner": "openai-gpt-6-1-sol",
            "task_synthesis": "openai-gpt-6-1-sol",
            "improvement_evaluator": "openai-gpt-6-1-sol",
            "converse_warm": "deepseek-v4-pro",
        }
        overrides = {name: {"primary": model} for name, model in primary_models.items()}
        overrides["converse_warm"].update(
            fallbacks=["kimi-k2.6", FLASH, OSS], max_tokens=320,
            json_required=False, temperature=.7, timeout=60)
        cfg = make_config(
            DO_CHAT_MODEL="anthropic-claude-5.5-haiku",
            ROUTER_MODEL="anthropic-claude-5.5-haiku", VISION_MODEL=VISION,
            LLM_PROFILES_JSON=json.dumps(overrides),
            PRICING_JSON=json.dumps({
                "anthropic-claude-5.5-haiku": [.5, 2.5],
                "anthropic-claude-sonnet-5.5": [2, 10],
                "openai-gpt-6-1-sol": [4, 15],
            }))
        profiles = llm.profiles(cfg)
        self.assertEqual({name: prof["primary"] for name, prof in profiles.items()},
                         primary_models)
        self.assertEqual(profiles["converse_warm"], overrides["converse_warm"])
        self.assertEqual(cfg.vision_model, VISION)
        self.assertEqual(llm.unpriced_models(cfg), [])

    def test_replacement_prices_preserve_legacy_rates_and_explicit_overrides(self):
        table = llm.pricing_table(make_config())
        for model in (FLASH, VISION):
            with self.subTest(model=model):
                self.assertEqual(table.get(model), (.30, 1.20))
                self.assertAlmostEqual(llm.chat_cost(model, 1000, 100, table), .00042)
        self.assertEqual(table[OSS], (.10, .70))
        self.assertEqual(table["deepseek-4-flash"], (.14, .28))
        self.assertEqual(table["openai-gpt-oss-20b"], (.05, .45))
        self.assertEqual(table["llama-4-maverick"], (.25, .87))
        cfg = make_config(PRICING_JSON=json.dumps({FLASH: [7, 9]}))
        self.assertEqual(llm.pricing_table(cfg)[FLASH], (7, 9))

    def test_both_flash_ids_price_measured_cache_reads_at_new_tariff(self):
        for model in (FLASH, VISION):
            with self.subTest(model=model):
                result = cache_usage.capture(model, {
                    "prompt_tokens": 1000, "completion_tokens": 100,
                    "prompt_tokens_details": {"cached_tokens": 800},
                }, provider_url=DO)
                self.assertEqual(result["status"], "known")
                self.assertEqual(result["model"], model)
                self.assertEqual(result["tariff_date"], "2026-10-29")
                self.assertAlmostEqual(result["provider_cost_usd"], .0001848)
                self.assertAlmostEqual(result["uncached_cost_usd"], .00042)
                self.assertAlmostEqual(result["savings_usd"], .0002352)

    def test_oss_cache_discount_remains_unknown_without_published_rate(self):
        result = cache_usage.capture(OSS, {
            "prompt_tokens": 1000, "completion_tokens": 100,
            "prompt_tokens_details": {"cached_tokens": 800},
        }, provider_url=DO)
        self.assertIsNone(result["provider_cost_usd"])
        self.assertIsNone(result["savings_usd"])
        uncached = cache_usage.capture(OSS, {
            "prompt_tokens": 1000, "completion_tokens": 100,
            "prompt_tokens_details": {"cached_tokens": 0},
        }, provider_url=DO)
        self.assertAlmostEqual(uncached["provider_cost_usd"], .00017)


if __name__ == "__main__":
    unittest.main()
