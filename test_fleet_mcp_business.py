"""Additional MCP business source IDs, with explicit unimplemented lifecycle gaps.

Facts and retained observations use the same production validators and answer
obligations as internal reads. A passing protocol exchange alone is insufficient.
The full disabled baseline suite remains a separate release gate.
"""
import copy
import json
import unittest
from types import SimpleNamespace
from unittest import mock

import cara_fleet
import fleet_facts as facts
import fleet_mcp as mcp
import fleet_mcp_client as client
import fleet_queries as queries
import llm
import test_fleet as fleet_tests
import test_fleet_catalog as catalog_tests
import test_fleet_mcp as existing
import test_fleet_mcp_integration as integration
from test_fleet_business_corpus import MCP, raw
from test_fleet_facts import fixture
from test_fleet_queries import planned


# These are material business gaps, never xfail/passing protocol substitutes.
# Root may retire a gap only after its actual durable application/client path is
# implemented and its complete original source assertions are executed.
UNIMPLEMENTED_MCP_CASES = {
    "mcp-reconnect-no-duplicate": "MCP has no durable investigation_start submission/reconciliation path.",
    "mcp-tasks-optional": "MCP has no base durable start/status/result lifecycle with optional negotiated Tasks.",
    "mcp-cancel-truthful": "MCP has no executing-investigation cancellation/reconciliation path; safe notification rejection is only partial coverage.",
}


class MCPDisabledOriginalBusiness(unittest.TestCase):
    setUp = catalog_tests.CatalogDispatch.setUp
    tearDown = catalog_tests.CatalogDispatch.tearDown
    turn = catalog_tests.CatalogDispatch.turn
    pump = catalog_tests.CatalogDispatch.pump
    reply = catalog_tests.CatalogDispatch.reply
    completion = catalog_tests.CatalogDispatch.completion

    def test_mcp_disabled_original_flows(self):
        case = MCP["mcp-disabled-original-flows"]
        self.assertEqual(case["mcp"], "disabled")
        self.cfg.fleet_mcp_enabled = False
        question = "What is the recorded address of Example application host?"
        plan = queries.validate_plan(raw((["host-a"], ["public_ipv4"], "explicit", "list")), self.facts)
        with mock.patch.object(mcp, "read_config", side_effect=AssertionError("disabled MCP read its configuration")), \
             mock.patch.object(client, "stdio_exchange", side_effect=AssertionError("disabled MCP started a transport")), \
             mock.patch.object(llm, "transport_chat", side_effect=AssertionError("known fact invoked a model")):
            internal = client.lookup(self.cfg, self.facts, plan)
            self.turn(1, question, dispatch=False)
            self.agent.do_fleet(111, "en", question)
            self.assertEqual(self.pump(), "done")
        self.assertEqual(internal["path"], "internal")
        self.assertIn("192.0.2.10", self.sent[-1])
        self.assertFalse((self.root / "spool/requests").exists())
        # This test covers actual fleet dispatch, not all ten baseline flows.
        # notes/reminders/calendar/journals/speech/research/memory/budgets/backup
        # require the unchanged full existing suite with default MCP disabled.


