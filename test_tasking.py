#!/usr/bin/env python3
"""Offline contract tests for durable assistant tasks and plan provenance."""
import copy
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import store
import tasking
import tool_broker


def valid_plan(source):
    note = source.index("#12")
    url_text = "https://example.com/report"
    url = source.index(url_text)
    digest = tasking.source_hash(source)
    return {
        "objective": "Compare the saved note with the supplied report",
        "deliverable": "brief",
        "steps": [
            {
                "key": "saved",
                "tool": "knowledge.read",
                "input": {"note_no": 12},
                "bindings": {
                    "note_no": {
                        "source": "boss_span",
                        "start": note,
                        "end": note + 3,
                        "source_hash": digest,
                        "transform": "positive_int",
                    },
                },
                "depends_on": [],
                "purpose": "Read the note the boss named",
            },
            {
                "key": "report",
                "tool": "source.fetch",
                "input": {"url": url_text},
                "bindings": {
                    "url": {
                        "source": "boss_span",
                        "start": url,
                        "end": url + len(url_text),
                        "source_hash": digest,
                        "transform": "url",
                    },
                },
                "depends_on": [],
                "purpose": "Read the supplied report without ingesting it",
            },
            {
                "key": "synthesis",
                "tool": "research.synthesize",
                "input": {
                    "receipt_steps": ["saved", "report"],
                    "question": "Compare the two sources",
                },
                "bindings": {},
                "depends_on": ["saved", "report"],
                "purpose": "Produce claims with citations",
            },
            {
                "key": "draft",
                "tool": "artifact.markdown",
                "input": {"content_step": "synthesis", "title": "Comparison"},
                "bindings": {},
                "depends_on": ["synthesis"],
                "purpose": "Create the requested managed draft",
            },
        ],
    }


class ToolRegistryTests(unittest.TestCase):
    def test_registry_is_closed_and_policy_consistent(self):
        tool_broker.assert_registry()
        self.assertIsNone(tool_broker.get_spec("shell.run"))
        for key, spec in tool_broker.TOOLS.items():
            self.assertEqual(key, spec.id)
            if spec.writes_state:
                self.assertNotIn(spec.risk, {"read_only", "network_read"})

    def test_tool_input_shapes_reject_extra_and_credentialed_url(self):
        spec = tool_broker.get_spec("source.fetch")
        with self.assertRaises(tool_broker.ToolInputError):
            tool_broker.validate_input(spec, {
                "url": "https://example.com", "method": "POST",
            })
        with self.assertRaises(tool_broker.ToolInputError):
            tool_broker.validate_input(spec, {
                "url": "https://user:secret@example.com/report",
            })
        with self.assertRaises(tool_broker.ToolInputError):
            tool_broker.validate_input(spec, {"url": "https://[::1"})

    def test_non_finite_integer_is_a_tool_error_not_raw_overflow(self):
        spec = tool_broker.get_spec("knowledge.search")
        with self.assertRaises(tool_broker.ToolInputError):
            tool_broker.validate_input(spec, {"query": "x", "limit": float("inf")})


