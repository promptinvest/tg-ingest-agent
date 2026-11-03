"""Expired fleet cards cannot consume another skill's unquoted confirmation."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import fleet
import fleet_protocol as protocol
import store
import tasking
from test_fleet import Agent, inventory


class FleetApprovalExpiryTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.conn = store.open_db(self.root / "test.db")
        self.addCleanup(self.conn.close)
        (self.root / "inventory.json").write_text(protocol.canonical(inventory()))
        self.agent = Agent(self.conn, self.root)
        self.next_update = 1

    def approval(self, *, expired=False):
        update = self.next_update
        self.next_update += 1
        question = "Restart the worker " + str(update)
        store.telegram_update_receive(self.conn, {
            "update_id": update,
            "message": {"chat": {"id": 111}, "text": question,
                        "message_id": 10 + update},
        }, chat_id=111)
        store.convo_add(self.conn, 111, "user", question, update_id=update,
                        source="boss", tg_message_id=10 + update)
        expires = datetime.now(timezone.utc) + timedelta(minutes=-1 if expired else 5)
        identity = format(update, "032x")
        payload = {
            "lang": "en", "source_hash": tasking.source_hash(question),
            "approval_message": 100 + update,
            "approval": {
                "operation": "restart", "host": "pd",
                "service": "cara-worker.service", "fingerprint": "a" * 64,
                "token": format(update + 100, "032x"),
                "expires": expires.isoformat(),
                "inventory_hash": protocol.digest(inventory()),
            },
        }
        stamp = protocol.now()
        self.conn.execute("INSERT INTO fleet_requests VALUES(?,?,?,?,?,?,?)", (
            identity, 111, update, "approval", protocol.canonical(payload), stamp, stamp))
        self.conn.commit()
        return self.conn.execute("SELECT * FROM fleet_requests WHERE id=?", (identity,)).fetchone()

    def resolve(self, text="yes", *, reply_to=None):
        message = {} if reply_to is None else {"reply_to_message": {"message_id": reply_to}}
        return self.agent.resolve_fleet_confirmation_text(111, "en", message, text)

    def rows(self):
        return [tuple(row) for row in self.conn.execute("SELECT * FROM fleet_requests ORDER BY id")]

    def test_expired_card_does_not_consume_unquoted_confirmation_or_mutate_history(self):
        self.approval(expired=True)
        before = self.rows()
        with mock.patch.object(self.agent, "handle_fleet_callback") as callback:
            for text in ("yes", "да", "confirm", "подтверждаю", "no", "нет", "cancel", "отмена"):
                with self.subTest(text=text):
                    self.assertFalse(self.resolve(text))
        callback.assert_not_called()
        self.assertEqual(self.agent.messages, [])
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fleet_events").fetchone()[0], 0)

    def test_expired_card_leaves_ordinary_pending_confirmation_available(self):
        self.approval(expired=True)
        with mock.patch.object(self.agent, "handle_fleet_callback") as callback:
            for kind in ("reminder", "note_archive", "note_edit"):
                store.pending_set(self.conn, 111, kind, {"title": "fixture"})
                before = store.pending_get(self.conn, 111)
                with self.subTest(kind=kind):
                    self.assertFalse(self.resolve("да"))
                    self.assertEqual(store.pending_get(self.conn, 111), before)
        callback.assert_not_called()
        self.assertEqual(self.agent.messages, [])

    def test_expired_card_does_not_intercept_an_existing_task_approval(self):
        self.approval(expired=True)
        with mock.patch.object(store, "task_approvals_live", return_value=True), \
                mock.patch.object(self.agent, "handle_fleet_callback") as callback:
            self.assertFalse(self.resolve())
        callback.assert_not_called()
        self.assertEqual(self.agent.messages, [])

    def test_one_live_card_is_selected_when_an_expired_card_remains_in_history(self):
        expired = self.approval(expired=True)
        live = self.approval()
        before = self.rows()
        message = json.loads(live["payload"])["approval_message"]
        with mock.patch.object(self.agent, "handle_fleet_callback") as callback:
            self.assertTrue(self.resolve())
        callback.assert_called_once_with(None, 111, {"message_id": message},
                                         "fl|" + live["id"] + "|y")
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.agent.messages, [])
        self.assertEqual(self.conn.execute("SELECT state FROM fleet_requests WHERE id=?",
                                          (expired["id"],)).fetchone()[0], "approval")

    def test_two_live_cards_still_require_an_explicit_card(self):
        self.approval(expired=True)
        self.approval()
        self.approval()
        before = self.rows()
        with mock.patch.object(self.agent, "handle_fleet_callback") as callback:
            self.assertTrue(self.resolve())
        callback.assert_not_called()
        self.assertIn("Reply to the intended card", self.agent.messages[-1][1])
        self.assertEqual(self.rows(), before)

    def test_live_fleet_card_still_conflicts_with_other_live_confirmation_types(self):
        self.approval()
        before = self.rows()
        with mock.patch.object(self.agent, "handle_fleet_callback") as callback:
            store.pending_set(self.conn, 111, "reminder", {"title": "fixture"})
            self.assertTrue(self.resolve())
            store.pending_clear(self.conn, 111)
            with mock.patch.object(store, "task_approvals_live", return_value=True):
                self.assertTrue(self.resolve())
        callback.assert_not_called()
        self.assertEqual(len(self.agent.messages), 2)
        self.assertEqual(self.rows(), before)

    def test_explicit_expired_card_reply_does_not_fall_through_to_a_live_card(self):
        expired = self.approval(expired=True)
        live = self.approval()
        live_before = tuple(live)
        message = json.loads(expired["payload"])["approval_message"]
        with mock.patch.object(fleet, "submit") as submit:
            self.assertTrue(self.resolve(reply_to=message))
        submit.assert_not_called()
        current = self.conn.execute("SELECT * FROM fleet_requests WHERE id=?", (live["id"],)).fetchone()
        self.assertEqual(tuple(current), live_before)
        self.assertEqual(self.conn.execute("SELECT state FROM fleet_requests WHERE id=?",
                                          (expired["id"],)).fetchone()[0], "ambiguous")
        self.assertIn("Check state", self.agent.messages[-1][1])

    def test_unknown_reply_card_still_falls_through_to_its_own_skill(self):
        self.approval(expired=True)
        self.approval()
        with mock.patch.object(self.agent, "handle_fleet_callback") as callback:
            self.assertFalse(self.resolve(reply_to=999))
        callback.assert_not_called()
        self.assertEqual(self.agent.messages, [])

    def test_malformed_expiry_still_requires_an_explicit_card_instead_of_falling_through(self):
        row = self.approval()
        payload = json.loads(row["payload"])
        payload["approval"]["expires"] = "not-a-time"
        self.conn.execute("UPDATE fleet_requests SET payload=? WHERE id=?",
                          (protocol.canonical(payload), row["id"]))
        self.conn.commit()
        store.pending_set(self.conn, 111, "reminder", {"title": "fixture"})
        with mock.patch.object(self.agent, "handle_fleet_callback") as callback:
            self.assertTrue(self.resolve())
        callback.assert_not_called()
        self.assertIn("Reply to the intended card", self.agent.messages[-1][1])


if __name__ == "__main__":
    unittest.main()
