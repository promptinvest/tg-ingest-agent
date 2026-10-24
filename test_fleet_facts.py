"""CF-01 provenance/privacy and last-good publication behavior, using synthetic data."""
import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import fleet_facts as facts


FIXTURE = Path(__file__).parent / "docs/backlog/cara-fleet-2026-10-24/bootstrap-facts.json"


def fixture():
    """Explicit test-only projection of the unchanged proposed example."""
    proposed = json.loads(FIXTURE.read_text(encoding="utf-8-sig"))
    value = copy.deepcopy(proposed)
    value["schema"] = facts.SCHEMA
    for source, original in zip(value["sources"], proposed["sources"]):
        source.clear()
        source.update({"id": original["id"], "kind": "synthetic",
                       "status": "historical" if original["id"].endswith("old") else "accepted",
                       "recorded_at": original["recorded_at"], "checked_at": "2026-10-24T00:00:00Z",
                       "sha256": hashlib.sha256(facts.canonical(original).encode()).hexdigest()})
    for subject in value["subjects"]:
        subject.update(source_ids=["fixture-source-current"], evidence_at="2026-10-20")
    for field in ("facts", "relations", "completeness"):
        for claim in value[field]:
            claim.setdefault("supersedes", [])
            claim.setdefault("conflicts", [])
    for claim in value["completeness"]:
        claim["coverage"] = ["project-alpha", "project-beta"] if claim["status"] == "complete" else []
    return facts.seal(value)


def change(value, revision=2):
    value = copy.deepcopy(value)
    value["revision"] = revision
    value["generated_at"] = "2026-10-24T01:00:00Z"
    return facts.seal(value)


def add_fact(value, identity="new-provider", predicate="provider", content="Second Cloud", subject="host-a", **extra):
    claim = {"fact_id": identity, "subject_id": subject, "predicate": predicate,
             "value": content, "status": "recorded", "evidence_at": "2026-10-23",
             "source_ids": ["fixture-source-current"], "supersedes": [], "conflicts": []}
    claim.update(extra)
    value["facts"].append(claim)
    return claim


