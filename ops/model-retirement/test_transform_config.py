"""Pure retirement-plan regressions; no model calls or deployment effects."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


MODULE = Path(__file__).with_name("transform_config.py")
spec = importlib.util.spec_from_file_location("transform_config", MODULE)
transform = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transform)


class TransformConfigTests(unittest.TestCase):
    def fixture(self):
        return {
            "DO_CHAT_MODEL": "deepseek-v4-pro",
            "ROUTER_MODEL": "anthropic-claude-5.5-haiku",
            "VISION_MODEL": "llama-4-maverick",
            "MODEL_ACCESS_KEY": "synthetic-secret-do-not-print",
            "PROMPT": "Use deepseek-4-flash or openai-gpt-oss-20b literally",
            "LLM_PROFILES_JSON": json.dumps({
                "converse_warm": {
                    "primary": "deepseek-v4-pro",
                    "fallbacks": ["kimi-k2.6", "deepseek-4-flash", "openai-gpt-oss-20b"],
                    "prompt": "deepseek-4-flash",
                    "temperature": 0.7, "timeout": 60, "max_tokens": 320,
                    "json_required": False,
                    "metadata": {"primary": "deepseek-4-flash"},
                },
                "routing": {"primary": "anthropic-claude-5.5-haiku", "fallbacks": []},
            }, ensure_ascii=False, indent=2),
            "PRICING_JSON": json.dumps({
                "anthropic-claude-5.5-haiku": [0.5, 2.5],
                "deepseek-4-flash": [0.14, 0.28],
            }),
        }

    def test_preserves_primaries_controls_and_every_unrelated_leaf(self):
        source = self.fixture()
        frozen = copy.deepcopy(source)
        actual, plan = transform.transform_environment(source)
        self.assertEqual(source, frozen)
        expected = copy.deepcopy(source)
        expected["VISION_MODEL"] = "deepseek-v4.1-flash"
        expected_profiles = json.loads(source["LLM_PROFILES_JSON"])
        expected_profiles["converse_warm"]["fallbacks"] = [
            "kimi-k2.6", "deepseek-v4.1-flash-1017", "openai-gpt-oss-120b"]
        expected_prices = json.loads(source["PRICING_JSON"])
        expected_prices.update({
            "deepseek-v4.1-flash": [0.30, 1.20],
            "deepseek-v4.1-flash-1017": [0.30, 1.20],
            "openai-gpt-oss-120b": [0.10, 0.70],
        })
        self.assertEqual(json.loads(actual.pop("LLM_PROFILES_JSON")), expected_profiles)
        self.assertEqual(json.loads(actual.pop("PRICING_JSON")), expected_prices)
        expected.pop("LLM_PROFILES_JSON")
        expected.pop("PRICING_JSON")
        self.assertEqual(actual, expected)
        self.assertEqual({change["path"] for change in plan["changes"]}, {
            "/VISION_MODEL",
            "/LLM_PROFILES_JSON/converse_warm/fallbacks/1",
            "/LLM_PROFILES_JSON/converse_warm/fallbacks/2",
            "/PRICING_JSON/deepseek-v4.1-flash",
            "/PRICING_JSON/deepseek-v4.1-flash-1017",
            "/PRICING_JSON/openai-gpt-oss-120b",
        })

    def test_exact_model_matches_only(self):
        source = self.fixture()
        source.update(DO_CHAT_MODEL="deepseek-4-flash", ROUTER_MODEL="openai-gpt-oss-20b")
        profiles = {"p": {"primary": "deepseek-4-flash", "fallbacks": [
            "openai-gpt-oss-20b", " deepseek-4-flash", "deepseek-4-flash:custom",
            "llama-4-maverick", "gemma-4-31B-it", "qwen3.5-397b-a17b"]}}
        source["LLM_PROFILES_JSON"] = json.dumps(profiles)
        actual, _ = transform.transform_environment(source)
        self.assertEqual(actual["DO_CHAT_MODEL"], "deepseek-v4.1-flash-1017")
        self.assertEqual(actual["ROUTER_MODEL"], "openai-gpt-oss-120b")
        profiles["p"]["primary"] = "deepseek-v4.1-flash-1017"
        profiles["p"]["fallbacks"][0] = "openai-gpt-oss-120b"
        self.assertEqual(json.loads(actual["LLM_PROFILES_JSON"]), profiles)

    def test_idempotent_and_retains_untouched_serialization(self):
        first, _ = transform.transform_environment(self.fixture())
        first["LLM_PROFILES_JSON"] = json.dumps(json.loads(first["LLM_PROFILES_JSON"]), indent=4)
        first["PRICING_JSON"] = json.dumps(json.loads(first["PRICING_JSON"]), indent=4)
        second, plan = transform.transform_environment(first)
        self.assertEqual(second, first)
        self.assertEqual(plan["changes"], [])
        self.assertEqual(plan["before_sha256"], plan["after_sha256"])

    def test_role_selected_oss_preserves_all_current_primaries_and_controls(self):
        source = self.fixture()
        profiles = json.loads(source["LLM_PROFILES_JSON"])
        profiles.update({
            "ingest_balanced": {"primary": "anthropic-claude-5.5-haiku",
                "fallbacks": ["openai-gpt-oss-20b", "deepseek-4-flash"],
                "max_tokens": 700, "temperature": .2, "timeout": 60,
                "json_required": True, "metadata": {"model": "deepseek-4-flash"}},
            "ask_grounded": {"primary": "anthropic-claude-sonnet-5.5",
                "fallbacks": ["kimi-k2.6", "deepseek-4-flash"],
                "max_tokens": 500, "timeout": 60, "json_required": False},
        })
        source["LLM_PROFILES_JSON"] = json.dumps(profiles)
        frozen = copy.deepcopy(source)
        actual, plan = transform.transform_environment(source)
        after = json.loads(actual["LLM_PROFILES_JSON"])
        self.assertEqual(source, frozen)
        self.assertEqual(after["ingest_balanced"], {**profiles["ingest_balanced"],
                         "fallbacks": ["openai-gpt-oss-120b"]})
        self.assertEqual(after["ask_grounded"], {**profiles["ask_grounded"],
                         "fallbacks": ["kimi-k2.6", "openai-gpt-oss-120b"]})
        self.assertEqual(after["converse_warm"], {**profiles["converse_warm"],
                         "fallbacks": ["kimi-k2.6", "deepseek-v4.1-flash-1017", "openai-gpt-oss-120b"]})
        self.assertEqual(after["routing"], profiles["routing"])
        self.assertEqual([c for c in plan["changes"] if c["operation"] == "remove"],
            [{"operation": "remove", "path": "/LLM_PROFILES_JSON/ingest_balanced/fallbacks/1"}])
        self.assertEqual(actual["DO_CHAT_MODEL"], source["DO_CHAT_MODEL"])
        self.assertEqual(actual["ROUTER_MODEL"], source["ROUTER_MODEL"])

    def test_only_exact_selected_role_fallbacks_use_oss_override(self):
        roles = ["ingest_balanced", "ask_grounded", "ingest", "ask", "INGEST_BALANCED",
                 "ask_grounded/custom", "unknown", "task_planner"]
        profiles = {role: {"primary": "deepseek-4-flash", "fallbacks": ["deepseek-4-flash"],
                           "metadata": {"fallbacks": ["deepseek-4-flash"]}} for role in roles}
        actual, _ = transform.transform_environment({"LLM_PROFILES_JSON": json.dumps(profiles)})
        for role, profile in json.loads(actual["LLM_PROFILES_JSON"]).items():
            with self.subTest(role=role):
                self.assertEqual(profile["primary"], "deepseek-v4.1-flash-1017")
                expected = "openai-gpt-oss-120b" if role in ("ingest_balanced", "ask_grounded") else "deepseek-v4.1-flash-1017"
                self.assertEqual(profile["fallbacks"], [expected])
                self.assertEqual(profile["metadata"], profiles[role]["metadata"])

    def test_only_introduced_collisions_coalesce_and_existing_order_and_duplicates_survive(self):
        oss, retired, flash = "openai-gpt-oss-120b", "openai-gpt-oss-20b", "deepseek-4-flash"
        cases = [([retired, flash], [oss]),
                 ([flash, "unknown-model", oss], ["unknown-model", oss]),
                 ([oss, "unknown-model", flash], [oss, "unknown-model"]),
                 ([retired, retired, flash], [oss, oss]),
                 ([flash, flash], [oss, oss]),
                 ([oss, oss, flash], [oss, oss]),
                 (["unknown-model", "unknown-model", flash], ["unknown-model", "unknown-model", oss])]
        for role in ("ingest_balanced", "ask_grounded"):
            for before, expected in cases:
                with self.subTest(role=role, before=before):
                    actual, _ = transform.transform_environment({"LLM_PROFILES_JSON":
                        json.dumps({role: {"primary": "keep", "fallbacks": before}})})
                    self.assertEqual(json.loads(actual["LLM_PROFILES_JSON"])[role]["fallbacks"], expected)
        actual, _ = transform.transform_environment({"LLM_PROFILES_JSON": json.dumps({
            "unknown-role": {"fallbacks": [oss, retired, flash]}})})
        self.assertEqual(json.loads(actual["LLM_PROFILES_JSON"])["unknown-role"]["fallbacks"],
                         [oss, oss, "deepseek-v4.1-flash-1017"])

    def test_role_transform_is_idempotent_and_keeps_unretired_flash_overrides(self):
        source = {"LLM_PROFILES_JSON": json.dumps({"ingest_balanced": {
            "primary": "keep", "fallbacks": ["openai-gpt-oss-20b", "deepseek-4-flash", "deepseek-v4.1-flash-1017"]}})}
        first, _ = transform.transform_environment(source)
        profiles = json.loads(first["LLM_PROFILES_JSON"])
        self.assertEqual(profiles["ingest_balanced"]["fallbacks"],
                         ["openai-gpt-oss-120b", "deepseek-v4.1-flash-1017"])
        first["LLM_PROFILES_JSON"] = json.dumps(profiles, indent=4)
        second, plan = transform.transform_environment(first)
        self.assertEqual(second, first)
        self.assertEqual(plan["changes"], [])

    def test_role_transform_preserves_native_environment_comments_key_order_and_other_bytes(self):
        import env_bytes
        profiles = json.dumps({"ingest_balanced": {"primary": "keep",
            "fallbacks": ["openai-gpt-oss-20b", "deepseek-4-flash"]}})
        raw = ("# deepseek-4-flash is retained comment text\r\nSECRET='synthetic-value'\r\n"
               "  export LLM_PROFILES_JSON = '" + profiles + "'\r\n"
               "# between assignments\r\nBUDGET_DAILY_USD=2\r\n"
               "PRICING_JSON='" + json.dumps(transform.SELECTED_PRICES) + "'\r\n").encode()
        original = env_bytes.parse_env_bytes(raw)
        candidate, _ = transform.transform_environment(original)
        updates = {k: v for k, v in candidate.items() if original.get(k) != v}
        self.assertEqual(set(updates), {"LLM_PROFILES_JSON"})
        rendered = env_bytes.update_env_bytes(raw, updates)
        self.assertEqual(env_bytes.parse_env_bytes(rendered), candidate)
        before, after = raw.splitlines(keepends=True), rendered.splitlines(keepends=True)
        self.assertEqual(len(before), len(after))
        for index in (0, 1, 3, 4, 5):
            self.assertEqual(before[index], after[index])
        self.assertTrue(after[2].startswith(b"  export LLM_PROFILES_JSON = '"))
        self.assertTrue(after[2].endswith(b"'\r\n"))

    def test_missing_fields_adds_only_prices(self):
        actual, plan = transform.transform_environment({"OTHER": "untouched"})
        self.assertEqual(set(actual), {"OTHER", "PRICING_JSON"})
        self.assertEqual(len(json.loads(actual["PRICING_JSON"])), 3)
        self.assertEqual(len(plan["changes"]), 3)

    def test_memory_curator_selects_flash_without_changing_consolidation_or_controls(self):
        original = {"primary": "anthropic-claude-5.5-haiku",
            "fallbacks": ["openai-gpt-oss-20b", "deepseek-4-flash"],
            "max_tokens": 700, "temperature": 0, "timeout": 60, "json_required": True}
        profiles = {name: copy.deepcopy(original) for name in
            ("memory_curator", "memory_consolidate", "memory", "MEMORY_CURATOR", "memory_curator/custom")}
        source = {"LLM_PROFILES_JSON": json.dumps(profiles), "KEEP": "unchanged"}
        frozen = copy.deepcopy(source)
        actual, plan = transform.transform_environment(source)
        after = json.loads(actual["LLM_PROFILES_JSON"])
        self.assertEqual(source, frozen)
        for name in profiles:
            expected = ["deepseek-v4.1-flash-1017"] if name == "memory_curator" else ["openai-gpt-oss-120b", "deepseek-v4.1-flash-1017"]
            self.assertEqual(after[name], {**profiles[name], "fallbacks": expected})
        self.assertEqual(actual["KEEP"], "unchanged")
        self.assertIn({"operation": "remove", "path": "/LLM_PROFILES_JSON/memory_curator/fallbacks/1"}, plan["changes"])

    def test_memory_override_only_coalesces_new_collisions_and_preserves_existing_targets(self):
        old, old_flash, flash = "openai-gpt-oss-20b", "deepseek-4-flash", "deepseek-v4.1-flash-1017"
        for before, expected in (([old, old_flash], [flash]), ([old_flash, old], [flash]),
                ([old, "other", flash], ["other", flash]), ([old, old, old_flash], [flash, flash]),
                ([flash, flash, old], [flash, flash]), (["openai-gpt-oss-120b", old], ["openai-gpt-oss-120b", flash])):
            with self.subTest(before=before):
                actual, _ = transform.transform_environment({"LLM_PROFILES_JSON": json.dumps({
                    "memory_curator": {"primary": "keep", "fallbacks": before}})})
                self.assertEqual(json.loads(actual["LLM_PROFILES_JSON"])["memory_curator"]["fallbacks"], expected)
                again, plan = transform.transform_environment(actual)
                self.assertEqual(again, actual)
                self.assertEqual(plan["changes"], [])

    def test_existing_equal_override_is_kept_byte_exact(self):
        source = {"PRICING_JSON": '{"deepseek-v4.1-flash":["0.30", "1.20"], '
                  '"deepseek-v4.1-flash-1017":[0.3, 1.2], "openai-gpt-oss-120b":[0.1,0.7]}'}
        actual, plan = transform.transform_environment(source)
        self.assertEqual(actual, source)
        self.assertEqual(plan["changes"], [])

    def test_conflicting_selected_price_rejects_without_mutation_or_disclosure(self):
        source = self.fixture()
        source["PRICING_JSON"] = '{"deepseek-v4.1-flash":[99, 99]}'
        frozen = copy.deepcopy(source)
        with self.assertRaises(transform.ConfigError) as caught:
            transform.transform_environment(source)
        self.assertEqual(source, frozen)
        self.assertNotIn("99", str(caught.exception))

    def test_strict_json_and_invalid_shapes_fail_closed(self):
        cases = [
            {"LLM_PROFILES_JSON": '{"p":{},"p":{}}'},
            {"LLM_PROFILES_JSON": '{"p":{"primary":"a","primary":"b"}}'},
            {"LLM_PROFILES_JSON": '{"p":{"fallbacks":"deepseek-4-flash"}}'},
            {"LLM_PROFILES_JSON": '{"p":{"fallbacks":[1]}}'},
            {"LLM_PROFILES_JSON": '{"p":{"primary":false}}'},
            {"LLM_PROFILES_JSON": '{"p":[]}'},
            {"LLM_PROFILES_JSON": '[]'},
            {"LLM_PROFILES_JSON": '{"p":{"timeout":NaN}}'},
            {"PRICING_JSON": '{"m":[0.1,0.2],"m":[0.3,0.4]}'},
            {"PRICING_JSON": '{"m":[true,0.2]}'},
            {"PRICING_JSON": '{"m":[-1,0.2]}'},
            {"PRICING_JSON": '{"m":["Infinity",0.2]}'},
            {"PRICING_JSON": '{"m":[0.1]}'},
            {"PRICING_JSON": 'null'},
            {"PRICING_JSON": '{private credential invalid'},
            {"DO_CHAT_MODEL": None},
        ]
        for case in cases:
            with self.subTest(case=list(case)):
                with self.assertRaises(transform.ConfigError):
                    transform.transform_environment(case)

    def test_json_pointer_escaping_and_deterministic_hash(self):
        source = {"LLM_PROFILES_JSON": '{"a/b~c":{"primary":"deepseek-4-flash"}}'}
        actual, plan = transform.transform_environment(source)
        self.assertIn("/LLM_PROFILES_JSON/a~1b~0c/primary", [c["path"] for c in plan["changes"]])
        canonical = json.dumps(source, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        self.assertEqual(plan["before_sha256"], hashlib.sha256(canonical.encode()).hexdigest())
        self.assertEqual(plan["after_sha256"], transform.environment_hash(actual))
        again, again_plan = transform.transform_environment(dict(reversed(list(source.items()))))
        self.assertEqual(actual, again)
        self.assertEqual(plan, again_plan)

    def test_cli_outputs_plan_only_and_writes_no_files(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, "-B", str(MODULE)],
                input=json.dumps(self.fixture()), text=True, capture_output=True,
                cwd=directory, check=False)
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertIn("after_sha256", plan)
        self.assertNotIn("synthetic-secret", result.stdout + result.stderr)
        self.assertNotIn("MODEL_ACCESS_KEY", result.stdout + result.stderr)
        self.assertNotIn("Use deepseek", result.stdout + result.stderr)
        self.assertNotIn("environment", plan)

    def test_cli_rejects_duplicate_outer_keys_without_echo(self):
        result = subprocess.run([sys.executable, "-B", str(MODULE)],
            input='{"PRIVATE_SECRET":"hidden","PRIVATE_SECRET":"alsohidden"}',
            text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("PRIVATE_SECRET", result.stderr)
        self.assertNotIn("hidden", result.stderr)


if __name__ == "__main__":
    unittest.main()
