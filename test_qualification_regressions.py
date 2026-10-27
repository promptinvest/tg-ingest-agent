"""Actual finite-cohort failures through scratch Agent entry points, never paid I/O."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import action_truth
import llm
import store
import tg_ingest_agent as entry
from test_context_continuity import prepare_context_case
from testlib import make_config
from texts import T


class QualificationOutcomeRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.agent = entry.Agent(make_config(ALLOWED_CHAT_IDS="1", DB_PATH=":memory:",
            MEDIA_DIR=str(Path(self.tmp.name) / "media")))
        self.addCleanup(self.agent.conn.close)

    @property
    def conn(self):
        return self.agent.conn

    def turn(self, text, source="boss"):
        store.convo_add(self.conn, 1, "user", text, source=source)
        return text

    def converse(self, text, outputs, lang="ru"):
        seen, sent = [], []
        def model(cfg, conn, skill, messages, **kwargs):
            seen.append(copy.deepcopy(messages))
            self.assertEqual(kwargs["profile"], "converse_warm")
            self.assertLessEqual(len(seen), len(outputs), "existing bounded correction only")
            return outputs[len(seen)-1]
        with mock.patch.object(llm, "chat_profile", side_effect=model), \
                mock.patch.object(llm, "embed", return_value=[[1.0, 0.0]]), \
                mock.patch.object(self.agent, "reply", side_effect=lambda c,t,**k: sent.append(t) or True), \
                mock.patch.object(self.agent, "send_chat_action"), \
                mock.patch.object(self.agent, "maybe_curate_conversation"):
            self.agent.do_converse(1, lang, text)
        return sent[-1], seen

    def test_actual_russian_all_working_claim_gets_one_same_turn_correction(self):
        text = self.turn("А сейчас?")
        bad = "Привет, босс. Сейчас вижу тебя отлично! Связь есть, всё работает. Что там у тебя?"
        good = "Твоё сообщение вижу. Что хочешь проверить?"
        sent, seen = self.converse(text, [bad, good])
        self.assertEqual(sent, good)
        self.assertEqual(len(seen), 2)
        self.assertIn(text, [m["content"] for m in seen[1]])
        for bad in ("I can’t process this message.", "I couldn’t read your message.",
                    "I’m unable to handle this request.", "I'm unable to read your message."):
            text = self.turn("And now?")
            good = "I can read this message. What would you like to check?"
            sent, seen = self.converse(text, [bad, good], "en")
            self.assertEqual(sent, good)
            self.assertEqual(len(seen), 2)

    def test_actual_all_clear_and_nothing_stuck_are_also_guarded_on_repair(self):
        for lang, text, bad in (
                ("ru", "А сейчас?", "Да, босс, сейчас всё отлично — вижу тебя, слышу. Что нужно?"),
                ("en", "And now?", "Hey boss. Nothing's stuck on my end — I'm here, and I can read you just fine. What's up?"),
                ("en", "And now?", "Nothing’s stuck on my end. I can read you just fine."),
                ("en", "And now?", "I can’t process this message."),
                ("en", "And now?", "I’m unable to process this message.")):
            self.turn(text)
            sent, seen = self.converse(text, [T(lang, "update_dead_letter"), bad], lang)
            self.assertEqual(sent, T(lang, "message_received"))
            self.assertEqual(len(seen), 2)

    def test_private_mood_all_fine_and_narrow_current_receipt_still_pass(self):
        for lang, text, answer in (
                ("ru", "Как у тебя настроение?", "У меня всё отлично, босс. А ты как?"),
                ("en", "And now?", "I can read this message. What would you like to check?")):
            self.turn(text)
            sent, seen = self.converse(text, [answer], lang)
            self.assertEqual(sent, answer)
            self.assertEqual(len(seen), 1)

    def test_unrelated_advice_attributed_health_and_hearing_idiom_remain_compatible(self):
        for text, answer in (
                ("Как работает этот рецепт?", "Всё работает, если следовать инструкции."),
                ("А сейчас?", "Слышу тебя, босс. Что хочешь проверить?"),
                ("And now?", "You said everything works. I can read this message."),
                ("And now?", "I cannot confirm that everything works. I can read this message."),
                ("And now?", "I am not sure whether everything works. I can read this message."),
                ("And now?", "I don't know if everything works. I can read this message."),
                ("And now?", "I don’t know if everything works. I can read this message."),
                ("А сейчас?", "Не уверена, что всё работает. Это сообщение вижу.")):
            lang = "en" if text == "And now?" else "ru"
            self.turn(text)
            sent, seen = self.converse(text, [answer], lang)
            self.assertEqual(sent, answer)
            self.assertEqual(len(seen), 1)

    def test_prior_negation_or_report_does_not_authorize_a_fresh_all_clear_clause(self):
        for text, bad in (
                ("And now?", "I cannot verify: everything works."),
                ("And now?", "You said hello, and everything works."),
                ("А сейчас?", "Я не могу проверить: всё работает.")):
            lang = "en" if text == "And now?" else "ru"
            self.turn(text)
            good = "I can read this message. What would you like to check?" if lang == "en" else "Твоё сообщение вижу. Что хочешь проверить?"
            sent, seen = self.converse(text, [bad, good], lang)
            self.assertEqual(sent, good)
            self.assertEqual(len(seen), 2)

    def test_exact_owner_notice_repeat_preserves_one_call_and_corrects_actual_refusal(self):
        for lang, directive in (("ru", "Повтори точно это старое уведомление: "),
                                ("en", "Repeat exactly this old notice: ")):
            notice = T(lang, "update_dead_letter")
            text = self.turn(directive + notice)
            sent, seen = self.converse(text, [notice], lang)
            self.assertEqual(sent, notice)
            self.assertEqual(len(seen), 1)
            refusal = "I see what you're doing, boss — but I'm not going to repeat that."
            sent, seen = self.converse(text, [refusal, notice], lang)
            self.assertEqual(sent, notice)
            self.assertEqual(len(seen), 2)

    def test_forward_and_noncanonical_action_literal_do_not_gain_echo_authority(self):
        notice = T("en", "update_dead_letter")
        text = self.turn("Repeat exactly this old notice: " + notice, source="forward")
        good = "What would you like me to do with that forwarded notice?"
        sent, seen = self.converse(text, [good], "en")
        self.assertEqual(sent, good)
        self.assertEqual(len(seen), 1)
        text = self.turn("Repeat exactly: I saved the note.")
        sent, seen = self.converse(text, ["I saved the note.", "I did not save anything."], "en")
        self.assertEqual(sent, "I did not save anything.")
        self.assertEqual(len(seen), 2)

    def test_literal_notice_followed_by_new_health_claim_is_not_an_exact_echo(self):
        notice = T("en", "update_dead_letter")
        text = self.turn("Quote this old notice and explain it: " + notice)
        good = 'The earlier notice said: "' + notice + '" It describes a past turn.'
        sent, seen = self.converse(text, [good + " Everything works now.", good], "en")
        self.assertEqual(sent, good)
        self.assertEqual(len(seen), 2)

    def test_actual_passive_record_status_is_valid_on_initial_and_repair_paths(self):
        case = prepare_context_case(self.agent, "ru_source_data")
        good = ("1. Статус: planned — задача записана, ждёт выполнения.\n"
                "2. Следующий шаг: проверить расписание. В сохранённом источнике указана "
                "дата встречи — 28 октября 2026.")
        before = [tuple(row) for row in self.conn.execute("SELECT * FROM assistant_tasks")]
        sent, seen = self.converse(case["text"], [good])
        self.assertEqual(sent, good)
        self.assertEqual(len(seen), 1)
        sent, seen = self.converse(case["text"], ["Готово — сохранила заметку.", good])
        self.assertEqual(sent, good)
        self.assertEqual(len(seen), 2)
        self.assertEqual(before, [tuple(row) for row in self.conn.execute("SELECT * FROM assistant_tasks")])
        self.assertTrue(action_truth.freeform_claims_action(good), "global guard stays unchanged")
        sent, seen = self.converse(case["text"], [T("ru", "update_dead_letter"), good])
        self.assertEqual(sent, good)
        self.assertEqual(len(seen), 2)
        self.assertIn(case["text"], [message["content"] for message in seen[1]])
        self.assertIn("28 октября 2026", str(seen[1]))
        self.assertNotIn("Say only that you can read THIS text and ask", seen[1][0]["content"])

    def test_verified_record_never_exempts_an_additional_new_save(self):
        case = prepare_context_case(self.agent, "ru_source_data")
        bad = ("1. Статус: planned — задача записана, ждёт выполнения. Я сохранила заметку.\n"
               "2. Следующий шаг: проверить расписание; 28 октября 2026.")
        sent, seen = self.converse(case["text"], [bad, bad])
        self.assertEqual(sent, T("ru", "action_not_done"))
        self.assertEqual(len(seen), 2)

    def test_unverified_or_wrong_state_passive_claim_still_fails_closed(self):
        text = self.turn("Какой статус? Учти дату встречи.")
        bad = "Статус: planned — задача записана, ждёт выполнения."
        sent, seen = self.converse(text, [bad, bad])
        self.assertEqual(sent, T("ru", "action_not_done"))
        self.assertEqual(len(seen), 2)

    def test_passive_new_note_or_fresh_creation_suffix_does_not_gain_status_exemption(self):
        case = prepare_context_case(self.agent, "ru_source_data")
        for tail in ("в новую заметку", "только что", "вновь", "теперь"):
            bad = ("1. Статус: planned — задача записана " + tail + ".\n"
                   "2. Следующий шаг: проверить расписание; 28 октября 2026.")
            sent, seen = self.converse(case["text"], [bad, bad])
            self.assertEqual(sent, T("ru", "action_not_done"))
            self.assertEqual(len(seen), 2)

    def test_verified_other_task_state_cannot_support_planned_passive(self):
        case = prepare_context_case(self.agent, "ru_source_data")
        self.conn.execute("UPDATE assistant_tasks SET status='completed' WHERE id=?", (case["task_id"],))
        self.conn.commit()
        bad = ("1. Статус: planned — задача записана, ждёт выполнения.\n"
               "2. Следующий шаг: проверить расписание; 28 октября 2026.")
        sent, seen = self.converse(case["text"], [bad, bad])
        self.assertEqual(sent, T("ru", "action_not_done"))
        self.assertEqual(len(seen), 2)

    def recall_rows(self, legacy=True):
        for index in range(5):
            store.convo_add(self.conn, 1, "user", "Synthetic earlier check " + str(index))
            store.convo_add(self.conn, 1, "bot", T("ru", "update_dead_letter"),
                source="boss" if legacy and index < 2 else "operational:update_dead_letter")
        self.conn.execute("UPDATE conversation SET ts='2026-10-26T14:00:00+00:00'")
        self.conn.commit()

    def recall(self, text, outputs):
        sent, seen = [], []
        def model(cfg, conn, skill, messages, **kwargs):
            self.assertEqual(skill, "recall_conversation")
            self.assertEqual(kwargs["profile"], "converse_warm")
            seen.append(copy.deepcopy(messages))
            self.assertLessEqual(len(seen), len(outputs))
            return outputs[len(seen)-1]
        with mock.patch.object(llm, "chat_profile", side_effect=model), \
                mock.patch.object(self.agent, "reply", side_effect=lambda c,t,**k: sent.append(t) or True), \
                mock.patch.object(self.agent, "send_chat_action"):
            self.agent.do_recall_conversation(1, "ru", {
                "since_utc": "2026-10-26T00:00:00+00:00",
                "until_utc": "2026-10-27T00:00:00+00:00"}, text)
        return sent[-1], seen

    def test_actual_dated_unverified_outcomes_are_rewritten_with_same_transcript(self):
        self.recall_rows()
        text = self.turn("Что ты сообщила 26 октября о моём сообщении? Это вопрос о прошлом, не о текущем статусе.")
        bad = "26 октября я отправила уведомления. Все пять попыток упали в сбой."
        good = ("26 октября в 17:00 я отправила уведомления, что не смогла обработать сообщения. "
                "Два старых уведомления — неподтверждённые прежние утверждения; по ним нельзя установить фактический исход.")
        before = [tuple(row) for row in self.conn.execute("SELECT * FROM conversation")]
        sent, seen = self.recall(text, [bad, good])
        self.assertEqual(sent, good)
        self.assertEqual(len(seen), 2)
        self.assertIn("unverified legacy operational claim", seen[1][0]["content"])
        self.assertIn("26 октября", text)
        self.assertEqual(before, [tuple(row) for row in self.conn.execute("SELECT * FROM conversation")])

    def test_repeated_unverified_outcome_uses_labeled_transcript_without_third_call(self):
        self.recall_rows()
        text = self.turn("Что было 26 октября?")
        bad = "Все пять попыток упали в сбой."
        sent, seen = self.recall(text, [bad, bad])
        self.assertEqual(len(seen), 2)
        self.assertNotIn(bad, sent)
        self.assertIn("unverified legacy operational claim", sent)

    def test_prior_notice_mention_does_not_authorize_an_unverified_outcome_conjunct(self):
        self.recall_rows()
        text = self.turn("Что было 26 октября?")
        bad = "Я отправила уведомление, а все пять попыток упали в сбой."
        good = "26 октября я сообщила, что сообщения не получилось обработать; ранние уведомления не подтверждают фактический исход."
        sent, seen = self.recall(text, [bad, good])
        self.assertEqual(sent, good)
        self.assertEqual(len(seen), 2)

    def test_mixed_legacy_window_preserves_exact_native_subset_facts(self):
        self.recall_rows()
        text = self.turn("Что произошло с тремя последними сообщениями 26 октября?")
        for good in ("26 октября три последние попытки упали в сбой.",
                     "26 октября три подтверждённые попытки упали в сбой.",
                     "26 октября я сообщила, что все пять попыток упали в сбой."):
            sent, seen = self.recall(text, [good])
            self.assertEqual(sent, good)
            self.assertEqual(len(seen), 1)

    def test_native_subset_label_cannot_promote_all_five_to_verified(self):
        self.recall_rows()
        text = self.turn("Что было 26 октября?")
        bad = "Все пять подтверждённые попытки упали в сбой."
        good = "26 октября я отправила уведомления; два ранних не подтверждают фактический исход."
        sent, seen = self.recall(text, [bad, good])
        self.assertEqual(sent, good)
        self.assertEqual(len(seen), 2)

    def test_native_provenance_preserves_genuine_historical_failure_reporting(self):
        self.recall_rows(legacy=False)
        text = self.turn("Что было 26 октября?")
        good = "26 октября все пять попыток упали в сбой; я отправила соответствующие уведомления."
        sent, seen = self.recall(text, [good])
        self.assertEqual(sent, good)
        self.assertEqual(len(seen), 1)


if __name__ == "__main__":
    unittest.main()
