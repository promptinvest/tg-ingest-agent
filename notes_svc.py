#!/usr/bin/env python3
"""Notes/inbox domain for Cara — the boss's saved-message inbox and its management:
browse/list, item detail, recategorize, merge categories, delete, purge (typed-confirm),
show media, discard; plus long-term journals and the problem/issues log. Gathered out of
the Agent/Hermes into one labelled module.

`NotesMixin` is mixed into the Agent (`class Agent(..., notes_svc.NotesMixin)`), so `self`
is the Agent: `self.reply`/`self.conn`/`self.finalize`/`self.tz_offset()`/`self.reply_chunks`
all resolve on it exactly as before. Pure relocation, no behaviour change.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import events
import ingest
import journals
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
        """Recall one journal page; callbacks keep the same period/category/
        person/tag filter. A structured journal also answers `stats` — a
        deterministic person-frequency view from VALIDATED fields only."""
        name = str(params.get("category") or "").strip()
        journal_names = store.journal_categories(self.conn)
        if not name and len(journal_names) == 1:
            name = journal_names[0]
        if not name:
            hint = ("\n" + ", ".join(journal_names)) if journal_names else ""
            self.reply(chat_id, T(lang, "journal_which") + hint)
            return
        canonical = self._match_journal_category(name, journal_names)
        if not canonical:
            self.reply(chat_id, T(lang, "journal_empty", category=name))
            return
        period = str(params.get("period") or "").strip().lower() or "month"
        if period not in ("day", "week", "month", "all"):
            period = "month"
        person = str(params.get("person") or "").strip() or None
        tag = str(params.get("tag") or "").strip() or None
        if params.get("stats"):
            self.reply(chat_id, self._journal_stats_text(lang, canonical, period))
            return
        rows, total = self._journal_rows(canonical, period, person, tag,
                                         0, self.JOURNAL_PAGE_SIZE)
        if not total:
            self.reply(chat_id, T(lang, "journal_empty", category=canonical))
            return
        store.list_views_prune(self.conn,
                               (datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
        token = store.list_view_add(
            self.conn, chat_id,
            {"view": "journal", "category": canonical, "period": period,
             "person": person, "tag": tag},
        )
        # Hand the page we already hold to the renderer. Opening a journal used
        # to run `_journal_rows` TWICE — once for this emptiness check, once
        # inside `_journal_page` — and each one is a full Python scan of every
        # confirmed categorized message (`journal_entries_page`). Same query,
        # same offset, same filters: fetching it a second time bought nothing.
        text, keyboard, _ = self._journal_page(lang, canonical, period, 0, token,
                                               person=person, tag=tag,
                                               prefetched=(rows, total))
        self.reply(chat_id, text, reply_markup=keyboard)

    def _journal_rows(self, canonical, period, person, tag, offset, limit):
        """One page of a journal + the filtered total. A structured journal
        reads from journal_entries (occurred_at order, payload filters); a
        plain journal category keeps the message-based page. Rows come back as
        (message-like row, payload dict)."""
        since = self._journal_since(period)
        gdef = store.journal_def_by_category(self.conn, canonical, active_only=False)
        if gdef is not None:
            rows = store.journal_entries_for(self.conn, gdef["id"], since)
            if person or tag:
                p = (person or "").casefold()
                t = (tag or "").casefold()
                kept = []
                for r in rows:
                    payload = store.journal_entry_payload(r)
                    people = [str(x).casefold() for x in payload.get("people") or []]
                    subject = str(payload.get("subject") or "").casefold()
                    tags = [str(x).casefold() for x in payload.get("tags") or []]
                    if p and not (any(p in x for x in people) or (p and p in subject)):
                        continue
                    if t and not any(t in x for x in tags):
                        continue
                    kept.append(r)
                rows = kept
            start = max(0, int(offset or 0))
            page = rows[start:start + max(1, int(limit or 5))]
            return [(r, store.journal_entry_payload(r)) for r in page], len(rows)
        page, total = store.journal_entries_page(self.conn, canonical, since,
                                                 offset, limit)
        return [(r, {}) for r in page], total

    def _journal_page(self, lang, canonical, period, offset, token,
                      person=None, tag=None, prefetched=None):
        """Render one oldest-first journal page with a stable filter. Entries of
        a structured journal carry the J#-prefixed stable number (§5.6 — the
        linked message's lazy note number, never a second counter).

        `prefetched` is `(entries, total)` for THIS offset/filter when the
        caller has already fetched it (see `do_journal_show`) — the page fetch
        is a full Python scan, so it must not run twice for one render."""
        ru = lang == "ru"
        gdef = store.journal_def_by_category(self.conn, canonical, active_only=False)
        prefix = "J#" if gdef is not None else "#"
        entries, total = prefetched if prefetched is not None else self._journal_rows(
            canonical, period, person, tag, offset, self.JOURNAL_PAGE_SIZE)
        plabel = {"day": ("за сегодня", "today"), "week": ("за неделю", "this week"),
                  "month": ("за месяц", "this month"),
                  "all": ("за всё время", "all time")}[period][0 if ru else 1]
        if person:
            plabel += (f", про {person}" if ru else f", about {person}")
        if tag:
            plabel += (f", тег «{tag}»" if ru else f", tag \"{tag}\"")
        # The all-time total beside the filtered one. When the page IS the
        # all-time unfiltered view the number already in hand is that total —
        # otherwise count it in SQL rather than fetching every entry again just
        # to call len() on it (this renders on every page turn).
        if period == "all" and not person and not tag:
            all_total = total
        elif gdef is not None:
            all_total = store.journal_entries_count_for(self.conn, gdef["id"])
        else:
            all_total = store.journal_count(self.conn, canonical)
        lines = [T(lang, "journal_header", category=canonical, n=total,
                   period=plabel, total=all_total)]
        last_day = None
        for row, payload in entries:
            mid = row["message_id"] if gdef is not None else row["id"]
            when = row["occurred_at"] if gdef is not None else row["received_at"]
            day = self._fmt_iso_local(when).split(",")[0]
            if day != last_day:
                lines.append(f"\n📅 {day}")
                last_day = day
            body = (row["summary"] or row["raw_text"] or "").strip()
            snippet = self._ellipsize(body.splitlines()[0], 120) if body else "—"
            lines.append(f"  {prefix}{self.note_no(mid)} • {snippet}")
            extras = journals.draft_lines(lang, payload)
            if extras:
                lines.append("     " + " · ".join(extras[:2]))
        lines.append(T(lang, "journal_open_hint"))
        return ("\n".join(lines),
                self._notes_page_keyboard(lang, token, offset, total,
                                          page_size=self.JOURNAL_PAGE_SIZE), total)

    def _journal_stats_text(self, lang, canonical, period):
        """Deterministic person counts from VALIDATED fields, with J# citations
        (descriptive only — never an inference about the boss)."""
        ru = lang == "ru"
        gdef = store.journal_def_by_category(self.conn, canonical, active_only=False)
        if gdef is None:
            return T(lang, "journal_stats_empty", category=canonical)
        rows = store.journal_entries_for(self.conn, gdef["id"],
                                         self._journal_since(period))
        pairs = [(store.journal_entry_payload(r), self.note_no(r["message_id"]))
                 for r in rows]
        counts = journals.person_counts(pairs)
        if not counts:
            return T(lang, "journal_stats_empty", category=canonical)
        plabel = {"day": ("за сегодня", "today"), "week": ("за неделю", "this week"),
                  "month": ("за месяц", "this month"),
                  "all": ("за всё время", "all time")}[period][0 if ru else 1]
        lines = [T(lang, "journal_stats_header", category=canonical, period=plabel)]
        for name, n, nos in counts[:10]:
            cite = ", ".join(f"J#{x}" for x in nos[:5])
            lines.append(f"  {name} — {n} ({cite})")
        return "\n".join(lines)

    def do_journal_prompt(self, chat_id, lang, params):
        """Opt-in journal prompts (§D-06, JRN-006): per-journal; ENABLING needs
        an explicit confirmation (pending), disabling is immediate. The prompt
        itself is a suggestion-only heartbeat nudge that honors quiet hours,
        days, and the daily cap."""
        name = str(params.get("category") or "").strip()
        defs = store.journal_defs(self.conn, active_only=True)
        gdef = None
        if name:
            gdef = store.journal_def_by_category(self.conn, name)
            if gdef is None:
                match = self._match_journal_category(
                    name, [d["category"] or d["display_name"] for d in defs])
                if match:
                    gdef = store.journal_def_by_category(self.conn, match)
        elif len(defs) == 1:
            gdef = defs[0]
        if gdef is None:
            hint = ", ".join((d["category"] or d["display_name"]) for d in defs)
            self.reply(chat_id, T(lang, "journal_which") + ("\n" + hint if hint else ""))
            return
        display = gdef["category"] or gdef["display_name"]
        on = params.get("on")
        on = True if on is None else bool(on)
        if not on:
            store.journal_def_update(self.conn, gdef["slug"], proactive_enabled=0)
            self.reply(chat_id, T(lang, "journal_prompt_disabled", category=display))
            return
        hour = journals.parse_prompt_hour(params.get("time"), default=21)
        store.pending_set(self.conn, chat_id, "journal_prompt",
                          {"slug": gdef["slug"], "hour": hour, "display": display})
        self.reply(chat_id, T(lang, "journal_prompt_confirm", category=display,
                              hour=hour))

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
        counts = store.notes_lifecycle_counts(self.conn)
        lines.append(T(lang, "overview_notes",
                       active=counts.get("active", 0), inbox=counts.get("inbox", 0),
                       due=counts.get("review_due", 0),
                       archived=counts.get("archived", 0)))
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
    JOURNAL_PAGE_SIZE = 5

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

    def _catalog_meta(self, row_category, row_id):
        """' · 2022 · Scott Mann' for a CATALOG note (Movies/Books and their RU
        aliases), or ''.

        «Когда прошу показать фильмы, хочу видеть не только названия, но и годы»
        (owner, 2026-07-28). The values come from the note's own
        provenance-tagged facts — `photo:`/`lookup:`/`model:` year and
        author/director (media.parse_catalog_facts, the same reader the md
        export uses), so a listing can only ever show what is actually stored.
        A note with no year (an ordinary «A Star is Born» filed by ingest long
        before this feature) shows its title alone: no placeholder, no invented
        year. The creator rides along only when there IS one — it is what makes
        two same-titled films tellable apart."""
        if not store.is_catalog_category(row_category):
            return ""
        import media
        fields, _comments = media.parse_catalog_facts(
            [f["fact"] for f in store.message_facts(self.conn, row_id)])
        bits = [b for b in (fields.get("year"), fields.get("creator")) if b]
        return "".join(" · " + " ".join(str(b).split())[:60] for b in bits)

    def _note_line(self, lang, row):
        """One note's compact block for a list: '📄 #N · category' (N = stable note number),
        a preview, and any attachment/url marks. Shared by the plain list and the paginated view.
        A catalog note (Movies/Books) also carries its stored year and creator."""
        ru = lang == "ru"
        row_category = row["category"] or row["suggested_category"] or (
            "без категории" if ru else "uncategorized")
        pending = " ⏳" if row["status"] != "confirmed" else ""
        item = [f"📄 #{self.note_no(row['id'])} · {row_category}{pending}"]
        text = self._ellipsize((row["summary"] or row["raw_text"] or "").replace("\n", " "), 110)
        if text:
            item.append(f"   {text}{self._catalog_meta(row_category, row['id'])}")
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

    def _notes_page(self, lang, category, query, offset, token, state=None):
        """Render one page of notes + its ◀/▶ keyboard.

        Returns (text, keyboard, total, rows) — `rows` is exactly what this page
        RENDERS, in display order, so the caller can pin it as "the list he is
        looking at" once it is actually delivered (C3, 2026-07-28). Re-deriving
        that list from a second query is what «покажи год каждого» must never do:
        the answer has to be about the notes on his screen."""
        rows, total = store.list_messages_page(self.conn, category, query, offset,
                                               self.NOTES_PAGE_SIZE, state=state)
        filter_part = ""
        if category:
            filter_part = T(lang, "items_filter_category", category=category)
        elif query:
            filter_part = T(lang, "items_filter_query", query=query)
        elif state:
            labels = {"inbox": ("входящие", "inbox"), "active": ("активные", "active"),
                      "archived": ("архив", "archive")}
            filter_part = T(lang, "items_filter_query",
                            query=labels[state][0 if lang == "ru" else 1])
        start = offset + 1 if rows else 0
        blocks = [T(lang, "notes_page_header", filter=filter_part,
                    start=start, end=offset + len(rows), total=total)]
        for row in rows:
            blocks.append(self._note_line(lang, row))
        return ("\n\n".join(blocks),
                self._notes_page_keyboard(lang, token, offset, total), total, rows)

    def _notes_page_keyboard(self, lang, token, offset, total, page_size=None):
        """A ◀ Back · X/Y · Next ▶ row — or None when it all fits on one page."""
        page_size = page_size or self.NOTES_PAGE_SIZE
        pages = max(1, (total + page_size - 1) // page_size)
        if pages <= 1:
            return None
        cur = offset // page_size
        buttons = []
        if cur > 0:
            buttons.append({"text": T(lang, "page_prev"), "callback_data": f"pg|{token}|{cur - 1}"})
        buttons.append({"text": f"{cur + 1}/{pages}", "callback_data": f"pg|{token}|noop"})
        if cur < pages - 1:
            buttons.append({"text": T(lang, "page_next"), "callback_data": f"pg|{token}|{cur + 1}"})
        return {"inline_keyboard": [buttons]}

    def do_list_items(self, chat_id, lang, params):
        """Browse saved notes with inline ◀/▶ pagination (edits one message in place instead
        of flooding the chat or capping the list at 10). An explicit `state`
        (inbox/active/archived) opens that lifecycle view.

        A CATALOG alias in the filter («покажи фильмы» after the 2026-07-28 fold
        moved those notes to «Movies») is resolved inside
        `store.list_messages_filtered` — ONE place, so resolve_item,
        resolve_items and the bulk recategorize get it too."""
        category, query = params.get("category"), params.get("query")
        state = str(params.get("state") or "").strip().lower() or None
        if state not in (None,) + store.NOTE_STATES:
            state = None
        _, total = store.list_messages_page(self.conn, category, query, 0,
                                            self.NOTES_PAGE_SIZE, state=state)
        if total == 0:
            self.reply(chat_id, T(lang, "archive_empty" if state == "archived"
                                  else "items_empty"))
            return
        store.list_views_prune(self.conn,
                               (datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
        token = store.list_view_add(self.conn, chat_id,
                                    {"category": category, "query": query, "state": state})
        text, keyboard, _, rows = self._notes_page(lang, category, query, 0, token,
                                                   state=state)
        if self.reply(chat_id, text, reply_markup=keyboard):
            # Delivered — so this IS the list he is looking at, and a follow-up
            # («покажи год каждого») answers about exactly these notes (C3).
            self._shown_list_set([r["id"] for r in rows])

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
        store.kv_set(self.conn, f"journal_draft:{row_id}", "")
        self.reply(chat_id, T(lang, "discarded"))

    def _purge_impact_text(self, lang, info):
        ru = lang == "ru"
        labels = {
            "messages": ("сообщений" if ru else "messages"),
            "reminders": ("напоминаний" if ru else "reminders"),
            # Closed reminders keep verbatim titles + event history — a purge
            # that takes them must SAY so (scope 'all'/'reminders', 2026-07-27).
            "reminders_closed": ("закрытых напоминаний (история)" if ru else
                                 "closed reminders (history)"),
            "categories": ("категорий" if ru else "categories"),
            "issues": ("записей о проблемах" if ru else "issue records"),
            "feedback": ("поправок" if ru else "corrections"),
            "conversation": ("реплик нашей переписки" if ru else "conversation turns"),
            "note_outcomes": ("метрик использования заметок" if ru else
                              "note outcome records"),
            # Scope 'all' also wipes the verbatim copies Telegram delivery left
            # in the durable inbox — disclosed, like conversation history.
            "updates_scrubbed": ("служебных копий входящих сообщений" if ru else
                                 "raw copies of incoming messages"),
            "assistant_tasks": ("задач Cara" if ru else "Cara tasks"),
            "task_artifacts": ("файлов задач" if ru else "task artifacts"),
            "task_feedback": ("отзывов о задачах" if ru else "task feedback rows"),
            "evaluation_cases": ("проверочных кейсов" if ru else "evaluation cases"),
            "evaluation_runs": ("прогонов проверок" if ru else "evaluation runs"),
            "improvement_proposals": (
                "предложений улучшений" if ru else "improvement proposals"),
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
        elif info.get("scope") == "journal" and "messages" in info:
            cat = info.get("category") or "?"
            parts = [f"  • {info['messages']} " + ("записей дневника «" if ru else
                     "entries of the journal \"") + f"{cat}»"]
        return "\n".join(parts)

    def do_purge(self, chat_id, lang, params):
        scope = str(params.get("scope") or "").strip().lower()
        if scope not in store.PURGE_SCOPES:
            self.reply(chat_id, T(lang, "clarify"))
            return
        category = params.get("category")
        if scope in ("category", "journal") and category:
            category = store.canonical_category(self.conn, category) or category
        # A diary always purges under its OWN typed phrase (plan v1.1 §11): a
        # generic category phrase must never wipe a journal — and vice versa.
        if scope == "category" and store.is_journal(self.conn, category):
            scope = "journal"
        elif scope == "journal" and not store.is_journal(self.conn, category):
            scope = "category"
        info = store.purge_preview(self.conn, scope, category)
        # Every key the preview can DISCLOSE belongs here: this guard decides
        # whether a disclosed effect happens at all, so a destructive effect it
        # cannot see (the scope-'all' inbox scrub) would be skipped on exactly
        # the database that still needs it — «здесь уже пусто» while verbatim
        # copies of his messages stay on disk and in the off-box backups.
        if not any(info.get(k) for k in ("messages", "reminders",
                                         "reminders_closed", "categories",
                                         "issues", "feedback", "conversation",
                                         "note_outcomes", "updates_scrubbed",
                                         "assistant_tasks", "task_artifacts",
                                         "task_feedback", "evaluation_cases",
                                         "evaluation_runs",
                                         "improvement_proposals")):
            self.reply(chat_id, T(lang, "purge_nothing"), record=False)
            return
        if scope in ("category", "journal"):
            phrase = T(lang, f"purge_phrase_{scope}", category=category or "?")
        else:
            phrase = T(lang, f"purge_phrase_{scope}")
        anticipated = dict(info)
        if scope == "all" and getattr(self, "_current_update_id", None) is not None:
            # The typed confirmation is itself one boss conversation turn and
            # one completed inbound update. Preview replies are deliberately
            # not recorded, so these are the only predictable additions.
            anticipated["conversation"] = anticipated.get("conversation", 0) + 1
            # The purge-request update is still pending during this preview and
            # becomes terminal after the handler; the future confirmation
            # update is the second raw copy included in the exact scope.
            anticipated["updates_scrubbed"] = anticipated.get("updates_scrubbed", 0) + 2
        store.pending_set(
            self.conn, chat_id, "purge",
            {"scope": scope, "category": category, "phrase": phrase,
             "anticipated": anticipated}, ttl_seconds=300)
        self.reply(chat_id, T(lang, "purge_preview",
                              impact=self._purge_impact_text(lang, anticipated),
                              phrase=phrase), record=False)

    def resolve_purge(self, chat_id, lang, pending, text):
        payload = pending["payload"]
        store.pending_clear(self.conn, chat_id)
        if text.strip().casefold() != str(payload.get("phrase") or "").strip().casefold():
            self.reply(chat_id, T(lang, "purge_cancelled"), record=False)
            return
        actual = store.purge_preview(
            self.conn, payload["scope"], payload.get("category"))
        compare_actual = dict(actual)
        if (payload["scope"] == "all"
                and getattr(self, "_current_update_id", None) is not None):
            # The confirmation update is still status=pending until this
            # handler returns, so the generic scrub predicate cannot count it
            # yet. It is nevertheless part of this purge and is scrubbed below.
            compare_actual["updates_scrubbed"] = (
                compare_actual.get("updates_scrubbed", 0) + 1)
        if compare_actual != (payload.get("anticipated") or compare_actual):
            # State changed while the destructive card was open. Never execute
            # a wider purge than the exact preview the boss confirmed.
            phrase = payload["phrase"]
            anticipated = dict(actual)
            if (payload["scope"] == "all"
                    and getattr(self, "_current_update_id", None) is not None):
                anticipated["conversation"] = anticipated.get("conversation", 0) + 1
                anticipated["updates_scrubbed"] = anticipated.get(
                    "updates_scrubbed", 0) + 2
            store.pending_set(
                self.conn, chat_id, "purge",
                {"scope": payload["scope"], "category": payload.get("category"),
                 "phrase": phrase, "anticipated": anticipated}, ttl_seconds=300)
            self.reply(
                chat_id, T(lang, "purge_preview",
                                impact=self._purge_impact_text(lang, anticipated),
                                phrase=phrase), record=False)
            return
        purge_nonce = None
        if payload["scope"] == "all":
            try:
                # Create a durable DB-side nonce first. The worker marker is
                # published only after purge_execute commits db_committed.
                purge_nonce = self.prepare_task_purge()
            except Exception as exc:
                log(f"PURGE all refused before durable task-file marker: {exc}")
                self.reply(
                    chat_id,
                    ("Не начала очистку: изолированный worker не принял "
                     "надёжный маркер удаления. Попробуй ещё раз."
                     if lang == "ru" else
                     "Purge was not started: the isolated worker could not accept "
                     "a durable deletion marker. Please retry."),
                    record=False)
                return
        info, paths = store.purge_execute(
            self.conn, payload["scope"], payload.get("category"),
            task_purge_nonce=purge_nonce)
        if payload["scope"] == "all" and getattr(self, "_current_update_id", None) is not None:
            self.conn.execute(
                "UPDATE telegram_updates SET payload = '{}', last_error = NULL,"
                " status = 'done', updated_at = ?"
                " WHERE update_id = ?",
                (store._now(), int(self._current_update_id)))
            self.conn.commit()
            info["updates_scrubbed"] = info.get("updates_scrubbed", 0) + 1
        for path in paths:
            Path(path).unlink(missing_ok=True)
        if payload["scope"] == "all":
            worker_purged = self.purge_task_external_state(purge_nonce)
        else:
            worker_purged = True
        log(f"PURGE scope={payload['scope']} category={payload.get('category')} by operator")
        done = T(lang, "purge_done", impact=self._purge_impact_text(lang, info))
        if not worker_purged:
            done += (
                "\nWorker-файлы помечены для удаления, но подтверждение ещё не получено."
                if lang == "ru" else
                "\nWorker files are durably marked for deletion, but acknowledgement "
                "is still pending."
            )
        self.reply(chat_id, done, record=False)

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
            # FAIL CLOSED, like the reminder path: an EXPLICIT ids list resolves to
            # exactly what it names. Falling through when none matched archived/
            # deleted the NEWEST unrelated note («в архив #7 и #9» with both gone).
            return out
        if params.get("id") is not None:
            # A SINGLE explicit #N is the router's canonical form («убери #5 в
            # архив» -> {"id": 5}) and is exactly as explicit as the ids list:
            # resolve it strictly. Falling through to resolve_item took the
            # NEWEST note on a miss, and lifecycle/recategorize act on it
            # immediately — no confirmation to catch the substitution.
            # Tested BEFORE count: «убери #7 в архив» routed as {"id": 7,
            # "count": 1} used to hand the newest note to a no-confirmation op.
            row = store.message_by_note_no(self.conn, params.get("id"))
            if row is not None or store.note_no_value(params.get("id")) is not None:
                return [row] if row is not None else []
            # Present but UNUSABLE ("", "abc") — a router artefact, not a number
            # he named (same rule as resolve_item, which was given this escape
            # by the audit while this resolver kept refusing): it may fall
            # through, but only to the count/query/category paths below, which
            # return solely what actually matches — never the newest note.
            if not (params.get("query") or params.get("category")
                    or params.get("count")):
                return []
        count = params.get("count")
        if count is not None:
            try:
                n = max(1, min(int(count), 20))
            except (TypeError, ValueError):
                return []   # an unusable count is not a licence to take the newest
            # Bounded by the filter he named: «удали 3 из crypto» took the three
            # newest notes of the WHOLE inbox when the filter was dropped here.
            return store.list_messages(self.conn, params.get("category"),
                                       params.get("query"), limit=n)
        row = self.resolve_item(params)
        return [row] if row else []

    def resolve_item(self, params):
        """Resolve an item by its stable note number (#N), query/category, or most recent.

        FAIL CLOSED on an EXPLICIT number, exactly like `resolve_items` (WP5) and
        the reminder path: «покажи #7» when #7 is gone is a not-found, never the
        newest note. The singular resolver was missed by WP5 and it feeds the
        handlers that act WITHOUT confirmation — `do_note_edit` rewrote another
        note's summary, `do_show_media` sent another note's photos, the detail
        card showed another note, and «напомни по заметке 7» tied the reminder to
        the newest one. An id-LESS request (a query or a category, or nothing)
        keeps its old meaning: the best match, else the most recent.
        """
        raw_id = params.get("id")
        if raw_id is not None:
            row = store.message_by_note_no(self.conn, raw_id)
            if row is not None or store.note_no_value(raw_id) is not None:
                return row      # a number he NAMED: it resolves, or it is gone
            # Present but UNUSABLE ("", "abc", a stray dict): that is a router
            # artefact, not a number he named, so it may fall through — but only
            # to a SEARCH, which can return solely what actually matches. With no
            # query and no category the fall-through would be "the most recent",
            # i.e. exactly the substitution this rule exists to stop.
            if not (params.get("query") or params.get("category")):
                return None
        # "покажи заметку 11" / "заметку #11" / "#11" / "J#11" — a bare note
        # reference (only a kind word + number) resolves by number regardless
        # of phrasing (J# is the journal-entry form of the SAME stable number,
        # §5.6); a richer query ("про крипту 2024") still goes to text search.
        # This one deliberately keeps its fall-through: `query` is not an
        # explicit id, and the fallback is a SEARCH for that same text (a bare
        # «9800» finds the note whose key fact says 9800) — it can only return
        # something that actually matches, never the newest note by recency.
        m = re.fullmatch(r"\s*(?:заметк\w*|запис\w*|пост\w*|note|item|[jJ]?#)?\s*"
                         r"[jJ]?#?(\d{1,7})\s*",
                         str(params.get("query") or ""), re.IGNORECASE)
        if m:
            row = store.message_by_note_no(self.conn, int(m.group(1)))
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
        # resolve_item speaks NOTE NUMBERS, row["id"] is the DB message id — the two
        # diverge on a long-lived DB (numbers are assigned lazily, newest-first), so
        # re-resolving by the raw id showed a DIFFERENT note's card.
        self.reply(chat_id, self.item_detail_text(lang, {"id": self.note_no(row["id"])}))
        # Opening the detail is a REAL use (unlike mere retrieval/ranking).
        if store.note_mark_used(self.conn, row["id"]):
            events.record_done(self.conn, "note_opened", chat_id=chat_id,
                               payload={"message_id": row["id"]})
            # Opening a just-resurfaced note = the suggestion was ACCEPTED.
            try:
                res = json.loads(store.kv_get(self.conn, "last_resurfaced") or "")
                ts = datetime.fromisoformat(res["ts"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                # The stable #N is checked alongside the rowid (which SQLite
                # reuses after a delete) so the acceptance can only be credited
                # to the note that was actually resurfaced.
                if (int(res["id"]) == row["id"]
                        and res.get("no") in (None, row["note_no"])
                        and datetime.now(timezone.utc) - ts <= timedelta(minutes=15)):
                    events.record_done(self.conn, "note_resurface_accepted",
                                       chat_id=chat_id,
                                       payload={"message_id": row["id"]})
                    store.kv_set(self.conn, "last_resurfaced", "")
            except (KeyError, TypeError, ValueError):
                pass
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
        # An id that is PRESENT but unusable («» / «первая») is a router
        # artefact, not a number he named (resolve_item's rule): his real
        # reference is the query, so drop the artefact and let the query branch
        # run — «удали заметку про крипту» arriving as {"id": "", "query":
        # "про крипту"} was a hard «ничего не нашла» on a note that exists.
        # With no query either, fail closed rather than take the newest note.
        if (params.get("id") is not None
                and store.note_no_value(params.get("id")) is None):
            params = {k: v for k, v in params.items() if k != "id"}
            if not (params.get("query") or params.get("ids") or params.get("count")):
                self.reply(chat_id, T(lang, "items_empty"))
                return
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
            # Only a VISIBLE note (one the #N lists can show) may lazily claim
            # its number here. `note_no` is a WRITER (ensure_note_no), and
            # calling it for a failed/duplicate row permanently consumed a #N
            # that no list ever showed — the boss's numbering jumped #56 → #58
            # and «убери #57» answered «вне жизненного цикла» (2026-07-27).
            if r["status"] in ("confirmed", "suggested"):
                lines.append(f"📎 {name} — #{self.note_no(r['message_id'])} · {cat}")
            else:
                lines.append(f"📎 {name} · {cat}")
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
        message text (raw_text, used for KB search) is preserved; only the summary changes.

        C4 (2026-07-28) — this is also how a stale CATALOG title gets fixed. Note
        #44's summary is a whole paragraph («Форвард кинофильма "The Ledge" …»)
        because it predates the forwarded-poster routing, and the listing reads
        it back verbatim. Retitling it here fixes the list line, the detail card
        and the catalog dedup index (all three read `summary or raw_text`) while
        the note's stored FACTS — its year, its director — are left alone: they
        describe the same work, and a title fix is not a claim about them.

        C2 — it ASKS first. Replacing the text of an entry that already exists is
        the operation the live failure claimed to have performed («#51 теперь
        «Всё везде и сразу» вместо «Я устал от тебя»»), and it is not additive:
        the old text does not survive it. So the confirmation NAMES the note and
        shows both versions, and nothing is written until he says yes."""
        row = self.resolve_item(params)
        if row is None:
            self.reply(chat_id, T(lang, "items_empty"))
            return
        new_summary = str(params.get("new_summary") or params.get("summary") or "").strip()
        if not new_summary:
            self.reply(chat_id, T(lang, "note_edit_unclear"))
            return
        new_summary = new_summary[:600]
        # ONE pending slot per chat, and a bare «да» resolves whatever is in it.
        # Taking a slot that belongs to another question would point his next yes
        # at a TEXT REPLACEMENT he was not answering — a media card's ✅ landing
        # on «исправь заметку #11 на …» is exactly the accident C2 forbids. The
        # media card's own discipline, applied here (review fix 2026-07-28): the
        # open question keeps the slot and she asks him to finish it first.
        existing = store.pending_get(self.conn, chat_id)
        if existing is not None and existing.get("kind") != "note_retitle":
            self.reply(chat_id, T(lang, "note_edit_slot_busy"))
            return
        old = (row["summary"] or row["raw_text"] or "").strip()
        note_no = self.note_no(row["id"])
        store.pending_set(self.conn, chat_id, "note_retitle",
                          {"row_id": row["id"], "note_no": note_no,
                           "new_summary": new_summary})
        self.reply(chat_id, T(
            lang, "note_edit_confirm", row_id=note_no,
            category=row["category"] or row["suggested_category"] or "?",
            old=self._ellipsize(old.replace("\n", " "), 200) or "—",
            new=self._ellipsize(new_summary.replace("\n", " "), 200)))

    def apply_note_retitle(self, chat_id, lang, payload):
        """His yes to the note_edit confirmation: the summary is replaced.

        Re-resolved and re-checked against the note he was SHOWN: the pending
        carries the stable #N beside the rowid (SQLite reuses rowids), so a
        confirmation can never land the new text on a different note than the one
        the question named."""
        row = store.get_message(self.conn, payload.get("row_id"))
        if row is None or (payload.get("note_no") is not None
                           and row["note_no"] != payload.get("note_no")):
            self.reply(chat_id, T(lang, "items_empty"))
            return
        new_summary = str(payload.get("new_summary") or "")[:600]
        if not new_summary:
            self.reply(chat_id, T(lang, "note_edit_unclear"))
            return
        store.message_update_summary(self.conn, row["id"], new_summary)
        # The semantic index was built from the OLD text. Leaving it stale would
        # let `ask` answer out of a title he just replaced — the same divergence
        # apply_note_edit re-embeds away. Best-effort: `index_message` swallows
        # its own LLM/budget failures, and the durable text is already correct.
        facts = [f["fact"] for f in store.message_facts(self.conn, row["id"])]
        store.set_chunks(self.conn, row["id"], [])
        self.index_message(row["id"], "\n".join([new_summary, *facts]))
        store.note_outcome_record(self.conn, row["id"], "note_edited", source="edit")
        self.reply(chat_id, T(lang, "note_edited",
                              row_id=self.note_no(row["id"]),
                              summary=new_summary[:200]))
        log(f"note #{row['id']} summary replaced by operator")

    # -- C3: ONE field across the list she just showed --------------------------
    #
    # «Покажи год каждого» right after a 3-item Movies listing returned ONE
    # note's full detail card (production, 2026-07-28 05:49). The request is a
    # follow-up ABOUT THAT LIST, and the only honest way to answer it is from the
    # pinned list — not from a fresh "most recent" query, which would answer
    # about notes he never saw.
    #
    # It reads STORED facts only (media.parse_catalog_facts / facts_fields — the
    # same reader the listing and the md export use). It never looks a value up
    # to fill a gap: a looked-up year printed in a list of HIS data would read as
    # something he has on file. Where nothing is stored it says so, per item.
    _LIST_FIELDS = ("year", "creator", "genre")
    # WORD-BOUNDED, never bare substrings — the lesson the catalog-grounding cues
    # already paid for: «в чём выгода?» contains «года» and «Категорически» contains
    # «категор».
    #
    # …and a stem + \w* is only half of that lesson (review fix 2026-07-28):
    # `год\w*` is word-bounded and still matches «годовщина», «годится»,
    # «годный». `_list_field_name` falls back to the RAW MESSAGE when the router
    # names no field, so an unrelated word would silently pick one. The year cue
    # is therefore spelled out as the inflections he actually uses; the other two
    # keep the stem, where every inflection really is the same word.
    _LIST_FIELD_PATTERNS = (
        ("year", re.compile(r"\b(?:years?|год|года|году|годом|годе|"
                            r"годы|годов|годам|годами|годах)\b")),
        ("creator", re.compile(r"\b(?:creators?|directors?|authors?|"
                               r"режисс\w*|автор\w*|чей|чьи|чь[её])\b")),
        ("genre", re.compile(r"\b(?:genres?|жанр\w*)\b")),
    )

    def _list_field_name(self, params, text):
        """The field he asked for: the router's `field` param, else the word in
        his own message («а режиссёры?»). '' when neither names one."""
        for source in (params.get("field"), text):
            low = " ".join(str(source or "").casefold().split())
            if not low:
                continue
            for field, pattern in self._LIST_FIELD_PATTERNS:
                if low == field or pattern.search(low):
                    return field
        return ""

    def do_list_field(self, chat_id, lang, params, text=""):
        """«Покажи год каждого» / «а режиссёры?» — one field for EVERY item of
        the list she just showed."""
        import media
        field = self._list_field_name(params, text)
        if field not in self._LIST_FIELDS:
            self.reply(chat_id, T(lang, "list_field_which"))
            return
        slots = self._shown_list_slots()
        if not slots:
            # No list was shown (or it aged out). She says that, and does NOT
            # substitute a recomputed one — the substitution this whole
            # mechanism exists to prevent.
            self.reply(chat_id, T(lang, "list_field_no_list"))
            return
        lines = [T(lang, "list_field_header", field=T(lang, "list_field_name_" + field))]
        for slot in slots:
            row = slot["row"]
            if row is None:
                lines.append(f"#{slot['no'] if slot['no'] is not None else '?'} — "
                             + T(lang, "list_field_gone"))
                continue
            title = self._ellipsize(
                (row["summary"] or row["raw_text"] or "").replace("\n", " "), 70)
            trio = media.facts_fields(
                [f["fact"] for f in store.message_facts(self.conn, row["id"])]
            ).get(field)
            value = str(trio[1]).strip() if trio and len(trio) > 1 else ""
            lines.append(f"#{self.note_no(row['id'])} {title} — "
                         + (value or T(lang, "list_field_unknown")))
        self.reply_chunks(chat_id, "\n".join(lines))

    # -- note review (deterministic ≤3-item batch + stable snapshot, §9) -------

    _REVIEW_REASONS = {
        "review_due": ("пора пересмотреть — ты сам просил вернуться",
                       "due for the review you asked for"),
        "temp_expiring": ("временная — срок подходит",
                          "temporary — its window is closing"),
        "actionable_unused": ("требовала действия, движения не видно",
                              "was actionable, no follow-up recorded"),
        "inbox": ("не разобрана", "still untriaged"),
        "old_unused": ("давно лежит без дела", "old and never used"),
    }

    def _pin_shown(self, key, ids, ttl_seconds):
        """Remember WHICH notes were shown, under `key` — each with its stable
        identity.

        `messages.id` is a plain rowid and SQLite REUSES the highest one after a
        delete, so a bare id list could point at a note he was never shown (WP3
        closed that for kv KEYS; the VALUE had the same hole). The `note_no` is
        stored beside the id and re-checked on resolve: it is claimed once and
        never reused, so a mismatch means the shown note is gone.

        Two snapshots use this now (2026-07-28): the review batch, and the LIST
        she just showed — «покажи год каждого» has to answer about the three
        notes on his screen, not about the three newest notes in the table.
        Every key written here belongs in store.NOTE_REF_KV_KEYS, which is what
        makes a purge drop it with the rows it describes."""
        items = []
        for i in ids:
            row = store.get_message(self.conn, i)
            items.append({"id": int(i),
                          "no": row["note_no"] if row is not None else None})
        store.kv_set(self.conn, key, json.dumps(
            {"items": items, "ids": list(ids),
             "ts": datetime.now(timezone.utc).isoformat(),
             "ttl": int(ttl_seconds)}, ensure_ascii=False))

    def _shown_slots(self, key):
        """[{'id','no','row'}] for a pinned snapshot, in DISPLAY order. `row` is
        None when that note is gone (deleted, or its rowid reused by another
        note) — the slot survives so the caller can say so per item instead of
        quietly showing a shorter list than he was given. Empty list = no live
        snapshot at all."""
        raw = store.kv_get(self.conn, key)
        try:
            snap = json.loads(raw or "")
            ts = datetime.fromisoformat(snap["ts"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - ts > timedelta(
                    seconds=int(snap.get("ttl") or 0)):
                return []
            items = snap.get("items")
            if items is None:   # snapshot written before identities were pinned
                items = [{"id": int(i), "no": None} for i in snap.get("ids") or []]
            items = [{"id": int(it["id"]), "no": it.get("no")} for it in items]
        except (KeyError, TypeError, ValueError):
            return []
        slots = []
        for item in items:
            row = store.get_message(self.conn, item["id"])
            # Rowid reuse: the row answering to this id now may be a DIFFERENT
            # note. He named a shown item — a substitute is never the answer.
            if row is not None and item["no"] is not None and row["note_no"] != item["no"]:
                row = None
            slots.append({"id": item["id"], "no": item["no"], "row": row})
        return slots

    def _review_snapshot_set(self, ids, ttl_seconds):
        """Pin the review batch (see `_pin_shown`)."""
        self._pin_shown("note_review_snapshot", ids, ttl_seconds)

    def _review_snapshot_rows(self, keep_gaps=False):
        """Live snapshot rows (ordinal follow-ups resolve against WHAT WAS SHOWN,
        never a recomputed list). Empty when absent/expired. With `keep_gaps` the
        list stays POSITIONAL — a since-deleted row keeps its slot as None, so
        «третье» still means the third item he was shown."""
        rows = [slot["row"] for slot in self._shown_slots("note_review_snapshot")]
        return rows if keep_gaps else [r for r in rows if r is not None]

    # -- C3: the list she JUST showed ------------------------------------------
    # «Покажи год каждого» right after a 3-item Movies listing answered with ONE
    # note's full detail card. A field follow-up has to be answered against the
    # list ON HIS SCREEN, so the listing pins what it rendered — the review
    # snapshot's mechanism, second customer. Only a DELIVERED list is pinned:
    # answering about a list that never arrived is the same substitution.
    #
    # It describes the list ON HIS SCREEN, so it is short-lived and it is DROPPED
    # the moment another view replaces it (review fix 2026-07-28 — a journal
    # page, a reminder list or a detail card used to leave an hour-old notes pin
    # live, and `list_field` would then answer about it under a header that says
    # «по каждому из последнего списка»). Ten minutes is "just now"; past that,
    # `list_field_no_list` is the honest answer.
    SHOWN_LIST_TTL_SECONDS = 600

    def _shown_list_set(self, ids):
        self._pin_shown("shown_list_snapshot", ids, self.SHOWN_LIST_TTL_SECONDS)

    def _shown_list_clear(self):
        """Another view took the screen — there is no "last list" any more."""
        store.kv_set(self.conn, "shown_list_snapshot", "")

    def _shown_list_slots(self):
        return self._shown_slots("shown_list_snapshot")

    def _rows_from_review_snapshot(self, text):
        """Resolve «второе / первую / все» against the live review snapshot.
        None = no snapshot claim (fall through to normal resolution); [] = he
        named a shown item that no longer exists (not-found, never a substitute)."""
        slots = self._review_snapshot_rows(keep_gaps=True)
        if not slots:
            return None          # no live snapshot at all -> no claim on this text
        t = str(text or "").casefold()
        # «всё» is a different string from «все» (ё), and «эти»/«их»/"them" are
        # how he actually points at the batch — any of them missing here made
        # «всё в архив» fall through to resolve_items({}) and archive the
        # NEWEST note in the inbox, unconfirmed (2026-07-27).
        if re.search(r"\bвс[её]\b|\ball\b|\bthem\b|\bобе\b|\bоба\b|\bэти\b|\bих\b", t):
            return [r for r in slots if r is not None]
        for stem, pos in self._ORDINALS.items():
            if stem in t:
                # Ordinals are POSITIONAL against the ORIGINAL snapshot: compacting
                # deleted rows out shifted them, so «третье» archived the 4th note.
                # Out of range, or the shown row is gone -> not-found. He NAMED a
                # shown item; the newest note is not an acceptable substitute.
                row = slots[pos - 1] if pos <= len(slots) else None
                return [row] if row is not None else []
        return None

    def do_note_review(self, chat_id, lang, params=None, preset_ids=None):
        """«Покажи, что стоит пересмотреть»: at most three snapshotted items,
        each with a deterministic reason. Suggestion-only — every action goes
        through the normal note_lifecycle/delete flows.

        Returns the ids it actually RENDERED (empty when nothing was shown or
        the reply was not delivered), so a caller that wants a different TTL
        re-stamps that exact list instead of re-deriving one: a snapshot must
        never name a note the card did not list."""
        ru = lang == "ru"
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        shown_key = f"note_review_shown:{day}"
        # Shown-today entries are PINNED {id, no} pairs, like the snapshot:
        # a bare rowid list meant «delete the highest-rowid shown note, save a
        # new one» silently excluded the BRAND-NEW note from every review batch
        # for the rest of the day (SQLite reuses the highest rowid). An entry
        # whose note_no no longer matches (or whose row is gone) is dropped —
        # it describes a note that no longer answers to that id (2026-07-27).
        try:
            raw = json.loads(store.kv_get(self.conn, shown_key) or "[]")
        except (TypeError, ValueError):
            raw = []
        shown_pins = {}
        if isinstance(raw, list):
            for it in raw:
                try:
                    if isinstance(it, dict):
                        mid, no = int(it["id"]), it.get("no")
                    else:   # unpinned entry written before this fix, same day
                        mid, no = int(it), None
                except (KeyError, TypeError, ValueError):
                    continue
                row = store.get_message(self.conn, mid)
                if row is None or (no is not None and row["note_no"] != no):
                    continue
                shown_pins[mid] = no if no is not None else row["note_no"]
        shown = list(shown_pins)
        if preset_ids:
            batch = []
            for mid in preset_ids[:3]:
                row = store.get_message(self.conn, mid)
                if row is not None and row["knowledge_state"] is not None:
                    batch.append((row, "review_due" if row["review_at"] else "inbox"))
        else:
            batch = store.notes_review_candidates(self.conn, exclude_ids=shown)
        if not batch:
            self.reply(chat_id, T(lang, "note_review_empty"))
            return []
        lines = [T(lang, "note_review_header", n=len(batch))]
        for i, (row, reason) in enumerate(batch, 1):
            no = self.note_no(row["id"])
            cat = row["category"] or row["suggested_category"] or "?"
            preview = self._ellipsize(row["summary"] or row["raw_text"] or "", 90)
            label = self._REVIEW_REASONS.get(reason, (reason, reason))[0 if ru else 1]
            lines.append(f"{i}. #{no} · {cat} — {preview}\n   ↳ {label}")
        lines.append(T(lang, "note_review_footer"))
        if not self.reply(chat_id, "\n\n".join(lines)):
            return []  # not delivered -> no snapshot, no shown-marking
        ids = [row["id"] for row, _ in batch]
        self._review_snapshot_set(ids, ttl_seconds=24 * 3600)
        # A review card IS a list on his screen, so «а годы?» right after one
        # answers about these three notes (C3). Its own snapshot lives a day
        # (ordinals must survive «второе в архив» tomorrow); the shown-list pin
        # is short-lived on purpose — it only ever answers an immediate
        # follow-up, and a stale one would answer about yesterday's screen.
        self._shown_list_set(ids)
        for mid in ids:   # every rendered row got its #N above (self.note_no)
            row = store.get_message(self.conn, mid)
            shown_pins[mid] = row["note_no"] if row is not None else None
        store.kv_set(self.conn, shown_key, json.dumps(
            [{"id": mid, "no": shown_pins[mid]} for mid in sorted(shown_pins)]))
        events.record_done(self.conn, "note_review_shown", chat_id=chat_id,
                           payload={"ids": ids})
        return ids

    _LIFECYCLE_OPS = ("archive", "restore", "keep", "set_purpose", "review_later",
                      "make_temporary")
    _LIFECYCLE_EVENT = {"archive": "note_archived", "restore": "note_restored",
                        "keep": "note_kept", "set_purpose": "note_triaged",
                        "review_later": "note_review_deferred",
                        "make_temporary": "note_triaged"}
    _PURPOSE_LABELS = {
        "reference": ("справка", "reference"), "source": ("источник", "source"),
        "idea": ("идея", "idea"), "decision": ("решение", "decision"),
        "temporary": ("временная", "temporary"),
        "actionable": ("требует действия", "actionable"),
    }

    def do_note_lifecycle(self, chat_id, lang, params, text=""):
        """Reversible note triage: archive/restore/keep/purpose/review/temporary.
        Single ops run directly (the reply carries the undo); a BULK archive is
        staged behind a pending confirm, like item_delete. Never deletes.
        A bare ordinal («второе в архив») right after a note review resolves
        against the SNAPSHOT of what was shown, never a recomputed list."""
        op = str(params.get("operation") or "").strip().lower()
        if op not in self._LIFECYCLE_OPS:
            self.reply(chat_id, T(lang, "clarify"))
            return
        rows = None
        if not any(params.get(k) for k in ("id", "ids", "query", "count")):
            rows = self._rows_from_review_snapshot(text)
        if rows is None:
            rows = self.resolve_items(params)
        if not rows:
            self.reply(chat_id, T(lang, "items_empty"))
            return
        if op == "archive" and len(rows) > 1:
            ids = [r["id"] for r in rows]
            store.pending_set(self.conn, chat_id, "note_archive", {"row_ids": ids})
            listing = ", ".join(f"#{self.note_no(i)}" for i in ids)
            self.reply(chat_id, T(lang, "note_archive_confirm_multi",
                                  n=len(ids), ids=listing))
            return
        purpose = str(params.get("purpose") or "").strip().lower()
        when = reminders.parse_iso_utc(params.get("when"))
        for row in rows:
            no = self.note_no(row["id"])
            if op == "archive":
                ok = store.note_archive(self.conn, row["id"], reason="archived by boss")
                key, kw = "note_archived", {"row_id": no}
            elif op == "restore":
                ok = store.note_restore(self.conn, row["id"])
                key, kw = "note_restored", {"row_id": no}
            elif op == "keep":
                ok = store.note_keep(self.conn, row["id"])
                key, kw = "note_kept", {"row_id": no}
            elif op == "set_purpose":
                ok = store.note_set_purpose(self.conn, row["id"], purpose)
                if not ok and purpose not in store.NOTE_PURPOSES:
                    self.reply(chat_id, T(lang, "clarify"))  # unknown purpose word
                    return
                label = self._PURPOSE_LABELS.get(purpose, (purpose, purpose))
                key, kw = "note_purpose_set", {"row_id": no,
                                               "purpose": label[0 if lang == "ru" else 1]}
            elif op == "review_later":
                due = when or (datetime.now(timezone.utc) + timedelta(days=7))
                ok = store.note_set_review(self.conn, row["id"], due.isoformat())
                key, kw = "note_review_set", {
                    "row_id": no,
                    "when_local": reminders.fmt_local(due.isoformat(), self.tz_offset())}
            else:  # make_temporary — advisory expiry, never an auto-delete
                due = when or (datetime.now(timezone.utc) + timedelta(days=30))
                ok = store.note_make_temporary(self.conn, row["id"], due.isoformat())
                key, kw = "note_temporary_set", {
                    "row_id": no,
                    "when_local": reminders.fmt_local(due.isoformat(), self.tz_offset())}
            if ok:
                events.record_done(self.conn, self._LIFECYCLE_EVENT[op],
                                   chat_id=chat_id, payload={"message_id": row["id"]})
                self.reply(chat_id, T(lang, key, **kw))
                log(f"note_lifecycle {op} #{no} (id={row['id']})")
            else:
                # journal entries / failed rows live outside note lifecycle
                self.reply(chat_id, T(lang, "note_lifecycle_na", row_id=no))

    def do_item_delete(self, chat_id, lang, params, text=""):
        # Same snapshot rule as do_note_lifecycle: right after a review card,
        # «второе удали» names the second SHOWN note. Skipping this check here
        # made the ordinal the review card teaches him mean one thing on the
        # archive path and «the newest note» on the delete path (2026-07-27).
        rows = None
        if not any(params.get(k) for k in ("id", "ids", "query", "count")):
            rows = self._rows_from_review_snapshot(text)
        if rows is None:
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
