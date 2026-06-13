#!/usr/bin/env python3
"""tg-ingest-agent: conversational personal assistant on Telegram.

One bot, one long-poll loop, skills as modules under a closed-world intent
router. Text and voice requests (RU/EN) are routed to: inbox ingest with
suggest-and-confirm categorization, reminders, AI-spend stats, and a small
preference memory. All model calls go through the budget-guarded gateway in
llm.py. No inbound ports; stdlib only.

Deployed on Pilot-VPS as /opt/tg-ingest-agent/agent.py.
"""
import signal
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boss_model
import events
import fetch
import gcal
import ingest
import jobs  # noqa: F401 (job helpers used by registered handlers)
import knowledge
import llm
import memory_curator
import persona
import reminders
import relationship
import review
import router
import runtime
import self_model
import skill_manifest
import spend
import storage
import store
import sysinfo
import trace
from common import Config, ShutdownInterrupt, current_trace, load_config, log  # noqa: F401
from tg_api import (TelegramError, tg_call, tg_download, tg_send_document,
                    tg_send_photo)
from texts import T

COMMAND_ALIASES = {"/start": "start", "/stats": "stats", "/categories": "categories"}


class Agent:
    def __init__(self, cfg):
        self.cfg = cfg
        self.conn = store.open_db(cfg.db_path)
        cfg.media_dir.mkdir(parents=True, exist_ok=True)
        for name in cfg.seed_categories:
            normalized = llm.normalize_category(name)
            if normalized:
                store.ensure_category(self.conn, normalized)
        self_model.seed(self.conn)  # Cara's deterministic self-knowledge
        # The memory curator runs as a background job (no proactive nudge):
        # it builds candidates the boss pulls via memory_review.
        runtime.register("memory_curator", "run_memory_curator",
                         lambda ctx, conn, payload, job: {"created": memory_curator.run_daily(conn)})
        self.albums = {}  # media_group_id -> {"parts": [...], "deadline": float}
        self.stop = False
        self.last_sweep = 0.0

    def request_stop(self, signum, _frame):
        log(f"received signal {signum}, shutting down")
        self.stop = True

    @staticmethod
    def _update_chat_id(update):
        msg = update.get("message") or (update.get("callback_query") or {}).get("message") or {}
        return (msg.get("chat") or {}).get("id")

    # -- preferences-backed settings

    def lang(self):
        return store.pref_get(self.conn, "language", self.cfg.language)

    def tz_offset(self):
        try:
            return int(store.pref_get(self.conn, "timezone_offset", self.cfg.timezone_offset))
        except (TypeError, ValueError):
            return self.cfg.timezone_offset

    def owner_name(self):
        name = store.pref_get(self.conn, "owner_name", "").strip()
        if name:
            return name
        return "друг" if self.lang() == "ru" else "friend"

    # -- Telegram helpers

    def reply(self, chat_id, text, reply_to=None, reply_markup=None, record=True):
        if record:
            store.convo_add(self.conn, chat_id, "bot", text)
        try:
            return tg_call(
                self.cfg.token,
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": text[:4000],
                    "reply_to_message_id": reply_to,
                    "allow_sending_without_reply": True,
                    "reply_markup": reply_markup,
                },
            )
        except TelegramError as exc:
            log(f"sendMessage failed: {exc}")
            return None

    def answer_callback(self, callback_id, text):
        try:
            tg_call(
                self.cfg.token,
                "answerCallbackQuery",
                {"callback_query_id": callback_id, "text": text[:200]},
            )
        except TelegramError as exc:
            log(f"answerCallbackQuery failed: {exc}")

    def edit_suggestion_message(self, chat_id, message_id, row):
        if not message_id:
            return
        summary = (row["summary"] or "")[:500]
        try:
            tg_call(
                self.cfg.token,
                "editMessageText",
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": f"{row['category']} ✅\n{summary}\n(#{row['id']})",
                },
            )
        except TelegramError as exc:
            log(f"editMessageText failed: {exc}")

    def download_file(self, file_id, unique_id, default_ext):
        existing = list(self.cfg.media_dir.glob(f"{unique_id}.*"))
        if existing:
            return str(existing[0])
        info = tg_call(self.cfg.token, "getFile", {"file_id": file_id})
        file_path = info.get("file_path") or ""
        ext = Path(file_path).suffix or default_ext
        dest = self.cfg.media_dir / f"{unique_id}{ext}"
        tg_download(self.cfg.token, file_path, dest)
        return str(dest)

    # -- Main loop

    def run(self):
        try:
            tg_call(self.cfg.token, "deleteWebhook", {"drop_pending_updates": False})
        except TelegramError as exc:
            log(f"deleteWebhook failed (continuing): {exc}")
        offset = int(store.kv_get(self.conn, "offset", "0") or 0)
        errors = 0
        log(
            f"polling started (model={self.cfg.do_model}, "
            f"known_categories={len(store.known_categories(self.conn))}, "
            f"allowed_chats={len(self.cfg.allowed_chat_ids)}, offset={offset})"
        )
        while not self.stop:
            now = time.time()
            self.flush_albums(now)
            self.fire_due_reminders()
            self.check_budget_notice()
            self.check_weekly_issues_report()
            self.check_daily_curator()
            if now - self.last_sweep >= self.cfg.retry_interval:
                self.last_sweep = now
                self.retry_sweep()
                runtime.drain(self.conn, self)  # runs due jobs (e.g. memory curator)
                self.housekeep()
            poll_timeout = 2 if self.albums else self.cfg.poll_timeout
            try:
                updates = tg_call(
                    self.cfg.token,
                    "getUpdates",
                    {
                        "offset": offset,
                        "timeout": poll_timeout,
                        "allowed_updates": ["message", "callback_query"],
                    },
                    timeout=poll_timeout + 15,
                )
                errors = 0
            except TelegramError as exc:
                if exc.status == 409:
                    log(f"getUpdates conflict (another poller or webhook active): {exc}")
                    time.sleep(30)
                    continue
                if exc.retry_after:
                    log(f"rate limited, sleeping {exc.retry_after}s")
                    time.sleep(min(int(exc.retry_after), 120))
                    continue
                errors += 1
                delay = min(60, 5 * (2 ** min(errors - 1, 4)))
                log(f"getUpdates failed ({exc}), retrying in {delay}s")
                time.sleep(delay)
                continue
            processed_max = None
            for update in updates or []:
                if self.stop:
                    break  # unprocessed updates redeliver after restart
                chat_id = self._update_chat_id(update)
                tid = trace.start(self.conn, "inbound", chat_id)
                try:
                    self.handle_update(update)
                    trace.finish(self.conn, tid, "ok")
                    events.record_done(self.conn, "telegram_message_received",
                                       chat_id=chat_id, trace_id=tid)
                except ShutdownInterrupt:
                    log(f"update {update.get('update_id')} left for redelivery (shutdown)")
                    trace.finish(self.conn, tid, "suppressed", "shutdown mid-update")
                    break  # do not count this update as processed
                except Exception as exc:  # never let one bad update kill the loop
                    log(f"error handling update {update.get('update_id')}: {exc!r}")
                    trace.event(self.conn, tid, trace.ISSUE_LOGGED, repr(exc), level="error")
                    trace.finish(self.conn, tid, "failed", repr(exc)[:200])
                    events.record_done(self.conn, "telegram_message_received", chat_id=chat_id,
                                       trace_id=tid, status="failed", error=repr(exc)[:200])
                processed_max = update["update_id"]
            if processed_max is not None:
                offset = processed_max + 1
                store.kv_set(self.conn, "offset", offset)
        self.flush_albums(time.time(), force=True)
        log("stopped")

    # -- Scheduler ticks

    def fire_due_reminders(self):
        now_iso = datetime.now(timezone.utc).isoformat()
        for row in store.reminders_due(self.conn, now_iso):
            lang = self.lang()
            self.reply(row["chat_id"], T(lang, "reminder_fired",
                                         name=self.owner_name(), title=row["title"]))
            store.pending_set(
                self.conn, row["chat_id"], "reminder_fired",
                {"reminder_id": row["id"], "title": row["title"]}, ttl_seconds=1800,
            )
            following = reminders.next_due(row["due_utc"], row["recurrence"])
            if following:
                store.reminder_update_due(self.conn, row["id"], following)
            else:
                # stays visible to snooze via the pending action; closed as done
                store.reminder_close(self.conn, row["id"], "done")
            log(f"reminder #{row['id']} fired")

    def check_budget_notice(self):
        state, period, spent, limit = llm.budget_state(self.cfg, self.conn)
        if state == "ok":
            return
        period_value = datetime.now(timezone.utc).strftime(
            "%Y-%m-%d" if period == "day" else "%Y-%m"
        )
        flag = f"budget_notice:{state}:{period}:{period_value}"
        if store.kv_get(self.conn, flag):
            return
        store.kv_set(self.conn, flag, "1")
        lang = self.lang()
        key = "budget_warn" if state == "warn" else "budget_stop"
        text = T(lang, key, spent=spent, limit=limit, period=T(lang, f"period_{period}"))
        for chat_id in self.cfg.allowed_chat_ids:
            self.reply(chat_id, text)

    def retry_sweep(self):
        rows = store.pending_messages(self.conn, self.cfg.llm_max_attempts)
        for row in rows:
            if self.stop:
                return
            suggestion = self.suggest_row(row)
            if suggestion:
                category, alternatives, summary = suggestion
                self.present_suggestion(
                    row["id"], row["chat_id"], row["tg_message_id"],
                    category, alternatives, summary, "",
                )

    # -- Update handling

    def handle_update(self, update):
        callback = update.get("callback_query")
        if callback:
            self.handle_callback(callback)
            return
        msg = update.get("message")
        if not msg:
            return
        chat_id = (msg.get("chat") or {}).get("id")
        if chat_id not in self.cfg.allowed_chat_ids:
            from_id = (msg.get("from") or {}).get("id")
            log(f"ignored message from chat_id={chat_id} user_id={from_id}")
            return

        # Cara learns her owner's name from the Telegram profile on first
        # contact; "запомни: меня зовут ..." overrides it anytime.
        if not store.pref_get(self.conn, "owner_name"):
            first_name = ((msg.get("from") or {}).get("first_name") or "").strip()
            if first_name:
                store.pref_set(self.conn, "owner_name", first_name)
                log(f"owner name captured: {first_name}")

        voice = msg.get("voice") or msg.get("audio")
        if voice:
            transcript = self.transcribe_voice(chat_id, voice)
            if transcript is None:
                return
            msg = dict(msg)
            msg["text"] = transcript
            self.reply(chat_id, T(self.lang(), "voice_quote", transcript=transcript[:300]),
                       record=False)

        text = (msg.get("text") or "").strip()
        is_content = bool(
            msg.get("forward_origin") or msg.get("photo") or msg.get("document")
            or msg.get("media_group_id")
        )
        if text:
            store.convo_add(self.conn, chat_id, "user", text)

        if is_content:
            group_id = msg.get("media_group_id")
            if group_id:
                buffer = self.albums.setdefault(str(group_id), {"parts": []})
                buffer["parts"].append(msg)
                buffer["deadline"] = time.time() + self.cfg.album_settle
                return
            self.finalize([msg])
            return

        if not text:
            return
        if text in COMMAND_ALIASES:
            self.handle_command(chat_id, COMMAND_ALIASES[text])
            return

        # Correction by replying to a suggestion message still works.
        reply_to_msg = msg.get("reply_to_message")
        if reply_to_msg:
            row = store.find_by_suggestion_message(
                self.conn, chat_id, reply_to_msg.get("message_id")
            )
            if row:
                self.handle_correction(row, chat_id, text, msg.get("message_id"))
                return

        self.dispatch(chat_id, msg, text)

    def transcribe_voice(self, chat_id, voice):
        lang = self.lang()
        if not self.cfg.stt_enabled:
            self.reply(chat_id, T(lang, "stt_failed"))
            return None
        path = None
        try:
            path = self.download_file(voice.get("file_id"), voice.get("file_unique_id"), ".oga")
            transcript = llm.transcribe(
                self.cfg, self.conn, "stt", path, int(voice.get("duration") or 0)
            )
        except (TelegramError, llm.LLMError) as exc:
            if self.stop:
                # A deploy/shutdown killed the transcription mid-run: say
                # nothing and let the update redeliver after restart (keep
                # the audio file for the retry).
                raise ShutdownInterrupt() from exc
            log(f"voice transcription failed: {exc}")
            store.issue_add(self.conn, chat_id, "stt_failed", str(exc)[:200])
            self.reply(chat_id, T(lang, "stt_failed"))
            return None
        finally:
            # The voice note is a transient artifact — we keep only the
            # transcript. Delete it once processing is done (not on shutdown).
            if path and not self.stop:
                Path(path).unlink(missing_ok=True)
        if not transcript:
            store.issue_add(self.conn, chat_id, "stt_failed", "empty transcript")
            self.reply(chat_id, T(lang, "stt_failed"))
            return None
        return transcript

    def housekeep(self):
        """Purge interim artifacts: media files with no DB reference (voice
        notes, orphans from deleted messages) and old review exports. Photos
        referenced by a stored message are content and kept. A grace window
        avoids racing in-flight downloads."""
        import time as _time
        referenced = set()
        for row in self.conn.execute(
            "SELECT local_path FROM images WHERE local_path IS NOT NULL"
        ):
            referenced.add(str(Path(row["local_path"])))
        grace = _time.time() - 3600  # 1h
        removed = 0
        try:
            for path in self.cfg.media_dir.glob("*"):
                if not path.is_file():
                    continue
                if str(path) in referenced:
                    continue
                if path.stat().st_mtime > grace:
                    continue  # too fresh — may be in flight
                path.unlink(missing_ok=True)
                removed += 1
        except OSError as exc:
            log(f"housekeep media error: {exc}")
        # Keep only the newest review exports.
        reviews_dir = self.cfg.db_path.parent / "reviews"
        if reviews_dir.is_dir():
            files = sorted(reviews_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
            for stale in files[self.cfg.review_keep:]:
                stale.unlink(missing_ok=True)
                removed += 1
        if removed:
            log(f"housekeep removed {removed} interim artifact(s)")

    # -- Router dispatch

    def dispatch(self, chat_id, msg, text):
        lang = self.lang()
        pending = store.pending_get(self.conn, chat_id)
        # A pending purge is confirmed ONLY by typing the exact phrase —
        # handled deterministically (no LLM), so a stray "да" can't wipe data.
        if pending and pending["kind"] == "purge":
            self.resolve_purge(chat_id, lang, pending, text)
            return
        # Greetings answered instantly without burning router tokens; with a
        # pending action, short acks like «ок» must reach the router instead
        # (they are confirmations there).
        if pending is None:
            kind = router.detect_smalltalk(text)
            if kind:
                self.reply(chat_id, T(lang, f"smalltalk_{kind}", name=self.owner_name()))
                return
        try:
            decision = router.route(self.cfg, self.conn, chat_id, text, pending)
        except llm.BudgetExceeded as exc:
            store.issue_add(self.conn, chat_id, "budget_stop", text[:200])
            self.reply(chat_id, T(lang, "budget_stop", spent=exc.spent, limit=exc.limit,
                                  period=T(lang, f"period_{exc.period}")))
            return
        except llm.LLMError as exc:
            log(f"router failed: {exc}")
            store.issue_add(self.conn, chat_id, "llm_error", f"router: {exc}")
            self.reply(chat_id, T(lang, "llm_error"))
            return
        action, params = decision["action"], decision["params"]
        log(f"routed chat={chat_id} action={action} confidence={decision['confidence']:.2f}")
        trace.event(self.conn, current_trace(), trace.ROUTER_COMPLETED, f"action={action}",
                    skill=action, data={"confidence": decision["confidence"]})

        if action == "ingest":
            self.finalize([msg])
        elif action == "reminder_create":
            draft = reminders.validate_draft(params)
            if not draft:
                self.reply(chat_id, T(lang, "clarify"))
                return
            store.pending_set(self.conn, chat_id, "reminder", draft)
            self.reply(chat_id, T(
                lang, "reminder_draft", title=draft["title"],
                when_local=reminders.fmt_local(draft["due_utc"], self.tz_offset()),
                recurrence=T(lang, "recurrence_" + draft["recurrence"]),
            ))
        elif action == "reminder_list":
            rows = store.reminders_active(self.conn, chat_id)
            self.reply(chat_id, reminders.format_list(rows, self.tz_offset(), lang))
        elif action == "reminder_cancel":
            rows = store.reminders_active(self.conn, chat_id)
            row = reminders.find_by_query(rows, params)
            if row:
                store.reminder_close(self.conn, row["id"], "cancelled")
                self.reply(chat_id, T(lang, "reminder_cancelled", rid=row["id"], title=row["title"]))
            else:
                self.reply(chat_id, T(lang, "reminder_not_found"))
        elif action == "calendar_add":
            draft = reminders.validate_draft(params)
            if draft:
                event = {
                    "uid": f"event-{int(time.time())}",
                    "title": draft["title"],
                    "start_utc": draft["due_utc"],
                    "duration_minutes": self.cfg.event_duration_minutes,
                    "recurrence": draft["recurrence"],
                }
                self.send_to_calendar(chat_id, event)
            else:
                rows = store.reminders_active(self.conn, chat_id)
                row = reminders.find_by_query(rows, params)
                if row:
                    self.send_to_calendar(
                        chat_id, gcal.event_from_reminder(row, self.cfg.event_duration_minutes)
                    )
                else:
                    self.reply(chat_id, T(lang, "calendar_not_found"))
        elif action == "spend":
            self.reply(chat_id, spend.format_spend(self.conn, params.get("period"), self.cfg, lang))
        elif action == "stats":
            self.reply(chat_id, self.stats_text(lang))
        elif action == "categories":
            self.reply(chat_id, self.categories_text(lang))
        elif action == "help":
            self.reply(chat_id, T(lang, "capabilities") + "\n— "
                       + " · ".join(skill_manifest.capability_titles(lang)))
        elif action == "overview":
            self.reply(chat_id, self.overview_text(lang))
        elif action == "list_items":
            self.reply(chat_id, self.items_text(lang, params))
        elif action == "item_detail":
            self.reply(chat_id, self.item_detail_text(lang, params))
        elif action == "item_delete":
            row = self.resolve_item(params)
            if row is None:
                self.reply(chat_id, T(lang, "items_empty"))
            else:
                store.pending_set(self.conn, chat_id, "delete", {"row_id": row["id"]})
                snippet = (row["summary"] or row["raw_text"] or "")[:60].replace("\n", " ")
                self.reply(chat_id, T(lang, "delete_confirm", row_id=row["id"],
                                      category=row["category"] or row["suggested_category"] or "?",
                                      snippet=snippet))
        elif action == "show_media":
            self.do_show_media(chat_id, lang, params)
        elif action == "discard":
            self.do_discard(chat_id, lang, pending)
        elif action == "vps_stats":
            self.reply(chat_id, sysinfo.format_report(
                sysinfo.collect(str(self.cfg.db_path.parent)), lang, self.media_bytes()))
        elif action == "purge":
            self.do_purge(chat_id, lang, params)
        elif action == "fetch":
            self.do_fetch(chat_id, lang, params)
        elif action == "ask":
            self.do_ask(chat_id, lang, params, text)
        elif action == "issues_report":
            self.reply(chat_id, self.issues_text(lang, params.get("period")))
        elif action == "review":
            self.do_review(chat_id, lang, params)
        elif action == "self_query":
            self.reply(chat_id, self_model.answer_self_query(self.conn, lang, self.cfg))
        elif action == "boss_query":
            self.reply(chat_id, boss_model.render_profile(self.conn, lang))
        elif action == "boss_memory_update":
            self.do_boss_memory(chat_id, lang, params)
        elif action == "style_update":
            self.do_style_update(chat_id, lang, params)
        elif action == "trace_query":
            self.reply(chat_id, self.trace_explain_text(lang, chat_id))
        elif action == "memory_review":
            self.show_memory_review(chat_id, lang)
        elif action == "working_history":
            self.reply(chat_id, relationship.render_working_history(self.conn, lang))
        elif action == "export":
            self.do_export(chat_id, lang, params)
        elif action == "memory":
            self.reply(chat_id, self.memory_text(lang))
        elif action == "remember":
            self.do_remember(chat_id, params, lang)
        elif action == "forget":
            self.do_forget(chat_id, params, lang)
        elif action == "smalltalk":
            kind = str(params.get("kind") or "hello").strip().lower()
            if kind not in router.SMALLTALK_KINDS:
                kind = "hello"
            self.reply(chat_id, T(lang, f"smalltalk_{kind}", name=self.owner_name()))
        elif action in ("confirm", "amend", "cancel"):
            self.resolve_pending(chat_id, action, params, pending, lang)
        elif action == "clarify":
            store.issue_add(self.conn, chat_id, "unclear_request", text[:200])
            question = str(params.get("question") or "").strip()
            self.reply(chat_id, question[:300] if question else T(lang, "clarify"))
        else:
            store.issue_add(self.conn, chat_id, "out_of_scope", text[:200])
            self.reply(chat_id, T(lang, "out_of_scope"))

    def handle_command(self, chat_id, name):
        lang = self.lang()
        if name == "start":
            self.reply(chat_id, T(lang, "start", name=self.owner_name()))
        elif name == "stats":
            self.reply(chat_id, self.stats_text(lang))
        else:
            self.reply(chat_id, self.categories_text(lang))

    # -- Pending-action resolution (conversational confirmation)

    def resolve_pending(self, chat_id, action, params, pending, lang):
        if not pending:
            self.reply(chat_id, T(lang, "nothing_pending"))
            return
        kind, payload = pending["kind"], pending["payload"]
        if action == "cancel":
            store.pending_clear(self.conn, chat_id)
            self.reply(chat_id, T(lang, "cancelled"))
            return
        if kind == "category":
            row = store.get_message(self.conn, payload.get("row_id"))
            if not row:
                store.pending_clear(self.conn, chat_id)
                return
            category = (llm.normalize_category(params.get("category"))
                        if action == "amend" else None)
            category = category or row["suggested_category"] or self.cfg.fallback_category
            store.pending_clear(self.conn, chat_id)
            self.apply_category_confirm(chat_id, row, category, reply_to=None)
        elif kind == "reminder":
            if action == "amend":
                merged = dict(payload)
                merged.update({k: v for k, v in params.items() if v is not None})
                draft = reminders.validate_draft(merged)
                if not draft:
                    self.reply(chat_id, T(lang, "clarify"))
                    return
                store.pending_set(self.conn, chat_id, "reminder", draft)
                self.reply(chat_id, T(
                    lang, "reminder_draft", title=draft["title"],
                    when_local=reminders.fmt_local(draft["due_utc"], self.tz_offset()),
                    recurrence=T(lang, "recurrence_" + draft["recurrence"]),
                ))
                return
            rid = store.reminder_add(
                self.conn, chat_id, payload["title"], payload["due_utc"], payload["recurrence"]
            )
            relationship.log_event(self.conn, "reminder_set",
                                   f"set a reminder: {payload['title']}", importance=1,
                                   source_table="reminders", source_id=rid)
            store.pending_clear(self.conn, chat_id)
            self.reply(chat_id, T(
                lang, "reminder_set", rid=rid, title=payload["title"],
                when_local=reminders.fmt_local(payload["due_utc"], self.tz_offset()),
            ))
            if (store.pref_get(self.conn, "auto_calendar") or "").casefold() in ("1", "true", "yes", "да"):
                row = store.reminder_get(self.conn, rid)
                self.send_to_calendar(chat_id, gcal.event_from_reminder(
                    row, self.cfg.event_duration_minutes))
        elif kind == "reminder_fired":
            store.pending_clear(self.conn, chat_id)
            snooze = params.get("snooze_minutes") if action == "amend" else None
            if snooze:
                try:
                    minutes = max(1, int(snooze))
                except (TypeError, ValueError):
                    minutes = 30
                due = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
                rid = store.reminder_add(self.conn, chat_id, payload["title"], due)
                self.reply(chat_id, T(lang, "reminder_snoozed",
                                      when_local=reminders.fmt_local(due, self.tz_offset())))
                log(f"reminder snoozed as #{rid}")
            else:
                self.reply(chat_id, T(lang, "reminder_done"))
        elif kind == "delete":
            if action != "confirm":  # deletion only on an explicit yes
                store.pending_clear(self.conn, chat_id)
                self.reply(chat_id, T(lang, "cancelled"))
                return
            store.pending_clear(self.conn, chat_id)
            row_id = payload.get("row_id")
            if store.get_message(self.conn, row_id) is not None:
                for path in store.delete_message(self.conn, row_id):
                    Path(path).unlink(missing_ok=True)
                log(f"message #{row_id} deleted by operator")
                self.reply(chat_id, T(lang, "deleted", row_id=row_id))
        elif kind == "habit":
            store.pending_clear(self.conn, chat_id)
            source = payload["source_chat_id"]
            if action == "confirm":
                store.pref_set(self.conn, f"auto_cat:{source}", payload["category"])
                self.reply(chat_id, T(lang, "habit_enabled",
                                      source=payload.get("source_title") or source,
                                      category=payload["category"]))
            else:
                store.pref_set(self.conn, f"auto_cat_declined:{source}", "1")
        else:
            store.pending_clear(self.conn, chat_id)

    # -- Calendar

    def send_to_calendar(self, chat_id, event):
        lang = self.lang()
        if gcal.configured(self.cfg):
            try:
                link = gcal.insert_event(self.cfg, self.conn, event)
                self.reply(chat_id, T(lang, "calendar_added", title=event["title"], link=link))
                return
            except gcal.CalendarError as exc:
                log(f"gcal insert failed, falling back to .ics: {exc}")
        ics = gcal.make_ics([event])
        try:
            tg_send_document(
                self.cfg.token, chat_id, f"{event['uid']}.ics", ics.encode("utf-8"),
                caption=T(lang, "calendar_ics", title=event["title"]),
            )
            store.convo_add(self.conn, chat_id, "bot", f"[.ics file: {event['title']}]")
        except TelegramError as exc:
            store.issue_add(self.conn, chat_id, "calendar_failed", str(exc)[:200])
            self.reply(chat_id, T(lang, "calendar_failed", error=str(exc)[:200]))

    # -- Memory skill

    def do_boss_memory(self, chat_id, lang, params):
        op = str(params.get("op") or "remember").strip().lower()
        value = params.get("value") or ""
        if op == "forget":
            removed = boss_model.forget(self.conn, value)
            self.reply(chat_id, T(lang, "boss_forgotten", value=removed) if removed
                       else T(lang, "boss_not_found"))
        elif op == "confirm":
            confirmed = boss_model.confirm(self.conn, value)
            self.reply(chat_id, T(lang, "boss_confirmed_ok", value=confirmed) if confirmed
                       else T(lang, "boss_not_found"))
        else:  # remember (explicit -> confirmed profile item)
            kind = str(params.get("kind") or "workflow").strip()
            if kind not in boss_model.KINDS:
                kind = "workflow"
            item_id = boss_model.remember_explicit(self.conn, value, kind)
            if item_id is None:
                self.reply(chat_id, T(lang, "clarify"))
            else:
                self.reply(chat_id, T(lang, "boss_remembered", value=value))

    def do_style_update(self, chat_id, lang, params):
        tone = str(params.get("tone") or "").strip().lower()
        if tone in ("warm", "warmer"):
            store.pref_set(self.conn, "tone", "warm")
            self.reply(chat_id, T(lang, "style_warmer"))
        elif tone in ("concise", "short", "neutral_concise"):
            store.pref_set(self.conn, "tone", "concise")
            self.reply(chat_id, T(lang, "style_concise"))
        else:
            store.pref_set(self.conn, "tone", "neutral")
            self.reply(chat_id, T(lang, "style_neutral"))

    def do_export(self, chat_id, lang, params):
        what = str(params.get("what") or "review").strip().lower()
        if what not in review.EXPORT_KINDS:
            what = "review"
        filename, md = review.export_document(self.conn, self.cfg, what, lang,
                                              params.get("period") or "week")
        exports_dir = self.cfg.db_path.parent / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)
        (exports_dir / filename).write_text(md, encoding="utf-8")
        try:
            tg_send_document(self.cfg.token, chat_id, filename, md.encode("utf-8"),
                             caption=T(lang, "review_file_caption"), content_type="text/markdown")
        except TelegramError as exc:
            log(f"export send failed: {exc}")
            self.reply(chat_id, T(lang, "llm_error"))

    def show_memory_review(self, chat_id, lang):
        pending = store.candidates_pending(self.conn)
        if not pending:
            self.reply(chat_id, T(lang, "memory_review_empty"))
            return
        self.reply(chat_id, T(lang, "memory_review_header"))
        for c in pending:
            keyboard = {"inline_keyboard": [[
                {"text": T(lang, "mc_remember"), "callback_data": f"mc|{c['id']}|y"},
                {"text": T(lang, "mc_skip"), "callback_data": f"mc|{c['id']}|n"},
            ]]}
            self.reply(chat_id, f"#{c['id']} {c['proposed_text']}", reply_markup=keyboard)

    def trace_explain_text(self, lang, chat_id):
        row = store.latest_trace(self.conn, chat_id, "inbound")
        if not row:
            return T(lang, "trace_none")
        action, confidence = "?", "?"
        for ev in store.trace_events(self.conn, row["trace_id"]):
            if ev["stage"] == trace.ROUTER_COMPLETED:
                action = (ev["skill"] or "?")
                try:
                    import json as _json
                    confidence = round(float((_json.loads(ev["data"]) or {}).get("confidence", 0)), 2)
                except Exception:
                    pass
        return T(lang, "trace_explain", action=action, confidence=confidence,
                 trace_id=row["trace_id"])

    def do_remember(self, chat_id, params, lang):
        value = str(params.get("value") or "").strip()
        if not value:
            self.reply(chat_id, T(lang, "clarify"))
            return
        key = str(params.get("key") or "").strip().lower()
        if key == "language":
            normalized = value.strip().lower()
            store.pref_set(self.conn, "language", "ru" if normalized.startswith("ru") else "en")
        elif key == "timezone_offset":
            try:
                store.pref_set(self.conn, "timezone_offset", int(value))
            except ValueError:
                self.reply(chat_id, T(lang, "clarify"))
                return
        elif key == "auto_calendar":
            truthy = value.strip().casefold() in ("1", "true", "yes", "да", "on")
            store.pref_set(self.conn, "auto_calendar", "true" if truthy else "false")
        elif key == "owner_name":
            store.pref_set(self.conn, "owner_name", value.strip()[:60])
        else:
            note_id = int(store.kv_get(self.conn, "note_seq", "0") or 0) + 1
            store.kv_set(self.conn, "note_seq", note_id)
            store.pref_set(self.conn, f"note:{note_id}", value)
        self.reply(chat_id, T(self.lang(), "remember_saved", value=value))

    def do_forget(self, chat_id, params, lang):
        query = str(params.get("value") or params.get("key") or "").strip().casefold()
        if not query:
            self.reply(chat_id, T(lang, "clarify"))
            return
        for row in store.pref_all(self.conn):
            if query == row["key"].casefold() or query in row["value"].casefold():
                store.pref_delete(self.conn, row["key"])
                self.reply(chat_id, T(lang, "forgotten", value=row["value"]))
                return
        self.reply(chat_id, T(lang, "forget_not_found"))

    def memory_text(self, lang):
        rows = [r for r in store.pref_all(self.conn) if not r["key"].startswith("auto_cat_declined:")]
        if not rows:
            return T(lang, "memory_empty")
        lines = [T(lang, "memory_header")]
        lines.extend(f"  {row['key']}: {row['value']}" for row in rows)
        return "\n".join(lines)

    # -- Stats / categories

    def stats_text(self, lang):
        status_rows = store.status_counts(self.conn)
        if not status_rows:
            return T(lang, "stats_empty")
        lines = [T(lang, "stats_status")]
        lines.extend(f"  {row['status']}: {row['n']}" for row in status_rows)
        category_rows = [r for r in store.category_counts(self.conn) if r["n"]]
        if category_rows:
            lines.append(T(lang, "stats_categories"))
            lines.extend(f"  {row['name']}: {row['n']}" for row in category_rows)
        return "\n".join(lines)

    def overview_text(self, lang):
        lines = [T(lang, "overview_header"), self.stats_text(lang)]
        active = store.reminders_active(self.conn, next(iter(self.cfg.allowed_chat_ids)))
        next_part = ""
        if active:
            next_part = T(lang, "overview_next",
                          when=reminders.fmt_local(active[0]["due_utc"], self.tz_offset()),
                          title=active[0]["title"][:60])
        lines.append(T(lang, "overview_reminders", n=len(active), next_part=next_part))
        memory_rows = [r for r in store.pref_all(self.conn)
                       if not r["key"].startswith("auto_cat_declined:")]
        lines.append(T(lang, "overview_memory", n=len(memory_rows)))
        lines.append(T(lang, "overview_spend",
                       day=store.usage_total(self.conn, "day"),
                       month=store.usage_total(self.conn, "month")))
        return "\n".join(lines)

    def items_text(self, lang, params):
        try:
            limit = min(int(params.get("limit") or 5), 10)
        except (TypeError, ValueError):
            limit = 5
        category = params.get("category")
        query = params.get("query")
        rows = store.list_messages(self.conn, category, query, limit)
        if not rows:
            return T(lang, "items_empty")
        filter_part = ""
        if category:
            filter_part = T(lang, "items_filter_category", category=category)
        elif query:
            filter_part = T(lang, "items_filter_query", query=query)
        lines = [T(lang, "items_header", filter=filter_part)]
        for row in rows:
            row_category = row["category"] or row["suggested_category"] or "?"
            text = (row["summary"] or row["raw_text"] or "").replace("\n", " ")[:90]
            marker = "" if row["status"] == "confirmed" else " (?)"
            lines.append(f"#{row['id']} [{row_category}{marker}] {text}")
            urls = store.message_urls(self.conn, row["id"])
            if urls:
                lines.append(f"   🔗 {urls[0]['url']}")
        return "\n".join(lines)

    def do_show_media(self, chat_id, lang, params):
        row = self.resolve_item(params)
        if row is None:
            self.reply(chat_id, T(lang, "items_empty"))
            return
        images = store.message_images(self.conn, row["id"])
        sent = 0
        for img in images:
            caption = f"#{row['id']}" if sent == 0 else None
            try:
                if img["tg_file_id"]:
                    tg_send_photo(self.cfg.token, chat_id, img["tg_file_id"], caption=caption)
                    sent += 1
                elif img["local_path"] and Path(img["local_path"]).exists():
                    tg_send_photo(self.cfg.token, chat_id,
                                  (Path(img["local_path"]).name, Path(img["local_path"]).read_bytes()),
                                  caption=caption, by_file_id=False)
                    sent += 1
            except TelegramError as exc:
                log(f"sendPhoto failed for #{row['id']}: {exc}")
        if sent == 0:
            self.reply(chat_id, T(lang, "no_media", row_id=row["id"]))

    def do_discard(self, chat_id, lang, pending):
        """Decline adding the just-suggested item: delete the fresh row."""
        if not pending or pending["kind"] != "category":
            self.reply(chat_id, T(lang, "nothing_to_discard"))
            return
        row_id = pending["payload"].get("row_id")
        store.pending_clear(self.conn, chat_id)
        if store.get_message(self.conn, row_id) is not None:
            for path in store.delete_message(self.conn, row_id):
                Path(path).unlink(missing_ok=True)
            log(f"message #{row_id} discarded (declined) by operator")
        self.reply(chat_id, T(lang, "discarded"))

    def _purge_impact_text(self, lang, info):
        ru = lang == "ru"
        labels = {
            "messages": ("сообщений" if ru else "messages"),
            "reminders": ("напоминаний" if ru else "reminders"),
            "categories": ("категорий" if ru else "categories"),
            "issues": ("записей о проблемах" if ru else "issue records"),
            "feedback": ("поправок" if ru else "corrections"),
        }
        parts = []
        for key, label in labels.items():
            n = info.get(key)
            if n:
                parts.append(f"  • {n} {label}")
        if info.get("scope") == "category" and "messages" in info:
            cat = info.get("category") or "?"
            parts = [f"  • {info['messages']} " + ("сообщений в категории «" if ru else
                     "messages in category \"") + f"{cat}»"]
        return "\n".join(parts)

    def do_purge(self, chat_id, lang, params):
        scope = str(params.get("scope") or "").strip().lower()
        if scope not in store.PURGE_SCOPES:
            self.reply(chat_id, T(lang, "clarify"))
            return
        category = params.get("category")
        info = store.purge_preview(self.conn, scope, category)
        if not any(info.get(k) for k in ("messages", "reminders", "categories", "issues", "feedback")):
            self.reply(chat_id, T(lang, "purge_nothing"))
            return
        if scope == "category":
            phrase = T(lang, "purge_phrase_category", category=category or "?")
        else:
            phrase = T(lang, f"purge_phrase_{scope}")
        store.pending_set(self.conn, chat_id, "purge",
                          {"scope": scope, "category": category, "phrase": phrase}, ttl_seconds=300)
        self.reply(chat_id, T(lang, "purge_preview",
                              impact=self._purge_impact_text(lang, info), phrase=phrase))

    def resolve_purge(self, chat_id, lang, pending, text):
        payload = pending["payload"]
        store.pending_clear(self.conn, chat_id)
        if text.strip().casefold() != str(payload.get("phrase") or "").strip().casefold():
            self.reply(chat_id, T(lang, "purge_cancelled"))
            return
        info, paths = store.purge_execute(self.conn, payload["scope"], payload.get("category"))
        for path in paths:
            Path(path).unlink(missing_ok=True)
        log(f"PURGE scope={payload['scope']} category={payload.get('category')} by operator")
        self.reply(chat_id, T(lang, "purge_done", impact=self._purge_impact_text(lang, info)))

    def do_fetch(self, chat_id, lang, params):
        if not self.cfg.fetch_enabled:
            self.reply(chat_id, T(lang, "fetch_disabled"))
            return
        url = str(params.get("url") or "").strip()
        if not url:
            self.reply(chat_id, T(lang, "fetch_no_url"))
            return
        self.reply(chat_id, T(lang, "fetch_reading"), record=False)
        try:
            final_url, title, text = fetch.fetch(
                url, timeout=self.cfg.fetch_timeout, max_bytes=self.cfg.fetch_max_bytes)
        except fetch.FetchError as exc:
            log(f"fetch failed for {url}: {exc}")
            store.issue_add(self.conn, chat_id, "fetch_failed", f"{url}: {exc}")
            key = exc.reason if exc.reason in ("fetch_blocked", "fetch_private") else "fetch_failed"
            self.reply(chat_id, T(lang, key, error=str(exc)) if key == "fetch_failed"
                       else T(lang, key))
            return
        self.ingest_fetched(chat_id, lang, final_url, title, text)

    def ingest_fetched(self, chat_id, lang, url, title, text):
        """Store fetched remote content as an inbox item and suggest a
        category — same suggest-and-confirm flow as a forwarded post."""
        from urllib.parse import urlparse
        source = title or urlparse(url).hostname or "web"
        row_id = store.insert_message(self.conn, {
            "chat_id": chat_id,
            "tg_message_id": -int(datetime.now(timezone.utc).timestamp()),  # synthetic, unique
            "forward_origin_type": "web",
            "forward_origin_title": source[:200],
            "received_at": datetime.now(timezone.utc).isoformat(),
            "raw_text": text,
        })
        if row_id is None:
            return
        store.insert_url(self.conn, row_id, url)
        suggestion = self.suggest_row(store.get_message(self.conn, row_id))
        if not suggestion:
            self.reply(chat_id, T(lang, "stored_retry", row_id=row_id))
            return
        category, alternatives, summary = suggestion
        counts = T(lang, "counts", row_id=row_id, images=0, urls=1)
        self.present_suggestion(row_id, chat_id, None, category, alternatives, summary, counts)

    def do_ask(self, chat_id, lang, params, text):
        question = str(params.get("question") or "").strip() or text.strip()
        if not question:
            self.reply(chat_id, T(lang, "clarify"))
            return
        try:
            qvec = llm.embed(self.cfg, self.conn, "ask", [question])[0]
            rows = store.all_embedded_chunks(self.conn)
            context = knowledge.rank_chunks(qvec, rows, self.cfg.ask_top_k,
                                            self.cfg.ask_context_chars)
            if not context:  # nothing indexed/matched -> keyword fallback
                context = self._keyword_context(question)
            hint = persona.boss_preference_hint(self.conn)
            answer = llm.chat_profile(self.cfg, self.conn, "ask",
                                      knowledge.build_ask_messages(question, context, hint),
                                      profile="ask_grounded")
        except llm.BudgetExceeded as exc:
            store.issue_add(self.conn, chat_id, "budget_stop", question[:200])
            self.reply(chat_id, T(lang, "budget_stop", spent=exc.spent, limit=exc.limit,
                                  period=T(lang, f"period_{exc.period}")))
            return
        except llm.LLMError as exc:
            log(f"ask failed: {exc}")
            store.issue_add(self.conn, chat_id, "llm_error", f"ask: {exc}")
            self.reply(chat_id, T(lang, "llm_error"))
            return
        if not context:
            store.issue_add(self.conn, chat_id, "ask_no_context", question[:200])
        self.reply(chat_id, answer.strip()[:4000])

    def _keyword_context(self, question):
        items = []
        for term in knowledge.salient_terms(question):
            for row in store.list_messages(self.conn, query=term, limit=3):
                if not any(c["message_id"] == row["id"] for c in items):
                    items.append({"message_id": row["id"],
                                  "text": (row["raw_text"] or row["summary"] or "")[:1500],
                                  "category": row["category"] or row["suggested_category"] or "?",
                                  "title": row["forward_origin_title"]})
            if len(items) >= self.cfg.ask_top_k:
                break
        return items[:self.cfg.ask_top_k]

    def index_message(self, row_id, text):
        """Chunk + embed a message's text for semantic recall. Best-effort:
        on LLM/budget failure the item is simply not searchable yet (keyword
        fallback still covers it)."""
        pieces = knowledge.chunk_text(text, self.cfg.chunk_chars)
        if not pieces:
            return
        try:
            vectors = llm.embed(self.cfg, self.conn, "ask", pieces)
        except llm.LLMError as exc:
            log(f"indexing skipped for #{row_id}: {exc}")
            return
        store.set_chunks(self.conn, row_id, list(zip(pieces, vectors)))
        log(f"indexed #{row_id} ({len(pieces)} chunk(s))")

    def media_bytes(self):
        total = 0
        try:
            for path in self.cfg.media_dir.glob("*"):
                if path.is_file():
                    total += path.stat().st_size
        except OSError:
            pass
        return total

    def resolve_item(self, params):
        """Resolve an item by explicit id, query/category, or most recent."""
        try:
            rid = int(params.get("id")) if params.get("id") is not None else None
        except (TypeError, ValueError):
            rid = None
        if rid is not None:
            row = store.get_message(self.conn, rid)
            if row is not None:
                return row
        rows = store.list_messages(self.conn, params.get("category"), params.get("query"), limit=1)
        return rows[0] if rows else None

    def item_detail_text(self, lang, params):
        ru = lang == "ru"
        row = self.resolve_item(params)
        if row is None:
            return T(lang, "items_empty")
        category = row["category"] or row["suggested_category"] or "?"
        lines = [f"#{row['id']} [{category}]"]
        post_date = row["forward_date"] or row["tg_date"]
        if post_date:
            local = datetime.fromtimestamp(int(post_date), tz=timezone.utc) \
                + timedelta(hours=self.tz_offset())
            lines.append(("Дата поста: " if ru else "Posted: ") + local.strftime("%Y-%m-%d %H:%M"))
        lines.append(("Сохранено: " if ru else "Saved: ") + row["received_at"][:16])
        if row["forward_origin_title"]:
            lines.append(("Источник: " if ru else "Source: ") + row["forward_origin_title"])
        post_link = ingest.source_link(
            row["forward_origin_username"], row["forward_origin_chat_id"],
            row["forward_origin_message_id"],
        )
        if post_link:
            lines.append(("Пост: " if ru else "Post: ") + post_link)
        if row["summary"]:
            lines.append(row["summary"][:500])
        facts = store.message_facts(self.conn, row["id"])
        if facts:
            lines.append("Ключевые факты:" if ru else "Key facts:")
            lines.extend(f"• {r['fact']}" for r in facts)
        urls = store.message_urls(self.conn, row["id"])
        if urls:
            lines.append("Ссылки:" if ru else "Links:")
            lines.extend(r["url"] for r in urls[:10])
        images = store.message_images(self.conn, row["id"])
        if images:
            lines.append(("Фото: " if ru else "Photos: ") + str(len(images)))
        return "\n".join(lines)

    def issues_text(self, lang, period=None):
        period = str(period or "week").strip().lower()
        days = {"day": 1, "week": 7, "month": 30}.get(period, 7)
        period_key = {1: "period_day", 7: "period_week", 30: "period_month"}[days]
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        counts = store.issue_counts(self.conn, since)
        period_label = T(lang, period_key)
        if not counts:
            return T(lang, "issues_empty", period=period_label)
        lines = [T(lang, "issues_header", period=period_label)]
        import texts as texts_module
        for row in counts:
            entry = texts_module.TEXTS.get(f"issue_kind_{row['kind']}")
            label = (entry.get(lang) or entry["en"]) if entry else row["kind"]
            lines.append(f"  {label}: {row['n']}")
        lines.append(T(lang, "issues_examples"))
        for row in store.issues_recent(self.conn, since):
            detail = (row["detail"] or "").replace("\n", " ")[:80]
            lines.append(f"  {row['ts'][:16]} [{row['kind']}] {detail}")
        return "\n".join(lines)

    def check_daily_curator(self):
        """Enqueue the memory-curator job once per UTC day (runs via the job
        runner on the next sweep). No proactive message — the boss pulls the
        results with memory_review."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if store.kv_get(self.conn, "curator_day") == today:
            return
        store.kv_set(self.conn, "curator_day", today)
        if not jobs.has_pending(self.conn, "memory_curator", "run_memory_curator"):
            jobs.add_job(self.conn, "memory_curator", "run_memory_curator",
                         trace_id=current_trace())

    def check_weekly_issues_report(self):
        now = datetime.now(timezone.utc)
        last = store.kv_get(self.conn, "last_issues_report")
        if not last:
            store.kv_set(self.conn, "last_issues_report", now.isoformat())
            return
        if (now - datetime.fromisoformat(last)).days < 7:
            return
        store.kv_set(self.conn, "last_issues_report", now.isoformat())
        lang = self.lang()
        report = review.chat_text(self.conn, self.cfg, lang, "week")
        for chat_id in self.cfg.allowed_chat_ids:
            self.reply(chat_id, T(lang, "review_weekly_intro", name=self.owner_name())
                       + "\n" + report)

    def do_review(self, chat_id, lang, params):
        period = review.normalize_period(params.get("period"))
        self.reply(chat_id, review.chat_text(self.conn, self.cfg, lang, period))
        if not params.get("export"):
            return
        md = review.markdown(self.conn, self.cfg, period)
        reviews_dir = self.cfg.db_path.parent / "reviews"
        reviews_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
        filename = f"cara-review-{period}-{stamp}.md"
        (reviews_dir / filename).write_text(md, encoding="utf-8")
        try:
            tg_send_document(self.cfg.token, chat_id, filename, md.encode("utf-8"),
                             caption=T(lang, "review_file_caption"),
                             content_type="text/markdown")
            store.convo_add(self.conn, chat_id, "bot", f"[review file: {filename}]")
        except TelegramError as exc:
            log(f"review export send failed: {exc}")
            self.reply(chat_id, T(lang, "llm_error"))
        log(f"review exported: {reviews_dir / filename}")

    def categories_text(self, lang):
        rows = store.category_counts(self.conn)
        if not rows:
            return T(lang, "no_categories")
        lines = [T(lang, "categories_header")]
        lines.extend(f"  {row['name']}: {row['n']}" for row in rows)
        return "\n".join(lines)

    # -- Buttons (fallback confirmation path)

    def handle_memory_callback(self, callback_id, chat_id, msg, data):
        lang = self.lang()
        parts = data.split("|")
        try:
            cand_id, accept = int(parts[1]), parts[2] == "y"
        except (IndexError, ValueError):
            self.answer_callback(callback_id, "?")
            return
        value, accepted = memory_curator.confirm_candidate(self.conn, cand_id, accept)
        if value is None:
            self.answer_callback(callback_id, "—")
            return
        key = "memory_candidate_kept" if accepted else "memory_candidate_skipped"
        self.answer_callback(callback_id, T(lang, key))
        if msg.get("message_id"):
            try:
                tg_call(self.cfg.token, "editMessageText", {
                    "chat_id": chat_id, "message_id": msg["message_id"],
                    "text": f"{value}\n— {T(lang, key)}"})
            except TelegramError as exc:
                log(f"editMessageText (mc) failed: {exc}")

    def handle_callback(self, callback):
        callback_id = callback.get("id")
        msg = callback.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        from_id = (callback.get("from") or {}).get("id")
        if chat_id not in self.cfg.allowed_chat_ids and from_id not in self.cfg.allowed_chat_ids:
            self.answer_callback(callback_id, "Not allowed.")
            return
        data = callback.get("data") or ""
        if data.startswith("mc|"):
            self.handle_memory_callback(callback_id, chat_id, msg, data)
            return
        parsed = ingest.parse_callback_data(callback.get("data"))
        if not parsed:
            self.answer_callback(callback_id, "Unknown action.")
            return
        kind, row_id, name = parsed
        row = store.get_message(self.conn, row_id)
        if not row:
            self.answer_callback(callback_id, "Unknown message.")
            return
        if row["status"] == "confirmed":
            self.answer_callback(callback_id, f"OK: {row['category']}")
            return
        category = name if kind == "named" else (row["suggested_category"] or self.cfg.fallback_category)
        pending = store.pending_get(self.conn, chat_id)
        if pending and pending["kind"] == "category" and pending["payload"].get("row_id") == row_id:
            store.pending_clear(self.conn, chat_id)
        self.answer_callback(callback_id, category)
        self.apply_category_confirm(
            chat_id, row, category, reply_to=None,
            edit_message_id=msg.get("message_id") or row["suggestion_message_id"],
            quiet=True,
        )

    def handle_correction(self, row, chat_id, text, reply_to):
        lang = self.lang()
        if row["status"] == "confirmed":
            self.reply(chat_id, T(lang, "already_confirmed", row_id=row["id"], category=row["category"]))
            return
        category = llm.normalize_category(text)
        if not category:
            return
        pending = store.pending_get(self.conn, chat_id)
        if pending and pending["kind"] == "category" and pending["payload"].get("row_id") == row["id"]:
            store.pending_clear(self.conn, chat_id)
        self.apply_category_confirm(chat_id, row, category, reply_to=reply_to)

    # -- Ingest flow

    def apply_category_confirm(self, chat_id, row, category, reply_to,
                               edit_message_id=None, quiet=False):
        lang = self.lang()
        canonical = store.ensure_category(self.conn, category)
        store.confirm_category(self.conn, row["id"], canonical)
        if row["suggested_category"] and canonical.casefold() != row["suggested_category"].casefold():
            store.feedback_add(
                self.conn, "ingest", (row["raw_text"] or "")[:100],
                row["suggested_category"], canonical,
            )
        log(f"message #{row['id']} confirmed as {canonical}")
        relationship.log_event(self.conn, "category_confirmed",
                               f"filed a message as «{canonical}»", importance=1,
                               source_table="messages", source_id=row["id"])
        updated = store.get_message(self.conn, row["id"])
        self.edit_suggestion_message(
            chat_id, edit_message_id or row["suggestion_message_id"], updated
        )
        if not quiet:
            self.reply(chat_id, T(lang, "confirmed", category=canonical, row_id=row["id"]), reply_to)
        self.maybe_propose_habit(chat_id, updated, lang)

    def maybe_propose_habit(self, chat_id, row, lang):
        source = row["forward_origin_chat_id"]
        if source is None:
            return
        if store.pref_get(self.conn, f"auto_cat:{source}"):
            return
        if store.pref_get(self.conn, f"auto_cat_declined:{source}"):
            return
        if store.pending_get(self.conn, chat_id):
            return
        category, streak = store.habit_streak(self.conn, source)
        if category and streak >= self.cfg.habit_threshold:
            store.pending_set(self.conn, chat_id, "habit", {
                "source_chat_id": source,
                "source_title": row["forward_origin_title"],
                "category": category,
            })
            self.reply(chat_id, T(lang, "habit_proposal", n=streak,
                                  source=row["forward_origin_title"] or source,
                                  category=category))

    def flush_albums(self, now, force=False):
        for group_id in list(self.albums):
            buffer = self.albums[group_id]
            if force or buffer.get("deadline", 0) <= now:
                del self.albums[group_id]
                parts = sorted(buffer["parts"], key=lambda m: m.get("message_id", 0))
                try:
                    self.finalize(parts)
                except Exception as exc:
                    log(f"error finalizing album {group_id}: {exc!r}")

    TEXT_DOC_EXTS = (".md", ".markdown", ".txt", ".text")
    MAX_DOC_CHARS = 100_000

    def read_text_document(self, parts):
        """Download & decode a text/markdown document part (a sent plan).
        Returns (text, filename) or (None, None)."""
        for part in parts:
            doc = part.get("document") or {}
            fname = doc.get("file_name") or ""
            mime = str(doc.get("mime_type") or "")
            is_text = (mime.startswith("text/") or mime in ("application/markdown",)
                       or fname.lower().endswith(self.TEXT_DOC_EXTS))
            if not (doc.get("file_id") and is_text):
                continue
            try:
                path = self.download_file(doc["file_id"], doc["file_unique_id"],
                                          Path(fname).suffix or ".txt")
                text = Path(path).read_text(encoding="utf-8", errors="replace")[:self.MAX_DOC_CHARS]
                Path(path).unlink(missing_ok=True)  # transient artifact
                if text.strip():
                    return text, fname
            except (TelegramError, OSError) as exc:
                log(f"text document read failed: {exc}")
        return None, None

    def finalize(self, parts):
        lang = self.lang()
        first = parts[0]
        chat_id = first["chat"]["id"]
        reply_to = first.get("message_id")
        doc_text, doc_name = self.read_text_document(parts)
        raw_text = doc_text or ingest.first_text(parts)
        urls = ingest.collect_urls(parts)
        forward = ingest.parse_forward_origin(first.get("forward_origin"))
        title = forward.get("title") or (doc_name if doc_text else None)
        row_id = store.insert_message(
            self.conn,
            {
                "chat_id": chat_id,
                "tg_message_id": first.get("message_id"),
                "media_group_id": first.get("media_group_id"),
                "from_user_id": (first.get("from") or {}).get("id"),
                "forward_origin_type": forward.get("type") or ("document" if doc_text else None),
                "forward_origin_chat_id": forward.get("chat_id"),
                "forward_origin_title": title,
                "forward_origin_username": forward.get("username"),
                "forward_origin_message_id": forward.get("message_id"),
                "forward_date": forward.get("date"),
                "received_at": datetime.now(timezone.utc).isoformat(),
                "tg_date": first.get("date"),
                "raw_text": raw_text,
            },
        )
        if row_id is None:
            log(f"skipping redelivered message chat_id={chat_id} message_id={first.get('message_id')}")
            return
        for url in urls:
            store.insert_url(self.conn, row_id, url)
        image_count = 0
        for part in parts:
            photo_sizes = part.get("photo") or []
            if photo_sizes:
                largest = photo_sizes[-1]  # Telegram orders PhotoSize ascending
                try:
                    local_path = self.download_file(
                        largest.get("file_id"), largest.get("file_unique_id"), ".jpg"
                    )
                except TelegramError as exc:
                    log(f"photo download failed for message #{row_id}: {exc}")
                    local_path = None
                store.insert_image(self.conn, row_id, part.get("message_id"), largest, local_path)
                image_count += 1
                continue
            document = part.get("document") or {}
            if str(document.get("mime_type") or "").startswith("image/"):
                # v1 limitation: uncompressed image documents are stored as
                # metadata only and not sent to the LLM.
                log(f"image document stored metadata-only for message #{row_id}")
                store.insert_image(self.conn, row_id, part.get("message_id"), document, None)
        if image_count:
            storage.offload(self.cfg, self.conn, row_id)  # durable copy (dormant on local backend)
        log(
            f"stored message #{row_id} (chat={chat_id}, images={image_count}, urls={len(urls)}, "
            f"forward={forward.get('title') or '-'})"
        )
        if forward.get("chat_id") is not None and forward.get("message_id") is not None:
            original = store.find_forward_duplicate(
                self.conn, forward["chat_id"], forward["message_id"], row_id
            )
            if original:
                store.mark_duplicate(self.conn, row_id, original)
                log(f"message #{row_id} is a duplicate of #{original['id']}, skipping LLM")
                if original["status"] == "confirmed":
                    detail = T(lang, "dup_confirmed", category=original["category"])
                elif original["suggested_category"]:
                    detail = T(lang, "dup_suggested", category=original["suggested_category"])
                else:
                    detail = T(lang, "dup_pending")
                self.reply(chat_id, T(lang, "duplicate", original_id=original["id"], detail=detail),
                           reply_to)
                return
        row = store.get_message(self.conn, row_id)
        suggestion = self.suggest_row(row)
        if not suggestion:
            self.reply(chat_id, T(lang, "stored_retry", row_id=row_id), reply_to)
            return
        category, alternatives, summary = suggestion
        # Learned habit: auto-confirm posts from sources you always file the same way.
        auto_category = (store.pref_get(self.conn, f"auto_cat:{forward['chat_id']}")
                         if forward.get("chat_id") is not None else None)
        if auto_category:
            store.confirm_category(self.conn, row_id, store.ensure_category(self.conn, auto_category))
            self.reply(chat_id, T(lang, "auto_confirmed", category=auto_category,
                                  row_id=row_id, summary=summary[:300]), reply_to)
            return
        counts = T(lang, "counts", row_id=row_id, images=image_count, urls=len(urls))
        self.present_suggestion(row_id, chat_id, reply_to, category, alternatives, summary, counts)

    def suggest_row(self, row):
        """Get an LLM suggestion for a stored row; returns (category,
        alternatives, summary) or None when the LLM call failed."""
        row_id = row["id"]
        urls = [r["url"] for r in store.message_urls(self.conn, row_id)]
        image_paths = [r["local_path"] for r in store.message_images(self.conn, row_id)
                       if r["local_path"]]
        known = store.known_categories(self.conn)
        if not (row["raw_text"] or urls or image_paths):
            category, alternatives, summary, facts = (
                self.cfg.fallback_category, [], "(no analyzable content)", []
            )
        else:
            text_block = ingest.build_text_block(
                row["raw_text"], row["forward_origin_type"], row["forward_origin_title"], urls
            )
            try:
                category, alternatives, summary, facts = ingest.suggest(
                    self.cfg, self.conn, known, text_block, image_paths
                )
            except llm.LLMError as exc:
                attempts = store.bump_attempts(self.conn, row_id)
                if attempts >= self.cfg.llm_max_attempts:
                    store.mark_failed(self.conn, row_id)
                    store.issue_add(self.conn, row["chat_id"], "ingest_failed",
                                    f"#{row_id}: {exc}")
                    log(f"message #{row_id} marked failed after {attempts} attempts: {exc}")
                else:
                    log(f"suggestion failed for message #{row_id} (attempt {attempts}): {exc}")
                return None
        store.set_suggestion(self.conn, row_id, category, summary, self.cfg.do_model)
        store.set_facts(self.conn, row_id, facts)
        # Index for semantic recall: full text for documents, else summary+facts.
        index_text = row["raw_text"] or summary
        if facts:
            index_text = (index_text or "") + "\n" + "\n".join(facts)
        if index_text:
            self.index_message(row_id, index_text)
        log(f"suggested {category} for message #{row_id} ({len(facts)} facts)")
        return category, alternatives, summary

    def present_suggestion(self, row_id, chat_id, reply_to, category, alternatives, summary, counts):
        lang = self.lang()
        keyboard = ingest.build_suggestion_keyboard(row_id, category, alternatives)
        result = self.reply(
            chat_id,
            T(lang, "suggestion", category=category, summary=summary[:500], counts=counts),
            reply_to,
            reply_markup={"inline_keyboard": keyboard},
        )
        if result and result.get("message_id"):
            store.set_suggestion_message(self.conn, row_id, result["message_id"])
        store.pending_set(self.conn, chat_id, "category", {"row_id": row_id})


def main():
    cfg = load_config()
    agent = Agent(cfg)
    signal.signal(signal.SIGTERM, agent.request_stop)
    signal.signal(signal.SIGINT, agent.request_stop)
    agent.run()


if __name__ == "__main__":
    main()
