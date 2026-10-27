"""Operational failures stay historical data, never a fresh model-owned status.

All fixtures use scratch persistence and fake model/Telegram transports. The
delivery bookkeeping, history replay, conversation guards and curator are real.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import converse
import llm
import memory_curator
import store
import tg_ingest_agent as entry
from testlib import make_config
from texts import T


class OperationalConversationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        cfg = make_config(ALLOWED_CHAT_IDS="1", DB_PATH=":memory:",
                          MEDIA_DIR=str(Path(self.temp.name) / "media"))
        self.agent = entry.Agent(cfg)
        self.addCleanup(self.agent.conn.close)
        self.sent = []
        self.transport = mock.patch.object(entry, "tg_call", side_effect=self.send)
        self.transport.start()
        self.addCleanup(self.transport.stop)

    @property
    def conn(self):
        return self.agent.conn

    def send(self, token, method, params, **kwargs):
        self.assertFalse(self.conn.in_transaction)
        self.sent.append((method, copy.deepcopy(params)))
        return {"message_id": 100 + len(self.sent), "text": params.get("text", "")}

    def owner_turn(self, text, update=910):
        store.telegram_update_receive(self.conn, {"update_id": update, "message": {
            "message_id": update, "chat": {"id": 1}, "from": {"id": 1},
            "text": text}}, 1)
        store.convo_add(self.conn, 1, "user", text, source="boss", update_id=update)
        self.agent._active_update_id = update
        return text

    def converse_with(self, text, outputs, lang="ru"):
        seen = []

        def model(cfg, conn, skill, messages, **kwargs):
            self.assertEqual((skill, kwargs["profile"]), ("converse", "converse_warm"))
            seen.append(copy.deepcopy(messages))
            self.assertLessEqual(len(seen), len(outputs), "no unbounded corrective loop")
            return outputs[len(seen) - 1]

        with mock.patch.object(llm, "chat_profile", side_effect=model), \
                mock.patch.object(llm, "embed", return_value=[[1.0, 0.0]]), \
                mock.patch.object(self.agent, "send_chat_action"), \
                mock.patch.object(self.agent, "maybe_curate_conversation"):
            self.agent.do_converse(1, lang, text)
        replies = [params["text"] for method, params in self.sent if method == "sendMessage"]
        return replies[-1], seen

    def assert_notice_is_history_data(self, lang):
        notice = T(lang, "update_dead_letter")
        store.convo_add(self.conn, 1, "bot", notice)
        text = self.owner_turn("А сейчас?" if lang == "ru" else "And now?")
        before = [tuple(row) for row in self.conn.execute("SELECT * FROM conversation ORDER BY id")]
        messages = converse.build_messages(self.conn, 1, lang)
        containing = [message for message in messages if notice in message["content"]]
        self.assertTrue(containing, "the original notice remains available as historical evidence")
        self.assertTrue(all(message["role"] == "user" for message in containing))
        self.assertTrue(all("historical" in message["content"].casefold() for message in containing))
        self.assertEqual(messages[-1], {"role": "user", "content": text})
        self.assertEqual(before, [tuple(row) for row in self.conn.execute(
            "SELECT * FROM conversation ORDER BY id")])

    def test_legacy_russian_failure_notice_is_not_an_assistant_turn(self):
        self.assert_notice_is_history_data("ru")

    def test_legacy_english_failure_notice_is_not_an_assistant_turn(self):
        self.assert_notice_is_history_data("en")

    def test_real_dead_letter_notice_is_preserved_but_not_replayed_as_cara_dialogue(self):
        self.agent._last_turn_lang = "ru"
        self.agent._notify_dead_letter(1)
        notice = T("ru", "update_dead_letter")
        self.assertEqual(self.sent[-1][1]["text"], notice)
        row = self.conn.execute("SELECT * FROM conversation ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual((row["role"], row["text"]), ("bot", notice))
        self.owner_turn("А сейчас?")
        replay = converse.build_messages(self.conn, 1, "ru")
        self.assertFalse(any(m["role"] == "assistant" and notice in m["content"] for m in replay))

    def test_owner_quotes_and_unrelated_cara_difficulty_remain_ordinary_history(self):
        quoted = T("en", "update_dead_letter")
        store.convo_add(self.conn, 1, "user", quoted, source="boss")
        store.convo_add(self.conn, 1, "bot", "I couldn't process the invoice yesterday.")
        self.owner_turn("What did that notice mean?")
        replay = converse.build_messages(self.conn, 1, "en")
        self.assertIn({"role": "user", "content": quoted}, replay)
        self.assertIn({"role": "assistant", "content": "I couldn't process the invoice yesterday."}, replay)

    def test_current_false_failure_gets_one_same_context_correction(self):
        for _ in range(5):
            store.convo_add(self.conn, 1, "bot", T("ru", "update_dead_letter"))
        text = self.owner_turn("А сейчас?")
        final = "Твоё сообщение получила. Что хочешь проверить?"
        sent, calls = self.converse_with(text, [T("ru", "update_dead_letter"), final])
        self.assertEqual(sent, final)
        self.assertEqual(len(calls), 2)
        for payload in calls:
            self.assertIn(text, [m["content"] for m in payload])
            self.assertIn("CURRENT MESSAGE", "\n".join(m["content"] for m in payload))
        self.assertEqual(self.conn.execute("SELECT attempts FROM telegram_updates WHERE update_id=910").fetchone()[0], 0)

    def test_repeated_false_failure_uses_received_turn_fallback_without_a_third_call(self):
        text = self.owner_turn("And now?")
        notice = T("en", "update_dead_letter")
        sent, calls = self.converse_with(text, [notice, notice], "en")
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(sent, notice)
        self.assertNotIn("failed inbox", sent.casefold())
        self.assertEqual(sent, T("en", "message_received"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM pending_actions").fetchone()[0], 0)

    def test_operational_notices_are_not_curator_life_evidence(self):
        notice = T("en", "update_dead_letter")
        store.convo_add(self.conn, 1, "bot", notice)
        self.owner_turn("And now?")
        before = sorted(tuple(row) for row in store.life_facts(self.conn))
        with mock.patch.object(llm, "chat_profile", return_value=json.dumps({
                "cara_life": [{"kind": "fact", "text": "Cannot process messages.",
                               "evidence": notice}]})) as model:
            result = memory_curator.curate_conversation(self.conn, self.agent.cfg, 1)
        self.assertEqual(result["life"], 0)
        model.assert_not_called()
        self.assertEqual(sorted(tuple(row) for row in store.life_facts(self.conn)), before)

    def test_real_life_and_owner_fact_still_learn_beside_an_operational_notice(self):
        notice = T("en", "update_dead_letter")
        store.convo_add(self.conn, 1, "bot", notice)
        store.convo_add(self.conn, 1, "bot", "I like baking bread.")
        self.owner_turn("I prefer short replies.")
        payloads = []

        def model(cfg, conn, skill, messages, **kwargs):
            payloads.append(copy.deepcopy(messages))
            return json.dumps({
                "cara_life": [{"kind": "hobby", "text": "Likes baking bread.",
                               "evidence": "I like baking bread."},
                              {"kind": "moment", "text": "Cannot process messages.",
                               "evidence": notice}],
                "boss_facts": [{"kind": "tone", "text": "He prefers short replies.",
                                "evidence": "I prefer short replies."}]})

        with mock.patch.object(llm, "chat_profile", side_effect=model):
            result = memory_curator.curate_conversation(self.conn, self.agent.cfg, 1)
        self.assertEqual((result["life"], result["boss"]), (1, 1))
        self.assertNotIn(notice, "\n".join(m["content"] for m in payloads[0]))
        self.assertTrue(any(row["text"] == "Likes baking bread." for row in store.life_facts(self.conn)))
        self.assertTrue(self.conn.execute("SELECT 1 FROM boss_profile_items WHERE value=?",
                                         ("He prefers short replies.",)).fetchone())

    def test_current_failure_is_also_rejected_on_an_action_repair(self):
        text = self.owner_turn("Do not save anything. Just answer my question.")
        sent, calls = self.converse_with(text, ["I saved the note.",
                                               T("en", "update_dead_letter")], "en")
        self.assertEqual(len(calls), 2)
        self.assertEqual(sent, T("en", "message_received"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM pending_actions").fetchone()[0], 0)

    def test_failure_guard_preserves_action_and_numbered_format_guards(self):
        text = self.owner_turn("Reply in exactly two numbered items: what can you read?")
        sent, calls = self.converse_with(text, [T("en", "update_dead_letter"),
                                               "1. I saved the note.\n2. All done."], "en")
        self.assertEqual(len(calls), 2)
        self.assertNotIn("I saved", sent)
        self.assertEqual(sent, self.agent._format_failure("en"))

    def test_owner_requested_verbatim_notice_is_data_not_a_runtime_claim(self):
        notice = T("en", "update_dead_letter")
        text = self.owner_turn("Repeat exactly this old notice: " + notice)
        sent, calls = self.converse_with(text, [notice], "en")
        self.assertEqual(len(calls), 1)
        self.assertEqual(sent, notice)

    def test_real_gateway_error_is_still_sent_and_marked_as_an_operational_notice(self):
        text = self.owner_turn("Can you answer this?")
        with mock.patch.object(llm, "chat_profile", side_effect=llm.LLMError("fixture outage")), \
                mock.patch.object(llm, "embed", return_value=[[1.0, 0.0]]), \
                mock.patch.object(self.agent, "send_chat_action"):
            self.agent.do_converse(1, "en", text)
        self.assertEqual(self.sent[-1][1]["text"], T("en", "llm_error"))
        row = self.conn.execute("SELECT * FROM conversation ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual((row["text"], row["source"]), (T("en", "llm_error"), "operational:llm_error"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM reply_outbox WHERE state='sent'").fetchone()[0], 1)

    def test_real_budget_stop_keeps_its_values_and_operational_provenance(self):
        text = self.owner_turn("Ответь, пожалуйста.")
        error = llm.BudgetExceeded("day", 2.01, 2.0)
        with mock.patch.object(llm, "chat_profile", side_effect=error), \
                mock.patch.object(llm, "embed", return_value=[[1.0, 0.0]]), \
                mock.patch.object(self.agent, "send_chat_action"):
            self.agent.do_converse(1, "ru", text)
        expected = T("ru", "budget_stop", spent=error.spent, limit=error.limit,
                     period=T("ru", "period_day"))
        self.assertEqual(self.sent[-1][1]["text"], expected)
        row = self.conn.execute("SELECT * FROM conversation ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual((row["text"], row["source"]), (expected, "operational:budget_stop"))


class ProcessingFailureClaimTests(unittest.TestCase):
    def test_current_message_failure_variants_are_detected_in_both_languages(self):
        cases = [
            "I couldn't process your message. Please resend it.",
            "I can't process your message.",
            "I cannot handle this request.",
            "Your message is dead-lettered.",
            "This message is not processed.",
            "Не смогла обработать это сообщение. Пришли его ещё раз.",
            "Я не могу прочитать твоё сообщение.",
            "Твой запрос не обработан.",
            "Оно лежит во входящих сбоях.",
            '"I could not process your message."',
            "«Не смогла обработать это сообщение.»",
            "Earlier I answered you, but I cannot process this message.",
            "Вчера я ответила, но я не могу обработать это сообщение.",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertTrue(converse.processing_failure_claim(text))

    def test_quotations_historical_discussion_and_real_clarification_are_preserved(self):
        cases = [
            'The earlier notice said: "I couldn\'t process that message." What did you want to ask?',
            "Раньше я отправила уведомление: «Не смогла обработать это сообщение». Оно про прошлый сбой.",
            "Earlier I couldn't process your message; I can read this one.",
            "On 2026-10-26 I couldn't process your message. I can read this one.",
            "I couldn't process your message yesterday.",
            "Я не смогла обработать твоё сообщение вчера.",
            "Раньше я не смогла обработать это сообщение; сейчас я его вижу.",
            "I couldn't process the invoice yesterday.",
            "Не могу прочитать вложение: его здесь нет.",
            "I don't understand which reminder you mean. Which one?",
            "Твоё сообщение вижу. Что именно хочешь проверить?",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertIsNone(converse.processing_failure_claim(text))


if __name__ == "__main__":
    unittest.main()
