"""CF-05 planner-to-executor isolation and legacy behavior; synthetic projects only."""
import copy
import json
import unittest
from unittest import mock

import cara_fleet
import fleet
import fleet_context
import fleet_host
import fleet_project
import fleet_protocol as protocol
import llm


def inventory():
    return {"version": protocol.VERSION,
        "hosts": {"host-a": {"label": "Example application host", "role": "Applications", "aliases": ["Primary"], "restart_services": ["example-worker.service"]},
                  "host-b": {"label": "Example second host", "role": "Second deployment", "aliases": ["Secondary"], "restart_services": []}},
        "projects": {"project-alpha": {"label": "Project Alpha", "hosts": ["host-a"], "recorded_at": "2026-10-20", "notes": "Synthetic application"},
                     "project-beta": {"label": "Project Beta", "hosts": ["host-a"], "recorded_at": "2026-10-20", "notes": "Colocated synthetic application"},
                     "project-mesh": {"label": "Example Mesh", "hosts": ["host-a", "host-b"], "recorded_at": "2026-10-20", "notes": "Multi-host synthetic application"}}}


def proposal(**updates):
    value = {"hosts": ["host-a"], "facets": ["resources"], "clarification": "", "operation": "inspect",
             "service": "", "scope": "explicit", "projects": [], "focus": ["overview"]}
    value.update(updates)
    return value


def scope(projects=None, capabilities=None, kind="projects"):
    return {"version": protocol.READ_VERSION, "kind": kind,
            "projects": ["project-alpha"] if projects is None else projects,
            "capabilities": ["http_health"] if capabilities is None else capabilities}


class LegacyCore:
    """Real legacy validator/builders behind the new additive mixin boundary."""
    def _fleet_planning_messages(self, question, catalog, previous, lang, error=""):
        return fleet.planner_messages(question, catalog, previous, lang)

    def _fleet_validate_planning(self, raw, catalog, previous):
        return fleet.validate_plan(llm.parse_llm_json(raw), catalog, previous)

    def _fleet_context_frame(self, question, plan, catalog, previous=None, update=None):
        return fleet_context.frame(question, plan, catalog, previous, update)

    def _fleet_inspect_operation(self, plan, catalog):
        return {"operation": "inspect", "hosts": plan["hosts"], "facets": plan["facets"], "inventory_hash": protocol.digest(catalog)}

    def _fleet_additional_operation(self, payload, checks, catalog):
        return {"operation": "inspect", "hosts": sorted({check["host"] for check in checks}),
                "facets": sorted({check["facet"] for check in checks}), "inventory_hash": payload["catalog_hash"]}


class Harness(fleet_project.ProjectReadMixin, LegacyCore):
    pass


