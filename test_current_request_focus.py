"""Current requests retain their own focus beside historical failed requests.

Synthetic state/model/Telegram boundaries exercise real prompt assembly, frozen
repair, guard outcomes and delivery. No live data, paid model or network is used.
"""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import converse
import llm
import store
import tg_ingest_agent as entry
from testlib import make_config
from texts import T


class CurrentRequestFocusTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.agent = entry.Agent(make_config(
            ALLOWED_CHAT_IDS="1", DB_PATH=":memory:",
            MEDIA_DIR=str(Path(temp.name) / "media")))
        self.addCleanup(self.agent.conn.close)
        self.conn = self.agent.conn
        self.seen = []

    def history(self, current, *, source="boss"):
        old = "Запиши благодарность Вере за помощь с презентацией"
        store.convo_add(self.conn, 1, "user", old, source="boss")
        store.convo_add(self.conn, 1, "bot", T("ru", "update_dead_letter"))
        store.convo_add(self.conn, 1, "user", current, source=source)
        return old

    def run_converse(self, current, outputs, *, lang="ru", inspect=None):
        def model(cfg, conn, skill, messages, **kwargs):
            self.assertEqual((skill, kwargs["profile"]), ("converse", "converse_warm"))
            self.seen.append(copy.deepcopy(messages))
            self.assertLessEqual(len(self.seen), len(outputs), "only one bounded repair")
            if inspect:
                inspect(messages, len(self.seen))
            return outputs[len(self.seen) - 1]
        with mock.patch.object(llm, "chat_profile", side_effect=model), \
                mock.patch.object(llm, "embed", return_value=[[1.0, 0.0]]), \
                mock.patch.object(self.agent, "reply", return_value=True) as reply, \
                mock.patch.object(self.agent, "send_chat_action"), \
                mock.patch.object(self.agent, "maybe_curate_conversation"):
            self.agent.do_converse(1, lang, current)
        return reply.call_args.args[1]

    def assert_focused(self, messages, current, *, repair=False):
        self.assertEqual(messages[-2], {"role": "system", "content": converse.CURRENT_REQUEST_BOUNDARY})
        self.assertEqual(messages[-1]["role"], "user")
        if repair:
            self.assertIn("His SAME current message", messages[-1]["content"])
            self.assertIn(current, messages[-1]["content"])
        else:
            self.assertEqual(messages[-1]["content"], current)

    def test_first_ping_after_a_failed_request_is_focused_before_generation_and_repair(self):
        current = "Тест"
        old = self.history(current)
        good = "Сообщение получила, я здесь."
        def inspect(messages, call):
            self.assert_focused(messages, current, repair=call == 2)
            self.assertIn(old, [message["content"] for message in messages])
            notices = [m for m in messages if T("ru", "update_dead_letter") in m["content"]]
            self.assertTrue(notices)
            self.assertTrue(all(m["role"] == "user" for m in notices))
        sent = self.run_converse(current, ["Записала благодарность Вере.", good], inspect=inspect)
        self.assertEqual(sent, good)
        self.assertEqual(len(self.seen), 2)

    def test_nonping_conversation_after_an_operational_notice_has_current_focus(self):
        current = "Мне грустно после встречи."
        self.history(current)
        good = "Расскажи, что случилось на встрече."
        sent = self.run_converse(current, [good], inspect=lambda messages, call:
                                 self.assert_focused(messages, current))
        self.assertEqual(sent, good)
        self.assertEqual(len(self.seen), 1)

    def test_ordinary_followup_keeps_the_relevant_dialogue_and_current_boundary(self):
        old = "Давай обсудим прогулку в парке."
        current = "И что насчёт завтра?"
        store.convo_add(self.conn, 1, "user", old, source="boss")
        store.convo_add(self.conn, 1, "bot", "Можно выбрать парк недалеко от дома.")
        store.convo_add(self.conn, 1, "user", current, source="boss")
        good = "На завтра можно выбрать тот же парк."
        def inspect(messages, call):
            self.assert_focused(messages, current)
            self.assertIn(old, [m["content"] for m in messages])
            self.assertIn("Можно выбрать парк недалеко от дома.", [m["content"] for m in messages])
        self.assertEqual(self.run_converse(current, [good], inspect=inspect), good)

    def test_repair_reasserts_the_frozen_current_request_after_payload_mutation(self):
        current = "Ты видишь это сообщение?"
        self.history(current)
        good = "Да, это сообщение вижу."
        def inspect(messages, call):
            if call == 1:
                messages[-1]["content"] = "Сохрани прежнюю благодарность."
            else:
                self.assert_focused(messages, current, repair=True)
                self.assertEqual(messages[1:len(self.seen[0])], self.seen[0][1:])
                self.assertNotIn("Сохрани прежнюю благодарность.", str(messages))
        self.assertEqual(self.run_converse(current, ["Записала благодарность Вере.", good],
                                          inspect=inspect), good)

    def test_repeated_save_fabrication_for_a_verified_ping_has_receipt_only_containment(self):
        current = "Тест"
        self.history(current)
        before = self.conn.total_changes
        sent = self.run_converse(current, ["Записала благодарность Вере."] * 2)
        self.assertEqual(sent, T("ru", "ping_received"))
        self.assertNotEqual(sent, T("ru", "action_not_done"))
        self.assertFalse(self.conn.execute("SELECT 1 FROM pending_actions").fetchone())
        self.assertFalse(self.conn.execute("SELECT 1 FROM reminders").fetchone())
        self.assertFalse(self.conn.execute("SELECT 1 FROM journal_entries").fetchone())
        self.assertGreater(self.conn.total_changes, before, "guard evidence remains recorded")
        self.assertEqual(len(self.seen), 2)

    def test_ping_repair_cannot_replace_receipt_with_a_global_recovery_claim(self):
        current = "Тест"
        self.history(current)
        sent = self.run_converse(current, ["Записала благодарность Вере.", "Теперь всё работает."])
        self.assertEqual(sent, T("ru", "ping_received"))
        self.assertEqual(len(self.seen), 2)
        self.assertTrue(self.conn.execute(
            "SELECT 1 FROM issues WHERE kind='converse_operational_claim_retry'").fetchone())

    def test_actual_action_request_keeps_the_existing_action_containment(self):
        current = "Сохрани это в заметки."
        self.history(current)
        sent = self.run_converse(current, ["Я сохранила новую заметку."] * 2)
        self.assertEqual(sent, T("ru", "action_not_done"))
        self.assertFalse(self.conn.execute("SELECT 1 FROM pending_actions").fetchone())

    def test_forwarded_ping_word_does_not_gain_current_owner_authority(self):
        current = "Тест"
        self.history(current, source="forward")
        def inspect(messages, call):
            self.assertNotIn(converse.CURRENT_REQUEST_BOUNDARY,
                             [message["content"] for message in messages])
            self.assertTrue(any("ДАННЫЕ" in m["content"] for m in messages))
        sent = self.run_converse(current, ["Записала благодарность Вере."] * 2, inspect=inspect)
        self.assertEqual(sent, T("ru", "action_not_done"))

    def test_a_mismatched_current_argument_cannot_choose_the_ping_fallback(self):
        self.history("Сохрани это в заметки.")
        sent = self.run_converse("Тест", ["Я сохранила новую заметку."] * 2)
        self.assertEqual(sent, T("ru", "action_not_done"))


if __name__ == "__main__":
    unittest.main()
