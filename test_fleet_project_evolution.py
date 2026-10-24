"""Append-only project sources retain qualified old answers and useful new dates."""
import copy
import unittest
from unittest import mock

import cara_fleet
import fleet_answers
import fleet_host
import fleet_project
import fleet_project_adapters as adapters
import fleet_protocol as protocol
import fleet_report
import test_fleet_project_adapters as fixture
import test_fleet_project_scope as scope_fixture


class ProjectEvidenceEvolution(unittest.TestCase):
    def test_actions_source_survives_selected_gateway_report_and_answer(self):
        inventory = scope_fixture.inventory()
        request = scope_fixture.operation(("development",))
        policy = scope_fixture.config()
        policy["project_adapters"] = {"project-alpha": {"development": fixture.config("development")}}
        policy["project_adapters"]["project-alpha"]["development"]["adapter"] = adapters.ADAPTERS["development_actions"]["implementation_version"]
        document = fixture.actions_development_document()
        with mock.patch.object(protocol, "now", return_value=fixture.NOW), mock.patch.object(protocol, "age", return_value=0), \
                mock.patch.object(adapters, "_read_fixed_json", return_value=document) as reader, \
                mock.patch.object(cara_fleet, "remote", side_effect=lambda _transport, _host, operation: fleet_host.run(policy, operation)), \
                mock.patch.object(fleet_host, "resources", side_effect=AssertionError("unselected resources")), \
                mock.patch.object(fleet_host, "command", side_effect=AssertionError("unselected command")):
            value = cara_fleet.inspect(inventory, {}, request)
            protocol.result(value, request["hosts"], read_scope=request["read_scope"])
        reader.assert_called_once_with(policy["project_adapters"]["project-alpha"]["development"]["path"])
        observation = value["hosts"]["host-a"]["projects"]["project-alpha"]["development"]
        self.assertEqual(observation["implementation_version"], "project-development-actions/v1")
        self.assertEqual(observation["ci"]["items"][0]["updated_at"], "2026-10-24T11:45:00Z")
        self.assertNotIn("completed_at", observation["ci"]["items"][0])
        plan = dict(request, focus=["progress"], presentation="table")
        report = fleet_report.build(value, inventory, plan)
        answer = "\n".join(fleet_answers.render(report, None, plan))
        for phrase in ("Project Alpha", "source updated 2026-10-24 11:45:00 UTC", "CI", "partial coverage", fixture.REPO):
            self.assertIn(phrase, answer)
        self.assertNotIn("Project Beta", answer)
        self.assertNotIn("development complete", answer.lower())
        self.assertNotIn("completed at", answer.lower())

    def test_exact_historical_read_contracts_stay_compatible_not_arbitrary_hashes(self):
        for digest in ("13dbb6e9ff2ae7734878d5e306f887424b9484518119b10f00e92dad544df4ce", "987718d875c39d4e90bb2cfebc383ec921a20f3926b7a5ee468a456d85789d94"):
            self.assertTrue(protocol.read_contract_compatible(digest))
            previous = dict(read_contract_hash=digest, read_scope=scope_fixture.operation()["read_scope"], projects=["project-alpha"], focus=["projects"])
            inherited = fleet_project._inherited_scope(dict(scope="inherit", projects=["project-alpha"]), previous, ["projects"])
            self.assertEqual(inherited, previous["read_scope"])
        for digest in (None, [], {}, "a" * 64, "project-read/v1"):
            self.assertFalse(protocol.read_contract_compatible(digest))
        previous["read_contract_hash"] = "b" * 64
        with self.assertRaises(ValueError):
            fleet_project._inherited_scope(dict(scope="inherit", projects=["project-alpha"]), previous, ["projects"])

    def test_new_source_version_cannot_be_used_for_other_capability(self):
        version = adapters.ADAPTERS["development_actions"]["implementation_version"]
        for capability in ("release", "http_health", "backups", "containers", "runtime"):
            self.assertIsNone(protocol.adapter_kind(capability, version))
            with self.assertRaises(ValueError):
                protocol.capability_implementation(capability, version)
        contract = protocol.capability_contract()
        self.assertIn(version, contract["development"]["supported_implementations"])
        contract["development"]["supported_implementations"][version].append("private_data")
        self.assertNotIn("private_data", protocol.capability_contract()["development"]["supported_implementations"][version])


if __name__ == "__main__":
    unittest.main()
