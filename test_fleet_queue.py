"""CF-04 executor queue deadlines, dispatch cancellation and bounded fairness."""
import json
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import cara_fleet
import fleet_queue as queue
import fleet_lifecycle
import fleet_protocol as protocol
import worker_client


def inventory():
    return {"version": protocol.VERSION,
            "hosts": {"host-" + letter: {"label": "Example host " + letter, "role": "Synthetic fixture",
                                           "aliases": [], "restart_services": ["fixture-worker.service"]}
                      for letter in "abcdef"},
            "projects": {"project-alpha": {"label": "Project Alpha", "hosts": ["host-a"],
                                            "recorded_at": "2026-10-24", "notes": "Synthetic placement."}}}


def observation(host):
    return {"host": host, "observed_at": protocol.now(), "resources": {"cpu_percent": 0}}


class FleetQueueLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.spool = self.root / "spool"
        self.config = self.root / "config"
        self.config.mkdir()
        (self.config / "inventory.json").write_text(protocol.canonical(inventory()), encoding="utf-8")
        (self.config / "transport.json").write_text("{}", encoding="utf-8")
        self.cfg = SimpleNamespace(task_worker_spool=str(self.spool))

    def tearDown(self):
        self.tmp.cleanup()

    def lifecycle(self, age=0):
        submitted = datetime.now(timezone.utc) - timedelta(seconds=age)
        return {"version": queue.VERSION, "submitted_at": submitted.isoformat(),
                "queue_expires_at": (submitted + timedelta(seconds=240)).isoformat(),
                "execution_timeout_seconds": 240}

    def request(self, number=1, *, legacy=False, lifecycle=None, operation=None):
        nonce = format(number, "032x")
        operation = operation or {"operation": "inspect", "hosts": list(inventory()["hosts"]),
                                  "facets": ["resources"], "inventory_hash": protocol.digest(inventory())}
        envelope = {"version": 1, "job_id": f"w_{number}_1_{nonce}", "nonce": nonce,
                    "task_id": number, "step_id": 1, "tool": "fleet." + operation["operation"],
                    "input": operation, "input_hash": protocol.digest(operation),
                    "policy_version": protocol.VERSION, "implementation_version": protocol.VERSION}
        if not legacy:
            envelope["lifecycle"] = lifecycle or self.lifecycle()
        worker_client._atomic_publish(self.spool / "requests", envelope["job_id"] + ".json",
                                      protocol.canonical(envelope).encode())
        return envelope

    def result(self, envelope):
        path = self.spool / "results" / (envelope["job_id"] + ".json")
        return json.loads(path.read_text(encoding="utf-8"))

    def run_once(self):
        cara_fleet.run_once(self.root, self.config)

    def test_twenty_retained_terminal_requests_do_not_starve_a_fresh_request(self):
        originals = []
        for number in range(1, 21):
            envelope = self.request(number, lifecycle=self.lifecycle(age=300))
            path = self.spool / "requests" / (envelope["job_id"] + ".json")
            os.utime(path, (number, number))
            originals.append((path, path.read_bytes()))
        with mock.patch.object(cara_fleet, "remote", side_effect=AssertionError("expired work dispatched")):
            self.run_once()
        terminal_bytes = {path.name: (self.spool / "results" / path.name).read_bytes()
                          for path, _ in originals}
        newest = self.request(21)
        os.utime(self.spool / "requests" / (newest["job_id"] + ".json"), (21, 21))
        with mock.patch.object(cara_fleet, "remote", side_effect=lambda transport, host, request: observation(host)) as remote:
            self.run_once()
            self.run_once()
        self.assertEqual(remote.call_count, len(inventory()["hosts"]))
        self.assertEqual(self.result(newest)["status"], "ok")
        for path, original in originals:
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual((self.spool / "results" / path.name).read_bytes(), terminal_bytes[path.name])

    def test_malformed_older_requests_do_not_consume_the_dispatch_batch(self):
        originals = []
        for number in range(1, 21):
            envelope = self.request(number)
            path = self.spool / "requests" / (envelope["job_id"] + ".json")
            path.write_bytes(b"{malformed retained request")
            os.utime(path, (number, number))
            originals.append(path)
        newest = self.request(21)
        os.utime(self.spool / "requests" / (newest["job_id"] + ".json"), (21, 21))
        with mock.patch.object(cara_fleet, "remote", side_effect=lambda transport, host, request: observation(host)) as remote:
            self.run_once()
        self.assertEqual(remote.call_count, len(inventory()["hosts"]))
        self.assertEqual(self.result(newest)["status"], "ok")
        for path in originals:
            self.assertEqual(path.read_bytes(), b"{malformed retained request")
            self.assertFalse((self.spool / "results" / path.name).exists())

    def test_new_terminal_receipts_still_have_a_twenty_request_batch_limit(self):
        envelopes = []
        for number in range(1, 22):
            envelope = self.request(number, lifecycle=self.lifecycle(age=300))
            os.utime(self.spool / "requests" / (envelope["job_id"] + ".json"), (number, number))
            envelopes.append(envelope)
        with mock.patch.object(cara_fleet, "remote", side_effect=AssertionError("expired work dispatched")):
            self.run_once()
            self.assertTrue(all((self.spool / "results" / (value["job_id"] + ".json")).exists()
                                for value in envelopes[:20]))
            self.assertFalse((self.spool / "results" / (envelopes[-1]["job_id"] + ".json")).exists())
            self.run_once()
        self.assertEqual(self.result(envelopes[-1])["error"], "queue_expired")

    def test_executor_readiness_is_exact_immutable_and_source_bound(self):
        self.assertIsNone(queue.readiness(self.cfg))
        with self.assertRaises(queue.QueueError):
            queue.lifecycle(self.cfg)
        receipt = queue.publish_ready(self.spool)
        path = self.spool / "results" / queue.READY_FILE
        before = path.stat().st_mtime_ns
        self.assertEqual(queue.publish_ready(self.spool), receipt)
        self.assertEqual(path.stat().st_mtime_ns, before)
        self.assertEqual(queue.readiness(self.cfg)["contract"], queue.contract())
        lifecycle = queue.lifecycle(self.cfg)
        self.assertEqual(lifecycle["execution_timeout_seconds"], 240)
        tampered = dict(receipt, implementation_hash="0" * 64)
        path.write_text(protocol.canonical(tampered), encoding="utf-8")
        with self.assertRaises(queue.QueueError):
            queue.readiness(self.cfg)

    def test_lifecycle_cannot_increase_queue_or_execution_deadlines(self):
        value = self.lifecycle()
        for update in ({"execution_timeout_seconds": 241}, {"execution_timeout_seconds": True},
                       {"version": "unknown/v99"}, {"extra": "arbitrary command"}):
            with self.subTest(update=update), self.assertRaises(queue.QueueError):
                queue.validate_lifecycle(dict(value, **update))
        value["queue_expires_at"] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        with self.assertRaises(queue.QueueError):
            queue.validate_lifecycle(value)

    def test_started_receipt_binds_nonce_input_lifecycle_and_execution_window(self):
        envelope = self.request()
        with mock.patch.object(cara_fleet, "remote", side_effect=lambda transport, host, request: observation(host)) as remote:
            self.run_once()
        started = queue.poll_started(self.cfg, lifecycle=envelope["lifecycle"], **queue.binding(envelope))
        self.assertEqual(started["state"], "started")
        start = datetime.fromisoformat(started["started_at"])
        expires = datetime.fromisoformat(started["execution_expires_at"])
        self.assertEqual((expires - start).total_seconds(), 240)
        self.assertEqual(remote.call_count, 6)
        self.assertEqual(self.result(envelope)["status"], "ok")
        self.assertEqual(self.result(envelope)["lifecycle"], envelope["lifecycle"])
        wrong = dict(queue.binding(envelope), input_hash="f" * 64)
        with self.assertRaises(queue.QueueError):
            queue.poll_started(self.cfg, lifecycle=envelope["lifecycle"], **wrong)

    def test_waiting_expiry_never_starts_and_is_distinct_from_execution_timeout(self):
        envelope = self.request(lifecycle=self.lifecycle(age=241))
        with mock.patch.object(cara_fleet, "remote") as remote:
            self.run_once()
            self.run_once()
        remote.assert_not_called()
        self.assertIsNone(queue.poll_started(self.cfg, lifecycle=envelope["lifecycle"], **queue.binding(envelope)))
        self.assertEqual(self.result(envelope)["error"], "queue_expired")

    def test_cancellation_marker_stays_inside_caller_writable_requests(self):
        envelope = self.request()
        self.assertTrue(queue.cancel(self.cfg, job_id=envelope["job_id"], nonce=envelope["nonce"]))
        self.assertTrue((self.spool / "requests" / (".cancel-" + envelope["job_id"] + ".json")).exists())
        self.assertFalse((self.spool / "cancel").exists())
        with mock.patch.object(cara_fleet, "remote") as remote:
            self.run_once()
            self.run_once()
        remote.assert_not_called()
        self.assertEqual(self.result(envelope)["status"], "cancelled")
        self.assertIsNone(queue.poll_started(self.cfg, lifecycle=envelope["lifecycle"], **queue.binding(envelope)))

    def test_wrong_nonce_cancel_and_malformed_started_receipt_fail_without_dispatch(self):
        envelope = self.request()
        with self.assertRaises(queue.QueueError):
            queue.cancel(self.cfg, job_id=envelope["job_id"], nonce="f" * 32)
        started = queue.publish_started(self.spool, envelope)
        path = self.spool / "results" / (envelope["job_id"] + ".started.json")
        started["binding"]["nonce"] = "f" * 32
        path.write_text(protocol.canonical(started), encoding="utf-8")
        original = path.read_bytes()
        with mock.patch.object(cara_fleet, "remote") as remote:
            self.run_once()
            self.run_once()
        remote.assert_not_called()
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse((self.spool / "results" / (envelope["job_id"] + ".json")).exists())

    def test_restart_with_unknown_started_read_does_not_execute_again(self):
        envelope = self.request()
        started = queue.publish_started(self.spool, envelope)
        with mock.patch.object(cara_fleet, "remote") as remote:
            self.run_once()
            self.run_once()
        remote.assert_not_called()
        self.assertEqual(self.result(envelope)["error"], "execution_interrupted")
        self.assertEqual(queue.poll_started(self.cfg, lifecycle=envelope["lifecycle"], **queue.binding(envelope)), started)

    def test_cancellation_after_first_three_hosts_prevents_next_host_dispatches(self):
        envelope = self.request()
        barrier = threading.Barrier(3, action=lambda: queue.cancel(self.cfg, job_id=envelope["job_id"]))
        def remote(transport, host, request):
            barrier.wait(timeout=3)
            return observation(host)
        with mock.patch.object(cara_fleet, "remote", side_effect=remote) as calls:
            self.run_once()
        self.assertEqual(calls.call_count, 3)
        self.assertEqual(self.result(envelope)["status"], "cancelled")
        self.assertNotIn("hosts", self.result(envelope)["result"])

    def test_execution_deadline_prevents_next_host_batch_without_queue_mislabel(self):
        envelope = self.request()
        clock = {"now": 100.0}
        barrier = threading.Barrier(3, action=lambda: clock.update(now=340.0))
        def remote(transport, host, request):
            barrier.wait(timeout=3)
            return observation(host)
        with mock.patch.object(queue.time, "monotonic", lambda: clock["now"]), \
                mock.patch.object(cara_fleet, "remote", side_effect=remote) as calls:
            self.run_once()
        self.assertEqual(calls.call_count, 3)
        self.assertEqual(self.result(envelope)["error"], "execution_expired")
        self.assertIsNotNone(queue.poll_started(self.cfg, lifecycle=envelope["lifecycle"], **queue.binding(envelope)))

    def test_eight_six_host_requests_are_fifo_and_keep_global_three_per_host_one(self):
        order = [100, 2, 40, 3, 12, 5, 6, 7]
        envelopes = [self.request(number) for number in order]
        for index, envelope in enumerate(envelopes):
            path = self.spool / "requests" / (envelope["job_id"] + ".json")
            os.utime(path, ns=(1_000_000_000 + index, 1_000_000_000 + index))
        lock, active, maximum, running, seen = threading.Lock(), {}, {"global": 0, "host": 0}, {"count": 0}, []
        barrier = threading.Barrier(3)
        def remote(transport, host, request):
            with lock:
                active[host] = active.get(host, 0) + 1
                running["count"] += 1
                maximum["global"] = max(maximum["global"], running["count"])
                maximum["host"] = max(maximum["host"], active[host])
            try:
                barrier.wait(timeout=3)
                return observation(host)
            finally:
                with lock:
                    active[host] -= 1
                    running["count"] -= 1
        execute = cara_fleet.execute
        def record(catalog, transport, request, root, control=None):
            seen.append(control.envelope["task_id"])
            return execute(catalog, transport, request, root, control=control)
        with mock.patch.object(cara_fleet, "remote", side_effect=remote), \
                mock.patch.object(cara_fleet, "execute", side_effect=record):
            self.run_once()
        self.assertEqual(seen, order)
        self.assertEqual(maximum, {"global": 3, "host": 1})
        self.assertTrue(all(self.result(envelope)["status"] == "ok" for envelope in envelopes))

    def test_started_sidecar_and_consumed_markers_survive_while_request_is_present(self):
        envelope = self.request()
        queue.publish_started(self.spool, envelope)
        result_path = self.spool / "results" / (envelope["job_id"] + ".json")
        worker_client._atomic_publish(result_path.parent, result_path.name,
                                      protocol.canonical(queue.terminal(envelope, "execution_interrupted")).encode())
        sidecar = self.spool / "results" / (envelope["job_id"] + ".started.json")
        consumed = self.root / "consumed" / envelope["nonce"]
        consumed.parent.mkdir()
        consumed.write_text("fixture one-time authority", encoding="utf-8")
        old = time.time() - 2 * 86400
        for path in (result_path, sidecar, consumed):
            os.utime(path, (old, old))
        self.run_once()
        self.assertTrue(result_path.exists())
        self.assertTrue(sidecar.exists())
        (self.spool / "requests" / (envelope["job_id"] + ".json")).unlink()
        self.run_once()
        self.assertFalse(result_path.exists())
        self.assertFalse(sidecar.exists())
        self.assertTrue(consumed.exists())

    def test_legacy_envelope_result_keys_and_remote_limits_remain_exact(self):
        envelope = self.request(legacy=True)
        path = self.spool / "requests" / (envelope["job_id"] + ".json")
        original = path.read_bytes()
        with mock.patch.object(cara_fleet, "remote", side_effect=lambda transport, host, request: observation(host)):
            self.run_once()
        expected = set(envelope) - {"input"} | {"status", "result", "result_hash"}
        self.assertEqual(set(self.result(envelope)), expected)
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse((self.spool / "results" / (envelope["job_id"] + ".started.json")).exists())
        self.assertEqual(queue.MAX_HOST_TRANSPORTS, 3)

    def test_scoped_cancel_between_discovery_and_inspect_prevents_second_remote(self):
        operation = protocol.scoped_inspection(inventory(), ["host-a"], ["project-alpha"], ["release"])
        envelope = self.request(operation=operation)
        def remote(transport, host, request):
            self.assertEqual(request["operation"], "capabilities")
            queue.cancel(self.cfg, job_id=envelope["job_id"])
            scope = request["read_scope"]
            return {"host": host, "observed_at": protocol.now(), "read_version": protocol.READ_VERSION,
                    "read_scope": scope, "capabilities": {"project-alpha": {"release": {
                        "configured": True, "implementation_version": protocol.PROJECT_CAPABILITIES["release"]["implementation_version"]}}}}
        with mock.patch.object(cara_fleet, "remote", side_effect=remote) as calls:
            self.run_once()
        self.assertEqual(calls.call_count, 1)
        self.assertEqual(self.result(envelope)["status"], "cancelled")


    # The original queue cases above remain exact; add legacy/hold edges.
    def restart(self):
        return {"operation": "restart", "host": "host-a", "service": "fixture-worker.service",
                "fingerprint": "a" * 64, "token": "e" * 32,
                "expires": (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat(),
                "inventory_hash": protocol.digest(inventory())}

    def test_legacy_cancelled_read_and_restart_never_dispatch_or_consume_effect_token(self):
        envelopes = [self.request(101, legacy=True), self.request(102, legacy=True, operation=self.restart())]
        for envelope in envelopes:
            queue.cancel(self.cfg, job_id=envelope["job_id"], nonce=envelope["nonce"])
        with mock.patch.object(cara_fleet, "remote") as remote:
            self.run_once(); self.run_once()
        remote.assert_not_called()
        self.assertFalse((self.root / "consumed").exists())
        for envelope in envelopes:
            result = self.result(envelope)
            self.assertEqual(result["status"], "cancelled")
            self.assertEqual(result["result"], {"state": "cancelled"})
            self.assertEqual(set(result), set(envelope) - {"input"} | {"status", "error", "result", "result_hash"})
            self.assertFalse((self.spool / "results" / (envelope["job_id"] + ".started.json")).exists())

    def test_legacy_malformed_cancel_fails_closed_without_overwrite_or_effect(self):
        envelope = self.request(103, legacy=True, operation=self.restart())
        path = self.spool / "requests" / (".cancel-" + envelope["job_id"] + ".json")
        path.write_text(protocol.canonical({"version": queue.VERSION, "job_id": envelope["job_id"], "nonce": "f" * 32}), encoding="utf-8")
        original = path.read_bytes()
        with mock.patch.object(cara_fleet, "remote") as remote:
            self.run_once(); self.run_once()
        remote.assert_not_called()
        self.assertFalse((self.spool / "results" / (envelope["job_id"] + ".json")).exists())
        self.assertFalse((self.root / "consumed").exists())
        self.assertEqual(path.read_bytes(), original)

    def test_uncancelled_legacy_restart_preserves_wire_result_and_single_consumption(self):
        envelope = self.request(104, legacy=True, operation=self.restart())
        original = (self.spool / "requests" / (envelope["job_id"] + ".json")).read_bytes()
        with mock.patch.object(cara_fleet, "remote", return_value={"outcome": "verified"}) as remote:
            self.run_once(); self.run_once()
        self.assertEqual(remote.call_count, 1)
        result = self.result(envelope)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["result"], {"outcome": "verified"})
        self.assertEqual(set(result), set(envelope) - {"input"} | {"status", "result", "result_hash"})
        self.assertEqual((self.spool / "requests" / (envelope["job_id"] + ".json")).read_bytes(), original)
        self.assertEqual((self.root / "consumed" / envelope["input"]["token"]).read_text(), protocol.digest(envelope["input"]))
        self.assertFalse((self.spool / "results" / (envelope["job_id"] + ".started.json")).exists())

    def test_global_purge_hold_stops_next_host_batch_and_ack_waits_for_transports(self):
        self.root = self.root / "cara-fleet"
        self.spool = self.root / "spool"
        self.cfg.task_worker_spool = str(self.spool)
        envelope = self.request(105)
        purge_nonce = "f" * 32
        cfg = SimpleNamespace(fleet_spool=str(self.spool))
        ack = self.spool / "results" / ("purge_" + purge_nonce + ".ack.json")
        barrier = threading.Barrier(3, action=lambda: fleet_lifecycle.request_quiescence(cfg, purge_nonce, {}))
        def remote(transport, host, request):
            barrier.wait(timeout=3)
            self.assertFalse(ack.exists())  # no ACK while this transport is live
            return observation(host)
        with mock.patch.object(cara_fleet, "remote", side_effect=remote) as calls:
            self.run_once()
            self.assertEqual(calls.call_count, 3)
            self.assertFalse(ack.exists())
            self.assertEqual(self.result(envelope)["status"], "cancelled")
            self.assertNotIn("hosts", self.result(envelope)["result"])
            self.run_once()  # next serial safe point, after all transports ended
            self.assertEqual(calls.call_count, 3)
        self.assertEqual(json.loads(ack.read_text())["status"], "quiesced")
        self.assertTrue((self.spool / "requests" / ".purge-all.json").exists())