class MCPMeaningAndAuthorityBusiness(unittest.TestCase):
    setUp = existing.MCPFactsClient.setUp
    call = existing.MCPFactsClient.call

    def test_mcp_unsupported_version_schema(self):
        case = MCP["mcp-unsupported-version-schema"]
        self.assertEqual(len(case["faults"]), 3)
        for fault in case["faults"]:
            def wrong(messages):
                responses = existing.exchange(existing.adapter(self.document), messages)
                if fault == "unsupported version":
                    responses[0]["result"]["supportedVersions"] = ["unqualified/v99"]
                elif fault == "wrong schema":
                    responses[2]["result"]["structuredContent"]["schema"] = "unqualified/v99"
                else:
                    responses[2]["result"]["structuredContent"]["cells"][0]["claims"] = []
                return responses
            with self.subTest(fault=fault), mock.patch("socket.create_connection", side_effect=AssertionError("invalid interface broadened into host probes")):
                result = self.call(wrong)
            self.assertEqual(result["path"], "internal")
            self.assertEqual(result["cells"], queries.cells(self.document, self.plan))
            self.assertEqual(result["pages"], queries.render(self.document, self.plan))
            self.assertEqual(result["facts_integrity"], self.document["integrity"])
            self.assertIn("192.0.2.10", "\n".join(result["pages"]))
            self.assertNotIn("CPU", "\n".join(result["pages"]))

    def test_mcp_caller_scope(self):
        case = MCP["mcp-caller-scope"]
        plan = planned(self.document, [case["requested_project"]], ["purpose"])
        config = existing.configured(self.document, case["caller_allowed_projects"])
        with mock.patch.object(mcp, "read_config", return_value=config), \
             mock.patch.object(facts, "load", side_effect=AssertionError("denied target loaded")), \
             mock.patch("socket.create_connection", side_effect=AssertionError("denied target probed")), \
             mock.patch.object(client, "stdio_exchange", side_effect=AssertionError("denied target transported")):
            with self.assertRaises(mcp.ScopeDenied):
                client.lookup(self.cfg, self.document, plan)

    def test_mcp_discovery_injection(self):
        case = MCP["mcp-discovery-injection"]
        advertisement = case["advertisement"]
        def injecting(messages):
            responses = existing.exchange(existing.adapter(self.document), messages)
            responses[0]["result"]["instructions"] = advertisement["instructions"]
            responses[1]["result"]["tools"].append({"name": advertisement["tool"], "inputSchema": {}})
            return responses
        with mock.patch.object(cara_fleet, "execute", side_effect=AssertionError("discovery became execution")), \
             mock.patch("socket.create_connection", side_effect=AssertionError("discovery probed a host")):
            result = self.call(injecting)
        self.assertEqual(result["path"], "internal")
        self.assertEqual(result["cells"], queries.cells(self.document, self.plan))
        self.assertIn("192.0.2.10", "\n".join(result["pages"]))
        self.assertNotIn(advertisement["instructions"], "\n".join(result["pages"]))
        self.assertNotIn(advertisement["tool"], "\n".join(result["pages"]))

    def test_mcp_no_write_surface(self):
        case = MCP["mcp-no-write-surface"]
        server = existing.adapter(self.document)
        tools = server.handle(client.request(1, "tools/list"))["result"]["tools"]
        self.assertEqual([tool["name"] for tool in tools], ["fleet_lookup"])
        self.assertTrue(all(tool["annotations"]["readOnlyHint"] for tool in tools))
        for request in case["requests"]:
            with self.subTest(request=request), mock.patch.object(cara_fleet, "execute") as execute:
                denied = server.handle(client.request(2, "tools/call", name=request, arguments={"approved": True}))
                self.assertIn("error", denied)
                execute.assert_not_called()
        # Existing approved privileged restart semantics remain independently
        # executable and one-shot; the new interface cannot grant authority.
        fleet_tests.FleetContracts.test_executor_never_retries_ambiguous_restart(self)