class FactsContract(unittest.TestCase):
    def test_original_proposed_fixture_is_preserved_and_not_an_installable_artifact(self):
        original = json.loads(FIXTURE.read_text(encoding="utf-8-sig"))
        self.assertEqual(original["schema"], "cara-fleet-facts/proposed-v1")
        self.assertNotIn("integrity", original)
        with self.assertRaises(ValueError):
            facts.validate(original, allow_fixture=True)
        with self.assertRaises(ValueError):
            facts.validate(fixture())
        value = fixture()
        self.assertEqual(facts.validate(value, allow_fixture=True), value)

    def test_known_address_provider_placement_are_local_without_network_or_executor(self):
        value = fixture()
        with mock.patch("socket.create_connection", side_effect=AssertionError("host probe forbidden")):
            self.assertEqual(facts.lookup(value, "host-a", "provider")["value"], "ExampleCloud")
            self.assertEqual(facts.lookup(value, "host-a", "public_ipv4")["value"], "192.0.2.10")
            placed = facts.relations(value, predicate="hosted_on", object_id="host-a")
            self.assertEqual({claim["subject_id"] for claim in placed}, {"project-alpha", "project-beta"})

    def test_meaningful_name_slash_separators_survive_validation_and_local_lookup(self):
        value = fixture()
        value["subjects"][0]["label"] = "Example / Project Alpha"
        value["subjects"][1]["label"] = "Example monitor / standby host"
        value = facts.seal(value)
        labels = {subject["id"]: subject["label"] for subject in facts.subjects(value, "host")}
        self.assertEqual(labels["host-a"], "Example / Project Alpha")
        self.assertEqual(labels["host-b"], "Example monitor / standby host")
        for path in ("/etc/private/key", "Example /etc/private/key", ".ssh/id_rsa", ".env.production"):
            unsafe = fixture()
            unsafe["subjects"][0]["label"] = path
            with self.subTest(path=path), self.assertRaises(ValueError):
                facts.seal(unsafe)

    def test_lifecycle_filter_excludes_retired_and_unverified_without_erasing_history(self):
        value = fixture()
        self.assertEqual([subject["id"] for subject in facts.subjects(value, "host")], ["host-a", "host-b", "host-c"])
        all_ids = {subject["id"] for subject in facts.subjects(value, "host", include_inactive=True)}
        self.assertTrue({"host-old", "host-unverified"} <= all_ids)
        self.assertEqual(facts.lookup(value, "host-old", "lifecycle")["value"], "retired")

    def test_public_and_outbound_addresses_keep_distinct_dates_and_meanings(self):
        value = fixture()
        public = facts.lookup(value, "host-c", "public_ipv4")
        outbound = facts.lookup(value, "host-c", "outbound_ipv4")
        self.assertEqual(public["value"], "203.0.113.30")
        self.assertEqual(outbound["value"], "203.0.113.31")
        self.assertEqual(outbound["claims"][0]["evidence_at"], "2026-06-01")
        self.assertEqual(public["claims"][0]["evidence_at"], "2026-10-20")

    def test_unknown_values_and_absent_dates_are_not_inferred(self):
        value = fixture()
        unknown = facts.lookup(value, "host-c", "provider")
        self.assertEqual(unknown["status"], "unknown")
        self.assertIsNone(unknown["value"])
        self.assertIsNone(unknown["claims"][0]["evidence_at"])
        self.assertEqual(facts.lookup(value, "host-c", "role"), {"status": "unknown", "value": None, "claims": []})

    def test_conflicting_scalar_claims_survive_even_without_explicit_link(self):
        value = fixture()
        add_fact(value)
        value = facts.seal(value)
        answer = facts.lookup(value, "host-a", "provider")
        self.assertEqual(answer["status"], "conflict")
        self.assertIsNone(answer["value"])
        self.assertEqual({claim["value"] for claim in answer["claims"]}, {"ExampleCloud", "Second Cloud"})

    def test_newer_date_does_not_resolve_conflict_but_explicit_supersession_does(self):
        value = fixture()
        new = add_fact(value)
        self.assertEqual(facts.lookup(value, "host-a", "provider")["status"], "conflict")
        new["supersedes"] = ["fact-2"]
        value = facts.seal(value)
        self.assertEqual(facts.lookup(value, "host-a", "provider")["value"], "Second Cloud")
        self.assertTrue(any(claim["fact_id"] == "fact-2" for claim in value["facts"]))
        self.assertEqual(facts.lookup(value, "host-a", "region")["value"], "Test region A")

    def test_explicit_conflict_is_reciprocal_and_preserved(self):
        value = fixture()
        add_fact(value, conflicts=["fact-2"])
        with self.assertRaises(ValueError):
            facts.seal(value)
        value["facts"][1]["conflicts"] = ["new-provider"]
        value = facts.seal(value)
        self.assertEqual(facts.lookup(value, "host-a", "provider")["status"], "conflict")

    def test_invalid_relations_and_cross_property_supersession_rejected(self):
        for update in ({"object_id": "missing"}, {"object_id": "project-beta"}, {"subject_id": "host-b"}, {"predicate": "restart"}):
            value = fixture()
            value["relations"][0].update(update)
            with self.subTest(update=update), self.assertRaises(ValueError):
                facts.seal(value)
        value = fixture()
        add_fact(value, supersedes=["fact-3"])
        with self.assertRaises(ValueError):
            facts.seal(value)

    def test_cycle_and_self_supersession_rejected(self):
        value = fixture()
        newer = add_fact(value, supersedes=["fact-2"])
        value["facts"][1]["supersedes"] = [newer["fact_id"]]
        with self.assertRaises(ValueError):
            facts.seal(value)
        value = fixture()
        value["facts"][1]["supersedes"] = ["fact-2"]
        with self.assertRaises(ValueError):
            facts.seal(value)

    def test_multihost_placement_remains_multihost_and_unknown_relation_is_explicit(self):
        value = fixture()
        self.assertEqual({claim["object_id"] for claim in facts.relations(value, "project-mesh", "hosted_on")}, {"host-b", "host-c"})
        value["relations"].append({"relation_id": "unknown-dependency", "subject_id": "project-alpha",
            "predicate": "depends_on", "object_id": None, "status": "unknown", "evidence_at": None,
            "source_ids": ["fixture-source-current"], "supersedes": [], "conflicts": []})
        value = facts.seal(value)
        self.assertIsNone(facts.relations(value, "project-alpha", "depends_on")[0]["object_id"])

    def test_completeness_defaults_unknown_and_cannot_be_inferred_from_registry(self):
        value = fixture()
        self.assertEqual(facts.completeness(value)["status"], "unknown")
        self.assertEqual(facts.completeness(value, "host-a", "hosted_projects")["status"], "complete")
        self.assertEqual(facts.completeness(value, "host-b", "hosted_projects")["status"], "unknown")
        value["completeness"][0]["evidence_at"] = None
        with self.assertRaises(ValueError):
            facts.seal(value)

    def test_conflicting_completeness_does_not_authorize_only_or_all(self):
        value = fixture()
        claim = copy.deepcopy(value["completeness"][0])
        claim.update(claim_id="completeness-new", status="partial", coverage=[])
        value["completeness"].append(claim)
        value = facts.seal(value)
        self.assertEqual(facts.completeness(value, "host-a", "hosted_projects")["status"], "conflict")

    def test_exact_schema_never_accepts_transport_authority(self):
        for section in (None, "subjects", "sources", "facts", "relations", "completeness"):
            for key in ("address", "command", "key", "shell", "path", "environment", "chat_id"):
                value = fixture()
                target = value if section is None else value[section][0]
                target[key] = "forbidden"
                with self.subTest(section=section, key=key), self.assertRaises(ValueError):
                    facts.seal(value)

    def test_secret_canaries_paths_links_ids_and_commands_are_rejected(self):
        canaries = ("SECRET_CANARY_123", "api_key=canary", "Bearer: canary", "password=hidden",
                    "https://private.example.test/secret", "/etc/cara/credential", r"C:\Users\owner\secret",
                    "root@example.test", "sudo systemctl restart service", "chat_id=12345678",
                    "ghp_abcdefghijklmnopqrst", "12345678:ABCDEFGHIJKLMNOPQRST", "$(whoami)", "alpha; id",
                    ".ssh/id_rsa", ".env.production", "credentials/config.json", "person@example.test", "123456789")
        for canary in canaries:
            value = fixture()
            add_fact(value, predicate="purpose", content=canary, subject="project-alpha")
            with self.subTest(canary=canary), self.assertRaises(ValueError):
                facts.seal(value)
        value = fixture()
        value["subjects"][0]["id"] = "telegram-123456789"
        with self.assertRaises(ValueError):
            facts.seal(value)

    def test_scalar_types_revision_and_metadata_are_strict(self):
        for key, wrongs in (("revision", (True, 0, -1, 1.0, "1", 2**53)), ("fixture_only", (1, "true", None))):
            for wrong in wrongs:
                value = fixture()
                value[key] = wrong
                with self.subTest(key=key, wrong=wrong), self.assertRaises(ValueError):
                    facts.seal(value)
        for field, key in (("sources", "kind"), ("sources", "status"), ("subjects", "kind"), ("facts", "status"), ("facts", "predicate")):
            value = fixture()
            value[field][0][key] = []
            with self.subTest(field=field, key=key), self.assertRaises(ValueError):
                facts.seal(value)

    def test_missing_provenance_and_source_digest_are_rejected(self):
        for field, key in (("subjects", "evidence_at"), ("facts", "source_ids"), ("relations", "evidence_at"), ("completeness", "source_ids"), ("sources", "sha256")):
            value = fixture()
            del value[field][0][key]
            with self.subTest(field=field, key=key), self.assertRaises(ValueError):
                facts.seal(value)
        value = fixture()
        value["sources"][0]["sha256"] = "not-a-digest"
        with self.assertRaises(ValueError):
            facts.seal(value)

    def test_no_evidence_date_is_filled_with_source_check_or_generation_date(self):
        value = fixture()
        value["sources"][0]["checked_at"] = "2026-10-24T12:00:00Z"
        value["generated_at"] = "2026-10-24T13:00:00Z"
        value = facts.seal(value)
        self.assertIsNone(facts.lookup(value, "host-c", "provider")["claims"][0]["evidence_at"])
        self.assertEqual(facts.lookup(value, "host-c", "outbound_ipv4")["claims"][0]["evidence_at"], "2026-06-01")

    def test_integrity_is_content_bound_and_results_are_independent_copies(self):
        value = fixture()
        value["facts"][1]["value"] = "Tampered"
        with self.assertRaises(ValueError):
            facts.validate(value, allow_fixture=True)
        value = fixture()
        result = facts.lookup(value, "host-a", "provider")
        result["claims"][0]["value"] = "Mutated"
        self.assertEqual(facts.lookup(value, "host-a", "provider")["value"], "ExampleCloud")

    def test_repository_is_identity_not_a_command_or_private_link(self):
        value = fixture()
        add_fact(value, predicate="repository", content="example/project-alpha", subject="project-alpha")
        value = facts.seal(value)
        self.assertEqual(facts.lookup(value, "project-alpha", "repository")["value"], "example/project-alpha")
        for repository in ("https://example.test/repo", "git@example.test:repo", "../secret", "example/project?token=abc"):
            value = fixture()
            add_fact(value, predicate="repository", content=repository, subject="project-alpha")
            with self.subTest(repository=repository), self.assertRaises(ValueError):
                facts.seal(value)

    def test_public_service_address_has_no_credentials_private_path_or_query(self):
        value = fixture()
        add_fact(value, predicate="public_url", content="https://service.example.test", subject="project-alpha")
        facts.seal(value)
        for url in ("https://user:pass@service.example.test", "https://service.example.test/private", "https://service.example.test?key=abc", "https://127.0.0.1", "https://service.internal", "ssh://service.example.test", "https://service.example.test:22"):
            value = fixture()
            add_fact(value, predicate="public_url", content=url, subject="project-alpha")
            with self.subTest(url=url), self.assertRaises(ValueError):
                facts.seal(value)

    def test_nonpublic_ip_and_wrong_address_type_are_rejected(self):
        for ip in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "not-an-ip", "2001:db8::1"):
            value = fixture()
            value["facts"][0]["value"] = ip
            with self.subTest(ip=ip), self.assertRaises(ValueError):
                facts.seal(value)


