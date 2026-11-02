"""ADR-0024: supplied parameter shapes, with legacy handler defaults intact."""
import json
import unittest

import router
import skill_manifest


class RouteParameterContracts(unittest.TestCase):
    def route(self, action, params, pending=False):
        return router.validate_route_reason(
            {"action": action, "params": params, "confidence": 0.9}, pending)

    def test_every_router_action_has_an_explicit_contract(self):
        self.assertEqual(set(skill_manifest.PARAM_CONTRACTS), router.ACTIONS)
        skill_manifest.assert_covers(router.ACTIONS)

    def test_exact_undo_command_policy_is_internal_and_not_a_model_route(self):
        policy = skill_manifest.get_policy("action_undo")
        self.assertEqual(policy["risk"], "state_write")
        self.assertTrue(policy["writes_state"])
        self.assertTrue(policy["internal"])
        self.assertNotIn("action_undo", router.ACTIONS)
        self.assertNotIn("action_undo", skill_manifest.PARAM_CONTRACTS)
        for lang in ("en", "ru"):
            self.assertNotIn(policy["title"][lang], skill_manifest.capability_titles(lang))

    def test_all_missing_and_legacy_nonobject_params_keep_empty_defaults(self):
        for action in sorted(router.ACTIONS):
            for supplied in (None, "junk", [], 0, False):
                with self.subTest(action=action, supplied=supplied):
                    parsed, reason = self.route(action, supplied, pending=True)
                    self.assertEqual(reason, "")
                    self.assertEqual(parsed["params"], {})
            parsed = router.validate_route({"action": action}, True)
            self.assertEqual(parsed["params"], {})

    def test_actual_prompt_examples_conform_without_rewriting_placeholders(self):
        count = 0
        for line in router.ROUTER_EXAMPLES.splitlines():
            if "-> {" not in line:
                continue
            example = json.loads("{" + line.rsplit("-> {", 1)[1])
            with self.subTest(example=example):
                parsed, reason = router.validate_route_reason(example, True)
                self.assertEqual(reason, "")
                self.assertEqual(parsed["params"], example["params"])
            count += 1
        self.assertGreater(count, 80)

    def test_unsafe_structured_scalar_fields_fail_before_handler_dispatch(self):
        cases = (
            ("list_items", "query"), ("recategorize", "category"),
            ("reminder_cancel", "all"), ("set_journal", "on"),
            ("task_list", "all"), ("proactive_prefs", "enabled"),
            ("remember", "value"), ("note_edit", "new_summary"),
            ("amend", "snooze_minutes"), ("amend", "done"),
        )
        for action, name in cases:
            for value in ({"untrusted": "value"}, ["untrusted"]):
                with self.subTest(action=action, name=name, value=value):
                    parsed, reason = self.route(action, {name: value}, pending=True)
                    self.assertIsNone(parsed)
                    self.assertEqual(reason, f"invalid_parameter:{action}.{name}")

    def test_scalar_coercions_are_not_replaced_with_strict_types_or_enums(self):
        cases = (
            ("budget_set", {"amount": "$1,50", "period": "месяц"}),
            ("budget_set", {"amount": 0, "period": None}),
            ("proactive_prefs", {"enabled": "да", "quiet_start": "23"}),
            ("set_journal", {"category": "Gratitude", "on": "false"}),
            ("catalog_add", {"title": "Dune", "year": 2021, "author": "Herbert"}),
            ("note_edit", {"summary": "legacy alias"}),
            ("style_update", {"tone": "neutral_concise"}),
            ("merge_categories", {"query": "old", "category": "new"}),
            ("review", {"focus": "corrections", "schedule": "next"}),
            ("amend", {"due_utc": None, "snooze_minutes": "30"}),
            ("journal_prompt", {"on": False, "time": 21}),
        )
        for action, params in cases:
            with self.subTest(action=action, params=params):
                parsed, reason = self.route(action, params, pending=True)
                self.assertEqual(reason, "")
                self.assertIs(parsed["params"], params)

    def test_safe_selector_artifacts_keep_existing_downstream_resolution(self):
        for action in ("item_detail", "item_delete", "read_media", "note_lifecycle",
                       "reminder_undo", "task_show"):
            for value in ("#7", "J#7", "7.", "", {"stray": "artifact"}, []):
                params = {"id": value, "query": "explicit search"}
                with self.subTest(action=action, value=value):
                    parsed, reason = self.route(action, params)
                    self.assertEqual(reason, "")
                    self.assertIs(parsed["params"], params)
        for ids in ([1, "#2"], [], "ignored legacy artifact", {"stray": 1}):
            params = {"ids": ids}
            parsed, reason = self.route("item_delete", params)
            self.assertEqual(reason, "")
            self.assertIs(parsed["params"], params)

    def test_handler_validated_fields_keep_their_safe_invalid_value_paths(self):
        cases = (
            ("item_delete", {"count": {"stray": 1}, "query": "match"}),
            ("recategorize", {"count": [], "category": "new"}),
            ("task_feedback", {"rating": {}, "correction": "real owner correction"}),
            ("improvement_decide", {"id": "1", "accept": {"bad": True}}),
            ("reminder_create", {"title": "keep partial", "due_utc": {"bad": 1}}),
            ("note_lifecycle", {"operation": "review_later", "when": ["bad"]}),
            ("journal_prompt", {"on": False, "time": {"bad": 1}}),
            ("list_field", {"field": {"bad": 1}}),
            ("proactive_prefs", {"note_review": False, "quiet_start": {"bad": 1}}),
            ("proactive_prefs", {"enabled": False, "days": ["bad"]}),
            ("catalog_add", {"title": "Dune", "year": [2021], "kind": ["book"]}),
            ("note_lifecycle", {"operation": "archive", "purpose": {"unused": 1}}),
            ("boss_memory_update", {"value": "fact", "kind": ["unknown"]}),
            ("review", {"period": "day", "focus": {"ignored": 1}}),
        )
        for action, params in cases:
            parsed, reason = self.route(action, params)
            self.assertEqual(reason, "")
            self.assertIs(parsed["params"], params)

    def test_empty_containers_keep_existing_missing_field_and_alias_defaults(self):
        cases = (
            ("note_edit", {"new_summary": {}, "summary": "usable legacy alias"}),
            ("ask", {"question": []}),
            ("list_items", {"category": [], "query": {}}),
            ("set_journal", {"on": {}, "category": "Gratitude"}),
            ("reminder_cancel", {"all": [], "id": 1}),
        )
        for action, params in cases:
            parsed, reason = self.route(action, params)
            self.assertEqual(reason, "")
            self.assertIs(parsed["params"], params)

    def test_unknown_ignored_fields_are_preserved_without_normalization(self):
        for action in sorted(router.ACTIONS - {"task_start"}):
            params = {"unused_legacy_field": {"nested": [1, "two"]}}
            parsed, reason = self.route(action, params, pending=True)
            self.assertEqual(reason, "")
            self.assertIs(parsed["params"], params)

    def test_existing_analysis_errors_take_precedence(self):
        cases = (
            ({"mode": ["analysis"]}, "invalid_task_mode"),
            ({"mode": "analysis", "unknown": 1}, "unknown_analysis_parameter"),
            ({"mode": "analysis", "context_task_id": "1"}, "invalid_analysis_context"),
        )
        for params, expected in cases:
            self.assertEqual(self.route("task_start", params), (None, expected))
        params = {"mode": "analysis", "context_task_id": 1}
        self.assertEqual(self.route("task_start", params)[0]["params"], params)

    def test_unsupported_schedule_fields_still_reach_original_guard(self):
        for action in ("reminder_create", "reminder_reschedule"):
            for params in ({"recurrence": "monthly"}, {"until_utc": "later"},
                           {"interval": {"days": 2}}, {"count": 3}):
                with self.subTest(action=action, params=params):
                    self.assertEqual(self.route(action, params),
                                     (None, "unsupported_reminder_schedule"))

    def test_pending_only_guard_keeps_its_original_error(self):
        self.assertEqual(self.route("amend", {"done": {"bad": True}}),
                         (None, "pending_only_without_pending"))


if __name__ == "__main__":
    unittest.main()
