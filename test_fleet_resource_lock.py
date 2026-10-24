"""Real Linux resource locks and durable unknown outcomes; no host effects."""
import fcntl
import json
import multiprocessing
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import fleet_host as host
import fleet_protocol as protocol
import test_fleet as legacy
import worker_client

UNIT = "fixture-worker.service"
OTHER = "fixture-other.service"
FINGERPRINT = "a" * 64


def request(token="b" * 32, unit=UNIT):
    return {"operation": "restart", "service": unit, "fingerprint": FINGERPRINT,
            "token": token, "expires": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()}


def resource_paths(ledger, unit=UNIT):
    identity = protocol.digest({"operation": "restart", "service": unit})
    return ledger / "resources" / (identity + ".lock"), ledger / "resources" / (identity + ".json")


def child_restart(ledger, item, entered, release, output, mode="held"):
    """The child owns an actual flock, but its effect is an event-only stub."""
    host.LEDGER = Path(ledger)
    host.fingerprint = lambda unit: FINGERPRINT
    host.unit_state = lambda unit: {"ActiveState": "active", "InvocationID": "fixture-current"}
    original_write = host._resource_write
    def effect(args, timeout=5):
        if mode == "subordinate":
            pid = os.fork()
            if pid == 0:
                try:
                    # A real subprocess exec closes these CLOEXEC descriptors.
                    # This event-only child performs no exec, so reproduce only
                    # that descriptor boundary before declaring dispatch.
                    for entry in Path("/proc/self/fd").iterdir():
                        try:
                            target = os.readlink(entry)
                            if target == ledger or target.startswith(ledger + "/"):
                                os.close(int(entry.name))
                        except (OSError, ValueError):
                            pass
                    entered.set()
                    release.wait(10)
                finally:
                    os._exit(0)
            output.send(("subordinate", pid))
            os.waitpid(pid, 0)
        else:
            entered.set()
            if not release.wait(10):
                raise subprocess.TimeoutExpired("fixture effect", 10)
        return ""
    def write(directory, name, value):
        if mode == "crash_after_receipt" and value.get("state") == "settled":
            os._exit(83)
        return original_write(directory, name, value)
    host.command = effect
    host._resource_write = write
    try:
        output.send(("result", host.restart({"restart_services": [UNIT, OTHER]}, item)))
    except BaseException as exc:
        output.send(("error", type(exc).__name__))
    finally:
        output.close()


