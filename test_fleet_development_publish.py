"""Synthetic bounded public-GitHub capture/export and protected Linux installation."""
import contextlib
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fleet_development_publish as publisher
import fleet_project_adapters as adapters

NOW = "2026-10-24T12:00:00Z"
EXPORTED = "2026-10-24T11:59:10Z"
REPO = "example/project-alpha"
REVISION = "a" * 40
CANARY = "SYNTHETIC_PRIVATE_CAPTURE_CANARY"


def record(value, suffix, second):
    return {"url": "https://api.github.com/repos/" + REPO + suffix,
            "captured_at": "2026-10-24T11:59:" + format(second, "02d") + "Z",
            "sha256": publisher.digest(value), "value": value}


def fixture(output="/protected/example-development.json"):
    base = "https://api.github.com/repos/" + REPO
    branch = {"name": "main", "commit": {"sha": REVISION}}
    capture = {"schema": publisher.CAPTURE_SCHEMA, "project_id": "project-alpha", "source_id": "github-alpha",
        "repository": record({"full_name": REPO, "private": False, "url": base,
                              "owner": {"login": CANARY}, "credentials": CANARY}, "", 0),
        "branch_before": record(branch, "/branches/main", 1),
        "commit": record({"sha": REVISION, "url": base + "/git/commits/" + REVISION,
            "committer": {"date": "2026-10-23T18:30:00Z", "email": CANARY},
            "message": CANARY}, "/git/commits/" + REVISION, 2),
        "actions": record({"total_count": 1, "workflow_runs": [{
            "id": 17, "head_sha": REVISION, "status": "completed", "conclusion": "success",
            "run_started_at": "2026-10-24T11:50:00Z", "updated_at": "2026-10-24T11:58:00Z",
            "repository": {"full_name": REPO}, "actor": {"login": CANARY},
            "logs_url": CANARY}]}, "/actions/runs?head_sha=" + REVISION + "&per_page=6&page=1", 3),
        "issues": [record({"number": 7, "repository_url": base, "title": "Example / Project Alpha backlog",
            "state": "open", "updated_at": "2026-10-24T11:30:00Z",
            "body": CANARY, "user": {"login": CANARY}}, "/issues/7", 4)],
        "branch_after": record(copy.deepcopy(branch), "/branches/main", 5)}
    config = {"schema": publisher.POLICY_SCHEMA, "project_id": "project-alpha", "source_id": "github-alpha",
        "repository": REPO, "ref": "main", "revision": REVISION, "output_path": output,
        "max_age_seconds": 3600, "issue_numbers": [7], "capture_sha256": "", "source_sha256s": {}}
    return repin(capture, config)


def repin(capture, config):
    """Operator pins reviewed raw source digests; production never guesses them."""
    config = copy.deepcopy(config)
    entries = {key: capture[key] for key in ("repository", "branch_before", "commit", "actions", "branch_after")}
    entries.update({"issue-" + str(number): entry for number, entry in zip(config["issue_numbers"], capture["issues"])})
    for entry in entries.values():
        entry["sha256"] = publisher.digest(entry["value"])
    config["source_sha256s"] = {key: entry["sha256"] for key, entry in entries.items()}
    config["capture_sha256"] = publisher.digest(capture)
    return capture, config


def artifact(capture=None, config=None, exported_at=EXPORTED):
    if capture is None:
        capture, config = fixture()
    return publisher.export_capture(capture, config, exported_at)


def reseal(value):
    value["integrity"] = publisher.digest({key: item for key, item in value.items() if key != "integrity"})
    return value


def recapture(capture, seconds):
    value = copy.deepcopy(capture)
    for entry in [value[key] for key in ("repository", "branch_before", "commit", "actions", "branch_after")] + value["issues"]:
        entry["captured_at"] = (adapters._time(entry["captured_at"]) + timedelta(seconds=seconds)).isoformat()
    return value


