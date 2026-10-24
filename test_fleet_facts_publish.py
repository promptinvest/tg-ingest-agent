"""Private-source binding, export, status and local operator-channel regressions."""
import copy
import hashlib
import io
import json
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import fleet_facts as facts
import fleet_facts_publish as publisher


class FactsPublisherTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.raw = self.root / "reviewed-source.md"
        self.raw.write_text("# Reviewed synthetic record\nCANARY_ONLY_IN_RAW_SOURCE\n", encoding="utf-8")
        digest = hashlib.sha256(self.raw.read_bytes()).hexdigest()
        support = {"source_ids": ["source-a"], "evidence_at": "2026-10-20"}
        claim = {**support, "status": "recorded", "supersedes": [], "conflicts": [],
                 "subject_id": "host-a"}
        self.projection = {
            "schema": publisher.PROJECTION_SCHEMA, "reviewed": True,
            "artifact": {"schema": facts.SCHEMA, "fixture_only": True, "revision": 1,
                         "generated_at": "2026-10-24T08:05:00Z",
                         "sources": [{"id": "source-a", "kind": "synthetic", "status": "accepted",
                                      "recorded_at": None, "checked_at": "2026-10-24T08:00:00Z",
                                      "sha256": digest}],
                         "subjects": [{"id": "host-a", "kind": "host", "label": "Example host", **support}],
                         "facts": [{**claim, "fact_id": "host-a-life", "predicate": "lifecycle", "value": "active"},
                                   {**claim, "fact_id": "host-a-provider", "predicate": "provider", "value": "Example Provider"}],
                         "relations": [], "completeness": []}}
        self.register = {"schema": publisher.REGISTER_SCHEMA, "sources": [
            {"id": "source-a", "path": str(self.raw), "sha256": digest,
             "checked_at": "2026-10-24T08:00:00Z"}]}

    def production_shape(self):
        # Synthetic values exercise the production gate without real fleet data.
        self.projection["artifact"]["fixture_only"] = False
        self.projection["artifact"]["sources"][0]["kind"] = "operational-record"

    def files(self):
        projection_path = self.root / "projection.json"
        register_path = self.root / "register.json"
        projection_path.write_text(json.dumps(self.projection), encoding="utf-8")
        register_path.write_text(json.dumps(self.register), encoding="utf-8")
        return projection_path, register_path

    def test_reproducible_export_preserves_all_explicit_dates(self):
        before = copy.deepcopy(self.projection)
        first = publisher.export(self.projection, self.register, allow_fixture=True)
        second = publisher.export(self.projection, self.register, allow_fixture=True)
        self.assertEqual(facts.canonical(first), facts.canonical(second))
        self.assertEqual(self.projection, before)
        self.assertEqual(first["generated_at"], "2026-10-24T08:05:00Z")
        self.assertIsNone(first["sources"][0]["recorded_at"])
        self.assertEqual(first["sources"][0]["checked_at"], "2026-10-24T08:00:00Z")
        self.assertEqual(first["facts"][0]["evidence_at"], "2026-10-20")
        self.assertNotIn("CANARY_ONLY_IN_RAW_SOURCE", facts.canonical(first))
        self.assertNotIn(str(self.raw), facts.canonical(first))
        facts.validate(first, allow_fixture=True)

    def test_source_change_is_pending_and_never_becomes_a_fact(self):
        self.raw.write_text("# A changed source requires review\n", encoding="utf-8")
        status = publisher.source_status(self.projection, self.register, allow_fixture=True)
        self.assertFalse(status["ready"])
        self.assertEqual(status["pending_source_changes"], ["source-a"])
        self.assertEqual(status["sources"][0]["state"], "changed")
        self.assertEqual(status["sources"][0]["checked_at"], "2026-10-24T08:00:00Z")
        with self.assertRaises(ValueError):
            publisher.export(self.projection, self.register, allow_fixture=True)

    def test_missing_source_is_unavailable_without_private_path_or_content(self):
        self.raw.unlink()
        status = publisher.source_status(self.projection, self.register, allow_fixture=True)
        self.assertEqual(status["sources"][0]["state"], "unavailable")
        self.assertNotIn(str(self.raw), json.dumps(status))
        self.assertNotIn("CANARY_ONLY_IN_RAW_SOURCE", json.dumps(status))

    def test_fixture_and_unreviewed_projections_fail_closed_by_default(self):
        with self.assertRaises(ValueError):
            publisher.export(self.projection, self.register)
        self.projection["reviewed"] = False
        with self.assertRaises(ValueError):
            publisher.export(self.projection, self.register, allow_fixture=True)

    def test_register_requires_exact_source_version_digest_and_check_binding(self):
        for field, value in (("sha256", "0" * 64), ("checked_at", "2026-10-24T08:01:00Z"),
                             ("id", "unbound-source"), ("path", "relative/private.md")):
            register = copy.deepcopy(self.register)
            register["sources"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                publisher.export(self.projection, register, allow_fixture=True)
        for entries in ([], self.register["sources"] * 2):
            with self.subTest(entries=len(entries)), self.assertRaises(ValueError):
                publisher.export(self.projection, {"schema": publisher.REGISTER_SCHEMA,
                                                   "sources": entries}, allow_fixture=True)

    def test_unallowlisted_raw_fields_and_artifact_integrity_are_rejected(self):
        for target, field, value in ((self.projection, "raw_markdown", "CANARY_ONLY_IN_RAW_SOURCE"),
                                     (self.register["sources"][0], "raw", "CANARY_ONLY_IN_RAW_SOURCE"),
                                     (self.projection["artifact"], "integrity", "0" * 64)):
            target[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                publisher.export(self.projection, self.register, allow_fixture=True)
            del target[field]

    def test_hash_limits_fail_closed(self):
        with mock.patch.object(publisher, "MAX_SOURCE_BYTES", 4):
            status = publisher.source_status(self.projection, self.register, allow_fixture=True)
            self.assertEqual(status["sources"][0]["state"], "unavailable")
        with mock.patch.object(publisher, "MAX_TOTAL_BYTES", 4):
            with self.assertRaises(ValueError):
                publisher.export(self.projection, self.register, allow_fixture=True)

    def test_symlink_source_does_not_escape_register_binding(self):
        linked = self.root / "linked.md"
        linked.symlink_to(self.raw)
        self.register["sources"][0]["path"] = str(linked)
        self.assertEqual(publisher.source_status(self.projection, self.register, allow_fixture=True)
                         ["sources"][0]["state"], "unavailable")

    def test_nonroot_cannot_claim_authenticated_installation(self):
        with mock.patch.object(publisher.os, "geteuid", return_value=1000), \
                mock.patch.object(facts, "publish") as publish:
            with self.assertRaises(ValueError):
                publisher.install("/private/projection.json", "/private/register.json", "/private/facts")
            publish.assert_not_called()

    def test_root_install_verifies_all_sources_before_atomic_publisher(self):
        self.production_shape()
        projection_path, register_path = self.files()
        checked = []
        def trusted(path, directory=False):
            checked.append((Path(path), directory))
            return Path(path)
        receipt = {"revision": 1, "previous_revision": 0}
        with mock.patch.object(publisher.os, "geteuid", return_value=0), \
                mock.patch.object(publisher, "_trusted_path", side_effect=trusted), \
                mock.patch.object(facts, "publish", return_value=receipt) as publish:
            self.assertEqual(publisher.install(projection_path, register_path, self.root), receipt)
            self.assertIn((self.raw, False), checked)
            self.assertIn((self.root, True), checked)
            self.assertTrue(publish.call_args.kwargs["authenticated"])
            self.assertEqual(publish.call_args.args[0]["revision"], 1)
            self.raw.write_text("changed after review", encoding="utf-8")
            publish.reset_mock()
            with self.assertRaises(ValueError):
                publisher.install(projection_path, register_path, self.root)
            publish.assert_not_called()

    def test_root_install_cannot_enable_fixture_artifacts(self):
        projection_path, register_path = self.files()
        with mock.patch.object(publisher.os, "geteuid", return_value=0), \
                mock.patch.object(publisher, "_trusted_path", side_effect=lambda p, directory=False: Path(p)), \
                mock.patch.object(facts, "publish") as publish:
            with self.assertRaises(ValueError):
                publisher.install(projection_path, register_path, self.root)
            publish.assert_not_called()

    def test_root_trust_checks_every_ancestor_owner_mode_and_type(self):
        target = Path("/private/cara/projection.json")
        def trusted_info(path):
            mode = stat.S_IFREG | 0o640 if path == target else stat.S_IFDIR | 0o750
            return SimpleNamespace(st_mode=mode, st_uid=0)
        with mock.patch.object(Path, "lstat", autospec=True, side_effect=trusted_info):
            self.assertEqual(publisher._trusted_path(target), target)
        for bad in (SimpleNamespace(st_mode=stat.S_IFDIR | 0o775, st_uid=0),
                    SimpleNamespace(st_mode=stat.S_IFDIR | 0o750, st_uid=1000),
                    SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_uid=0)):
            def unsafe(path):
                return bad if path == Path("/private") else trusted_info(path)
            with self.subTest(mode=bad.st_mode, owner=bad.st_uid), \
                    mock.patch.object(Path, "lstat", autospec=True, side_effect=unsafe), \
                    self.assertRaises(ValueError):
                publisher._trusted_path(target)

    def test_cli_export_failure_preserves_last_good_export_and_redacts_errors(self):
        self.production_shape()
        projection_path, register_path = self.files()
        output = self.root / "export.json"
        argv = ["export", "--projection", str(projection_path), "--register", str(register_path),
                "--output", str(output)]
        console, errors = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdout", console), mock.patch("sys.stderr", errors):
            self.assertEqual(publisher.main(argv), 0)
            original = output.read_bytes()
            self.raw.write_text("changed CANARY_ONLY_IN_RAW_SOURCE", encoding="utf-8")
            self.assertEqual(publisher.main(argv), 2)
            self.assertEqual(output.read_bytes(), original)
        self.assertNotIn(str(self.raw), console.getvalue() + errors.getvalue())
        self.assertNotIn("CANARY_ONLY_IN_RAW_SOURCE", console.getvalue() + errors.getvalue())
        self.assertEqual(json.loads(console.getvalue())["revision"], 1)

    def test_cli_status_reports_pending_source_without_exporting(self):
        self.production_shape()
        projection_path, register_path = self.files()
        self.raw.unlink()
        console = io.StringIO()
        with mock.patch("sys.stdout", console):
            self.assertEqual(publisher.main(["status", "--projection", str(projection_path),
                                             "--register", str(register_path)]), 3)
        self.assertEqual(json.loads(console.getvalue())["pending_source_changes"], ["source-a"])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["projection.json", "register.json"])


    def test_install_export_requires_only_sealed_artifact_not_private_sources(self):
        self.production_shape()
        artifact = publisher.export(self.projection, self.register)
        exported = self.root / "exported.json"
        exported.write_text(facts.canonical(artifact), encoding="utf-8")
        self.raw.unlink()  # no raw source needs to exist at the receiving host
        receipt = {"revision": 1, "previous_revision": 0}
        with mock.patch.object(publisher.os, "geteuid", return_value=0), \
                mock.patch.object(publisher, "_trusted_path", side_effect=lambda p, directory=False: Path(p)) as trusted, \
                mock.patch.object(facts, "publish", return_value=receipt) as publish:
            self.assertEqual(publisher.install_export(exported, self.root), receipt)
            self.assertEqual(trusted.call_args_list, [mock.call(exported), mock.call(self.root, directory=True)])
            publish.assert_called_once_with(artifact, self.root, authenticated=True)

    def test_install_export_rejects_nonroot_and_fixture_or_tampered_artifact(self):
        exported = self.root / "exported.json"
        exported.write_text(facts.canonical(facts.seal(self.projection["artifact"])), encoding="utf-8")
        with mock.patch.object(publisher.os, "geteuid", return_value=1000), \
                mock.patch.object(facts, "publish") as publish:
            with self.assertRaises(ValueError):
                publisher.install_export(exported, self.root)
            publish.assert_not_called()
        with mock.patch.object(publisher.os, "geteuid", return_value=0), \
                mock.patch.object(publisher, "_trusted_path", side_effect=lambda p, directory=False: Path(p)), \
                mock.patch.object(facts, "publish") as publish:
            with self.assertRaises(ValueError):
                publisher.install_export(exported, self.root)
            self.production_shape()
            artifact = publisher.export(self.projection, self.register)
            artifact["facts"][1]["value"] = "Tampered Provider"
            exported.write_text(facts.canonical(artifact), encoding="utf-8")
            with self.assertRaises(ValueError):
                publisher.install_export(exported, self.root)
            publish.assert_not_called()

    def test_cli_install_export_cannot_accept_private_source_or_authentication_flags(self):
        receipt = {"revision": 2, "previous_revision": 1}
        console, errors = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdout", console), mock.patch("sys.stderr", errors), \
                mock.patch.object(publisher, "install_export", return_value=receipt) as install:
            self.assertEqual(publisher.main(["install-export", "--artifact", "/private/export.json",
                                            "--directory", "/private/facts"]), 0)
            install.assert_called_once_with("/private/export.json", "/private/facts")
            install.reset_mock()
            self.assertEqual(publisher.main(["install-export", "--artifact", "/private/export.json",
                                            "--directory", "/private/facts", "--register", "/private/source.json"]), 2)
            install.assert_not_called()
        self.assertNotIn("/private", console.getvalue() + errors.getvalue())



if __name__ == "__main__":
    unittest.main()
