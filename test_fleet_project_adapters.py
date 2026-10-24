"""Offline CF-05 evidence projections; synthetic identities and no live effects."""
import copy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import fleet_project_adapters as adapters

NOW = "2026-10-24T12:00:00Z"
EXPORTED = "2026-10-24T11:55:00Z"
BACKUP_AT = "2026-10-24T11:30:00Z"
RESTORE_AT = "2026-10-24T11:40:00Z"
ARCHIVE = "ingest-20261024T110000Z.db.gz"
PROJECT = "project-alpha"
SOURCE = "source-alpha"
REPO = "example/project-alpha"
REVISION = "a" * 40


def job(action="db_backup", status="done", created="2026-10-24T11:20:00Z", finished=BACKUP_AT, archive=ARCHIVE):
    if status == "done":
        if action == "db_backup":
            result = {"file": archive, "bytes": 12345, "offsite": "telegram:fleet", "offbox_blocked": False,
                      "encrypted_file": archive + ".enc"}
        else:
            result = {"file": archive + ".enc", "encrypted": True, "bytes": 45678, "tables": 23, "integrity": "ok"}
    else:
        result = None
    return {"action": action, "status": status, "created_at": created, "finished_at": finished, "result": result}


def backup_document():
    return {"schema": adapters.BACKUP_SCHEMA, "project_id": PROJECT, "source_id": SOURCE,
            "source_format": adapters.BACKUP_SOURCE, "exported_at": EXPORTED,
            "jobs": [job(), job("backup_verify", created="2026-10-24T11:35:00Z", finished=RESTORE_AT)]}


def development_document():
    return {"schema": adapters.DEVELOPMENT_SCHEMA, "project_id": PROJECT, "source_id": SOURCE,
        "exported_at": EXPORTED, "repository": REPO,
        "repository_state": {"status": "recorded", "captured_at": "2026-10-24T11:50:00Z",
            "revision": REVISION, "last_commit_at": "2026-10-23T18:30:00Z"},
        "ci": {"status": "recorded", "captured_at": "2026-10-24T11:50:00Z", "complete": False,
            "items": [{"run_id": 101, "revision": REVISION, "status": "completed", "conclusion": "success",
                       "started_at": "2026-10-24T11:40:00Z", "completed_at": "2026-10-24T11:45:00Z"}]},
        "issues": {"status": "recorded", "captured_at": "2026-10-24T11:50:00Z", "complete": False,
            "items": [{"number": 7, "title": "Example / Project Alpha: keep useful names", "state": "open", "updated_at": "2026-10-24T11:20:00Z"}]}}


def backup_project(document=None, age=86400):
    return adapters.backup_receipt(document or backup_document(), PROJECT, SOURCE, NOW, age)


def development_project(document=None, age=3600):
    return adapters.development_snapshot(document or development_document(), PROJECT, SOURCE, REPO, NOW, age)


def config(kind="backups", path="/protected/example-alpha.json"):
    value = {"adapter": adapters.ADAPTERS[kind]["implementation_version"], "path": path,
             "source_id": SOURCE, "max_age_seconds": 86400}
    if kind == "development":
        value["repository"] = REPO
    return value


