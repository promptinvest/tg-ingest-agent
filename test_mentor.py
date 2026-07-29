#!/usr/bin/env python3
"""Adversarial tests for Cara's separate Mentor and candidate runner."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import cara_mentor
import improvement
import mentor_client
import mentor_protocol as protocol
import mentor_runner
import store


def valid_patch():
    return """diff --git a/router.py b/router.py
--- a/router.py
+++ b/router.py
@@ -1,1 +1,1 @@
-#!/usr/bin/env python3
+#!/usr/bin/env python3
diff --git a/test_mentor_candidates.py b/test_mentor_candidates.py
--- a/test_mentor_candidates.py
+++ b/test_mentor_candidates.py
@@ -1,1 +1,1 @@
-#!/usr/bin/env python3
+#!/usr/bin/env python3
"""


class MentorProtocolTests(unittest.TestCase):
    def test_candidate_requires_behavior_and_dedicated_test(self):
        with self.assertRaises(protocol.MentorProtocolError):
            protocol.validate_target_files(["router.py", "texts.py"])
        self.assertEqual(
            protocol.validate_target_files(
                ["router.py", "test_mentor_candidates.py"]),
            ["router.py", "test_mentor_candidates.py"],
        )

    def test_patch_preserves_whitespace_and_exact_bound_paths(self):
        patch = valid_patch()
        self.assertEqual(
            protocol.validate_patch(
                patch, ["router.py", "test_mentor_candidates.py"]),
            patch,
        )
        attack = patch.replace(
            "diff --git a/router.py b/router.py",
            "diff --git a/../../etc/passwd b/../../etc/passwd",
            1,
        )
        with self.assertRaises(protocol.MentorProtocolError):
            protocol.validate_patch(
                attack, ["router.py", "test_mentor_candidates.py"])

    def test_patch_cannot_change_topology_or_security_files(self):
        with self.assertRaises(protocol.MentorProtocolError):
            protocol.validate_target_files(
                ["tool_broker.py", "test_mentor_candidates.py"])
        with self.assertRaises(protocol.MentorProtocolError):
            protocol.validate_patch(
                valid_patch() + "\nnew file mode 100644\n",
                ["router.py", "test_mentor_candidates.py"])
        malicious = valid_patch().replace(
            "+#!/usr/bin/env python3",
            "+import subprocess\n+#!/usr/bin/env python3",
            1,
        )
        with self.assertRaises(protocol.MentorProtocolError):
            protocol.validate_patch(
                malicious, ["router.py", "test_mentor_candidates.py"])

    def test_high_risk_and_policy_proposals_cannot_emit_patch_targets(self):
        base = {
            "kind": "policy",
            "hypothesis": "Policy needs review",
            "proposed_change": "Change the policy",
            "risk": "high",
            "rollback": "Restore current policy",
            "target_files": ["router.py", "test_mentor_candidates.py"],
        }
        with self.assertRaises(protocol.MentorProtocolError):
            protocol.validate_proposal(base)
        base["target_files"] = []
        self.assertEqual(protocol.validate_proposal(base)["target_files"], [])


class MentorBoundaryTests(unittest.TestCase):
    def test_weekly_call_cap_is_reserved_before_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / "usage").mkdir()
            cfg = {"state": state, "max_calls_per_week": 2}
            cara_mentor._reserve_call(cfg)
            cara_mentor._reserve_call(cfg)
            with self.assertRaisesRegex(RuntimeError, "weekly inference-call cap"):
                cara_mentor._reserve_call(cfg)
            usage = json.loads(
                (state / "usage" / "usage.json").read_text(encoding="utf-8"))
            self.assertEqual(usage["count"], 2)

    def test_inference_env_copies_no_telegram_or_deploy_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "cara.env"
            target = Path(tmp) / "mentor.env"
            source.write_text(
                "DO_MODEL_ACCESS_KEY=fixture-inference\n"
                "DO_CHAT_MODEL=fixture-model\n"
                "TELEGRAM_BOT_TOKEN=fixture-telegram\n"
                "FLEET_NOTIFY_BOT_TOKEN=fixture-fleet\n"
                "ALLOWED_CHAT_IDS=123\n",
                encoding="utf-8",
            )
            cara_mentor.write_inference_env(source, target)
            text = target.read_text(encoding="utf-8")
            self.assertIn("DO_MODEL_ACCESS_KEY", text)
            self.assertIn("DO_CHAT_MODEL", text)
            self.assertNotIn("TELEGRAM", text)
            self.assertNotIn("FLEET", text)
            self.assertNotIn("CHAT_IDS", text)

    def test_endpoint_is_fixed_to_digitalocean_and_ignores_proxy(self):
        env = {
            "DO_MODEL_ACCESS_KEY": "fixture",
            "DO_INFERENCE_BASE_URL": "https://attacker.example/v1",
        }
        with mock.patch.dict("os.environ", env, clear=True):
            with self.assertRaises(RuntimeError):
                cara_mentor._config()

    def test_runner_environment_contains_no_parent_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                    "os.environ",
                    {"DO_MODEL_ACCESS_KEY": "secret", "TELEGRAM_BOT_TOKEN": "secret"}):
                env = mentor_runner._safe_env(Path(tmp))
        self.assertNotIn("DO_MODEL_ACCESS_KEY", env)
        self.assertNotIn("TELEGRAM_BOT_TOKEN", env)
        self.assertEqual(env["NO_PROXY"], "*")
        self.assertNotIn("/var/lib/", "\n".join(env.values()))
        self.assertTrue(env["TASK_WORKER_SPOOL"].endswith("cara-worker/spool"))
        self.assertTrue(
            env["MENTOR_REVIEW_SPOOL"].endswith("cara-mentor/spool"))
        self.assertTrue(
            env["MENTOR_RUNNER_SPOOL"].endswith(
                "cara-mentor-runner/spool"))

    def test_runner_failure_summary_names_cases_without_full_output(self):
        output = (
            "FAIL: test_denied (test_candidate.Case)\n"
            "ERROR: test_path (test_candidate.Case)\n"
            "Ran 2 tests in 1.000s\n"
            "FAILED (failures=1, errors=1)\n"
        )
        summary = mentor_runner._tests_summary(output)
        self.assertIn("test_denied", summary)
        self.assertIn("test_path", summary)
        self.assertNotIn("Traceback", summary)

    def test_service_units_enforce_network_and_secret_boundaries(self):
        root = Path(__file__).resolve().parent
        mentor = (root / "cara-mentor.service").read_text(encoding="utf-8")
        runner = (root / "cara-mentor-runner.service").read_text(encoding="utf-8")
        installer = (
            root / "install-tg-ingest-agent-pilot-remote.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("EnvironmentFile=/etc/cara-mentor.env", mentor)
        self.assertNotIn("EnvironmentFile=/etc/tg-ingest-agent.env", mentor)
        self.assertIn("InaccessiblePaths=/etc/tg-ingest-agent.env", mentor)
        self.assertIn("PrivateNetwork=yes", runner)
        self.assertIn("/etc/cara-mentor.env", runner)
        self.assertIn("CapabilityBoundingSet=", mentor)
        self.assertIn("CapabilityBoundingSet=", runner)
        self.assertIn('"$MENTOR_STATE/usage"', installer)
        verifier = (
            root / "verify-cara-runtime.sh"
        ).read_text(encoding="utf-8")
        self.assertNotIn(
            "cara-worker-spool,cara-mentor-spool", verifier)


class MentorCycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.conn = store.open_db(self.root / "cara.db")
        self.cfg = SimpleNamespace(
            mentor_enabled=True,
            mentor_review_spool=self.root / "mentor" / "spool",
            mentor_runner_spool=self.root / "runner" / "spool",
            mentor_result_timeout_hours=48,
            task_artifacts_dir=self.root / "artifacts",
        )
        self.agent = SimpleNamespace(
            cfg=self.cfg,
            tz_offset=lambda: 0,
            build_version=lambda: "fixture-build",
        )
        store.issue_add(
            self.conn, 1, "clarify", "repeated routing confusion",
            context={"source": "fixture"})

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _cycle(self, period):
        self.assertEqual(
            improvement.weekly_analysis(self.agent, self.conn, period), 1)
        return store.mentor_cycle_for_period(self.conn, period)

    def _review_result(self, cycle, proposal, candidate):
        payload = {
            "status": "ok",
            "proposal": proposal,
            "candidate": candidate,
            "error": None,
        }
        value = {
            "version": protocol.PROTOCOL_VERSION,
            "job_id": cycle["review_job_id"],
            "nonce": cycle["review_nonce"],
            "evidence_hash": cycle["evidence_hash"],
            "source_build": cycle["source_build"],
            "source_hash": cycle["source_hash"],
            **payload,
            "result_hash": protocol.digest(payload),
        }
        protocol.atomic_publish(
            self.cfg.mentor_review_spool / "results",
            cycle["review_job_id"] + ".json",
            value,
        )

    def test_weekly_bundle_contains_redacted_evidence_not_conversation_db(self):
        cycle = self._cycle("2026-W31")
        request = protocol.read_regular_json(
            self.cfg.mentor_review_spool / "requests"
            / (cycle["review_job_id"] + ".json"),
            protocol.MAX_REVIEW_REQUEST_BYTES,
        )
        self.assertEqual(request["evidence_hash"], cycle["evidence_hash"])
        text = protocol.canonical(request["evidence"])
        self.assertIn("issue_kind", text)
        self.assertNotIn("telegram_updates", text)
        self.assertNotIn("conversation", text)
        self.assertEqual(
            improvement.weekly_analysis(
                self.agent, self.conn, "2026-W31"), 0)

    def test_high_risk_review_stays_proposal_only_and_draft(self):
        cycle = self._cycle("2026-W32")
        proposal = {
            "kind": "policy",
            "hypothesis": "A permission boundary may be incomplete",
            "proposed_change": "Review the boundary manually",
            "risk": "high",
            "rollback": "Keep the current boundary",
            "target_files": [],
        }
        self._review_result(cycle, proposal, None)
        self.assertEqual(improvement.mentor_tick(self.agent, self.conn), 1)
        cycle = store.mentor_cycle_get(self.conn, cycle["id"])
        row = improvement.proposal_get(self.conn, cycle["proposal_id"])
        self.assertEqual((cycle["status"], row["status"]),
                         ("proposal_only", "draft"))
        self.assertFalse(improvement.decide(self.conn, row["id"], True))

    def test_bound_candidate_becomes_ready_only_after_runner_pass(self):
        cycle = self._cycle("2026-W33")
        targets = ["router.py", "test_mentor_candidates.py"]
        patch = valid_patch()
        proposal = {
            "kind": "routing",
            "hypothesis": "A repeated request is routed incorrectly",
            "proposed_change": "Narrow one deterministic routing condition",
            "risk": "medium",
            "rollback": "Revert the candidate commit",
            "target_files": targets,
        }
        candidate = {
            "patch": patch,
            "patch_hash": protocol.digest(patch),
            "target_files": targets,
        }
        self._review_result(cycle, proposal, candidate)
        self.assertGreaterEqual(improvement.mentor_tick(self.agent, self.conn), 1)
        cycle = store.mentor_cycle_get(self.conn, cycle["id"])
        self.assertEqual(cycle["status"], "testing")
        self.assertTrue(cycle["runner_job_id"])
        row = improvement.proposal_get(self.conn, cycle["proposal_id"])
        change_hash = improvement.candidate_change_hash(
            row["kind"], row["proposed_change"])
        payload = {
            "status": "passed",
            "tests_summary": "Ran 1533 tests in 1.000s; OK (skipped=9)",
            "branch": "mentor/" + cycle["cycle_uid"],
            "commit": "a" * 40,
            "duration_seconds": 1.0,
            "error": None,
        }
        result = {
            "version": protocol.PROTOCOL_VERSION,
            "job_id": cycle["runner_job_id"],
            "nonce": cycle["runner_nonce"],
            "cycle_uid": cycle["cycle_uid"],
            "patch_hash": cycle["patch_hash"],
            "source_build": cycle["source_build"],
            "source_hash": cycle["source_hash"],
            "proposed_change_hash": change_hash,
            **payload,
            "result_hash": protocol.digest(payload),
        }
        protocol.atomic_publish(
            self.cfg.mentor_runner_spool / "results",
            cycle["runner_job_id"] + ".json",
            result,
        )
        self.assertEqual(improvement.mentor_tick(self.agent, self.conn), 1)
        cycle = store.mentor_cycle_get(self.conn, cycle["id"])
        row = improvement.proposal_get(self.conn, cycle["proposal_id"])
        self.assertEqual((cycle["status"], row["status"]), ("ready", "ready"))
        exported = improvement.proposal_patch(self.cfg, self.conn, row)
        self.assertEqual(exported[1].decode("utf-8"), patch)
        self.assertTrue(improvement.decide(self.conn, row["id"], True))

    def test_forged_runner_binding_fails_closed(self):
        cycle = self._cycle("2026-W34")
        proposal = {
            "kind": "routing",
            "hypothesis": "Routing regression",
            "proposed_change": "Narrow one condition",
            "risk": "low",
            "rollback": "Revert",
            "target_files": ["router.py", "test_mentor_candidates.py"],
        }
        patch = valid_patch()
        self._review_result(cycle, proposal, {
            "patch": patch,
            "patch_hash": protocol.digest(patch),
            "target_files": proposal["target_files"],
        })
        improvement.mentor_tick(self.agent, self.conn)
        cycle = store.mentor_cycle_get(self.conn, cycle["id"])
        payload = {
            "status": "passed",
            "tests_summary": "Ran 9999 tests in 0s; OK",
            "branch": "mentor/forged",
            "commit": "b" * 40,
            "duration_seconds": 0.0,
            "error": None,
        }
        value = {
            "version": 1,
            "job_id": cycle["runner_job_id"],
            "nonce": cycle["runner_nonce"],
            "cycle_uid": cycle["cycle_uid"],
            "patch_hash": "0" * 64,
            "source_build": cycle["source_build"],
            "source_hash": cycle["source_hash"],
            "proposed_change_hash": "0" * 64,
            **payload,
            "result_hash": protocol.digest(payload),
        }
        protocol.atomic_publish(
            self.cfg.mentor_runner_spool / "results",
            cycle["runner_job_id"] + ".json",
            value,
        )
        self.assertEqual(improvement.mentor_tick(self.agent, self.conn), 1)
        cycle = store.mentor_cycle_get(self.conn, cycle["id"])
        row = improvement.proposal_get(self.conn, cycle["proposal_id"])
        self.assertEqual((cycle["status"], row["status"]), ("failed", "draft"))


if __name__ == "__main__":
    unittest.main()
