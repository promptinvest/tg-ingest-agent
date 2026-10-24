"""Offline nonblocking MCP facts tests; no process, model, DB or host connection."""
import copy
import json
from queue import Queue
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import fleet_facts as facts
import fleet_mcp as protocol
import fleet_mcp_client as client
import fleet_queries as queries
from test_fleet_facts import fixture
from test_fleet_queries import planned
from test_fleet_mcp import adapter, configured, exchange

CANARY = "SYNTHETIC-PRIVATE-CONFIG-CANARY"


class AsyncFactsTests(unittest.TestCase):
    def setUp(self):
        self.document = fixture()
        self.plan = planned(self.document, ["host-a", "host-c"], ["provider", "public_ipv4", "outbound_ipv4"])
        self.cfg = SimpleNamespace(fleet_mcp_enabled=False, fleet_mcp_config="/etc/example-facts/mcp.json", api_token=CANARY)
        self.binding = {"request_id": "a" * 32, "source_hash": "b" * 64,
                        "facts_hash": client.facts_revision_hash(self.document), "policy_version": "cara-fleet-inference/v1"}
        self.slots, self.releases = [], []

    def tearDown(self):
        for release in self.releases:
            release.set()
        end = time.monotonic() + 3
        while any(slot._future is not None for slot in self.slots) and time.monotonic() < end:
            for slot in self.slots:
                slot.settle()
            time.sleep(0.001)
        self.assertTrue(all(slot._future is None for slot in self.slots), "test background work must settle")

    def slot(self):
        slot = client.FactsLookupSlot(); self.slots.append(slot)
        return slot

    def start(self, slot=None, **changes):
        slot = slot or self.slot()
        values = dict(cfg=self.cfg, document=self.document, plan=self.plan, binding=self.binding, lang="en")
        values.update(changes)
        return slot, slot.start(**values)

    def wait_done(self, slot):
        end = time.monotonic() + 2
        while not slot._future.done() and time.monotonic() < end:
            time.sleep(0.001)
        self.assertTrue(slot._future.done())
        # Allow the worker's finally release to complete; no transport is pending.
        time.sleep(0.001)

    def blocked(self, value=None, error=None):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        baseline = value or client.lookup(self.cfg, self.document, self.plan)
        def lookup(_cfg, _document, _plan, _lang="en"):
            entered.set()
            if not release.wait(2):
                raise AssertionError("fixture release missing")
            if error:
                raise error
            return copy.deepcopy(baseline)
        return entered, release, lookup

    def test_start_and_pending_poll_never_wait_for_lookup(self):
        entered, release, slow = self.blocked()
        with patch.object(client, "lookup", side_effect=slow):
            began = time.monotonic(); slot, started = self.start()
            self.assertLess(time.monotonic() - began, 0.25)
            self.assertEqual(started["status"], "started")
            self.assertTrue(entered.wait(1))
            with patch.object(slot._future, "result", side_effect=AssertionError("no premature result wait")):
                began = time.monotonic()
                pending = slot.poll(started["handle"], self.binding, self.document)
                self.assertLess(time.monotonic() - began, 0.25)
                self.assertEqual(pending, {"status": "pending", "settled": False})
            release.set(); self.wait_done(slot)
            self.assertEqual(slot.poll(started["handle"], self.binding, self.document)["status"], "ready")

    def test_disabled_adapter_still_uses_background_and_exact_internal_answer(self):
        expected = client.lookup(self.cfg, self.document, self.plan)
        with patch.object(client, "stdio_exchange", side_effect=AssertionError("disabled means no transport")):
            slot, started = self.start(); self.wait_done(slot)
            result = slot.poll(started["handle"], self.binding, self.document)
        self.assertEqual(result["result"], expected)
        self.assertEqual(result["result"]["path"], "internal")
        self.assertIsNone(slot._input)
        self.assertIsNone(slot._future)

    def test_verified_protocol_answer_matches_same_requested_facts(self):
        self.cfg.fleet_mcp_enabled = True
        config = configured(self.document)
        server = adapter(self.document)
        with patch.object(protocol, "read_config", return_value=config), patch.object(facts, "load", return_value=self.document), patch.object(client, "stdio_exchange", side_effect=lambda _path, messages: exchange(server, messages)):
            slot, started = self.start(); self.wait_done(slot)
            result = slot.poll(started["handle"], self.binding, self.document)["result"]
        self.assertEqual(result["path"], "mcp")
        self.assertEqual(result["cells"], queries.cells(self.document, self.plan))
        self.assertEqual(result["pages"], queries.render(self.document, self.plan))
        outbound = next(cell for cell in result["cells"] if cell["subject_id"] == "host-c" and cell["field"] == "outbound_ipv4")
        self.assertEqual(outbound["evidence_dates"], ["2026-06-01"])
        self.assertEqual(next(cell for cell in result["cells"] if cell["subject_id"] == "host-c" and cell["field"] == "provider")["status"], "unknown")

    def test_snapshot_copies_only_sanitized_cfg_and_immutable_data(self):
        entered, release = threading.Event(), threading.Event(); self.releases.append(release)
        captured = {}
        real_lookup = client.lookup
        def inspect(cfg, document, plan, lang):
            entered.set(); release.wait(2)
            captured.update(cfg=cfg, document=document, plan=plan, lang=lang)
            return real_lookup(cfg, document, plan, lang)
        original_doc, original_plan = copy.deepcopy(self.document), copy.deepcopy(self.plan)
        original_binding = dict(self.binding)
        with patch.object(client, "lookup", side_effect=inspect):
            slot, started = self.start(); self.assertTrue(entered.wait(1))
            self.cfg.api_token = "changed"; self.cfg.fleet_mcp_enabled = True; self.cfg.fleet_mcp_config = "/etc/changed.json"
            self.document["revision"] += 1; self.plan["fields"].append("purpose"); self.binding["source_hash"] = "c" * 64
            release.set(); self.wait_done(slot)
            result = slot.poll(started["handle"], original_binding, original_doc)
        self.assertEqual(result["status"], "ready")
        self.assertFalse(captured["cfg"].fleet_mcp_enabled)
        self.assertEqual(captured["cfg"].fleet_mcp_config, "/etc/example-facts/mcp.json")
        self.assertFalse(hasattr(captured["cfg"], "api_token"))
        with self.assertRaises(AttributeError):
            captured["cfg"].fleet_mcp_enabled = True
        self.assertEqual(captured["document"], original_doc)
        self.assertEqual(captured["plan"], queries.validate_plan(dict(original_plan, scope="explicit"), original_doc))
        self.assertNotIn(CANARY, json.dumps(result))

    def test_global_background_admission_has_no_queued_second_job(self):
        entered, release, slow = self.blocked()
        with patch.object(client, "lookup", side_effect=slow) as lookup:
            first, started = self.start(); self.assertTrue(entered.wait(1))
            second, busy = self.start()
            self.assertEqual(busy, {"status": "busy", "reason": "equivalent_internal_facts_required"})
            self.assertIsNone(second._future)
            self.assertEqual(lookup.call_count, 1)
            release.set(); self.wait_done(first)
            first.poll(started["handle"], self.binding, self.document)

    def test_completed_unconsumed_slot_cannot_be_overwritten(self):
        slot, started = self.start(); self.wait_done(slot)
        self.assertEqual(slot.start(self.cfg, self.document, self.plan, self.binding)["status"], "busy")
        self.assertEqual(slot.poll(started["handle"], self.binding, self.document)["status"], "ready")

    def test_busy_internal_fallback_preserves_exact_obligations_without_stdio(self):
        self.cfg.fleet_mcp_enabled = True
        config = configured(self.document)
        with patch.object(protocol, "read_config", return_value=config), patch.object(facts, "load", return_value=self.document), patch.object(client, "stdio_exchange", side_effect=AssertionError("no busy-path transport")):
            value = client.internal_fallback(self.cfg, self.document, self.plan)
        self.assertEqual(value["path"], "internal")
        self.assertEqual(value["cells"], queries.cells(self.document, self.plan))
        self.assertEqual(value["pages"], queries.render(self.document, self.plan))

    def test_busy_fallback_never_bypasses_known_scope_denial(self):
        self.cfg.fleet_mcp_enabled = True
        with patch.object(protocol, "read_config", return_value=configured(self.document, ["project-alpha"])), patch.object(client, "stdio_exchange", side_effect=AssertionError("no transport")), self.assertRaises(protocol.ScopeDenied):
            client.internal_fallback(self.cfg, self.document, self.plan)

    def test_scope_denial_propagates_only_after_matching_completed_poll(self):
        entered, release, slow = self.blocked(error=protocol.ScopeDenied(CANARY))
        with patch.object(client, "lookup", side_effect=slow):
            slot, started = self.start(); self.assertTrue(entered.wait(1))
            self.assertEqual(slot.poll(started["handle"], self.binding, self.document)["status"], "pending")
            release.set(); self.wait_done(slot)
            with self.assertRaises(protocol.ScopeDenied) as raised:
                slot.poll(started["handle"], self.binding, self.document)
        self.assertNotIn(CANARY, str(raised.exception))
        self.assertIsNone(slot._future)

    def test_interface_outage_returns_equivalent_internal_facts(self):
        self.cfg.fleet_mcp_enabled = True
        with patch.object(protocol, "read_config", return_value=configured(self.document)), patch.object(facts, "load", return_value=self.document), patch.object(client, "stdio_exchange", side_effect=protocol.MCPError("offline")):
            slot, started = self.start(); self.wait_done(slot)
            value = slot.poll(started["handle"], self.binding, self.document)["result"]
        self.assertEqual(value["reason"], "interface_unavailable_or_invalid")
        self.assertEqual(value["cells"], queries.cells(self.document, self.plan))

    def test_unexpected_background_error_has_no_private_text_or_result(self):
        with patch.object(client, "lookup", side_effect=RuntimeError(CANARY)):
            slot, started = self.start(); self.wait_done(slot)
            value = slot.poll(started["handle"], self.binding, self.document)
        self.assertEqual(value, {"status": "unavailable", "reason": "lookup_unavailable", "settled": True})
        self.assertNotIn(CANARY, json.dumps(value))

    def test_cancel_suppresses_late_result_and_retains_live_handle_until_settled(self):
        entered, release, slow = self.blocked()
        with patch.object(client, "lookup", side_effect=slow):
            slot, started = self.start(); self.assertTrue(entered.wait(1))
            with patch.object(slot._future, "result", side_effect=AssertionError("do not await live future")):
                cancelled = slot.cancel(started["handle"])
                self.assertEqual(cancelled, {"status": "suppressed", "reason": "cancelled", "settled": False})
                self.assertEqual(slot.settle(), {"status": "waiting", "pending_calls": 1})
                self.assertIsNotNone(slot._future)
            release.set(); self.wait_done(slot)
            settled = slot.settle()
        self.assertEqual(settled, {"status": "settled", "pending_calls": 0})
        self.assertEqual(slot.poll(started["handle"], self.binding, self.document), {"status": "not_current"})

    def test_purge_hold_suppresses_before_transport_settlement(self):
        entered, release, slow = self.blocked()
        with patch.object(client, "lookup", side_effect=slow):
            slot, started = self.start(); self.assertTrue(entered.wait(1))
            value = slot.poll(started["handle"], self.binding, self.document, purge_hold=True)
            self.assertEqual(value, {"status": "suppressed", "reason": "purge_hold", "settled": False})
            release.set(); self.wait_done(slot)
            final = slot.poll(started["handle"], self.binding, self.document)
        self.assertEqual(final["status"], "suppressed")
        self.assertTrue(final["settled"])
        self.assertNotIn("result", final)

    def test_purge_and_cancel_after_completion_still_prevent_publication(self):
        for action in ("purge", "cancel"):
            with self.subTest(action=action):
                slot, started = self.start(); self.wait_done(slot)
                value = slot.poll(started["handle"], self.binding, self.document, purge_hold=True) if action == "purge" else slot.cancel(started["handle"])
                self.assertEqual(value["status"], "suppressed")
                self.assertTrue(value["settled"])
                self.assertNotIn("result", value)

    def test_source_deletion_or_edit_suppresses_ready_result(self):
        slot, started = self.start(); self.wait_done(slot)
        value = slot.poll(started["handle"], self.binding, self.document, source_current=False)
        self.assertEqual(value["status"], "suppressed")
        self.assertEqual(value["reason"], "source_no_longer_current")
        self.assertNotIn("result", value)

    def test_every_binding_field_change_suppresses_publication(self):
        for field, changed in (("request_id", "c"*32), ("source_hash", "c"*64), ("facts_hash", "c"*64), ("policy_version", "cara-fleet-inference/v2")):
            with self.subTest(field=field):
                slot, started = self.start(); self.wait_done(slot)
                value = slot.poll(started["handle"], dict(self.binding, **{field: changed}), self.document)
                self.assertEqual(value["status"], "suppressed")
                self.assertNotIn("result", value)

    def test_new_facts_revision_and_invalid_document_suppress(self):
        changed = copy.deepcopy(self.document); changed["revision"] += 1; changed = facts.seal(changed)
        for document in (changed, None, {"integrity": "a"*64}):
            slot, started = self.start(); self.wait_done(slot)
            value = slot.poll(started["handle"], self.binding, document)
            self.assertEqual(value["status"], "suppressed")
            self.assertNotIn("result", value)

    def test_changed_source_while_pending_waits_without_late_result(self):
        entered, release, slow = self.blocked()
        with patch.object(client, "lookup", side_effect=slow):
            slot, started = self.start(); self.assertTrue(entered.wait(1))
            value = slot.poll(started["handle"], dict(self.binding, source_hash="c"*64), self.document)
            self.assertEqual(value["status"], "suppressed"); self.assertFalse(value["settled"])
            release.set(); self.wait_done(slot)
            final = slot.poll(started["handle"], self.binding, self.document)
        self.assertEqual(final["status"], "suppressed")
        self.assertTrue(final["settled"])

    def test_stale_handle_cannot_cancel_or_consume_current_work(self):
        slot, started = self.start(); self.wait_done(slot)
        self.assertEqual(slot.cancel("f"*32), {"status": "not_current"})
        self.assertEqual(slot.poll("f"*32, self.binding, self.document), {"status": "not_current"})
        self.assertEqual(slot.poll(started["handle"], self.binding, self.document)["status"], "ready")
        self.assertEqual(slot.poll(started["handle"], self.binding, self.document), {"status": "not_current"})

    def test_slot_reusable_only_after_cancelled_work_settles(self):
        entered, release, slow = self.blocked()
        with patch.object(client, "lookup", side_effect=slow):
            slot, started = self.start(); self.assertTrue(entered.wait(1))
            slot.cancel(started["handle"])
            self.assertEqual(slot.start(self.cfg, self.document, self.plan, self.binding)["status"], "busy")
            release.set(); self.wait_done(slot); slot.settle()
        next_started = slot.start(self.cfg, self.document, self.plan, self.binding)
        self.assertEqual(next_started["status"], "started")
        self.assertNotEqual(next_started["handle"], started["handle"])
        self.wait_done(slot)
        self.assertEqual(slot.poll(next_started["handle"], self.binding, self.document)["status"], "ready")

    def test_binding_shape_types_extra_privilege_and_revision_mismatch_rejected(self):
        for changed in (dict(self.binding, principal="root"), dict(self.binding, request_id="bad"), dict(self.binding, source_hash=True), dict(self.binding, facts_hash="c"*64), dict(self.binding, policy_version="../private")):
            with self.subTest(keys=list(changed)), self.assertRaises(ValueError):
                self.start(binding=changed)

    def test_bad_plan_or_document_never_dispatches_transport(self):
        for value in (dict(self.plan, subjects=["not-a-subject"]), dict(self.plan, facts_integrity="a"*64)):
            with patch.object(client, "lookup") as lookup, self.assertRaises(ValueError):
                self.start(plan=value)
            lookup.assert_not_called()
        bad = copy.deepcopy(self.document); bad["revision"] += 1
        with self.assertRaises(ValueError):
            self.start(document=bad)

    def test_arbitrary_cfg_objects_and_relative_paths_are_not_copied(self):
        class SecretConfig:
            fleet_mcp_enabled = False
            fleet_mcp_config = "/etc/example-facts/mcp.json"
            def __deepcopy__(self, _memo):
                raise AssertionError("must not copy whole cfg")
            @property
            def api_token(self):
                raise AssertionError("must not access unrelated credential")
        slot, started = self.start(cfg=SecretConfig()); self.wait_done(slot)
        self.assertEqual(slot.poll(started["handle"], self.binding, self.document)["status"], "ready")
        for path in ("relative.json", "http://example.invalid/config", "/etc/example\nprivate"):
            with self.assertRaises(ValueError):
                self.start(cfg=SimpleNamespace(fleet_mcp_enabled=True, fleet_mcp_config=path))

    def test_fabricated_background_cells_pages_or_metadata_cannot_publish(self):
        base = client.lookup(self.cfg, self.document, self.plan)
        for value in (dict(base, pages=["Invented provider " + CANARY]), dict(base, cells=[]), dict(base, path="other"), dict(base, extra=CANARY)):
            with patch.object(client, "lookup", return_value=value):
                slot, started = self.start(); self.wait_done(slot)
                outcome = slot.poll(started["handle"], self.binding, self.document)
            self.assertEqual(outcome["status"], "unavailable")
            self.assertNotIn("result", outcome)
            self.assertNotIn(CANARY, json.dumps(outcome))

    def test_source_and_purge_states_require_actual_booleans(self):
        slot, started = self.start(); self.wait_done(slot)
        with self.assertRaises(ValueError):
            slot.poll(started["handle"], self.binding, self.document, source_current=1)
        with self.assertRaises(ValueError):
            slot.poll(started["handle"], self.binding, self.document, purge_hold="false")
        self.assertEqual(slot.poll(started["handle"], self.binding, self.document)["status"], "ready")

    def test_wrong_owner_thread_cannot_start_poll_cancel_or_settle(self):
        slot = self.slot(); errors = Queue()
        def misuse():
            for call in (lambda: slot.start(self.cfg, self.document, self.plan, self.binding), lambda: slot.poll("a"*32, self.binding, self.document), lambda: slot.cancel("a"*32), slot.settle):
                try:
                    call()
                except Exception as error:
                    errors.put(error)
        thread = threading.Thread(target=misuse); thread.start(); thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors.qsize(), 4)
        self.assertTrue(all(isinstance(errors.get_nowait(), protocol.MCPError) for _ in range(4)))

    def test_facts_hash_matches_existing_authority_canonical_digest(self):
        import fleet_protocol
        self.assertEqual(client.facts_revision_hash(self.document), fleet_protocol.digest({"revision": self.document["revision"], "integrity": self.document["integrity"]}))

    def test_empty_settlement_has_no_persistence_or_background_job(self):
        slot = self.slot()
        self.assertEqual(slot.settle(), {"status": "settled", "pending_calls": 0})
        self.assertEqual(slot.cancel("a"*32), {"status": "not_current"})
        self.assertIsNone(slot._input)


if __name__ == "__main__":
    unittest.main()