class HealthProjectionTests(unittest.TestCase):
    def test_actual_nested_worker_schema_retains_application_failure(self):
        body = {"status": "degraded", "database": "ok", "app_env": "production",
            "workers": {"delivery": {"status": "ok", "seconds_since_progress": 8},
                        "intake": {"status": "failed", "seconds_since_progress": 5, "reason": "authentication"}}}
        value = adapters.daily_lingo_health(body, 503)
        self.assertEqual(value["health"], {"status": "degraded", "database": "ok", "delivery": "ok", "intake": "failed"})
        self.assertEqual(value["http_status"], 503)
        self.assertEqual(value["worker_progress"], {"delivery": 8, "intake": 5})
        self.assertNotIn("authentication", json.dumps(value))
        self.assertNotIn("app_env", value)

    def test_unexposed_metrics_cannot_be_filled_by_plausible_extra_fields(self):
        value = adapters.daily_lingo_health({"status": "ok", "database": "ok", "generation_enabled": True,
            "generation": "enabled", "reserves": [5]*6, "target": 5, "day_budget": 40,
            "token": "SYNTHETIC-SECRET-CANARY", "workers": {"delivery": {"status": "starting", "seconds_since_progress": 0}}}, 200)
        for field in ("generation", "reserves", "target", "day_budget"):
            self.assertEqual(value["gaps"][field], "not_exposed")
            self.assertNotIn(field, value["health"])
        self.assertEqual(value["gaps"]["intake"], "not_exposed")
        self.assertNotIn("SYNTHETIC", json.dumps(value))
        self.assertNotIn("healthy", value)

    def test_stalled_and_starting_are_preserved_without_running_override(self):
        value = adapters.daily_lingo_health({"status": "degraded", "database": "error", "Running": True,
            "workers": {"delivery": {"status": "stalled", "seconds_since_progress": 181},
                        "intake": {"status": "starting", "seconds_since_progress": 4}}}, 200)
        self.assertEqual(value["health"]["delivery"], "stalled")
        self.assertEqual(value["health"]["intake"], "starting")
        self.assertEqual(value["health"]["database"], "error")

    def test_invalid_types_unknown_states_and_oversize_fail_closed(self):
        cases = [(False, {}), (200, []), (200, {"status": True}), (200, {"database": "healthy"}),
            (200, {"workers": []}), (200, {"workers": {"delivery": {"status": "ok", "seconds_since_progress": True}}}),
            (200, {"workers": {"intake": {"status": "running", "seconds_since_progress": 1}}}),
            (200, {"workers": {"intake": {"status": "ok"}}}), (200, {"extra": "x"*8193})]
        for code, body in cases:
            with self.subTest(code=code, body_type=type(body).__name__), self.assertRaises(ValueError):
                adapters.daily_lingo_health(body, code)


