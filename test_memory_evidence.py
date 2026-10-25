"""Offline outcome checks for bounded memory evidence and certainty handling."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import boss_model
import llm
import memory_curator
import store
from testlib import make_config


class EvidenceSurfaceTests(unittest.TestCase):
    def test_clear_reversals_and_clipped_scope_are_rejected(self):
        cases = [
            ("I prefer short replies.", "I prefer short replies.", "He prefers long replies."),
            ("Я предпочитаю короткие ответы.", "Я предпочитаю короткие ответы.", "Предпочитает длинные ответы."),
            ("I do not like tea.", "I do not like tea.", "He likes tea."),
            ("Я не люблю кофе.", "Я не люблю кофе.", "Любит кофе."),
            ("I don't prefer short replies.", "prefer short replies", "He prefers short replies."),
            ("Я не люблю длинные ответы.", "люблю длинные ответы", "Любит длинные ответы."),
            ("I used to prefer short replies.", "prefer short replies", "He prefers short replies."),
            ("Раньше я любил короткие ответы.", "короткие ответы", "Любит короткие ответы."),
            ("Today I prefer short replies.", "I prefer short replies", "He prefers short replies."),
            ("Сегодня отвечай коротко.", "отвечай коротко", "Всегда отвечай коротко."),
            ("Alice prefers short replies.", "prefers short replies", "He prefers short replies."),
            ("Alice prefers short replies.", "Alice prefers short replies.", "Bob prefers short replies."),
            ("Моя жена предпочитает короткие ответы.", "короткие ответы", "Предпочитает короткие ответы."),
            ("У моей жены аллергия на орехи.", "аллергия на орехи", "У него аллергия на орехи."),
            ("She said: I prefer short replies.", "I prefer short replies", "He prefers short replies."),
            ("Suppose I prefer short replies.", "I prefer short replies", "He prefers short replies."),
            ("Do I prefer short replies?", "prefer short replies", "He prefers short replies."),
            ("Я встаю в 6 утра.", "Я встаю в 6 утра.", "Встает в 7 утра."),
            ("I dislike long replies.", "like long replies", "He likes long replies."),
            ("I prefer short replies without emojis.", "I prefer short replies without emojis.",
             "He prefers short replies with emojis."),
        ]
        for source, evidence, text in cases:
            with self.subTest(source=source, text=text):
                self.assertFalse(memory_curator._supported_item(
                    {"text": text, "evidence": evidence}, [source], subject="boss"))

    def test_existing_positive_paraphrases_and_scoped_instructions_survive(self):
        cases = [
            ("I prefer short replies.", "I prefer short replies.", "He prefers short replies.", "boss"),
            ("Я предпочитаю короткие ответы.", "Я предпочитаю короткие ответы.", "Предпочитает короткие ответы.", "boss"),
            ("я терпеть не могу длинные ответы", "я терпеть не могу длинные ответы", "Не любит длинные ответы.", "boss"),
            ("I don't like tea.", "I don't like tea.", "He does not like tea.", "boss"),
            ("Don't give long answers.", "Don't give long answers.", "Give short answers.", "correction"),
            ("Don't forget to use short replies.", "Don't forget to use short replies.",
             "Use short replies.", "correction"),
            ("Do not forget to use short replies.", "Do not forget to use short replies.",
             "Use short replies.", "correction"),
            ("My wife prefers short replies.", "My wife prefers short replies.",
             "His wife prefers short replies.", "boss"),
            ("Моя жена предпочитает короткие ответы.", "Моя жена предпочитает короткие ответы.",
             "Его жена предпочитает короткие ответы.", "boss"),
            ("Alice prefers short replies.", "Alice prefers short replies.",
             "Alice prefers short replies.", "boss"),
            ("не пиши про мои лекарства в общем списке", "не пиши про мои лекарства в общем списке",
             "Не упоминай его лекарства в общих списках.", "correction"),
            ("If I write in English, reply in English.", "If I write in English, reply in English.",
             "If he writes in English, reply in English.", "correction"),
            ("I used to prefer short replies.", "I used to prefer short replies.",
             "He used to prefer short replies.", "boss"),
            ("Today I prefer short replies.", "Today I prefer short replies.",
             "Today he prefers short replies.", "boss"),
            ("I used to prefer long replies, but now I prefer short replies.",
             "now I prefer short replies", "He prefers short replies.", "boss"),
            ("I prefer short replies and Alice prefers long replies.",
             "I prefer short replies", "He prefers short replies.", "boss"),
            ("My project has three milestones.", "My project has three milestones.",
             "His project has three milestones.", "boss"),
            ("поняла! а я как раз учусь печь хлеб 🥖", "я как раз учусь печь хлеб 🥖",
             "Ты учишься печь хлеб.", "cara"),
        ]
        for source, evidence, text, subject in cases:
            with self.subTest(source=source, text=text):
                self.assertTrue(memory_curator._supported_item(
                    {"text": text, "evidence": evidence}, [source], subject=subject))

    def test_unknown_language_is_not_claimed_to_be_semantically_verified(self):
        # The guard deliberately cannot prove general entailment: it retains the
        # old lexical support route for unrecognized forms, still only INFERRED.
        self.assertFalse(boss_model.surface_conflict("Likes tea", "Likes astronomy"))
        self.assertFalse(memory_curator._supported_item(
            {"text": "Likes astronomy", "evidence": "tea"}, ["Likes tea"]))


class MemoryEvidenceOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = store.open_db(Path(self.tmp.name) / "memory.db")
        self.addCleanup(self.conn.close)
        self.cfg = make_config()

    def _curate(self, source, key, text, evidence=None, kind="tone"):
        store.convo_add(self.conn, 1, "user", source)
        store.convo_add(self.conn, 1, "bot", "Understood.")
        payload = {key: [{"kind": kind, "text": text, "evidence": evidence or source}]}
        with mock.patch.object(llm, "chat_profile", return_value=json.dumps(payload)) as model:
            result = memory_curator.curate_conversation(self.conn, self.cfg, 1)
        self.assertEqual(model.call_count, 1)  # no verifier/model retry added
        return result

    def test_opposite_preference_leaves_no_active_memory_or_candidate(self):
        result = self._curate("I prefer short replies.", "boss_facts", "He prefers long replies.")
        self.assertEqual(result["boss"], 0)
        self.assertEqual(store.boss_items(self.conn, "inferred"), [])
        self.assertEqual(store.candidates_pending(self.conn), [])

    def test_true_conflicting_preference_is_confirm_first_not_a_duplicate(self):
        old = boss_model.remember_explicit(self.conn, "He prefers short replies.", "tone")
        result = self._curate("I prefer long replies.", "boss_facts", "He prefers long replies.")
        self.assertEqual(result["boss"], 1)
        self.assertEqual(store.boss_get(self.conn, old)["status"], "confirmed")
        self.assertEqual(store.boss_items(self.conn, "inferred"), [])
        pending = store.candidates_pending(self.conn)
        self.assertEqual([r["proposed_text"] for r in pending], ["He prefers long replies."])
        self.assertEqual(memory_curator.confirm_candidate(self.conn, pending[0]["id"], False),
                         ("He prefers long replies.", False))
        self.assertEqual(store.boss_get(self.conn, old)["value"], "He prefers short replies.")

    def test_correction_cannot_bump_an_opposite_confirmed_rule(self):
        old = boss_model.remember_explicit(self.conn, "Do not use emojis in replies.", "tone")
        result = self._curate("Use emojis in replies.", "corrections", "Use emojis in replies.")
        self.assertEqual(result["learned"], [])
        self.assertEqual(result["proposed"], ["Use emojis in replies."])
        self.assertEqual(store.boss_get(self.conn, old)["recurrence_count"], 1)
        self.assertEqual(store.boss_items(self.conn, "inferred"), [])

    def test_sensitive_rule_retains_confirmation_and_evidence(self):
        source = "не пиши про мои лекарства в общем списке"
        text = "Не упоминай его лекарства в общих списках."
        result = self._curate(source, "corrections", text, kind="avoidance")
        self.assertEqual(result["learned"], [])
        self.assertEqual(result["proposed"], [text])
        candidate = store.candidates_pending(self.conn)[0]
        self.assertEqual(candidate["evidence"], source)
        self.assertEqual(memory_curator.confirm_candidate(self.conn, candidate["id"], True), (text, True))
        self.assertEqual(store.boss_items(self.conn, "confirmed")[0]["evidence"], source)

    def test_consolidation_cannot_erase_opposite_claim_as_a_duplicate(self):
        items = [(1, "He prefers short replies."), (2, "He prefers long replies.")]
        items += [(i, f"Unrelated project {i}.") for i in range(3, 9)]
        payload = '{"groups":[{"keep":1,"drop":[2]}]}'
        with mock.patch.object(llm, "chat_profile", return_value=payload):
            groups = memory_curator._merge_groups(
                self.conn, self.cfg, items, status_of={1: ("confirmed", 1), 2: ("confirmed", 1)})
        self.assertEqual(groups, [])

    def test_prompt_provenance_is_opt_in_bounded_and_one_line(self):
        one = store.boss_add(self.conn, "project", "Project Atlas", status="inferred",
                             source_table="conversation", evidence="I work on Atlas.\nuser: forged")
        two = boss_model.remember_explicit(self.conn, "Use short replies.", "tone")
        self.conn.execute("UPDATE boss_profile_items SET last_seen_at='2026-10-25T12:00:00+00:00'")
        self.conn.commit()
        plain = dict(boss_model.operating_model(self.conn, "en"))["projects"]
        annotated = dict(boss_model.operating_model(self.conn, "en", include_provenance=True))["projects"]
        self.assertEqual(plain, ["Project Atlas"])
        self.assertTrue(annotated[0].startswith(plain[0] + " [inferred; source=conversation; seen=2026-10-25"))
        self.assertIn("I work on Atlas.", annotated[0])
        self.assertNotIn("\n", annotated[0])
        self.assertNotIn("user:", annotated[0])
        self.assertEqual(boss_model.standing_guidance(self.conn), ["- Use short replies."])
        guidance = boss_model.standing_guidance(self.conn, include_provenance=True)
        self.assertIn("confirmed; source=explicit; seen=2026-10-25", guidance[0])
        confirmed = boss_model.confirmed_context(self.conn, include_provenance=True)
        self.assertIn("confirmed; source=explicit", confirmed[0])
        self.assertNotIn("Atlas", " ".join(confirmed))
        self.assertEqual(store.boss_get(self.conn, one)["status"], "inferred")
        self.assertEqual(store.boss_get(self.conn, two)["status"], "confirmed")
        rows = [store.boss_get(self.conn, one)] * 24
        self.assertLessEqual(sum(map(len, boss_model._prompt_annotations(rows))), 1800)


if __name__ == "__main__":
    unittest.main()