class CaptureProjectionTests(unittest.TestCase):
    def setUp(self):
        self.capture, self.config = fixture()

    def test_actual_fields_are_useful_and_private_bulk_data_is_discarded(self):
        with patch("socket.create_connection", side_effect=AssertionError("export cannot connect")):
            exported = artifact(self.capture, self.config)
        document = publisher.validate_export(exported, self.config)
        self.assertEqual(document["repository_state"]["revision"], REVISION)
        self.assertEqual(document["repository_state"]["last_commit_at"], "2026-10-23T18:30:00Z")
        self.assertEqual(document["issues"]["items"][0]["title"], "Example / Project Alpha backlog")
        self.assertEqual(document["ci"]["items"][0]["conclusion"], "success")
        self.assertNotIn(CANARY, json.dumps(exported))
        for forbidden in ("credentials", "email", "logs_url", "body", "actor", "completed_at"):
            self.assertNotIn(forbidden, json.dumps(document))
        self.assertEqual(exported["provenance"]["sources"]["actions"]["sha256"], self.config["source_sha256s"]["actions"])

    def test_actions_update_is_never_relabelled_completion(self):
        document = artifact(self.capture, self.config)["document"]
        run = document["ci"]["items"][0]
        self.assertEqual(run["started_at"], "2026-10-24T11:50:00Z")
        self.assertEqual(run["updated_at"], "2026-10-24T11:58:00Z")
        self.assertNotIn("completed_at", run)
        self.assertEqual(document["ci"]["source_format"], adapters.ACTIONS_SOURCE)
        self.assertFalse(document["ci"]["complete"])
        self.assertFalse(document["issues"]["complete"])
        observation = adapters.development_actions_snapshot(document, "project-alpha", "github-alpha", REPO, NOW, 3600)
        adapters.validate_observation("development_actions", observation)
        self.assertEqual(observation["gaps"]["ci"], "partial_coverage")
        self.assertEqual(observation["gaps"]["issues"], "partial_coverage")
        self.assertNotIn("development_complete", observation)

    def test_aggregate_capture_hash_binds_every_operator_input(self):
        self.capture["commit"]["value"]["message"] = "changed"
        with self.assertRaises(ValueError):
            artifact(self.capture, self.config)

    def test_response_hash_and_policy_source_hash_are_independent_gates(self):
        self.capture["commit"]["value"]["message"] = "changed"
        self.config["capture_sha256"] = publisher.digest(self.capture)
        with self.assertRaises(ValueError):
            artifact(self.capture, self.config)
        self.capture, self.config = repin(self.capture, self.config)
        self.config["source_sha256s"]["commit"] = "f" * 64
        with self.assertRaises(ValueError):
            artifact(self.capture, self.config)

    def test_public_identity_ref_and_source_ids_are_fixed(self):
        changes = [
            ("repository", "private", True), ("repository", "full_name", "example/other-project"),
            ("branch_before", "name", "other"), ("commit", "sha", "b"*40),
        ]
        for record_key, field, value in changes:
            capture, config = fixture()
            capture[record_key]["value"][field] = value
            capture, config = repin(capture, config)
            with self.subTest(field=field), self.assertRaises(ValueError):
                artifact(capture, config)
        for field, value in (("project_id", "project-beta"), ("source_id", "other-source")):
            capture, config = fixture()
            capture[field] = value
            config["capture_sha256"] = publisher.digest(capture)
            with self.subTest(field=field), self.assertRaises(ValueError):
                artifact(capture, config)

    def test_fixed_get_urls_cannot_select_other_accounts_endpoints_or_queries(self):
        for url in ("https://api.github.com/user", "https://api.github.com/repos/example/other/issues/7",
                    "https://example.invalid/private", self.capture["actions"]["url"] + "&event=pull_request"):
            capture, config = fixture()
            capture["actions"]["url"] = url
            config["capture_sha256"] = publisher.digest(capture)
            with self.subTest(url=url), self.assertRaises(ValueError):
                artifact(capture, config)

    def test_changed_branch_never_exports_a_mixed_head(self):
        self.capture["branch_after"]["value"]["commit"]["sha"] = "b"*40
        self.capture, self.config = repin(self.capture, self.config)
        with self.assertRaises(ValueError):
            artifact(self.capture, self.config)

    def test_workflow_repo_revision_duplicate_ids_and_missing_dates_are_rejected(self):
        for fault in ("repository", "revision", "duplicate", "date"):
            capture, config = fixture()
            row = capture["actions"]["value"]["workflow_runs"][0]
            if fault == "repository": row["repository"]["full_name"] = "example/other-project"
            elif fault == "revision": row["head_sha"] = "b"*40
            elif fault == "duplicate":
                capture["actions"]["value"]["workflow_runs"].append(copy.deepcopy(row))
                capture["actions"]["value"]["total_count"] = 2
            else: row.pop("updated_at")
            capture, config = repin(capture, config)
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                artifact(capture, config)

    def test_pending_requested_waiting_and_prestart_cancellation_keep_real_states(self):
        for state, conclusion in (("pending", None), ("requested", None), ("waiting", None),
                                  ("queued", None), ("completed", "cancelled")):
            capture, config = fixture()
            run = capture["actions"]["value"]["workflow_runs"][0]
            run.update(status=state, conclusion=conclusion, run_started_at=None)
            capture, config = repin(capture, config)
            document = artifact(capture, config)["document"]
            with self.subTest(state=state):
                self.assertEqual(document["ci"]["items"][0]["status"], state)
                self.assertIsNone(document["ci"]["items"][0]["started_at"])
                self.assertNotIn("completed_at", document["ci"]["items"][0])

    def test_issue_subset_keeps_names_states_dates_and_never_includes_prs(self):
        for fault in ("number", "repository", "pull_request"):
            capture, config = fixture()
            issue = capture["issues"][0]["value"]
            if fault == "number": issue["number"] = 8
            elif fault == "repository": issue["repository_url"] += "-other"
            else: issue["pull_request"] = {"url": CANARY}
            capture, config = repin(capture, config)
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                artifact(capture, config)

    def test_source_capture_order_future_and_window_are_bounded(self):
        for key, timestamp in (("commit", "2026-10-24T11:58:00Z"),
                               ("actions", "2026-10-24T12:01:00Z"),
                               ("repository", "2026-10-24T11:50:00Z"),
                               ("branch_after", "2026-10-24T11:59:00Z")):
            capture, config = fixture()
            capture[key]["captured_at"] = timestamp
            config["capture_sha256"] = publisher.digest(capture)
            with self.subTest(key=key), self.assertRaises(ValueError):
                artifact(capture, config)

    def test_export_time_never_refreshes_historical_source_dates(self):
        exported = artifact(self.capture, self.config, "2026-10-25T12:00:00Z")
        value = adapters.development_actions_snapshot(exported["document"], "project-alpha", "github-alpha",
                                                       REPO, "2026-10-25T12:00:00Z", 3600)
        self.assertEqual(value["gaps"]["repository"], "stale")
        self.assertEqual(value["gaps"]["ci"], "stale")
        self.assertEqual(value["gaps"]["issues"], "stale")
        self.assertEqual(value["ci"]["items"][0]["updated_at"], "2026-10-24T11:58:00Z")
        self.assertNotIn("snapshot", value["gaps"])  # capture and export dates are independent

    def test_empty_valid_actions_are_specific_coverage_gap_not_empty_repository(self):
        self.capture["actions"]["value"] = {"total_count": 0, "workflow_runs": []}
        self.capture, self.config = repin(self.capture, self.config)
        exported = artifact(self.capture, self.config)
        value = adapters.development_actions_snapshot(exported["document"], "project-alpha", "github-alpha", REPO, NOW, 3600)
        self.assertEqual(value["gaps"]["ci_revision"], "ci_does_not_cover_head")
        self.assertEqual(value["repository"]["revision"], REVISION)
        self.assertEqual(value["issues"]["items"][0]["number"], 7)

    def test_unknown_fields_counts_and_unsafe_titles_cannot_expand_projection(self):
        for title in ("secret=synthetic", "private@example.invalid", "/root/private", "https://example.invalid/private"):
            capture, config = fixture()
            capture["issues"][0]["value"]["title"] = title
            capture, config = repin(capture, config)
            with self.subTest(title=title), self.assertRaises(ValueError):
                artifact(capture, config)
        capture, config = fixture()
        capture["actions"]["value"]["total_count"] = False
        capture, config = repin(capture, config)
        with self.assertRaises(ValueError):
            artifact(capture, config)
        exported = artifact(self.capture, self.config)
        exported["provenance"]["approved"] = True
        reseal(exported)
        with self.assertRaises(ValueError):
            publisher.validate_export(exported, self.config)

    def test_policy_cannot_expand_authority_or_accept_arbitrary_paths_refs(self):
        for patch_value in ({"approved": True}, {"ref": "../main"}, {"ref": "main?token=x"},
                            {"output_path": "../private"}, {"output_path": "//server/private"},
                            {"issue_numbers": []}, {"issue_numbers": [True]},
                            {"issue_numbers": [7, 7]}, {"revision": "a"*64}, {"max_age_seconds": True}):
            with self.subTest(patch=patch_value), self.assertRaises(ValueError):
                publisher.policy(dict(self.config, **patch_value))

    def test_capture_and_projected_output_size_are_bounded(self):
        self.capture["repository"]["value"]["oversized"] = "x" * publisher.MAX_CAPTURE_BYTES
        self.capture, self.config = repin(self.capture, self.config)
        with self.assertRaises(ValueError):
            artifact(self.capture, self.config)

    def test_resealed_export_cannot_change_policy_revision_or_source_hash(self):
        for fault in ("policy", "source", "revision", "capture-date", "scope", "completion"):
            exported = artifact(self.capture, self.config)
            if fault == "policy": exported["provenance"]["policy_sha256"] = "b"*64
            elif fault == "source": exported["provenance"]["sources"]["actions"]["sha256"] = "b"*64
            elif fault == "revision": exported["document"]["repository_state"]["revision"] = "b"*40
            elif fault == "capture-date": exported["document"]["ci"]["captured_at"] = EXPORTED
            elif fault == "scope": exported["document"]["issues"]["items"][0]["number"] = 8
            else: exported["document"]["ci"]["items"][0]["completed_at"] = "2026-10-24T11:58:00Z"
            reseal(exported)
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                publisher.validate_export(exported, self.config)

    def test_cli_export_prints_only_sealed_sanitized_document_and_generic_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path, capture_path = root/"policy.json", root/"capture.json"
            policy_path.write_bytes(publisher.canonical(self.config))
            capture_path.write_bytes(publisher.canonical(self.capture))
            output = io.StringIO()
            with patch.object(publisher, "datetime") as clock, contextlib.redirect_stdout(output):
                clock.now.return_value = adapters._time(NOW)
                self.assertEqual(publisher.main(["export", "--policy", str(policy_path), "--capture", str(capture_path)]), 0)
            exported = json.loads(output.getvalue())
            self.assertNotIn(CANARY, output.getvalue())
            publisher.validate_export(exported, self.config)
        output = io.StringIO()
        with patch.object(publisher, "_read_local", side_effect=ValueError(CANARY)), contextlib.redirect_stderr(output):
            self.assertEqual(publisher.main(["export", "--capture", "/synthetic/capture.json"]), 1)
        self.assertEqual(output.getvalue(), '{"status":"unavailable"}\n')


