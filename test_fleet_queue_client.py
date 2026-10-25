"""Main-loop queue/start/result authority with fake clocks and real spool validation."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import fleet
import fleet_protocol as protocol
import fleet_queue as queue
import llm
import store
import worker_client
from test_fleet_async import inventory, plan, response, completion
from testlib import make_config


class Agent(fleet.FleetMixin):
    def __init__(self, cfg, conn):
        self.cfg, self.conn = cfg, conn
        self.fleet_async = True
        self.messages = []
    def reply(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text, kwargs))
        return {"message_id": len(self.messages) + 100}
    def answer_callback(self, *args):
        pass


class FleetExecutorClientLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = make_config(DB_PATH=str(self.root / "agent.db"), CARA_FLEET_ENABLED="true")
        self.cfg.fleet_inventory = str(self.root / "inventory.json")
        self.cfg.fleet_facts = str(self.root / "missing-facts.json")
        self.cfg.fleet_spool = str(self.root / "spool")
        (self.root / "inventory.json").write_text(protocol.canonical(inventory()), encoding="utf-8")
        self.conn = store.open_db(self.cfg.db_path)
        self.agent = Agent(self.cfg, self.conn)
        self.clock = {"now": datetime.now(timezone.utc)}
        clock = self.clock
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock["now"].astimezone(tz) if tz else clock["now"].replace(tzinfo=None)
        self.clock_patches = [mock.patch.object(protocol, "datetime", Clock), mock.patch.object(queue, "datetime", Clock)]
        for patch in self.clock_patches:
            patch.start()
        self.transport = mock.patch.object(llm, "transport_chat", return_value=completion(plan()))
        self.send = self.transport.start()

    def tearDown(self):
        for call in getattr(self.agent, "_fleet_inference_calls", {}).values():
            if call.future is not None:
                try:
                    call.future.result(timeout=3)
                except Exception:
                    pass
        self.transport.stop()
        for patch in reversed(self.clock_patches):
            patch.stop()
        self.conn.close()
        self.tmp.cleanup()

    def row(self, update=1):
        return self.conn.execute("SELECT * FROM fleet_requests WHERE source_update=?", (update,)).fetchone()

    def payload(self, update=1):
        return json.loads(self.row(update)["payload"])

    def start(self, update=1, maintenance=False):
        text = "Check the fixture service now" if maintenance else "Check the fixture memory now"
        incoming = {"update_id": update, "message": {"chat": {"id": 111}, "message_id": update + 10, "text": text}}
        store.telegram_update_receive(self.conn, incoming, chat_id=111)
        store.convo_add(self.conn, 111, "user", text, source="boss", update_id=update, tg_message_id=update + 10)
        self.agent._current_update_id = update
        self.agent.do_fleet(111, "en", text, allow_maintenance=maintenance)
        for _ in range(10):
            if self.row(update)["state"] in {"queued", "failed", "ambiguous"}:
                return
            self.agent.check_fleet_requests()
            for call in getattr(self.agent, "_fleet_inference_calls", {}).values():
                if call.future is not None:
                    try:
                        call.future.result(timeout=3)
                    except Exception:
                        pass
        self.fail("fixture planning did not reach executor boundary")

    def ready(self):
        return queue.publish_ready(self.root / "spool")

    def envelope(self, update=1):
        payload = self.payload(update)
        return json.loads((self.root / "spool" / "requests" / (payload["binding"]["job_id"] + ".json")).read_text(encoding="utf-8"))

    def publish_result(self, envelope, result):
        receipt = {key: value for key, value in envelope.items() if key != "input"}
        receipt.update(status="ok", result=result, result_hash=protocol.digest(result))
        worker_client._atomic_publish(self.root / "spool" / "results", envelope["job_id"] + ".json",
                                      protocol.canonical(receipt).encode())

    def publish_terminal(self, envelope, state):
        worker_client._atomic_publish(self.root / "spool" / "results", envelope["job_id"] + ".json",
                                      protocol.canonical(queue.terminal(envelope, state)).encode())

    def cancel_marker(self, envelope):
        return self.root / "spool" / "requests" / (".cancel-" + envelope["job_id"] + ".json")

    def test_missing_readiness_preserves_legacy_envelope_and_exact_binding(self):
        self.start()
        envelope = self.envelope()
        self.assertNotIn("lifecycle", envelope)
        self.assertNotIn("executor_lifecycle", self.payload())
        self.assertEqual(set(envelope), {"version", "job_id", "nonce", "task_id", "step_id", "tool",
                                         "input", "input_hash", "policy_version", "implementation_version"})
        self.assertEqual(self.payload()["executor_binding"], queue.binding(envelope))

    def test_exact_readiness_enables_lifecycle_and_persists_before_publication(self):
        ready = self.ready()
        submit = fleet.submit
        observed = []
        def inspect_before_send(cfg, update, step, operation, **kwargs):
            payload = self.payload(update)
            self.assertEqual(payload["binding"], kwargs["binding"])
            self.assertEqual(payload["executor_lifecycle"], kwargs["lifecycle"])
            self.assertEqual(payload["executor_binding"]["input_hash"], protocol.digest(operation))
            observed.append(payload["source_hash"])
            return submit(cfg, update, step, operation, **kwargs)
        with mock.patch.object(fleet, "submit", side_effect=inspect_before_send):
            self.start()
        self.assertEqual(len(observed), 1)
        envelope = self.envelope()
        self.assertEqual(envelope["lifecycle"], self.payload()["executor_lifecycle"])
        self.assertEqual(self.payload()["executor_contract"]["implementation_hash"], ready["implementation_hash"])
        self.assertEqual(self.payload()["executor_contract"]["pid"], ready["pid"])

    def test_malformed_readiness_cannot_downgrade_to_legacy_send(self):
        ready = self.ready()
        ready["implementation_hash"] = "f" * 64
        (self.root / "spool" / "results" / queue.READY_FILE).write_text(protocol.canonical(ready), encoding="utf-8")
        with mock.patch.object(fleet, "submit") as submit:
            self.start()
        submit.assert_not_called()
        self.assertEqual(self.row()["state"], "failed")
        self.assertEqual(self.send.call_count, 1)

    def test_late_queue_start_gets_its_own_execution_deadline(self):
        self.ready()
        self.start()
        envelope = self.envelope()
        self.clock["now"] += timedelta(seconds=230)
        started = queue.publish_started(self.root / "spool", envelope)
        self.agent.check_fleet_requests()
        self.clock["now"] += timedelta(seconds=20)
        self.agent.check_fleet_requests()
        self.assertEqual(self.row()["state"], "inspection_started")
        self.assertEqual(self.payload()["executor_started"], started)
        self.assertNotIn("executor_status", self.payload())
        self.assertFalse(self.cancel_marker(envelope).exists())
        self.assertEqual(self.send.call_count, 1)

    def test_queue_deadline_requests_cancel_without_claiming_execution(self):
        self.ready()
        with mock.patch.object(fleet, "submit", wraps=fleet.submit) as submit:
            self.start()
            envelope = self.envelope()
            self.clock["now"] += timedelta(seconds=241)
            self.agent.check_fleet_requests()
            self.agent.check_fleet_requests()
        self.assertEqual(submit.call_count, 1)
        self.assertEqual(self.row()["state"], "failed")
        self.assertEqual(self.payload()["executor_status"]["state"], "queue_timeout")
        self.assertNotIn("executor_started", self.payload())
        self.assertTrue(self.cancel_marker(envelope).exists())
        self.assertIn("waiting deadline without a confirmed start", self.agent.messages[-1][1])

    def test_execution_deadline_uses_started_receipt_and_late_result_cannot_revive(self):
        self.ready()
        self.start()
        envelope = self.envelope()
        self.clock["now"] += timedelta(seconds=230)
        queue.publish_started(self.root / "spool", envelope)
        self.agent.check_fleet_requests()
        self.clock["now"] += timedelta(seconds=241)
        self.agent.check_fleet_requests()
        self.assertEqual(self.payload()["executor_status"]["state"], "execution_timeout")
        self.assertEqual(self.row()["state"], "failed")
        before = len(self.agent.messages)
        self.publish_result(envelope, response())
        self.agent.check_fleet_requests()
        self.assertEqual(self.row()["state"], "failed")
        self.assertEqual(len(self.agent.messages), before)
        self.assertEqual(self.send.call_count, 1)
        self.assertTrue(self.cancel_marker(envelope).exists())

    def test_verified_queue_expiry_is_specific_and_still_uses_worker_validation(self):
        self.ready()
        self.start()
        envelope = self.envelope()
        self.publish_terminal(envelope, "queue_expired")
        with mock.patch.object(worker_client, "poll", wraps=worker_client.poll) as poll:
            self.agent.check_fleet_requests()
        self.assertEqual(poll.call_count, 1)
        self.assertEqual(self.payload()["executor_status"]["state"], "queue_expired")
        self.assertIn("expired while waiting", self.agent.messages[-1][1])
        self.assertEqual(self.row()["state"], "failed")

    def test_started_interrupted_receipt_never_causes_a_resubmission(self):
        self.ready()
        with mock.patch.object(fleet, "submit", wraps=fleet.submit) as submit:
            self.start()
            envelope = self.envelope()
            queue.publish_started(self.root / "spool", envelope)
            self.publish_terminal(envelope, "execution_interrupted")
            self.agent.check_fleet_requests()
            self.agent.check_fleet_requests()
        self.assertEqual(submit.call_count, 1)
        self.assertEqual(self.payload()["executor_status"]["state"], "execution_interrupted")
        self.assertIn("will not repeat it automatically", self.agent.messages[-1][1])
        self.assertEqual(self.send.call_count, 1)

    def test_wrong_terminal_binding_is_rejected_without_bypassing_worker_checks(self):
        self.ready()
        self.start()
        envelope = self.envelope()
        receipt = queue.terminal(envelope, "queue_expired")
        receipt["nonce"] = "f" * 32
        worker_client._atomic_publish(self.root / "spool" / "results", envelope["job_id"] + ".json",
                                      protocol.canonical(receipt).encode())
        with mock.patch.object(worker_client, "poll", wraps=worker_client.poll) as poll:
            self.agent.check_fleet_requests()
        self.assertEqual(poll.call_count, 1)
        self.assertEqual(self.row()["state"], "failed")
        self.assertNotIn("executor_status", self.payload())
        self.assertTrue(self.cancel_marker(envelope).exists())
        self.assertNotIn("expired while waiting", self.agent.messages[-1][1])

    def test_source_edit_publishes_cancel_before_terminal_and_suppresses_reply(self):
        self.ready()
        self.start()
        envelope = self.envelope()
        self.conn.execute("UPDATE conversation SET text='Changed owner request' WHERE update_id=1")
        self.conn.commit()
        before = len(self.agent.messages)
        save = self.agent._fleet_save
        checks = []
        def assert_cancel_first(row, state, payload, event):
            if state == "failed":
                checks.append(self.cancel_marker(envelope).exists())
            return save(row, state, payload, event)
        with mock.patch.object(self.agent, "_fleet_save", side_effect=assert_cancel_first):
            self.agent.check_fleet_requests()
        self.assertEqual(checks, [True])
        self.assertEqual(self.row()["state"], "failed")
        self.assertEqual(len(self.agent.messages), before)
        marker = json.loads(self.cancel_marker(envelope).read_text())
        self.assertEqual(marker["nonce"], envelope["nonce"])

    def test_authority_revision_cancels_queued_execution_and_sends_only_one_final_status(self):
        self.ready()
        self.start()
        envelope = self.envelope()
        before = len(self.agent.messages)
        with mock.patch.object(self.agent, "_fleet_facts_revision", return_value="f" * 64), \
                mock.patch.object(worker_client, "poll", side_effect=AssertionError("stale result consumed")):
            self.agent.check_fleet_requests()
            self.agent.check_fleet_requests()
        self.assertEqual(self.row()["state"], "failed")
        self.assertTrue(self.cancel_marker(envelope).exists())
        self.assertEqual(len(self.agent.messages), before + 1)
        self.assertEqual(self.agent.messages[-1][1], "The check stopped because its configuration changed. No result was delivered; I did not repeat it automatically.")
        self.assertEqual(self.payload()["terminal_status_notice"]["state"], "sent")
        self.assertEqual(self.send.call_count, 1)
        self.assertNotIn("result", self.payload())

    def test_success_without_required_start_receipt_cannot_be_accepted(self):
        self.ready()
        self.start()
        envelope = self.envelope()
        self.publish_result(envelope, response())
        self.agent.check_fleet_requests()
        self.assertEqual(self.row()["state"], "failed")
        self.assertTrue(self.cancel_marker(envelope).exists())
        self.assertNotIn("result", self.payload())
        self.assertEqual(self.send.call_count, 1)

    def test_confirmed_maintenance_keeps_card_binding_and_unknown_deadline_ambiguous(self):
        self.ready()
        restart_plan = dict(plan(), operation="restart", service="fixture-worker.service")
        self.send.return_value = completion(restart_plan)
        self.start(maintenance=True)
        inspect_envelope = self.envelope()
        queue.publish_started(self.root / "spool", inspect_envelope)
        observed = response()
        observed["hosts"]["host-a"]["services"] = {"fixture-worker.service": {
            "ActiveState": "active", "fingerprint": "a" * 64}}
        self.publish_result(inspect_envelope, observed)
        self.agent.check_fleet_requests()
        self.assertEqual(self.row()["state"], "approval")
        row = self.row()
        approval = self.payload()["approval_message"]
        self.agent.handle_fleet_callback("fixture-callback", 111, {"message_id": approval}, "fl|" + row["id"] + "|y")
        self.assertEqual(self.row()["state"], "executing")
        restart_envelope = self.envelope()
        self.assertEqual(restart_envelope["input"]["operation"], "restart")
        self.assertEqual(restart_envelope["step_id"], 2)
        self.clock["now"] += timedelta(seconds=241)
        self.agent.check_fleet_requests()
        self.assertEqual(self.row()["state"], "ambiguous")
        self.assertEqual(self.payload()["executor_status"]["state"], "queue_timeout")
        self.assertTrue(self.cancel_marker(restart_envelope).exists())
