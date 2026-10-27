"""Offline regressions for async voice settlement and final task acknowledgements."""
import hashlib
import sqlite3
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest import mock

import call_budget
import llm
import store
import tasks_svc
import voice_svc
from testlib import make_config


class VoiceAgent(voice_svc.VoiceMixin):
    VOICE_LISTENING_NOTICE_SECONDS = 10

    def __init__(self, conn, cfg, path):
        self.conn, self.cfg, self.path = conn, cfg, path
        self.stop = False
        self.voice_jobs = {}
        self.voice_executor = mock.Mock()
        self.reply = mock.Mock()
        self.process_update_batch = mock.Mock()
        self.send_chat_action = mock.Mock()

    def lang(self):
        return "en"

    def download_file(self, *_args):
        return str(self.path)

    def _update_chat_id(self, update):
        return update["message"]["chat"]["id"]


class AsyncVoiceReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = store.open_db(Path(self.tmp.name) / "voice.db")
        self.addCleanup(self.conn.close)
        self.cfg = make_config()
        self.cfg.stt_mode = "remote"
        self.path = Path(self.tmp.name) / "voice.oga"
        self.path.write_bytes(b"synthetic audio")
        self.agent = VoiceAgent(self.conn, self.cfg, self.path)
        self.update = {"update_id": 3100, "message": {
            "message_id": 310, "chat": {"id": 111},
            "from": {"id": 111}, "voice": {"file_id": "fixture", "duration": 60}}}
        store.telegram_update_receive(self.conn, self.update, 111)

    def usage(self):
        return self.conn.execute("SELECT * FROM llm_usage ORDER BY id").fetchall()

    def completed(self, *, text=None, error=None):
        reservation = call_budget.reserve(
            self.cfg, self.conn, "stt", "stt", self.cfg.stt_model,
            llm.STT_PRICE_PER_MINUTE, llm.budget_limits, llm.BudgetExceeded)
        future = Future()
        if error is None:
            future.set_result(llm.Transcription(text, self.cfg.stt_model))
        else:
            future.set_exception(error)
        self.agent.voice_jobs[3100] = (future, self.update, str(self.path), 60, reservation)

    def test_failed_listening_notice_releases_only_unsent_stt_reservation(self):
        self.agent.reply.side_effect = OSError("notification unavailable")
        with self.assertRaises(OSError):
            self.agent.start_voice_job(self.update, 111, self.update["message"]["voice"])
        self.agent.voice_executor.submit.assert_not_called()
        self.assertFalse(self.path.exists())
        self.assertEqual([(r["kind"], r["cost_usd"]) for r in self.usage()],
                         [("rejected_stt", 0)])

    def test_successful_remote_noise_is_settled_before_transcript_rejection(self):
        self.completed(text="synthetic noise")
        with mock.patch.object(voice_svc.common, "is_stt_noise", return_value=True):
            self.agent.flush_voice_jobs()
        self.assertEqual([(r["kind"], r["seconds"], r["cost_usd"]) for r in self.usage()],
                         [("stt", 60, llm.STT_PRICE_PER_MINUTE)])
        self.agent.process_update_batch.assert_not_called()
        self.assertEqual(self.agent.voice_jobs, {})
        self.assertFalse(self.path.exists())

    def test_rejected_executor_submission_cleans_audio_and_unused_reservation(self):
        self.agent.voice_executor.submit.side_effect = RuntimeError("executor is shut down")
        with self.assertRaises(RuntimeError):
            self.agent.start_voice_job(self.update, 111, self.update["message"]["voice"])
        self.assertFalse(self.path.exists())
        self.assertEqual(self.agent.voice_jobs, {})
        self.assertEqual([(r["kind"], r["cost_usd"]) for r in self.usage()],
                         [("rejected_stt", 0)])

    def test_failed_reservation_cleanup_still_removes_unsent_audio(self):
        self.agent.voice_executor.submit.side_effect = RuntimeError("executor is shut down")
        with mock.patch.object(voice_svc.call_budget, "settle", side_effect=OSError("database unavailable")):
            with self.assertRaises(OSError):
                self.agent.start_voice_job(self.update, 111, self.update["message"]["voice"])
        self.assertFalse(self.path.exists())
        self.assertEqual(self.agent.voice_jobs, {})
        self.assertEqual(self.usage()[0]["kind"], "unknown_stt")

    def test_definitely_unsent_transport_releases_reservation(self):
        error = llm.LLMError("no transport capacity", transient=True)
        error.request_not_sent = True
        self.completed(error=error)
        self.agent.flush_voice_jobs()
        self.assertEqual([(r["kind"], r["cost_usd"]) for r in self.usage()],
                         [("rejected_stt", 0)])

    def test_unknown_transport_failure_keeps_conservative_charge(self):
        self.completed(error=llm.LLMError("completion usage unknown", transient=True))
        self.agent.flush_voice_jobs()
        self.assertEqual([(r["kind"], r["cost_usd"]) for r in self.usage()],
                         [("unknown_stt", llm.STT_PRICE_PER_MINUTE)])
        self.agent.process_update_batch.assert_not_called()

    def test_audio_cleanup_failure_does_not_keep_a_finished_job_live(self):
        self.completed(text="Synthetic owner request")
        with mock.patch.object(voice_svc.Path, "unlink", side_effect=OSError("file busy")):
            with self.assertRaises(OSError):
                self.agent.flush_voice_jobs()
        self.assertEqual(self.agent.voice_jobs, {})

    def test_later_routing_failure_does_not_release_completed_transcription(self):
        self.completed(text="Synthetic owner request")
        error = llm.LLMError("routing was not sent", transient=True)
        error.request_not_sent = True
        self.agent.process_update_batch.side_effect = error
        self.agent.flush_voice_jobs()
        self.assertEqual([(r["kind"], r["cost_usd"]) for r in self.usage()],
                         [("stt", llm.STT_PRICE_PER_MINUTE)])


class TaskArtifactAcknowledgementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = store.open_db(Path(self.tmp.name) / "tasks.db")
        self.addCleanup(self.conn.close)
        self.cfg = make_config(TASK_ARTIFACTS_DIR=str(
            Path(self.tmp.name) / "tg-ingest-agent" / "task-artifacts"))
        source = {"update_id": 3200, "message": {"message_id": 320,
                  "chat": {"id": 111}, "from": {"id": 111}, "text": "Read reminders"}}
        store.telegram_update_receive(self.conn, source, 111)
        store.convo_add(self.conn, 111, "user", "Read reminders", source="boss", update_id=3200)
        self.task, _ = store.assistant_task_create(self.conn, 111, 3200, {
            "objective": "Read reminders", "deliverable": "answer", "steps": [{
                "key": "read", "tool": "reminders.read", "input": {}, "bindings": {},
                "depends_on": [], "purpose": "Read the existing reminders"}]})
        root = Path(self.cfg.task_artifacts_dir)
        root.mkdir(parents=True)
        path = root / "result.md"
        body = b"Synthetic task result"
        path.write_bytes(body)
        self.artifact_id = store.task_artifact_create(
            self.conn, self.task["id"], "markdown", path.name, str(path), len(body),
            hashlib.sha256(body).hexdigest())
        self.conn.execute("UPDATE assistant_tasks SET status='completed',"
                          " final_summary='done', final_artifact_id=? WHERE id=?",
                          (self.artifact_id, self.task["id"]))
        self.conn.commit()
        self.agent = tasks_svc.TasksMixin()
        self.agent.conn, self.agent.cfg = self.conn, self.cfg

    def artifact(self):
        return self.conn.execute("SELECT * FROM task_artifacts WHERE id=?",
                                 (self.artifact_id,)).fetchone()

    def task_row(self):
        return store.assistant_task_get(self.conn, self.task["id"])

    def test_malformed_document_ack_does_not_claim_artifact_delivered_or_retry(self):
        with mock.patch.object(tasks_svc, "tg_send_document", return_value={"message_id": True}) as send:
            self.assertFalse(self.agent.on_task_completed(self.task_row(), "done", self.artifact_id))
            self.agent.retry_task_deliveries()
        self.assertEqual(send.call_count, 1)
        self.assertEqual(self.task_row()["delivery_status"], "ambiguous")
        self.assertIsNone(self.artifact()["delivered_at"])

    def test_valid_document_ack_binds_task_and_artifact_once(self):
        with mock.patch.object(tasks_svc, "tg_send_document", return_value={"message_id": 322}) as send:
            self.assertTrue(self.agent.on_task_completed(self.task_row(), "done", self.artifact_id))
            self.assertTrue(self.agent.on_task_completed(self.task_row(), "done", self.artifact_id))
        self.assertEqual(send.call_count, 1)
        self.assertEqual(self.task_row()["final_message_id"], 322)
        self.assertEqual(self.task_row()["delivery_status"], "delivered")
        self.assertIsNotNone(self.artifact()["delivered_at"])

    def test_artifact_and_final_message_binding_commit_together(self):
        self.conn.execute("CREATE TRIGGER reject_delivery BEFORE UPDATE OF delivery_status"
                          " ON assistant_tasks WHEN NEW.delivery_status='delivered'"
                          " BEGIN SELECT RAISE(ABORT, 'synthetic commit failure'); END")
        self.conn.commit()
        with mock.patch.object(tasks_svc, "tg_send_document", return_value={"message_id": 322}):
            with self.assertRaises(sqlite3.IntegrityError):
                self.agent.on_task_completed(self.task_row(), "done", self.artifact_id)
        self.assertIsNone(self.artifact()["delivered_at"])
        self.assertEqual(self.task_row()["delivery_status"], "sending")


if __name__ == "__main__":
    unittest.main()
