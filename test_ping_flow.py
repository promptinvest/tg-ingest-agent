"""Owner liveness probes remain receipt-only across real dispatch and delivery."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import llm
import router
import store
import tg_ingest_agent as entry
from testlib import make_config
from texts import T


class PingFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.agent = entry.Agent(make_config(
            ALLOWED_CHAT_IDS="1", DB_PATH=":memory:",
            MEDIA_DIR=str(Path(self.temp.name) / "media")))
        self.addCleanup(self.agent.conn.close)
        self.sent = []
        for target, name, replacement in (
                (entry, "tg_call", self.send),
                (self.agent, "send_chat_action", lambda *a, **k: None)):
            patch = mock.patch.object(target, name, side_effect=replacement)
            patch.start()
            self.addCleanup(patch.stop)

    @property
    def conn(self):
        return self.agent.conn

    def send(self, token, method, params, **kwargs):
        self.assertFalse(self.conn.in_transaction)
        self.sent.append((method, copy.deepcopy(params)))
        return {"message_id": 100 + len(self.sent), "text": params.get("text", "")}

    def update(self, text, number=910, **message):
        return {"update_id": number, "message": {
            "message_id": number, "chat": {"id": 1}, "from": {"id": 1},
            "text": text, **message}}

    def seed_failed_history(self):
        for number, text in enumerate((
                "Напомни завтра в 9 - задания аналитика",
                "За чудесное утро с семьёй"), 800):
            update = self.update(text, number)
            store.telegram_update_receive(self.conn, update, 1)
            for _ in range(3):
                store.telegram_update_attempt(self.conn, number)
            store.telegram_update_fail(self.conn, number, "historical installed-layout failure", terminal=True)
            store.convo_add(self.conn, 1, "user", text, source="boss", update_id=number)
            store.convo_add(self.conn, 1, "bot", T("ru", "update_dead_letter"))
        mid = store.insert_message(self.conn, {
            "chat_id": 1, "tg_message_id": 850, "received_at": store._now(),
            "raw_text": "Старая сохранённая заметка о благодарности"})
        store.set_suggestion(self.conn, mid, "Plan", "Old context", "fixture")
        store.confirm_category(self.conn, mid, "Plan")
        store.set_chunks(self.conn, mid, [("Old unrelated gratitude context", [1.0, 0.0])])

    def rows(self, table):
        return [tuple(row) for row in self.conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]

    def test_failed_history_then_ping_and_clarification_use_no_models_or_retrieval(self):
        self.seed_failed_history()
        before = {name: self.rows(name) for name in (
            "messages", "chunks", "reminders", "journal_entries", "task_approvals")}
        old_history = self.rows("conversation")
        old_updates = self.rows("telegram_updates")
        with mock.patch.object(llm, "chat_profile", side_effect=AssertionError("no paid chat")) as chat, \
                mock.patch.object(llm, "embed", side_effect=AssertionError("no embedding")) as embed, \
                mock.patch.object(self.agent, "maybe_curate_conversation") as curate:
            self.assertEqual(self.agent.process_update_batch([
                self.update("Тест"), self.update("Это был просто пинг", 911)]), 911)
            self.agent.process_update_batch([self.update("Тест")])
        chat.assert_not_called()
        embed.assert_not_called()
        curate.assert_not_called()
        replies = [p["text"] for method, p in self.sent if method == "sendMessage"]
        self.assertEqual(replies, ["Я здесь, сообщение получила."] * 2)
        self.assertEqual(self.rows("conversation")[:len(old_history)], old_history)
        self.assertEqual(self.rows("telegram_updates")[:len(old_updates)], old_updates)
        for name, rows in before.items():
            self.assertEqual(self.rows(name), rows, name)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM reply_outbox WHERE state='sent'").fetchone()[0], 2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM issues").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0], 0)

    def test_english_probe_acknowledges_receipt_not_global_health(self):
        with mock.patch.object(llm, "chat_profile", side_effect=AssertionError("no model")):
            self.agent.process_update_batch([self.update("Just checking you're there.")])
        self.assertEqual([p["text"] for m, p in self.sent if m == "sendMessage"],
                         ["I'm here; I received your message."])

    def test_recognizes_only_complete_receipt_intents(self):
        for text in ("Тест!", "пинг", "  Проверка   связи. ", "Ты тут?", "ты здесь?",
                     "просто пинг", "Это был просто пинг", "это была проверка связи",
                     "PING", "Test.", "are you there?", "just a ping", "that was just a ping",
                     "Just checking you're there."):
            with self.subTest(text=text):
                self.assertTrue(router.is_liveness_ping(text))
        for text in ("сохрани тест", "проверь сервер", "тестирование", "ping example.com",
                     "test the backup", "what is a ping?", "напомни завтра про тест",
                     "пинг и покажи заметки", '"пинг"', "`ping`", "тест 2", "/test",
                     "are all services working?", "как дела", "привет", "да", "ок"):
            with self.subTest(text=text):
                self.assertFalse(router.is_liveness_ping(text))

    def test_pending_journal_draft_keeps_ownership_of_next_text(self):
        store.pending_set(self.conn, 1, "journal_edit", {"row_id": 12})
        with mock.patch.object(self.agent, "resolve_journal_edit") as edit, \
                mock.patch.object(self.agent, "reply") as reply:
            self.agent.dispatch(1, self.update("Тест")["message"], "Тест")
        edit.assert_called_once()
        reply.assert_not_called()

    def test_pending_reminder_card_is_unchanged_and_keeps_normal_route(self):
        store.pending_set(self.conn, 1, "reminder", {
            "title": "Test", "due_utc": "2099-01-01T09:00:00+00:00", "card_message_id": 77})
        before = self.rows("pending_actions")
        with mock.patch.object(router, "route", return_value={
                "action": "converse", "params": {}, "confidence": 1}) as route, \
                mock.patch.object(self.agent, "do_converse") as converse:
            self.agent.dispatch(1, self.update("Тест")["message"], "Тест")
        route.assert_called_once()
        converse.assert_called_once()
        self.assertEqual(self.rows("pending_actions"), before)

    def test_reply_forward_quote_and_media_are_not_plain_ping_shortcuts(self):
        variants = (
            {"reply_to_message": {"message_id": 77, "text": "Name the task?"}},
            {"forward_origin": {"type": "user"}}, {"quote": {"text": "ping"}},
            {"external_reply": {"origin": {"type": "user"}}},
            {"photo": [{"file_id": "x"}]}, {"document": {"file_id": "x"}},
            {"voice": {"file_id": "x"}}, {"caption": "ping"})
        for extra in variants:
            with self.subTest(extra=extra), \
                    mock.patch.object(router, "route", return_value={
                        "action": "converse", "params": {}, "confidence": 1}) as route, \
                    mock.patch.object(self.agent, "do_converse") as converse:
                self.agent.dispatch(1, self.update("ping", **extra)["message"], "ping")
                route.assert_called_once()
                converse.assert_called_once()

    def test_real_action_containing_ping_keeps_its_action_route(self):
        with mock.patch.object(router, "route", return_value={
                "action": "reminder_list", "params": {}, "confidence": 1}) as route, \
                mock.patch.object(self.agent, "_reminder_list_body", return_value="Existing reminders") as handler:
            self.agent.process_update_batch([self.update("show reminders about the ping test")])
        route.assert_called_once()
        handler.assert_called_once()

    def test_greeting_still_uses_warm_conversation(self):
        with mock.patch.object(self.agent, "do_converse") as converse, \
                mock.patch.object(router, "route") as route:
            self.agent.process_update_batch([self.update("привет")])
        converse.assert_called_once()
        route.assert_not_called()

    def test_nonowner_ping_is_rejected_before_history_or_delivery(self):
        update = self.update("ping")
        update["message"]["from"]["id"] = 2
        with mock.patch.object(llm, "chat_profile") as chat:
            self.agent.process_update_batch([update])
        chat.assert_not_called()
        self.assertEqual(self.rows("conversation"), [])
        self.assertEqual(self.sent, [])

    def test_live_task_approval_is_not_answered_or_replaced_by_ping(self):
        store.telegram_update_receive(self.conn, self.update("Read reminders", 800), 1)
        store.convo_add(self.conn, 1, "user", "Read reminders", source="boss", update_id=800)
        task, _ = store.assistant_task_create(self.conn, 1, 800, {
            "objective": "Read reminders", "deliverable": "answer", "steps": [{
                "key": "read", "tool": "reminders.read", "input": {}, "bindings": {},
                "depends_on": [], "purpose": "Read reminders"}]})
        step = store.assistant_task_steps(self.conn, task["id"])[0]
        store.task_approval_create(self.conn, task, step, {"title": "Review"}, "hash", {}, "1")
        before = self.rows("task_approvals")
        with mock.patch.object(router, "route", return_value={
                "action": "converse", "params": {}, "confidence": 1}) as route, \
                mock.patch.object(self.agent, "do_converse") as converse:
            self.agent.process_update_batch([self.update("ping")])
        route.assert_called_once()
        converse.assert_called_once()
        self.assertEqual(self.rows("task_approvals"), before)

    def test_answer_to_an_open_question_keeps_normal_interpretation(self):
        store.convo_add(self.conn, 1, "bot", "What should the task be called?")
        with mock.patch.object(router, "route", return_value={
                "action": "converse", "params": {}, "confidence": 1}) as route, \
                mock.patch.object(self.agent, "do_converse") as converse:
            self.agent.process_update_batch([self.update("Test")])
        route.assert_called_once()
        converse.assert_called_once()

    def test_expired_gratitude_invitation_cannot_consume_new_plain_ping(self):
        with mock.patch.object(self.agent, "gratitude_invitation", return_value={
                "title": "Благодарность"}) as invitation, \
                mock.patch.object(self.agent, "_deterministic_capture") as capture, \
                mock.patch.object(llm, "chat_profile", side_effect=AssertionError("no model")):
            self.agent.process_update_batch([self.update("Тест")])
        invitation.assert_not_called()
        capture.assert_not_called()
        self.assertEqual([p["text"] for m, p in self.sent if m == "sendMessage"],
                         ["Я здесь, сообщение получила."])

    def test_live_gratitude_draft_retains_existing_capture_semantics(self):
        store.set_category_kind(self.conn, "Благодарность", "journal")
        store.pending_set(self.conn, 1, "reminder_fired", {"title": "Благодарность"})
        with mock.patch.object(self.agent, "_deterministic_capture") as capture, \
                mock.patch.object(llm, "chat_profile", side_effect=AssertionError("no model")):
            self.agent.process_update_batch([self.update("Тест")])
        capture.assert_called_once()
        self.assertEqual(capture.call_args.kwargs["source"], "fired_gratitude")

    def test_ping_delivery_retry_does_not_regenerate_or_repeat_an_effect(self):
        from tg_api import TelegramError
        with mock.patch.object(entry, "tg_call", side_effect=TelegramError(
                "rate limited", status=429, retry_after=1)), \
                mock.patch.object(llm, "chat_profile", side_effect=AssertionError("no model")):
            self.assertEqual(self.agent.process_update_batch([self.update("ping")]), 910)
        self.assertEqual(self.conn.execute("SELECT state FROM reply_outbox").fetchone()[0], "pending")
        self.conn.execute("UPDATE reply_outbox SET available_at='2000-01-01T00:00:00+00:00'")
        self.conn.commit()
        with mock.patch.object(llm, "chat_profile", side_effect=AssertionError("no model")):
            self.agent.check_reply_deliveries()
        self.assertEqual(self.conn.execute("SELECT state FROM reply_outbox").fetchone()[0], "sent")
        self.assertEqual([p["text"] for m, p in self.sent if m == "sendMessage"],
                         ["I'm here; I received your message."])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
