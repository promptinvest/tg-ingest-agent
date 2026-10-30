"""Regressions for attributive reminders without weakening action claims."""
import json
from contextlib import ExitStack
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest import mock

import action_truth


FIRST_RETAINED = ('Recorded status: planned — nothing on it has been marked done. '
    'The step still sitting there is "Read the saved reminders," and it\'s still pending.\n\n'
    'So the next move is exactly that: pull up the saved reminders for the Budget review. Want me to show them?')
CORRECTIVE_RETAINED = ('Status: planned — nothing on the Budget review has been marked done.\n\n'
    'Next step still pending: read the saved reminders. That\'s where it stands.')


class SavedReminderNounPhraseTests(unittest.TestCase):
    def test_both_retained_truthful_status_answers_are_not_completed_actions(self):
        for value in (FIRST_RETAINED, CORRECTIVE_RETAINED):
            with self.subTest(value=value):
                self.assertIsNone(action_truth.action_claim_match(value))

    def test_exact_attributive_phrase_in_references_lists_and_questions(self):
        for value in ('Read the saved reminders.', 'The saved reminders are still available.',
                      'Next step: read the saved reminders.', '1. Read the saved reminders.',
                      'Can you list the saved reminders?', 'The task is "Read the saved reminders".',
                      'Read THE SAVED REMINDERS.', 'Read the\u00a0saved\u00a0reminders.',
                      'Read the _saved_ reminders.'):
            with self.subTest(value=value):
                self.assertIsNone(action_truth.action_claim_match(value))

    def test_finite_bare_passive_and_completed_saves_stay_blocked(self):
        for value in ('I saved reminders.', 'Saved reminders.', 'I saved the reminders.',
                      'I have saved reminders.', 'Reminders saved.', 'The reminders were saved.',
                      'I saved the saved reminders.', 'Done — saved reminders.',
                      'Done — the saved reminders.', 'The saved reminders are done.',
                      'Done. The saved reminders.', 'Done! The saved reminders.',
                      'Готово. The saved reminders.',
                      'Готово — сохранила напоминания.', '1. Saved reminders.',
                      'I **saved** reminders.', 'I _saved_ reminders.',
                      'I\u00a0saved\u00a0reminders.', 'I sa\u200bved reminders.'):
            with self.subTest(value=value):
                self.assertIsNotNone(action_truth.action_claim_match(value))

    def test_same_sentence_other_claims_stay_conservatively_blocked(self):
        for value, verb in (
            ('Read the saved reminders; I saved a reminder.', 'saved'),
            ('The saved reminders are visible. I saved it.', 'saved'),
            ('The saved reminders are visible; I deleted a note.', 'deleted'),
            ('Read the saved reminders, then I updated the task.', 'updated'),
            ('Read the saved reminders.\nSaved reminders.', 'saved'),
            ('Read the saved reminders; I added a new task — want the list?', 'added'),
            ('Read the saved reminders; then read the saved reminders; I saved a note.', 'saved'),
        ):
            with self.subTest(value=value):
                match = action_truth.action_claim_match(value)
                self.assertIsNotNone(match)

    def test_long_same_sentence_tail_cannot_hide_a_later_no_object_claim(self):
        for tail in ('I saved it.', 'I deleted it.', 'I updated it.', 'Saved it.'):
            value = 'The saved reminders ' + ('remain visible beside the calendar ' * 8) + '; ' + tail
            with self.subTest(tail=tail):
                self.assertIsNotNone(action_truth.action_claim_match(value))

    def test_completed_status_or_passive_cannot_hide_behind_the_noun_phrase(self):
        for value in ('The saved reminders have been completed.', 'The saved reminders are finished.',
                      'The saved reminders are complete.', 'I completed the saved reminders.',
                      'I executed the saved reminders.', 'The saved reminders have been processed.',
                      'The saved reminders are resolved.', 'The saved reminders have been handled.',
                      'I performed the saved reminders.', 'The saved reminders have been fulfilled.',
                      'The saved reminders are taken care of.', 'The saved reminders were dealt with.'):
            with self.subTest(value=value):
                self.assertIsNotNone(action_truth.action_claim_match(value))

    def test_later_sentence_claim_is_detected_after_the_noun_phrase_is_skipped(self):
        for value, expected in (
            ('Read the saved reminders. I saved it.', 'I saved'),
            ('Read the saved reminders.\nSaved reminders.', 'Saved'),
            ('The saved reminders are visible. I deleted it.', 'I deleted'),
        ):
            with self.subTest(value=value):
                match = action_truth.action_claim_match(value)
                self.assertIsNotNone(match)
                self.assertIn(expected, match)

    def test_genuine_earlier_claim_is_not_excused_by_a_later_noun_phrase(self):
        for value in ('I saved a note; read the saved reminders.',
                      'I deleted a reminder. The saved reminders are still visible.',
                      'Done — updated the task; next, read the saved reminders.'):
            with self.subTest(value=value):
                self.assertIsNotNone(action_truth.action_claim_match(value))

    def test_other_determiners_objects_and_verbs_are_outside_this_proposal(self):
        for value in ('Read saved reminders.', 'Read your saved reminders.',
                      'Read the saved notes.', 'Read the deleted reminders.',
                      'Read the newly saved reminders.', 'Read the saved reminder.'):
            with self.subTest(value=value):
                self.assertIsNotNone(action_truth.action_claim_match(value))