@unittest.skipUnless(os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() == 0,
                     "protected operator fixtures require isolated root Linux")
class ProtectedDevelopmentInstallation(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="cara-development-test-", dir="/root")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.output = self.root / "current.json"
        self.policy_path, self.artifact_path = self.root/"policy.json", self.root/"export.json"
        self.capture, self.config = fixture(str(self.output))
        self.exported = artifact(self.capture, self.config)
        self.stage()

    def stage(self):
        self.policy_path.write_bytes(publisher.canonical(self.config)); self.policy_path.chmod(0o600)
        self.artifact_path.write_bytes(publisher.canonical(self.exported)); self.artifact_path.chmod(0o600)

    def install(self):
        with patch.object(publisher, "datetime") as clock:
            clock.now.return_value = adapters._time(NOW)
            return publisher.install_export(self.policy_path, self.artifact_path)

    def candidate(self, seconds=0, exported_at="2026-10-24T11:59:11Z"):
        self.capture, self.config = repin(recapture(self.capture, seconds), self.config)
        self.exported = artifact(self.capture, self.config, exported_at)
        self.stage()

    def test_root_install_reaches_actual_shared_read_adapter_with_useful_evidence(self):
        receipt = self.install()
        self.assertEqual(receipt["status"], "installed")
        self.assertEqual(self.output.stat().st_uid, 0)
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(receipt["file_sha256"], hashlib.sha256(self.output.read_bytes()).hexdigest())
        binding = {"adapter": adapters.ADAPTERS["development_actions"]["implementation_version"],
            "path": str(self.output), "source_id": "github-alpha", "repository": REPO, "max_age_seconds": 3600}
        value = adapters.read_bound("development_actions", binding, "project-alpha", NOW)
        self.assertEqual(value["status"], "recorded")
        self.assertEqual(value["repository"]["revision"], REVISION)
        self.assertEqual(value["ci"]["items"][0]["updated_at"], "2026-10-24T11:58:00Z")
        self.assertEqual(value["issues"]["items"][0]["title"], "Example / Project Alpha backlog")

    def test_identical_install_is_idempotent_without_replacement_or_new_install_time(self):
        self.install()
        stamp = self.output.stat().st_mtime_ns
        with patch.object(publisher.os, "replace", side_effect=AssertionError("unchanged source must not replace")):
            receipt = self.install()
        self.assertEqual(receipt["status"], "unchanged")
        self.assertNotIn("installed_at", receipt)
        self.assertEqual(self.output.stat().st_mtime_ns, stamp)

    def test_nonroot_cannot_gain_authority_from_valid_hashes(self):
        with patch.object(publisher.os, "geteuid", return_value=123), self.assertRaises(ValueError):
            self.install()
        self.assertFalse(self.output.exists())

    def test_invalid_export_policy_or_provenance_preserves_last_good(self):
        self.install(); before = self.output.read_bytes()
        for fault in ("integrity", "policy", "revision", "source", "project"):
            value = copy.deepcopy(self.exported)
            if fault == "integrity": value["integrity"] = "f"*64
            elif fault == "policy": value["provenance"]["policy_sha256"] = "f"*64
            elif fault == "revision": value["document"]["repository_state"]["revision"] = "b"*40
            elif fault == "source": value["provenance"]["sources"]["commit"]["sha256"] = "f"*64
            else: value["document"]["project_id"] = "project-beta"
            if fault != "integrity": reseal(value)
            self.artifact_path.write_bytes(publisher.canonical(value))
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                self.install()
            self.assertEqual(self.output.read_bytes(), before)

    def test_new_export_date_cannot_hide_older_capture(self):
        self.install(); before = self.output.read_bytes()
        self.candidate(seconds=-1, exported_at="2026-10-24T12:00:00Z")
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(self.output.read_bytes(), before)

    def test_publication_time_rollback_and_same_time_conflict_are_refused(self):
        self.install(); before = self.output.read_bytes()
        self.candidate(exported_at="2026-10-24T11:59:06Z")
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(self.output.read_bytes(), before)
        self.capture["issues"][0]["value"]["title"] = "Changed / Project Alpha title"
        self.capture, self.config = repin(self.capture, self.config)
        self.exported = artifact(self.capture, self.config, EXPORTED)
        self.stage()
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(self.output.read_bytes(), before)

    def test_existing_protected_read_permissions_are_preserved(self):
        self.install(); self.output.chmod(0o640)
        self.candidate()
        self.assertEqual(self.install()["status"], "installed")
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o640)

    def test_sources_and_destination_reject_symlinks_hardlinks_and_writable_inputs(self):
        for name in ("policy", "artifact"):
            path = self.policy_path if name == "policy" else self.artifact_path
            original = path.read_bytes()
            backup = path.with_suffix(".real")
            path.rename(backup); path.symlink_to(backup)
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.install()
            path.unlink(); backup.rename(path)
            path.chmod(0o660)
            with self.assertRaises(ValueError):
                self.install()
            path.chmod(0o600)
            link = path.with_suffix(".hard"); os.link(path, link)
            with self.assertRaises(ValueError):
                self.install()
            link.unlink()
            self.assertEqual(path.read_bytes(), original)
        victim = self.root/"victim.json"; victim.write_text("keep")
        self.output.symlink_to(victim)
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(victim.read_text(), "keep")

    def test_policy_or_export_cannot_be_the_publication_destination(self):
        self.config["output_path"] = str(self.policy_path)
        self.exported = artifact(self.capture, self.config)
        self.stage(); before = self.policy_path.read_bytes()
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(self.policy_path.read_bytes(), before)

    def test_old_generic_development_source_is_not_silently_overwritten(self):
        previous = dict(self.exported["document"], schema=adapters.DEVELOPMENT_SCHEMA)
        self.output.write_bytes(publisher.canonical(previous)); self.output.chmod(0o600)
        before = self.output.read_bytes()
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(self.output.read_bytes(), before)

    def test_overlapping_publisher_cannot_change_last_good(self):
        import fcntl
        self.install(); before = self.output.read_bytes(); self.candidate()
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(ValueError):
                self.install()
        finally:
            os.close(descriptor)
        self.assertEqual(self.output.read_bytes(), before)

    def test_replace_failure_keeps_last_good_and_removes_only_own_temporary_file(self):
        self.install(); before = self.output.read_bytes(); self.candidate()
        unrelated = self.root/".unrelated.pending"; unrelated.write_text("keep")
        with patch.object(publisher.os, "replace", side_effect=OSError("synthetic replace failure")), self.assertRaises(OSError):
            self.install()
        self.assertEqual(self.output.read_bytes(), before)
        self.assertEqual(unrelated.read_text(), "keep")
        self.assertEqual(list(self.root.glob(".current.json.*.pending")), [])

    def test_after_replace_sync_failure_is_explicitly_ambiguous_not_rollback(self):
        self.install(); before = self.output.read_bytes(); self.candidate()
        with patch.object(publisher.os, "fsync", side_effect=[None, OSError("synthetic sync failure")]), self.assertRaises(publisher.PublicationAmbiguous):
            self.install()
        self.assertNotEqual(self.output.read_bytes(), before)
        self.assertEqual(json.loads(self.output.read_text()), self.exported["document"])

    def test_malformed_protected_json_duplicate_keys_and_nonfinite_values_are_refused(self):
        self.install(); before = self.output.read_bytes()
        for raw in (b'{"schema":1,"schema":2}', b'{"value":NaN}', b'not json'):
            self.artifact_path.write_bytes(raw)
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.install()
            self.assertEqual(self.output.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
