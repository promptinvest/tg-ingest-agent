"""Legacy host-token uncertainty remains authoritative without data migration."""
import fcntl
import json
import os
from pathlib import Path
import unittest
from unittest import mock

import fleet_host as host
import fleet_protocol as protocol
import test_fleet_resource_lock as fixtures

UNIT, OTHER = fixtures.UNIT, fixtures.OTHER
OLD = "c" * 32


class FleetLegacyResourceHistory(unittest.TestCase):
    setUp = fixtures.FleetResourceLocks.setUp
    tearDown = fixtures.FleetResourceLocks.tearDown
    restart = fixtures.FleetResourceLocks.restart

    def legacy(self, value, token=OLD, *, raw=False):
        self.ledger.mkdir(mode=0o700, exist_ok=True)
        path = self.ledger / (token + ".json")
        path.write_bytes(value if raw else protocol.canonical(value).encode("utf-8"))
        path.chmod(0o600)
        return path

    def settled(self, unit=UNIT, outcome="succeeded"):
        return {"outcome": outcome, "service": unit,
                "state": {"ActiveState": "active" if outcome == "succeeded" else "failed", "InvocationID": "fixture-old"}}

    def unknown(self):
        value, fingerprint, effect = self.restart()
        self.assertEqual(value, {"outcome": "failed", "service": UNIT, "reason": "resource_unknown"})
        fingerprint.assert_not_called(); effect.assert_not_called()
        self.assertFalse((self.ledger / ("b" * 32 + ".json")).exists())
        self.assertFalse(fixtures.resource_paths(self.ledger)[1].exists(), "legacy evidence must not be migrated or refreshed")
        protocol.transport_value(value)

    def test_legacy_executing_without_modern_hold_blocks_fresh_token_before_fingerprint(self):
        path = self.legacy({"state": "executing", "service": UNIT, "at": "2025-01-01T00:00:00+00:00"})
        before = (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
        self.assertFalse((self.ledger / "resources").exists())
        self.unknown()
        self.assertEqual((path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns), before)

    def test_legacy_ambiguous_without_modern_hold_cannot_be_cleared_by_fresh_fingerprint(self):
        path = self.legacy({"outcome": "ambiguous", "service": UNIT})
        before = path.read_bytes()
        for _ in range(2):
            self.unknown()
        self.assertEqual(path.read_bytes(), before)

    def test_consumed_original_token_retains_ambiguous_duplicate_semantics(self):
        for value in ({"state": "executing", "service": UNIT, "at": protocol.now()},
                      {"outcome": "ambiguous", "service": UNIT}):
            with self.subTest(value=value):
                path = self.legacy(value)
                before = path.read_bytes()
                result, fingerprint, effect = self.restart(fixtures.request(OLD))
                self.assertEqual(result, {"outcome": "ambiguous", "reason": "authorization already consumed"})
                fingerprint.assert_not_called(); effect.assert_not_called()
                self.assertEqual(path.read_bytes(), before)

    def test_closed_known_final_receipts_allow_fresh_sequential_approval_without_rewrite(self):
        old = [self.legacy(self.settled(), "c" * 32),
               self.legacy(self.settled(outcome="failed"), "d" * 32),
               self.legacy({"outcome": "failed", "service": UNIT, "reason": "authorization_expired"}, "e" * 32)]
        before = {path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns) for path in old}
        result, fingerprint, effect = self.restart()
        self.assertEqual(result["outcome"], "succeeded")
        fingerprint.assert_called_once_with(UNIT)
        effect.assert_called_once_with(["/usr/bin/systemctl", "restart", UNIT], timeout=20)
        for path, expected in before.items():
            self.assertEqual((path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns), expected)

    def test_valid_unresolved_other_unit_does_not_block_independent_service(self):
        paths = [self.legacy({"state": "executing", "service": OTHER, "at": protocol.now()}, "c" * 32),
                 self.legacy({"outcome": "ambiguous", "service": OTHER}, "d" * 32)]
        before = {path: path.read_bytes() for path in paths}
        value, fingerprint, effect = self.restart()
        self.assertEqual(value["outcome"], "succeeded")
        fingerprint.assert_called_once_with(UNIT)
        effect.assert_called_once_with(["/usr/bin/systemctl", "restart", UNIT], timeout=20)
        for path, expected in before.items():
            self.assertEqual(path.read_bytes(), expected)
        self.assertFalse(fixtures.resource_paths(self.ledger, OTHER)[1].exists())

    def test_legacy_scan_runs_under_actual_unit_flock_before_fingerprint(self):
        path = self.legacy(self.settled())
        original = host._resource_json
        events = []
        def read(directory, name):
            if name == path.name:
                descriptor = os.open(fixtures.resource_paths(self.ledger)[0], os.O_RDWR)
                try:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    events.append("legacy_checked")
                finally:
                    os.close(descriptor)
            return original(directory, name)
        def fingerprint(unit):
            self.assertEqual(events, ["legacy_checked"])
            return fixtures.FINGERPRINT
        with mock.patch.object(host, "_resource_json", side_effect=read), \
                mock.patch.object(host, "fingerprint", side_effect=fingerprint), \
                mock.patch.object(host, "command", return_value="") as effect, \
                mock.patch.object(host, "unit_state", return_value={"ActiveState": "active", "InvocationID": "fixture-new"}):
            value = host.restart({"restart_services": [UNIT]}, fixtures.request())
        self.assertEqual(value["outcome"], "succeeded"); effect.assert_called_once()

    def test_malformed_nonfinite_duplicate_or_inconsistent_old_evidence_refuses(self):
        values = [b"", b"not-json", b"[]", b'{"service":"fixture-worker.service","number":NaN}',
                  b'{"service":"fixture-worker.service","service":"fixture-other.service"}']
        values.extend(protocol.canonical(value).encode() for value in (
            {"state": "executing", "service": UNIT},
            {"state": "executing", "service": UNIT, "at": "invalid"},
            {"outcome": "ambiguous", "service": UNIT, "extra": "forbidden"},
            {"outcome": "failed", "service": UNIT, "reason": "invented"},
            {"outcome": "succeeded", "service": UNIT, "state": {"ActiveState": "failed", "InvocationID": "fixture-old"}},
            {"outcome": "succeeded", "service": UNIT, "state": {"ActiveState": "active"}},
            {"outcome": "ambiguous", "service": "unknown/service"}))
        for value in values:
            with self.subTest(value=value[:120]):
                path = self.legacy(value, raw=True)
                before = path.read_bytes()
                self.unknown()
                self.assertEqual(path.read_bytes(), before)

    def test_malformed_other_service_evidence_cannot_be_dismissed_as_independent(self):
        path = self.legacy({"outcome": "ambiguous", "service": OTHER, "extra": "unqualified"})
        before = path.read_bytes()
        self.unknown()
        self.assertEqual(path.read_bytes(), before)

    def test_symlink_hardlink_writable_and_fifo_old_tokens_fail_closed(self):
        self.ledger.mkdir(mode=0o700)
        old = self.ledger / (OLD + ".json")
        outside = self.root / "outside"; outside.write_text(protocol.canonical(self.settled())); outside.chmod(0o600)
        for kind in ("symlink", "hardlink", "writable", "fifo"):
            if kind == "symlink": old.symlink_to(outside)
            elif kind == "hardlink": os.link(outside, old)
            elif kind == "fifo": os.mkfifo(old, 0o600)
            else: old.write_text(protocol.canonical(self.settled())); old.chmod(0o666)
            try:
                with self.subTest(kind=kind):
                    before = outside.read_bytes()
                    self.unknown()
                    self.assertEqual(outside.read_bytes(), before)
                    self.assertTrue(old.is_symlink() if kind == "symlink" else old.exists())
            finally:
                old.unlink()

    def test_unknown_filename_or_nested_evidence_refuses_without_removal(self):
        self.ledger.mkdir(mode=0o700)
        for name in ("unqualified.json", "unexpected-directory"):
            path = self.ledger / name
            if name.endswith(".json"): path.write_text("{}"); path.chmod(0o600)
            else: path.mkdir(mode=0o700)
            try:
                with self.subTest(name=name):
                    self.unknown()
                    self.assertTrue(path.exists())
            finally:
                path.unlink() if path.is_file() else path.rmdir()

    def test_more_than_4096_real_entries_refuses_before_new_token_or_fingerprint(self):
        self.ledger.mkdir(mode=0o700)
        raw = protocol.canonical(self.settled()).encode()
        for number in range(4096):
            path = self.ledger / (format(number, "032x") + ".json")
            path.write_bytes(raw); path.chmod(0o600)
        self.unknown()
        self.assertEqual(len(list(self.ledger.glob("*.json"))), 4096)
        self.assertEqual((self.ledger / ("0" * 32 + ".json")).read_bytes(), raw)

    def test_total_4mib_bound_refuses_valid_closed_receipts_below_entry_cap(self):
        self.ledger.mkdir(mode=0o700)
        state = {name: "x" * 100 for name in host.PROPERTIES}
        state.update(ActiveState="failed", InvocationID="fixture-old")
        value = {"outcome": "failed", "service": UNIT, "state": state}
        self.assertTrue(host._resource_completed(value, UNIT))
        raw = protocol.canonical(value).encode()
        count = 4 * 1024 * 1024 // len(raw) + 1
        self.assertLess(count + 1, 4096)
        for number in range(count):
            path = self.ledger / (format(number, "032x") + ".json")
            path.write_bytes(raw); path.chmod(0o600)
        self.assertGreater(sum(path.stat().st_size for path in self.ledger.iterdir()), 4 * 1024 * 1024)
        self.unknown()
        self.assertEqual(len(list(self.ledger.glob("*.json"))), count)

    def test_individual_16kib_bound_refuses_without_reading_unbounded_history(self):
        path = self.legacy(b" " * 16385, raw=True)
        self.unknown()
        self.assertEqual(path.stat().st_size, 16385)


if __name__ == "__main__":
    unittest.main()
