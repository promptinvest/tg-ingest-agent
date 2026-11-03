"""Confirmed search eligibility precedes result limits without changing browse."""
import tempfile
import unittest
from pathlib import Path

import store
import task_runner
import tool_broker


class KnowledgeSearchEligibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.open_db(Path(self.tmp.name) / "notes.db")
        self.sequence = 0

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _note(self, text, *, confirmed=True, chat_id=111, category="Options"):
        self.sequence += 1
        message_id = store.insert_message(self.conn, {
            "chat_id": chat_id, "tg_message_id": self.sequence,
            "received_at": "2026-11-03T10:00:00+00:00", "raw_text": text})
        store.set_suggestion(self.conn, message_id, category, text, "synthetic-model")
        if confirmed:
            store.confirm_category(self.conn, message_id, category)
        return dict(store.get_message(self.conn, message_id))

    def _search(self, query="Plan", limit=None, chat_id=111):
        inputs = {"query": query}
        if limit is not None:
            inputs["limit"] = limit
        inputs = tool_broker.validate_input(tool_broker.get_spec("knowledge.search"), inputs)
        outcome = task_runner._knowledge_search(self.conn, {"chat_id": chat_id}, None, inputs)
        self.assertEqual(outcome[0], "ok")
        return outcome[2]["value"]["results"]

    def test_one_new_suggestion_does_not_hide_an_older_confirmed_match(self):
        confirmed = self._note("Plan Alpha confirmed")
        suggested = self._note("Plan Beta awaiting confirmation", confirmed=False)
        self.assertEqual([row["note_no"] for row in self._search(limit=1)],
                         [confirmed["note_no"]])
        self.assertNotEqual(confirmed["note_no"], suggested["note_no"])

    def test_default_limit_counts_only_confirmed_matches(self):
        confirmed = [self._note(f"Plan confirmed {number}") for number in range(7)]
        for number in range(8):
            self._note(f"Plan suggested {number}", confirmed=False)
        expected = [row["note_no"] for row in reversed(confirmed)][:5]
        self.assertEqual([row["note_no"] for row in self._search()], expected)

    def test_confirmed_result_limit_preserves_newest_first_order(self):
        first = self._note("Plan first")
        second = self._note("Plan second")
        self._note("Plan awaiting confirmation", confirmed=False)
        self._note("Unrelated confirmed entry")
        self.assertEqual([row["note_no"] for row in self._search(limit=2)],
                         [second["note_no"], first["note_no"]])

    def test_general_browse_still_includes_suggestions(self):
        self._note("Plan confirmed")
        suggested = self._note("Plan suggested", confirmed=False)
        rows = store.list_messages(self.conn, query="Plan", limit=1, chat_id=111)
        self.assertEqual([(row["id"], row["status"]) for row in rows],
                         [(suggested["id"], "suggested")])

    def test_search_remains_owner_scoped(self):
        own = self._note("Plan owned")
        self._note("Plan other chat", chat_id=222)
        self._note("Plan pending", confirmed=False)
        results = self._search(limit=1)
        self.assertEqual([row["note_no"] for row in results], [own["note_no"]])
        self.assertEqual([row["text"] for row in results], ["Plan owned"])

    def test_suggestions_alone_produce_no_confirmed_results(self):
        self._note("Plan pending", confirmed=False)
        self.assertEqual(self._search(), [])

    def test_explicit_query_keeps_archived_confirmed_notes_reachable(self):
        archived = self._note("Plan archived")
        self.conn.execute("UPDATE messages SET knowledge_state='archived' WHERE id=?",
                          (archived["id"],))
        self.conn.commit()
        self._note("Plan suggested", confirmed=False)
        self.assertEqual([row["note_no"] for row in self._search(limit=1)],
                         [archived["note_no"]])

    def test_cyrillic_casefold_remains_supported(self):
        confirmed = self._note("ПЛАН Альфа")
        self._note("план Бета", confirmed=False)
        self.assertEqual([row["note_no"] for row in self._search("план", 1)],
                         [confirmed["note_no"]])


if __name__ == "__main__":
    unittest.main()