class BackupProjectionTests(unittest.TestCase):
    def test_existing_result_contract_dates_and_restore_coverage_are_preserved(self):
        value = backup_project()
        self.assertEqual(value["status"], "recorded")
        self.assertEqual(value["observed_at"], NOW)
        self.assertEqual(value["exported_at"], EXPORTED)
        self.assertEqual(value["backup"]["finished_at"], BACKUP_AT)
        self.assertEqual(value["restore"]["finished_at"], RESTORE_AT)
        self.assertEqual(value["restore"]["coverage"], "latest_recorded_backup")
        self.assertEqual(value["restore"]["integrity"], "ok")
        self.assertEqual(value["backup"]["compressed_bytes"], 12345)
        self.assertEqual(value["restore"]["restored_bytes"], 45678)
        self.assertEqual(value["gaps"], {})
        self.assertEqual(len(value["receipt_sha256"]), 64)
        rendered = json.dumps(value)
        self.assertNotIn("ingest-", rendered)
        self.assertNotIn("telegram:", rendered)
        self.assertNotIn("file", value["backup"])
        self.assertNotIn("recovery_ready", rendered)

    def test_active_timer_or_empty_job_list_proves_no_backup_success(self):
        document = backup_document(); document["jobs"] = []
        value = backup_project(document)
        self.assertIsNone(value["backup"]); self.assertIsNone(value["restore"])
        self.assertEqual(value["gaps"], {"backup": "no_success_recorded", "restore": "no_success_recorded"})
        document["timer_active"] = True
        with self.assertRaises(ValueError):
            backup_project(document)

    def test_creation_without_restore_does_not_become_verified_recovery(self):
        document = backup_document(); document["jobs"] = document["jobs"][:1]
        value = backup_project(document)
        self.assertEqual(value["gaps"]["latest_backup_restore"], "latest_backup_not_restore_verified")
        self.assertEqual(value["gaps"]["restore"], "no_success_recorded")
        self.assertIsNone(value["restore"])

    def test_older_verified_archive_does_not_verify_newer_snapshot(self):
        document = backup_document()
        document["jobs"][1]["result"]["file"] = "ingest-20261023T110000Z.db.gz.enc"
        value = backup_project(document)
        self.assertEqual(value["restore"]["integrity"], "ok")
        self.assertEqual(value["restore"]["coverage"], "different_or_unknown_backup")
        self.assertEqual(value["gaps"]["latest_backup_restore"], "latest_backup_not_restore_verified")

    def test_failed_newer_attempt_is_not_hidden_by_prior_success(self):
        document = backup_document()
        document["jobs"].append(job(status="failed", created="2026-10-24T11:45:00Z", finished="2026-10-24T11:46:00Z"))
        value = backup_project(document)
        self.assertEqual(value["backup"]["finished_at"], BACKUP_AT)
        self.assertEqual(value["latest_attempts"]["backup"]["status"], "failed")
        self.assertEqual(value["gaps"]["backup_latest_attempt"], "latest_attempt_failed")

    def test_pending_newer_attempt_does_not_fabricate_finished_evidence_date(self):
        document = backup_document()
        document["jobs"].append(job(status="pending", created="2026-10-24T11:45:00Z", finished=None))
        value = backup_project(document)
        self.assertIsNone(value["latest_attempts"]["backup"]["finished_at"])
        self.assertEqual(value["gaps"]["backup_latest_attempt"], "latest_attempt_pending")
        self.assertEqual(value["backup"]["finished_at"], BACKUP_AT)

    def test_new_export_cannot_refresh_old_success_dates(self):
        value = backup_project(age=60)
        self.assertEqual(value["gaps"]["snapshot"], "stale")
        self.assertEqual(value["gaps"]["backup"], "stale")
        self.assertEqual(value["gaps"]["restore"], "stale")
        self.assertEqual(value["restore"]["finished_at"], RESTORE_AT)

    def test_offsite_blocked_not_due_and_unconfigured_remain_distinct(self):
        for original, blocked, expected in (("blocked:size", True, "blocked"), ("not-due", False, "not_due"), ("", False, "not_configured"), ("spaces", False, "delivered")):
            with self.subTest(original=original):
                document = backup_document(); document["jobs"][0]["result"].update(offsite=original, offbox_blocked=blocked)
                value = backup_project(document)
                self.assertEqual(value["backup"]["offsite"], expected)

    def test_cross_project_source_format_and_extra_fields_fail_closed(self):
        for field, bad in (("project_id", "project-beta"), ("source_id", "source-beta"), ("source_format", "timer-active/v1"), ("schema", "v0"), ("operator_path", "/root/SYNTHETIC-CANARY")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                document = backup_document(); document[field] = bad
                backup_project(document)

    def test_invalid_dates_booleans_and_success_payloads_fail_closed(self):
        mutators = [lambda doc: doc["jobs"][0].update(finished_at=None),
            lambda doc: doc["jobs"][0].update(finished_at="2026-10-24T11:19:00Z"),
            lambda doc: doc["jobs"][0].update(created_at="2026-10-25T11:20:00Z"),
            lambda doc: doc["jobs"][0]["result"].update(bytes=True),
            lambda doc: doc["jobs"][0]["result"].update(offsite="spaces:private/SYNTHETIC-CANARY"),
            lambda doc: doc["jobs"][0]["result"].update(offbox_blocked=True),
            lambda doc: doc["jobs"][0]["result"].update(encrypted_file=""),
            lambda doc: doc["jobs"][1]["result"].update(integrity="unchecked"),
            lambda doc: doc["jobs"][1]["result"].update(tables=False),
            lambda doc: doc["jobs"][1]["result"].update(encrypted=False),
            lambda doc: doc["jobs"][0]["result"].update(file="/root/ingest-20261024T110000Z.db.gz"),
            lambda doc: doc["jobs"][0]["result"].update(file="ingest-20269924T110000Z.db.gz"),
            lambda doc: doc["jobs"][0]["result"].update(file="ingest-20261025T110000Z.db.gz", encrypted_file="ingest-20261025T110000Z.db.gz.enc")]
        for index, mutate in enumerate(mutators):
            with self.subTest(index=index), self.assertRaises(ValueError):
                document = backup_document(); mutate(document); backup_project(document)

    def test_duplicate_or_failed_success_records_are_refused(self):
        document = backup_document(); document["jobs"].append(copy.deepcopy(document["jobs"][0]))
        with self.assertRaises(ValueError):
            backup_project(document)
        document = backup_document(); document["jobs"][0]["status"] = "failed"
        with self.assertRaises(ValueError):
            backup_project(document)


class DevelopmentProjectionTests(unittest.TestCase):
    def test_actual_question_gets_revision_ci_and_issue_details_with_dates(self):
        value = development_project()
        self.assertEqual(value["status"], "recorded")
        self.assertEqual(value["repository"]["name"], REPO)
        self.assertEqual(value["repository"]["revision"], REVISION)
        self.assertEqual(value["ci"]["items"][0]["conclusion"], "success")
        self.assertEqual(value["issues"]["items"][0]["title"], "Example / Project Alpha: keep useful names")
        self.assertEqual(value["issues"]["items"][0]["state"], "open")
        self.assertEqual(value["issues"]["captured_at"], "2026-10-24T11:50:00Z")
        self.assertEqual(value["gaps"], {"ci": "partial_coverage", "issues": "partial_coverage"})
        self.assertNotIn("on_track", json.dumps(value))
        self.assertNotIn("deployed", value)

    def test_each_unconnected_source_stays_truthful(self):
        document = development_document()
        document["repository_state"] = {"status": "not_connected", "captured_at": None, "revision": None, "last_commit_at": None}
        document["ci"] = {"status": "not_connected", "captured_at": None, "items": [], "complete": False}
        document["issues"] = copy.deepcopy(document["ci"])
        value = development_project(document)
        self.assertEqual(value["gaps"], {key: "not_connected" for key in ("repository", "ci", "issues")})
        self.assertNotIn("revision", value["repository"])
        self.assertNotIn("items", value["issues"])

    def test_missing_ci_binding_cannot_hide_repository_or_backlog_information(self):
        document = development_document(); document["ci"] = {"status": "not_connected", "captured_at": None, "items": [], "complete": False}
        value = development_project(document)
        self.assertEqual(value["repository"]["revision"], REVISION)
        self.assertEqual(value["issues"]["items"][0]["number"], 7)
        self.assertEqual(value["gaps"]["ci"], "not_connected")

    def test_success_for_other_revision_cannot_qualify_recorded_head(self):
        document = development_document(); document["ci"]["items"][0]["revision"] = "b"*40
        value = development_project(document)
        self.assertEqual(value["gaps"]["ci_revision"], "ci_does_not_cover_head")
        self.assertEqual(value["ci"]["items"][0]["conclusion"], "success")

    def test_closed_issues_and_ci_success_do_not_become_project_completion(self):
        document = development_document(); document["issues"]["items"][0]["state"] = "closed"
        document["issues"]["complete"] = True; document["ci"]["complete"] = True
        value = development_project(document)
        self.assertEqual(value["issues"]["items"][0]["state"], "closed")
        self.assertNotIn("development_complete", value)
        self.assertNotIn("healthy", value)
        self.assertNotIn("release_status", value)

    def test_source_age_and_evidence_dates_remain_separate_from_read(self):
        value = development_project(age=60)
        self.assertEqual(value["gaps"]["snapshot"], "stale")
        for field in ("repository", "ci", "issues"):
            self.assertEqual(value["gaps"][field], "stale")
            self.assertEqual(value[field]["captured_at"], "2026-10-24T11:50:00Z")

    def test_ci_pending_and_failed_states_are_preserved(self):
        for status, conclusion, started, completed in (("queued", None, None, None), ("in_progress", None, "2026-10-24T11:40:00Z", None), ("completed", "failure", "2026-10-24T11:40:00Z", "2026-10-24T11:45:00Z")):
            with self.subTest(status=status):
                document = development_document(); document["ci"]["items"][0].update(status=status, conclusion=conclusion, started_at=started, completed_at=completed)
                self.assertEqual(development_project(document)["ci"]["items"][0]["status"], status)

    def test_wrong_project_repository_source_and_provider_fields_refused(self):
        for key, bad in (("project_id", "project-beta"), ("source_id", "source-beta"), ("repository", "example/project-beta"), ("provider", "arbitrary-client"), ("url", "https://private.invalid")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                document = development_document(); document[key] = bad
                development_project(document)

    def test_unconnected_with_data_and_bad_coverage_refused(self):
        mutators = [lambda doc: doc["ci"].update(status="not_connected"),
            lambda doc: doc["issues"].update(complete=1),
            lambda doc: doc["ci"]["items"][0].update(run_id=True),
            lambda doc: doc["issues"]["items"][0].update(number=False),
            lambda doc: doc["ci"]["items"][0].update(status="in_progress"),
            lambda doc: doc["ci"]["items"][0].update(started_at="2026-10-24T11:46:00Z"),
            lambda doc: doc["repository_state"].update(last_commit_at="2026-10-24T11:51:00Z"),
            lambda doc: doc["issues"]["items"].append(copy.deepcopy(doc["issues"]["items"][0])),
            lambda doc: doc["ci"]["items"].append(copy.deepcopy(doc["ci"]["items"][0]))]
        for index, mutate in enumerate(mutators):
            with self.subTest(index=index), self.assertRaises(ValueError):
                document = development_document(); mutate(document); development_project(document)

    def test_secret_path_link_and_identity_canaries_are_rejected(self):
        for title in ("Bearer SYNTHETIC_SECRET", "password=SYNTHETIC_SECRET", "Use /etc/private/file", "C:\\private\\example",
            "Contact someone@example.invalid", "See https://private.invalid", "ghp_SYNTHETIC_SECRET", "API key: SYNTHETIC_SECRET"):
            with self.subTest(title=title), self.assertRaises(ValueError):
                document = development_document(); document["issues"]["items"][0]["title"] = title
                development_project(document)


class BoundReadTests(unittest.TestCase):
    def test_missing_binding_does_no_io(self):
        with patch.object(adapters, "_read_fixed_json", side_effect=AssertionError("unexpected read")):
            for kind in ("backups", "development"):
                value = adapters.read_bound(kind, None, PROJECT, NOW)
                self.assertEqual(value["status"], "not_configured")
                self.assertEqual(value["reason"], "not_configured")

    def test_binding_selects_exact_one_source_and_keeps_requested_project(self):
        with patch.object(adapters, "_read_fixed_json", return_value=backup_document()) as reader:
            value = adapters.read_bound("backups", config(), PROJECT, NOW)
            self.assertEqual(value["status"], "recorded")
            reader.assert_called_once_with("/protected/example-alpha.json")
        with patch.object(adapters, "_read_fixed_json", return_value=backup_document()):
            denied = adapters.read_bound("backups", config(), "project-beta", NOW)
            self.assertEqual(denied["status"], "unavailable")
            self.assertNotIn("project-alpha", json.dumps(denied))

    def test_missing_and_unavailable_files_are_distinct_without_path_leaks(self):
        for error, expected in ((FileNotFoundError("/private/SYNTHETIC-CANARY"), "not_found"),
            (PermissionError("/private/SYNTHETIC-CANARY"), "unavailable"), (ValueError("SYNTHETIC-SECRET"), "unavailable")):
            with self.subTest(expected=expected), patch.object(adapters, "_read_fixed_json", side_effect=error):
                value = adapters.read_bound("backups", config(), PROJECT, NOW)
                self.assertEqual(value["status"], expected)
                self.assertNotIn("SYNTHETIC", json.dumps(value))

    def test_invalid_mapping_refuses_before_read_and_no_arbitrary_arguments(self):
        cases = [dict(config(), path="relative.json"), dict(config(), adapter="shell/v1"), dict(config(), command="anything"),
                 dict(config(), max_age_seconds=True), dict(config(), source_id="../source-alpha"), dict(config(), path="//remote/file")]
        for value in cases:
            with self.subTest(value=value), patch.object(adapters, "_read_fixed_json", side_effect=AssertionError("unexpected IO")):
                self.assertEqual(adapters.read_bound("backups", value, PROJECT, NOW)["status"], "unavailable")

    def test_development_binding_uses_same_pure_projection(self):
        with patch.object(adapters, "_read_fixed_json", return_value=development_document()):
            self.assertEqual(adapters.read_bound("development", config("development"), PROJECT, NOW), development_project(age=86400))


@unittest.skipUnless(os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() == 0, "protected-reader qualification requires isolated Linux root stage")
class ProtectedReaderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="cara-project-adapter-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.root.chmod(0o700)
        self.path = self.root / "source.json"
        self.path.write_text(json.dumps(backup_document()), encoding="utf-8")
        self.path.chmod(0o600)

    def test_protected_fixed_source_reads_and_has_no_side_effects(self):
        before = self.path.read_bytes()
        value = adapters.read_bound("backups", config(path=str(self.path)), PROJECT, NOW)
        self.assertEqual(value["status"], "recorded")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(set(self.root.iterdir()), {self.path})

    def test_leaf_and_parent_symlinks_and_other_writable_files_are_denied(self):
        link = self.root / "link.json"; link.symlink_to(self.path)
        self.assertEqual(adapters.read_bound("backups", config(path=str(link)), PROJECT, NOW)["status"], "unavailable")
        child = self.root / "child"; child.mkdir(mode=0o700)
        (child / "source.json").write_bytes(self.path.read_bytes()); (child / "source.json").chmod(0o600)
        parent_link = self.root / "linked"; parent_link.symlink_to(child, target_is_directory=True)
        self.assertEqual(adapters.read_bound("backups", config(path=str(parent_link / "source.json")), PROJECT, NOW)["status"], "unavailable")
        self.path.chmod(0o666)
        self.assertEqual(adapters.read_bound("backups", config(path=str(self.path)), PROJECT, NOW)["status"], "unavailable")

    def test_fifo_is_rejected_without_blocking_or_consumption(self):
        fifo = self.root / "source.fifo"; os.mkfifo(fifo, 0o600)
        started = time.monotonic()
        self.assertEqual(adapters.read_bound("backups", config(path=str(fifo)), PROJECT, NOW)["status"], "unavailable")
        self.assertLess(time.monotonic() - started, 1)

    def test_duplicate_nonfinite_and_oversize_sources_are_rejected(self):
        for raw in (b'{"schema":1,"schema":2}', b'{"value":NaN}', b'x'*(adapters.MAX_SOURCE_BYTES+1)):
            with self.subTest(length=len(raw)):
                self.path.write_bytes(raw)
                self.assertEqual(adapters.read_bound("backups", config(path=str(self.path)), PROJECT, NOW)["status"], "unavailable")



class OutputValidationTests(unittest.TestCase):
    def test_all_produced_payloads_and_observations_share_strict_validation(self):
        for kind, value in (("backups", backup_project()), ("development", development_project())):
            with self.subTest(kind=kind):
                self.assertIs(adapters.validate_observation(kind, value), value)
                fields = {field: value[field] for field in adapters.ADAPTERS[kind]["outputs"]}
                self.assertIs(adapters.validate_payload(kind, fields, NOW), fields)
        fields = adapters.daily_lingo_health({"status": "ok", "database": "ok"}, 200)
        value = {"status": "observed", "implementation_version": adapters.ADAPTERS["daily_lingo_http"]["implementation_version"], "observed_at": NOW, **fields}
        self.assertIs(adapters.validate_observation("daily_lingo_http", value), value)
        for kind in ("backups", "development"):
            self.assertEqual(adapters.validate_observation(kind, adapters.read_bound(kind, None, PROJECT, NOW))["status"], "not_configured")

    def test_dropped_gaps_injected_data_and_evidence_time_changes_are_refused(self):
        candidates = []
        value = backup_project(age=60); value["gaps"] = {}; candidates.append(("backups", value))
        value = backup_project(); value["restore"]["integrity"] = "unchecked"; candidates.append(("backups", value))
        value = backup_project(); value["backup"]["compressed_bytes"] = True; candidates.append(("backups", value))
        value = backup_project(); value["restore"]["finished_at"] = NOW; candidates.append(("backups", value))
        value = backup_project(); value["secret"] = "SYNTHETIC-CANARY"; candidates.append(("backups", value))
        value = development_project(); value["gaps"] = {}; candidates.append(("development", value))
        value = development_project(); value["repository"]["account_token"] = "SYNTHETIC-CANARY"; candidates.append(("development", value))
        value = development_project(); value["issues"]["complete"] = True; candidates.append(("development", value))
        for kind, value in candidates:
            with self.subTest(kind=kind, keys=set(value)), self.assertRaises(ValueError):
                adapters.validate_observation(kind, value)

    def test_health_missing_metric_gap_cannot_be_silently_replaced_with_success(self):
        fields = adapters.daily_lingo_health({"status": "ok", "database": "ok"}, 200)
        del fields["gaps"]["generation"]
        with self.assertRaises(ValueError):
            adapters.validate_payload("daily_lingo_http", fields, NOW)
        fields = adapters.daily_lingo_health({"status": "ok", "database": "ok"}, 200)
        fields["health"]["target"] = 5
        with self.assertRaises(ValueError):
            adapters.validate_payload("daily_lingo_http", fields, NOW)

if __name__ == "__main__":
    unittest.main()
