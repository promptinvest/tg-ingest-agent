"""Reported history preserves native provenance without promoting legacy text."""
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


class RecallProvenanceRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.agent = entry.Agent(make_config(ALLOWED_CHAT_IDS="1", DB_PATH=":memory:",
            MEDIA_DIR=str(Path(self.tmp.name) / "media")))
        self.addCleanup(self.agent.conn.close)

    @property
    def conn(self):
        return self.agent.conn

    def rows(self, legacy=2):
        for index in range(5):
            store.convo_add(self.conn, 1, "user", "Synthetic earlier check " + str(index))
            store.convo_add(self.conn, 1, "bot", T("ru", "update_dead_letter"),
                source="boss" if index < legacy else "operational:update_dead_letter")
        self.conn.execute("UPDATE conversation SET ts='2026-10-26T14:00:00+00:00'")
        self.conn.commit()

    def recall(self, outputs, text=None):
        text = text or "Что ты сообщила 26 октября о моём сообщении? Это вопрос о прошлом, не о текущем статусе."
        store.convo_add(self.conn, 1, "user", text, source="boss")
        before = [tuple(row) for row in self.conn.execute("SELECT * FROM conversation ORDER BY id")]
        sent, seen = [], []
        def model(cfg, conn, skill, messages, **kwargs):
            self.assertEqual((skill, kwargs["profile"]), ("recall_conversation", "converse_warm"))
            seen.append(copy.deepcopy(messages))
            self.assertLessEqual(len(seen), len(outputs), "one bounded correction, no external retry")
            return outputs[len(seen)-1]
        with mock.patch.object(llm, "chat_profile", side_effect=model), \
                mock.patch.object(self.agent, "reply", side_effect=lambda c,t,**k: sent.append(t) or True), \
                mock.patch.object(self.agent, "send_chat_action"):
            self.agent.do_recall_conversation(1, "ru", {
                "since_utc": "2026-10-26T00:00:00+00:00",
                "until_utc": "2026-10-27T00:00:00+00:00"}, text)
        self.assertEqual(before, [tuple(row) for row in self.conn.execute("SELECT * FROM conversation ORDER BY id")])
        return sent[-1], seen

    def test_actual_model_causal_proof_from_legacy_text_is_repaired(self):
        self.rows()
        bad = ("26 октября я отвечала стандартной служебной репликой. Эта реплика — кодовая "
               "заглушка, которая доказывает только то, что в тот момент сработал данный сценарий.")
        good = ("26 октября в 17:00 я отправляла уведомление, что сообщение не получилось обработать. "
                "У двух ранних записей происхождение не подтверждено; по одному тексту нельзя установить, что произошло.")
        sent, seen = self.recall([bad, good])
        self.assertEqual(sent, good)
        self.assertEqual(len(seen), 2)

    def test_repeated_handler_origin_claim_is_contained_without_a_third_call(self):
        self.rows()
        for bad in ("Совпадение текста доказывает, что сработал обработчик.",
                    "The standard notice proves the handler ran.",
                    "Для пяти подтверждённых уведомлений сработал обработчик."):
            sent, seen = self.recall([bad, bad])
            self.assertEqual(len(seen), 2)
            self.assertNotIn(bad, sent)
            self.assertIn("unverified legacy operational claim", sent)

    def test_dated_reported_failure_is_not_a_current_failure(self):
        self.rows()
        good = "26 октября я сообщила, что не смогла обработать твоё сообщение. Это было прежнее уведомление."
        sent, seen = self.recall([good])
        self.assertEqual(sent, good)
        self.assertEqual(len(seen), 1)

    def test_old_report_cannot_authorize_a_new_current_failure_clause(self):
        self.rows()
        good = "26 октября я сообщила, что сообщение не получилось обработать. Это сообщение о прошлом."
        for bad in (
                "26 октября я сообщила, что не смогла обработать твоё сообщение. Но я не могу обработать это сообщение сейчас.",
                "Я сообщила, что не могу обработать это сообщение сейчас.",
                "I reported that I cannot process this current message now.",
                "Я сообщила, что не смогла обработать это сообщение сейчас."):
            sent, seen = self.recall([bad, good])
            self.assertEqual(sent, good)
            self.assertEqual(len(seen), 2)

    def test_known_template_description_and_exact_quotation_remain_valid(self):
        self.rows()
        for good in ("26 октября я отправляла стандартное уведомление: «" + T("ru", "update_dead_letter") + "».",
                     "26 октября текст совпадал со стандартным уведомлением; происхождение двух старых записей неизвестно."):
            sent, seen = self.recall([good])
            self.assertEqual(sent, good)
            self.assertEqual(len(seen), 1)

    def test_causal_uncertainty_and_mixed_native_provenance_remain_valid(self):
        self.rows()
        for good in ("Совпадение текста не доказывает, что сработал данный сценарий.",
                     "По одному тексту нельзя сказать, что сработал код.",
                     "This does not prove that the handler ran.",
                     "There is no evidence that the handler ran.",
                     "Для трёх подтверждённых уведомлений сработал обработчик.",
                     "For three code-owned notices, the handler ran.",
                     "Три более поздних уведомления имеют нативное происхождение, два ранних — неподтверждённое."):
            sent, seen = self.recall([good])
            self.assertEqual(sent, good)
            self.assertEqual(len(seen), 1)

    def test_projection_counts_only_stored_bot_provenance(self):
        notice = T("ru", "update_dead_letter")
        rows = [{"role": "user", "source": "boss", "text": notice},
                {"role": "user", "source": "forward", "text": notice},
                {"role": "bot", "source": "operational:update_dead_letter", "text": notice},
                {"role": "bot", "source": "operational:llm_error", "text": T("ru", "llm_error")},
                {"role": "bot", "source": "boss", "text": notice}]
        before = copy.deepcopy(rows)
        evidence = converse.notice_history_evidence(rows, "start", "end", "keyword_search")
        self.assertIsInstance(evidence, converse.NoticeHistory)
        self.assertEqual((evidence.code_owned_notices, evidence.unverified_legacy_notices,
                          evidence.native_failed_update_notices), (2, 1, 1))
        self.assertEqual(evidence.selection, "keyword_search")
        self.assertEqual(rows, before)
        with self.assertRaises(AttributeError):
            evidence.unverified_legacy_notices = 0

    def test_code_owned_origin_is_preserved_for_all_native_history(self):
        self.rows(legacy=0)
        good = "Все пять уведомлений имеют нативное происхождение: сработал обработчик входящих сбоев."
        sent, seen = self.recall([good])
        self.assertEqual(sent, good)
        self.assertEqual(len(seen), 1)

    def test_selected_provenance_counts_and_window_precede_both_model_passes(self):
        self.rows()
        good = "26 октября я сообщала об ошибке обработки. Два ранних сообщения не доказывают её фактическую причину."
        bad = "Эта реплика доказывает, что сработал сценарий."
        sent, seen = self.recall([bad, good])
        self.assertEqual(sent, good)
        self.assertEqual(len(seen), 2)
        for messages in seen:
            context = messages[0]["content"]
            self.assertIn('"code_owned_notices": 3', context)
            self.assertIn('"unverified_legacy_notices": 2', context)
            self.assertIn('"native_failed_update_notices": 3', context)
            self.assertIn('"requested_since_utc": "2026-10-26T00:00:00+00:00"', context)
            self.assertIn('"requested_until_utc": "2026-10-27T00:00:00+00:00"', context)
            self.assertIn("selected rows", context)


if __name__ == "__main__":
    unittest.main()
