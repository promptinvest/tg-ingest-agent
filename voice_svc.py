"""Serial voice transport off the poll thread; all persistence stays on the Agent."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import call_budget
import common
import llm
import store
from texts import T


class VoiceMixin:
    def start_voice_job(self, update, chat_id, voice):
        if not self.cfg.stt_enabled:
            self.reply(chat_id, T(self.lang(), "stt_failed"))
            return None
        if not hasattr(self, "voice_jobs"):
            self.voice_jobs = {}
            self.voice_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="voice")
        uid = int(update["update_id"])
        if uid in self.voice_jobs:
            return "defer"
        duration = int(voice.get("duration") or 0)
        reserved = None
        if self.cfg.stt_mode not in ("local", "local_server"):
            reserved = call_budget.reserve(
                self.cfg, self.conn, "stt", "stt", self.cfg.stt_model,
                max(duration, 1) / 60 * llm.STT_PRICE_PER_MINUTE,
                llm.budget_limits, llm.BudgetExceeded)
        try:
            path = self.download_file(voice.get("file_id"), voice.get("file_unique_id"), ".oga")
        except Exception:
            if reserved is not None:
                call_budget.settle(self.conn, reserved, "rejected_stt")
            raise
        self.send_chat_action(chat_id, "typing")
        if duration > self.VOICE_LISTENING_NOTICE_SECONDS:
            self.reply(chat_id, T(self.lang(), "voice_listening", seconds=duration), record=False)
        future = self.voice_executor.submit(llm.transcribe, self.cfg, None, "stt", path, duration)
        self.voice_jobs[uid] = (future, update, path, duration, reserved)
        return "defer"

    def flush_voice_jobs(self):
        for uid, job in list(getattr(self, "voice_jobs", {}).items()):
            future, update, path, duration, reserved = job
            if not future.done() or self.stop:
                continue
            chat_id = self._update_chat_id(update)
            try:
                text = future.result()
                if not text or common.is_stt_noise(text):
                    raise llm.LLMError("unusable voice transcript")
                model = getattr(text, "model", self.cfg.stt_model)
                cost = 0 if self.cfg.stt_mode in ("local", "local_server") else (
                    max(duration, 1) / 60 * llm.STT_PRICE_PER_MINUTE)
                if reserved is not None:
                    call_budget.settle(self.conn, reserved, "stt", seconds=duration, cost_usd=cost)
                else:
                    store.usage_add(self.conn, "stt", "stt", model, seconds=duration, cost_usd=cost)
                # Durable before routing: an interruption resumes from the transcript.
                store.kv_set(self.conn, f"voice_transcript:{uid}", str(text))
                self.process_update_batch([update])
                row = self.conn.execute("SELECT status FROM telegram_updates WHERE update_id=?",
                                        (uid,)).fetchone()
                if row and row[0] == "done":
                    store.kv_set(self.conn, f"voice_transcript:{uid}", "")
            except (llm.LLMError, OSError) as exc:
                store.issue_add(self.conn, chat_id, "stt_failed", str(exc)[:200])
                self.reply(chat_id, T(self.lang(), "stt_failed"))
                store.telegram_update_done(self.conn, uid)
            finally:
                Path(path).unlink(missing_ok=True)
                del self.voice_jobs[uid]
