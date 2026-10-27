"""Processing-claim guards remain visible without reopening completed handling."""
import tempfile
import unittest
from pathlib import Path

import issue_digest
import review
import store


class OperationalReportingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.conn = store.open_db(Path(self.temp.name) / "db")
        self.addCleanup(self.conn.close)

    def test_unrepaired_processing_claim_remains_actionable(self):
        store.issue_add(self.conn, 1, "converse_operational_claim_retry", "synthetic processing claim")
        kinds = {row["kind"] for row in review.collect(self.conn, "week")["open_issue_patterns"]}
        self.assertIn("converse_operational_claim_retry", kinds)

    def test_completed_rewrite_is_not_an_open_monthly_problem(self):
        store.issue_add(self.conn, 1, "converse_operational_repaired", "synthetic repaired answer")
        self.assertEqual(issue_digest.text(self.conn, "en"), "")

    def test_all_current_completed_handling_keeps_exact_history(self):
        for kind in issue_digest.COMPLETED_HANDLING:
            store.issue_add(self.conn, 1, kind, "synthetic completed handling")
        before = [tuple(row) for row in self.conn.execute("SELECT * FROM issue_patterns ORDER BY fingerprint")]
        self.assertEqual(issue_digest.text(self.conn, "en"), "")
        self.assertEqual([tuple(row) for row in self.conn.execute("SELECT * FROM issue_patterns ORDER BY fingerprint")], before)

    def test_failed_repair_is_a_translated_failure(self):
        self.assertIn("converse_operational_repair_failed", review.FAILURE_ISSUE_KINDS)
        store.issue_add(self.conn, 1, "converse_operational_repair_failed", "synthetic private diagnostic")
        digest = issue_digest.text(self.conn, "en")
        self.assertIn("processing-status", digest)
        self.assertNotIn("synthetic private diagnostic", digest)


if __name__ == "__main__":
    unittest.main()
