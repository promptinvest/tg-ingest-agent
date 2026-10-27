"""Operational provenance survives durable delivery without changing history."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import store
import tg_ingest_agent as entry
from testlib import make_config
from texts import T
from tg_api import TelegramError


class OperationalDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cfg = make_config(ALLOWED_CHAT_IDS="1", DB_PATH=str(Path(self.temp.name) / "db"),
                               MEDIA_DIR=str(Path(self.temp.name) / "media"))
        self.agent = entry.Agent(self.cfg)
        self.addCleanup(lambda: self.agent.conn.close())
        self.transport = mock.patch.object(entry, "tg_call", return_value={"message_id": 12}).start()
        self.addCleanup(mock.patch.stopall)

    def last_row(self):
        return self.agent.conn.execute("SELECT * FROM conversation ORDER BY id DESC LIMIT 1").fetchone()

    def test_native_dead_letter_keeps_operational_provenance(self):
        self.agent._last_turn_lang = "en"
        self.agent._notify_dead_letter(1)
        self.assertEqual(self.last_row()["text"], T("en", "update_dead_letter"))
        self.assertEqual(self.last_row()["source"], "operational:update_dead_letter")
        metadata = json.loads(self.agent.conn.execute("SELECT metadata_json FROM reply_outbox").fetchone()[0])
        self.assertEqual(metadata["conversation_source"], "operational:update_dead_letter")

    def test_provenance_survives_known_unsent_retry_and_restart_once(self):
        self.transport.side_effect = TelegramError("rejected", status=429, retry_after=1)
        self.agent._last_turn_lang = "en"
        self.agent._notify_dead_letter(1)
        self.assertIsNone(self.last_row())
        self.agent.conn.close()
        self.agent = entry.Agent(self.cfg)
        self.agent.conn.execute("UPDATE reply_outbox SET available_at='2000-01-01'")
        self.agent.conn.commit()
        self.transport.side_effect = None
        self.agent.check_reply_deliveries()
        self.agent.check_reply_deliveries()
        self.assertEqual(self.last_row()["source"], "operational:update_dead_letter")
        self.assertEqual(self.agent.conn.execute("SELECT COUNT(*) FROM conversation").fetchone()[0], 1)
        self.assertEqual(self.transport.call_count, 2)

    def test_unknown_provenance_cannot_change_ordinary_history(self):
        self.agent._reply_delivery_metadata = {"conversation_source": "operational:invented"}
        self.agent.reply(1, "ordinary answer")
        self.assertEqual(self.last_row()["source"], "boss")

    def test_scheduled_budget_stop_is_tagged_without_leaking_delivery_context(self):
        prior = {"synthetic_existing_context": True}
        self.agent._reply_delivery_metadata = prior
        with mock.patch.object(entry.llm, "budget_state", return_value=("stop", "day", 2.0, 2.0)):
            self.agent.check_budget_notice()
        self.assertEqual(self.last_row()["source"], "operational:budget_stop")
        self.assertIs(self.agent._reply_delivery_metadata, prior)

    def test_tagged_notice_replay_is_historical_data(self):
        row = {"role": "bot", "source": "operational:llm_error", "text": "known native outage"}
        rendered = store.convo_replay_text(row)
        self.assertIn("historical", rendered.casefold())
        self.assertIn("known native outage", rendered)
        self.assertNotEqual(rendered, row["text"])

    def test_legacy_notice_replay_does_not_assert_native_failure(self):
        row = {"role": "bot", "source": "boss", "text": T("ru", "update_dead_letter")}
        rendered = store.convo_replay_text(row)
        self.assertIn("unverified", rendered.casefold())
        self.assertIn(row["text"], rendered)

    def test_owner_and_forward_quotations_keep_their_origins(self):
        notice = T("en", "update_dead_letter")
        self.assertEqual(store.convo_replay_text({"role": "user", "text": notice, "source": "boss"}), notice)
        forwarded = store.convo_replay_text({"role": "user", "text": notice, "source": "forward"})
        self.assertIn("пересланный", forwarded)
        self.assertNotIn("operational", forwarded)


if __name__ == "__main__":
    unittest.main()