class ProjectSemanticDispatch(unittest.TestCase):
    def setUp(self):
        self.catalog = inventory()
        self.agent = Harness()

    def validate(self, value, previous=None):
        return self.agent._fleet_validate_planning(protocol.canonical(value), self.catalog, previous)

    def test_project_question_derives_scoped_read_before_collecting_any_host_data(self):
        plan = self.validate(proposal(projects=["project-alpha"], focus=["services"]))
        self.assertEqual(plan["facets"], ["projects"])
        self.assertEqual(plan["projects"], ["project-alpha"])
        self.assertEqual(plan["read_scope"], scope(capabilities=["runtime"]))
        operation = self.agent._fleet_inspect_operation(plan, self.catalog)
        self.assertEqual(operation["read_scope"], plan["read_scope"])
        self.assertEqual(operation["facets"], ["projects"])
        self.assertNotIn("project-beta", protocol.canonical(operation))

    def test_multi_host_project_coverage_is_resolved_in_code_not_shrunk_by_model(self):
        plan = self.validate(proposal(hosts=["host-a"], projects=["project-mesh"], focus=["release"]))
        self.assertEqual(plan["hosts"], ["host-a", "host-b"])
        operation = self.agent._fleet_inspect_operation(plan, self.catalog)
        self.assertEqual(operation["hosts"], ["host-a", "host-b"])
        self.assertEqual(operation["read_scope"]["projects"], ["project-mesh"])

    def test_explicit_capabilities_are_closed_and_project_only(self):
        declared = scope(capabilities=["release", "http_health"])
        plan = self.validate(proposal(read_scope=declared, presentation="table", detail="detailed"))
        self.assertEqual(plan["read_scope"], declared)
        self.assertEqual(plan["presentation"], "table")
        self.assertEqual(plan["detail"], "detailed")
        self.assertEqual(plan["facets"], ["projects"])
        self.assertEqual(plan["read_contract_hash"], fleet_project.contract_hash())
        for capability in ("shell", "restart", "all_logs", "database_query"):
            with self.subTest(capability=capability), self.assertRaises(ValueError):
                self.validate(proposal(read_scope=scope(capabilities=[capability])))

    def test_unknown_project_version_fields_and_disagreeing_scope_fail_closed(self):
        for declared in (scope(projects=["missing"]), dict(scope(), version="unknown/v9"),
                         dict(scope(), path="/etc/private"), dict(scope(), approved=True)):
            with self.subTest(declared=declared), self.assertRaises(ValueError):
                self.validate(proposal(read_scope=declared))
        with self.assertRaises(ValueError):
            self.validate(proposal(projects=["project-beta"], read_scope=scope()))
        with self.assertRaises(ValueError):
            self.validate(proposal(projects=[{}], read_scope=scope()))

    def test_explicit_all_projects_and_host_infrastructure_are_separate(self):
        plan = self.validate(proposal(facets=["projects"], read_scope=scope(projects=[], capabilities=["release"], kind="all_projects")))
        self.assertEqual(set(plan["read_scope"]["projects"]), {"project-alpha", "project-beta", "project-mesh"})
        host = self.validate(proposal(focus=["memory"], read_scope=scope(projects=[], capabilities=[], kind="host")))
        self.assertEqual(host["read_scope"]["kind"], "host")
        self.assertNotIn("projects", host["facets"])
        operation = self.agent._fleet_inspect_operation(host, self.catalog)
        self.assertEqual(operation["read_scope"]["projects"], [])
        with self.assertRaises(ValueError):
            self.validate(proposal(read_scope=scope(projects=["project-alpha"], capabilities=["release"], kind="all_projects")))

    def test_legacy_host_and_all_diagnostics_keep_their_exact_plan_and_wire(self):
        for value in (proposal(), proposal(scope="all", facets=["resources", "services"], focus=["overview"])):
            expected = fleet.validate_plan(copy.deepcopy(value), self.catalog)
            actual = self.validate(value)
            with self.subTest(value=value):
                self.assertEqual(actual, expected)
                self.assertNotIn("read_scope", actual)
                self.assertEqual(self.agent._fleet_inspect_operation(actual, self.catalog), LegacyCore()._fleet_inspect_operation(actual, self.catalog))

    def test_new_narrow_host_semantics_omit_projects_even_without_optional_read_scope(self):
        for mode, hosts in (("explicit", ["host-a"]), ("all", ["host-a", "host-b"])):
            for focus in (["memory"], ["cpu"], ["disk"], ["services"], ["errors"], ["trend"], ["memory", "cpu"]):
                value = proposal(hosts=hosts, scope=mode, focus=focus)
                legacy = fleet.validate_plan(copy.deepcopy(value), self.catalog)
                self.assertIn("projects", legacy["facets"])
                actual = self.validate(value)
                self.assertEqual(actual["read_scope"], scope(projects=[], capabilities=[], kind="host"))
                self.assertEqual(actual["hosts"], legacy["hosts"])
                self.assertEqual(actual["focus"], focus)
                self.assertEqual(actual["facets"], [facet for facet in legacy["facets"] if facet != "projects"])
                operation = self.agent._fleet_inspect_operation(actual, self.catalog)
                self.assertNotIn("projects", operation["facets"])
                self.assertEqual(operation["read_scope"], actual["read_scope"])
                # Parsing stored legacy envelopes does not pass through the
                # new semantic planner boundary and retains its exact wire.
                self.assertEqual(LegacyCore()._fleet_inspect_operation(legacy, self.catalog)["facets"], legacy["facets"])

    def test_restart_card_plan_is_unchanged_and_cannot_acquire_a_scoped_read(self):
        value = proposal(operation="restart", service="example-worker.service", facets=["services"])
        self.assertEqual(self.validate(value), fleet.validate_plan(copy.deepcopy(value), self.catalog))
        with self.assertRaises(ValueError):
            self.validate(dict(value, read_scope=scope()))

    def test_optional_style_is_retained_without_weakening_legacy_fields(self):
        for key, allowed in fleet_project.STYLE.items():
            for item in allowed:
                plan = self.validate(proposal(**{key: item}))
                self.assertEqual(plan[key], item)
            for item in ([], "unbounded", True):
                with self.subTest(key=key, item=item), self.assertRaises(ValueError):
                    self.validate(proposal(**{key: item}))
        with self.assertRaises(ValueError):
            self.validate(proposal(answer="Invented health claim"))

    def test_owner_frame_retains_capability_hash_scope_and_requested_style(self):
        plan = self.validate(proposal(projects=["project-alpha"], focus=["release"], presentation="table", detail="detailed"))
        frame = self.agent._fleet_context_frame("Check Project Alpha release", plan, self.catalog, update=12)
        self.assertEqual(frame["read_scope"], plan["read_scope"])
        self.assertEqual(frame["read_contract_hash"], fleet_project.contract_hash())
        self.assertEqual(frame["presentation"], "table")
        self.assertEqual(frame["detail"], "detailed")
        self.assertEqual(frame["source_update"], 12)
        frame["read_scope"]["projects"].append("project-beta")
        self.assertEqual(plan["read_scope"]["projects"], ["project-alpha"])

    def test_canonical_inherit_cannot_drop_project_ids_and_broaden_to_host(self):
        original = self.validate(proposal(projects=["project-alpha"], focus=["backups"]))
        previous = self.agent._fleet_context_frame("Check Project Alpha backups", original, self.catalog, update=12)
        plan = self.validate(proposal(hosts=[], projects=[], scope="inherit", focus=["overview"], detail="concise"), previous)
        self.assertEqual(plan["projects"], ["project-alpha"])
        self.assertEqual(plan["read_scope"]["capabilities"], ["backups"])
        self.assertEqual(plan["facets"], ["projects"])
        self.assertEqual(self.agent._fleet_inspect_operation(plan, self.catalog)["read_scope"], plan["read_scope"])

    def test_new_focus_or_new_named_project_changes_only_requested_scope(self):
        original = self.validate(proposal(projects=["project-alpha"], focus=["release"]))
        previous = self.agent._fleet_context_frame("Check Project Alpha release", original, self.catalog, update=12)
        changed = self.validate(proposal(hosts=[], projects=[], scope="inherit", focus=["backups"]), previous)
        self.assertEqual(changed["read_scope"]["capabilities"], ["backups"])
        named = self.validate(proposal(hosts=[], projects=["project-mesh"], scope="inherit", focus=["release"]), previous)
        self.assertEqual(named["projects"], ["project-mesh"])
        self.assertEqual(named["hosts"], ["host-a", "host-b"])

    def test_changed_capability_contract_rejects_inherited_and_pending_submission(self):
        plan = self.validate(proposal(projects=["project-alpha"]))
        previous = self.agent._fleet_context_frame("Check Project Alpha", plan, self.catalog, update=12)
        plan["read_contract_hash"] = "0" * 64
        with self.assertRaises(ValueError):
            self.agent._fleet_inspect_operation(plan, self.catalog)
        previous["read_contract_hash"] = "0" * 64
        with self.assertRaises(ValueError):
            self.validate(proposal(hosts=[], projects=[], scope="inherit"), previous)

    def test_additional_read_preserves_capabilities_and_cannot_expand_colocated_scope(self):
        plan = self.validate(proposal(projects=["project-mesh"], focus=["release"]))
        payload = {"plan": plan, "catalog_hash": protocol.digest(self.catalog)}
        operation = self.agent._fleet_additional_operation(payload, [{"host": "host-b", "facet": "projects"}], self.catalog)
        self.assertEqual(operation["hosts"], ["host-b"])
        self.assertEqual(operation["read_scope"]["projects"], ["project-mesh"])
        self.assertEqual(operation["read_scope"]["capabilities"], ["release"])
        self.assertNotIn("project-beta", protocol.canonical(operation))
        for check in ({"host": "host-a", "facet": "resources"}, {"host": "missing", "facet": "projects"}):
            with self.subTest(check=check), self.assertRaises(ValueError):
                self.agent._fleet_additional_operation(payload, [check], self.catalog)

    def test_old_gateway_returns_project_capability_gap_without_legacy_retry(self):
        plan = self.validate(proposal(projects=["project-alpha"], focus=["release"]))
        operation = self.agent._fleet_inspect_operation(plan, self.catalog)
        calls = []
        def old_gateway(_transport, host, request):
            calls.append((host, request))
            raise ValueError("old gateway does not support scoped capability discovery")
        with mock.patch.object(cara_fleet, "remote", side_effect=old_gateway):
            result = cara_fleet.inspect(self.catalog, {}, operation)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1]["operation"], "capabilities")
        state = result["hosts"]["host-a"]["projects"]["project-alpha"]["release"]
        self.assertEqual(state["reason"], "gateway_contract_unavailable")
        self.assertNotIn("project-beta", protocol.canonical(result))

    def test_selected_project_dispatch_reaches_only_its_fixed_gateway_resources(self):
        plan = self.validate(proposal(projects=["project-alpha"], focus=["release"]))
        operation = self.agent._fleet_inspect_operation(plan, self.catalog)
        host_config = {"host": "host-a", "services": [],
                       "projects": {"project-alpha": ["/example/alpha.json"], "project-beta": ["/example/beta.json"]}}
        reads = []
        def read(path, limit=65536):
            reads.append(path)
            if path != "/example/alpha.json":
                raise AssertionError("unrelated colocated project was collected")
            return json.dumps({"build_version": "synthetic-alpha-build", "status": "verified"})
        selected = protocol.scope_for_host(operation["read_scope"], self.catalog, "host-a")
        with mock.patch.object(fleet_host, "read", side_effect=read), mock.patch.object(fleet_host, "resources", side_effect=AssertionError("host scan forbidden")), mock.patch.object(fleet_host, "unit_state", side_effect=AssertionError("unrelated unit scan forbidden")):
            result = fleet_host.run(host_config, {"operation": "inspect", "facets": operation["facets"], "read_scope": selected})
        self.assertEqual(reads, ["/example/alpha.json"])
        self.assertEqual(set(result["projects"]), {"project-alpha"})
        self.assertNotIn("project-beta", protocol.canonical(result))

    def test_prompt_exposes_only_existing_capability_names_and_no_extra_model_stage(self):
        messages = self.agent._fleet_planning_messages("Check Project Alpha", self.catalog, {}, "en")
        system = messages[0]["content"]
        self.assertIn(protocol.READ_VERSION, system)
        self.assertIn("Optional presentation", system)
        self.assertIn("confirmed preference", system)
        for capability in protocol.PROJECT_CAPABILITIES:
            self.assertIn(capability, system)
        self.assertEqual(len(messages), 2)


if __name__ == "__main__":
    unittest.main()