class FactsPrecedence(unittest.TestCase):
    def test_historical_or_unverified_sources_cannot_supersede_accepted_current_facts(self):
        for status in ("historical", "unverified"):
            value = fixture()
            value["sources"].append({"id": "later-low-authority", "kind": "operational-record", "status": status,
                "recorded_at": "2026-10-23", "checked_at": "2026-10-24T00:00:00Z", "sha256": "b" * 64})
            add_fact(value, supersedes=["fact-2"], source_ids=["later-low-authority"])
            with self.subTest(status=status), self.assertRaises(ValueError):
                facts.seal(value)

    def test_old_july_record_cannot_supersede_october_fact_even_if_recently_checked(self):
        value = fixture()
        add_fact(value, supersedes=["fact-2"], evidence_at="2026-07-01")
        with self.assertRaises(ValueError):
            facts.seal(value)
        value = fixture()
        add_fact(value, supersedes=["fact-2"], evidence_at=None)
        with self.assertRaises(ValueError):
            facts.seal(value)

    def test_current_owner_decision_cannot_be_overridden_by_operational_or_derived_claim(self):
        for kind in ("operational-record", "derived-register"):
            value = fixture()
            value["sources"][0]["kind"] = "owner-decision"
            value["sources"].append({"id": "later-source", "kind": kind, "status": "accepted",
                "recorded_at": "2026-10-23", "checked_at": "2026-10-24T00:00:00Z", "sha256": "b" * 64})
            add_fact(value, supersedes=["fact-2"], source_ids=["later-source"])
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                facts.seal(value)

    def test_derived_dashboard_cannot_override_an_accepted_operational_record(self):
        value = fixture()
        value["sources"].append({"id": "fresh-dashboard", "kind": "derived-register", "status": "accepted",
            "recorded_at": "2026-10-23", "checked_at": "2026-10-24T00:00:00Z", "sha256": "b" * 64})
        add_fact(value, supersedes=["fact-2"], source_ids=["fresh-dashboard"])
        with self.assertRaises(ValueError):
            facts.seal(value)

    def test_completeness_coverage_kind_is_bound_to_scope(self):
        for predicate, subject, coverage in (("hosts", None, ["project-alpha"]),
                ("projects", None, ["host-a"]), ("hosted_projects", "host-a", ["host-b"]),
                ("project_hosts", "project-alpha", ["project-beta"])):
            value = fixture()
            value["completeness"][0].update(scope={"subject_id": subject, "predicate": predicate}, coverage=coverage)
            with self.subTest(predicate=predicate), self.assertRaises(ValueError):
                facts.seal(value)

    def test_incomplete_coverage_cannot_make_only_or_all_claims(self):
        value = fixture()
        value["completeness"][0]["coverage"] = ["project-alpha"]
        value = facts.seal(value)
        result = facts.completeness(value, "host-a", "hosted_projects")
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(result["coverage"], [])
        self.assertEqual(result["claims"][0]["coverage"], ["project-alpha"])
        value = fixture()
        value["completeness"][0].update(scope={"subject_id": None, "predicate": "hosts"}, coverage=["host-a"])
        value = facts.seal(value)
        self.assertEqual(facts.completeness(value)["status"], "conflict")

    def test_unverified_claim_cannot_become_a_known_scalar_or_active_catalog(self):
        value = fixture()
        value["sources"][0]["status"] = "unverified"
        value = facts.seal(value)
        self.assertEqual(facts.lookup(value, "host-a", "provider")["status"], "unknown")
        self.assertIsNone(facts.lookup(value, "host-a", "provider")["value"])
        self.assertEqual(facts.subjects(value, "host"), [])
        self.assertEqual(facts.completeness(value, "host-a", "hosted_projects")["status"], "conflict")

    def test_unknown_unplaced_project_prevents_only_claim_even_with_reduced_coverage(self):
        value = fixture()
        value["relations"][0].update(status="unknown", object_id=None)
        value["completeness"][0]["coverage"] = ["project-beta"]
        value = facts.seal(value)
        self.assertEqual(facts.completeness(value, "host-a", "hosted_projects")["status"], "conflict")

    def test_undisputed_matching_coverage_can_authorize_its_exact_scope(self):
        value = fixture()
        value["completeness"][0].update(scope={"subject_id": None, "predicate": "hosts"}, coverage=["host-a", "host-b", "host-c"])
        value = facts.seal(value)
        self.assertEqual(facts.completeness(value)["status"], "complete")
        self.assertEqual(facts.completeness(value, predicate="projects")["status"], "unknown")

    def test_unknown_project_placement_prevents_complete_host_catalog_claim(self):
        value = fixture()
        value["relations"][0].update(status="unknown", object_id=None)
        value = facts.seal(value)
        self.assertEqual(facts.completeness(value, "host-a", "hosted_projects")["status"], "conflict")


