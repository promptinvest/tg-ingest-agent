"""Bounded voice transport; durable handoffs and all SQLite work stay on the owner."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import time

import action_delivery
import call_budget
import common
import llm
import store
from texts import T
from tg_api import TelegramError, tg_call, tg_download


def _download_voice(token, media_dir, file_id, unique_id):
    """Transport receives captured plain inputs, never an Agent or connection."""
    directory = Path(media_dir)
    existing = [path for path in directory.glob(f"{unique_id}.*")
                if not path.name.endswith(".voice-part")]
    if existing:
        return str(existing[0])
    info = tg_call(token, "getFile", {"file_id": file_id})
    remote = info.get("file_path") or ""
    destination = directory / f"{unique_id}{Path(remote).suffix or '.oga'}"
    partial = Path(str(destination) + ".voice-part")
    try:
        tg_download(token, remote, partial)
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)
    return str(destination)


class _VoiceJob:
    def __init__(self, future, update, path, duration, reserved, *, phase="stt", cfg=None):
        self.future, self.update, self.path = future, update, path
        self.duration, self.reserved, self.phase, self.cfg = duration, reserved, phase, cfg
        self.error = None

    def __getitem__(self, index):
        # Preserve the original future/update/path/duration/reservation seam.
        return (self.future, self.update, self.path, self.duration, self.reserved)[index]


class VoiceMixin:
    VOICE_RETRY_SECONDS = 5

    def voice_work_pending(self):
        return bool(getattr(self, "voice_jobs", {}) or getattr(self, "_voice_retry_at", {})
                    or getattr(self, "_voice_waiting", False))

    @staticmethod
    def _voice_marker(uid):
        return f"voice_dispatch:{uid}"

    def _voice_notice(self, uid, chat_id, duration):
        self.send_chat_action(chat_id, "typing")
        if duration <= self.VOICE_LISTENING_NOTICE_SECONDS:
            return
        previous = getattr(self, "_reply_delivery_key", None)
        self._reply_delivery_key = f"voice-listening:{uid}"
        try:
            self.reply(chat_id, T(self.lang(), "voice_listening", seconds=duration), record=False)
        finally:
            self._reply_delivery_key = previous

    def _voice_reserve(self, uid, duration):
        if self.cfg.stt_mode in ("local", "local_server"):
            return None
        with action_delivery.local_transaction(self.conn):
            reserved = call_budget.reserve(
                self.cfg, self.conn, "stt", "stt", self.cfg.stt_model,
                max(duration, 1) / 60 * llm.STT_PRICE_PER_MINUTE,
                llm.budget_limits, llm.BudgetExceeded)
            # An interruption after this point cannot authorize another paid STT.
            store.kv_set(self.conn, self._voice_marker(uid), str(reserved))
        return reserved

    def _voice_reject_unsent(self, uid, reserved):
        if reserved is None:
            return
        with action_delivery.local_transaction(self.conn):
            call_budget.settle(self.conn, reserved, "rejected_stt")
            self.conn.execute("DELETE FROM kv WHERE key=?", (self._voice_marker(uid),))

    def _voice_interrupted(self, uid, chat_id):
        with action_delivery.local_transaction(self.conn):
            store.issue_add(self.conn, chat_id, "stt_failed",
                            "remote transcription interrupted; usage unknown; automatic retry withheld")
            store.telegram_update_done(self.conn, uid)
            self.conn.execute("DELETE FROM kv WHERE key=?", (self._voice_marker(uid),))
        self.reply(chat_id, T(self.lang(), "stt_failed"))

    def start_voice_job(self, update, chat_id, voice):
        if not self.cfg.stt_enabled:
            self.reply(chat_id, T(self.lang(), "stt_failed"))
            return None
        if not hasattr(self, "voice_jobs"):
            self.voice_jobs = {}
        if not hasattr(self, "voice_executor"):
            self.voice_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="voice")
        uid = int(update["update_id"])
        if uid in self.voice_jobs:
            return "defer"
        if self.voice_jobs:
            # The inbox owns overflow; it consumes no thread, download or reservation.
            self._voice_waiting = True
            return "defer"
        if store.kv_get(self.conn, self._voice_marker(uid)):
            self._voice_interrupted(uid, chat_id)
            return None
        duration = int(voice.get("duration") or 0)
        captured = copy.copy(self.cfg)
        if getattr(self, "voice_download_async", False):
            future = self.voice_executor.submit(
                _download_voice, str(captured.token), str(captured.media_dir),
                voice.get("file_id"), voice.get("file_unique_id"))
            self.voice_jobs[uid] = _VoiceJob(future, update, None, duration, None,
                                            phase="download", cfg=captured)
            return "defer"
        # Keep the existing direct-call download seam. The live loop explicitly
        # selects the captured-input download phase above.
        reserved = self._voice_reserve(uid, duration)
        try:
            path = self.download_file(voice.get("file_id"), voice.get("file_unique_id"), ".oga")
        except Exception:
            self._voice_reject_unsent(uid, reserved)
            raise
        try:
            self._voice_notice(uid, chat_id, duration)
            future = self.voice_executor.submit(llm.transcribe, captured, None, "stt", path, duration)
        except Exception:
            try:
                self._voice_reject_unsent(uid, reserved)
            finally:
                Path(path).unlink(missing_ok=True)
            raise
        self.voice_jobs[uid] = _VoiceJob(future, update, path, duration, reserved, cfg=captured)
        return "defer"

    def _voice_cleanup(self, uid, job):
        # Cleanup failure cannot resurrect consumed inference or block admission.
        self.voice_jobs.pop(uid, None)
        if job.path:
            Path(job.path).unlink(missing_ok=True)

    def _voice_retry(self, uid):
        if not hasattr(self, "_voice_retry_at"):
            self._voice_retry_at = {}
        self._voice_retry_at[uid] = time.monotonic() + self.VOICE_RETRY_SECONDS

    def _voice_resume(self, uid, update):
        try:
            self.process_update_batch([update])
        except (llm.LLMError, OSError) as exc:
            # Retain the supported direct/mocked caller failure behavior; real
            # batch dispatch records its retryable failures in the durable inbox.
            with action_delivery.local_transaction(self.conn):
                store.issue_add(self.conn, self._update_chat_id(update), "stt_failed", str(exc)[:200])
                store.telegram_update_done(self.conn, uid)
            self.reply(self._update_chat_id(update), T(self.lang(), "stt_failed"))
        row = store.telegram_update_get(self.conn, uid)
        if row and row["status"] == "pending":
            self._voice_retry(uid)
        else:
            store.kv_set(self.conn, f"voice_transcript:{uid}", "")
            getattr(self, "_voice_retry_at", {}).pop(uid, None)

    def _voice_download_failed(self, uid, job, error):
        row = store.telegram_update_get(self.conn, uid)
        terminal = bool(row and row["attempts"] >= self.cfg.update_max_attempts)
        with action_delivery.local_transaction(self.conn):
            store.telegram_update_fail(self.conn, uid, repr(error), terminal=terminal)
            if terminal:
                store.issue_add(self.conn, self._update_chat_id(job.update), "stt_failed", str(error)[:200])
        if terminal:
            getattr(self, "_voice_retry_at", {}).pop(uid, None)
        else:
            self._voice_retry(uid)
        self._voice_cleanup(uid, job)
        if terminal:
            key = "stt_too_big" if "too big" in str(error).lower() else "stt_failed"
            self.reply(self._update_chat_id(job.update), T(self.lang(), key))

    def _voice_advance(self, uid, job):
        if job.phase == "download":
            try:
                job.path = job.future.result()
            except Exception as exc:
                self._voice_download_failed(uid, job, exc)
                return
            job.phase = "prepare"
        if job.phase == "prepare":
            try:
                self._voice_notice(uid, self._update_chat_id(job.update), job.duration)
                job.reserved = self._voice_reserve(uid, job.duration)
            except (TelegramError, llm.LLMError, OSError) as exc:
                self._voice_download_failed(uid, job, exc)
                return
            # After reservation succeeds, no exception may return to prepare.
            job.phase = "submit"
        if job.phase == "submit":
            try:
                job.future = self.voice_executor.submit(
                    llm.transcribe, job.cfg, None, "stt", job.path, job.duration)
            except Exception as exc:
                job.error, job.phase = exc, "unsent"
            else:
                job.phase = "stt"
                return
        if job.phase == "unsent":
            self._voice_reject_unsent(uid, job.reserved)
            self._voice_download_failed(uid, job, job.error)
            return
        try:
            text = job.future.result()
        except Exception as exc:
            text, job.error = None, exc
        model = getattr(text, "model", self.cfg.stt_model)
        cost = 0 if self.cfg.stt_mode in ("local", "local_server") else (
            max(job.duration, 1) / 60 * llm.STT_PRICE_PER_MINUTE)
        unusable = job.error is None and (not text or common.is_stt_noise(text))
        error = job.error or (llm.LLMError("unusable voice transcript") if unusable else None)
        with action_delivery.local_transaction(self.conn):
            if job.error is None:
                if job.reserved is not None:
                    call_budget.settle(self.conn, job.reserved, "stt", seconds=job.duration, cost_usd=cost)
                else:
                    store.usage_add(self.conn, "stt", "stt", model, seconds=job.duration, cost_usd=cost)
            elif job.reserved is not None and getattr(job.error, "request_not_sent", False):
                call_budget.settle(self.conn, job.reserved, "rejected_stt")
            self.conn.execute("DELETE FROM kv WHERE key=?", (self._voice_marker(uid),))
            if error is None:
                store.kv_set(self.conn, f"voice_transcript:{uid}", str(text))
            else:
                store.issue_add(self.conn, self._update_chat_id(job.update), "stt_failed", str(error)[:200])
                store.telegram_update_done(self.conn, uid)
        # The committed transcript owns routing retries; the consumed transport
        # stays live until this transaction commits, including across DB outages.
        self._voice_cleanup(uid, job)
        if error is None:
            self._voice_resume(uid, job.update)
        else:
            self.reply(self._update_chat_id(job.update), T(self.lang(), "stt_failed"))
            getattr(self, "_voice_retry_at", {}).pop(uid, None)

    def _voice_admit_pending(self, excluded):
        if self.voice_jobs or self.stop:
            return
        after = getattr(self, "_voice_scan_after", -1)
        query = ("SELECT * FROM telegram_updates WHERE status='pending' AND update_id>?"
                 " AND instr(payload,'\"voice\":')>0 ORDER BY update_id LIMIT 32")
        rows = self.conn.execute(query, (after,)).fetchall()
        if not rows and after != -1:
            self._voice_scan_after = -1
            rows = self.conn.execute(query, (-1,)).fetchall()
        self._voice_waiting = bool(rows)
        for row in rows:
            uid = row["update_id"]
            self._voice_scan_after = uid
            if uid in excluded or time.monotonic() < getattr(self, "_voice_retry_at", {}).get(uid, 0):
                continue
            try:
                update = json.loads(row["payload"])
            except (TypeError, ValueError):
                continue  # the existing inbox recovery owns malformed payloads
            msg = update.get("message") or {}
            voice = msg.get("voice")
            if not voice or msg.get("forward_origin"):
                continue
            chat_id = self._update_chat_id(update)
            owner_gate = getattr(self, "is_owner", None)
            if callable(owner_gate) and not owner_gate(chat_id, (msg.get("from") or {}).get("id")):
                continue
            if store.kv_get(self.conn, f"voice_transcript:{uid}"):
                self._voice_resume(uid, update)
            else:
                if uid in getattr(self, "_voice_retry_at", {}):
                    store.telegram_update_attempt(self.conn, uid)
                getattr(self, "_voice_retry_at", {}).pop(uid, None)
                try:
                    self.start_voice_job(update, chat_id, voice)
                except sqlite3.Error:
                    raise  # the existing database-stall guard owns write outages
                except Exception as exc:
                    self._voice_download_failed(uid, _VoiceJob(None, update, None,
                                                int(voice.get("duration") or 0), None), exc)
            break  # at most one admission or routing retry per tick

    def flush_voice_jobs(self):
        if not hasattr(self, "voice_jobs"):
            self.voice_jobs = {}
        processed = set()
        for uid, value in list(self.voice_jobs.items()):
            job = value if isinstance(value, _VoiceJob) else _VoiceJob(*value, cfg=copy.copy(self.cfg))
            self.voice_jobs[uid] = job
            if self.stop or (job.phase in ("download", "stt") and not job.future.done()):
                continue
            processed.add(uid)
            self._voice_advance(uid, job)
        self._voice_admit_pending(processed)
