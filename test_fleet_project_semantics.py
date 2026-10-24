"""Closed answer obligations do not change project reads or historical plans."""
import unittest

import fleet_project
import fleet_project_report
import fleet_protocol as protocol
from test_fleet_project_dispatch import Harness, inventory, proposal, scope


class ProjectAnswerFieldSemantics(unittest.TestCase):
    def setUp(self):
        self.catalog = inventory()
        self.agent = Harness()

    def validate(self, value, previous=None):
        return self.agent._fleet_validate_planning(value, self.catalog, previous)

    def monitor(self, **changes):
        value = proposal(projects=["project-alpha"], facets=["projects"], focus=["projects"],
                         read_scope=scope(capabilities=["runtime"]))
        value.update(changes)
        return value

    def previous(self, fields=None):
        plan = self.validate(self.monitor(answer_fields=fields or ["generation", "reserves", "day_budget"]))
        return self.agent._fleet_context_frame("Generation, reserves and daily limit for Project Alpha", plan,
                                               self.catalog, update=12)

    def correction(self, **changes):
        value = proposal(hosts=[], projects=[], scope="inherit", focus=["overview"],
                         answer_mode="reformat", presentation="table", detail="concise")
        value.update(changes)
        return value

    def test_explicit_monitor_fields_preserve_order_and_read_exactly_the_same_scope(self):
        fields = ["reserves", "generation", "day_budget"]
        value = self.monitor(answer_fields=fields, presentation="list", detail="concise")
        plain = self.validate(self.monitor(presentation="list", detail="concise"))
        plan = self.validate(value)
        self.assertEqual(plan["answer_fields"], fields)
        self.assertIsNot(plan["answer_fields"], fields)
        self.assertEqual(plan["facets"], ["projects"])
        self.assertEqual(plan["read_scope"]["capabilities"], ["runtime"])
        self.assertEqual(self.agent._fleet_inspect_operation(plan, self.catalog),
                         self.agent._fleet_inspect_operation(plain, self.catalog))
        self.assertNotIn("answer_fields", self.agent._fleet_inspect_operation(plan, self.catalog))
        self.assertEqual(value["answer_fields"], fields)

    def test_all_nine_fields_and_explicit_all_projects_remain_presentation_only(self):
        fields = list(fleet_project_report.ANSWER_FIELD_ORDER)
        self.assertEqual(len(fields), 9)
        plan = self.validate(self.monitor(answer_fields=fields,
            read_scope=scope(projects=[], capabilities=["runtime"], kind="all_projects")))
        self.assertEqual(plan["answer_fields"], fields)
        self.assertEqual(set(plan["projects"]), {"project-alpha", "project-beta", "project-mesh"})
        self.assertEqual(plan["read_scope"]["capabilities"], ["runtime"])
        self.assertEqual(plan["facets"], ["projects"])

    def test_nonlist_empty_duplicate_unknown_and_nonstring_fields_are_rejected(self):
        invalid = (None, [], "generation", ("generation",), True, {}, [True], [1], [{}],
                   ["generation", "generation"], ["shell"], ["generation", "http://example.invalid"],
                   list(fleet_project_report.ANSWER_FIELD_ORDER) + ["generation"])
        for fields in invalid:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.validate(self.monitor(answer_fields=fields))

    def test_host_legacy_unresolved_and_wrong_capability_plans_cannot_acquire_fields(self):
        invalid = [proposal(answer_fields=["generation"]),
                   proposal(focus=["memory"], answer_fields=["project_metrics"]),
                   self.monitor(answer_fields=["generation"],
                                read_scope=scope(projects=[], capabilities=[], kind="host"), projects=[]),
                   self.monitor(answer_fields=["generation"], read_scope=scope(capabilities=["http_health"])),
                   self.monitor(answer_fields=["accepted_release"], read_scope=scope(capabilities=["release"])),
                   self.monitor(answer_fields=["generation"], clarification="Which project?")]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.validate(value)

    def test_restart_and_maintenance_card_meaning_stay_unchanged(self):
        restart = proposal(operation="restart", service="example-worker.service", facets=["services"])
        baseline = self.validate(restart)
        self.assertEqual(baseline["operation"], "restart")
        self.assertNotIn("answer_fields", baseline)
        with self.assertRaises(ValueError):
            self.validate(dict(restart, answer_fields=["application_health"]))

    def test_legacy_absence_preserves_plan_and_frame(self):
        plan = self.validate(self.monitor())
        self.assertNotIn("answer_fields", plan)
        frame = self.agent._fleet_context_frame("Check Project Alpha", plan, self.catalog, update=12)
        self.assertNotIn("answer_fields", frame)
        prior = self.agent._fleet_context_frame("Check Project Alpha", plan, self.catalog, update=12)
        self.assertNotIn("answer_fields", self.validate(self.correction(), prior))

    def test_frame_copies_ordered_fields_without_mutating_plan(self):
        plan = self.validate(self.monitor(answer_fields=["day_budget", "generation"]))
        frame = self.agent._fleet_context_frame("Project Alpha budget and generation", plan, self.catalog, update=12)
        self.assertEqual(frame["answer_fields"], ["day_budget", "generation"])
        frame["answer_fields"].append("reserves")
        self.assertEqual(plan["answer_fields"], ["day_budget", "generation"])

    def test_format_only_reformat_inherits_same_canonical_fields_and_scope(self):
        prior = self.previous()
        plan = self.validate(self.correction(), prior)
        self.assertEqual(plan["answer_fields"], prior["answer_fields"])
        self.assertEqual(plan["read_scope"], prior["read_scope"])
        self.assertEqual(plan["hosts"], prior["hosts"])
        self.assertEqual(plan["projects"], prior["projects"])
        self.assertEqual(plan["presentation"], "table")
        self.assertEqual(plan["detail"], "concise")
        plan["answer_fields"].append("target")
        self.assertNotIn("target", prior["answer_fields"])

    def test_explicit_reformat_fields_replace_presentation_without_changing_reads(self):
        prior = self.previous()
        inherited = self.validate(self.correction(), prior)
        revised = self.validate(self.correction(answer_fields=["day_budget"]), prior)
        self.assertEqual(revised["answer_fields"], ["day_budget"])
        self.assertEqual(self.agent._fleet_inspect_operation(inherited, self.catalog),
                         self.agent._fleet_inspect_operation(revised, self.catalog))
        self.assertEqual(prior["answer_fields"], ["generation", "reserves", "day_budget"])

    def test_fresh_same_target_question_does_not_inherit_previous_answer_fields(self):
        prior = self.previous()
        for value in (self.monitor(), self.correction(answer_mode="inspect")):
            with self.subTest(value=value):
                self.assertNotIn("answer_fields", self.validate(value, prior))
        revised = self.validate(self.monitor(answer_fields=["application_health"]), prior)
        self.assertEqual(revised["answer_fields"], ["application_health"])

    def test_new_concrete_focus_does_not_inherit_even_when_runtime_scope_matches(self):
        prior = self.previous()
        plan = self.validate(self.correction(focus=["services"]), prior)
        self.assertEqual(plan["read_scope"], prior["read_scope"])
        self.assertNotIn("answer_fields", plan)
        explicit = self.validate(self.correction(focus=["services"], answer_fields=["application_health"]), prior)
        self.assertEqual(explicit["answer_fields"], ["application_health"])

    def test_new_named_target_or_capability_scope_never_inherits_field_selection(self):
        prior = self.previous()
        named = self.validate(self.monitor(projects=["project-beta"],
            read_scope=scope(projects=["project-beta"], capabilities=["runtime"])), prior)
        self.assertEqual(named["projects"], ["project-beta"])
        self.assertNotIn("answer_fields", named)
        changed = self.validate(self.correction(read_scope=scope(capabilities=["runtime", "containers"])), prior)
        self.assertNotIn("answer_fields", changed)

    def test_invalid_or_stale_prior_cannot_supply_answer_fields(self):
        for key, value in (("inventory_hash", "0" * 64), ("read_contract_hash", "0" * 64),
                           ("answer_fields", ["generation", "generation"]), ("answer_fields", ["shell"])):
            prior = self.previous()
            prior[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.validate(self.correction(), prior)
        with self.assertRaises(ValueError):
            self.validate(self.correction())

    def test_answer_fields_never_normalize_an_invalid_focus(self):
        for invalid in (["runtime"], ["generation"], ["reserves"], ["day_budget"]):
            with self.subTest(focus=invalid), self.assertRaisesRegex(ValueError, "unknown investigation focus"):
                self.validate(self.monitor(focus=invalid, answer_fields=["generation"]))

    def test_existing_single_planner_call_declares_fields_separately_from_focus(self):
        messages = self.agent._fleet_planning_messages(
            "Project Alpha generation, reserves and daily limit, briefly", self.catalog, {}, "en")
        self.assertEqual(len(messages), 2)
        system = messages[0]["content"]
        for field in fleet_project_report.ANSWER_FIELD_ORDER:
            self.assertIn(field, system)
        for expected in ("answer_fields IDs are not focus values", "not a focus", "focus:[projects]",
                         "same field obligations", "confirmed preference", "never an inferred spend",
                         "application_health means application/Postgres", "never authenticated proxy protocol/route health",
                         "or connection questions ordinarily omit answer_fields"):
            self.assertIn(expected, system)


if __name__ == "__main__":
    unittest.main()
