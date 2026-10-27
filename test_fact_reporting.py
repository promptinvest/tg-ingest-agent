"""Factual repair reporting preserves history and keeps diagnostics private."""
from types import SimpleNamespace
import unittest

import issue_digest
import review
import store


class FactReportingTests(unittest.TestCase):
    PRIVATE_DRAFT = "PRIVATE_DRAFT_ZEBRA_4821: invented earlier save and unsupported current fact"

    def setUp(self):
        self.conn = store.open_db(":memory:")
        self.addCleanup(self.conn.close)
        self.cfg = SimpleNamespace(timezone_offset=0)

    def history(self):
        return {name: [tuple(row) for row in self.conn.execute(
            "SELECT * FROM " + name + " ORDER BY " + order)]
            for name, order in (("issues", "id"), ("issue_patterns", "fingerprint"))}

    def add(self, kind):
        store.issue_add(self.conn, 1, kind, self.PRIVATE_DRAFT,
                        context={"synthetic": True, "reason": "unsupported fact"})

    def test_completed_repair_is_quiet_but_exact_history_remains_journaled(self):
        self.add("converse_fact_repaired")
        self.add("converse_fact_repaired")
        before = self.history()
        self.assertEqual(len(before["issues"]), 2)
        self.assertEqual(self.conn.execute(
            "SELECT status,occurrences FROM issue_patterns").fetchone()[:], ("open", 2))
        for lang in ("ru", "en"):
            with self.subTest(lang=lang):
                self.assertEqual(issue_digest.text(self.conn, lang), "")
                self.assertNotIn("⚠️", review.chat_text(self.conn, self.cfg, lang))
        data = review.collect(self.conn, "week")
        self.assertEqual(data["open_issue_patterns"], [])
        self.assertEqual({row["kind"]: row["n"] for row in data["issue_counts"]},
                         {"converse_fact_repaired": 2})
        self.assertEqual(self.history(), before)

    def test_unresolved_claim_retry_and_failure_remain_actionable(self):
        unresolved = {"converse_fact_claim", "converse_fact_claim_retry",
                      "converse_fact_repair_failed"}
        for kind in (*sorted(unresolved), "converse_fact_repaired"):
            self.add(kind)
        before = self.history()
        data = review.collect(self.conn, "week")
        self.assertEqual({row["kind"] for row in data["open_issue_patterns"]}, unresolved)
        self.assertEqual(self.history(), before)

    def test_monthly_diagnostics_are_localized_without_the_private_draft(self):
        for kind in ("converse_fact_claim", "converse_fact_claim_retry", "converse_fact_repair_failed"):
            self.add(kind)
        expected = {
            "ru": ("заблокированные неподтверждённые утверждения",
                   "повторные неподтверждённые утверждения",
                   "неудавшиеся исправления фактических утверждений"),
            "en": ("blocked unsupported factual claims", "repeat unsupported factual claims",
                   "factual rewrites that failed"),
        }
        before = self.history()
        for lang, labels in expected.items():
            with self.subTest(lang=lang):
                body = issue_digest.text(self.conn, lang)
                for label in labels:
                    self.assertIn(label, body)
                self.assertNotIn(self.PRIVATE_DRAFT, body)
                self.assertNotIn("PRIVATE_DRAFT_ZEBRA_4821", body)
                self.assertNotIn("converse_fact_", body)
        self.assertEqual(self.history(), before)

    def test_failed_factual_repair_has_only_a_localized_health_summary(self):
        self.add("converse_fact_repair_failed")
        before = self.history()
        for lang, label in (("ru", "неудавшиеся исправления фактических утверждений"),
                            ("en", "factual rewrites that failed")):
            with self.subTest(lang=lang):
                body = review.chat_text(self.conn, self.cfg, lang)
                self.assertIn("⚠️", body)
                self.assertIn(label + ": 1", body)
                self.assertNotIn("converse_fact_repair_failed", body)
                self.assertNotIn(self.PRIVATE_DRAFT, body)
        self.assertEqual(self.history(), before)

    def test_blocked_claims_and_completed_repair_do_not_count_as_failures(self):
        for kind in ("converse_fact_claim", "converse_fact_claim_retry", "converse_fact_repaired"):
            self.add(kind)
        for lang in ("ru", "en"):
            with self.subTest(lang=lang):
                self.assertNotIn("⚠️", review.chat_text(self.conn, self.cfg, lang))
        self.assertEqual(review._issue_label("converse_fact_repaired", "ru"),
                         "исправленные фактические утверждения")
        self.assertEqual(review._issue_label("converse_fact_repaired", "en"),
                         "corrected factual replies")


if __name__ == "__main__":
    unittest.main()