class PlanValidationTests(unittest.TestCase):
    def setUp(self):
        self.source = (
            "Compare my saved note #12 with https://example.com/report "
            "and make a short brief."
        )

    def test_valid_plan_is_bounded_and_bound_values_are_not_persisted(self):
        normalized = tasking.validate_plan(valid_plan(self.source), self.source)
        self.assertEqual(normalized["version"], 1)
        self.assertEqual(len(normalized["steps"]), 4)
        self.assertEqual(normalized["steps"][0]["input"]["note_no"],
                         {"$bound": "note_no"})
        self.assertEqual(normalized["steps"][1]["input"]["url"], {"$bound": "url"})
        self.assertNotIn("https://example.com/report", str(normalized))

    def test_unknown_tool_future_dependency_and_hidden_fields_fail_closed(self):
        for mutate in (
            lambda p: p["steps"][0].update(tool="shell.run"),
            lambda p: p["steps"][0].update(depends_on=["draft"]),
            lambda p: p["steps"][0].update(command="cat /etc/passwd"),
        ):
            plan = valid_plan(self.source)
            mutate(plan)
            with self.subTest(plan=plan["steps"][0]), self.assertRaises(tasking.PlanError):
                tasking.validate_plan(plan, self.source)

    def test_boss_span_hash_and_literal_must_match(self):
        plan = valid_plan(self.source)
        plan["steps"][1]["bindings"]["url"]["source_hash"] = "0" * 64
        with self.assertRaisesRegex(tasking.PlanError, "source hash mismatch"):
            tasking.validate_plan(plan, self.source)

        plan = valid_plan(self.source)
        plan["steps"][1]["input"]["url"] = "https://attacker.example/"
        with self.assertRaisesRegex(tasking.PlanError, "does not match"):
            tasking.validate_plan(plan, self.source)

    def test_transform_is_allowlisted_per_tool_field(self):
        plan = valid_plan(self.source)
        plan["steps"][0]["bindings"]["note_no"]["transform"] = "reminder_due"
        with self.assertRaisesRegex(tasking.PlanError, "not allowed"):
            tasking.validate_plan(plan, self.source)

    def test_symbolic_reference_cannot_carry_an_illicit_binding(self):
        plan = valid_plan(self.source)
        plan["steps"][3]["bindings"]["content_step"] = {
            "source": "boss_span",
            "start": 0,
            "end": 7,
            "source_hash": tasking.source_hash(self.source),
            "transform": "literal",
        }
        with self.assertRaisesRegex(tasking.PlanError, "provenance is not allowed"):
            tasking.validate_plan(plan, self.source)

        plan = valid_plan(self.source)
        plan["steps"][1]["bindings"]["url"]["transform"] = "positive_int"
        with self.assertRaisesRegex(tasking.PlanError, "not allowed"):
            tasking.validate_plan(plan, self.source)

    def test_untrusted_predecessor_cannot_become_write_input(self):
        plan = valid_plan(self.source)
        due_text = "tomorrow at 09:00"
        source = self.source + " Remind me " + due_text + " about the report."
        digest = tasking.source_hash(source)
        due_start = source.index(due_text)
        plan["steps"] = plan["steps"][:2] + [{
            "key": "write",
            "tool": "reminder.propose",
            "input": {
                "title": "external title",
                "due_utc": "2026-07-30T06:00:00+00:00",
            },
            "bindings": {
                "title": {
                    "source": "step_output",
                    "step": "report",
                    "path": "document",
                    "schema": "source.fetch/v1",
                    "trust": "external_untrusted",
                },
                "due_utc": {
                    "source": "boss_span",
                    "start": due_start,
                    "end": due_start + len(due_text),
                    "source_hash": digest,
                    "transform": "reminder_due",
                },
            },
            "depends_on": ["report"],
            "purpose": "Propose a reminder",
        }]
        # The original plan's boss bindings must be re-pinned to the extended
        # canonical source before the trust-laundering check is reached.
        for step in plan["steps"][:2]:
            for binding in step["bindings"].values():
                binding["source_hash"] = digest
        with self.assertRaisesRegex(tasking.PlanError, "cannot trust untrusted output"):
            tasking.validate_plan(plan, source)

    def test_recognizable_secret_is_rejected_from_derived_unbound_input(self):
        secrets = (
            "api_key=ABCDEFGHIJKLMNOPQRSTUV",
            "Bearer abcDEF1234567890abcDEF1234567890",
            "ghp_abcdefghijklmnopqrstuvwxyz1234567890AB",
            "dop_v1_abcdefghijklmnopqrstuvwxyz1234567890AB",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature123456",
            "abcdefghijklmnopqrstuvwxyz1234567890ABCD",
        )
        for secret in secrets:
            plan = valid_plan(self.source)
            plan["steps"][2]["input"]["question"] = f"Use {secret} in the comparison"
            with self.subTest(secret=secret[:10]), self.assertRaisesRegex(
                    tasking.PlanError, "secret"):
                tasking.validate_plan(plan, self.source)

    def test_derived_text_redaction_does_not_touch_primary_source(self):
        raw = "password=correct-horse-battery-staple"
        redacted = tasking.redact_derived_text(raw)
        self.assertEqual(redacted, "[REDACTED]")
        self.assertEqual(raw, "password=correct-horse-battery-staple")

    def test_non_default_recurrence_requires_boss_provenance_and_due_is_aware(self):
        source = "Remind me daily at 09:00 about standup."
        digest = tasking.source_hash(source)
        title_start = source.index("standup")
        due_text = "at 09:00"
        due_start = source.index(due_text)
        recurrence_start = source.index("daily")
        plan = {
            "objective": "Propose the requested recurring reminder",
            "deliverable": "answer",
            "steps": [{
                "key": "reminder",
                "tool": "reminder.propose",
                "input": {
                    "title": "standup",
                    "due_utc": "2026-07-30T06:00:00+00:00",
                    "recurrence": "daily",
                },
                "bindings": {
                    "title": {
                        "source": "boss_span", "start": title_start,
                        "end": title_start + len("standup"), "source_hash": digest,
                        "transform": "reminder_title",
                    },
                    "due_utc": {
                        "source": "boss_span", "start": due_start,
                        "end": due_start + len(due_text), "source_hash": digest,
                        "transform": "reminder_due",
                    },
                },
                "depends_on": [],
                "purpose": "Prepare a preview without writing",
            }],
        }
        with self.assertRaisesRegex(tasking.PlanError, "recurrence"):
            tasking.validate_plan(plan, source)
        plan["steps"][0]["bindings"]["recurrence"] = {
            "source": "boss_span", "start": recurrence_start,
            "end": recurrence_start + len("daily"), "source_hash": digest,
            "transform": "literal",
        }
        normalized = tasking.validate_plan(plan, source)
        self.assertEqual(normalized["steps"][0]["input"]["recurrence"],
                         {"$bound": "recurrence"})

        plan["steps"][0]["input"]["due_utc"] = "2026-07-30T06:00:00"
        with self.assertRaisesRegex(tasking.PlanError, "timezone"):
            tasking.validate_plan(plan, source)


class TaskPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "tasks.db"
        self.conn = store.open_db(self.db_path)
        self.source = (
            "Compare my saved note #12 with https://example.com/report "
            "and make a short brief."
        )
        self.plan = valid_plan(self.source)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def add_source(self, update_id, *, chat_id=111, source=None, kind="boss"):
        source = self.source if source is None else source
        update = {
            "update_id": update_id,
            "message": {
                "message_id": update_id + 100,
                "chat": {"id": chat_id},
                "from": {"id": chat_id},
                "text": source,
            },
        }
        store.telegram_update_receive(self.conn, update, chat_id=chat_id)
        store.convo_add(
            self.conn, chat_id, "user", source, source=kind,
            update_id=update_id, tg_message_id=update_id + 100,
        )

    def test_create_is_atomic_and_telegram_redelivery_is_idempotent(self):
        self.add_source(9001)
        row, created = store.assistant_task_create(
            self.conn, 111, 9001, self.plan, trace_id="trace-1")
        self.assertTrue(created)
        self.assertEqual(row["status"], "planned")
        self.assertEqual(len(store.assistant_task_steps(self.conn, row["id"])), 4)

        changed_plan = copy.deepcopy(self.plan)
        changed_plan["objective"] = "A replay must not replace the original plan"
        replay, created = store.assistant_task_create(
            self.conn, 111, 9001, changed_plan, trace_id="trace-2")
        self.assertFalse(created)
        self.assertEqual(replay["id"], row["id"])
        self.assertEqual(replay["objective"], self.plan["objective"])
        self.assertEqual(len(store.assistant_task_steps(self.conn, row["id"])), 4)

    def test_persisted_plan_does_not_duplicate_bound_url_or_note_number(self):
        self.add_source(9002)
        row, _ = store.assistant_task_create(self.conn, 111, 9002, self.plan)
        self.assertNotIn("https://example.com/report", row["plan_json"])
        steps = store.assistant_task_steps(self.conn, row["id"])
        self.assertNotIn("https://example.com/report", steps[1]["input_json"])
        self.assertEqual(steps[0]["input_json"], '{"note_no":{"$bound":"note_no"}}')

    def test_step_failure_rolls_back_parent_task(self):
        self.add_source(9003)
        broken = copy.deepcopy(self.plan)
        broken["steps"][1]["key"] = broken["steps"][0]["key"]
        with self.assertRaisesRegex(tasking.PlanError, "duplicate step key"):
            store.assistant_task_create(self.conn, 111, 9003, broken)
        self.assertIsNone(store.assistant_task_by_source(self.conn, 111, 9003))
        count = self.conn.execute(
            "SELECT COUNT(*) AS n FROM assistant_task_steps").fetchone()["n"]
        self.assertEqual(count, 0)

    def test_cancel_is_owner_scoped_and_expires_unstarted_steps(self):
        self.add_source(9004)
        row, _ = store.assistant_task_create(self.conn, 111, 9004, self.plan)
        self.assertIsNone(store.assistant_task_cancel(self.conn, row["id"], 222))
        self.assertEqual(
            store.assistant_task_cancel(self.conn, row["id"], 111), "cancelled")
        task = store.assistant_task_get(self.conn, row["id"], 111)
        self.assertEqual(task["status"], "cancelled")
        self.assertTrue(all(
            step["status"] == "cancelled"
            for step in store.assistant_task_steps(self.conn, row["id"])
        ))
        self.assertIsNone(store.assistant_task_cancel(self.conn, row["id"], 111))

    def test_running_step_turns_cancel_into_request_not_terminal_claim(self):
        self.add_source(9005)
        row, _ = store.assistant_task_create(self.conn, 111, 9005, self.plan)
        first = store.assistant_task_steps(self.conn, row["id"])[0]
        self.conn.execute(
            "UPDATE assistant_task_steps SET status = 'running' WHERE id = ?",
            (first["id"],),
        )
        self.conn.commit()
        self.assertEqual(
            store.assistant_task_cancel(self.conn, row["id"], 111),
            "cancel_requested",
        )
        task = store.assistant_task_get(self.conn, row["id"], 111)
        self.assertEqual(task["status"], "cancel_requested")
        self.assertIsNone(task["completed_at"])
        self.assertEqual(
            self.conn.execute(
                "SELECT status FROM assistant_task_steps WHERE id = ?",
                (first["id"],),
            ).fetchone()["status"],
            "running",
        )

    def test_storage_rejects_missing_foreign_or_noncanonical_sources(self):
        with self.assertRaisesRegex(ValueError, "source update"):
            store.assistant_task_create(self.conn, 111, 9100, self.plan)

        self.add_source(9101, chat_id=222)
        with self.assertRaisesRegex(ValueError, "source update"):
            store.assistant_task_create(self.conn, 111, 9101, self.plan)

        self.add_source(9102, kind="forward")
        with self.assertRaisesRegex(ValueError, "source update"):
            store.assistant_task_create(self.conn, 111, 9102, self.plan)

    def test_storage_revalidates_raw_plan_and_source_hash(self):
        self.add_source(9103)
        forged = tasking.validate_plan(self.plan, self.source)
        with self.assertRaisesRegex(tasking.PlanError, "unknown plan field"):
            store.assistant_task_create(self.conn, 111, 9103, forged)
        self.assertIsNone(store.assistant_task_by_source(self.conn, 111, 9103))

        tampered = copy.deepcopy(self.plan)
        tampered["steps"][0]["bindings"]["note_no"]["source_hash"] = "0" * 64
        with self.assertRaisesRegex(tasking.PlanError, "source hash mismatch"):
            store.assistant_task_create(self.conn, 111, 9103, tampered)
        self.assertIsNone(store.assistant_task_by_source(self.conn, 111, 9103))

    def test_two_connections_share_one_source_update_task(self):
        self.add_source(9104)
        results = []
        errors = []
        barrier = threading.Barrier(2)

        def create():
            conn = store.open_db(self.db_path)
            try:
                barrier.wait(timeout=5)
                task, created = store.assistant_task_create(
                    conn, 111, 9104, copy.deepcopy(self.plan))
                results.append((task["id"], created))
            except BaseException as exc:
                errors.append(exc)
            finally:
                conn.close()

        threads = [threading.Thread(target=create) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(errors)
        self.assertEqual(len(results), 2)
        self.assertEqual(len({item[0] for item in results}), 1)
        self.assertEqual(sum(1 for _, created in results if created), 1)

    def test_replay_returns_existing_after_canonical_source_is_erased(self):
        self.add_source(9108)
        original, created = store.assistant_task_create(
            self.conn, 111, 9108, self.plan)
        self.assertTrue(created)
        self.conn.execute("DELETE FROM conversation WHERE update_id = 9108")
        self.conn.execute("DELETE FROM telegram_updates WHERE update_id = 9108")
        self.conn.commit()
        replay, created = store.assistant_task_create(
            self.conn, 111, 9108, {"malicious": "ignored on replay"})
        self.assertFalse(created)
        self.assertEqual(replay["id"], original["id"])

    def test_source_cannot_change_during_validated_insert(self):
        self.add_source(9109)
        original_validate = tasking.validate_plan
        blocked = []

        def validate_while_competing(plan, source):
            other = sqlite3.connect(str(self.db_path), timeout=0)
            try:
                other.execute("DELETE FROM conversation WHERE update_id = 9109")
                other.commit()
            except sqlite3.OperationalError as exc:
                blocked.append(str(exc))
                other.rollback()
            finally:
                other.close()
            return original_validate(plan, source)

        with mock.patch.object(tasking, "validate_plan", side_effect=validate_while_competing):
            row, created = store.assistant_task_create(
                self.conn, 111, 9109, self.plan)
        self.assertTrue(created)
        self.assertTrue(any("locked" in error.lower() for error in blocked))
        self.assertEqual(row["source_hash"], tasking.source_hash(self.source))

    def test_foreign_keys_reject_cross_task_receipts_and_approvals(self):
        self.add_source(9105)
        self.add_source(9106)
        task1, _ = store.assistant_task_create(self.conn, 111, 9105, self.plan)
        task2, _ = store.assistant_task_create(self.conn, 111, 9106, self.plan)
        step2 = store.assistant_task_steps(self.conn, task2["id"])[0]
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO tool_receipts"
                " (receipt_uid, idempotency_key, task_id, step_id, tool,"
                " input_hash, policy_version, implementation_version, status,"
                " summary, data_json, evidence_json, created_at)"
                " VALUES ('r1', ?, ?, ?, ?, 'h', ?, ?,"
                " 'ok', 'x', '{}', '[]', '2026-01-01T00:00:00+00:00')",
                (step2["idempotency_key"], task1["id"], step2["id"], step2["tool"],
                 step2["policy_version"], step2["implementation_version"]),
            )
        self.conn.rollback()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO task_approvals"
                " (task_id, step_id, chat_id, source_update, preview_json,"
                " preview_hash, input_hash, policy_version, implementation_version,"
                " target_snapshot_json, target_version, consume_token, status, created_at)"
                " VALUES (?, ?, 111, 9105, '{}', 'ph', 'ih', ?, ?, '{}',"
                " 't1', 'token1', 'pending', '2026-01-01T00:00:00+00:00')",
                (task1["id"], step2["id"], step2["policy_version"],
                 step2["implementation_version"]),
            )
        self.conn.rollback()

    def test_receipt_and_approval_must_match_exact_step_contract_and_owner(self):
        self.add_source(9110)
        task, _ = store.assistant_task_create(self.conn, 111, 9110, self.plan)
        step = store.assistant_task_steps(self.conn, task["id"])[0]
        receipt_sql = (
            "INSERT INTO tool_receipts"
            " (receipt_uid, idempotency_key, task_id, step_id, tool, input_hash,"
            " policy_version, implementation_version, status, summary, data_json,"
            " evidence_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, 'h', ?, ?, 'ok', 'x', '{}', '[]',"
            " '2026-01-01T00:00:00+00:00')"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                receipt_sql,
                ("wrong-tool", step["idempotency_key"], task["id"], step["id"],
                 "source.fetch", step["policy_version"],
                 step["implementation_version"]),
            )
        self.conn.rollback()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                receipt_sql,
                ("wrong-idem", "forged", task["id"], step["id"], step["tool"],
                 step["policy_version"], step["implementation_version"]),
            )
        self.conn.rollback()

        approval_sql = (
            "INSERT INTO task_approvals"
            " (task_id, step_id, chat_id, source_update, preview_json, preview_hash,"
            " input_hash, policy_version, implementation_version, target_snapshot_json,"
            " target_version, consume_token, status, created_at)"
            " VALUES (?, ?, ?, ?, '{}', ?, 'ih', ?, ?, '{}', 't1', ?, 'pending',"
            " '2026-01-01T00:00:00+00:00')"
        )
        for chat_id, source_update, token in (
                (222, 9110, "wrong-chat"), (111, 9999, "wrong-source")):
            with self.subTest(token=token), self.assertRaises(sqlite3.IntegrityError):
                self.conn.execute(
                    approval_sql,
                    (task["id"], step["id"], chat_id, source_update, token,
                     step["policy_version"], step["implementation_version"], token),
                )
            self.conn.rollback()

    def test_expired_identical_preview_can_be_refreshed(self):
        self.add_source(9107)
        task, _ = store.assistant_task_create(self.conn, 111, 9107, self.plan)
        step = store.assistant_task_steps(self.conn, task["id"])[0]
        values = (
            task["id"], step["id"], 111, 9107, "{}", "same-preview",
            "input", step["policy_version"], step["implementation_version"],
            "{}", "target-v1",
        )
        sql = (
            "INSERT INTO task_approvals"
            " (task_id, step_id, chat_id, source_update, preview_json,"
            " preview_hash, input_hash, policy_version, implementation_version,"
            " target_snapshot_json, target_version, consume_token, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending',"
            " '2026-01-01T00:00:00+00:00')"
        )
        self.conn.execute(sql, values + ("token-a",))
        self.conn.execute(
            "UPDATE task_approvals SET status = 'expired' WHERE consume_token = 'token-a'")
        self.conn.execute(sql, values + ("token-b",))
        self.conn.commit()
        count = self.conn.execute(
            "SELECT COUNT(*) AS n FROM task_approvals WHERE step_id = ?",
            (step["id"],),
        ).fetchone()["n"]
        self.assertEqual(count, 2)

    def test_ambiguous_or_executing_effect_blocks_refresh_and_terminal_cancel(self):
        for offset, status in enumerate(("ambiguous", "executing"), start=1):
            update_id = 9120 + offset
            self.add_source(update_id)
            task, _ = store.assistant_task_create(
                self.conn, 111, update_id, copy.deepcopy(self.plan))
            step = store.assistant_task_steps(self.conn, task["id"])[0]
            self.conn.execute(
                "UPDATE assistant_task_steps SET status = 'waiting_approval' WHERE id = ?",
                (step["id"],),
            )
            self.conn.execute(
                "INSERT INTO task_approvals"
                " (task_id, step_id, chat_id, source_update, preview_json, preview_hash,"
                " input_hash, policy_version, implementation_version,"
                " target_snapshot_json, target_version, consume_token, status, created_at)"
                " VALUES (?, ?, 111, ?, '{}', 'same', 'ih', ?, ?, '{}', 't1', ?, ?,"
                " '2026-01-01T00:00:00+00:00')",
                (task["id"], step["id"], update_id, step["policy_version"],
                 step["implementation_version"], f"active-{status}", status),
            )
            self.conn.commit()
            with self.subTest(status=status), self.assertRaises(sqlite3.IntegrityError):
                self.conn.execute(
                    "INSERT INTO task_approvals"
                    " (task_id, step_id, chat_id, source_update, preview_json,"
                    " preview_hash, input_hash, policy_version, implementation_version,"
                    " target_snapshot_json, target_version, consume_token, status, created_at)"
                    " VALUES (?, ?, 111, ?, '{}', 'new', 'ih2', ?, ?, '{}', 't2', ?,"
                    " 'pending', '2026-01-01T00:00:01+00:00')",
                    (task["id"], step["id"], update_id, step["policy_version"],
                     step["implementation_version"], f"new-{status}"),
                )
            self.conn.rollback()
            self.assertEqual(
                store.assistant_task_cancel(self.conn, task["id"], 111),
                "cancel_requested",
            )
            current = self.conn.execute(
                "SELECT status FROM assistant_task_steps WHERE id = ?",
                (step["id"],),
            ).fetchone()
            self.assertEqual(current["status"], "waiting_approval")
            self.assertIsNone(
                store.assistant_task_get(self.conn, task["id"])["completed_at"])

    def test_non_null_backreferences_cascade_with_the_task(self):
        self.add_source(9130)
        task, _ = store.assistant_task_create(self.conn, 111, 9130, self.plan)
        step = store.assistant_task_steps(self.conn, task["id"])[0]
        artifact = self.conn.execute(
            "INSERT INTO task_artifacts"
            " (task_id, kind, safe_filename, local_path, size_bytes, sha256, created_at)"
            " VALUES (?, 'markdown', 'x.md', '/managed/x.md', 1, 'hash',"
            " '2026-01-01T00:00:00+00:00')",
            (task["id"],),
        ).lastrowid
        approval = self.conn.execute(
            "INSERT INTO task_approvals"
            " (task_id, step_id, chat_id, source_update, preview_json, preview_hash,"
            " input_hash, policy_version, implementation_version, target_snapshot_json,"
            " target_version, consume_token, status, created_at)"
            " VALUES (?, ?, 111, 9130, '{}', 'p', 'ih', ?, ?, '{}', 't', 'consume',"
            " 'effect_recorded', '2026-01-01T00:00:00+00:00')",
            (task["id"], step["id"], step["policy_version"],
             step["implementation_version"]),
        ).lastrowid
        receipt = self.conn.execute(
            "INSERT INTO tool_receipts"
            " (receipt_uid, idempotency_key, task_id, step_id, tool, input_hash,"
            " policy_version, implementation_version, status, summary, data_json,"
            " evidence_json, artifact_id, created_at)"
            " VALUES ('receipt', ?, ?, ?, ?, 'ih', ?, ?, 'ok', 'x', '{}', '[]', ?,"
            " '2026-01-01T00:00:00+00:00')",
            (step["idempotency_key"], task["id"], step["id"], step["tool"],
             step["policy_version"], step["implementation_version"], artifact),
        ).lastrowid
        self.conn.execute(
            "UPDATE assistant_task_steps SET approval_id = ?, receipt_id = ? WHERE id = ?",
            (approval, receipt, step["id"]),
        )
        self.conn.execute(
            "UPDATE assistant_tasks SET final_artifact_id = ? WHERE id = ?",
            (artifact, task["id"]),
        )
        self.conn.commit()
        self.conn.execute("DELETE FROM assistant_tasks WHERE id = ?", (task["id"],))
        self.conn.commit()
        for table in (
                "assistant_tasks", "assistant_task_steps", "task_approvals",
                "tool_receipts", "task_artifacts"):
            count = self.conn.execute(
                f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            self.assertEqual(count, 0, table)

    def test_existing_database_reopens_with_clean_foreign_keys(self):
        self.conn.close()
        self.conn = store.open_db(self.db_path)
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_pre_task_database_gets_additive_task_tables(self):
        legacy = Path(self.tmp.name) / "legacy.db"
        raw = sqlite3.connect(str(legacy))
        raw.execute("CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        raw.commit()
        raw.close()
        conn = store.open_db(legacy)
        try:
            tables = {
                row["name"] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            self.assertTrue({
                "assistant_tasks", "assistant_task_steps", "tool_receipts",
                "task_approvals", "task_artifacts",
            } <= tables)
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
