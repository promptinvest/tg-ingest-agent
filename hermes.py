#!/usr/bin/env python3
"""Hermes — Cara's business subsystem (in-process).

Cara is one person with two registers. HERMES is her *businesslike* side: the
sharp, efficient assistant mode that runs the work — reminders, notes, files, the
knowledge base, spend, reviews. It is NOT a separate agent, bot, process or memory;
it's a bounded domain (ACTIONS), a distinct VOICE (PERSONA), and the business
handler logic (HermesMixin) — all governed by the one Cara. The companion
(warm/personal/intimate) side stays in `converse.py` / the Agent.

HermesMixin is mixed into the Agent (`class Agent(hermes.HermesMixin)`), so its
methods run on the SAME object: `self` is the Agent, `self.reply` / `self.conn` /
`self.reminder_no` resolve exactly as before. This physically separates the
business code into its own labelled module with ZERO behaviour change — pure
relocation; the router is still the single delegation hop (business -> these
handlers, personal -> the companion).
"""
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import fetch
import ingest
import llm
import proactive
import reminders
import relationship
import review
import store
from common import log
from texts import T
from tg_api import TelegramError, tg_send_document
# NOTE: `knowledge` and `persona` are imported LOCALLY inside do_ask/_keyword_context —
# `knowledge` imports `hermes` (for the Hermes register), so a top-level import here would
# be a cycle. Call-time local imports keep it clean.

# The Hermes domain — the business/assistant actions Cara delegates to her work
# register. Routing one of these means "he's working": it sets the businesslike
# resting tone (see Agent._register_state) and is answered in the Hermes voice.
ACTIONS = frozenset({
    "ingest", "reminder_create", "reminder_list", "reminder_cancel",
    "reminder_reschedule", "reminder_rename", "reminder_undo", "list_files",
    "calendar_add", "spend", "budget_set", "stats", "categories", "overview",
    "list_items", "item_detail", "item_delete", "recategorize", "merge_categories",
    "show_media", "vps_stats", "purge", "fetch", "ask", "issues_report",
    "report_problem", "multi_action", "set_journal", "journal_show", "export",
    "working_history", "review",
})

# The Hermes register — the system-prompt persona for any LLM-generated BUSINESS
# reply (the KB `ask`, a fetched-page summary, a review/brief, working history).
# Businesslike Cara: crisp and structured, never flirty/intimate/chatty here — but
# still HER human voice on «ты», never a cold "I'm an AI assistant" disclaimer.
PERSONA = (
    "You are operating as HERMES — Cara's businesslike side: her sharp, efficient work "
    "mode for getting things done. It's the SAME person (you are still Cara, a real woman "
    "on «ты»), just her work register. In this mode:\n"
    "- Be CRISP, structured and factual — lead with the answer, keep it brief and concrete "
    "(the specific fact, date, time, number, name); use a short clean list when it helps.\n"
    "- Stay professional and warm, but NOT flirty, intimate, playful or chatty here — no "
    "endearments, no roleplay, no sharing your own day/feelings; that's for off the clock.\n"
    "- Never call yourself an AI, a bot, a program or 'твой помощник'/'assistant', and never "
    "add disclaimers about how you work — just do the task and report the result plainly.\n"
    "- Answer in the SAME language he used."
)


def is_business(action):
    """True if `action` belongs to the Hermes (business) domain."""
    return action in ACTIONS