class RetainedNativeAnswerTests(unittest.TestCase):
    def test_each_retained_answer_reaches_native_reply_without_new_model_request(self):
        import llm
        import store
        import tasks_svc
        import tg_ingest_agent
        from testlib import make_config

        def denied(*args, **kwargs):
            raise AssertionError("external model/network/process effect forbidden")

        def receive(agent, number, text):
            store.telegram_update_receive(agent.conn, {
                "update_id": number, "message": {"message_id": number,
                    "chat": {"id": 1}, "from": {"id": 1}, "text": text}}, 1)
            store.convo_add(agent.conn, 1, "user", text, source="boss",
                            update_id=number, tg_message_id=number)
            agent._current_update_id = agent._active_update_id = number
            agent._reply_sequence = 0

        def snapshot(conn):
            return {table: [tuple(row) for row in conn.execute(
                "SELECT * FROM " + table + " ORDER BY id")]
                for table in ("reminders", "assistant_tasks",
                              "assistant_task_steps", "task_approvals")}

        source = "Read my saved reminders for the Budget review."
        question = ("What still needs doing on the Budget review? Tell me the recorded "
                    "task status and next step, without claiming any step has been completed.")
        plan = {"objective": "Budget review", "deliverable": "answer", "steps": [{
            "key": "read", "tool": "reminders.read", "input": {}, "bindings": {},
            "depends_on": [], "purpose": "Read the saved reminders"}]}
        for raw in (FIRST_RETAINED, CORRECTIVE_RETAINED):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
                for owner, name in ((socket, "create_connection"), (socket.socket, "connect"),
                                    (subprocess, "Popen"), (llm, "transport_chat"), (llm, "embed")):
                    stack.enter_context(mock.patch.object(owner, name, side_effect=denied))
                for module in (tg_ingest_agent, tasks_svc):
                    stack.enter_context(mock.patch.object(module, "tg_call", return_value={"message_id": 700}))
                stack.enter_context(mock.patch.object(tg_ingest_agent.Agent, "send_chat_action"))
                cfg = make_config(ALLOWED_CHAT_IDS="1",
                    DB_PATH=str(Path(temporary) / "synthetic.db"),
                    MEDIA_DIR=str(Path(temporary) / "media"),
                    TASK_ARTIFACTS_DIR=str(Path(temporary) / "tg-ingest-agent" / "task-artifacts"),
                    TASK_WORKER_SPOOL=str(Path(temporary) / "cara-worker" / "spool"), CARA_FLEET_ENABLED="false")
                agent = tg_ingest_agent.Agent(cfg)
                stack.callback(agent.conn.close)
                receive(agent, 41, source)
                with mock.patch.object(llm, "chat_profile", return_value=json.dumps(plan)):
                    agent.do_task_start(1, "en", source, 41)
                task = store.assistant_task_by_source(agent.conn, 1, 41)
                self.assertEqual(task["status"], "planned")
                store.reminder_add(agent.conn, 1, "Call Alice", "2099-01-01T09:00:00+00:00")
                receive(agent, 42, question)
                before = snapshot(agent.conn)
                with mock.patch.object(llm, "chat_profile", side_effect=[raw]) as chat, \
                     mock.patch.object(agent, "reply", return_value=True) as reply, \
                     mock.patch.object(agent, "maybe_curate_conversation"):
                    agent.do_converse(1, "en", question, 42)
                self.assertEqual(chat.call_count, 1)
                self.assertEqual(chat.call_args.kwargs["profile"], "converse_warm")
                reply.assert_called_once_with(1, raw)
                self.assertEqual(snapshot(agent.conn), before)
                self.assertEqual(agent.conn.execute(
                    "SELECT COUNT(*) FROM issues WHERE kind IN "
                    "('converse_action_claim', 'converse_action_repaired')").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
