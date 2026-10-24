"""CF06 real-process crash boundaries for the existing fixed restart runbook.

All effects are event-only doubles. These tests complement, rather than repeat,
test_fleet_resource_lock.FleetResourceLocks.test_known_final_receipt_reconciles_crash_before_hold_settlement,
which proves exact original receipt bytes survive automatic reconciliation.
Remote host ledger preservation is an operator/recovery contract; this suite does
not invent a host restore API or treat the normal application DB backup as one.
"""
import json
import multiprocessing
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone

import fleet_host as host
import fleet_protocol as protocol

UNIT = "fixture-worker.service"
TOKEN = "a" * 32
OTHER_TOKEN = "b" * 32
FINGERPRINT = "c" * 64
EXIT = {"hold_before_token": 81, "token_before_dispatch": 82,
        "effect_before_receipt": 83}


def request(token=TOKEN):
    return {"operation": "restart", "service": UNIT,
            "fingerprint": FINGERPRINT, "token": token,
            "expires": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()}


def crash_at_boundary(ledger, mode, observed, effect_completed, report):
    """Fork child runs real persistence/locking and exits at one durable boundary."""
    host.LEDGER = Path(ledger)
    original_write, original_file, original_fsync = host._resource_write, host._resource_file, os.fsync
    token_name = TOKEN + ".json"
    durable = {"token": False, "ledger": False}

    def fsync(descriptor):
        original_fsync(descriptor)
        target = os.readlink("/proc/self/fd/" + str(descriptor))
        if target == str(Path(ledger) / token_name):
            durable["token"] = True
        elif target == ledger:
            durable["ledger"] = True

    def boundary():
        observed.set()
        os._exit(EXIT[mode])

    def write(directory, name, value):
        result = original_write(directory, name, value)
        if mode == "hold_before_token" and value.get("state") == "executing":
            if (Path(ledger) / token_name).exists():
                raise AssertionError("host token existed before the selected hold boundary")
            boundary()
        return result

    def effect(arguments, timeout=5):
        if arguments != ["/usr/bin/systemctl", "restart", UNIT] or timeout != 20:
            raise AssertionError("restart escaped the existing fixed runbook")
        if not durable["token"] or not durable["ledger"]:
            raise AssertionError("effect boundary preceded durable token consumption")
        if mode == "token_before_dispatch":
            boundary()
        effect_completed.set()
        return ""

    def file(directory, name, flags, mode=0o600):
        if (mode_name == "effect_before_receipt" and name == token_name
                and flags == os.O_WRONLY and effect_completed.is_set()):
            boundary()
        return original_file(directory, name, flags, mode)

    mode_name = mode
    try:
        with mock.patch.object(host, "_resource_write", side_effect=write), \
                mock.patch.object(host, "_resource_file", side_effect=file), \
                mock.patch.object(host.os, "fsync", side_effect=fsync), \
                mock.patch.object(host, "fingerprint", return_value=FINGERPRINT), \
                mock.patch.object(host, "command", side_effect=effect), \
                mock.patch.object(host, "unit_state", return_value={"ActiveState": "active", "InvocationID": "fixture-current"}):
            value = host.restart({"restart_services": [UNIT]}, request())
        report.send(("unexpected_return", value))
    except BaseException as error:
        report.send(("error", type(error).__name__, str(error)))
    finally:
        report.close()


