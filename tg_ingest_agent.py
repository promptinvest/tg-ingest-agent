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

import gcal
import ingest
import llm
import reminders
import router
import spend
import store
from common import Config, load_config, log  # noqa: F401 (Config re-exported for tests)
from tg_api import TelegramError, tg_call, tg_download, tg_send_document
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
        self.albums = {}  # media_group_id -> {"parts": [...], "deadline": float}
        self.stop = False
        self.last_sweep = 0.0

    def request_stop(self, signum, _frame):
        log(f"received signal {signum}, shutting down")
        self.stop = True

    # -- preferences-backed settings

    def lang(self):
        return store.pref_get(self.conn, "language", self.cfg.language)

    def tz_offset(self):
        try:
            return int(store.pref_get(self.conn, "timezone_offset", self.cfg.timezone_offset))
        except (TypeError, ValueError):
            return self.cfg.timezone_offset

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
            if now - self.last_sweep >= self.cfg.retry_interval:
                self.last_sweep = now
                self.retry_sweep()
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
            for update in updates or []:
                try:
                    self.handle_update(update)
                except Exception as exc:  # never let one bad update kill the loop
                    log(f"error handling update {update.get('update_id')}: {exc!r}")
            if updates:
                offset = max(u["update_id"] for u in updates) + 1
                store.kv_set(self.conn, "offset", offset)
        self.flush_albums(time.time(), force=True)
        log("stopped")

    # -- Scheduler ticks

    def fire_due_reminders(self):
        now_iso = datetime.now(timezone.utc).isoformat()
        for row in store.reminders_due(self.conn, now_iso):
            lang = self.lang()
            self.reply(row["chat_id"], T(lang, "reminder_fired", title=row["title"]))
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
        try:
            path = self.download_file(voice.get("file_id"), voice.get("file_unique_id"), ".oga")
            transcript = llm.transcribe(
                self.cfg, self.conn, "stt", path, int(voice.get("duration") or 0)
            )
        except (TelegramError, llm.LLMError) as exc:
            log(f"voice transcription failed: {exc}")
            self.reply(chat_id, T(lang, "stt_failed"))
            return None
        if not transcript:
            self.reply(chat_id, T(lang, "stt_failed"))
            return None
        return transcript

    # -- Router dispatch

    def dispatch(self, chat_id, msg, text):
        lang = self.lang()
        pending = store.pending_get(self.conn, chat_id)
        try:
            decision = router.route(self.cfg, self.conn, chat_id, text, pending)
        except llm.BudgetExceeded as exc:
            self.reply(chat_id, T(lang, "budget_stop", spent=exc.spent, limit=exc.limit,
                                  period=T(lang, f"period_{exc.period}")))
            return
        except llm.LLMError as exc:
            log(f"router failed: {exc}")
            self.reply(chat_id, T(lang, "llm_error"))
            return
        action, params = decision["action"], decision["params"]
        log(f"routed chat={chat_id} action={action} confidence={decision['confidence']:.2f}")

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
            self.reply(chat_id, T(lang, "capabilities"))
        elif action == "overview":
            self.reply(chat_id, self.overview_text(lang))
        elif action == "list_items":
            self.reply(chat_id, self.items_text(lang, params))
        elif action == "memory":
            self.reply(chat_id, self.memory_text(lang))
        elif action == "remember":
            self.do_remember(chat_id, params, lang)
        elif action == "forget":
            self.do_forget(chat_id, params, lang)
        elif action in ("confirm", "amend", "cancel"):
            self.resolve_pending(chat_id, action, params, pending, lang)
        elif action == "clarify":
            question = str(params.get("question") or "").strip()
            self.reply(chat_id, question[:300] if question else T(lang, "clarify"))
        else:
            self.reply(chat_id, T(lang, "out_of_scope"))

    def handle_command(self, chat_id, name):
        lang = self.lang()
        if name == "start":
            self.reply(chat_id, T(lang, "start"))
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
            self.reply(chat_id, T(lang, "calendar_failed", error=str(exc)[:200]))

    # -- Memory skill

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
        return "\n".join(lines)

    def categories_text(self, lang):
        rows = store.category_counts(self.conn)
        if not rows:
            return T(lang, "no_categories")
        lines = [T(lang, "categories_header")]
        lines.extend(f"  {row['name']}: {row['n']}" for row in rows)
        return "\n".join(lines)

    # -- Buttons (fallback confirmation path)

    def handle_callback(self, callback):
        callback_id = callback.get("id")
        msg = callback.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        from_id = (callback.get("from") or {}).get("id")
        if chat_id not in self.cfg.allowed_chat_ids and from_id not in self.cfg.allowed_chat_ids:
            self.answer_callback(callback_id, "Not allowed.")
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

    def finalize(self, parts):
        lang = self.lang()
        first = parts[0]
        chat_id = first["chat"]["id"]
        reply_to = first.get("message_id")
        raw_text = ingest.first_text(parts)
        urls = ingest.collect_urls(parts)
        forward = ingest.parse_forward_origin(first.get("forward_origin"))
        row_id = store.insert_message(
            self.conn,
            {
                "chat_id": chat_id,
                "tg_message_id": first.get("message_id"),
                "media_group_id": first.get("media_group_id"),
                "from_user_id": (first.get("from") or {}).get("id"),
                "forward_origin_type": forward.get("type"),
                "forward_origin_chat_id": forward.get("chat_id"),
                "forward_origin_title": forward.get("title"),
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
            category, alternatives, summary = self.cfg.fallback_category, [], "(no analyzable content)"
        else:
            text_block = ingest.build_text_block(
                row["raw_text"], row["forward_origin_type"], row["forward_origin_title"], urls
            )
            try:
                category, alternatives, summary = ingest.suggest(
                    self.cfg, self.conn, known, text_block, image_paths
                )
            except llm.LLMError as exc:
                attempts = store.bump_attempts(self.conn, row_id)
                if attempts >= self.cfg.llm_max_attempts:
                    store.mark_failed(self.conn, row_id)
                    log(f"message #{row_id} marked failed after {attempts} attempts: {exc}")
                else:
                    log(f"suggestion failed for message #{row_id} (attempt {attempts}): {exc}")
                return None
        store.set_suggestion(self.conn, row_id, category, summary, self.cfg.do_model)
        log(f"suggested {category} for message #{row_id}")
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
