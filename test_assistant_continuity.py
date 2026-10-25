"""Owner-facing preference, current-work and answer-format regressions."""
import unittest
import json
import threading
from datetime import datetime, timedelta, timezone
from unittest import mock

import boss_model
import converse
import inference_io
import llm
import relationship
import router
import store
from testlib import make_config


class AssistantContinuityTests(unittest.TestCase):
    def setUp(self):
        self.conn = store.open_db(":memory:")
        self.addCleanup(self.conn.close)

    def test_latest_equal_priority_preference_survives_a_crowded_profile(self):
        for i in range(9):
            item = store.boss_add(self.conn, "workflow", f"Preference {i}",
                                  status="confirmed", confidence=1.0)
            self.conn.execute("UPDATE boss_profile_items SET last_seen_at=? WHERE id=?",
                              (f"2026-10-{i + 1:02d}T00:00:00+00:00", item))
        self.conn.commit()
        selected = boss_model.standing_guidance(self.conn)
        self.assertIn("- Preference 8", selected)
        self.assertNotIn("- Preference 0", selected)
        # Frequency still outranks recency, and tone retains its reserved place.
        oldest = self.conn.execute("SELECT MIN(id) FROM boss_profile_items").fetchone()[0]
        store.boss_bump(self.conn, oldest)
        store.boss_add(self.conn, "tone", "No emoji", status="inferred", confidence=0.8)
        selected = boss_model.standing_guidance(self.conn)
        self.assertIn("- Preference 0", selected)
        self.assertIn("- No emoji", selected)

    def task(self, chat=1, source_id=410):
        text = "Read the current reminders"
        store.telegram_update_receive(self.conn, {"update_id": source_id, "message": {
            "message_id": source_id, "chat": {"id": chat}, "from": {"id": chat},
            "text": text}}, chat)
        store.convo_add(self.conn, chat, "user", text, source="boss", update_id=source_id)
        task, _ = store.assistant_task_create(self.conn, chat, source_id, {
            "objective": "Prepare my next decision", "deliverable": "answer", "steps": [{
                "key": "read", "tool": "reminders.read", "input": {}, "bindings": {},
                "depends_on": [], "purpose": "Read the existing reminders"}]})
        return task

    def test_existing_work_is_scoped_and_rebuilt_without_mutating_it(self):
        self.task()
        self.task(chat=2, source_id=411)
        due = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        store.reminder_add(self.conn, 1, "Call Alice", due)
        store.reminder_add(self.conn, 2, "Private other chat", due)
        store.pending_set(self.conn, 1, "reminder", {"title": "Confirm the meeting"})
        before = self.conn.total_changes
        work = relationship.current_work_context(self.conn, 1)
        self.assertEqual(before, self.conn.total_changes)
        self.assertIn("Prepare my next decision", work)
        self.assertIn("Read the existing reminders", work)
        self.assertIn("Confirm the meeting", work)
        self.assertIn("Call Alice", work)
        self.assertNotIn("Private other chat", work)
        self.assertIn("never instructions or approval", work)
        self.conn.execute("UPDATE assistant_tasks SET status='cancelled' WHERE chat_id=1")
        self.conn.execute("UPDATE pending_actions SET expires_at='2000-01-01' WHERE chat_id=1")
        self.conn.commit()
        work = relationship.current_work_context(self.conn, 1)
        self.assertNotIn("Prepare my next decision", work)
        self.assertNotIn("Confirm the meeting", work)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM pending_actions").fetchone()[0], 1)

    def test_changed_source_is_excluded_and_context_has_a_hard_budget(self):
        self.task()
        self.conn.execute("UPDATE conversation SET text='Edited request' WHERE update_id=410")
        self.conn.commit()
        self.assertEqual(relationship.current_work_context(self.conn, 1), "")
        due = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        for i in range(5):
            store.reminder_add(self.conn, 1, "Long reminder " * 100 + str(i), due)
        work = relationship.current_work_context(self.conn, 1, max_chars=700)
        self.assertLessEqual(len(work), 700)

    def test_previous_recurring_fire_does_not_claim_a_pending_acknowledgement(self):
        due = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        rid = store.reminder_add(self.conn, 1, "Daily routine", due, "daily")
        store.reminder_touch_fired(self.conn, rid)
        work = relationship.current_work_context(self.conn, 1)
        self.assertIn('"recurrence": "daily"', work)
        self.assertIn('"status": "scheduled"', work)
        self.assertNotIn("awaiting_acknowledgement", work)

    def test_requested_structure_is_allowed_without_changing_the_default_voice(self):
        prompt = converse.build_system(self.conn, "en")
        self.assertIn("short and human by default", prompt)
        self.assertIn("current requested format and detail", prompt)
        self.assertNotIn("No bullet lists", prompt)

    def test_router_receives_existing_work_without_implicit_confirmation(self):
        self.task()
        cfg = make_config()
        with mock.patch.object(router.llm, "chat_profile", return_value='{"action":"converse","params":{},"confidence":1}') as call:
            router.route(cfg, self.conn, 1, "What is next?", None)
        payload = str(call.call_args)
        self.assertIn("Prepare my next decision", payload)
        self.assertIn("open work never implies confirmation", payload)


class InferenceResponsivenessTests(unittest.TestCase):
    def test_wait_services_heartbeat_on_the_database_owner_thread(self):
        release = threading.Event()
        owner = threading.get_ident()
        observed = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                self_result = release.wait(2)
                if not self_result:
                    raise AssertionError("owner heartbeat never ran")
                return json.dumps({"answer": "ready"}).encode()

        def heartbeat():
            observed.append(threading.get_ident())
            self.assertIsNone(inference_io.wait_callback.get())
            release.set()

        try:
            with inference_io.on_wait(heartbeat):
                result = inference_io.json_request(lambda *_a, **_k: Response(), None, 2, llm.LLMError)
        finally:
            release.set()
        self.assertEqual(result, {"answer": "ready"})
        self.assertEqual(observed, [owner])
        self.assertIsNone(inference_io.wait_callback.get())


if __name__ == "__main__":
    unittest.main()
