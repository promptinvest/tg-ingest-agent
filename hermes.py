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

import reminders
import store
from texts import T

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

    def do_report_problem(self, chat_id, lang, params, text):
        """Record a boss-reported problem ('запиши в проблемы', 'добавь в
        ошибки') in the issues log so it surfaces in the weekly review —
        distinct from issues_report, which only shows the report."""
        detail = str(params.get("detail") or "").strip() or (text or "").strip()
        store.issue_add(self.conn, chat_id, "boss_reported", detail)
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