@unittest.skipUnless(os.name == "posix", "process crash boundaries require isolated Linux")
class FleetResourceRecovery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.ledger = self.root / "host-ledger"
        self.context = multiprocessing.get_context("fork")
        self.patch = mock.patch.object(host, "LEDGER", self.ledger)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def paths(self):
        identity = protocol.digest({"operation": "restart", "service": UNIT})
        return (self.ledger / "resources" / (identity + ".lock"),
                self.ledger / "resources" / (identity + ".json"),
                self.ledger / (TOKEN + ".json"))

    def crash(self, mode):
        observed, effect_completed = self.context.Event(), self.context.Event()
        receiving, sending = self.context.Pipe(duplex=False)
        process = self.context.Process(target=crash_at_boundary,
            args=(str(self.ledger), mode, observed, effect_completed, sending))
        process.start()
        sending.close()
        try:
            process.join(5)
            self.assertFalse(process.is_alive(), "synthetic boundary did not terminate")
            if process.exitcode != EXIT[mode] and receiving.poll(.1):
                self.fail("fixture child did not reach the selected boundary: " + repr(receiving.recv()))
            self.assertEqual(process.exitcode, EXIT[mode])
            self.assertTrue(observed.is_set())
        finally:
            if process.is_alive():
                process.terminate()
                process.join(3)
            receiving.close()
        lock, hold, token = self.paths()
        for path in (self.ledger, self.ledger / "resources"):
            metadata = path.lstat()
            self.assertEqual(metadata.st_uid, os.geteuid())
            self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o700)
        for path in (lock, hold):
            metadata = path.lstat()
            self.assertTrue(stat.S_ISREG(metadata.st_mode))
            self.assertEqual(metadata.st_nlink, 1)
            self.assertEqual(metadata.st_uid, os.geteuid())
            self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o600)
        self.assertEqual(json.loads(hold.read_text())["state"], "executing")
        self.assertEqual(json.loads(hold.read_text())["token"], TOKEN)
        return effect_completed.is_set()

    def assert_blocked_without_new_attempt(self, *, consumed):
        lock, hold, token = self.paths()
        hold_bytes = hold.read_bytes()
        lock_inode = lock.stat().st_ino
        self.assertEqual(token.exists(), consumed)
        token_bytes = token.read_bytes() if consumed else None
        if consumed:
            self.assertEqual(json.loads(token_bytes)["state"], "executing")
        with mock.patch.object(host, "fingerprint", side_effect=AssertionError("held outcome collected fingerprint")) as fingerprint, \
                mock.patch.object(host, "command", side_effect=AssertionError("held outcome repeated effect")) as effect, \
                mock.patch.object(host, "unit_state", side_effect=AssertionError("held outcome inspected service")) as observed:
            result = host.restart({"restart_services": [UNIT]}, request(OTHER_TOKEN))
            self.assertEqual(result, {"outcome": "failed", "service": UNIT, "reason": "resource_unknown"})
            protocol.transport_value(result)
            self.assertFalse((self.ledger / (OTHER_TOKEN + ".json")).exists())
            old = host.restart({"restart_services": [UNIT]}, request())
            if consumed:
                self.assertEqual(old, {"outcome": "ambiguous", "reason": "authorization already consumed"})
            else:
                self.assertEqual(old, {"outcome": "failed", "service": UNIT, "reason": "resource_unknown"})
            fingerprint.assert_not_called()
            effect.assert_not_called()
            observed.assert_not_called()
        self.assertEqual(lock.stat().st_ino, lock_inode)
        self.assertEqual(hold.read_bytes(), hold_bytes)
        if consumed:
            self.assertEqual(token.read_bytes(), token_bytes)
        else:
            self.assertFalse(token.exists())

    def test_crash_after_durable_hold_before_token_blocks_new_and_unsent_old_token(self):
        self.assertFalse(self.crash("hold_before_token"))
        self.assert_blocked_without_new_attempt(consumed=False)

    def test_crash_after_token_fsync_before_dispatch_preserves_consumption_and_unknown_hold(self):
        self.assertFalse(self.crash("token_before_dispatch"))
        self.assert_blocked_without_new_attempt(consumed=True)

    def test_crash_after_effect_completion_before_final_receipt_is_unknown_without_replay(self):
        self.assertTrue(self.crash("effect_before_receipt"))
        self.assert_blocked_without_new_attempt(consumed=True)


if __name__ == "__main__":
    unittest.main()
