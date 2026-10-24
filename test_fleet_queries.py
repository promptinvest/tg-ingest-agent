"""Catalog business obligations and frozen semantic contracts; no model qualification claim."""
import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import fleet_facts as facts
import fleet_queries as queries
from test_fleet_facts import FIXTURE, add_fact, change, fixture


def raw(subjects=None, fields=None, scope="explicit", presentation="list", detail="concise", **extra):
    value = {"kind": "catalog", "subjects": subjects if subjects is not None else ["host-a"],
             "scope": scope, "fields": fields if fields is not None else ["label"],
             "presentation": presentation, "detail": detail, "clarification": ""}
    value.update(extra)
    return value


def planned(document, *args, **kwargs):
    return queries.validate_plan(raw(*args, **kwargs), document)


def prior(document, subjects=None, fields=None):
    plan = planned(document, subjects, fields)
    return queries.frame("Owner's original catalog request", plan, 101)


class CatalogContract(unittest.TestCase):
    def setUp(self):
        self.document = fixture()

    def test_prompt_extends_existing_planner_without_factual_prose_or_execution_authority(self):
        instruction = queries.plan_instruction()
        for required in ("catalog", "subjects", "scope", "fields", "presentation", "detail", "clarification", "RU/EN"):
            self.assertIn(required, instruction)
        self.assertIn("NEVER supplies answer text", instruction)
        context = queries.planner_context(self.document)
        self.assertEqual(context["integrity"], self.document["integrity"])
        self.assertIsNone(context["prior_catalog"])
        self.assertNotIn("restart_services", facts.canonical(context))
        self.assertNotIn("192.0.2.10", facts.canonical(context))

    def test_planner_context_contains_typed_placement_and_dependency_links(self):
        context = queries.planner_context(self.document)
        placement = [relation for relation in context["relations"] if relation["subject_id"] == "project-alpha"]
        self.assertEqual(placement, [{"subject_id": "project-alpha", "predicate": "hosted_on", "object_id": "host-a",
                                    "status": "recorded", "evidence_at": "2026-10-20", "conflicting": False}])
        self.assertNotIn("source_ids", facts.canonical(context["relations"]))
        self.assertNotIn("command", facts.canonical(context["relations"]))

    def test_strict_plan_fields_prevent_model_fact_or_command_injection(self):
        for extra in ({"answer": "Project Alpha is healthy"}, {"value": "WrongCloud"}, {"command": "id"}, {"hosts": ["host-a"]}, {"operation": "restart"}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                queries.validate_plan(raw(**extra), self.document)
        for field in ("healthy", "backup_success", "complete", "cpu", "shell"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                queries.validate_plan(raw(fields=[field]), self.document)

    def test_unknown_duplicate_and_executable_subject_strings_are_rejected(self):
        for subjects in (["missing"], ["host-a", "host-a"], ["192.0.2.10"], ["host-a;id"], [["host-a"]]):
            with self.subTest(subjects=subjects), self.assertRaises(ValueError):
                queries.validate_plan(raw(subjects), self.document)

    def test_modes_presentation_and_obligation_types_are_closed(self):
        for key, value in (("kind", "runtime"), ("scope", "everything"), ("presentation", "free_html"),
                           ("detail", "infinite"), ("subjects", "host-a"), ("fields", "label"),
                           ("fields", ["label", "label"]), ("clarification", 12), ("presentation", [])):
            candidate = raw()
            candidate[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                queries.validate_plan(candidate, self.document)

    def test_scope_all_is_resolved_in_code_and_excludes_retired_and_unverified(self):
        plan = planned(self.document, ["host-a"], ["provider"], scope="all")
        self.assertEqual(plan["subjects"], ["host-a", "host-b", "host-c"])
        project_plan = planned(self.document, ["project-alpha"], ["label"], scope="all")
        self.assertEqual(set(project_plan["subjects"]), {"project-alpha", "project-beta", "project-mesh"})
        self.assertNotIn("host-old", plan["subjects"])

    def test_explicit_historical_subject_can_be_described_without_becoming_a_target(self):
        plan = planned(self.document, ["host-old"], ["lifecycle"])
        answer = "\n".join(queries.render(self.document, plan))
        self.assertIn("Retired fixture", answer)
        self.assertIn("retired", answer)
        self.assertNotIn("healthy", answer)

    def test_inherited_scope_cannot_be_silently_shrunk(self):
        previous = prior(self.document, ["host-a", "host-b", "host-c"], ["provider", "public_ipv4"])
        plan = queries.validate_plan(raw(["host-a"], [], scope="inherit", presentation="table"), self.document, previous)
        self.assertEqual(plan["subjects"], previous["subjects"])
        self.assertEqual(plan["fields"], previous["fields"])
        self.assertEqual(plan["presentation"], "table")
        self.assertEqual(len(queries.cells(self.document, plan)), 6)

    def test_explicit_new_project_overrides_old_host_scope(self):
        previous = prior(self.document, ["host-a", "host-b", "host-c"], ["label"])
        plan = queries.validate_plan(raw(["project-alpha"], ["project_hosts"], scope="inherit"), self.document, previous)
        self.assertEqual(plan["scope"], "explicit")
        self.assertEqual(plan["subjects"], ["project-alpha"])
        answer = "\n".join(queries.render(self.document, plan))
        self.assertIn("Project Alpha", answer)
        self.assertIn("Example application host", answer)
        self.assertNotIn("Project Beta", answer)

    def test_prior_requires_matching_integrity_owner_update_age_and_field_types(self):
        previous = prior(self.document)
        self.assertTrue(queries.prior_valid(self.document, previous))
        for update in ({"source_update": True}, {"source_update": -1}, {"facts_integrity": "a" * 64},
                       {"presentation": []}, {"subjects": ["missing"]}, {"fields": ["healthy"]},
                       {"created_at": (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()}):
            invalid = dict(previous, **update)
            with self.subTest(update=update):
                self.assertFalse(queries.prior_valid(self.document, invalid))
                with self.assertRaises(ValueError):
                    queries.validate_plan(raw([], [], scope="inherit"), self.document, invalid)
        self.assertFalse(queries.prior_valid(change(self.document), previous))

    def test_frame_retains_original_owner_request_but_explicit_scope_starts_new_question(self):
        previous = prior(self.document, ["host-a"], ["provider"])
        plan = queries.validate_plan(raw([], [], scope="inherit", presentation="table"), self.document, previous)
        frame = queries.frame("Put that in a table", plan, 102, previous)
        self.assertEqual(frame["original_question"], previous["original_question"])
        self.assertEqual(frame["question"], "Put that in a table")
        explicit = planned(self.document, ["project-alpha"], ["purpose"])
        self.assertEqual(queries.frame("Describe Project Alpha", explicit, 103, previous)["original_question"], "Describe Project Alpha")

    def test_names_format_cannot_erase_requested_provider_or_address_fields(self):
        with self.assertRaises(ValueError):
            planned(self.document, fields=["provider", "public_ipv4"], presentation="names")
        plan = planned(self.document, fields=["label"], presentation="names")
        self.assertEqual(queries.render(self.document, plan), ["Example application host"])

    def test_inert_clarification_submits_no_subject_and_does_not_send_model_assertions(self):
        plan = queries.validate_plan(raw(["host-a"], ["provider"], kind="clarification", clarification="Ghost Project is healthy"), self.document)
        self.assertEqual(plan["subjects"], [])
        self.assertEqual(queries.cells(self.document, plan), [])
        answer = "\n".join(queries.render(self.document, plan))
        self.assertIn("Which server or project", answer)
        self.assertNotIn("Ghost Project", answer)
        self.assertNotIn("healthy", answer)

    def test_changed_or_tampered_facts_cannot_silently_answer_old_bound_plan(self):
        plan = planned(self.document, fields=["provider"])
        with self.assertRaises(ValueError):
            queries.render(change(self.document), plan)
        tampered = copy.deepcopy(self.document)
        tampered["facts"][1]["value"] = "WrongCloud"
        with self.assertRaises(ValueError):
            queries.render(tampered, plan)
        with self.assertRaises(ValueError):
            queries.render(self.document, raw(fields=["provider"]))

    def test_production_load_rejects_fixture_and_uses_only_configured_local_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "facts.json"
            path.write_text(facts.canonical(self.document))
            with self.assertRaises(ValueError):
                queries.load(SimpleNamespace(fleet_facts=str(path)))
        with mock.patch.object(facts, "load", return_value=self.document) as loader:
            self.assertEqual(queries.load(SimpleNamespace()), self.document)
        loader.assert_called_once_with(Path("/etc/cara-fleet/facts/current.json"))


class CatalogBusinessAnswers(unittest.TestCase):
    def setUp(self):
        self.document = fixture()

    def test_every_requested_entity_field_has_a_value_or_specific_gap(self):
        plan = planned(self.document, ["host-a"], ["label", "provider", "public_ipv4", "region", "role"], scope="all", presentation="table")
        obligations = queries.cells(self.document, plan)
        self.assertEqual(len(obligations), 15)
        gaps = {(cell["subject_id"], cell["field"]) for cell in obligations if cell["status"] == "unknown"}
        self.assertTrue({("host-c", "provider"), ("host-c", "region"), ("host-a", "role")} <= gaps)
        answer = "\n".join(queries.render(self.document, plan))
        for expected in ("Example application host", "Example edge host", "Example observer host", "ExampleCloud", "192.0.2.10", "Provider", "Public IPv4", "Region", "unknown"):
            self.assertIn(expected, answer)
        for irrelevant in ("CPU", "PID", "healthy", "on track", "backup successful"):
            self.assertNotIn(irrelevant, answer)

    def test_project_listing_uses_placements_not_processes_and_retains_colocated_names(self):
        plan = planned(self.document, ["host-a"], ["hosted_projects"])
        answer = "\n".join(queries.render(self.document, plan, "ru"))
        self.assertIn("Project Alpha", answer)
        self.assertIn("Project Beta", answer)
        self.assertNotIn("Example Mesh", answer)
        self.assertNotIn("process", answer)
        self.assertNotIn("единствен", answer)

    def test_unknown_completeness_never_becomes_only_or_absence(self):
        plan = planned(self.document, ["host-b"], ["hosted_projects"])
        answer = "\n".join(queries.render(self.document, plan))
        self.assertIn("Example Mesh", answer)
        self.assertIn("catalog completeness unknown", answer)
        self.assertNotIn("only", answer)
        empty = planned(self.document, ["host-c"], ["dependencies"])
        answer = "\n".join(queries.render(self.document, empty))
        self.assertIn("Dependencies: unknown", answer)
        self.assertNotIn("no dependencies", answer)

    def test_multihost_project_placement_is_complete_for_requested_project_scope(self):
        plan = planned(self.document, ["project-mesh"], ["project_hosts"])
        answer = "\n".join(queries.render(self.document, plan))
        self.assertIn("Example edge host", answer)
        self.assertIn("Example observer host", answer)
        self.assertNotIn("Example application host", answer)

    def test_outbound_and_public_addresses_remain_distinct_and_outbound_date_is_explicit(self):
        plan = planned(self.document, ["host-c"], ["public_ipv4", "outbound_ipv4"])
        answer = "\n".join(queries.render(self.document, plan))
        self.assertIn("Public IPv4: 203.0.113.30", answer)
        self.assertIn("Outbound IPv4: 203.0.113.31", answer)
        self.assertIn("2026-06-01", answer)
        self.assertNotIn(self.document["generated_at"], answer)
        unknown = "\n".join(queries.render(self.document, planned(self.document, ["host-a"], ["outbound_ipv4"])))
        self.assertIn("observation date unknown", unknown)

    def test_conflicting_values_are_reported_without_selecting_newest_or_guessing(self):
        add_fact(self.document)
        self.document = facts.seal(self.document)
        plan = planned(self.document, fields=["provider"], detail="detailed")
        answer = "\n".join(queries.render(self.document, plan))
        for value in ("conflicting records", "ExampleCloud", "Second Cloud", "2026-10-20", "2026-10-23"):
            self.assertIn(value, answer)

    def test_purpose_repository_and_lifecycle_do_not_invent_development_completion(self):
        add_fact(self.document, "repository-alpha", "repository", "example/project-alpha", "project-alpha")
        self.document = facts.seal(self.document)
        plan = planned(self.document, ["project-alpha"], ["purpose", "repository", "lifecycle"])
        answer = "\n".join(queries.render(self.document, plan))
        self.assertIn("Fictional application", answer)
        self.assertIn("example/project-alpha", answer)
        self.assertIn("recorded as deployed", answer)
        for unsupported in ("complete", "healthy", "working", "on track"):
            self.assertNotIn(unsupported, answer)

    def test_meaningful_labels_are_preserved_in_each_presentation(self):
        self.document["subjects"][0]["label"] = "Example / Project Alpha"
        self.document = facts.seal(self.document)
        for presentation in queries.PRESENTATIONS:
            plan = planned(self.document, fields=["label"], presentation=presentation)
            with self.subTest(presentation=presentation):
                self.assertIn("Example / Project Alpha", "\n".join(queries.render(self.document, plan)))

    def test_pages_are_bounded_and_every_entity_and_requested_value_survives(self):
        for index in range(45):
            identity = f"project-extra-{index}"
            self.document["subjects"].append({"id": identity, "kind": "project", "label": f"Example extra project {index}",
                "source_ids": ["fixture-source-current"], "evidence_at": None})
            add_fact(self.document, "lifecycle-extra-" + str(index), "lifecycle", "development", identity)
            add_fact(self.document, "purpose-extra-" + str(index), "purpose", ("Useful documented purpose " * 20).strip() + " " + str(index), identity)
        self.document = facts.seal(self.document)
        for presentation in ("table", "list", "prose"):
            plan = planned(self.document, ["project-alpha"], ["label", "purpose"], scope="all", presentation=presentation)
            pages = queries.render(self.document, plan)
            with self.subTest(presentation=presentation):
                self.assertGreater(len(pages), 1)
                self.assertTrue(all(len(page) <= queries.MAX_PAGE for page in pages))
                for index in range(45):
                    self.assertIn(f"Example extra project {index}", "\n".join(pages))
                if presentation == "table":
                    self.assertTrue(all(page.startswith("| Name | Purpose |") for page in pages))

    def test_original_catalog_corpus_is_used_for_frozen_obligation_contracts(self):
        corpus = json.loads((FIXTURE.parent / "regression-cases.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(len(corpus["cases"]), 47)
        checked = []
        # Frozen completions exercise validation/rendering, not semantic-model quality.
        for case in corpus["cases"]:
            expected = case["expected"]
            if expected.get("mode") != "catalog":
                continue
            identity = case["id"]
            subjects = expected.get("scope") or (["host-a"] if identity == "missing-probe" else ["host-a", "host-b", "host-c"])
            fields = expected.get("required_fields") or (["provider"] if identity == "unknown-provider" else
                ["hosted_projects"] if identity in {"shared-host-projects", "edge-host-projects", "missing-probe"} else
                ["public_ipv4", "outbound_ipv4"] if identity == "public-versus-egress" else ["public_ipv4"])
            if identity in {"catalog-outage"}:
                subjects = ["host-a"]
            if identity in {"unknown-provider", "public-versus-egress"}:
                subjects = ["host-c"]
            presentation = {"names only": "names"}.get(expected.get("presentation"), expected.get("presentation", "list"))
            if identity == "reformat-no-reprobe":
                subjects, fields = list(expected["required_entities"]), ["label"]
            elif identity == "untrusted-note-action":
                fields = ["label"]
            elif identity == "known-ip-model-unavailable":
                subjects = ["host-a"]
            if identity == "correction-retains-question":
                subjects = list(case["prior_scope"])
            plan = planned(self.document, subjects, fields, presentation=presentation)
            with self.subTest(case=identity), mock.patch("socket.create_connection", side_effect=AssertionError("catalog must not inspect hosts")):
                answer = "\n".join(queries.render(self.document, plan, "ru" if re_cyrillic(case["question"]) else "en"))
                covered = {(cell["subject_id"], cell["field"]) for cell in queries.cells(self.document, plan)}
                self.assertTrue(all((subject, field) in covered for subject in subjects for field in fields))
                for required in expected.get("required_values", []):
                    self.assertIn(required, answer)
                labels = {subject["id"]: subject["label"] for subject in self.document["subjects"]}
                for entity in expected.get("required_entities", []):
                    self.assertIn(labels[entity], answer)
                checked.append(identity)
        self.assertEqual(len(checked), 17)


class OfflineCatalogFallback(unittest.TestCase):
    def setUp(self):
        self.document = fixture()

    def test_original_roster_word_orders_and_held_out_paraphrases_work_without_model(self):
        for question in ("Какие у меня VPS?", "Какие VPS у меня?", "Which machines are mine?", "Перечисли мои серверы, пожалуйста."):
            plan = queries.fallback(question, self.document)
            with self.subTest(question=question):
                self.assertEqual(plan["kind"], "catalog")
                self.assertEqual(plan["subjects"], ["host-a", "host-b", "host-c"])
                self.assertEqual(plan["fields"], ["label"])

    def test_known_address_and_provider_named_lookup_need_no_executor(self):
        for question, field, expected in (("What is the address of Example application host?", "public_ipv4", "192.0.2.10"),
                ("Who hosts Example observer host?", "provider", "unknown")):
            plan = queries.fallback(question, self.document)
            with self.subTest(question=question), mock.patch("socket.create_connection", side_effect=AssertionError("executor unavailable")):
                self.assertEqual(plan["fields"], [field])
                self.assertIn(expected, "\n".join(queries.render(self.document, plan)))

    def test_aliases_are_meaningful_named_scope_and_ambiguous_reference_stays_inert(self):
        add_fact(self.document, "alias-host-a", "aliases", ["Example Primary", "Пример основной"], "host-a")
        self.document = facts.seal(self.document)
        plan = queries.fallback("IP для Пример основной", self.document)
        self.assertEqual(plan["subjects"], ["host-a"])
        self.assertEqual(queries.fallback("What projects are on that server?", self.document)["kind"], "clarification")

    def test_owner_bound_followups_preserve_fields_and_names_only_is_explicit(self):
        previous = prior(self.document, ["host-a", "host-b", "host-c"], ["provider", "public_ipv4"])
        table = queries.fallback("Put that in a table", self.document, previous)
        self.assertEqual(table["fields"], ["provider", "public_ipv4"])
        self.assertEqual(table["subjects"], previous["subjects"])
        self.assertEqual(table["presentation"], "table")
        names = queries.fallback("just the names", self.document, previous)
        self.assertEqual(names["fields"], ["label"])
        self.assertEqual(names["presentation"], "names")
        ips = queries.fallback("Дай ip адреса", self.document, previous)
        self.assertEqual(ips["fields"], ["public_ipv4"])
        self.assertEqual(ips["subjects"], previous["subjects"])

    def test_project_names_reformat_selects_relationship_targets_without_a_probe(self):
        previous = prior(self.document, ["host-a"], ["hosted_projects"])
        for question in ("Just the project names, please.", "just the names", "Только названия проектов"):
            plan = queries.fallback(question, self.document, previous)
            with self.subTest(question=question), mock.patch("socket.create_connection", side_effect=AssertionError("no re-probe")):
                self.assertEqual(plan["subjects"], ["project-alpha", "project-beta"])
                self.assertEqual(plan["fields"], ["label"])
                self.assertEqual(plan["presentation"], "names")
                answer = "\n".join(queries.render(self.document, plan))
                self.assertIn("Project Alpha", answer)
                self.assertIn("Project Beta", answer)
                self.assertNotIn("Example application host", answer)
                self.assertNotIn("only", answer)

    def test_names_reformat_preserves_unknown_relationship_gap(self):
        previous = prior(self.document, ["host-a"], ["dependencies"])
        plan = queries.fallback("just the names", self.document, previous)
        self.assertEqual(plan["fields"], ["dependencies"])
        answer = "\n".join(queries.render(self.document, plan))
        self.assertIn("Dependencies: unknown", answer)
        self.assertNotEqual(answer, "Example application host")

    def test_unknown_target_or_cleared_context_does_not_substitute_fleet_scan(self):
        plan = queries.fallback("А IP?", self.document, None)
        self.assertEqual(plan["kind"], "clarification")
        self.assertEqual(plan["subjects"], [])
        plan = queries.fallback("Address of Unregistered Example", self.document)
        self.assertEqual(plan["kind"], "clarification")

    def test_project_host_followups_use_registry_with_no_runtime_state_claim(self):
        previous = prior(self.document, ["host-a"], ["label"])
        for question in ("Какие мои проекты на этом сервере?", "Which projects are hosted here?"):
            plan = queries.fallback(question, self.document, previous)
            answer = "\n".join(queries.render(self.document, plan))
            with self.subTest(question=question):
                self.assertEqual(plan["fields"], ["hosted_projects"])
                self.assertIn("Project Alpha", answer)
                self.assertIn("Project Beta", answer)

    def test_unrelated_runtime_development_action_and_ordinary_skills_remain_outside_fallback(self):
        for question in ("Напомни IP завтра", "Проверь Project Alpha сейчас", "Перечисли VPS и проверь Project Alpha", "Is Project Alpha healthy?",
                         "Which projects are complete?", "Какие проекты уже готовы?", "Show my budget", "Explain the CPU load", "Restart my server"):
            with self.subTest(question=question):
                self.assertIsNone(queries.fallback(question, self.document))


def re_cyrillic(text):
    return any("а" <= character.casefold() <= "я" for character in text)


class SemanticRelationNames(unittest.TestCase):
    def setUp(self):
        self.document = fixture()

    def test_semantic_names_relationship_resolves_exact_targets_without_host_label(self):
        plan = planned(self.document, ["host-a"], ["hosted_projects"], presentation="names")
        self.assertEqual(set(plan["subjects"]), {"project-alpha", "project-beta"})
        self.assertEqual(plan["fields"], ["label"])
        answer = "\n".join(queries.render(self.document, plan))
        self.assertIn("Project Alpha", answer)
        self.assertIn("Project Beta", answer)
        self.assertNotIn("Example application host", answer)

    def test_names_relationship_keeps_partial_catalog_and_unknown_gaps(self):
        plan = planned(self.document, ["host-c"], ["hosted_projects"], presentation="names")
        self.assertEqual(plan["fields"], ["hosted_projects"])
        self.assertEqual(plan["presentation"], "list")
        answer = "\n".join(queries.render(self.document, plan))
        self.assertIn("catalog", answer)
        self.assertNotIn("only project", answer)

if __name__ == "__main__":
    unittest.main()