@unittest.skipUnless(os.name == "posix", "resource locks require isolated Linux")
class FleetResourceLocks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ledger = self.root / "ledger"
        self.ctx = multiprocessing.get_context("fork")
        self.children = []
        self.patch = mock.patch.object(host, "LEDGER", self.ledger)
        self.patch.start()

    def tearDown(self):
        for process, release, connection in self.children:
            release.set()
            process.join(3)
            if process.is_alive():
                process.terminate()
                process.join(3)
            connection.close()
        self.patch.stop()
        self.tmp.cleanup()

    def start_child(self, item=None, mode="held"):
        entered, release = self.ctx.Event(), self.ctx.Event()
        receiving, sending = self.ctx.Pipe(duplex=False)
        process = self.ctx.Process(target=child_restart,
            args=(str(self.ledger), item or request(), entered, release, sending, mode))
        process.start()
        sending.close()
        self.children.append((process, release, receiving))
        self.assertTrue(entered.wait(5), "synthetic command did not cross dispatch boundary")
        return process, release, receiving

    def complete_child(self, process, release, connection):
        release.set()
        process.join(5)
        self.assertFalse(process.is_alive())
        self.assertEqual(process.exitcode, 0)
        self.assertTrue(connection.poll(1))
        kind, value = connection.recv()
        self.assertEqual(kind, "result")
        return value

    def restart(self, item=None, **patches):
        with mock.patch.object(host, "fingerprint", return_value=FINGERPRINT) as fingerprint, \
                mock.patch.object(host, "command", return_value="") as effect, \
                mock.patch.object(host, "unit_state", return_value={"ActiveState": "active", "InvocationID": "fixture-current"}):
            result = host.restart({"restart_services": [UNIT, OTHER]}, item or request())
        return result, fingerprint, effect

    def prepare_state(self, value, *, raw=False):
        self.ledger.mkdir(mode=0o700, exist_ok=True)
        directory = self.ledger / "resources"
        directory.mkdir(mode=0o700, exist_ok=True)
        _, path = resource_paths(self.ledger)
        path.write_text(value if raw else protocol.canonical(value), encoding="utf-8")
        path.chmod(0o600)
        return path

    def hold(self, token="c" * 32, **updates):
        return {"version": host.RESOURCE_VERSION, "service": UNIT, "token": token,
                "state": "executing", "at": protocol.now(), **updates}

    def test_same_unit_distinct_token_is_busy_before_fingerprint_or_consumption(self):
        first = self.start_child()
        lock, _ = resource_paths(self.ledger)
        inode = lock.stat().st_ino
        value, fingerprint, effect = self.restart(request("c" * 32))
        self.assertEqual(value, {"outcome": "failed", "service": UNIT, "reason": "resource_busy"})
        fingerprint.assert_not_called(); effect.assert_not_called()
        self.assertFalse((self.ledger / ("c" * 32 + ".json")).exists())
        protocol.transport_value(value)
        self.assertEqual(self.complete_child(*first)["outcome"], "succeeded")
        self.assertEqual(lock.stat().st_ino, inode)
        self.assertEqual(json.loads((self.ledger / ("b" * 32 + ".json")).read_text())["outcome"], "succeeded")

    def test_different_units_are_independent_while_first_command_is_active(self):
        first = self.start_child()
        value, fingerprint, effect = self.restart(request("c" * 32, OTHER))
        self.assertEqual(value["outcome"], "succeeded")
        fingerprint.assert_called_once_with(OTHER)
        effect.assert_called_once_with(["/usr/bin/systemctl", "restart", OTHER], timeout=20)
        self.assertTrue(first[0].is_alive())
        self.assertEqual(self.complete_child(*first)["outcome"], "succeeded")
        self.assertNotEqual(resource_paths(self.ledger)[0], resource_paths(self.ledger, OTHER)[0])

    def test_same_consumed_token_stays_ambiguous_during_and_after_first_attempt(self):
        first = self.start_child()
        original = (self.ledger / ("b" * 32 + ".json")).read_bytes()
        value, fingerprint, effect = self.restart()
        self.assertEqual(value, {"outcome": "ambiguous", "reason": "authorization already consumed"})
        self.assertEqual((self.ledger / ("b" * 32 + ".json")).read_bytes(), original)
        fingerprint.assert_not_called(); effect.assert_not_called()
        self.complete_child(*first)
        value, fingerprint, effect = self.restart()
        self.assertEqual(value, {"outcome": "ambiguous", "reason": "authorization already consumed"})
        fingerprint.assert_not_called(); effect.assert_not_called()

    def test_gateway_crash_leaves_unknown_hold_while_subordinate_is_still_active(self):
        process, release, connection = self.start_child(mode="subordinate")
        self.assertTrue(connection.poll(1))
        kind, subordinate = connection.recv()
        self.assertEqual(kind, "subordinate")
        os.kill(subordinate, 0)
        process.terminate(); process.join(3)
        self.assertEqual(process.exitcode, -signal.SIGTERM)
        _, state_path = resource_paths(self.ledger)
        self.assertEqual(json.loads(state_path.read_text())["state"], "executing")
        marker = (self.ledger / ("b" * 32 + ".json")).read_bytes()
        os.kill(subordinate, 0)
        value, fingerprint, effect = self.restart(request("c" * 32))
        self.assertEqual(value["reason"], "resource_unknown")
        fingerprint.assert_not_called(); effect.assert_not_called()
        self.assertFalse((self.ledger / ("c" * 32 + ".json")).exists())
        self.assertEqual((self.ledger / ("b" * 32 + ".json")).read_bytes(), marker)
        release.set()
        again, _, _ = self.restart(request("d" * 32))
        self.assertEqual(again["reason"], "resource_unknown", "elapsed command lifetime cannot reconcile an unknown effect")

    def test_timeout_is_ambiguous_and_blocks_other_tokens_without_replay(self):
        with mock.patch.object(host, "fingerprint", return_value=FINGERPRINT), \
                mock.patch.object(host, "command", side_effect=subprocess.TimeoutExpired("fixture", 20)) as effect, \
                mock.patch.object(host, "unit_state") as observation:
            result = host.restart({"restart_services": [UNIT]}, request())
        self.assertEqual(result["outcome"], "ambiguous")
        effect.assert_called_once(); observation.assert_not_called()
        self.assertEqual(json.loads(resource_paths(self.ledger)[1].read_text())["state"], "unknown")
        value, fingerprint, effect = self.restart(request("c" * 32))
        self.assertEqual(value["reason"], "resource_unknown")
        fingerprint.assert_not_called(); effect.assert_not_called()

    def test_known_final_receipt_reconciles_crash_before_hold_settlement(self):
        process, release, connection = self.start_child(mode="crash_after_receipt")
        release.set(); process.join(5)
        self.assertEqual(process.exitcode, 83)
        self.assertEqual(json.loads(resource_paths(self.ledger)[1].read_text())["state"], "executing")
        first_receipt = (self.ledger / ("b" * 32 + ".json")).read_bytes()
        self.assertEqual(json.loads(first_receipt)["outcome"], "succeeded")
        value, fingerprint, effect = self.restart(request("c" * 32))
        self.assertEqual(value["outcome"], "succeeded")
        fingerprint.assert_called_once(); effect.assert_called_once()
        self.assertEqual((self.ledger / ("b" * 32 + ".json")).read_bytes(), first_receipt)
        self.assertEqual(json.loads(resource_paths(self.ledger)[1].read_text())["state"], "settled")

    def test_fingerprint_runs_inside_acquired_lock_and_drift_never_consumes_token(self):
        observed = []
        def fingerprint(unit):
            descriptor = os.open(resource_paths(self.ledger, unit)[0], os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                observed.append(unit)
            finally:
                os.close(descriptor)
            return "d" * 64
        with mock.patch.object(host, "fingerprint", side_effect=fingerprint), mock.patch.object(host, "command") as effect:
            with self.assertRaisesRegex(ValueError, "fresh approval"):
                host.restart({"restart_services": [UNIT]}, request())
        self.assertEqual(observed, [UNIT]); effect.assert_not_called()
        self.assertFalse((self.ledger / ("b" * 32 + ".json")).exists())
        self.assertFalse(resource_paths(self.ledger)[1].exists())

    def test_expiry_after_fingerprint_is_refused_before_consumption(self):
        with mock.patch.object(protocol, "age", side_effect=[-300, 1]), \
                mock.patch.object(host, "fingerprint", return_value=FINGERPRINT), mock.patch.object(host, "command") as effect:
            with self.assertRaisesRegex(ValueError, "expired"):
                host.restart({"restart_services": [UNIT]}, request())
        effect.assert_not_called()
        self.assertFalse((self.ledger / ("b" * 32 + ".json")).exists())

    def test_expiry_at_last_effect_boundary_is_definite_nonstart_and_settled(self):
        with mock.patch.object(protocol, "age", side_effect=[-300, -300, 1]), \
                mock.patch.object(host, "fingerprint", return_value=FINGERPRINT), mock.patch.object(host, "command") as effect:
            value = host.restart({"restart_services": [UNIT]}, request())
        self.assertEqual(value, {"outcome": "failed", "service": UNIT, "reason": "authorization_expired"})
        effect.assert_not_called()
        self.assertEqual(json.loads(resource_paths(self.ledger)[1].read_text())["state"], "settled")
        self.assertEqual(json.loads((self.ledger / ("b" * 32 + ".json")).read_text()), value)

    def test_invalid_policy_is_refused_before_any_filesystem_access(self):
        candidates = [dict(request(), service="ssh.service"), dict(request(), token="bad"),
                      dict(request(), token=int("8" * 32)), dict(request(), command="forbidden"),
                      dict(request(), expires=protocol.now())]
        for candidate in candidates:
            with self.subTest(candidate=candidate), mock.patch.object(host, "_resource_directory") as filesystem, \
                    mock.patch.object(host, "fingerprint") as fingerprint, mock.patch.object(host, "command") as effect:
                with self.assertRaises(ValueError):
                    host.restart({"restart_services": [UNIT]}, candidate)
                filesystem.assert_not_called(); fingerprint.assert_not_called(); effect.assert_not_called()
        self.assertFalse(self.ledger.exists())

    def test_symlink_ledger_and_resource_directories_are_refused(self):
        target = self.root / "target"; target.mkdir(mode=0o700)
        self.ledger.symlink_to(target, target_is_directory=True)
        with self.assertRaises((OSError, ValueError)):
            self.restart()
        self.ledger.unlink(); self.ledger.mkdir(mode=0o700)
        (self.ledger / "resources").symlink_to(target, target_is_directory=True)
        with self.assertRaises((OSError, ValueError)):
            self.restart()
        self.assertEqual(list(target.iterdir()), [])

    def test_writable_ledger_ancestor_and_resources_are_refused(self):
        self.ledger.mkdir(mode=0o700)
        for selected in (self.ledger, self.root, self.ledger / "resources"):
            if not selected.exists():
                selected.mkdir(mode=0o700)
            original = selected.stat().st_mode & 0o7777
            selected.chmod(0o777)
            try:
                with self.subTest(selected=selected.name), self.assertRaises(ValueError):
                    self.restart()
            finally:
                selected.chmod(original)
        self.assertFalse((self.ledger / ("b" * 32 + ".json")).exists())

    def test_unsafe_lock_files_are_refused_without_following_or_effects(self):
        self.ledger.mkdir(mode=0o700); (self.ledger / "resources").mkdir(mode=0o700)
        lock, _ = resource_paths(self.ledger)
        outside = self.root / "outside"; outside.write_text("unchanged"); outside.chmod(0o600)
        for kind in ("symlink", "hardlink", "writable", "fifo"):
            if kind == "symlink": lock.symlink_to(outside)
            elif kind == "hardlink": os.link(outside, lock)
            elif kind == "fifo": os.mkfifo(lock, 0o600)
            else: lock.write_text(""); lock.chmod(0o666)
            try:
                with self.subTest(kind=kind), self.assertRaises((OSError, ValueError)):
                    self.restart()
                self.assertEqual(outside.read_text(), "unchanged")
                self.assertFalse((self.ledger / ("b" * 32 + ".json")).exists())
            finally:
                lock.unlink()

    def test_unsafe_existing_token_files_cannot_be_consumed_or_replaced(self):
        self.ledger.mkdir(mode=0o700)
        path = self.ledger / ("b" * 32 + ".json")
        outside = self.root / "outside"; outside.write_text("unchanged"); outside.chmod(0o600)
        for kind in ("symlink", "hardlink", "writable", "fifo"):
            if kind == "symlink": path.symlink_to(outside)
            elif kind == "hardlink": os.link(outside, path)
            elif kind == "fifo": os.mkfifo(path, 0o600)
            else: path.write_text("{}"); path.chmod(0o666)
            try:
                with self.subTest(kind=kind), self.assertRaises((OSError, ValueError)):
                    self.restart()
                self.assertEqual(outside.read_text(), "unchanged")
            finally:
                path.unlink()

    def test_unsafe_resource_evidence_stays_unknown_and_is_not_overwritten(self):
        self.ledger.mkdir(mode=0o700); (self.ledger / "resources").mkdir(mode=0o700)
        _, path = resource_paths(self.ledger)
        outside = self.root / "outside"; outside.write_text("unchanged"); outside.chmod(0o600)
        for kind in ("symlink", "hardlink", "writable", "fifo"):
            if kind == "symlink": path.symlink_to(outside)
            elif kind == "hardlink": os.link(outside, path)
            elif kind == "fifo": os.mkfifo(path, 0o600)
            else: path.write_text("{}"); path.chmod(0o666)
            try:
                with self.subTest(kind=kind):
                    value, fingerprint, effect = self.restart()
                    self.assertEqual(value["reason"], "resource_unknown")
                    fingerprint.assert_not_called(); effect.assert_not_called()
                    self.assertEqual(outside.read_text(), "unchanged")
                    self.assertFalse((self.ledger / ("b" * 32 + ".json")).exists())
            finally:
                path.unlink()

    def test_corrupt_duplicate_nonfinite_or_oversize_holds_are_closed(self):
        values = ["not json", "[]", '{"state":"executing","state":"settled"}',
                  '{"number":NaN}', '{"number":Infinity}', "x" * 16385]
        values.extend(protocol.canonical(self.hold(**change)) for change in (
            {"version": "unqualified"}, {"service": OTHER}, {"state": "healthy"},
            {"token": "invalid"}, {"at": "not-a-date"}, {"extra": "forbidden"}))
        for value in values:
            with self.subTest(value=value[:100]):
                path = self.prepare_state(value, raw=True)
                before = path.read_bytes()
                result, fingerprint, effect = self.restart()
                self.assertEqual(result["reason"], "resource_unknown")
                fingerprint.assert_not_called(); effect.assert_not_called()
                self.assertEqual(path.read_bytes(), before)
                self.assertFalse((self.ledger / ("b" * 32 + ".json")).exists())

    def test_corrupt_final_receipts_cannot_reconcile_unknown_effects(self):
        token = "c" * 32
        values = [{"outcome": "succeeded", "service": UNIT},
                  {"outcome": "failed", "service": UNIT, "reason": "invented"},
                  {"outcome": "succeeded", "service": UNIT, "state": "active"},
                  {"outcome": "succeeded", "service": UNIT, "state": {"ActiveState": "active"}, "extra": "forbidden"},
                  {"outcome": "succeeded", "service": OTHER, "state": {"ActiveState": "active"}}]
        values.extend({"outcome": "failed", "service": UNIT,
                       "state": {"ActiveState": invalid, "InvocationID": "fixture-current"}}
                      for invalid in ("", 0, False, None))
        for value in values:
            with self.subTest(value=value):
                self.prepare_state(self.hold(token))
                marker = self.ledger / (token + ".json")
                marker.write_text(protocol.canonical(value)); marker.chmod(0o600)
                result, fingerprint, effect = self.restart()
                self.assertEqual(result["reason"], "resource_unknown")
                fingerprint.assert_not_called(); effect.assert_not_called()
                self.assertFalse((self.ledger / ("b" * 32 + ".json")).exists())


class FleetResourceOwnerMessages(unittest.TestCase):
    setUp = legacy.FleetLifecycle.setUp
    tearDown = legacy.FleetLifecycle.tearDown
    start = legacy.FleetLifecycle.start
    result = legacy.FleetLifecycle.result

    def completed_maintenance(self, reason, lang):
        row = self.result(self.start("restart"))
        payload = json.loads(row["payload"])
        payload["lang"] = lang
        self.conn.execute("UPDATE fleet_requests SET payload=? WHERE id=?", (protocol.canonical(payload), row["id"]))
        self.conn.commit()
        self.agent.handle_fleet_callback("cb", 111, {"message_id": payload["approval_message"]}, "fl|" + row["id"] + "|y")
        with mock.patch.object(worker_client, "poll", return_value={"outcome": "failed", "service": "cara-worker.service", "reason": reason}):
            self.agent.check_fleet_requests()
        current = self.conn.execute("SELECT * FROM fleet_requests WHERE id=?", (row["id"],)).fetchone()
        self.assertEqual(current["state"], "done")
        return json.loads(current["payload"])["answer"], row["id"]

    def test_busy_is_an_honest_definite_nonstart_in_existing_owner_delivery(self):
        answer, request_id = self.completed_maintenance("resource_busy", "en")
        self.assertIn("not started: resource busy", answer)
        self.assertIn("pd / cara-worker.service", answer)
        before = list(self.agent.messages)
        self.agent.check_fleet_requests()
        self.assertEqual(self.agent.messages, before)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_requests WHERE id=?", (request_id,)).fetchone()[0], 1)

    def test_unknown_is_an_honest_reconciliation_gap_in_russian(self):
        answer, _ = self.completed_maintenance("resource_unknown", "ru")
        self.assertIn("\u043d\u0435 \u0437\u0430\u043f\u0443\u0449\u0435\u043d", answer)
        self.assertIn("\u0441\u0432\u0435\u0440\u043a\u0438", answer)
        self.assertIn("pd / cara-worker.service", answer)

    def test_unrecognized_host_reason_is_never_rendered_as_owner_text(self):
        answer, _ = self.completed_maintenance("SYNTHETIC-PRIVATE-DETAIL /operator/path", "en")
        self.assertEqual(answer, "Restart outcome: failed \u00b7 pd / cara-worker.service")
        self.assertNotIn("SYNTHETIC", answer)


if __name__ == "__main__":
    unittest.main()