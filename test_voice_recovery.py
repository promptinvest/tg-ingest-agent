"""Durable voice handoffs survive routing and SQLite settlement failures."""
from concurrent.futures import Future
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

import call_budget
import llm
import store
import tg_ingest_agent as entry
from testlib import make_config


class VoiceRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_config(ALLOWED_CHAT_IDS="111", DB_PATH=str(Path(self.tmp.name) / "cara.db"),
                               MEDIA_DIR=str(Path(self.tmp.name) / "media"), STT_MODE="remote")
        self.agent = entry.Agent(self.cfg)
        self.conn = self.agent.conn
        self.addCleanup(self.conn.close)
        self.agent.voice_async = True
        self.agent.VOICE_RETRY_SECONDS = 0
        self.agent.reply = mock.Mock(return_value={"message_id": 800})
        self.agent.send_chat_action = mock.Mock()
        self.agent._sleep = mock.Mock()
        self.update = {"update_id": 38001, "message": {"message_id": 380,
            "chat": {"id": 111}, "from": {"id": 111},
            "voice": {"file_id": "fixture", "file_unique_id": "voice38", "duration": 3}}}
        store.telegram_update_receive(self.conn, self.update, 111)
        store.telegram_update_attempt(self.conn, 38001)
        store.kv_set(self.conn, "offset", 38002)
        self.path = self.cfg.media_dir / "voice38.oga"
        self.path.write_bytes(b"synthetic audio")

    def completed(self):
        reserved = call_budget.reserve(self.cfg, self.conn, "stt", "stt", self.cfg.stt_model,
            3 / 60 * llm.STT_PRICE_PER_MINUTE, llm.budget_limits, llm.BudgetExceeded)
        future = Future()
        future.set_result(llm.Transcription("Please show my reminders", self.cfg.stt_model))
        self.agent.voice_jobs = {38001: (future, self.update, str(self.path), 3, reserved)}

    def test_retryable_routing_failure_resumes_without_restart_or_retranscription(self):
        self.completed()
        with mock.patch.object(self.agent, "dispatch", side_effect=[RuntimeError("temporary route failure"), None]) as dispatch, \
                mock.patch.object(llm, "transcribe") as transcribe:
            self.agent.flush_voice_jobs()
            self.assertEqual(store.telegram_update_get(self.conn, 38001)["status"], "pending")
            self.agent.flush_voice_jobs()
            self.agent.flush_voice_jobs()
        self.assertEqual(dispatch.call_count, 2)
        self.assertEqual(store.telegram_update_get(self.conn, 38001)["status"], "done")
        self.assertFalse(store.kv_get(self.conn, "voice_transcript:38001"))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM llm_usage WHERE kind='stt'").fetchone()[0], 1)
        transcribe.assert_not_called()

    def test_completed_result_survives_sqlite_failure_before_transcript_commit(self):
        self.completed()
        original = store.kv_set
        failed = False
        def write(conn, key, value):
            nonlocal failed
            if key == "voice_transcript:38001" and not failed:
                failed = True
                raise sqlite3.OperationalError("synthetic transient write outage")
            return original(conn, key, value)
        with mock.patch.object(store, "kv_set", side_effect=write), \
                mock.patch.object(self.agent, "dispatch") as dispatch:
            with self.assertRaises(sqlite3.OperationalError):
                self.agent.flush_voice_jobs()
            self.assertIn(38001, self.agent.voice_jobs)
            self.agent.flush_voice_jobs()
        self.assertEqual(store.telegram_update_get(self.conn, 38001)["status"], "done")
        self.assertEqual(dispatch.call_count, 1)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM llm_usage").fetchone()[0], 1)

    def test_routing_retries_stop_at_the_existing_update_attempt_cap(self):
        self.completed()
        self.cfg.update_max_attempts = 3
        with mock.patch.object(self.agent, "dispatch", side_effect=RuntimeError("persistent routing failure")) as dispatch:
            self.agent.flush_voice_jobs()
            self.agent.flush_voice_jobs()
            self.agent.flush_voice_jobs()
        self.assertEqual(dispatch.call_count, 2)
        self.assertEqual(store.telegram_update_get(self.conn, 38001)["status"], "failed")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM llm_usage WHERE kind='stt'").fetchone()[0], 1)

    def test_listening_notice_does_not_replace_the_resumed_transcript_quote(self):
        self.update["message"]["voice"]["duration"] = 30
        self.agent.reply = entry.Agent.reply.__get__(self.agent)
        self.agent.voice_jobs = {}
        self.agent.voice_executor = mock.Mock()
        future = Future()
        future.set_result(llm.Transcription("Please show my reminders", self.cfg.stt_model))
        self.agent.voice_executor.submit.return_value = future
        self.agent.download_file = mock.Mock(return_value=str(self.path))
        sent = []
        def send(token, method, params, **kwargs):
            sent.append((method, params))
            return {"message_id": 900 + len(sent)}
        with mock.patch.object(entry, "tg_call", side_effect=send), \
                mock.patch.object(self.agent, "dispatch"):
            self.agent.process_update_batch([self.update])
            self.agent.flush_voice_jobs()
        messages = [params["text"] for method, params in sent if method == "sendMessage"]
        self.assertEqual(len(messages), 2)
        self.assertIn("Please show my reminders", messages[1])
        keys = [row[0] for row in self.conn.execute("SELECT delivery_key FROM reply_outbox ORDER BY id")]
        self.assertEqual(len(set(keys)), 2)
        self.assertEqual(store.telegram_update_get(self.conn, 38001)["status"], "done")
    def test_restart_never_repeats_an_unsettled_remote_transcription(self):
        self.agent.voice_jobs = {}
        self.agent.voice_executor = mock.Mock()
        self.agent.voice_executor.submit.return_value = Future()
        self.agent.download_file = mock.Mock(return_value=str(self.path))
        self.agent.start_voice_job(self.update, 111, self.update["message"]["voice"])
        restarted = entry.Agent(self.cfg)
        self.addCleanup(restarted.conn.close)
        restarted.voice_jobs = {}
        restarted.voice_executor = mock.Mock()
        restarted.voice_executor.submit.return_value = Future()
        restarted.download_file = mock.Mock(return_value=str(self.path))
        restarted.reply = mock.Mock(return_value={"message_id": 801})
        restarted.send_chat_action = mock.Mock()
        restarted.start_voice_job(self.update, 111, self.update["message"]["voice"])
        restarted.voice_executor.submit.assert_not_called()
        self.assertEqual(restarted.conn.execute("SELECT count(*) FROM llm_usage").fetchone()[0], 1)
        self.assertEqual(restarted.conn.execute("SELECT kind FROM llm_usage").fetchone()[0], "unknown_stt")


if __name__ == "__main__":
    unittest.main()
