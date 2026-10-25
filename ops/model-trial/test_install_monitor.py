"""Synthetic monitor planning/wrapper checks; never touches real systemd."""
from datetime import datetime, timedelta, timezone
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import install_monitor as monitor


START = datetime(2026, 10, 25, 14, 2, 3, 456789, timezone.utc)


def receipts():
    identity = {"source_revision": monitor.SOURCE, "sha256": "a" * 64,
                "deployment_id": "synthetic", "build_version": "synthetic"}
    common = {"identity": identity, "before_env_sha256": "b" * 64,
              "candidate_env_sha256": "c" * 64, "runtime_hashes": {"llm.py": "d" * 64},
              "budget_limits_usd": [2.0, 12.0]}
    accepted = dict(common, schema="cara.model-trial-acceptance/v1", status="accepted",
                    at=START.isoformat(), observation_until=(START + timedelta(hours=72)).isoformat(),
                    database_restored=False, code_installed=False, operator_messages_sent=0,
                    stopped_tables_preserved=56)
    prepared = dict(common, schema="cara.model-trial-prepared/v1", database_path=str(monitor.DATABASE),
                    tables={"table" + str(i): {} for i in range(56)})
    return accepted, prepared


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "fixed-attempt4"
        self.root.mkdir()
        self.units = Path(self.temp.name) / "systemd"
        self.units.mkdir()
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(monitor, "ROOT", self.root).start()
        mock.patch.object(monitor, "UNIT_DIR", self.units).start()
        mock.patch.object(monitor, "protected", side_effect=self.check_file).start()
        self.accepted, self.prepared = receipts()
        (self.root / "accepted.json").write_bytes(monitor.canonical(self.accepted))
        (self.root / "prepared.json").write_bytes(monitor.canonical(self.prepared))
        self.calls = []

    def check_file(self, path, directory=False):
        self.assertFalse(path.is_symlink())
        self.assertTrue(path.is_dir() if directory else path.is_file())

    def runner(self, args, **kwargs):
        self.calls.append(args)
        self.assertNotIn("shell", kwargs)
        self.assertEqual(kwargs["timeout"], 15)
        return subprocess.CompletedProcess(args, 0)

    def plan(self):
        return monitor.make_plan(self.root, monitor.canonical(self.accepted), monitor.canonical(self.prepared), b"wrapper", b"observer")

    def prepare_tick(self):
        plan = self.plan()
        directory = self.root / "monitor"
        directory.mkdir()
        for name, raw in (("manifest.json", plan["manifest_raw"]), ("install_monitor.py", b"wrapper"),
                          ("observe_trial.py", b"observer")):
            (directory / name).write_bytes(raw)
        (self.root / "observations").mkdir()
        return plan

    def tick(self, plan, now, loader):
        return monitor.tick(self.root, monitor.sha(plan["manifest_raw"]), plan["manifest"]["accepted_sha256"],
                            START, START + timedelta(hours=72), now=now,
                            runner=self.runner, observer_loader=loader)

    def test_only_twelve_absolute_dates_with_final_after_exact_deadline(self):
        dates = self.plan()["manifest"]["calendar_dates"]
        self.assertEqual(len(dates), 12)
        parsed = [datetime.strptime(value, "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc) for value in dates]
        for hours, value in zip(range(6, 73, 6), parsed):
            self.assertGreaterEqual(value, START + timedelta(hours=hours))
            self.assertLess(value, START + timedelta(hours=hours, seconds=1))
        raw = self.plan()["timer_raw"].decode()
        self.assertEqual(raw.count("OnCalendar="), 12)
        self.assertNotIn("OnUnitActiveSec", raw)
        self.assertNotIn("OnBootSec", raw)

    def test_service_is_restrictive_and_writes_only_observation_directory(self):
        raw = self.plan()["service_raw"].decode()
        for required in ("User=root", "ProtectSystem=strict", "ProtectHome=read-only",
                         "RestrictAddressFamilies=AF_UNIX", "CapabilityBoundingSet=\n",
                         "ReadOnlyPaths=" + str(monitor.DATABASE.parent),
                         "ReadWritePaths=" + str(self.root / "observations")):
            self.assertIn(required, raw)
        self.assertEqual(raw.count("ReadWritePaths="), 1)
        self.assertNotIn("/bin/sh", raw)
        self.assertNotIn("OnFailure", raw)

    def test_arbitrary_root_unit_or_extended_period_rejected(self):
        with self.assertRaisesRegex(monitor.MonitorError, "unapproved_trial_root"):
            monitor.make_plan(self.root / "elsewhere", monitor.canonical(self.accepted), monitor.canonical(self.prepared), b"w", b"o")
        with self.assertRaisesRegex(monitor.MonitorError, "unapproved_timer_unit"):
            monitor.stop_timer("tg-ingest-agent.service", runner=self.runner)
        with self.assertRaisesRegex(monitor.MonitorError, "unapproved_systemctl_action"):
            monitor.systemctl(["restart", "tg-ingest-agent.service"], self.runner)
        self.accepted["observation_until"] = (START + timedelta(hours=73)).isoformat()
        with self.assertRaisesRegex(monitor.MonitorError, "exact_72_hour"):
            self.plan()
        self.assertEqual(self.calls, [])

    def test_changed_acceptance_or_database_binding_refused(self):
        self.accepted["candidate_env_sha256"] = "e" * 64
        with self.assertRaisesRegex(monitor.MonitorError, "environment_binding"):
            self.plan()
        self.accepted, self.prepared = receipts()
        self.prepared["database_path"] = "/other/private.sqlite"
        with self.assertRaisesRegex(monitor.MonitorError, "unapproved_database"):
            self.plan()

    def test_final_timer_stopped_when_observer_fails(self):
        plan = self.prepare_tick()

        def loader(*args):
            def failed(*args, **kwargs):
                raise RuntimeError("synthetic observation failure")
            return SimpleNamespace(scheduled_snapshot=failed)

        with self.assertRaisesRegex(RuntimeError, "synthetic observation failure"):
            self.tick(plan, START + timedelta(hours=72), loader)
        self.assertEqual(self.calls, [["/usr/bin/systemctl", "stop", monitor.TIMER]])

    def test_final_timer_stopped_when_receipt_binding_fails(self):
        plan = self.prepare_tick()
        (self.root / "accepted.json").write_text("{}")
        with self.assertRaisesRegex(monitor.MonitorError, "trial_receipt_binding"):
            self.tick(plan, START + timedelta(hours=73), mock.Mock())
        self.assertEqual(self.calls, [["/usr/bin/systemctl", "stop", monitor.TIMER]])

    def test_nonterminal_observation_binds_original_dates_and_never_stops(self):
        plan = self.prepare_tick()
        observer = SimpleNamespace(scheduled_snapshot=mock.Mock(return_value={"status": "written"}))
        self.assertEqual(self.tick(plan, START + timedelta(hours=6), lambda *args: observer), {"status": "written"})
        arguments = observer.scheduled_snapshot.call_args
        self.assertEqual(arguments.args[:3], (monitor.DATABASE, START, self.root / "observations"))
        self.assertEqual(arguments.kwargs["binding_sha256"], plan["manifest"]["accepted_sha256"])
        self.assertEqual(self.calls, [])

    def test_successful_final_stops_only_fixed_timer(self):
        plan = self.prepare_tick()
        observer = SimpleNamespace(scheduled_snapshot=mock.Mock(return_value={"status": "written", "final": True}))
        result = self.tick(plan, START + timedelta(hours=72, seconds=1), lambda *args: observer)
        self.assertTrue(result["final"])
        self.assertEqual(self.calls, [["/usr/bin/systemctl", "stop", monitor.TIMER]])

    def test_dry_run_has_no_writes_or_systemctl(self):
        before = {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        result = monitor.install(self.root, dry_run=True, now=START + timedelta(minutes=1), runner=self.runner)
        after = {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(before, after)
        self.assertEqual(list(self.units.iterdir()), [])
        self.assertEqual(self.calls, [])

    def test_fresh_exclusive_install_preserves_unrelated_units(self):
        unrelated = self.units / "original-monitor.service"
        unrelated.write_bytes(b"unchanged")
        with mock.patch.object(monitor.os, "geteuid", return_value=0, create=True):
            result = monitor.install(self.root, now=START + timedelta(minutes=1), runner=self.runner)
        self.assertEqual(result["status"], "installed")
        self.assertEqual(unrelated.read_bytes(), b"unchanged")
        self.assertEqual(self.calls, [["/usr/bin/systemctl", "daemon-reload"],
                                     ["/usr/bin/systemctl", "enable", "--now", monitor.TIMER]])
        original = (self.units / monitor.SERVICE).read_bytes()
        with mock.patch.object(monitor.os, "geteuid", return_value=0, create=True), \
             self.assertRaisesRegex(monitor.MonitorError, "already_exists"):
            monitor.install(self.root, now=START + timedelta(minutes=2), runner=self.runner)
        self.assertEqual((self.units / monitor.SERVICE).read_bytes(), original)

    def test_cli_unexpected_failure_never_discloses_private_text(self):
        private = "PRIVATE-CONTENT-MUST-NOT-LEAVE-WRAPPER"
        output = io.StringIO()
        with mock.patch.object(monitor, "install", side_effect=RuntimeError(private)), redirect_stdout(output):
            result = monitor.main(["install", "--root", str(self.root), "--dry-run"])
        self.assertEqual(result, 2)
        self.assertNotIn(private, output.getvalue())

    def test_expired_install_does_not_create_any_units(self):
        with self.assertRaisesRegex(monitor.MonitorError, "outside_trial_window"):
            monitor.install(self.root, dry_run=True, now=START + timedelta(hours=72), runner=self.runner)
        self.assertEqual(list(self.units.iterdir()), [])
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
