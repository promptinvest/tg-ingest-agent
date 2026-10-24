"""CF-03 cleanup keeps emphasized answers and the existing output safeguards."""
import json
from pathlib import Path
import unittest

from converse_svc import ConverseMixin


class ReplyCleanupTests(unittest.TestCase):
    def setUp(self):
        self.cleaner = ConverseMixin()

    def clean(self, text):
        return self.cleaner._clean_converse_output(text)[1]

    def test_original_cleanup_corpus_through_final_output(self):
        corpus = json.loads((Path(__file__).parent / "docs/backlog/"
                             "cara-fleet-2026-10-24/regression-cases.json").read_text())
        cases = [case for case in corpus["cases"] if case.get("kind") == "output_cleanup"]
        self.assertEqual({case["id"] for case in cases}, {
            "bold-name-preserved", "single-emphasis-preserved", "mixed-roleplay-emphasis"})
        for case in cases:
            for raw in [case["raw_output"], *case.get("raw_output_variants", [])]:
                with self.subTest(case=case["id"], raw=raw):
                    final = self.clean(raw)
                    for required in case["expected"].get("required_final_text", []):
                        self.assertIn(required, final)
                    for forbidden in case["expected"].get("forbidden_final_text", []):
                        self.assertNotIn(forbidden, final)
                    self.assertNotIn("*", final)

    def test_both_names_and_ordinary_emphasis_survive(self):
        self.assertEqual(self.clean("Projects: **Project Alpha** and *Project Beta*."),
                         "Projects: Project Alpha and Project Beta.")
        self.assertEqual(self.clean("***Project Alpha***: *important* details."),
                         "Project Alpha: important details.")
        self.assertEqual(self.clean("**Альфа** и *Бета*: обычный ответ."),
                         "Альфа и Бета: обычный ответ.")

    def test_each_original_gesture_mixed_with_each_emphasis_form(self):
        for gesture in ("закрываю глаза, выдыхаю", "прижимаю телефон к губам"):
            for emphasis in ("*", "**", "***"):
                with self.subTest(gesture=gesture, emphasis=emphasis):
                    raw = (f"*{gesture}* {emphasis}Project Alpha{emphasis}. "
                           f"Ordinary answer text. *Project Beta*")
                    self.assertEqual(self.clean(raw),
                                     "Project Alpha. Ordinary answer text. Project Beta")

    def test_original_standalone_gesture_assertions_remain_true(self):
        self.assertEqual(self.clean("*закрываю глаза, выдыхаю*\n\nТвоя. Полностью."),
                         "Твоя. Полностью.")
        self.assertEqual(self.clean(
            "Всегда — это очень долго. *прижимаю телефон к губам* И я согласна."),
            "Всегда — это очень долго. И я согласна.")
        self.assertEqual(self.clean("просто текст без действий"), "просто текст без действий")

    def test_mixed_span_preserves_answer_clause_instead_of_deleting_it(self):
        for raw in ("*улыбаюсь, Project Alpha is recorded*",
                    "**Project Alpha is recorded; закрываю глаза**",
                    "*smiles and Project Alpha is recorded*"):
            with self.subTest(raw=raw):
                self.assertEqual(self.clean(raw), "Project Alpha is recorded")

    def test_more_recognizable_ru_en_gestures_are_suppressed(self):
        for gesture in ("тихо улыбаюсь", "киваю и выдыхаю", "I close my eyes",
                        "smiles softly", "presses her phone to her lips"):
            with self.subTest(gesture=gesture):
                self.assertEqual(self.clean(f"*{gesture}* **Project Alpha** is recorded."),
                                 "Project Alpha is recorded.")

    def test_unknown_content_and_ambiguous_names_are_never_erased(self):
        for text in ("Project Smiles", "Smiles", "Киваю", "all backups succeeded",
                     "Project Alpha runs alongside Project Beta", "there is only one project"):
            with self.subTest(text=text):
                self.assertEqual(self.clean(f"*{text}*"), text)

    def test_literal_asterisks_lists_numbers_and_links_survive(self):
        text = "2 * 3 * 4 = 24\n* Project Alpha\n* Project Beta\n1. https://example.test/alpha"
        self.assertEqual(self.clean(text), text)

    def test_array_reaction_id_and_photo_cleanup_still_apply(self):
        raw = json.dumps(["👍", "**Project Alpha**. *закрываю глаза* "
                          "tr_1782452622_ff1810017f [photo]"], ensure_ascii=False)
        reaction, final = self.cleaner._clean_converse_output(raw)
        self.assertEqual(reaction, "👍")
        self.assertEqual(final, "Project Alpha.")
        for text in ("[[react:👍]] **Project Alpha** is recorded.",
                     "👍\n\n**Project Alpha** is recorded."):
            with self.subTest(text=text):
                reaction, final = self.cleaner._clean_converse_output(text)
                self.assertEqual(reaction, "👍")
                self.assertEqual(final, "Project Alpha is recorded.")

    def test_cleanup_does_not_hide_claims_from_downstream_truth_guard(self):
        import action_truth
        final = self.clean("**Готово — сохранила заметку #52.**")
        self.assertIn("сохранила", final)
        self.assertTrue(action_truth.freeform_claims_action(final))


if __name__ == "__main__":
    unittest.main()