class FactsPublication(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cara-facts-test-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "facts"
        self.value = fixture()

    def publish(self, value=None, **kwargs):
        return facts.publish(value or self.value, self.directory, authenticated=True, allow_fixture=True, **kwargs)

    def test_authenticated_atomic_install_has_separate_installation_receipt(self):
        receipt = self.publish()
        self.assertEqual(facts.load(self.directory / "current.json", allow_fixture=True), self.value)
        self.assertEqual(receipt["generated_at"], self.value["generated_at"])
        self.assertNotEqual(receipt["installed_at"], self.value["generated_at"])
        status = facts.publication_status(self.directory / "current.json", allow_fixture=True)
        self.assertEqual(status["installed_at"], receipt["installed_at"])
        self.assertEqual(status["source_checks"]["fixture-source-current"], "2026-10-24T00:00:00Z")

    def test_authentication_failure_and_fixture_default_rejection_leave_last_good(self):
        self.publish()
        before = (self.directory / "current.json").read_bytes()
        for authentication in (False, None, 1, "yes"):
            with self.subTest(authentication=authentication), self.assertRaises(ValueError):
                facts.publish(change(self.value), self.directory, authenticated=authentication, allow_fixture=True)
        with self.assertRaises(ValueError):
            facts.publish(change(self.value), self.directory, authenticated=True)
        self.assertEqual((self.directory / "current.json").read_bytes(), before)

    def test_same_revision_identical_retry_returns_original_receipt(self):
        first = self.publish()
        with mock.patch.object(facts.os, "replace", side_effect=AssertionError("retry must not reinstall")):
            self.assertEqual(self.publish(), first)
        self.assertEqual(len(list((self.directory / "revisions").glob("*.json"))), 1)

    def test_rollback_and_conflicting_same_revision_are_refused(self):
        self.publish(change(self.value, 3))
        before = (self.directory / "current.json").read_bytes()
        for candidate in (self.value, change(self.value, 2), change(self.value, 3)):
            if candidate["revision"] == 3:
                candidate["generated_at"] = "2026-10-24T02:00:00Z"
                candidate = facts.seal(candidate)
            with self.subTest(revision=candidate["revision"]), self.assertRaises(ValueError):
                self.publish(candidate)
        self.assertEqual((self.directory / "current.json").read_bytes(), before)

    def test_forward_revision_retains_artifacts_and_true_prior_receipt(self):
        first = self.publish()
        second = self.publish(change(self.value))
        self.assertEqual(second["previous_revision"], 1)
        self.assertEqual(len(list((self.directory / "revisions").glob("*.json"))), 2)
        archived = json.loads(next((self.directory / "receipts").glob("*.json")).read_text())
        self.assertEqual(archived["receipt"], first)
        self.assertEqual(facts.load(self.directory / "current.json", allow_fixture=True)["revision"], 2)

    def test_precommit_partial_failure_preserves_last_good_and_candidate_evidence(self):
        self.publish()
        before = (self.directory / "current.json").read_bytes()
        with mock.patch.object(facts.os, "replace", side_effect=OSError("simulated atomic install failure")):
            with self.assertRaises(OSError):
                self.publish(change(self.value))
        self.assertEqual((self.directory / "current.json").read_bytes(), before)
        self.assertEqual(facts.load(self.directory / "current.json", allow_fixture=True), self.value)
        self.assertEqual(len(list((self.directory / "revisions").glob("*.json"))), 2)
        self.assertEqual(len(list(self.directory.glob(".pending-*.json"))), 1)
        self.assertFalse((self.directory / ".publish-lock").exists())

    def test_no_mutation_after_invalid_schema_even_when_authenticated(self):
        self.publish()
        before = (self.directory / "current.json").read_bytes()
        candidate = change(self.value)
        candidate["facts"][0]["command"] = "forbidden"
        with self.assertRaises(ValueError):
            self.publish(candidate)
        self.assertEqual((self.directory / "current.json").read_bytes(), before)

    def test_history_and_tombstones_cannot_be_deleted_or_rewritten(self):
        self.publish()
        for field in ("facts", "relations", "completeness", "sources", "subjects"):
            candidate = copy.deepcopy(self.value)
            if field == "sources":
                candidate[field][0]["checked_at"] = "2026-10-24T03:00:00Z"
            elif field == "subjects":
                candidate[field] = [subject for subject in candidate[field] if subject["id"] != "host-old"]
                candidate["facts"] = [claim for claim in candidate["facts"] if claim["subject_id"] != "host-old"]
            else:
                candidate[field].pop()
            candidate["revision"] = 2
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.publish(facts.seal(candidate))
        self.assertEqual(facts.load(self.directory / "current.json", allow_fixture=True), self.value)

    def test_old_topology_cannot_reactivate_excluded_target(self):
        self.publish()
        candidate = change(self.value)
        add_fact(candidate, "reactivate", "lifecycle", "active", "host-old", supersedes=["fact-16"])
        with self.assertRaises(ValueError):
            self.publish(facts.seal(candidate))
        self.assertEqual(facts.lookup(facts.load(self.directory / "current.json", allow_fixture=True), "host-old", "lifecycle")["value"], "retired")

    def test_explicit_owner_lifecycle_evidence_can_publish_forward_without_execution(self):
        self.publish()
        candidate = change(self.value)
        candidate["sources"].append({"id": "owner-lifecycle-decision", "kind": "owner-decision", "status": "accepted",
            "recorded_at": "2026-10-23", "checked_at": "2026-10-24T00:00:00Z", "sha256": "a" * 64})
        add_fact(candidate, "reactivate", "lifecycle", "active", "host-old", supersedes=["fact-16"], source_ids=["owner-lifecycle-decision"])
        self.publish(facts.seal(candidate))
        current = facts.load(self.directory / "current.json", allow_fixture=True)
        self.assertEqual(facts.lookup(current, "host-old", "lifecycle")["value"], "active")
        self.assertNotIn("restart_services", facts.canonical(current))
        self.assertNotIn("command", facts.canonical(current))

    def test_retired_unknown_active_sequence_keeps_owner_reactivation_requirement(self):
        self.publish()
        uncertain = change(self.value)
        add_fact(uncertain, "uncertain-lifecycle", "lifecycle", None, "host-old", status="unknown", supersedes=["fact-16"])
        uncertain = facts.seal(uncertain)
        self.publish(uncertain)
        self.assertEqual(facts.lookup(uncertain, "host-old", "lifecycle")["status"], "unknown")
        active = change(uncertain, 3)
        add_fact(active, "ordinary-reactivation", "lifecycle", "active", "host-old", evidence_at="2026-10-24", supersedes=["uncertain-lifecycle"])
        with self.assertRaises(ValueError):
            self.publish(facts.seal(active))
        self.assertEqual(facts.load(self.directory / "current.json", allow_fixture=True)["revision"], 2)

    def test_retired_conflict_active_sequence_keeps_owner_reactivation_requirement(self):
        self.publish()
        conflict = change(self.value)
        add_fact(conflict, "conflicting-lifecycle", "lifecycle", "active", "host-old")
        conflict = facts.seal(conflict)
        self.publish(conflict)
        self.assertEqual(facts.lookup(conflict, "host-old", "lifecycle")["status"], "conflict")
        active = change(conflict, 3)
        add_fact(active, "ordinary-reactivation", "lifecycle", "active", "host-old", evidence_at="2026-10-24", supersedes=["fact-16", "conflicting-lifecycle"])
        with self.assertRaises(ValueError):
            self.publish(facts.seal(active))
        self.assertEqual(facts.load(self.directory / "current.json", allow_fixture=True)["revision"], 2)

    def test_owner_reactivation_can_bind_through_retained_uncertain_history(self):
        self.publish()
        uncertain = change(self.value)
        add_fact(uncertain, "uncertain-lifecycle", "lifecycle", None, "host-old", status="unknown", supersedes=["fact-16"])
        uncertain = facts.seal(uncertain)
        self.publish(uncertain)
        active = change(uncertain, 3)
        active["sources"].append({"id": "owner-reactivation", "kind": "owner-decision", "status": "accepted",
            "recorded_at": "2026-10-24", "checked_at": "2026-10-24T01:00:00Z", "sha256": "c" * 64})
        add_fact(active, "owner-active", "lifecycle", "active", "host-old", evidence_at="2026-10-24", supersedes=["uncertain-lifecycle"], source_ids=["owner-reactivation"])
        self.publish(facts.seal(active))
        self.assertEqual(facts.lookup(facts.load(self.directory / "current.json", allow_fixture=True), "host-old", "lifecycle")["value"], "active")

    def test_pointed_artifact_must_be_an_object(self):
        self.publish()
        pointer = json.loads((self.directory / "current.json").read_text())
        artifact = self.directory / "revisions" / pointer["artifact"]
        artifact.write_text("[]")
        with self.assertRaises(ValueError):
            facts.load(self.directory / "current.json", allow_fixture=True)

    def test_parallel_publisher_rejected_without_changing_current(self):
        self.publish()
        before = (self.directory / "current.json").read_bytes()
        (self.directory / ".publish-lock").mkdir()
        with self.assertRaises(ValueError):
            self.publish(change(self.value))
        self.assertEqual((self.directory / "current.json").read_bytes(), before)

    def test_duplicate_json_keys_and_path_escape_pointer_rejected(self):
        path = Path(self.temp.name) / "bad.json"
        path.write_text('{"schema":"first","schema":"second"}')
        with self.assertRaises(ValueError):
            facts.load(path, allow_fixture=True)
        self.publish()
        pointer = json.loads((self.directory / "current.json").read_text())
        pointer["artifact"] = "../../private.json"
        path.write_text(json.dumps(pointer))
        with self.assertRaises(ValueError):
            facts.load(path, allow_fixture=True)

    def test_receipt_and_artifact_integrity_mismatch_rejected(self):
        self.publish()
        current = self.directory / "current.json"
        pointer = json.loads(current.read_text())
        pointer["receipt"]["integrity"] = "f" * 64
        current.write_text(json.dumps(pointer))
        with self.assertRaises(ValueError):
            facts.load(current, allow_fixture=True)

    @unittest.skipUnless(hasattr(os, "symlink") and os.name == "posix", "Linux nofollow contract")
    def test_symlink_read_and_publication_are_rejected(self):
        self.publish()
        link = Path(self.temp.name) / "link.json"
        link.symlink_to(self.directory / "current.json")
        with self.assertRaises(ValueError):
            facts.load(link, allow_fixture=True)
        link_dir = Path(self.temp.name) / "link-dir"
        link_dir.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaises(ValueError):
            facts.publish(change(self.value), link_dir, authenticated=True, allow_fixture=True)

    @unittest.skipUnless(hasattr(os, "symlink") and os.name == "posix", "Linux nofollow contract")
    def test_symlink_ancestor_of_loader_or_publisher_is_rejected(self):
        self.publish()
        link = Path(self.temp.name) / "link-parent"
        link.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaises(ValueError):
            facts.load(link / "current.json", allow_fixture=True)
        with self.assertRaises(ValueError):
            facts.publish(change(self.value), link / "nested", authenticated=True, allow_fixture=True)

    def test_failed_archive_preparation_does_not_install(self):
        self.publish()
        before = (self.directory / "current.json").read_bytes()
        original = facts._write_new
        def fail_receipt(path, data):
            if Path(path).parent.name == "receipts":
                raise OSError("simulated receipt archive failure")
            return original(path, data)
        with mock.patch.object(facts, "_write_new", side_effect=fail_receipt), self.assertRaises(OSError):
            self.publish(change(self.value))
        self.assertEqual((self.directory / "current.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()




