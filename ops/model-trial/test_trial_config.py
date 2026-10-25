"""Offline regression checks for the reversible model-only trial configuration."""
from copy import deepcopy
import hashlib
import json
import unittest
from unittest import mock

import trial_config as trial


class InstalledLLM:
    @staticmethod
    def default_profiles(cfg):
        rows = [
            ("router_fast", cfg.router_model, ["deepseek-v4-pro", "openai-gpt-oss-20b"], 400, True, 30),
            ("ingest_balanced", cfg.do_model, ["openai-gpt-oss-20b"], 600, True, 45),
            ("ask_grounded", cfg.do_model, [], 500, False, 60),
            ("converse_warm", cfg.do_model, ["openai-gpt-oss-20b"], 320, False, 60),
            ("memory_curator", cfg.do_model, ["openai-gpt-oss-20b"], 700, True, 60),
            ("memory_consolidate", cfg.do_model, ["openai-gpt-oss-20b"], 700, True, 60),
            ("task_planner", "deepseek-v4-pro", [trial.FLASH], 1600, True, 90),
            ("task_synthesis", "deepseek-v4-pro", [trial.FLASH], 1200, True, 90),
            ("improvement_evaluator", "deepseek-v4-pro", [trial.FLASH], 1000, True, 90),
        ]
        result = {name: dict(primary=primary, fallbacks=backups, max_tokens=cap,
                             json_required=structured, timeout=timeout)
                  for name, primary, backups, cap, structured, timeout in rows}
        result["converse_warm"]["temperature"] = 0.7
        return result


