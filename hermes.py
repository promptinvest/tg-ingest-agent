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
    """Cara's business handlers (reminders / journals / problem log). Mixed into the
    Agent, so `self` is the Agent — every `self.reply`/`self.conn`/`self.reminder_no`/
    `self.tz_offset()` resolves on it exactly as before. Pure relocation, no behaviour
    change. (Other business areas — notes, KB, spend, fetch, review — still live on the
    Agent and move here in later stages.)"""

    def do_reschedule(self, chat_id, lang, params, text=None):
        """Move an existing reminder to a new time (applied immediately, like
        cancel). Targets by id/title; a bare 'это/последнее' reference uses the
        sole active reminder, but never silently picks one when an explicit
        id/title was given but matched nothing (that moved the wrong reminder)."""
        due = reminders.parse_iso_utc(params.get("due_utc"))
        if due is None:
            self.reply(chat_id, T(lang, "reschedule_when"))
            return
        # A reschedule must land in the FUTURE — if the parsed time is already past (a
        # misparsed 'today' at a late hour), roll it to the next occurrence of that local
        # time so it doesn't immediately re-fire (the 'rescheduled into the past' bug).
        now = datetime.now(timezone.utc)
        if due <= now:
            due = reminders.roll_forward(due, now)
        # Same op on SEVERAL reminders ("перенеси первые две / #1 и #2 / обе / все на 17:00")
        # — one reschedule across multiple targets, NOT a per-one back-and-forth.
        active = store.reminders_active(self.conn, chat_id)
        targets = []
        if params.get("all"):
            targets = list(active)
        else:
            ids = params.get("ids")
            if isinstance(ids, list) and len(ids) > 1:
                for i in ids:
                    r = reminders.find_by_query(active, {"id": i})  # display position
                    if r is not None and r["id"] not in {t["id"] for t in targets}:
                        targets.append(r)
        if len(targets) > 1:
            for r in targets:
                store.reminder_update_due(self.conn, r["id"], due.isoformat())
            self._remember_reminder(targets[-1]["id"])
            self.reply(chat_id, T(lang, "reminders_rescheduled_multi", n=len(targets),
                                  when_local=reminders.fmt_local(due.isoformat(), self.tz_offset())))
            return
        row = self._resolve_reminder_target(
            chat_id, lang, params,
            op={"op": "reschedule", "due_utc": due.isoformat(), "text": text})
        if row is None:
            return  # _resolve_reminder_target already replied (not found / which?)
        store.reminder_update_due(self.conn, row["id"], due.isoformat())
        self._remember_reminder(row["id"])
        self.reply(chat_id, T(lang, "reminder_rescheduled",
                              rid=self.reminder_no(chat_id, row["id"]), title=row["title"],
                              when_local=reminders.fmt_local(due.isoformat(), self.tz_offset())))

    def do_rename_reminder(self, chat_id, lang, params, text=None):
        """Retitle an existing reminder IN PLACE (keeps id/time/recurrence/history).
        The new name is params['new_title']; the target is resolved by id/title_query/
        'это' through the shared guard, so it never renames the wrong reminder. (Note
        targeting reads title_query/title, never new_title, so the new name can't be
        mistaken for the target.)"""
        new_title = str(params.get("new_title") or "").strip()
        if not new_title and params.get("id") is not None:
            # target given by number -> a stray 'title' must be the NEW name
            new_title = str(params.get("title") or "").strip()
        new_title = new_title[:reminders.MAX_TITLE_CHARS]
        if not new_title:
            self.reply(chat_id, T(lang, "reminder_rename_what"))
            return
        row = self._resolve_reminder_target(
            chat_id, lang, params, op={"op": "rename", "new_title": new_title, "text": text})
        if row is None:
            return  # _resolve_reminder_target already replied (not found / which?)
        store.reminder_rename(self.conn, row["id"], new_title)
        self._remember_reminder(row["id"])
        self.reply(chat_id, T(lang, "reminder_renamed",
                              rid=self.reminder_no(chat_id, row["id"]), title=new_title))

    def start_partial_reminder(self, chat_id, lang, params):
        """A reminder_create missing the subject or the time: keep whatever the
        boss gave and ask for the rest, instead of dropping it to a generic
        clarify (which lost 'напомни в 17:00' entirely)."""
        now = datetime.now(timezone.utc)
        draft = {"recurrence": "none"}
        title = str(params.get("title") or "").strip()
        if title:
            draft["title"] = title[:reminders.MAX_TITLE_CHARS]
        due = reminders.parse_iso_utc(params.get("due_utc"))
        if due is not None and due >= now - timedelta(minutes=1):
            draft["due_utc"] = due.isoformat()
        rec = str(params.get("recurrence") or "").strip().lower()
        if rec in reminders.RECURRENCES:
            draft["recurrence"] = rec
        if not draft.get("title") and not draft.get("due_utc"):
            self.reply(chat_id, T(lang, "clarify"))  # nothing to anchor on
            return
        need = "title" if not draft.get("title") else "time"
        draft["need"] = need
        store.pending_set(self.conn, chat_id, "reminder_partial", draft)
        self.reply(chat_id, T(lang, "reminder_need_" + need))

    def continue_partial_reminder(self, chat_id, lang, pending, action, params):
        """Stitch a missing field into a half-specified reminder. Returns True if
        the message completed/continued the draft (or cancelled it), False if it
        is an unrelated intent (the partial is then abandoned and falls through)."""
        if action == "cancel":
            store.pending_clear(self.conn, chat_id)
            self.reply(chat_id, T(lang, "reminder_partial_cancelled"))
            return True
        if action not in ("amend", "confirm"):
            store.pending_clear(self.conn, chat_id)  # boss moved on to something else
            return False
        draft = {k: v for k, v in pending["payload"].items()
                 if k in ("title", "due_utc", "recurrence")}
        title = str(params.get("title") or "").strip()
        if title and not draft.get("title"):
            draft["title"] = title[:reminders.MAX_TITLE_CHARS]
        due = reminders.parse_iso_utc(params.get("due_utc"))
        if due is not None and not draft.get("due_utc"):
            draft["due_utc"] = due.isoformat()
        rec = str(params.get("recurrence") or "").strip().lower()
        if rec in reminders.RECURRENCES:
            draft["recurrence"] = rec
        full = reminders.validate_draft(draft)
        if full:
            store.pending_set(self.conn, chat_id, "reminder", full)
            self.reply(chat_id, T(lang, "reminder_draft", title=full["title"],
                       when_local=reminders.fmt_local(full["due_utc"], self.tz_offset()),
                       recurrence=T(lang, "recurrence_" + full["recurrence"])))
        else:
            need = "title" if not draft.get("title") else "time"
            draft["need"] = need
            store.pending_set(self.conn, chat_id, "reminder_partial", draft)
            self.reply(chat_id, T(lang, "reminder_need_" + need))
        return True

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

    def _note_reminder_title(self, params):
        """'поставь напоминание по заметке N' arrives with note_id and no real
        subject (the router otherwise titles it literally 'Заметка N'); use the
        note's actual subject instead. The boss's own title always wins."""
        note_id = params.get("note_id")
        if note_id is None:
            return params
        title = str(params.get("title") or "").strip()
        if title and not re.fullmatch(r"(?:заметк\w*|запис\w*|note|item|#)?\s*#?\d{1,7}",
                                      title, re.IGNORECASE):
            return params  # a meaningful subject was given — keep it
        note = self.resolve_item({"id": note_id})
        if note is None:
            return params
        subject = (note["summary"] or note["raw_text"] or note["category"]
                   or note["suggested_category"] or "").strip()
        subject = subject.splitlines()[0][:80].strip() if subject else ""
        if subject:
            params = dict(params)
            params["title"] = subject
        return params

    def do_reminder_undo(self, chat_id, lang, params):
        """Undo the last reschedule ('верни предыдущее время', 'отмени перенос')
        by swapping due_utc back to the remembered previous time."""
        rows = store.reminders_active(self.conn, chat_id)
        if not rows:
            self.reply(chat_id, T(lang, "reminder_not_found"))
            return
        row = reminders.find_by_query(rows, params)
        if row is None:
            moved = [r for r in rows if r["prev_due_utc"]]
            if len(moved) == 1:
                row = moved[0]
            elif len(moved) > 1:
                # Show the full active list so the numbers match how a typed "#N"
                # resolves (position in the active list, not within the subset).
                self.reply(chat_id, T(lang, "reschedule_which") + "\n"
                           + reminders.format_list(rows, self.tz_offset(), lang))
                return
            else:
                self.reply(chat_id, T(lang, "reminder_no_prev"))
                return
        prev = store.reminder_restore_due(self.conn, row["id"])
        if prev is None:
            self.reply(chat_id, T(lang, "reminder_no_prev"))
            return
        self.reply(chat_id, T(lang, "reminder_restored",
                              rid=self.reminder_no(chat_id, row["id"]), title=row["title"],
                              when_local=reminders.fmt_local(prev, self.tz_offset())))

    def _remember_reminder(self, rid):
        """Track the reminder the boss is dealing with right now, so a later bare
        'это напоминание' (reschedule/rename) binds to it instead of guessing (B3)."""
        store.kv_set(self.conn, "last_reminder_id", str(rid))

    _ORDINALS = {"перв": 1, "втор": 2, "трет": 3, "четвёрт": 4, "четверт": 4, "пят": 5,
                 "шест": 6, "седьм": 7, "first": 1, "second": 2, "third": 3,
                 "fourth": 4, "fifth": 5}

    def _parse_reminder_selector(self, text, rows):
        """Map the boss's disambiguation answer to one of `rows` (display order):
        a number / '#2', an ordinal word ('второе'), or a title word ('про банк')."""
        t = (text or "").strip().casefold()
        if not t or not rows:
            return None
        m = re.search(r"#?\s*(\d{1,3})", t)
        if m:
            n = int(m.group(1))
            if 1 <= n <= len(rows):
                return rows[n - 1]
        for stem, n in self._ORDINALS.items():
            if stem in t and 1 <= n <= len(rows):
                return rows[n - 1]
        for r in rows:  # a word of the title appearing in his answer ('про банк')
            words = [w for w in re.split(r"\W+", r["title"].casefold()) if len(w) >= 3]
            if any(w in t for w in words):
                return r
        return None

    def _resolve_reminder_op(self, chat_id, lang, pending, text):
        """Apply a remembered reschedule/rename to the reminder the boss just picked
        (pending reminder_op). Returns True if handled; False if his message isn't a
        pick (caller then abandons the pending and routes the message normally)."""
        payload = pending["payload"]
        ids = payload.get("ids") or []
        rows = [r for r in store.reminders_active(self.conn, chat_id) if r["id"] in ids]
        row = self._parse_reminder_selector(text, rows)
        if row is None:
            return False
        store.pending_clear(self.conn, chat_id)
        op = payload.get("op")
        if op == "reschedule":
            due = reminders.parse_iso_utc(payload.get("due_utc"))
            if due is None:
                self.reply(chat_id, T(lang, "reschedule_when"))
                return True
            store.reminder_update_due(self.conn, row["id"], due.isoformat())
            self._remember_reminder(row["id"])
            self.reply(chat_id, T(lang, "reminder_rescheduled",
                                  rid=self.reminder_no(chat_id, row["id"]), title=row["title"],
                                  when_local=reminders.fmt_local(due.isoformat(), self.tz_offset())))
            return True
        if op == "rename":
            new_title = str(payload.get("new_title") or "").strip()[:reminders.MAX_TITLE_CHARS]
            if not new_title:
                self.reply(chat_id, T(lang, "reminder_rename_what"))
                return True
            store.reminder_rename(self.conn, row["id"], new_title)
            self._remember_reminder(row["id"])
            self.reply(chat_id, T(lang, "reminder_renamed",
                                  rid=self.reminder_no(chat_id, row["id"]), title=new_title))
            return True
        return False

    def _resolve_reminder_target(self, chat_id, lang, params, op=None):
        """Resolve which active reminder a reschedule/rename/undo refers to. Returns
        the row, or None after replying. A bare 'это' binds to the last reminder he
        touched (B3); when that's absent and several are active, and `op` is given, it
        remembers the operation as a `reminder_op` pending so his next pick completes
        it (B2) — instead of losing the time/new-title to a fresh route."""
        rows = store.reminders_active(self.conn, chat_id)
        if not rows:
            self.reply(chat_id, T(lang, "reminder_not_found"))
            return None
        has_target = params.get("id") is not None or (params.get("title_query")
                                                       or params.get("title"))
        row = reminders.find_by_query(rows, params)
        if row is None and has_target:
            # Explicit id/title given but nothing active matched — do NOT move an
            # unrelated reminder; show what IS active so the boss can pick.
            self.reply(chat_id, T(lang, "reminder_not_found") + "\n"
                       + reminders.format_list(rows, self.tz_offset(), lang))
            return None
        if row is None and op is not None and op.get("text") and len(rows) > 1:
            # An ordinal word ("первое"/"второе"/"third") names a POSITION in the shown
            # list — resolve it BEFORE the bare last-touched fallback, so "перенеси второе"
            # moves the 2nd reminder, not the one he just touched. (Only ordinal STEMS, not
            # bare numbers — a time like "12:15" must not be read as reminder #12.)
            t = (op.get("text") or "").casefold()
            for stem, n in self._ORDINALS.items():
                if stem in t and 1 <= n <= len(rows):
                    return rows[n - 1]
        if row is None:  # bare "это/последнее/это напоминание" reference
            last_id = store.kv_get(self.conn, "last_reminder_id")
            if last_id:
                match = next((r for r in rows if str(r["id"]) == str(last_id)), None)
                if match is not None:
                    return match
            if len(rows) == 1:
                return rows[0]
            if op is not None:  # remember the op so his next pick ('второе'/'#2'/'про X') completes it
                store.pending_set(self.conn, chat_id, "reminder_op",
                                  {**op, "ids": [r["id"] for r in rows]})
            self.reply(chat_id, T(lang, "reschedule_which") + "\n"
                       + reminders.format_list(rows, self.tz_offset(), lang))
            return None
        return row

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
        dmap = store.display_map(self.conn)
        for row in rows:
            row_category = row["category"] or row["suggested_category"] or (
                "без категории" if ru else "uncategorized")
            pending = " ⏳" if row["status"] != "confirmed" else ""
            item = [f"📄 #{dmap.get(row['id'], row['id'])} · {row_category}{pending}"]
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
            blocks.append("\n".join(item))
        blocks.append(T(lang, "items_footer"))
        return "\n\n".join(blocks)

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
                row = store.message_by_display_no(self.conn, i)  # user numbers are display 1..N
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
        """Resolve an item by user-facing note number (display 1..N),
        query/category, or most recent."""
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
            row = store.message_by_display_no(self.conn, no)
            if row is not None:
                return row
        rows = store.list_messages(self.conn, params.get("category"), params.get("query"), limit=1)
        return rows[0] if rows else None

    def note_no(self, message_id):
        """User-facing display number (1..N) for a note id; falls back to the id
        for a not-yet-visible/transient row (e.g. a pending failed ingest)."""
        n = store.display_no(self.conn, message_id)
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
        dmap = store.display_map(self.conn)
        lines = [T(lang, "files_header", n=len(rows))]
        for r in rows:
            cat = r["category"] or r["suggested_category"] or ("без категории" if ru else "uncategorized")
            name = r["file_name"] or ("файл" if ru else "file")
            lines.append(f"📎 {name} — #{dmap.get(r['message_id'], r['message_id'])} · {cat}")
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

    # -- reminders firing/expiry + spend/review/export (stage 4) --------------

    def fire_due_reminders(self):
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        in_quiet = proactive.in_quiet_hours(self.cfg, self.conn, now)
        in_meeting = self._in_social_meeting()
        in_window = self._recent_intimate_msg(now)
        for row in store.reminders_due(self.conn, now_iso):
            # Hold for the WHOLE date and through quiet hours (never lost — fires once the
            # meeting ends / the window closes). A live meeting holds indefinitely; a bare
            # intimate-message window holds only up to the max-defer so nothing's stranded.
            if in_meeting or in_quiet:
                continue
            if in_window:
                due = reminders.parse_iso_utc(row["due_utc"])
                if due is not None and (now - due).total_seconds() < \
                        self.cfg.reminder_max_defer_hours * 3600:
                    continue
            lang = self.lang()
            self.reply(row["chat_id"], T(lang, "reminder_fired",
                                         name=self.owner_name(), title=row["title"]))
            store.pending_set(
                self.conn, row["chat_id"], "reminder_fired",
                {"reminder_id": row["id"], "title": row["title"]}, ttl_seconds=1800,
            )
            following = reminders.next_due(row["due_utc"], row["recurrence"])
            if following:
                store.reminder_update_due(self.conn, row["id"], following)  # recurring: re-arm
            # B5: a fired ONE-SHOT is NOT auto-closed — it stays active/visible until the
            # boss explicitly acks ('готово') or cancels it; last_fired_at stops it
            # re-firing. (Old behavior closed it here, which read as 'why did you close it'.)
            store.reminder_touch_fired(self.conn, row["id"])
            self._remember_reminder(row["id"])  # "готово/перенеси это" binds to the just-fired one
            log(f"reminder #{row['id']} fired")

    def check_reminder_expiry(self):
        """Auto-close fired one-shot reminders left unacked past the expiry window, so the
        'ждёт готово' list doesn't pile up forever. (0 disables.)"""
        days = self.cfg.reminder_fired_expire_days
        if not days or days <= 0:
            return
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        try:
            n = store.reminders_expire_stale(self.conn, cutoff)
        except Exception as exc:  # noqa: BLE001 — must not kill the loop
            log(f"reminder expiry sweep error: {exc!r}")
            return
        if n:
            log(f"auto-expired {n} stale fired reminder(s)")

    def reminder_no(self, chat_id, rid):
        """User-facing display number (1..N) for an active reminder; falls back
        to the id if it isn't active (already fired/cancelled)."""
        n = store.reminder_display_no(self.conn, chat_id, rid)
        return n if n is not None else rid

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
