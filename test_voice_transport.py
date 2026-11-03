"""Voice download admission stays bounded and transport leaves the SQLite owner."""
from concurrent.futures import Future
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

import llm
import store
import tg_ingest_agent as entry
import voice_svc
from testlib import make_config


class VoiceTransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cfg = make_config(ALLOWED_CHAT_IDS="111", DB_PATH=str(Path(self.tmp.name) / "cara.db"),
                          MEDIA_DIR=str(Path(self.tmp.name) / "media"), STT_MODE="local_server")
        self.agent = entry.Agent(cfg)
        self.addCleanup(self.agent.conn.close)
        self.agent.voice_download_async = True
        self.agent.reply = mock.Mock(return_value={"message_id": 900})
        self.agent.send_chat_action = mock.Mock()
        self.update = {"update_id": 38101, "message": {"message_id": 381,
            "chat": {"id": 111}, "from": {"id": 111},
            "voice": {"file_id": "fixture", "file_unique_id": "voice381", "duration": 3}}}
        store.telegram_update_receive(self.agent.conn, self.update, 111)

    def test_live_download_runs_off_owner_thread_and_poll_state_stays_usable(self):
        owner = threading.get_ident()
        started, release = threading.Event(), threading.Event()
        def download(token, remote, destination):
            self.assertNotEqual(threading.get_ident(), owner)
            started.set()
            if not release.wait(3):
                raise AssertionError("download was not released")
            Path(destination).write_bytes(b"synthetic audio")
        def lookup(*args, **kwargs):
            self.assertNotEqual(threading.get_ident(), owner)
            return {"file_path": "voice/fixture.oga"}
        def old_download(*args):
            self.assertNotEqual(threading.get_ident(), owner, "download blocked the SQLite owner")
            return str(self.agent.cfg.media_dir / "voice381.oga")
        self.agent.download_file = old_download
        try:
            with mock.patch.object(voice_svc, "tg_call", side_effect=lookup, create=True), \
                    mock.patch.object(voice_svc, "tg_download", side_effect=download, create=True), \
                    mock.patch.object(llm, "transcribe", return_value=llm.Transcription("Please show reminders", "whisper.cpp-server")), \
                    mock.patch.object(self.agent, "process_update_batch"):
                self.assertEqual(self.agent.start_voice_job(self.update, 111, self.update["message"]["voice"]), "defer")
                self.assertTrue(started.wait(1))
                store.kv_set(self.agent.conn, "voice_poll_progress", "yes")
                self.assertEqual(store.kv_get(self.agent.conn, "voice_poll_progress"), "yes")
                release.set()
                for _ in range(4):
                    for job in list(self.agent.voice_jobs.values()):
                        job[0].result(timeout=2)
                    self.agent.flush_voice_jobs()
                    if not self.agent.voice_jobs:
                        break
        finally:
            release.set()
            executor = getattr(self.agent, "voice_executor", None)
            if executor is not None:
                executor.shutdown(wait=True)

    def test_second_voice_waits_in_durable_inbox_without_transport_or_budget(self):
        self.agent.voice_jobs = {38101: (Future(), self.update, None, 3, None)}
        self.agent.voice_executor = mock.Mock()
        self.agent.download_file = mock.Mock(return_value="unused.oga")
        second = {"update_id": 38102, "message": {"message_id": 382,
            "chat": {"id": 111}, "from": {"id": 111},
            "voice": {"file_id": "fixture2", "file_unique_id": "voice382", "duration": 3}}}
        store.telegram_update_receive(self.agent.conn, second, 111)
        with mock.patch.object(voice_svc, "tg_call", create=True) as lookup, \
                mock.patch.object(voice_svc, "tg_download", create=True) as download:
            self.assertEqual(self.agent.start_voice_job(second, 111, second["message"]["voice"]), "defer")
        self.assertEqual(len(self.agent.voice_jobs), 1)
        self.agent.voice_executor.submit.assert_not_called()
        self.agent.download_file.assert_not_called()
        lookup.assert_not_called()
        download.assert_not_called()
        self.assertEqual(store.telegram_update_get(self.agent.conn, 38102)["status"], "pending")
        self.assertEqual(self.agent.conn.execute("SELECT count(*) FROM llm_usage").fetchone()[0], 0)
        self.agent.voice_executor.submit.return_value = Future()
        self.agent.voice_jobs[38101][0].set_result(llm.Transcription("Please show reminders", "whisper.cpp-server"))
        with mock.patch.object(self.agent, "dispatch"):
            self.agent.flush_voice_jobs()
        self.assertEqual(set(self.agent.voice_jobs), {38102})
        self.assertEqual(self.agent.voice_executor.submit.call_count, 1)
        self.assertEqual(store.telegram_update_get(self.agent.conn, 38101)["status"], "done")
        self.assertEqual(store.telegram_update_get(self.agent.conn, 38102)["status"], "pending")


if __name__ == "__main__":
    unittest.main()
