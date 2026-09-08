#!/usr/bin/env python3
"""Reminders domain for Cara — handlers, disambiguation/resolvers, and the scheduler
fire/expiry sweeps, gathered out of the Agent/Hermes into one labelled module.

`ReminderMixin` is mixed into the Agent (`class Agent(..., reminders_svc.ReminderMixin)`),
so `self` is the Agent: `self.reply`/`self.conn`/`self.cfg`/`self.tz_offset()`/`self.owner_name()`/
`self._recent_boss_msg`/`self.resolve_item` all resolve on it exactly as before. Pure relocation,
no behaviour change — the reminder subsystem now lives together. Stateless parsing/formatting
helpers stay in `reminders.py`; the thin create/list/cancel/calendar handlers stay on the Agent.
"""
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone

import journals
import reminders
import store
from common import log
from texts import T


class ReminderMixin:
    """Reminder handlers + resolvers + fire/expiry sweeps. Mixed into the Agent."""

    # Phase A (2026-09-07) constants — see docs/adr/ADR-0001…0004.
    OVERDUE_FIRED_GRACE_HOURS = 2   # a fired one-shot unacked this long is «overdue» (ADR-0003)
    REPING_LOCAL_HOUR = 9           # the one re-ping before expiry lands at the next local 09:00
    SNOOZE_ESCALATE_AT = 3          # third snooze in a day → «перенести на день или закрыть?»

    def _send_reminder_draft(self, chat_id, lang, draft, twin=None):
        """The draft card (ADR-0001): Ставлю · Другое время · Не надо buttons, plus
        the «move the existing one» offer when an active reminder already carries
        this subject (ADR-0004). Text «да»/«нет» keep working."""
        text = T(lang, "reminder_draft", title=draft["title"],
                 when_local=reminders.fmt_local(draft["due_utc"], self.tz_offset()),
                 recurrence=T(lang, "recurrence_" + draft["recurrence"]))
        twin_id = None
        if twin is not None:
            twin_id = twin["id"]
            text += "\n" + T(lang, "reminder_twin", rid=self.reminder_no(chat_id, twin["id"]),
                             title=twin["title"],
                             when_rel=reminders.fmt_relative(twin["due_utc"], self.tz_offset(), lang))
        return self.reply(chat_id, text, reply_markup=reminders.draft_keyboard(lang, twin_id))

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
            # Every mutation re-renders the numbered list (ADR-0004): a move
            # re-orders it, and «закрой #2» must read off what is on his screen.
            self.reply(chat_id, T(lang, "reminders_rescheduled_multi", n=len(targets),
                                  when_local=reminders.fmt_local(due.isoformat(), self.tz_offset()))
                       + "\n\n" + self._reminder_list_body(chat_id, lang))
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
                              when_local=reminders.fmt_local(due.isoformat(), self.tz_offset()))
                   + "\n\n" + self._reminder_list_body(chat_id, lang))

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
                              rid=self.reminder_no(chat_id, row["id"]), title=new_title)
                   + "\n\n" + self._reminder_list_body(chat_id, lang))

    def start_partial_reminder(self, chat_id, lang, params, msg_id=None):
        """A reminder_create missing the subject or the time: keep whatever the
        boss gave and ask for the rest, instead of dropping it to a generic
        clarify (which lost 'напомни в 17:00' entirely). `msg_id` is the
        command turn's Telegram message id — it rides the partial into the
        full draft so the edit-notice pointer is written when the reminder is
        actually created at confirm (2026-07-27)."""
        now = datetime.now(timezone.utc)
        draft = {"recurrence": "none"}
        if msg_id:
            draft["src_msg_id"] = int(msg_id)
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
                 if k in ("title", "due_utc", "recurrence", "src_msg_id", "note_msg_id")}
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
            if draft.get("src_msg_id"):   # validate_draft strips extra keys
                full["src_msg_id"] = draft["src_msg_id"]
            if draft.get("note_msg_id"):
                full["note_msg_id"] = draft["note_msg_id"]
            twin = reminders.find_twin(store.reminders_active(self.conn, chat_id), full["title"])
            if twin is not None:
                full["twin_id"] = twin["id"]
            store.pending_set(self.conn, chat_id, "reminder", full)
            self._send_reminder_draft(chat_id, lang, full, twin=twin)
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
        note's actual subject instead. The boss's own title always wins.

        Returns None when he named a note that does NOT exist — `resolve_item`
        used to answer with the newest note, so «напомни по заметке 7» with #7
        gone took a stranger note's subject as the title AND linked the reminder
        to it (WP5's fail-closed rule; the note is never named back to him, so
        the substitution is invisible in the draft he confirms)."""
        note_id = params.get("note_id")
        if note_id is None:
            return params
        note = self.resolve_item({"id": note_id})
        if note is None:
            return None
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
        if row is None and getattr(self, "turn_reply_reminder_id", None) is not None:
            # Same binding as `_resolve_reminder_target`: a Reply to a fired
            # notification names ITS reminder and nothing else. Undo resolves its
            # own target, so the widened guard did not cover it — «верни как
            # было» on an already-acked alarm fell to the `moved` fallback below
            # and silently restored an UNRELATED reminder's previous time.
            row = next((r for r in rows if r["id"] == self.turn_reply_reminder_id), None)
            if row is None:
                self.reply(chat_id, T(lang, "reminder_already_closed"))
                return
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

    def _followup_day_due(self, t, days):
        """UTC ISO for «(после)завтра [в] HH[:MM]» — `days` ahead in LOCAL time,
        defaulting to 09:00 when the boss named no clock time.

        The meridiem is read too: «am»/«pm» are follow-up scaffold words
        (reminders._FOLLOWUP_SCAFFOLD), so «tomorrow at 5 pm» reaches this
        branch instead of the router — dropping the «pm» re-armed the alarm at
        05:00, twelve hours early. The Russian twins («в 5 вечера/дня»,
        «в 12 ночи») are the same trap one language over (2026-07-27)."""
        tm = re.search(r"(?:в|на|at)?\s*(\d{1,2})(?::(\d{2}))?"
                       r"(?:\s*(?P<mer>am|pm|утра|ночи|дня|вечера)\b)?\b", t)
        # No clock time: a part-of-day word picks its own default (ADR-0004 —
        # «завтра вечером» re-armed at 09:00), a bare «завтра» keeps 09:00.
        hour = (max(0, min(23, int(tm.group(1)))) if tm
                else (reminders.part_of_day_hour(t) or reminders.DEFAULT_SNOOZE_HOUR))
        minute = max(0, min(59, int(tm.group(2) or 0))) if tm else 0
        mer = tm.group("mer") if tm else None
        if mer in ("pm", "дня", "вечера") and hour < 12:
            hour += 12
        elif mer in ("am", "ночи", "утра") and hour == 12:
            hour = 0
        local_now = datetime.now(timezone.utc) + timedelta(hours=self.tz_offset())
        local_due = datetime.combine(local_now.date() + timedelta(days=days),
                                     datetime.min.time(), tzinfo=timezone.utc)
        local_due = local_due.replace(hour=hour, minute=minute)
        return (local_due - timedelta(hours=self.tz_offset())).isoformat()

    # «готово 2» / «закрой второе» on a batch card name ONE member (ADR-0002).
    _MEMBER_ORDINALS = {"перв": 1, "втор": 2, "трет": 3, "четв": 4, "пят": 5, "шест": 6,
                        "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
                        "sixth": 6}

    def _parse_member(self, t, members):
        """(member_index, text_without_it) when the reply names one member of a
        batch card by number or ordinal, else (None, t). A number that reads as a
        time («на 12», «через 2 часа», «12:30») is never a member."""
        if members < 2:
            return None, t
        m = re.search(r"(?:^|\s)#?(\d{1,2})(?=\s|$|[.!])", t)
        if m:
            before = t[:m.start(1)].rstrip()
            after = t[m.end(1):]
            if not re.search(r"(?:^|\s)(?:в|во|на|к|до|через|спустя|at|to|in|after|until)$",
                             before) and not re.match(r"\s*(?::|час|ч\b|мин|м\b|h\b|hour|hr\b"
                                                      r"|day|дн|недел|нед\b|сутк)", after):
                n = int(m.group(1))
                if 1 <= n <= members:
                    return n, (t[:m.start()] + " " + after).strip()
        for stem, n in self._MEMBER_ORDINALS.items():
            hit = re.search(rf"(?:^|\s)({stem}\w*)(?=\s|$|[.!])", t)
            if hit and 1 <= n <= members:
                return n, (t[:hit.start()] + " " + t[hit.end():]).strip()
        return None, t

    def _parse_fired_followup(self, text, *, allow_bare_ack=False, title="", members=1):
        """Deterministic common acknowledgements/snoozes for a fired reminder.

        Returns (action, params), or None when the message is substantive or
        ambiguous and should continue through normal routing. On a batch card
        (`members` > 1) a named member rides in params["member"] (1-based).
        """
        t = str(text or "").strip().casefold()
        if not t or len(t) > 90:
            return None
        if any(w in t for w in ("запиш", "сохран", "добав", "заметк", "note", "save")):
            return None
        member, t = self._parse_member(t, members)
        extra = {"member": member} if member is not None else {}
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
            return "amend", {"done": True, **extra}
        if re.fullmatch(r"(?:закрой|закрыть|готово|сделано|выполнено|done|close|closed)[.! ]*", t):
            return "confirm", extra
        if member is not None and not t:
            return "confirm", extra          # «2» alone on a batch card = close member 2
        if allow_bare_ack and re.fullmatch(r"(?:да|yes|yep|ага|ок|okay|ok|\+|✅|👍)[.! ]*", t):
            return "confirm", extra
        # «сегодня вечером» / «tonight» (no digits): today at the part-of-day default —
        # already past → the honest clarification, never a silent roll to tomorrow.
        if (("сегодня" in t or "today" in t or "tonight" in t) and not re.search(r"\d", t)
                and reminders.part_of_day_hour(t) is not None):
            hour = reminders.part_of_day_hour(t)
            due = reminders.local_day_at(self.tz_offset(), 0, hour)
            if reminders.parse_iso_utc(due) <= datetime.now(timezone.utc):
                return "clarify_time", {"time": f"{hour:02d}:00"}
            return "amend", {"due_utc": due, **extra}
        # «завтра…» MUST be checked before the relative-duration regexes: in
        # «давай завтра в 10 часов» the hours pattern would otherwise eat «10
        # часов» first and silently re-arm the alarm 10 hours from now (~06:00)
        # instead of tomorrow 10:00.
        # «послезавтра» CONTAINS «завтра» — it must be tested FIRST, or the
        # substring match re-arms the alarm a full day EARLY (the scaffold
        # whitelist in reminders.py already admits «послезавтра» as follow-up
        # language, so it reaches this point).
        if "послезавтра" in t or "day after tomorrow" in t:
            return "amend", {"due_utc": self._followup_day_due(t, 2), **extra}
        if "завтра" in t or "tomorrow" in t:
            return "amend", {"due_utc": self._followup_day_due(t, 1), **extra}
        # A bare absolute-clock snooze is common boss language: «отложи на 12»
        # means 12:00 LOCAL TODAY, not 12 minutes/hours from now and not a trip
        # through the probabilistic router. If today's clock time already passed,
        # fail closed with a clarification instead of silently rolling tomorrow.
        # 2026-09-08 incident: he REPLIED to the fired «LinkedIn» card with
        # «Напомни в 17:45» and got «Про что напомнить?» — the grammar knew
        # «напомни НА/ДО HH:MM» but not the everyday «В HH:MM», so the bound
        # follow-up fell through to the router's time-only CREATE shortcut. «в»
        # is a preposition here now, and a bound message may drop the verb
        # («в 17:45», «давай в 17:45», «лучше в 17:45»); «на/до» without a verb
        # stay out (a bare «на 2» is too easily something else).
        absolute = re.fullmatch(
            r"(?:(?:давай|лучше|ладно|ок|окей)[,\s]+)?"
            r"(?:(?P<verb>отложи|перенеси|напомни)(?:\s+(?:это|напоминание|его|мне))?\s+)?"
            r"(?P<prep>на|до|в)\s+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?"
            r"(?:\s*(?P<unit>час(?:а|ов)?|ч))?[.! ]*",
            t,
        )
        if absolute is not None and absolute.group("prep") != "в" and not absolute.group("verb"):
            absolute = None
        if absolute is not None and absolute.group("prep") == "на" \
                and absolute.group("unit") and not absolute.group("minute"):
            # «отложи на 2 часа» is the DURATION idiom — postpone BY two hours,
            # not to 02:00 (which read as 'already passed' and asked to clarify).
            # «до 2 часов» and a unit-less «на 2» stay absolute-clock.
            return "amend", {"snooze_minutes": max(1, int(absolute.group("hour"))) * 60, **extra}
        if absolute is None:
            absolute = re.fullmatch(
                r"(?:snooze|remind me|move it)(?:\s+(?:until|to|at))\s+"
                r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?[.! ]*",
                t,
            )
        if absolute is not None:
            hour = int(absolute.group("hour"))
            minute = int(absolute.group("minute") or 0)
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                return None
            local_now = datetime.now(timezone.utc) + timedelta(hours=self.tz_offset())
            local_due = datetime.combine(local_now.date(), datetime.min.time(),
                                         tzinfo=timezone.utc).replace(
                                             hour=hour, minute=minute)
            if local_due <= local_now:
                return "clarify_time", {"time": f"{hour:02d}:{minute:02d}"}
            due = local_due - timedelta(hours=self.tz_offset())
            return "amend", {"due_utc": due.isoformat(), **extra}
        if "полчас" in t or "half an hour" in t:
            return "amend", {"snooze_minutes": 30, **extra}
        m = re.search(r"(\d{1,4})\s*(минут|минуты|минуту|мин|minutes?|mins?)\b", t)
        if m:
            return "amend", {"snooze_minutes": max(1, int(m.group(1))), **extra}
        m = re.search(r"(\d{1,3})\s*(часа|часов|час|ч|hours?|hrs?)\b", t)
        if m:
            return "amend", {"snooze_minutes": max(1, int(m.group(1))) * 60, **extra}
        if re.search(r"(?:на|ещ[ёе]|через)\s+час(?:ок)?\b|\ban hour\b", t):
            return "amend", {"snooze_minutes": 60, **extra}
        return None

    # A RECURRING reminder auto-advances at fire — it has no lingering
    # awaiting-ack state, so late binding via last_reminder_id (after the
    # 30-min pending expires) only makes sense shortly after the fire. Outside
    # this window a «завтра в 10»-shaped message belongs to the router. Fired
    # ONE-SHOTS genuinely stay open until «готово», so they keep the unbounded
    # explicit-followup binding.
    RECURRING_FOLLOWUP_WINDOW = timedelta(hours=3)
    _CREATE_VERB_RE = re.compile(r"^\s*(?:напомни|поставь|создай|remind|set)\b", re.IGNORECASE)

    # -- fired-notification message → reminder binding (2026-07-23 incident) --
    # The boss can TG-Reply to a SPECIFIC fired-reminder notification («Отложи
    # на завтра» on the «заметка #9» alarm). That reply names the exact
    # reminder — recency (last_reminder_id) must never override it: last night
    # it snoozed the just-fired gratitude daily instead of the reminder he
    # replied to. Delivered notification message ids are remembered (bounded)
    # so the reply target resolves deterministically.
    _FIRED_MSGS_KV = "fired_reminder_msgs"
    _FIRED_MSGS_KEEP = 30

    def _fired_messages(self):
        try:
            data = json.loads(store.kv_get(self.conn, self._FIRED_MSGS_KV) or "{}")
        except ValueError:
            data = {}
        return data if isinstance(data, dict) else {}

    def _remember_fired_message(self, tg_message_id, reminder_id):
        """Map a DELIVERED fired-notification Telegram message to its reminder —
        or, for a batch card (ADR-0002), to its id SET (`{"ids": [...], "done": []}`,
        `done` tracking the members already resolved from that card)."""
        if not tg_message_id:
            return
        data = self._fired_messages()
        if isinstance(reminder_id, (list, tuple)):
            data[str(int(tg_message_id))] = {"ids": [int(r) for r in reminder_id], "done": []}
        else:
            data[str(int(tg_message_id))] = int(reminder_id)
        for key in sorted(data, key=int)[:-self._FIRED_MSGS_KEEP]:
            del data[key]
        store.kv_set(self.conn, self._FIRED_MSGS_KV, json.dumps(data))

    def fired_reminders_for_message(self, tg_message_id):
        """Every reminder a fired notification named (a batch card lists several),
        in card order; [] when the message is not a fired notification."""
        if tg_message_id is None:
            return []
        try:
            entry = self._fired_messages().get(str(int(tg_message_id)))
        except (TypeError, ValueError):
            return []
        if isinstance(entry, dict):
            try:
                return [int(r) for r in entry.get("ids") or []]
            except (TypeError, ValueError):
                return []
        try:
            return [int(entry)] if entry is not None else []
        except (TypeError, ValueError):
            return []

    def fired_reminder_for_message(self, tg_message_id):
        """The reminder whose fired notification the boss replied to (the FIRST
        member of a batch card), or None."""
        ids = self.fired_reminders_for_message(tg_message_id)
        return ids[0] if ids else None

    def _mark_fired_message_done(self, tg_message_id, rid):
        """Remember that member `rid` of a batch card was resolved; returns the
        members still open on that card (for the rebuilt keyboard)."""
        data = self._fired_messages()
        entry = data.get(str(int(tg_message_id))) if tg_message_id else None
        if not isinstance(entry, dict):
            return []
        done = [int(r) for r in entry.get("done") or []]
        if int(rid) not in done:
            done.append(int(rid))
        entry["done"] = done
        store.kv_set(self.conn, self._FIRED_MSGS_KV, json.dumps(data))
        return [int(r) for r in entry.get("ids") or [] if int(r) not in done]

    def resolve_fired_followup(self, chat_id, lang, text, pending):
        """Handle a fired-reminder reply before the probabilistic router.

        The pending confirmation may expire after 30 minutes, but a fired
        one-shot remains open; an explicit close/skip/snooze can still bind to
        last_reminder_id (a recurring one only within the recency window).
        Bare yes/ok requires the live pending context.
        """
        live_pending = pending if pending and pending.get("kind") == "reminder_fired" else None
        # A TG Reply to a SPECIFIC fired notification is the STRONGEST binding:
        # it names the exact reminder — overriding both the live pending (which
        # may be about a different, later reminder) and the last-fired recency
        # rule (no window: replying IS explicit, however old the alarm).
        context = None
        closed_reply = False
        reply_rid = getattr(self, "turn_reply_reminder_id", None)
        if reply_rid is not None:
            # A reply to a BATCH card names every member still open (ADR-0002).
            reply_ids = list(getattr(self, "turn_reply_reminder_ids", None) or [reply_rid])
            rows = [r for r in (store.reminder_get(self.conn, i) for i in reply_ids)
                    if r is not None and r["status"] == "active"]
            row = store.reminder_get(self.conn, reply_rid)
            if rows:
                context = {"kind": "reminder_fired",
                           "payload": {"reminder_id": rows[0]["id"], "title": rows[0]["title"],
                                       "reminder_ids": [r["id"] for r in rows],
                                       "titles": [r["title"] for r in rows]}}
            else:
                closed_reply = True
        if closed_reply:
            # He replied to a notification whose reminder is already closed (or
            # gone). That reply NAMES that reminder — falling through to the live
            # pending / last-touched one is exactly the 2026-07-23 incident class.
            # A follow-up gets the honest refusal; anything substantive still
            # routes normally (a closed alarm doesn't swallow real content).
            if self._parse_fired_followup(
                    text, allow_bare_ack=True,
                    title=str((row["title"] if row is not None else "") or "")) is None:
                return False
            self.reply(chat_id, T(lang, "reminder_already_closed"))
            return True
        if context is None:
            context = live_pending
        if context is None and pending is not None:
            # ANY current non-fired card is stronger than the stale
            # last_reminder_id fallback. In production, «Через полчаса» was the
            # missing time for a new reminder_partial; a gratitude capture also
            # leaves a category card whose «Готово» must confirm the entry, not
            # close the alarm behind it. An explicit Telegram reply to a fired
            # notification still wins above because it names the exact reminder.
            return False
        if context is None:
            last_id = store.kv_get(self.conn, "last_reminder_id")
            try:
                row = store.reminder_get(self.conn, int(last_id)) if last_id is not None else None
            except (TypeError, ValueError):
                row = None
            if row is not None and (row["status"] != "active" or row["last_fired_at"] is None):
                row = None
            if row is not None and row["recurrence"] != "none":
                fired = reminders.parse_iso_utc(row["last_fired_at"])
                if fired is None or (datetime.now(timezone.utc) - fired
                                     > self.RECURRING_FOLLOWUP_WINDOW):
                    row = None
            if row is None:
                # ADR-0011: after the pending window a bare snooze/ack still belongs
                # to the last FIRED one-shot (open until «готово»), never to the LLM
                # router with pending=None — «Через час» 116 min after a fire went
                # there and converse invented a time.
                open_fired = store.reminders_fired_unacked(self.conn, chat_id)
                row = open_fired[-1] if open_fired else None
            if row is None:
                return False
            fired = reminders.parse_iso_utc(row["last_fired_at"])
            stale = (fired is None or (datetime.now(timezone.utc) - fired
                                       > self.RECURRING_FOLLOWUP_WINDOW))
            # A subject-less CREATE («напомни завтра в 10») hours after a one-shot
            # fired is a new reminder in the making, not a snooze of the stale
            # one (ADR-0004): let it open a partial. Explicit close/skip/snooze
            # verbs keep the unbounded binding a fired one-shot has always had.
            if stale and self._CREATE_VERB_RE.match(str(text or "")):
                return False
            context = {"kind": "reminder_fired",
                       "payload": {"reminder_id": row["id"], "title": row["title"]}}
        member_ids = context["payload"].get("reminder_ids") or [context["payload"].get("reminder_id")]
        parsed = self._parse_fired_followup(
            text,
            allow_bare_ack=(live_pending is not None
                            or context["payload"].get("reminder_id") == reply_rid),
            title=str(context["payload"].get("title") or ""),
            members=len(member_ids))
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

    # Sentinel for «he picked a SHOWN position whose reminder is gone» — a
    # not-found, never a shifted substitute (and never «not a pick», which
    # would silently abandon the remembered op and re-route the message).
    _PICK_GONE = object()

    def _parse_reminder_selector(self, text, rows):
        """Map the boss's disambiguation answer to one of `rows` (display order):
        a number / '#2', an ordinal word ('второе'), or a title word ('про банк').

        `rows` is POSITIONAL and may carry None slots (a shown reminder that
        has since fired-and-advanced or closed): «первое» must mean the first
        item of the card he was SHOWN, exactly like the notes snapshot — the
        old re-queried list re-numbered itself when a daily advanced, and his
        answer landed on a different reminder. A pick of a gone slot returns
        `_PICK_GONE` (2026-07-27)."""
        t = (text or "").strip().casefold()
        if not t or not any(r is not None for r in rows):
            return None
        # Only a BARE or #-prefixed number is a PICK. «давай лучше в 2 часа» is a
        # fresh TIME during the disambiguation, not a choice of reminder #2 — a
        # number preceded by «в/на/до/через/at» or followed by ANY time unit /
        # «:» belongs to a new reschedule, so we return None and the message
        # re-routes. (Units beyond hours/minutes matter: «через 2 дня» slipped
        # through both guards and picked #2 with the stale time.)
        for m in re.finditer(r"(#\s*)?(\d{1,3})(?!\d)", t):
            if not m.group(1):
                before = t[:m.start()]
                if before and before[-1] in ":.,-/":
                    continue                       # inside a time/date («12:15»)
                if re.search(r"(?:^|\s)(?:в|во|на|к|до|через|спустя|at|to|in|after)\s*$",
                             before):
                    continue                       # «в 2 часа» / «через 2 дня» — a time
                if re.match(r"\s*(?::|час|ч\b|мин|м\b|h\b|hour|hr\b|day|week"
                            r"|дн|недел|нед\b|сутк)", t[m.end():]):
                    continue                       # «2 часа», «2 дня», «2:30»
            n = int(m.group(2))
            if 1 <= n <= len(rows):
                return rows[n - 1] if rows[n - 1] is not None else self._PICK_GONE
        for stem, n in self._ORDINALS.items():
            if stem in t and 1 <= n <= len(rows):
                return rows[n - 1] if rows[n - 1] is not None else self._PICK_GONE
        for r in rows:  # a word of the title appearing in his answer ('про банк')
            if r is None:
                continue
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
        # PIN the positions to the ORDER the «какое?» card showed (the stored
        # ids list): re-deriving them from a live re-query re-numbered the list
        # when a recurring reminder fired-and-advanced (or one expired) between
        # the question and his answer, so «первое» landed on a different
        # reminder. A gone id keeps its slot as None — exactly the notes
        # snapshot's rule — and picking it is a not-found, never a substitute
        # (2026-07-27).
        active = {r["id"]: r for r in store.reminders_active(self.conn, chat_id)}
        rows = [active.get(i) for i in ids]
        row = self._parse_reminder_selector(text, rows)
        if row is None:
            return False
        store.pending_clear(self.conn, chat_id)
        if row is self._PICK_GONE:
            self.reply(chat_id, T(lang, "reminder_already_closed"))
            return True
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
        # A ONE-element ids list («перенеси #2» arriving as {"ids": [2]}) is just as
        # explicit as `id`: fold it in, or it is discarded and the op silently lands
        # on the last-touched reminder instead of the one he named.
        ids = params.get("ids")
        if params.get("id") is None and isinstance(ids, list) and len(ids) == 1:
            params = dict(params, id=ids[0])
        # A LONGER ids list is explicit too: when only some of its entries exist
        # («перенеси #1 и #99») do_reschedule's multi path doesn't fire, and an
        # ids list that never counted as a target dropped the op onto the
        # last-touched reminder. Any non-empty ids list is a named target.
        has_target = (params.get("id") is not None or bool(params.get("ids"))
                      or params.get("title_query") or params.get("title"))
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
        if row is None and getattr(self, "turn_reply_reminder_id", None) is not None:
            # He REPLIED to a fired notification and named no target of his own:
            # that reply names ITS reminder and nothing else. Still active -> it
            # is the target; already closed/gone -> not-found. The deterministic
            # follow-up guard only sees the wordings `_parse_fired_followup`
            # recognises; everything else reaches the router, and the bare
            # fallback below would hand the move to the last-touched reminder —
            # the 2026-07-23 incident, one route further along.
            match = next((r for r in rows if r["id"] == self.turn_reply_reminder_id), None)
            if match is not None:
                return match
            self.reply(chat_id, T(lang, "reminder_already_closed"))
            return None
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

    def _journal_for_reminder_title(self, title):
        """The active structured-journal category a fired reminder's title names
        («благодарности» → «Благодарность»), or '' — the card then invites the
        entry itself instead of asking for «готово» (ADR-0005)."""
        title = str(title or "").strip()
        if not title:
            return ""
        category = ""
        if journals.is_gratitude_name(title):
            gdef = store.journal_def_get(self.conn, journals.GRATITUDE_SLUG)
            if gdef is not None and gdef["active"]:
                category = str(gdef["category"] or "").strip()
        if not category:
            category = self._match_journal_category(title, store.journal_categories(self.conn))
        if not category:
            return ""
        gdef = store.journal_def_by_category(self.conn, category)
        if gdef is None or not journals.ENTRY_TYPES.get(gdef["entry_type"], {}).get("active"):
            return ""
        return category if store.is_journal(self.conn, category) else ""

    def _send_fired_card(self, chat_id, rows):
        """One reminder → its card (a journal variant when the title IS a journal);
        two or more → ONE numbered card with per-member buttons (ADR-0001/0002).
        Returns what reply() returned (the message dict, or None on failure)."""
        lang = self.lang()
        name = self.owner_name()
        if len(rows) == 1:
            row = rows[0]
            journal = self._journal_for_reminder_title(row["title"])
            if journal:
                return self.reply(chat_id, T(lang, "reminder_fired_journal", name=name,
                                             title=row["title"]),
                                  reply_markup=reminders.journal_fired_keyboard(row["id"], lang))
            return self.reply(chat_id, T(lang, "reminder_fired", name=name, title=row["title"]),
                              reply_markup=reminders.fired_keyboard(row["id"], lang))
        items = "\n".join(f"{i}) {r['title']}" for i, r in enumerate(rows, start=1))
        return self.reply(chat_id, T(lang, "reminder_fired_batch", name=name, items=items),
                          reply_markup=reminders.batch_keyboard([r["id"] for r in rows], lang))

    def fire_due_reminders(self):
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        recent_msg = self._recent_boss_msg(now)
        max_defer = self.cfg.reminder_max_defer_hours
        defer_cutoff = ((now - timedelta(hours=max_defer)).isoformat()
                        if max_defer and max_defer > 0 else None)
        by_chat = {}
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
            by_chat.setdefault(row["chat_id"], []).append(row)
        for chat_id, rows in by_chat.items():
            # A row in the owed map was already DELIVERED on an earlier pass whose
            # post-send bookkeeping hit a sqlite error (2026-07-27): the DB kept
            # re-selecting it (last_fired_at never stamped) and each poll cycle
            # re-sent the identical alarm — a write outage turned one reminder
            # into a ~50-second broadcast for as long as the outage lasted. Only
            # the BOOKKEEPING is owed now; the send is not repeated. Matched on
            # (id, due_utc), not the id alone: the store helpers commit one by
            # one, so when the outage began only after reminder_update_due had
            # committed, an id-only marker went stale — and the NEXT legitimate
            # occurrence was swallowed as bookkeeping-only, one alarm silently
            # consumed (the outcome the comment below calls worse than a
            # duplicate). A mismatched due is a NEW occurrence: send normally;
            # the stale entry dies with the pop below or is overwritten by a
            # fresh failure.
            owed = [r for r in rows if self._reminder_stamp_owed.get(r["id"]) == r["due_utc"]]
            fresh = [r for r in rows if self._reminder_stamp_owed.get(r["id"]) != r["due_utc"]]
            delivered = None
            if fresh:
                try:
                    delivered = self._send_fired_card(chat_id, fresh)
                except sqlite3.Error:
                    # reply() records the conversation turn AFTER Telegram accepted
                    # the send — a sqlite error here means the card IS (or may
                    # well be) on his screen. Treat it as delivered-but-unstamped;
                    # re-raise so _tick counts the stall.
                    for r in fresh:
                        self._reminder_stamp_owed[r["id"]] = r["due_utc"]
                    raise
                if not delivered:
                    # Prefer at-least-once delivery to silently consuming an alarm.
                    # Telegram may have accepted an ambiguously timed-out request, so
                    # this can duplicate a reminder; losing it is the worse outcome.
                    log(f"reminder(s) {[r['id'] for r in fresh]} delivery failed; left due for retry")
                    fresh = []
            ids = [r["id"] for r in fresh]
            # Everything below is bookkeeping the delivered card is owed: pre-mark it
            # so a sqlite error anywhere in the middle leaves each unstamped row owed
            # (and each stamped one popped), never re-sent.
            for r in owed + fresh:
                self._reminder_stamp_owed[r["id"]] = r["due_utc"]
            # The slot read comes FIRST, for owed rows too: during a write outage it
            # is the probe that raises before any partial bookkeeping write lands.
            existing = store.pending_get(self.conn, chat_id)
            if fresh:
                # Remember the notification's message id: a TG Reply to it names
                # THIS exact reminder (or this card's id set) for close/snooze.
                if isinstance(delivered, dict):
                    self._remember_fired_message(delivered.get("message_id"),
                                                 ids if len(ids) > 1 else ids[0])
                # The pending slot is single (PK = chat_id). A firing reminder must NOT
                # clobber a confirmation the boss is mid-way through (a reminder draft,
                # an ingest suggestion, a typed purge phrase): his next "да" would then
                # ack THIS reminder instead of confirming what he was asked — the draft
                # silently lost. If a pending exists, keep it; the fired reminder stays
                # addressable via last_reminder_id ("готово"/"закрой её" still lands).
                if existing is None or existing.get("kind") == "reminder_fired":
                    payload = {"reminder_id": ids[0], "title": fresh[0]["title"],
                               "reminder_ids": ids, "titles": [r["title"] for r in fresh]}
                    if len(fresh) == 1:
                        journal = self._journal_for_reminder_title(fresh[0]["title"])
                        if journal:
                            payload["journal"] = journal
                    store.pending_set(self.conn, chat_id, "reminder_fired", payload,
                                      ttl_seconds=1800)
            for r in owed + fresh:
                rid = r["id"]
                following = reminders.next_due(r["due_utc"], r["recurrence"])
                if following:
                    store.reminder_update_due(
                        self.conn, rid, following, reason="recurrence_advanced")
                # B5: a fired ONE-SHOT is NOT auto-closed — it stays active/visible until the
                # boss explicitly acks ('готово') or cancels it; last_fired_at stops it
                # re-firing. (Old behavior closed it here, which read as 'why did you close it'.)
                store.reminder_touch_fired(self.conn, rid)
                self._reminder_stamp_owed.pop(rid, None)
                log(f"reminder #{rid} fired" + (" (stamp recovered)" if r in owed else ""))
            if fresh:
                self._remember_reminder(ids[-1])  # "готово/перенеси это" binds to the just-fired one

    def expiry_notice_enabled(self):
        value = str(store.pref_get(self.conn, "reminder_expiry_notice") or "on").strip().casefold()
        return value not in ("off", "false", "0", "no", "нет")

    def check_reminder_expiry(self):
        """The fired-but-unacked sweep (ADR-0003): ONE re-ping per reminder at the
        next local 09:00, then — past `reminder_fired_expire_days` (0 disables) —
        auto-close, ANNOUNCED («Закрыла как просроченные: … — вернуть?») with a
        reopen path instead of the old silent close."""
        now = datetime.now(timezone.utc)
        try:
            self._reping_stale_fired(now)
        except Exception as exc:  # noqa: BLE001 — must not kill the loop
            log(f"reminder re-ping sweep error: {exc!r}")
        days = self.cfg.reminder_fired_expire_days
        if not days or days <= 0:
            return
        cutoff = (now - timedelta(days=days)).isoformat()
        try:
            rows = store.reminders_expire_stale(self.conn, cutoff)
        except Exception as exc:  # noqa: BLE001 — must not kill the loop
            log(f"reminder expiry sweep error: {exc!r}")
            return
        if not rows:
            return
        log(f"auto-expired {len(rows)} stale fired reminder(s)")
        if not self.expiry_notice_enabled():
            return
        by_chat = {}
        for row in rows:
            by_chat.setdefault(row["chat_id"], []).append(row)
        for chat_id, chat_rows in by_chat.items():
            lang = self.lang()
            items = ", ".join(f"«{r['title']}»" for r in chat_rows[:5])
            if len(chat_rows) > 5:
                items += " …"
            ids = [r["id"] for r in chat_rows]
            sent = self.reply(chat_id, T(lang, "reminders_expired_notice", items=items),
                              reply_markup=reminders.reopen_keyboard(ids, lang))
            # «верни» as plain text works while the notice is the open question; the
            # single pending slot is never taken from a confirmation in flight.
            if sent and store.pending_get(self.conn, chat_id) is None:
                store.pending_set(self.conn, chat_id, "reminder_expired",
                                  {"reminder_ids": ids}, ttl_seconds=24 * 3600)

    def _reping_stale_fired(self, now):
        """One re-ping per fired one-shot still unacked past the grace window, at the
        next local 09:00 after it fired (never twice, never mid-exchange)."""
        grace = (now - timedelta(hours=self.OVERDUE_FIRED_GRACE_HOURS)).isoformat()
        rows = store.reminders_fired_unacked(self.conn, fired_before_iso=grace)
        if not rows:
            return
        offset = self.tz_offset()
        local_now = now + timedelta(hours=offset)
        if local_now.hour < self.REPING_LOCAL_HOUR:
            return
        if self._recent_boss_msg(now):
            return
        today_9 = reminders.parse_iso_utc(
            reminders.local_day_at(offset, 0, self.REPING_LOCAL_HOUR, now=now))
        for row in rows:
            fired = reminders.parse_iso_utc(row["last_fired_at"])
            if fired is None or fired >= today_9:
                continue   # fired after this morning's 09:00 — its re-ping is tomorrow's
            if store.reminder_has_event(self.conn, row["id"], "repinged"):
                continue
            lang = self.lang()
            sent = self.reply(row["chat_id"], T(
                lang, "reminder_repinged", name=self.owner_name(), title=row["title"],
                fired_rel=reminders.fmt_relative(row["last_fired_at"], offset, lang, now)),
                reply_markup=reminders.fired_keyboard(row["id"], lang))
            if not sent:
                continue
            store.reminder_event(self.conn, row["id"], "repinged")
            if isinstance(sent, dict):
                self._remember_fired_message(sent.get("message_id"), row["id"])
            self._remember_reminder(row["id"])

    def reopen_reminders(self, chat_id, lang, ids):
        """Bring expired reminders back, re-armed at the next local 09:00 (ADR-0003).
        Returns the reply text."""
        offset = self.tz_offset()
        now = datetime.now(timezone.utc)
        local_now = now + timedelta(hours=offset)
        days_ahead = 0 if local_now.hour < self.REPING_LOCAL_HOUR else 1
        due = reminders.local_day_at(offset, days_ahead, self.REPING_LOCAL_HOUR, now=now)
        restored = []
        for rid in ids:
            row = store.reminder_get(self.conn, rid)
            if row is None or int(row["chat_id"]) != int(chat_id):
                continue
            if store.reminder_reopen(self.conn, rid, due):
                restored.append((rid, row["title"]))
        if not restored:
            return T(lang, "reminder_not_found")
        self._remember_reminder(restored[-1][0])
        return T(lang, "reminders_reopened",
                 items=", ".join(f"«{t}»" for _rid, t in restored),
                 when_rel=reminders.fmt_relative(due, offset, lang, now))

    _REOPEN_RE = re.compile(r"^(?:да,?\s*)?(?:верни(?:те)?|вернуть|восстанови(?:ть)?|"
                            r"reopen|restore|bring (?:them |it )?back|yes|да)[.! ]*$")
    _REOPEN_NO_RE = re.compile(r"^(?:нет|не надо|не нужно|no|nope|пусть)[.! ]*$")

    def resolve_expired_reopen_text(self, chat_id, lang, pending, text):
        """«верни» / «нет» while the expiry notice is the open question."""
        if not pending or pending.get("kind") != "reminder_expired":
            return False
        t = str(text or "").strip().casefold()
        if self._REOPEN_RE.fullmatch(t):
            store.pending_clear(self.conn, chat_id)
            ids = [int(i) for i in pending["payload"].get("reminder_ids") or []]
            self.reply(chat_id, self.reopen_reminders(chat_id, lang, ids))
            return True
        if self._REOPEN_NO_RE.fullmatch(t):
            store.pending_clear(self.conn, chat_id)
            self.reply(chat_id, T(lang, "cancelled"))
            return True
        return False

    # -- fired outcomes shared by text follow-ups and card buttons (ADR-0001/0002) --

    def _apply_snooze(self, chat_id, rid, title, due_iso):
        """Re-arm one fired reminder at `due_iso`. B4: the ORIGINAL one-shot moves
        (keeps id/history); a fired RECURRING one gets a one-shot ECHO and the series
        stays put (the anchor drifted 22:00 → 23:33 over two snoozes once). The
        snooze is counted on the row the boss acted on either way, so the
        third-of-the-day escalation sees it. Returns (effective_id, title)."""
        rem = store.reminder_get(self.conn, rid) if rid is not None else None
        if rem is not None and rem["recurrence"] != "none":
            eff = store.reminder_add(self.conn, chat_id, rem["title"], due_iso)
            store.reminder_event(self.conn, rid, "snoozed", due_iso)
        elif rem is not None:
            store.reminder_update_due(self.conn, rid, due_iso, reason="snoozed")
            eff = rid
        else:
            eff = store.reminder_add(self.conn, chat_id, title, due_iso)
        log(f"reminder #{eff} snoozed to {due_iso}")
        return eff, (rem["title"] if rem is not None else title)

    def _snoozes_today(self, rid):
        offset = self.tz_offset()
        day_start = reminders.local_day_at(offset, 0, 0)
        return store.reminder_snoozes_since(self.conn, rid, day_start)

    def _close_fired(self, chat_id, rid, reason):
        """«готово»/«пропустить» on one fired reminder: closes a one-shot (B5 — it was
        left open at fire), acknowledges a recurring one. Returns (row, display_no)
        or (None, None) when the reminder is gone."""
        rem = store.reminder_get(self.conn, rid) if rid is not None else None
        if rem is None:
            return None, None
        disp = self.reminder_no(chat_id, rid)   # capture before it leaves the active list
        if rem["recurrence"] == "none" and rem["status"] == "active":
            store.reminder_close(self.conn, rid, "done", reason=reason)
        else:
            store.reminder_event(self.conn, rid, "acknowledged", reason)
        return rem, disp

    def resolve_fired_action(self, chat_id, lang, payload, action, params):
        """Apply a готово / пропустить / snooze to the fired reminder(s) a card names —
        one id, or a batch card's id set (a member index in params narrows it).
        Returns (reply_text, reply_markup_or_None, remaining_ids)."""
        ids = [int(i) for i in (payload.get("reminder_ids") or [payload.get("reminder_id")])
               if i is not None]
        titles = list(payload.get("titles") or [payload.get("title") or ""] * len(ids))
        member = params.get("member")
        remaining = []
        if member is not None:
            try:
                idx = int(member)
            except (TypeError, ValueError):
                idx = 0
            if not 1 <= idx <= len(ids):
                return T(lang, "reminder_not_found"), None, ids
            remaining = [i for i in ids if i != ids[idx - 1]]
            ids, titles = [ids[idx - 1]], [titles[idx - 1] if idx - 1 < len(titles) else ""]
        offset = self.tz_offset()
        now = datetime.now(timezone.utc)
        snooze = params.get("snooze_minutes") if action == "amend" else None
        # Snooze by an absolute time too ("отложи до завтра в 9"), not only by
        # minutes ("через полчаса") — "отложи на час"/"до завтра" used to fall
        # through to reschedule and dead-end.
        due_at = reminders.parse_iso_utc(params.get("due_utc")) if action == "amend" else None
        if snooze or due_at is not None:
            if due_at is not None:
                due = due_at.isoformat()
            else:
                try:
                    minutes = max(1, int(snooze))
                except (TypeError, ValueError):
                    minutes = 30
                due = (now + timedelta(minutes=minutes)).isoformat()
            when_rel = reminders.fmt_relative(due, offset, lang, now)
            done = []
            for rid, title in zip(ids, titles):
                eff, real_title = self._apply_snooze(chat_id, rid, title, due)
                done.append((rid, real_title))
            if len(done) == 1:
                rid, title = done[0]
                if self._snoozes_today(rid) >= self.SNOOZE_ESCALATE_AT:
                    return (T(lang, "reminder_snooze_escalate", title=title),
                            reminders.escalate_keyboard(rid, lang), remaining)
                return T(lang, "reminder_snoozed", title=title, when_rel=when_rel), None, remaining
            return (T(lang, "reminder_snoozed_multi", when_rel=when_rel,
                      items=", ".join(f"«{t}»" for _r, t in done)), None, remaining)
        # 'готово' — or «сегодня пропустим», which on a journal-invitation card is
        # also what a bare «готово» means (ADR-0005: no entry today).
        close_reason = "skipped" if (action == "amend" and params.get("done")) else "done"
        journal = payload.get("journal") if len(ids) == 1 else None
        if journal and close_reason == "done":
            close_reason = "skipped"
        closed = []
        for rid in ids:
            rem, disp = self._close_fired(chat_id, rid, close_reason)
            if rem is not None:
                closed.append((rem, disp))
        if not closed:
            return T(lang, "reminder_not_found"), None, remaining
        if len(closed) == 1:
            rem, disp = closed[0]
            if journal:
                return T(lang, "journal_skipped_today", category=journal), None, remaining
            if close_reason == "skipped":
                return T(lang, "reminder_skipped", title=rem["title"]), None, remaining
            return T(lang, "reminder_done", title=rem["title"], rid=disp), None, remaining
        return (T(lang, "reminder_done_multi",
                  items=", ".join(f"«{rem['title']}»" for rem, _d in closed)), None, remaining)

    def _drop_from_fired_pending(self, chat_id, rid):
        """A button resolved `rid`: take it out of a live reminder_fired pending (and
        free the slot when nothing is left) — never touching a foreign pending."""
        pending = store.pending_get(self.conn, chat_id)
        if not pending or pending.get("kind") != "reminder_fired":
            return
        payload = dict(pending["payload"])
        ids = [int(i) for i in (payload.get("reminder_ids") or [payload.get("reminder_id")])
               if i is not None and int(i) != int(rid)]
        if not ids:
            store.pending_clear(self.conn, chat_id)
            return
        titles = payload.get("titles") or []
        keep = [t for i, t in zip(payload.get("reminder_ids") or [], titles) if int(i) != int(rid)]
        payload.update({"reminder_ids": ids, "reminder_id": ids[0], "titles": keep})
        if keep:
            payload["title"] = keep[0]
        store.pending_set(self.conn, chat_id, "reminder_fired", payload, ttl_seconds=1800)

    def handle_reminder_callback(self, callback_id, chat_id, msg, data):
        """Inline buttons on fired / batch / re-ping / expiry cards (`rm|…`) and on the
        draft card (`dr|…`). Deterministic — no LLM; the card is edited in place to
        show the outcome and its dead buttons are dropped (ADR-0001)."""
        parsed = reminders.parse_callback(data)
        if not parsed:
            self.answer_callback(callback_id, "?")
            return
        kind, op, rid, arg = parsed
        lang = self.lang()
        message_id = msg.get("message_id")
        base_text = msg.get("text") or ""
        if kind == "dr":
            self._handle_draft_callback(callback_id, chat_id, msg, op, rid)
            return
        if op == "reopen":
            text = self.reopen_reminders(chat_id, lang, rid)
            pending = store.pending_get(self.conn, chat_id)
            if pending and pending.get("kind") == "reminder_expired":
                store.pending_clear(self.conn, chat_id)
            self.answer_callback(callback_id, T(lang, "card_reopened"))
            self.edit_message(chat_id, message_id, base_text + "\n— " + T(lang, "card_reopened"))
            self.reply(chat_id, text)
            return
        if op == "alld":
            payload = {"reminder_ids": rid, "reminder_id": rid[0]}
            text, _kb, _rest = self.resolve_fired_action(chat_id, lang, payload, "confirm", {})
            for one in rid:
                self._drop_from_fired_pending(chat_id, one)
                self._mark_fired_message_done(message_id, one)
            self.answer_callback(callback_id, T(lang, "card_all_done"))
            self.edit_message(chat_id, message_id, base_text + "\n— " + T(lang, "card_all_done"))
            self.reply(chat_id, text)
            return
        row = store.reminder_get(self.conn, rid)
        if row is None or int(row["chat_id"]) != int(chat_id):
            self.answer_callback(callback_id, "—")
            return
        card_ids = self.fired_reminders_for_message(message_id)
        batch = len(card_ids) > 1
        if op == "days":
            self.answer_callback(callback_id, "")
            self.edit_message(chat_id, message_id, base_text,
                              reply_markup=reminders.days_keyboard(rid, lang, self.tz_offset()))
            return
        if op == "back":
            self.answer_callback(callback_id, "")
            keyboard = (reminders.batch_keyboard(card_ids, lang) if batch
                        else reminders.fired_keyboard(rid, lang))
            self.edit_message(chat_id, message_id, base_text, reply_markup=keyboard)
            return
        if row["status"] != "active":
            self.answer_callback(callback_id, T(lang, "card_expired"))
            self.edit_message(chat_id, message_id, base_text + "\n" + T(lang, "card_expired"))
            return
        payload = {"reminder_id": rid, "title": row["title"]}
        if not batch:
            journal = self._journal_for_reminder_title(row["title"])
            if journal:
                payload["journal"] = journal
        keyboard = None
        if op in ("done", "skip"):
            action, params = ("amend", {"done": True}) if op == "skip" else ("confirm", {})
            text, keyboard, _rest = self.resolve_fired_action(chat_id, lang, payload, action, params)
            outcome = T(lang, "card_skipped" if op == "skip" else "card_done")
        else:
            offset = self.tz_offset()
            if op == "h1":
                due = (datetime.now(timezone.utc) + timedelta(minutes=60)).isoformat()
            elif op == "tmw":
                due = reminders.local_day_at(offset, 1, reminders.DEFAULT_SNOOZE_HOUR)
            else:   # day|n
                due = reminders.local_day_at(offset, int(arg), reminders.DEFAULT_SNOOZE_HOUR)
            text, keyboard, _rest = self.resolve_fired_action(
                chat_id, lang, payload, "amend", {"due_utc": due})
            outcome = T(lang, "card_snoozed",
                        when_rel=reminders.fmt_relative(due, offset, lang))
        self._drop_from_fired_pending(chat_id, rid)
        self._remember_reminder(rid)
        if batch:
            remaining = self._mark_fired_message_done(message_id, rid)
            outcome = f"{outcome}: «{row['title']}»"
            if keyboard is None and remaining:
                keyboard = reminders.batch_keyboard(remaining, lang)
        self.answer_callback(callback_id, outcome)
        self.edit_message(chat_id, message_id, base_text + "\n— " + outcome,
                          reply_markup=keyboard)
        if keyboard is not None and not batch:
            # The escalation question («перенести на день или закрыть?») is the card
            # itself now — say it once in chat too so the question is not only a caption.
            self.reply(chat_id, text)

    def _handle_draft_callback(self, callback_id, chat_id, msg, op, rid):
        lang = self.lang()
        message_id = msg.get("message_id")
        base_text = msg.get("text") or ""
        pending = store.pending_get(self.conn, chat_id)
        if not pending or pending.get("kind") != "reminder":
            self.answer_callback(callback_id, T(lang, "card_expired"))
            self.edit_message(chat_id, message_id, base_text + "\n" + T(lang, "card_expired"))
            return
        payload = pending["payload"]
        if op == "set":
            self.resolve_pending(chat_id, "confirm", {}, pending, lang)
            self.answer_callback(callback_id, T(lang, "card_set"))
            self.edit_message(chat_id, message_id, base_text + "\n— " + T(lang, "card_set"))
            return
        if op == "no":
            store.pending_clear(self.conn, chat_id)
            self.answer_callback(callback_id, T(lang, "card_cancelled"))
            self.edit_message(chat_id, message_id, base_text + "\n— " + T(lang, "card_cancelled"))
            return
        if op == "time":
            draft = {k: payload[k] for k in ("title", "recurrence", "src_msg_id", "note_msg_id")
                     if k in payload}
            draft.setdefault("recurrence", "none")
            draft["need"] = "time"
            store.pending_set(self.conn, chat_id, "reminder_partial", draft)
            self.answer_callback(callback_id, "🕒")
            self.edit_message(chat_id, message_id, base_text)
            self.reply(chat_id, T(lang, "reminder_need_time"))
            return
        # op == "move": reschedule the existing twin to the draft's time instead
        twin = store.reminder_get(self.conn, rid)
        if twin is None or twin["status"] != "active" or int(twin["chat_id"]) != int(chat_id):
            self.answer_callback(callback_id, T(lang, "card_expired"))
            return
        store.pending_clear(self.conn, chat_id)
        store.reminder_update_due(self.conn, twin["id"], payload["due_utc"])
        self._remember_reminder(twin["id"])
        self.answer_callback(callback_id, "↪️")
        self.edit_message(chat_id, message_id, base_text + "\n— ↪️")
        self.reply(chat_id, T(lang, "reminder_rescheduled",
                              rid=self.reminder_no(chat_id, twin["id"]), title=twin["title"],
                              when_local=reminders.fmt_local(payload["due_utc"], self.tz_offset()))
                   + "\n\n" + self._reminder_list_body(chat_id, lang))

    # -- deterministic yes/no/time on a draft or partial (ADR-0006) --

    _DRAFT_YES_RE = re.compile(
        r"^(?:да|давай|ставь|ставим|ок|окей|ok|okay|yes|yep|ага|угу|\+|✅|👍|конечно|"
        r"хорошо|ладно|идёт|идет|го|sure)[.! ]*$")
    _DRAFT_NO_RE = re.compile(
        r"^(?:нет|не надо|не нужно|не ставь|отмена|отмени|отбой|no|nope|cancel|"
        r"never mind)[.! ]*$")

    def _parse_draft_time(self, t, title):
        """A bare time for a draft/partial — «через 20 минут», «в 18:30», «на 9»,
        «завтра в 10», «завтра вечером» — with no subject words of its own."""
        if reminders.followup_extra_words(t, title):
            return None
        now = datetime.now(timezone.utc)
        if "послезавтра" in t or "day after tomorrow" in t:
            return self._followup_day_due(t, 2)
        if "завтра" in t or "tomorrow" in t:
            return self._followup_day_due(t, 1)
        if "полчас" in t or "half an hour" in t:
            return (now + timedelta(minutes=30)).isoformat()
        m = re.search(r"(\d{1,4})\s*(минут|минуты|минуту|мин|minutes?|mins?)\b", t)
        if m:
            return (now + timedelta(minutes=max(1, int(m.group(1))))).isoformat()
        m = re.search(r"(\d{1,3})\s*(часа|часов|час|ч|hours?|hrs?)\b", t)
        if m:
            return (now + timedelta(hours=max(1, int(m.group(1))))).isoformat()
        if re.search(r"(?:на|ещ[ёе]|через)\s+час(?:ок)?\b|\ban hour\b", t):
            return (now + timedelta(hours=1)).isoformat()
        m = re.fullmatch(r"(?:в|во|на|к|at|for)?\s*(\d{1,2})(?:[:.](\d{2}))?"
                         r"(?:\s*(am|pm|утра|ночи|дня|вечера))?[.! ]*", t)
        if m:
            hour, minute = int(m.group(1)), int(m.group(2) or 0)
            mer = m.group(3)
            if mer in ("pm", "дня", "вечера") and hour < 12:
                hour += 12
            elif mer in ("am", "ночи", "утра") and hour == 12:
                hour = 0
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                return None
            due = reminders.local_day_at(self.tz_offset(), 0, hour, minute, now=now)
            if reminders.parse_iso_utc(due) <= now:
                due = reminders.local_day_at(self.tz_offset(), 1, hour, minute, now=now)
            return due
        hour = reminders.part_of_day_hour(t)
        if hour is not None and not re.search(r"\d", t):
            due = reminders.local_day_at(self.tz_offset(), 0, hour, now=now)
            if reminders.parse_iso_utc(due) <= now:
                due = reminders.local_day_at(self.tz_offset(), 1, hour, now=now)
            return due
        return None

    def resolve_reminder_draft_text(self, chat_id, lang, pending, text):
        """«да» / «нет» / a bare time while a reminder draft or partial is pending —
        resolved before the router (the 13k-token confirm call is gone)."""
        if not pending or pending.get("kind") not in ("reminder", "reminder_partial"):
            return False
        t = str(text or "").strip().casefold()
        if not t or len(t) > 60:
            return False
        kind, payload = pending["kind"], pending["payload"]
        if self._DRAFT_NO_RE.fullmatch(t):
            if kind == "reminder":
                self.resolve_pending(chat_id, "cancel", {}, pending, lang)
            else:
                self.continue_partial_reminder(chat_id, lang, pending, "cancel", {})
            return True
        if kind == "reminder" and self._DRAFT_YES_RE.fullmatch(t):
            self.resolve_pending(chat_id, "confirm", {}, pending, lang)
            return True
        if kind == "reminder_partial" and payload.get("need") != "time":
            return False
        due = self._parse_draft_time(t, str(payload.get("title") or ""))
        if due is None:
            return False
        if kind == "reminder":
            self.resolve_pending(chat_id, "amend", {"due_utc": due}, pending, lang)
        else:
            self.continue_partial_reminder(chat_id, lang, pending, "amend", {"due_utc": due})
        return True

    def reminder_no(self, chat_id, rid):
        """User-facing display number (1..N) for an active reminder; falls back
        to the id if it isn't active (already fired/cancelled)."""
        n = store.reminder_display_no(self.conn, chat_id, rid)
        return n if n is not None else rid