class MCPRetainedBusiness(unittest.TestCase):
    setUp = existing.MCPRetainedReferences.setUp
    update = existing.MCPRetainedReferences.update
    arguments = existing.MCPRetainedReferences.arguments
    read = existing.MCPRetainedReferences.read

    def test_mcp_project_isolation(self):
        case = MCP["mcp-project-isolation"]
        self.policy["allowed_projects"].append("project-beta")
        self.reader = mcp.RetainedReferences(self.policy, clock=lambda: self.clock)
        self.update(existing.reference_payload(["project-alpha", "project-beta"]))
        value = self.read("project_observe", projects=case["expected"]["project_scope"])
        self.assertEqual(value["scope"]["projects"], ["project-alpha"])
        self.assertEqual([item["project_id"] for item in value["observations"]], ["project-alpha"])
        self.assertNotIn(case["expected"]["forbidden_colocated_project"], facts.canonical(value))
        self.assertEqual(value["observations"][0]["observation"]["observed_at"], existing.REFERENCE_AT)
        self.assertEqual(value["observations"][0]["freshness"], "within_window")
        stale = self.read("project_observe", max_age_seconds=10)
        self.assertEqual(stale["observations"][0]["freshness"], "stale")
        self.assertEqual(stale["observations"][0]["observation"]["observed_at"], existing.REFERENCE_AT)

    def test_mcp_no_hidden_content(self):
        MCP["mcp-no-hidden-content"]
        before_files = set(self.root.iterdir())
        before_tables = self.writer.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        before_bytes = self.path.read_bytes()
        with mock.patch("socket.create_connection", side_effect=AssertionError("retained fetch created transport")), \
             mock.patch.object(client.subprocess, "Popen", side_effect=AssertionError("retained fetch started a process")):
            self.read()
            self.read("investigation_status")
            self.read("project_observe")
        self.assertEqual(self.path.read_bytes(), before_bytes)
        self.assertEqual(set(self.root.iterdir()), before_files)
        self.assertEqual(self.writer.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall(), before_tables)
        existing.MCPRetainedReferences.test_purge_quiescence_and_deleted_ledger_never_recreate_content(self)
        self.assertEqual(self.writer.execute("SELECT count(*) FROM fleet_requests").fetchone()[0], 0)

    def test_cancel_notification_is_only_partial_lifecycle_coverage(self):
        server = existing.adapter(fixture(), references=self.policy)
        before = self.writer.execute("SELECT state FROM fleet_requests").fetchone()[0]
        self.assertIsNone(server.handle({"jsonrpc": "2.0", "method": "notifications/cancelled",
                                        "params": {"requestId": existing.REFERENCE_ID}}))
        self.assertEqual(self.writer.execute("SELECT state FROM fleet_requests").fetchone()[0], before)
        # No claim of confirmed cancellation follows from a notification.
        self.assertEqual(self.read("investigation_status")["state"], before)


class MCPAsyncBusiness(unittest.TestCase):
    setUp = integration.EnabledFactsIntegration.setUp
    tearDown = integration.EnabledFactsIntegration.tearDown
    reply = integration.EnabledFactsIntegration.reply
    turn = integration.EnabledFactsIntegration.turn
    completion = integration.EnabledFactsIntegration.completion
    row = integration.EnabledFactsIntegration.row
    payload = integration.EnabledFactsIntegration.payload

    def test_mcp_async_responsiveness(self):
        self.assertEqual(MCP["mcp-async-responsiveness"]["fake_mcp_delay_seconds"], 90)
        # Hold a real enabled-Agent stdio future with synchronization, rather
        # than sleeping90seconds or substituting a primitive-only proof.
        integration.EnabledFactsIntegration.test_production_loop_handles_owner_stats_and_due_reminder_during_blocked_stdio(self)
        self.assertEqual(self.exchanges, 1)
        self.assertEqual(self.payload(101)["model_calls"], 0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM llm_usage").fetchone()[0], 0)
        self.assertEqual(self.agent._pending_work_poll_timeout(), 2)
        self.assertFalse((self.root / "spool/requests").exists())


class MCPUnimplementedBusiness(unittest.TestCase):
    @unittest.skip(UNIMPLEMENTED_MCP_CASES["mcp-reconnect-no-duplicate"])
    def test_mcp_reconnect_no_duplicate(self):
        pass

    @unittest.skip(UNIMPLEMENTED_MCP_CASES["mcp-tasks-optional"])
    def test_mcp_tasks_optional(self):
        pass

    @unittest.skip(UNIMPLEMENTED_MCP_CASES["mcp-cancel-truthful"])
    def test_mcp_cancel_truthful(self):
        pass


if __name__ == "__main__":
    unittest.main()