class TrialConfigTests(unittest.TestCase):
    def setUp(self):
        self.env = {
            "DO_CHAT_MODEL": trial.FLASH,
            "ROUTER_MODEL": trial.FLASH,
            "VISION_MODEL": "llama-4-maverick",
            "DO_EMBEDDING_MODEL": "BGE-M3",
            "BUDGET_DAILY_USD": "0.80",  # SQLite's runtime override is authoritative.
            "BUDGET_MONTHLY_USD": "12.00",
            "TELEGRAM_BOT_TOKEN": "fixture-private",
            "PRICING_JSON": '{"historical-model":[0.25,0.75]}',
            "LLM_PROFILES_JSON": json.dumps({
                "converse_warm": {"max_tokens": 450, "temperature": 0.6,
                                   "fallbacks": ["openai-gpt-oss-20b", "legacy-backup"]},
                "task_planner": {"fallbacks": [trial.FLASH, "legacy-planner"]},
            }),
        }

    def test_candidate_maps_every_role_and_preserves_all_controls_and_backups(self):
        original = deepcopy(self.env)
        before = trial.effective_profiles(self.env, InstalledLLM)
        candidate = trial.build_candidate(self.env, InstalledLLM)
        after = trial.effective_profiles(candidate, InstalledLLM)
        self.assertEqual(self.env, original)
        self.assertEqual(set(after), trial.PROFILE_NAMES)
        for name, prior in before.items():
            if name in trial.UNCHANGED_PROFILES:
                self.assertEqual(after[name], prior)
                continue
            self.assertEqual(after[name]["primary"], trial.PRIMARY_BY_PROFILE[name])
            backups = list(prior["fallbacks"])
            if trial.FLASH not in backups:
                backups.append(trial.FLASH)
            self.assertEqual(after[name]["fallbacks"], backups)
            for key in prior.keys() - {"primary", "fallbacks"}:
                self.assertEqual(after[name][key], prior[key])
        self.assertEqual(candidate["VISION_MODEL"], self.env["VISION_MODEL"])
        self.assertEqual(candidate["BUDGET_DAILY_USD"], "0.80")
        self.assertEqual(trial.validate_candidate(self.env, candidate, InstalledLLM)["profiles"], 9)

    def test_partial_trial_preserves_entire_effective_conversation_profile_and_binds_hash(self):
        before = trial.effective_profiles(self.env, InstalledLLM)
        candidate = trial.build_candidate(self.env, InstalledLLM)
        after = trial.effective_profiles(candidate, InstalledLLM)
        differences = {(name, key) for name in before for key in before[name]
                       if key not in {"primary", "fallbacks"} and before[name][key] != after[name][key]}
        self.assertEqual(differences, set())
        self.assertEqual(after["converse_warm"], before["converse_warm"])
        self.assertEqual(after["converse_warm"]["primary"], trial.FLASH)
        self.assertEqual(after["ask_grounded"]["primary"], trial.SONNET)
        self.assertEqual(after["memory_consolidate"]["primary"], trial.SONNET)
        self.assertEqual(after["converse_warm"]["max_tokens"], 450)
        validation = trial.validate_candidate(self.env, candidate, InstalledLLM)
        self.assertEqual(validation["profile_control_exceptions"], {})
        self.assertEqual(validation['unchanged_profiles'], ['converse_warm'])
        self.assertEqual(validation['changed_active_profiles'], sorted([
            'router_fast', 'ingest_balanced', 'ask_grounded', 'memory_curator',
            'memory_consolidate', 'task_planner', 'task_synthesis']))
        self.assertEqual(validation['dormant_configured_profiles'], ['improvement_evaluator'])
        expected_sha = hashlib.sha256(json.dumps(before['converse_warm'], sort_keys=True,
            ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        self.assertEqual(validation['unchanged_profile_sha256s'], {'converse_warm': expected_sha})

    def test_conversation_639_641_and_every_other_profile_control_change_are_rejected(self):
        candidate = trial.build_candidate(self.env, InstalledLLM)
        profiles = trial.strict_json(candidate["LLM_PROFILES_JSON"])
        changes = [("converse_warm", 639), ("converse_warm", 641)]
        baseline = trial.effective_profiles(self.env, InstalledLLM)
        changes += [(name, profile["max_tokens"] + 1) for name, profile in baseline.items()
                    if name != "converse_warm"]
        for name, cap in changes:
            altered = deepcopy(profiles)
            altered[name]["max_tokens"] = cap
            changed = dict(candidate, LLM_PROFILES_JSON=json.dumps(altered))
            with self.subTest(profile=name, cap=cap), self.assertRaises(ValueError):
                trial.validate_candidate(self.env, changed, InstalledLLM)

        for key, value in [('primary', trial.SOL), ('fallbacks', [trial.FLASH]),
                           ('temperature', .123), ('timeout', 17), ('json_required', True)]:
            altered = deepcopy(profiles)
            altered['converse_warm'][key] = value
            changed = dict(candidate, LLM_PROFILES_JSON=json.dumps(altered))
            with self.subTest(control=key), self.assertRaises(ValueError):
                trial.validate_candidate(self.env, changed, InstalledLLM)

    def test_partial_trial_keeps_original_320_conversation_and_reverts_original_configuration(self):
        profiles = trial.strict_json(self.env["LLM_PROFILES_JSON"])
        profiles["converse_warm"].pop("max_tokens")
        old = dict(self.env, LLM_PROFILES_JSON=json.dumps(profiles))
        self.assertEqual(trial.effective_profiles(old, InstalledLLM)["converse_warm"]["max_tokens"], 320)
        candidate = trial.build_candidate(old, InstalledLLM)
        selected = trial.effective_profiles(candidate, InstalledLLM)
        self.assertEqual(selected["converse_warm"]["max_tokens"], 320)
        self.assertEqual(selected["converse_warm"], trial.effective_profiles(old, InstalledLLM)["converse_warm"])
        self.assertEqual(candidate['DO_CHAT_MODEL'], trial.HAIKU)
        self.assertEqual(selected['converse_warm']['primary'], trial.FLASH)
        reverted = trial.revert_model_keys(candidate, old, candidate)
        self.assertEqual(reverted, old)
        restored = trial.effective_profiles(reverted, InstalledLLM)
        self.assertEqual(restored["converse_warm"]["max_tokens"], 320)
        self.assertEqual(restored, trial.effective_profiles(old, InstalledLLM))

    def test_prices_preserve_prior_overrides_and_add_conservative_trial_values(self):
        prices = trial.strict_json(trial.build_candidate(self.env, InstalledLLM)["PRICING_JSON"])
        self.assertEqual(prices["historical-model"], [0.25, 0.75])
        for model, pair in trial.ACCOUNTING_UPPER_PRICES.items():
            self.assertEqual(tuple(prices[model]), pair)

    def test_upper_meter_covers_normal_reference_and_is_explicit_in_receipt(self):
        for model, upper in trial.ACCOUNTING_UPPER_PRICES.items():
            for ceiling, ordinary in zip(upper, trial.NORMAL_REFERENCE_PRICES[model]):
                self.assertGreaterEqual(ceiling, ordinary)
        candidate = trial.build_candidate(self.env, InstalledLLM)
        result = trial.validate_candidate(self.env, candidate, InstalledLLM)
        self.assertEqual(result["pricing_mode"], "conservative_context_tier_upper_bound")
        self.assertEqual(result["accounting_upper_rates"][trial.HAIKU], [0.5, 2.5])
        self.assertEqual(result["normal_reference_rates"][trial.HAIKU], [0.1, 0.5])
        self.assertEqual(json.loads(json.dumps(result)), result)

    def test_conflicting_existing_trial_price_is_rejected_without_mutation(self):
        self.env["PRICING_JSON"] = json.dumps({trial.HAIKU: [1, 5]})
        original = deepcopy(self.env)
        with self.assertRaises(ValueError):
            trial.build_candidate(self.env, InstalledLLM)
        self.assertEqual(self.env, original)

    def test_bad_json_duplicates_and_nonfinite_values_fail_closed(self):
        cases = ['{', '[]', '{"x":1,"x":2}', '{"x":{"a":1,"a":2}}',
                 '{"x":NaN}', '{"x":Infinity}', '{"x":1e999}']
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                trial.strict_json(value)
        for key in ("PRICING_JSON", "LLM_PROFILES_JSON"):
            for value in ('{', '{"duplicate":{},"duplicate":{}}'):
                env = dict(self.env, **{key: value})
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    trial.build_candidate(env, InstalledLLM)

    def test_invalid_price_shapes_fail_closed(self):
        for pair in ([True, 1], [-1, 1], [1], "1,2", [1, None]):
            env = dict(self.env, PRICING_JSON=json.dumps({"model": pair}))
            with self.subTest(pair=pair), self.assertRaises(ValueError):
                trial.build_candidate(env, InstalledLLM)

    def test_invalid_profiles_and_catalogue_drift_fail_closed(self):
        values = [
            {"new_unknown": {}}, {"router_fast": None},
            {"router_fast": {"fallbacks": "oops"}},
            {"router_fast": {"max_tokens": True}},
            {"router_fast": {"max_tokens": 0}},
            {"router_fast": {"json_required": "yes"}},
            {"router_fast": {"timeout": 91}},
            {"router_fast": {"temperature": True}},
        ]
        for value in values:
            env = dict(self.env, LLM_PROFILES_JSON=json.dumps(value))
            with self.subTest(value=value), self.assertRaises(ValueError):
                trial.build_candidate(env, InstalledLLM)
        with mock.patch.object(InstalledLLM, "default_profiles", return_value={}):
            with self.assertRaises(ValueError):
                trial.build_candidate(self.env, InstalledLLM)

    def test_validation_rejects_unrelated_deltas_controls_and_budget_changes(self):
        candidate = trial.build_candidate(self.env, InstalledLLM)
        changed = dict(candidate, VISION_MODEL=trial.HAIKU)
        with self.assertRaises(ValueError):
            trial.validate_candidate(self.env, changed, InstalledLLM)
        changed = deepcopy(candidate)
        profiles = trial.strict_json(changed["LLM_PROFILES_JSON"])
        profiles["router_fast"]["max_tokens"] = 999
        changed["LLM_PROFILES_JSON"] = json.dumps(profiles)
        with self.assertRaises(ValueError):
            trial.validate_candidate(self.env, changed, InstalledLLM)
        for budgets in ((0, 12), (2, 15), (float("nan"), 12), (True, 12), (2,)):
            with self.subTest(budgets=budgets), self.assertRaises(ValueError):
                trial.validate_candidate(self.env, candidate, InstalledLLM, budgets)

    def test_env_preserves_unrelated_bytes_and_assignment_style(self):
        raw = (b"# private fixture comment\r\n\r\n  export DO_CHAT_MODEL = 'deepseek-4-flash'\r\n"
               b"UNCHANGED=  keep internal spaces  \r\nSECRET='do not rewrite'\r\n; comment\r\n")
        parsed = trial.parse_env_bytes(raw)
        output = trial.update_env_bytes(raw, {"DO_CHAT_MODEL": trial.HAIKU, "NEW_KEY": "new value"})
        self.assertIn(b"  export DO_CHAT_MODEL = 'anthropic-claude-5.5-haiku'\r\n", output)
        for line in raw.splitlines(keepends=True):
            if b"DO_CHAT_MODEL" not in line:
                self.assertIn(line, output)
        self.assertEqual(trial.parse_env_bytes(output), dict(parsed, DO_CHAT_MODEL=trial.HAIKU, NEW_KEY="new value"))

    def test_env_quote_escapes_roundtrip_and_unchanged_values_keep_bytes(self):
        raw = b"A='same'\nB=plain\n"
        self.assertEqual(trial.update_env_bytes(raw, {"A": "same"}), raw)
        value = 'he said "it\'s $5" at C:\\folder'
        output = trial.update_env_bytes(raw, {"B": value})
        self.assertEqual(trial.parse_env_bytes(output)["B"], value)
        self.assertEqual(trial.parse_env_bytes(b'A="escaped \\" quote and \\$ and \\x"\n')["A"], 'escaped " quote and $ and \\x')
        # Unicode separators are ordinary value bytes, not physical file lines.
        raw = "A='before\u2028after'\nB=keep\n".encode()
        updated = trial.update_env_bytes(raw, {"B": "changed"})
        self.assertIn("A='before\u2028after'\n".encode(), updated)
        self.assertEqual(trial.parse_env_bytes(updated)["A"], "before\u2028after")

    def test_env_ambiguous_duplicates_and_multiline_rejected(self):
        for raw in (b"A=x\nA=y\n", b"bad assignment\n", b"A='unterminated\n",
                    b"A=continued\\\n", b"A=bad\x00value\n", b"A=\xff\n"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                trial.parse_env_bytes(raw)
        for value in ("a\nb", "a\rb", "a\x00b"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                trial.update_env_bytes(b"A=x\n", {"A": value})

    def test_reversion_only_merges_trial_keys_and_retains_later_unrelated_edits(self):
        candidate = trial.build_candidate(self.env, InstalledLLM)
        current = dict(candidate, NEW_SETTING="later", BUDGET_DAILY_USD="1.25", VISION_MODEL="later-vision")
        restored = trial.revert_model_keys(current, self.env, candidate)
        expected = dict(self.env, NEW_SETTING="later", BUDGET_DAILY_USD="1.25", VISION_MODEL="later-vision")
        self.assertEqual(restored, expected)
        self.assertEqual(current["DO_CHAT_MODEL"], trial.HAIKU)

    def test_reversion_removes_only_new_trial_keys_and_refuses_model_conflicts(self):
        old = {"UNRELATED": "keep"}
        candidate = trial.build_candidate(old, InstalledLLM)
        self.assertEqual(trial.revert_model_keys(dict(candidate, OTHER="later"), old, candidate),
                         {"UNRELATED": "keep", "OTHER": "later"})
        for key in trial.changed_model_keys(old, candidate):
            changed = dict(candidate, **{key: "owner changed this"})
            before = deepcopy(changed)
            with self.subTest(key=key), self.assertRaises(ValueError):
                trial.revert_model_keys(changed, old, candidate)
            self.assertEqual(changed, before)

    def test_candidate_and_revert_bytes_preserve_later_comments_and_settings(self):
        raw = b"# original\r\nUNRELATED=keep\r\nVISION_MODEL=llama-4-maverick\r\n"
        old = trial.parse_env_bytes(raw)
        candidate_raw = trial.candidate_env_bytes(raw, InstalledLLM)
        candidate = trial.parse_env_bytes(candidate_raw)
        current = candidate_raw + b"# added during trial\r\nLATER=unchanged\r\n"
        reverted = trial.revert_env_bytes(current, old, candidate)
        self.assertEqual(reverted, raw + b"# added during trial\r\nLATER=unchanged\r\n")

    def test_configuration_helpers_perform_no_filesystem_or_network_io(self):
        with mock.patch("builtins.open", side_effect=AssertionError("no file I/O")):
            candidate = trial.build_candidate(self.env, InstalledLLM)
            trial.validate_candidate(self.env, candidate, InstalledLLM)
            trial.revert_model_keys(candidate, self.env, candidate)
            trial.update_env_bytes(b"UNRELATED=keep\n", {"DO_CHAT_MODEL": trial.HAIKU})


if __name__ == "__main__":
    unittest.main()
