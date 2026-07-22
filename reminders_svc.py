#!/usr/bin/env python3
"""Reminders domain for Cara — handlers, disambiguation/resolvers, and the scheduler
fire/expiry sweeps, gathered out of the Agent/Hermes into one labelled module.

`ReminderMixin` is mixed into the Agent (`class Agent(..., reminders_svc.ReminderMixin)`),
so `self` is the Agent: `self.reply`/`self.conn`/`self.cfg`/`self.tz_offset()`/`self.owner_name()`/
`self._recent_boss_msg`/`self.resolve_item` all resolve on it exactly as before. Pure relocation,
no behaviour change — the reminder subsystem now lives together. Stateless parsing/formatting
helpers stay in `reminders.py`; the thin create/list/cancel/calendar handlers stay on the Agent.
"""
import re
from datetime import datetime, timedelta, timezone

import reminders
import store
from common import log
from texts import T


class ReminderMixin:
    """Reminder handlers + resolvers + fire/expiry sweeps. Mixed into the Agent."""

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
        # Same past-time filter as start_partial_reminder — and a fresh valid time
        # always WINS over a stored one: without both, one past-parsed «в 9» wedged a
        # dead due_utc into the draft that no later correction could replace (the
        # draft then failed validation forever, re-asking for the time in a loop).
        due = reminders.parse_iso_utc(params.get("due_utc"))
        if due is not None and due >= datetime.now(timezone.utc) - timedelta(minutes=1):
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

    def continue_partial_reminder_from_forward(self, chat_id, lang, pending, text):
        """Use a forward as DATA for a title only after the boss opened the draft.

        A standalone forward is still inbox content.  This bridge is deliberately
        limited to a partial that already has a time and explicitly needs a title;
        the resulting full draft still requires the normal owner confirmation.
        """
        if not pending or pending.get("kind") != "reminder_partial":
            return False
        payload = pending.get("payload") or {}
        if payload.get("need") != "title" or not payload.get("due_utc"):
            return False
        title = reminders.title_from_forward(text)
        if not title:
            return False
        return self.continue_partial_reminder(
            chat_id, lang, pending, "amend", {"title": title})

    def _note_reminder_title(self, params):
        """'поставь напоминание по заметке N' arrives with note_id and no real
        subject (the router otherwise titles it literally 'Заметка N'); use the
        note's actual subject instead. The boss's own title always wins."""
        note_id = params.get("note_id")
        if note_id is None:
            return params
        note = self.resolve_item({"id": note_id})
        if note is None:
            return params
        params = dict(params)
        # Keep the note→reminder link (DB id) for the saved-to-used outcome
        # metrics (MET-001): the commit point records note_reminder_created.
        params["note_msg_id"] = note["id"]
        title = str(params.get("title") or "").strip()
        if title and not re.fullmatch(r"(?:заметк\w*|запис\w*|note|item|#)?\s*#?\d{1,7}",
                                      title, re.IGNORECASE):
            return params  # a meaningful subject was given — keep it
        subject = (note["summary"] or note["raw_text"] or note["category"]
                   or note["suggested_category"] or "").strip()
        subject = subject.splitlines()[0][:80].strip() if subject else ""
        if subject:
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

    def _parse_fired_followup(self, text, *, allow_bare_ack=False, title=""):
        """Deterministic common acknowledgements/snoozes for a fired reminder.

        Returns (action, params), or None when the message is substantive or
        ambiguous and should continue through normal routing.
        """
        t = str(text or "").strip().casefold()
        if not t or len(t) > 90:
            return None
        if any(w in t for w in ("запиш", "сохран", "добав", "заметк", "note", "save")):
            return None
        # CORE guard (2026-07-22 incident): a follow-up never introduces its OWN
        # subject. «Поставь напоминание на завтра 10:30 - Эрика» is a NEW
        # reminder about Эрика — matching just «завтра … 10:30» used to eat it
        # as a snooze of the last-fired reminder and silently drop the subject.
        # Any content word that is neither follow-up scaffold (verb / reminder
        # reference / time) nor part of the bound reminder's title sends the
        # message to the normal router instead.
        if reminders.followup_extra_words(t, title):
            return None
        if "пропуст" in t or "пропуск" in t or "skip today" in t:
            return "amend", {"done": True}
        if re.fullmatch(r"(?:закрой|закрыть|готово|сделано|выполнено|done|close|closed)[.! ]*", t):
            return "confirm", {}
        if allow_bare_ack and re.fullmatch(r"(?:да|yes|yep|ага|ок|okay|ok|\+|✅|👍)[.! ]*", t):
            return "confirm", {}
        # «завтра…» MUST be checked before the relative-duration regexes: in
        # «давай завтра в 10 часов» the hours pattern would otherwise eat «10
        # часов» first and silently re-arm the alarm 10 hours from now (~06:00)
        # instead of tomorrow 10:00.
        if "завтра" in t or "tomorrow" in t:
            tm = re.search(r"(?:в|на|at)?\s*(\d{1,2})(?::(\d{2}))?\b", t)
            hour = max(0, min(23, int(tm.group(1)))) if tm else 9
            minute = max(0, min(59, int(tm.group(2) or 0))) if tm else 0
            local_now = datetime.now(timezone.utc) + timedelta(hours=self.tz_offset())
            local_due = datetime.combine(local_now.date() + timedelta(days=1),
                                         datetime.min.time(), tzinfo=timezone.utc)
            local_due = local_due.replace(hour=hour, minute=minute)
            due = local_due - timedelta(hours=self.tz_offset())
            return "amend", {"due_utc": due.isoformat()}
        # A bare absolute-clock snooze is common boss language: «отложи на 12»
        # means 12:00 LOCAL TODAY, not 12 minutes/hours from now and not a trip
        # through the probabilistic router. If today's clock time already passed,
        # fail closed with a clarification instead of silently rolling tomorrow.
        absolute = re.fullmatch(
            r"(?:отложи|перенеси|напомни)(?:\s+(?:это|напоминание|его))?\s+"
            r"(?:на|до)\s+(\d{1,2})(?::(\d{2}))?"
            r"(?:\s*(?:час(?:а|ов)?|ч))?[.! ]*",
            t,
        )
        if absolute is None:
            absolute = re.fullmatch(
                r"(?:snooze|remind me|move it)(?:\s+(?:until|to|at))\s+"
                r"(\d{1,2})(?::(\d{2}))?[.! ]*",
                t,
            )
        if absolute is not None:
            hour = int(absolute.group(1))
            minute = int(absolute.group(2) or 0)
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                return None
            local_now = datetime.now(timezone.utc) + timedelta(hours=self.tz_offset())
            local_due = datetime.combine(local_now.date(), datetime.min.time(),
                                         tzinfo=timezone.utc).replace(
                                             hour=hour, minute=minute)
            if local_due <= local_now:
                return "clarify_time", {"time": f"{hour:02d}:{minute:02d}"}
            due = local_due - timedelta(hours=self.tz_offset())
            return "amend", {"due_utc": due.isoformat()}
        if "полчас" in t or "half an hour" in t:
            return "amend", {"snooze_minutes": 30}
        m = re.search(r"(\d{1,4})\s*(минут|минуты|минуту|мин|minutes?|mins?)\b", t)
        if m:
            return "amend", {"snooze_minutes": max(1, int(m.group(1)))}
        m = re.search(r"(\d{1,3})\s*(часа|часов|час|ч|hours?|hrs?)\b", t)
        if m:
            return "amend", {"snooze_minutes": max(1, int(m.group(1))) * 60}
        if re.search(r"(?:на|ещ[ёе])\s+час(?:ок)?\b|\ban hour\b", t):
            return "amend", {"snooze_minutes": 60}
        return None

    # A RECURRING reminder auto-advances at fire — it has no lingering
    # awaiting-ack state, so late binding via last_reminder_id (after the
    # 30-min pending expires) only makes sense shortly after the fire. Outside
    # this window a «завтра в 10»-shaped message belongs to the router. Fired
    # ONE-SHOTS genuinely stay open until «готово», so they keep the unbounded
    # explicit-followup binding.
    RECURRING_FOLLOWUP_WINDOW = timedelta(hours=3)

    def resolve_fired_followup(self, chat_id, lang, text, pending):
        """Handle a fired-reminder reply before the probabilistic router.

        The pending confirmation may expire after 30 minutes, but a fired
        one-shot remains open; an explicit close/skip/snooze can still bind to
        last_reminder_id (a recurring one only within the recency window).
        Bare yes/ok requires the live pending context.
        """
        live_pending = pending if pending and pending.get("kind") == "reminder_fired" else None
        context = live_pending
        if context is None:
            last_id = store.kv_get(self.conn, "last_reminder_id")
            try:
                row = store.reminder_get(self.conn, int(last_id)) if last_id is not None else None
            except (TypeError, ValueError):
                row = None
            if (row is None or row["status"] != "active" or row["last_fired_at"] is None):
                return False
            if row["recurrence"] != "none":
                fired = reminders.parse_iso_utc(row["last_fired_at"])
                if fired is None or (datetime.now(timezone.utc) - fired
                                     > self.RECURRING_FOLLOWUP_WINDOW):
                    return False
            context = {"kind": "reminder_fired",
                       "payload": {"reminder_id": row["id"], "title": row["title"]}}
        parsed = self._parse_fired_followup(
            text, allow_bare_ack=live_pending is not None,
            title=str(context["payload"].get("title") or ""))
        if parsed is None:
            return False
        action, params = parsed
        if action == "clarify_time":
            # Remove the short yes/no pending: after this clarification a bare
            # «да» must not accidentally ACK/close the fired reminder. The
            # reminder remains addressable through last_reminder_id, so an
            # explicit «завтра в 12» still resolves deterministically.
            if live_pending is not None:
                store.pending_clear(self.conn, chat_id)
            self.reply(chat_id, T(lang, "reminder_snooze_past", time=params["time"]))
            return True
        self.resolve_pending(chat_id, action, params, context, lang)
        return True

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

    def _reminder_list_body(self, chat_id, lang):
        """Freshly-numbered active reminders, and stamp that they were JUST shown — so a
        follow-up '#N' targets a reminder (not a note) AND maps to this current list. Used
        both for an explicit list request and to auto-refresh after a delete, so the numbers
        the boss sees never go stale under a back-to-back delete."""
        rows = store.reminders_active(self.conn, chat_id)
        store.kv_set(self.conn, "reminders_listed_at", datetime.now(timezone.utc).isoformat())
        return reminders.format_list(rows, self.tz_offset(), lang)

    def fire_due_reminders(self):
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        recent_msg = self._recent_boss_msg(now)
        max_defer = self.cfg.reminder_max_defer_hours
        defer_cutoff = ((now - timedelta(hours=max_defer)).isoformat()
                        if max_defer and max_defer > 0 else None)
        for row in store.reminders_due(self.conn, now_iso):
            # A reminder is an EXPLICIT alarm the boss set for a chosen time: it fires at that
            # time even inside quiet hours (quiet hours only silences Cara's PROACTIVE outreach
            # — nudges/brief). The ONLY
            # in-conversation safety is the ~5-min lull: it won't land within
            # reminder_quiet_after_msg_minutes of his last message, so it never interrupts an
            # active exchange — EXCEPT the max-defer valve: an alarm overdue beyond
            # reminder_max_defer_hours is delivered anyway (a continuous exchange must not
            # defer it indefinitely — "never lost to a long evening"; 0 disables the valve).
            if recent_msg and not (defer_cutoff and (row["due_utc"] or "") < defer_cutoff):
                continue
            lang = self.lang()
            delivered = self.reply(row["chat_id"], T(lang, "reminder_fired",
                                                      name=self.owner_name(), title=row["title"]))
            if not delivered:
                # Prefer at-least-once delivery to silently consuming an alarm.
                # Telegram may have accepted an ambiguously timed-out request, so
                # this can duplicate a reminder; losing it is the worse outcome.
                log(f"reminder #{row['id']} delivery failed; left due for retry")
                continue
            # The pending slot is single (PK = chat_id). A firing reminder must NOT
            # clobber a confirmation the boss is mid-way through (a reminder draft,
            # an ingest suggestion, a typed purge phrase): his next "да" would then
            # ack THIS reminder instead of confirming what he was asked — the draft
            # silently lost. If a pending exists, keep it; the fired reminder stays
            # addressable via last_reminder_id ("готово"/"закрой её" still lands).
            existing = store.pending_get(self.conn, row["chat_id"])
            if existing is None or existing.get("kind") == "reminder_fired":
                store.pending_set(
                    self.conn, row["chat_id"], "reminder_fired",
                    {"reminder_id": row["id"], "title": row["title"]}, ttl_seconds=1800,
                )
            following = reminders.next_due(row["due_utc"], row["recurrence"])
            if following:
                store.reminder_update_due(
                    self.conn, row["id"], following, reason="recurrence_advanced")
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