class HermesMixin:
    """Cara's business handlers (notes/inbox, journals, KB/fetch, spend/review/export,
    problem log). The reminder subsystem lives in its own module now
    (`reminders_svc.ReminderMixin`). Mixed into the Agent, so `self` is the Agent — every
    `self.reply`/`self.conn`/`self.reminder_no`/`self.tz_offset()` resolves on it exactly as
    before. Pure relocation, no behaviour change."""

    # Bare triggers ("запиши в проблемы") carry NO problem of their own — the problem
    # is whatever he just said. Detecting one means we pull the preceding turn as the body.
    _REPORT_TRIGGERS = (
        "запиши в проблем", "добавь в проблем", "запиши проблем", "в проблемы",
        "добавь в ошибк", "запиши в ошибк", "это ошибк", "была ошибка", "запиши ошибк",
        "log this as a problem", "log as a problem", "report a problem", "add to issues",
        "log this issue", "note this problem",
    )

    def do_report_problem(self, chat_id, lang, params, text):
        """Record a boss-reported problem ('запиши в проблемы', 'добавь в
        ошибки') in the issues log so it surfaces in the weekly review —
        distinct from issues_report, which only shows the report. A bare trigger
        ('запиши в проблемы') has no content of its own — the problem is what he
        said just before — so we capture the preceding turn as the body instead
        of logging the command back to itself."""
        detail = str(params.get("detail") or "").strip()
        cur = (text or "").strip()
        low = detail.casefold() or cur.casefold()
        # The router often echoes the trigger (or a stale example) as 'detail'; treat any
        # trigger-only detail as empty and reach back into the conversation for the real issue.
        if not detail or any(t in low for t in self._REPORT_TRIGGERS):
            prior = [r["text"].strip() for r in store.convo_recent(self.conn, chat_id, limit=8)
                     if r["role"] == "user" and r["text"].strip()
                     and r["text"].strip().casefold() != cur.casefold()
                     and not any(t in r["text"].strip().casefold() for t in self._REPORT_TRIGGERS)]
            context = prior[-1] if prior else ""
            detail = (f"{cur} | {context}" if context else cur)
        store.issue_add(self.conn, chat_id, "boss_reported", detail[:500])
        self.reply(chat_id, T(lang, "problem_logged"))

    def do_set_journal(self, chat_id, lang, params):
        """Mark a category as a long-term journal (append-only, recalled as a
        dated series, spared by 'clear all notes') or back to a one-time one."""
        name = str(params.get("category") or "").strip()
        if not name:
            self.reply(chat_id, T(lang, "journal_which"))
            return
        on = params.get("on")
        on = True if on is None else bool(on)
        canonical = store.set_category_kind(self.conn, name, "journal" if on else "inbox")
        self.reply(chat_id, T(lang, "journal_marked" if on else "journal_unmarked",
                              category=canonical))

    def _journal_since(self, period):
        """ISO-UTC lower bound for a journal recall period; None = all time."""
        now = datetime.now(timezone.utc)
        if period == "day":
            d = (now + timedelta(hours=self.tz_offset())).date()
            start_local = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
            return (start_local - timedelta(hours=self.tz_offset())).isoformat()
        if period == "week":
            return (now - timedelta(days=7)).isoformat()
        if period == "month":
            return (now - timedelta(days=30)).isoformat()
        return None

    def do_journal_show(self, chat_id, lang, params):
        """Recall a journal as a dated series (entries grouped by day)."""
        ru = lang == "ru"
        name = str(params.get("category") or "").strip()
        journals = store.journal_categories(self.conn)
        if not name and len(journals) == 1:
            name = journals[0]
        if not name:
            hint = ("\n" + ", ".join(journals)) if journals else ""
            self.reply(chat_id, T(lang, "journal_which") + hint)
            return
        canonical = store.ensure_category(self.conn, name)
        period = str(params.get("period") or "").strip().lower() or "month"
        if period not in ("day", "week", "month", "all"):
            period = "month"
        entries = store.journal_entries(self.conn, canonical, self._journal_since(period))
        if not entries:
            self.reply(chat_id, T(lang, "journal_empty", category=canonical))
            return
        plabel = {"day": ("за сегодня", "today"), "week": ("за неделю", "this week"),
                  "month": ("за месяц", "this month"),
                  "all": ("за всё время", "all time")}[period][0 if ru else 1]
        lines = [T(lang, "journal_header", category=canonical, n=len(entries),
                   period=plabel, total=store.journal_count(self.conn, canonical))]
        last_day = None
        for e in entries:
            day = self._fmt_iso_local(e["received_at"]).split(",")[0]
            if day != last_day:
                lines.append(f"\n📅 {day}")
                last_day = day
            body = (e["summary"] or e["raw_text"] or "").strip()
            snippet = body.splitlines()[0][:120] if body else "—"
            lines.append(f"  • {snippet}")
        self.reply_chunks(chat_id, "\n".join(lines))

    # -- notes / inbox (stage 2) ----------------------------------------------

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
        ru = lang == "ru"
        filter_part = ""
        if category:
            filter_part = T(lang, "items_filter_category", category=category)
        elif query:
            filter_part = T(lang, "items_filter_query", query=query)
        blocks = [T(lang, "items_header", filter=filter_part, n=len(rows))]
        for row in rows:
            blocks.append(self._note_line(lang, row))
        blocks.append(T(lang, "items_footer"))
        return "\n\n".join(blocks)

    NOTES_PAGE_SIZE = 8

    def _note_line(self, lang, row):
        """One note's compact block for a list: '📄 #N · category' (N = stable note number),
        a preview, and any attachment/url marks. Shared by the plain list and the paginated view."""
        ru = lang == "ru"
        row_category = row["category"] or row["suggested_category"] or (
            "без категории" if ru else "uncategorized")
        pending = " ⏳" if row["status"] != "confirmed" else ""
        item = [f"📄 #{self.note_no(row['id'])} · {row_category}{pending}"]
        text = (row["summary"] or row["raw_text"] or "").replace("\n", " ").strip()[:110]
        if text:
            item.append(f"   {text}")
        marks = []
        files = store.message_files(self.conn, row["id"])
        if files:
            marks.append("📎 " + ", ".join(f["file_name"] or ("файл" if ru else "file")
                                           for f in files[:2]))
        images = store.message_images(self.conn, row["id"])
        if images:
            marks.append(f"🖼 {len(images)}")
        urls = store.message_urls(self.conn, row["id"])
        if urls:
            marks.append(f"🌐 {urls[0]['url']}")
        if marks:
            item.append("   " + " · ".join(marks))
        return "\n".join(item)

    def _notes_page(self, lang, category, query, offset, token):
        """Render one page of notes + its ◀/▶ keyboard. Returns (text, keyboard, total)."""
        rows, total = store.list_messages_page(self.conn, category, query, offset,
                                               self.NOTES_PAGE_SIZE)
        filter_part = ""
        if category:
            filter_part = T(lang, "items_filter_category", category=category)
        elif query:
            filter_part = T(lang, "items_filter_query", query=query)
        start = offset + 1 if rows else 0
        blocks = [T(lang, "notes_page_header", filter=filter_part,
                    start=start, end=offset + len(rows), total=total)]
        for row in rows:
            blocks.append(self._note_line(lang, row))
        return "\n\n".join(blocks), self._notes_page_keyboard(lang, token, offset, total), total

    def _notes_page_keyboard(self, lang, token, offset, total):
        """A ◀ Back · X/Y · Next ▶ row — or None when it all fits on one page."""
        pages = max(1, (total + self.NOTES_PAGE_SIZE - 1) // self.NOTES_PAGE_SIZE)
        if pages <= 1:
            return None
        cur = offset // self.NOTES_PAGE_SIZE
        buttons = []
        if cur > 0:
            buttons.append({"text": T(lang, "page_prev"), "callback_data": f"pg|{token}|{cur - 1}"})
        buttons.append({"text": f"{cur + 1}/{pages}", "callback_data": f"pg|{token}|noop"})
        if cur < pages - 1:
            buttons.append({"text": T(lang, "page_next"), "callback_data": f"pg|{token}|{cur + 1}"})
        return {"inline_keyboard": [buttons]}

    def do_list_items(self, chat_id, lang, params):
        """Browse saved notes with inline ◀/▶ pagination (edits one message in place instead
        of flooding the chat or capping the list at 10)."""
        category, query = params.get("category"), params.get("query")
        _, total = store.list_messages_page(self.conn, category, query, 0, self.NOTES_PAGE_SIZE)
        if total == 0:
            self.reply(chat_id, T(lang, "items_empty"))
            return
        store.list_views_prune(self.conn,
                               (datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
        token = store.list_view_add(self.conn, chat_id, {"category": category, "query": query})
        text, keyboard, _ = self._notes_page(lang, category, query, 0, token)
        self.reply(chat_id, text, reply_markup=keyboard)

    def do_show_media(self, chat_id, lang, params):
        row = self.resolve_item(params)
        if row is None:
            self.reply(chat_id, T(lang, "items_empty"))
            return
        if self.send_attachments(chat_id, row) == 0:
            self.reply(chat_id, T(lang, "no_media", row_id=self.note_no(row["id"])))

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

    def resolve_items(self, params):
        """Resolve one or more items: an explicit ids list, a count of most
        recent ('удали 7 сообщений'), or a single id/query/category. Returns a
        list of message rows (possibly empty)."""
        ids = params.get("ids")
        if isinstance(ids, list) and ids:
            out = []
            for i in ids:
                row = store.message_by_note_no(self.conn, i)  # stable #N
                if row is not None:
                    out.append(row)
            if out:
                return out
        count = params.get("count")
        if count is not None:
            try:
                n = max(1, min(int(count), 20))
            except (TypeError, ValueError):
                n = 0
            if n:
                return store.list_messages(self.conn, limit=n)
        row = self.resolve_item(params)
        return [row] if row else []

    def resolve_item(self, params):
        """Resolve an item by its stable note number (#N), query/category, or most recent."""
        try:
            no = int(params.get("id")) if params.get("id") is not None else None
        except (TypeError, ValueError):
            no = None
        if no is None:
            # "покажи заметку 11" / "заметку #11" / "#11" — a bare note reference
            # (only a kind word + number) resolves by number regardless of phrasing;
            # a richer query ("про крипту 2024") still goes to text search.
            m = re.fullmatch(r"\s*(?:заметк\w*|запис\w*|пост\w*|note|item|#)?\s*#?(\d{1,7})\s*",
                             str(params.get("query") or ""), re.IGNORECASE)
            if m:
                no = int(m.group(1))
        if no is not None:
            row = store.message_by_note_no(self.conn, no)
            if row is not None:
                return row
        rows = store.list_messages(self.conn, params.get("category"), params.get("query"), limit=1)
        return rows[0] if rows else None

    def note_no(self, message_id):
        """The note's STABLE number (#N) — assigned once, never reused; gaps on delete are
        intentional. Falls back to the id for a transient row with no number yet."""
        n = store.ensure_note_no(self.conn, message_id)
        return n if n is not None else message_id

    def item_detail_text(self, lang, params):
        """Readable, sectioned detail card (plain text + emoji; sections split by
        blank lines; rows shown only when present)."""
        ru = lang == "ru"
        row = self.resolve_item(params)
        if row is None:
            return T(lang, "items_empty")

        def L(r, e):
            return r if ru else e

        category = row["category"] or row["suggested_category"] or L("без категории", "uncategorized")
        blocks = [[f"📄 #{self.note_no(row['id'])} · {category}"]]

        meta = []
        post_date = row["forward_date"] or row["tg_date"]
        if post_date:
            meta.append("🗓 " + L("Создано: ", "Created: ") + self._fmt_ts_local(post_date))
        if row["received_at"]:
            meta.append("💾 " + L("Сохранено: ", "Saved: ") + self._fmt_iso_local(row["received_at"]))
        if row["forward_origin_title"]:
            meta.append("👤 " + L("Источник: ", "Source: ") + row["forward_origin_title"])
        post_link = ingest.source_link(
            row["forward_origin_username"], row["forward_origin_chat_id"],
            row["forward_origin_message_id"],
        )
        if post_link:
            meta.append("🔗 " + L("Пост: ", "Post: ") + post_link)
        if meta:
            blocks.append(meta)

        summary = (row["summary"] or "").strip()
        if summary:
            blocks.append(["📝 " + summary[:600]])

        facts = store.message_facts(self.conn, row["id"])
        if facts:
            blocks.append(["🔑 " + L("Ключевые факты:", "Key facts:")]
                          + [f"   • {r['fact']}" for r in facts])

        attachments = []
        files = store.message_files(self.conn, row["id"])
        if files:
            names = ", ".join(f["file_name"] or L("файл", "file") for f in files[:10])
            attachments.append("📎 " + L("Файлы: ", "Files: ") + names)
        images = store.message_images(self.conn, row["id"])
        if images:
            attachments.append("🖼 " + L("Фото: ", "Photos: ") + str(len(images)))
        urls = store.message_urls(self.conn, row["id"])
        if urls:
            attachments.append("🌐 " + L("Ссылки:", "Links:"))
            attachments.extend(f"   {r['url']}" for r in urls[:10])
        if attachments:
            blocks.append(attachments)

        return "\n\n".join("\n".join(b) for b in blocks)

    def do_item_detail(self, chat_id, lang, params):
        row = self.resolve_item(params)
        if row is None:
            self.reply(chat_id, T(lang, "items_empty"))
            return
        self.reply(chat_id, self.item_detail_text(lang, {"id": row["id"]}))
        # Hand back the actual photos/files attached to the item, too.
        self.send_attachments(chat_id, row)

    def do_recategorize(self, chat_id, lang, params):
        """Change the category of an already-saved item (by id/ids/query/count,
        else the most recent). Reuses the confirm path, so the change is recorded
        as a correction and feeds learning."""
        category = llm.normalize_category(params.get("category"))
        if not category:
            self.reply(chat_id, T(lang, "clarify"))
            return
        # Resolve the TARGET item(s) WITHOUT the destination category (it's where
        # they go, not a filter): explicit ids/count, a single id, "all in <cat>"
        # / a text query (bulk), else the most recent.
        if params.get("ids") or params.get("count"):
            rows = self.resolve_items({k: params[k] for k in ("ids", "count")
                                       if params.get(k) is not None})
        elif params.get("id") is not None:
            rows = self.resolve_items({"id": params["id"]})
        elif params.get("query"):
            q = params["query"]
            rows = (store.list_messages(self.conn, q, None, limit=20)
                    or store.list_messages(self.conn, None, q, limit=20))
        else:
            row = self.resolve_item({})
            rows = [row] if row else []
        if not rows:
            self.reply(chat_id, T(lang, "items_empty"))
            return
        multi = len(rows) > 1
        for row in rows:
            self.apply_category_confirm(chat_id, row, category, reply_to=None, quiet=multi)
        if multi:
            self.reply(chat_id, T(lang, "recategorized_multi", n=len(rows), category=category))

    def do_merge_categories(self, chat_id, lang, params):
        """Fold a duplicate category into another ('объедини «AI tools» в «AI Tools &
        Resources»'): move all its items over and drop the empty one."""
        src = str(params.get("from") or params.get("query") or "").strip()
        dst = llm.normalize_category(params.get("into") or params.get("category"))
        if not src or not dst:
            self.reply(chat_id, T(lang, "merge_which"))
            return
        moved, dst_name = store.merge_categories(self.conn, src, dst)
        if dst_name is None:
            self.reply(chat_id, T(lang, "category_not_found", category=src))
            return
        self.reply(chat_id, T(lang, "categories_merged", src=src, dst=dst_name, n=moved))

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

    def files_text(self, lang):
        rows = store.recent_files(self.conn, limit=20)
        if not rows:
            return T(lang, "files_empty")
        ru = lang == "ru"
        lines = [T(lang, "files_header", n=len(rows))]
        for r in rows:
            cat = r["category"] or r["suggested_category"] or ("без категории" if ru else "uncategorized")
            name = r["file_name"] or ("файл" if ru else "file")
            lines.append(f"📎 {name} — #{self.note_no(r['message_id'])} · {cat}")
        lines.append(T(lang, "files_footer"))
        return "\n".join(lines)

    def categories_text(self, lang):
        rows = store.category_counts(self.conn)
        if not rows:
            return T(lang, "no_categories")
        lines = [T(lang, "categories_header")]
        lines.extend(f"  {row['name']}: {row['n']}" for row in rows)
        return "\n".join(lines)

    # -- knowledge base / remote fetch (stage 3) ------------------------------

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
            self.reply(chat_id, T(lang, "stored_retry", row_id=self.note_no(row_id)))
            return
        category, alternatives, summary = suggestion
        counts = T(lang, "counts", row_id=self.note_no(row_id), images=0, files=0, urls=1)
        self.present_suggestion(row_id, chat_id, None, category, alternatives, summary, counts)

    def do_ask(self, chat_id, lang, params, text):
        import knowledge
        import persona
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
        import knowledge
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

    # -- spend / review / export ----------------------------------------------

    # -- agreements (passive memory of commitments we made) -------------------

    def do_agreement_add(self, chat_id, lang, params, text):
        import agreements
        now = datetime.now(timezone.utc)
        draft = agreements.validate_add(params, now)
        if not draft:  # router gave no clean text -> fall back to the raw message
            draft = agreements.validate_add(
                {"text": params.get("text") or text, "party": params.get("party"),
                 "due_utc": params.get("due_utc")}, now)
        if not draft:
            self.reply(chat_id, T(lang, "agreement_unclear"))
            return
        store.agreement_add(self.conn, chat_id, draft["text"], party=draft["party"],
                            due_utc=draft["due_utc"], source="explicit")
        relationship.log_event(self.conn, "agreement", f"agreed: {draft['text']}", importance=2)
        self.reply(chat_id, T(lang, "agreement_saved", text=draft["text"]))

    def do_agreements_list(self, chat_id, lang):
        import agreements
        rows = store.agreements_open(self.conn, chat_id)
        self.reply(chat_id, agreements.format_list(rows, self.tz_offset(), lang))

    def do_agreement_close(self, chat_id, lang, params, text):
        import agreements
        rows = store.agreements_open(self.conn, chat_id)
        row = agreements.find(rows, params)
        if row is None:
            self.reply(chat_id, T(lang, "agreement_not_found"))
            return
        outcome = str(params.get("outcome") or "kept").strip().lower()
        cancelled = outcome in ("cancel", "cancelled", "отмена", "отменить", "сними", "снять")
        store.agreement_set_status(self.conn, row["id"], "cancelled" if cancelled else "kept")
        self.reply(chat_id, T(lang, "agreement_cancelled" if cancelled else "agreement_kept",
                              text=row["text"]))

    def do_budget_set(self, chat_id, lang, params):
        """Change the AI spend cap on the boss's explicit request — a runtime
        override stored in preferences and enforced by the budget gateway."""
        raw = str(params.get("amount") or "").replace("$", "").replace(",", ".").strip()
        try:
            amount = round(float(raw), 2)
        except (TypeError, ValueError):
            self.reply(chat_id, T(lang, "budget_set_unclear"))
            return
        if not 0 <= amount <= 1000:  # sane bounds; 0 disables the cap
            self.reply(chat_id, T(lang, "budget_set_unclear"))
            return
        period = str(params.get("period") or "day").strip().lower()
        if period in ("month", "monthly", "месяц", "месячный"):
            store.pref_set(self.conn, "budget_monthly_usd", amount)
            plabel = "месяц" if lang == "ru" else "month"
        else:
            store.pref_set(self.conn, "budget_daily_usd", amount)
            plabel = "день" if lang == "ru" else "day"
        log(f"budget override set: {period}=${amount}")
        self.reply(chat_id, T(lang, "budget_set_done", period=plabel, amount=f"{amount:.2f}"))

    def do_review(self, chat_id, lang, params):
        if str(params.get("focus") or "").strip().lower() == "corrections":
            self.reply(chat_id, review.corrections_report(self.conn, lang))
            return
        if params.get("schedule") or str(params.get("when") or "").strip().lower() in (
            "when", "schedule", "next"
        ):
            self.reply(chat_id, self.review_schedule_text(lang))
            return
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

    def do_export(self, chat_id, lang, params):
        what = str(params.get("what") or "review").strip().lower()
        if what in ("last_trace", "trace_timeline", "trace_steps"):
            filename, md = self._last_trace_markdown(chat_id)
            if not md:
                self.reply(chat_id, T(lang, "trace_none"))
                return
        elif what not in review.EXPORT_KINDS:
            filename, md = review.export_document(self.conn, self.cfg, "review", lang,
                                                  params.get("period") or "week")
        else:
            filename, md = review.export_document(self.conn, self.cfg, what, lang,
                                                  params.get("period") or "week")
        exports_dir = self.cfg.db_path.parent / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)
        (exports_dir / filename).write_text(md, encoding="utf-8")
        try:
            tg_send_document(self.cfg.token, chat_id, filename, md.encode("utf-8"),
                             caption=T(lang, "review_file_caption"), content_type="text/markdown")
            relationship.log_event(self.conn, "export_created",
                                   f"exported {what} to {filename}", importance=1,
                                   title=filename)
        except TelegramError as exc:
            log(f"export send failed: {exc}")
            self.reply(chat_id, T(lang, "llm_error"))
