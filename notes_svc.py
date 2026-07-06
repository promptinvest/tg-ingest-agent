#!/usr/bin/env python3
"""Notes/inbox domain for Cara — the boss's saved-message inbox and its management:
browse/list, item detail, recategorize, merge categories, delete, purge (typed-confirm),
show media, discard; plus long-term journals and the problem/issues log. Gathered out of
the Agent/Hermes into one labelled module.

`NotesMixin` is mixed into the Agent (`class Agent(..., notes_svc.NotesMixin)`), so `self`
is the Agent: `self.reply`/`self.conn`/`self.finalize`/`self.tz_offset()`/`self.reply_chunks`
all resolve on it exactly as before. Pure relocation, no behaviour change.
"""
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ingest
import llm
import reminders
import store
from common import log
from texts import T


class NotesMixin:
    """Notes/inbox, journals, and the problem log. Mixed into the Agent."""

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

    def _match_journal_category(self, name, journals):
        """Map a loosely-typed category to an EXISTING journal so an inflection
        ('благодарности' vs the stored 'Благодарность') doesn't spawn a phantom empty
        one. Exact (case-insensitive) first, then a shared-stem match (long common
        prefix, both diverging only in a short suffix). '' if nothing fits."""
        n = (name or "").casefold()
        if not n:
            return ""
        for j in journals:
            if j.casefold() == n:
                return j
        for j in journals:
            jc = j.casefold()
            cpl = 0
            for a, b in zip(n, jc):
                if a != b:
                    break
                cpl += 1
            if cpl >= 5 and (len(n) - cpl) <= 3 and (len(jc) - cpl) <= 3:
                return j
        return ""

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
        canonical = self._match_journal_category(name, journals) or store.ensure_category(self.conn, name)
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
            snippet = self._ellipsize(body.splitlines()[0], 120) if body else "—"
            lines.append(f"  #{self.note_no(e['id'])} • {snippet}")
        lines.append(T(lang, "journal_open_hint"))
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

    NOTES_PAGE_SIZE = 8

    @staticmethod
    def _ellipsize(text, limit):
        """Trim to `limit` on a word boundary with an ellipsis — a hard slice cut
        previews mid-word («Сервис п»). Short text passes through unchanged."""
        text = (text or "").strip()
        if len(text) <= limit:
            return text
        cut = text[:limit]
        if " " in cut[limit // 2:]:          # only back up when a word break is near
            cut = cut[:cut.rfind(" ")]
        return cut.rstrip(" ,;:·—-") + "…"

    @staticmethod
    def _short_url(url):
        """Compact list-view form of a URL: host + a path stub, no query/fragment
        (tracking params took whole lines). The full URL stays in the detail card."""
        from urllib.parse import urlparse
        try:
            p = urlparse(str(url or ""))
        except ValueError:
            return str(url or "")[:40]
        host = p.netloc[4:] if p.netloc.startswith("www.") else p.netloc
        path = (p.path or "").rstrip("/")
        if len(path) > 24:
            path = path[:24] + "…"
        return (host + path) or str(url or "")[:40]

    def _note_line(self, lang, row):
        """One note's compact block for a list: '📄 #N · category' (N = stable note number),
        a preview, and any attachment/url marks. Shared by the plain list and the paginated view."""
        ru = lang == "ru"
        row_category = row["category"] or row["suggested_category"] or (
            "без категории" if ru else "uncategorized")
        pending = " ⏳" if row["status"] != "confirmed" else ""
        item = [f"📄 #{self.note_no(row['id'])} · {row_category}{pending}"]
        text = self._ellipsize((row["summary"] or row["raw_text"] or "").replace("\n", " "), 110)
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
            marks.append(f"🌐 {self._short_url(urls[0]['url'])}")
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
            # "переложи всё из crypto в news" (a whole category) or a text query. Move the
            # WHOLE set, not a silent slice — the reply reports the real count moved.
            # limit=None: genuinely everything (list_messages no longer pre-caps at 200).
            q = params["query"]
            rows = (store.list_messages(self.conn, q, None, limit=None)
                    or store.list_messages(self.conn, None, q, limit=None))
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

    def do_note_edit(self, chat_id, lang, params, text):
        """Fix a saved note's SUMMARY in place (the LLM-written line shown in lists and the
        detail card) — 'исправь заметку #11 на …', 'поменяй краткое #3 на …'. The original
        message text (raw_text, used for KB search) is preserved; only the summary changes."""
        row = self.resolve_item(params)
        if row is None:
            self.reply(chat_id, T(lang, "items_empty"))
            return
        new_summary = str(params.get("new_summary") or params.get("summary") or "").strip()
        if not new_summary:
            self.reply(chat_id, T(lang, "note_edit_unclear"))
            return
        store.message_update_summary(self.conn, row["id"], new_summary[:600])
        self.reply(chat_id, T(lang, "note_edited", row_id=self.note_no(row["id"]),
                              summary=new_summary[:200]))

    def do_item_delete(self, chat_id, lang, params):
        rows = self.resolve_items(params)
        if not rows:
            self.reply(chat_id, T(lang, "items_empty"))
        elif len(rows) == 1:
            row = rows[0]
            store.pending_set(self.conn, chat_id, "delete", {"row_ids": [row["id"]]})
            snippet = (row["summary"] or row["raw_text"] or "")[:60].replace("\n", " ")
            self.reply(chat_id, T(lang, "delete_confirm", row_id=self.note_no(row["id"]),
                                  category=row["category"] or row["suggested_category"] or "?",
                                  snippet=snippet))
        else:
            ids = [r["id"] for r in rows]
            store.pending_set(self.conn, chat_id, "delete", {"row_ids": ids})
            listing = ", ".join(f"#{self.note_no(i)}" for i in ids)
            self.reply(chat_id, T(lang, "delete_confirm_multi", n=len(ids), ids=listing))

