#!/usr/bin/env python3
"""Adversarial tests for Cara's separate Mentor and candidate runner."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
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
    def test_runner_timeout_budget_covers_the_full_live_suite(self):
        self.assertEqual(protocol.DEFAULT_RUNNER_TEST_TIMEOUT_SECONDS, 900)
        self.assertEqual(protocol.MAX_RUNNER_TEST_TIMEOUT_SECONDS, 1200)
        self.assertEqual(
            protocol.RUNNER_CPU_LIMIT_SECONDS,
            protocol.MAX_RUNNER_TEST_TIMEOUT_SECONDS + 60,
        )
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "cara.env"
            target = Path(tmp) / "runner.env"
            source.write_text("", encoding="utf-8")
            cara_mentor.write_runner_env(source, target)
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                'MENTOR_TEST_TIMEOUT_SECONDS="900"\n')
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(mentor_runner._config()["timeout"], 900)
        with mock.patch.object(
                mentor_runner.resource, "setrlimit") as setrlimit:
            mentor_runner._limits()
        setrlimit.assert_any_call(
            mentor_runner.resource.RLIMIT_CPU,
            (protocol.RUNNER_CPU_LIMIT_SECONDS,) * 2,
        )
        verifier = (Path(__file__).resolve().parent
                    / "verify_mentor_runtime.py").read_text(encoding="utf-8")
        self.assertIn("protocol.MAX_RUNNER_TEST_TIMEOUT_SECONDS + 120", verifier)
        self.assertIn('result.get("error")', verifier)

    def test_acknowledge_is_durable_before_database_ack(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            requests = root / "requests"
            (root / "results").mkdir()
            requests.mkdir()
            job_id = protocol.new_job_id("review")
            request_path = requests / f"{job_id}.json"
            request_path.write_text("{}", encoding="utf-8")
            with mock.patch.object(
                    protocol, "fsync_dir", side_effect=OSError("fixture")):
                self.assertFalse(mentor_client.acknowledge(root, job_id))
            self.assertFalse(request_path.exists())
            with mock.patch.object(protocol, "fsync_dir") as fsync_dir:
                self.assertTrue(mentor_client.acknowledge(root, job_id))
            fsync_dir.assert_called_once_with(requests)

    def test_weekly_call_cap_is_reserved_before_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / "usage").mkdir()
            cfg = {"state": state, "max_calls_per_week": 4}
            for _ in range(4):
                cara_mentor._reserve_call(cfg)
            with self.assertRaisesRegex(RuntimeError, "weekly inference-call cap"):
                cara_mentor._reserve_call(cfg)
            usage = json.loads(
                (state / "usage" / "usage.json").read_text(encoding="utf-8"))
            self.assertEqual(usage["count"], 4)

    def test_existing_corrupt_usage_ledger_fails_closed(self):
        for body in (
                "not-json", "{}", '{"period":"bad","count":0}',
                '{"period":"2026-W35","count":true}'):
            with self.subTest(body=body), tempfile.TemporaryDirectory() as tmp:
                state = Path(tmp)
                (state / "usage").mkdir()
                (state / "usage" / "usage.json").write_text(
                    body, encoding="utf-8")
                with self.assertRaisesRegex(
                        cara_mentor.InferenceFailure, "ledger is (unreadable|invalid)"):
                    cara_mentor._reserve_call(
                        {"state": state, "max_calls_per_week": 4})

    def test_chat_classifies_transient_and_permanent_transport_failures(self):
        cfg = {
            "base": "https://inference.do-ai.run/v1", "key": "fixture",
            "model": "fixture", "timeout": 10,
        }
        cases = (
            (cara_mentor.HTTPError("https://inference.do-ai.run", 429,
                                  "rate", None, None), True, "http_transient"),
            (cara_mentor.HTTPError("https://inference.do-ai.run", 520,
                                  "origin", None, None), True, "http_transient"),
            (cara_mentor.HTTPError("https://inference.do-ai.run", 401,
                                  "auth", None, None), False, "http_permanent"),
            (TimeoutError("read timed out"), True, "timeout"),
            (cara_mentor.URLError("unreachable"), True, "transport"),
        )
        for failure, retryable, code in cases:
            opener = mock.Mock()
            opener.open.side_effect = failure
            with self.subTest(code=code), mock.patch.object(
                    cara_mentor, "_reserve_call"), mock.patch.object(
                    cara_mentor, "build_opener", return_value=opener):
                with self.assertRaises(cara_mentor.InferenceFailure) as raised:
                    cara_mentor._chat(cfg, "system", "user", 10)
            self.assertEqual(raised.exception.retryable, retryable)
            self.assertEqual(raised.exception.code, code)

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

    def test_service_runs_proposal_and_candidate_as_separate_one_call_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            requests = root / "requests"
            results = root / "results"
            inflight = root / "inflight"
            for path in (source, requests, results, inflight):
                path.mkdir()
            (source / "VERSION").write_text("build-1", encoding="utf-8")
            (source / "SOURCE_HASH").write_text("a" * 64, encoding="utf-8")
            (source / "router.py").write_text(
                "#!/usr/bin/env python3\n", encoding="utf-8")
            (source / "test_mentor_candidates.py").write_text(
                "#!/usr/bin/env python3\n", encoding="utf-8")
            cfg = {"source": source}
            proposal = {
                "kind": "routing", "hypothesis": "One route is too broad",
                "proposed_change": "Narrow one deterministic route",
                "risk": "low", "rollback": "Revert",
                "target_files": ["router.py", "test_mentor_candidates.py"],
            }
            review = mentor_client.build_review_request(
                evidence=[{"kind": "issue", "id": "fixture"}],
                source_build="build-1", source_hash="a" * 64)
            review_path = requests / (review["job_id"] + ".json")
            protocol.atomic_publish(requests, review_path.name, review)
            events = []
            with mock.patch.object(
                    cara_mentor.os, "fsync",
                    side_effect=lambda _fd: events.append("marker-file")), \
                    mock.patch.object(
                        protocol, "fsync_dir",
                        side_effect=lambda path: events.append(
                            f"dir:{Path(path).name}")), \
                    mock.patch.object(
                        cara_mentor, "_chat",
                        side_effect=lambda *_args: (
                            events.append("inference") or (proposal, 0.25))) as chat:
                self.assertTrue(cara_mentor.process_one(
                    cfg, review_path, results, inflight))
            chat.assert_called_once()
            self.assertLess(events.index("marker-file"),
                            events.index("dir:inflight"))
            self.assertLess(events.index("dir:inflight"),
                            events.index("inference"))
            review_result = protocol.read_regular_json(
                results / review_path.name, protocol.MAX_REVIEW_RESULT_BYTES)
            self.assertEqual(review_result["status"], "ok")
            self.assertEqual(review_result["proposal"], proposal)
            self.assertNotIn("candidate", review_result)

            candidate = mentor_client.build_candidate_request(
                cycle_uid="fixture-cycle", attempt_no=1, proposal=proposal,
                evidence_hash=review["evidence_hash"], source_build="build-1",
                source_hash="a" * 64)
            candidate_path = requests / (candidate["job_id"] + ".json")
            protocol.atomic_publish(requests, candidate_path.name, candidate)
            failure = cara_mentor.InferenceFailure(
                "timeout", "Mentor inference transport failed: TimeoutError",
                retryable=True, duration_seconds=0.5)
            with mock.patch.object(
                    cara_mentor, "_chat", side_effect=failure) as chat:
                self.assertTrue(cara_mentor.process_one(
                    cfg, candidate_path, results, inflight))
            chat.assert_called_once()
            candidate_result = protocol.read_regular_json(
                results / candidate_path.name,
                protocol.MAX_CANDIDATE_RESULT_BYTES)
            self.assertEqual(candidate_result["status"], "error")
            self.assertEqual(candidate_result["error_code"], "timeout")
            self.assertTrue(candidate_result["retryable"])
            self.assertIsNone(candidate_result["candidate"])


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

    def _review_result(self, cycle, proposal=None, *, status="ok",
                       retryable=False, error_code=None, error=None,
                       duration_seconds=1.0):
        payload = {
            "status": status,
            "proposal": proposal,
            "retryable": retryable,
            "error_code": error_code,
            "error": error,
            "duration_seconds": duration_seconds,
        }
        value = {
            "version": protocol.INFERENCE_PROTOCOL_VERSION,
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

    def _candidate_result(self, cycle, candidate=None, *, status="ok",
                          retryable=False, error_code=None, error=None,
                          duration_seconds=2.0):
        attempt = store.mentor_attempt_active(
            self.conn, cycle["id"], "candidate")
        self.assertIsNotNone(attempt)
        payload = {
            "status": status,
            "candidate": candidate,
            "retryable": retryable,
            "error_code": error_code,
            "error": error,
            "duration_seconds": duration_seconds,
        }
        value = {
            "version": protocol.INFERENCE_PROTOCOL_VERSION,
            "job_id": attempt["job_id"], "nonce": attempt["nonce"],
            "cycle_uid": cycle["cycle_uid"],
            "attempt_no": attempt["attempt_no"],
            "proposal_hash": cycle["proposal_hash"],
            "evidence_hash": cycle["evidence_hash"],
            "source_build": cycle["source_build"],
            "source_hash": cycle["source_hash"],
            **payload,
            "result_hash": protocol.digest(payload),
        }
        protocol.atomic_publish(
            self.cfg.mentor_review_spool / "results",
            attempt["job_id"] + ".json", value)
        return attempt

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

    def test_legacy_submitted_cycle_migrates_to_recoverable_failure(self):
        cycle = self._cycle("2026-W30")
        self.conn.execute(
            "DELETE FROM mentor_attempts WHERE cycle_id=?", (cycle["id"],))
        self.conn.commit()
        self.conn.close()
        self.conn = store.open_db(self.root / "cara.db")
        cycle = store.mentor_cycle_get(self.conn, cycle["id"])
        self.assertEqual(cycle["status"], "failed")
        self.assertEqual(cycle["error"], "protocol_upgrade_requires_recovery")
        self.assertIsNone(cycle["proposal_id"])
        request_path = (
            self.cfg.mentor_review_spool / "requests"
            / (cycle["review_job_id"] + ".json"))
        self.assertTrue(request_path.exists())
        self.assertEqual(improvement.mentor_tick(self.agent, self.conn), 0)
        cycle = store.mentor_cycle_get(self.conn, cycle["id"])
        self.assertFalse(request_path.exists())
        self.assertTrue(cycle["review_acknowledged_at"])

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
        self._review_result(cycle, proposal)
        self.assertEqual(improvement.mentor_tick(self.agent, self.conn), 1)
        cycle = store.mentor_cycle_get(self.conn, cycle["id"])
        row = improvement.proposal_get(self.conn, cycle["proposal_id"])
        self.assertEqual((cycle["status"], row["status"]),
                         ("proposal_only", "draft"))
        attempt = store.mentor_attempt_last(
            self.conn, cycle["id"], "proposal")
        self.assertEqual(
            (attempt["status"], attempt["error_class"],
             attempt["latency_seconds"]),
            ("succeeded", None, 1.0),
        )
        self.assertFalse(improvement.decide(self.conn, row["id"], True))

    def test_malformed_proposal_result_is_terminal_and_acknowledged(self):
        cycle = self._cycle("2026-W32-bad")
        self._review_result(cycle, {
            "kind": "routing", "hypothesis": "Bad target binding",
            "proposed_change": "Change one file", "risk": "low",
            "rollback": "Revert", "target_files": ["router.py"],
        })
        self.assertEqual(improvement.mentor_tick(self.agent, self.conn), 1)
        cycle = store.mentor_cycle_get(self.conn, cycle["id"])
        attempt = store.mentor_attempt_last(
            self.conn, cycle["id"], "proposal")
        self.assertEqual(cycle["status"], "failed")
        self.assertEqual(attempt["status"], "permanent_failed")
        self.assertEqual(attempt["error_class"], "protocol")
        self.assertTrue(attempt["acknowledged_at"])
        self.assertFalse(
            (self.cfg.mentor_review_spool / "requests"
             / (cycle["review_job_id"] + ".json")).exists())

    def test_unsafe_candidate_result_preserves_proposal_and_fails_candidate(self):
        cycle = self._cycle("2026-W32-unsafe")
        targets = ["router.py", "test_mentor_candidates.py"]
        proposal = {
            "kind": "routing", "hypothesis": "One route is too broad",
            "proposed_change": "Narrow one route", "risk": "low",
            "rollback": "Revert", "target_files": targets,
        }
        self._review_result(cycle, proposal)
        improvement.mentor_tick(self.agent, self.conn)
        cycle = store.mentor_cycle_get(self.conn, cycle["id"])
        attack = valid_patch().replace(
            "+#!/usr/bin/env python3",
            "+import subprocess\n+#!/usr/bin/env python3", 1)
        self._candidate_result(cycle, {
            "patch": attack, "patch_hash": protocol.digest(attack),
            "target_files": targets,
        })
        self.assertEqual(improvement.mentor_tick(self.agent, self.conn), 1)
        cycle = store.mentor_cycle_get(self.conn, cycle["id"])
        attempt = store.mentor_attempt_last(
            self.conn, cycle["id"], "candidate")
        self.assertEqual(cycle["status"], "candidate_failed")
        self.assertIsNotNone(cycle["proposal_id"])
        self.assertEqual(attempt["error_class"], "protocol")

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
        self._review_result(cycle, proposal)
        self.assertGreaterEqual(improvement.mentor_tick(self.agent, self.conn), 1)
        cycle = store.mentor_cycle_get(self.conn, cycle["id"])
        self.assertEqual(cycle["status"], "candidate_pending")
        self._candidate_result(cycle, candidate)
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

    def test_runner_publish_failure_keeps_valid_candidate_in_testing(self):
        cycle = self._cycle("2026-W33-runner-defer")
        targets = ["router.py", "test_mentor_candidates.py"]
        proposal = {
            "kind": "routing", "hypothesis": "One route is too broad",
            "proposed_change": "Narrow one route", "risk": "low",
            "rollback": "Revert", "target_files": targets,
        }
        patch = valid_patch()
        self._review_result(cycle, proposal)
        improvement.mentor_tick(self.agent, self.conn)
        cycle = store.mentor_cycle_get(self.conn, cycle["id"])
        self._candidate_result(cycle, {
            "patch": patch, "patch_hash": protocol.digest(patch),
            "target_files": targets,
        })
        with mock.patch.object(
                improvement.mentor_client, "publish_runner",
                side_effect=mentor_client.MentorUnavailable("local spool")):
            self.assertEqual(improvement.mentor_tick(
                self.agent, self.conn), 1)
        cycle = store.mentor_cycle_get(self.conn, cycle["id"])
        job_id = cycle["runner_job_id"]
        self.assertEqual(cycle["status"], "testing")
        self.assertTrue(job_id)
        self.assertEqual(improvement.mentor_tick(self.agent, self.conn), 0)
        cycle = store.mentor_cycle_get(self.conn, cycle["id"])
        self.assertEqual(cycle["runner_job_id"], job_id)
        self.assertTrue(
            (self.cfg.mentor_runner_spool / "requests"
             / (job_id + ".json")).exists())

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
        candidate = {
            "patch": patch,
            "patch_hash": protocol.digest(patch),
            "target_files": proposal["target_files"],
        }
        self._review_result(cycle, proposal)
        improvement.mentor_tick(self.agent, self.conn)
        cycle = store.mentor_cycle_get(self.conn, cycle["id"])
        self._candidate_result(cycle, candidate)
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
            "version": protocol.PROTOCOL_VERSION,
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
        self.assertEqual(
            (cycle["status"], row["status"]), ("candidate_failed", "draft"))

    def test_transient_candidate_retries_are_bounded_and_keep_proposal(self):
        cycle = self._cycle("2026-W35")
        proposal = {
            "kind": "routing", "hypothesis": "Routing timeout",
            "proposed_change": "Narrow a routing condition", "risk": "low",
            "rollback": "Revert", "target_files": [
                "router.py", "test_mentor_candidates.py"],
        }
        self._review_result(cycle, proposal)
        improvement.mentor_tick(self.agent, self.conn)
        cycle = store.mentor_cycle_get(self.conn, cycle["id"])
        proposal_id = cycle["proposal_id"]
        jobs = []
        for attempt_no in (1, 2, 3):
            cycle = store.mentor_cycle_get(self.conn, cycle["id"])
            attempt = store.mentor_attempt_active(
                self.conn, cycle["id"], "candidate")
            self.assertEqual(attempt["attempt_no"], attempt_no)
            jobs.append(attempt["job_id"])
            self._candidate_result(
                cycle, status="error", retryable=True,
                error_code="timeout", error="TimeoutError")
            before = datetime.now(timezone.utc)
            self.assertEqual(improvement.mentor_tick(self.agent, self.conn), 1)
            cycle = store.mentor_cycle_get(self.conn, cycle["id"])
            if attempt_no < 3:
                self.assertEqual(cycle["status"], "candidate_deferred")
                self.assertEqual(cycle["proposal_id"], proposal_id)
                due = datetime.fromisoformat(cycle["next_candidate_at"])
                expected = timedelta(hours=1 if attempt_no == 1 else 6)
                self.assertGreaterEqual(due, before + expected)
                self.assertLess(due, before + expected + timedelta(seconds=2))
                self.assertEqual(
                    improvement.mentor_tick(self.agent, self.conn), 0)
                self.conn.execute(
                    "UPDATE mentor_cycles SET next_candidate_at=? WHERE id=?",
                    ("2000-01-01T00:00:00+00:00", cycle["id"]),
                )
                self.conn.commit()
                self.assertEqual(
                    improvement.mentor_tick(self.agent, self.conn), 1)
            else:
                self.assertEqual(cycle["status"], "candidate_failed")
        self.assertEqual(len(set(jobs)), 3)
        attempts = self.conn.execute(
            "SELECT status, error_class FROM mentor_attempts"
            " WHERE cycle_id=? AND phase='candidate' ORDER BY attempt_no",
            (cycle["id"],),
        ).fetchall()
        self.assertEqual(len(attempts), 3)
        self.assertEqual(
            [row["status"] for row in attempts],
            ["transient_failed", "transient_failed", "transient_failed"],
        )
        self.assertEqual(
            [row["error_class"] for row in attempts],
            ["timeout", "timeout", "timeout"],
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM improvement_proposals WHERE id=?",
                (proposal_id,),
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM mentor_attempts WHERE cycle_id=?",
                (cycle["id"],),
            ).fetchone()[0],
            4,
        )
        self.assertEqual(improvement.mentor_tick(self.agent, self.conn), 0)

    def test_weekly_cap_event_is_preserved_and_does_not_spend_retry_slot(self):
        cycle = self._cycle("2026-W35-cap")
        proposal = {
            "kind": "routing", "hypothesis": "A route is too broad",
            "proposed_change": "Narrow one route", "risk": "low",
            "rollback": "Revert", "target_files": [
                "router.py", "test_mentor_candidates.py"],
        }
        self._review_result(cycle, proposal)
        improvement.mentor_tick(self.agent, self.conn)
        cycle = store.mentor_cycle_get(self.conn, cycle["id"])
        capped = self._candidate_result(
            cycle, status="error", retryable=True,
            error_code="weekly_cap", error="weekly cap")
        improvement.mentor_tick(self.agent, self.conn)
        cycle = store.mentor_cycle_get(self.conn, cycle["id"])
        capped = store.mentor_attempt_get(self.conn, capped["id"])
        self.assertEqual(cycle["status"], "candidate_deferred")
        self.assertEqual(capped["status"], "cap_deferred")
        self.assertEqual(capped["error_class"], "weekly_cap")
        self.assertTrue(capped["acknowledged_at"])
        self.assertEqual(store.mentor_attempt_count(
            self.conn, cycle["id"], "candidate"), 0)
        self.conn.execute(
            "UPDATE mentor_cycles SET next_candidate_at=? WHERE id=?",
            ("2000-01-01T00:00:00+00:00", cycle["id"]),
        )
        self.conn.commit()
        improvement.mentor_tick(self.agent, self.conn)
        cycle = store.mentor_cycle_get(self.conn, cycle["id"])
        fresh = store.mentor_attempt_active(
            self.conn, cycle["id"], "candidate")
        self.assertEqual(fresh["attempt_no"], 2)
        self.assertNotEqual(fresh["job_id"], capped["job_id"])
        self.assertEqual(store.mentor_attempt_count(
            self.conn, cycle["id"], "candidate"), 1)

    def test_proposal_uses_submitted_issue_snapshot_after_live_resolution(self):
        cycle = self._cycle("2026-W36")
        snapshot = json.loads(cycle["evidence_payload_json"])[0]
        pattern = self.conn.execute(
            "SELECT * FROM issue_patterns WHERE fingerprint=?",
            (snapshot["id"],),
        ).fetchone()
        self.assertEqual(pattern["status"], "open")
        self.assertEqual(store.issue_resolve_exact(
            self.conn, pattern["fingerprint"], pattern["last_issue_id"],
            "fixture regression"), 1)
        proposal = {
            "kind": "policy", "hypothesis": "Review one boundary",
            "proposed_change": "Document a manual review", "risk": "high",
            "rollback": "Keep current behavior", "target_files": [],
        }
        self._review_result(cycle, proposal)
        improvement.mentor_tick(self.agent, self.conn)
        cycle = store.mentor_cycle_get(self.conn, cycle["id"])
        saved = json.loads(improvement.proposal_get(
            self.conn, cycle["proposal_id"])["evidence_json"])[0]
        self.assertEqual(saved, snapshot)
        self.assertEqual(saved["status"], "open")

    def test_issue_resolution_cas_rejects_a_new_occurrence(self):
        row = store.issue_open_patterns(self.conn, limit=1)[0]
        old_last = row["last_issue_id"]
        store.issue_add(
            self.conn, 1, row["kind"], row["detail"],
            context={"source": "new-fixture"})
        self.assertEqual(store.issue_resolve_exact(
            self.conn, row["fingerprint"], old_last, "stale fixture"), 0)
        current = self.conn.execute(
            "SELECT * FROM issue_patterns WHERE fingerprint=?",
            (row["fingerprint"],),
        ).fetchone()
        self.assertEqual(current["status"], "open")
        self.assertEqual(store.issue_resolve_exact(
            self.conn, row["fingerprint"], current["last_issue_id"],
            "verified fixture regression"), 1)
        resolved = self.conn.execute(
            "SELECT * FROM issue_patterns WHERE fingerprint=?",
            (row["fingerprint"],),
        ).fetchone()
        self.assertEqual(resolved["status"], "resolved")
        self.assertEqual(resolved["resolution"], "verified fixture regression")
        self.assertTrue(resolved["resolved_at"])
        store.issue_add(self.conn, 1, row["kind"], row["detail"])
        reopened = self.conn.execute(
            "SELECT * FROM issue_patterns WHERE fingerprint=?",
            (row["fingerprint"],),
        ).fetchone()
        self.assertEqual(reopened["status"], "open")
        self.assertIsNone(reopened["resolved_at"])

    def test_feedback_cursor_advances_only_with_atomic_proposal(self):
        evidence = [{"kind": "feedback", "id": 7}]
        payload = [{
            "kind": "feedback", "id": 7, "rating": 1,
            "correction": "redacted fixture",
        }]
        request = mentor_client.build_review_request(
            evidence=payload, source_build="fixture-build",
            source_hash="f" * 64)
        cycle = store.mentor_cycle_create(
            self.conn, cycle_uid="feedback-fixture", period_key="2026-W37",
            evidence_refs=evidence, evidence_payload=request["evidence"],
            evidence_hash=request["evidence_hash"],
            source_build="fixture-build", source_hash="f" * 64,
            review_job_id=request["job_id"], review_nonce=request["nonce"],
            review_request_hash=protocol.digest(request), feedback_cursor_end=7,
        )
        mentor_client.publish_review(self.cfg, request)
        self.assertEqual(store.kv_get(
            self.conn, "improvement_last_evidence_id", "0"), "0")
        self._review_result(cycle, {
            "kind": "policy", "hypothesis": "Feedback needs review",
            "proposed_change": "Review the response manually", "risk": "high",
            "rollback": "Keep current response", "target_files": [],
        })
        improvement.mentor_tick(self.agent, self.conn)
        cycle = store.mentor_cycle_get(self.conn, cycle["id"])
        self.assertEqual(store.kv_get(
            self.conn, "improvement_last_evidence_id", "0"), "7")
        self.assertEqual(
            json.loads(improvement.proposal_get(
                self.conn, cycle["proposal_id"])["evidence_json"]),
            request["evidence"],
        )

    def test_failed_proposal_keeps_feedback_cursor_and_creates_no_proposal(self):
        evidence = [{"kind": "feedback", "id": 9}]
        payload = [{"kind": "feedback", "id": 9, "rating": 1}]
        request = mentor_client.build_review_request(
            evidence=payload, source_build="fixture-build",
            source_hash="e" * 64)
        cycle = store.mentor_cycle_create(
            self.conn, cycle_uid="failed-feedback-fixture",
            period_key="2026-W37-failed", evidence_refs=evidence,
            evidence_payload=request["evidence"],
            evidence_hash=request["evidence_hash"],
            source_build="fixture-build", source_hash="e" * 64,
            review_job_id=request["job_id"], review_nonce=request["nonce"],
            review_request_hash=protocol.digest(request), feedback_cursor_end=9,
        )
        mentor_client.publish_review(self.cfg, request)
        self._review_result(
            cycle, status="error", retryable=True,
            error_code="transport", error="URLError")
        self.assertEqual(improvement.mentor_tick(self.agent, self.conn), 1)
        cycle = store.mentor_cycle_get(self.conn, cycle["id"])
        self.assertEqual(cycle["status"], "failed")
        self.assertIsNone(cycle["proposal_id"])
        self.assertEqual(store.kv_get(
            self.conn, "improvement_last_evidence_id", "0"), "0")
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM improvement_proposals"
            ).fetchone()[0],
            0,
        )

    def test_failed_cycle_replay_is_linked_exact_and_idempotent(self):
        original = self._cycle("2026-W38")
        self._review_result(
            original, status="error", retryable=False,
            error_code="http_permanent", error="HTTP 401")
        improvement.mentor_tick(self.agent, self.conn)
        original = store.mentor_cycle_get(self.conn, original["id"])
        self.assertEqual(original["status"], "failed")
        replay, created = improvement.replay_failed_mentor_cycle(
            self.agent, self.conn, "2026-W38", original["evidence_hash"])
        self.assertTrue(created)
        again, created_again = improvement.replay_failed_mentor_cycle(
            self.agent, self.conn, "2026-W38", original["evidence_hash"])
        self.assertFalse(created_again)
        self.assertEqual(replay["id"], again["id"])
        self.assertEqual(replay["recovery_of_cycle_id"], original["id"])
        self.assertEqual(
            json.loads(replay["evidence_payload_json"]),
            json.loads(original["evidence_payload_json"]),
        )
        self.assertEqual(
            store.mentor_cycle_get(self.conn, original["id"])["status"],
            "failed",
        )
        self._review_result(replay, {
            "kind": "policy", "hypothesis": "Recover the weekly review",
            "proposed_change": "Keep the recovered proposal for manual review",
            "risk": "high", "rollback": "Keep current behavior",
            "target_files": [],
        })
        improvement.mentor_tick(self.agent, self.conn)
        replay = store.mentor_cycle_get(self.conn, replay["id"])
        self.assertEqual(replay["status"], "proposal_only")
        with mock.patch.object(
                improvement.mentor_client, "publish_review") as publish:
            terminal, created_terminal = improvement.replay_failed_mentor_cycle(
                self.agent, self.conn, "2026-W38", original["evidence_hash"])
        self.assertFalse(created_terminal)
        self.assertEqual(terminal["id"], replay["id"])
        publish.assert_not_called()
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM improvement_proposals"
                " WHERE id=?", (replay["proposal_id"],)
            ).fetchone()[0],
            1,
        )


if __name__ == "__main__":
    unittest.main()
