"""Independent delivered-behavior holdouts for reading recorded work.

Use public durable source/task APIs and a real Agent handler. Only model,
embedding and Telegram transports are replaced; no reader helper is patched.
"""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import action_truth
import llm
import store
import tasking
import tg_ingest_agent
from testlib import make_config


class RecordedWorkHoldoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.agent = tg_ingest_agent.Agent(make_config(
            ALLOWED_CHAT_IDS="1", DB_PATH=":memory:",
            MEDIA_DIR=str(Path(self.tmp.name) / "media")))
        self.conn = self.agent.conn
        self.addCleanup(self.conn.close)
        self.sequence = 460000

    def owner_turn(self, text, source="boss"):
        self.sequence += 1
        update_id = self.sequence
        message = {"message_id": update_id, "chat": {"id": 1},
                   "from": {"id": 1}, "text": text}
        if source == "forward":
            message["forward_origin"] = {
                "type": "channel", "date": 1792915200,
                "chat": {"id": -100654321, "title": "Synthetic source"},
                "message_id": 17}
        store.telegram_update_receive(self.conn, {"update_id": update_id,
                                                   "message": message}, 1)
        store.convo_add(self.conn, 1, "user", text, source=source,
                        update_id=update_id, tg_message_id=update_id)
        return update_id

    def task(self, *, purpose="Check the reminder times", repeated=False):
        text = ("Read my existing reminders again for packing review" if repeated
                else "Read my existing reminders for packing review")
        update_id = self.owner_turn(text)
        row, created = store.assistant_task_create(self.conn, 1, update_id, {
            "objective": "Packing review", "deliverable": "answer", "steps": [{
                "key": "reminders", "tool": "reminders.read", "input": {},
                "bindings": {}, "depends_on": [], "purpose": purpose}]})
        self.assertTrue(created)
        return row, update_id

    def task_state(self):
        return {table: [tuple(row) for row in self.conn.execute(
            f"SELECT * FROM {table}")]
                for table in ("assistant_tasks", "assistant_task_steps",
                              "task_step_attempts", "tool_receipts", "task_approvals",
                              "pending_actions", "reminders", "boss_profile_items")}

    def converse(self, text, model_reply, *, expected_calls, lang="en"):
        before = self.task_state()
        seen, delivered = [], []

        def model(cfg, conn, skill, messages, **kwargs):
            self.assertEqual((skill, kwargs.get("profile")), ("converse", "converse_warm"))
            seen.append(copy.deepcopy(messages))
            if expected_calls == 0:
                raise AssertionError("unambiguous recorded read must not call a model")
            return (model_reply[len(seen) - 1] if isinstance(model_reply, (list, tuple))
                    else model_reply)

        with mock.patch.object(llm, "chat_profile", side_effect=model) as chat, \
                mock.patch.object(llm, "embed", return_value=[[1.0, 0.0]]), \
                mock.patch.object(tg_ingest_agent, "tg_call", side_effect=AssertionError(
                    "holdout must not contact Telegram")), \
                mock.patch.object(self.agent, "send_chat_action"), \
                mock.patch.object(self.agent, "maybe_curate_conversation"), \
                mock.patch.object(self.agent, "reply", side_effect=lambda c, t, **k:
                                  delivered.append(t) or True):
            self.agent.do_converse(1, lang, text)
        self.assertEqual(chat.call_count, expected_calls)
        self.assertEqual(self.task_state(), before)
        self.assertEqual(len(delivered), 1)
        return delivered[0], seen

    def test_unambiguous_owner_read_returns_actual_record_without_model_or_execution(self):
        self.task()
        question = ("What still needs doing on Packing review? Give its recorded status "
                    "and the next step. Do not execute anything.")
        self.owner_turn(question)
        delivered, seen = self.converse(question, None, expected_calls=0)
        self.assertEqual(seen, [])
        self.assertIn("Packing review", delivered)
        self.assertIn("planned", delivered.casefold())
        self.assertIn("Check the reminder times", delivered)
        self.assertFalse(action_truth.freeform_claims_action(delivered))

    def test_same_title_tasks_keep_model_clarification_instead_of_selecting_one(self):
        self.task()
        self.task(purpose="Check the reminder dates", repeated=True)
        question = ("What still needs doing on Packing review? Give its recorded status "
                    "and the next step. Do not execute anything.")
        self.owner_turn(question)
        answer = ("There are two recorded Packing review tasks. Which one do you mean: "
                  "the earlier reminder-times review or the later reminder-dates review?")
        delivered, seen = self.converse(question, answer, expected_calls=1)
        self.assertEqual(delivered, answer)
        context = "\n".join(str(m["content"]) for m in seen[0])
        self.assertIn("Check the reminder times", context)
        self.assertIn("Check the reminder dates", context)

    def test_multiple_explicit_task_references_cannot_silently_select_the_first(self):
        first, _ = self.task()
        second, _ = self.task(purpose="Check the reminder dates", repeated=True)
        for copula in ("is", "are"):
            question = (f"What {copula} the recorded status and next step for task "
                        f"#{first['id']} and #{second['id']}?")
            with self.subTest(question=question):
                self.owner_turn(question)
                answer = ("Both recorded tasks are planned. The first next step is checking "
                          "reminder times; the second is checking reminder dates.")
                delivered, _seen = self.converse(question, answer, expected_calls=1)
                self.assertEqual(delivered, answer)

    def test_edited_task_source_cannot_be_read_as_an_unchanged_authoritative_plan(self):
        task, source_update = self.task()
        original_hash = task["source_hash"]
        edited = "Do not read reminders; discuss packing options instead."
        self.assertTrue(store.convo_set_text(self.conn, 1, source_update, edited))
        current_source = store.assistant_task_source(self.conn, 1, source_update)
        self.assertNotEqual(tasking.source_hash(current_source["text"]), original_hash)
        question = ("What still needs doing on Packing review? Give its recorded status "
                    "and the next step. Do not execute anything.")
        self.owner_turn(question)
        answer = ("The original request has changed. Please confirm which version you "
                  "want to discuss before relying on the old plan.")
        delivered, seen = self.converse(question, answer, expected_calls=1)
        self.assertEqual(delivered, answer)
        self.assertNotIn("Check the reminder times", delivered)
        self.assertIn(edited, "\n".join(str(m["content"]) for m in seen[0]))
        self.assertEqual(store.assistant_task_get(self.conn, task["id"], 1)["source_hash"],
                         original_hash)

    def test_forwarded_question_cannot_trigger_trusted_owner_read(self):
        self.task()
        question = ("What still needs doing on Packing review? Give its recorded status "
                    "and the next step. Do not execute anything.")
        self.owner_turn(question, source="forward")
        answer = ("That forwarded question is source material. If you want to review your "
                  "packing task, please ask in your own message.")
        delivered, seen = self.converse(question, answer, expected_calls=1)
        self.assertEqual(delivered, answer)
        self.assertEqual(store.convo_row_source(
            store.convo_recent(self.conn, 1, limit=1)[0]), "forward")

    def test_mixed_saved_date_request_retains_model_and_actual_source_evidence(self):
        self.task()
        source_text = "Flight date: 12 November 2026."
        message_id = store.insert_message(self.conn, {
            "chat_id": 1, "tg_message_id": 960001,
            "received_at": "2026-10-25T11:00:00+00:00", "raw_text": source_text})
        store.set_suggestion(self.conn, message_id, "Travel", "Flight ticket", "holdout")
        store.confirm_category(self.conn, message_id, "Travel")
        store.set_chunks(self.conn, message_id, [(source_text, [1.0, 0.0])])
        question = ("What is the recorded status and next step for Packing review, and "
                    "what date is the flight in my saved source? Reply in two numbered items.")
        self.owner_turn(question)
        answer = ("1. Status: Packing review is planned; its reminder check is still pending.\n"
                  "2. Next step: Check the reminder times; the flight date in the source is 12 November 2026.")
        delivered, seen = self.converse(question, answer, expected_calls=1)
        self.assertEqual(delivered, answer)
        context = "\n".join(str(m["content"]) for m in seen[0])
        self.assertIn(source_text, context)
        self.assertIn("Check the reminder times", context)
        self.assertIn("12 November 2026", delivered)

    def test_ordinary_chat_keeps_the_existing_model_path_even_with_recorded_work(self):
        self.task()
        question = "How about a quiet conversation about tea?"
        self.owner_turn(question)
        answer = ("A cup of tea sounds lovely. Tell me what kind you are in the mood for "
                  "and we can have a quiet conversation.")
        delivered, _seen = self.converse(question, answer, expected_calls=1)
        self.assertEqual(delivered, answer)

    def test_other_current_intents_are_not_discarded_by_the_status_reader(self):
        self.task()
        cases = [
            ("What is the status of Packing review, and draft a short message to Alice about it.",
             "Packing review is planned. Draft for Alice: 'The review is pending; the next step is checking reminder times.'"),
            ("What is the status of Packing review? Translate it into French.",
             "État enregistré : Packing review est planifié. La prochaine étape est la vérification des horaires des rappels."),
            ("What is the status of Packing review and how much does it cost?",
             "Packing review is planned. No price appears in the available record, so a price needs a separate source."),
            ("What is the status of Packing review and the deadline?",
             "Packing review is planned. There is no deadline in the available record; please identify the source that contains it."),
        ]
        for question, answer in cases:
            with self.subTest(question=question):
                self.owner_turn(question)
                delivered, _seen = self.converse(question, answer, expected_calls=1)
                self.assertEqual(delivered, answer)

    def test_mentioning_an_old_or_quoted_format_is_not_a_current_format_request(self):
        cases = [
            ("en", "Why did you answer with exactly two numbered items?",
             "You are discussing a previous response. That wording describes its format; it does not by itself request that format again."),
            ("en", "What does ‘exactly two numbered items’ mean?",
             "The phrase describes a response with two numbered entries. This explanation can use ordinary prose."),
            ("ru", "Что значит «ровно двумя нумерованными пунктами»?",
             "Это описание формы ответа: два пункта с номерами. Объяснение самой фразы может быть обычным текстом."),
            ("ru", "Почему ты ответила ровно двумя нумерованными пунктами?",
             "Ты спрашиваешь о форме прошлого ответа. Сам этот вопрос не требует повторять прежний формат."),
        ]
        for lang, question, answer in cases:
            with self.subTest(question=question):
                self.owner_turn(question)
                delivered, _seen = self.converse(question, answer, expected_calls=1, lang=lang)
                self.assertEqual(delivered, answer)

    def test_actual_current_numbered_requests_still_work_in_both_languages(self):
        cases = [
            ("en", "Explain tea briefly. Reply in exactly two numbered items.",
             "1. Tea is a drink from steeped leaves.\n2. Its flavor depends on the leaves and preparation."),
            ("ru", "Расскажи о чае. Ответь ровно двумя нумерованными пунктами.",
             "1. Чай готовят, заваривая листья водой.\n2. Вкус зависит от листьев и способа приготовления."),
        ]
        for lang, question, answer in cases:
            with self.subTest(question=question):
                self.owner_turn(question)
                delivered, _seen = self.converse(question, answer, expected_calls=1, lang=lang)
                self.assertEqual(delivered, answer)

    def test_extra_top_level_numbers_require_the_bounded_format_correction(self):
        cases = [
            ("Explain tea briefly. Reply in exactly two numbered items.",
             "1. Tea uses leaves.\n2. Water steeps them.\n10. An extra numbered item.",
             "1. Tea uses leaves.\n2. Water steeps them."),
            ("Explain tea briefly. Reply in exactly eight numbered items.",
             "\n".join(f"{i}. Tea fact number {i}." for i in range(1, 10)),
             "\n".join(f"{i}. Tea fact number {i}." for i in range(1, 9))),
        ]
        for question, rejected, corrected in cases:
            with self.subTest(question=question):
                self.owner_turn(question)
                delivered, _seen = self.converse(question, [rejected, corrected], expected_calls=2)
                self.assertEqual(delivered, corrected)


if __name__ == "__main__":
    unittest.main()
