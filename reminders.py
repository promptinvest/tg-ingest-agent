#!/usr/bin/env python3
"""Reminders skill: draft validation, recurrence, local-time rendering."""
import re
from datetime import datetime, timedelta, timezone

import store
from texts import T

RECURRENCES = ("none", "daily", "weekly")
MAX_TITLE_CHARS = 200


# -- fired-reminder follow-up subject guard -----------------------------------
# A reply that is STILL about the fired reminder is built only from this
# scaffold: defer/ack verbs, reminder references, time words and numbers.
# Anything beyond it («…завтра 10:30 — Эрика», «напомни завтра про отчёт»)
# introduces the message's OWN subject — that is a NEW command for the normal
# router, never a snooze of whatever fired last (the 2026-07-22 incident:
# «Поставь напоминание на завтра 10:30 - Эрика» was eaten as a snooze of the
# gratitude daily and the «Эрика» subject was silently dropped).

_FOLLOWUP_SCAFFOLD = frozenset({
    # ru particles/prepositions/fillers
    "в", "на", "до", "к", "и", "же", "ну", "бы", "а", "с", "о", "об", "при",
    "за", "по", "из", "у", "через", "это", "этот", "его", "её", "ее", "их",
    "ещё", "еще", "давай", "давайте", "лучше", "снова", "опять", "потом",
    "позже", "чуть", "пока", "ладно", "хорошо", "ок", "окей", "да", "угу",
    "ага", "пожалуйста", "спасибо",
    # reminder references
    "напоминание", "напоминания", "напоминалку", "напоминалка", "будильник",
    "время",
    # day/time words
    "завтра", "послезавтра", "сегодня", "утром", "утра", "утро", "вечером",
    "вечера", "вечер", "днем", "днём", "дня", "день", "ночью", "ночи", "ночь",
    "полдень", "полудня", "обед", "обеда",
    # units
    "час", "часа", "часов", "часок", "часик", "часика", "ч", "мин", "м",
    "полчаса", "полчасика", "минут", "минуты", "минуту", "минутку",
    # common verb forms (stems below catch the rest)
    "поставь", "сделай", "повтори",
    # en
    "remind", "reminder", "me", "it", "the", "this", "that", "a", "an", "at",
    "in", "on", "to", "until", "till", "for", "of", "later", "again",
    "tomorrow", "today", "tonight", "morning", "evening", "noon", "night",
    "day", "after",  # "day after tomorrow" — the EN twin of «послезавтра»
    "snooze", "move", "push", "postpone", "delay", "please", "ok", "okay",
    "yes", "yep", "half", "hour", "hours", "hr", "hrs", "minute", "minutes",
    "min", "mins", "am", "pm", "oclock", "skip", "done", "close", "closed",
})

_FOLLOWUP_STEMS = ("отлож", "перенес", "перенос", "сдвин", "напомн", "пропус",
                   "закры", "закро", "готов", "сделан", "выполн", "полчас",
                   "минут", "часик")


def followup_extra_words(text, title=""):
    """Content words of `text` that are neither follow-up scaffold nor words of
    the bound reminder's title (inflection-tolerant 4-char stem match).
    Non-empty ⇒ the message carries its OWN subject and must NOT be treated as
    an ack/snooze of the last-fired reminder."""
    title_words = [w for w in re.split(r"\W+", str(title or "").casefold()) if w]
    extras = []
    for w in re.split(r"\W+", str(text or "").casefold()):
        if not w or w.isdigit():
            continue
        if w in _FOLLOWUP_SCAFFOLD or any(w.startswith(s) for s in _FOLLOWUP_STEMS):
            continue
        if w in title_words:
            continue
        if len(w) >= 4 and any(len(tw) >= 4 and w[:4] == tw[:4] for tw in title_words):
            continue
        extras.append(w)
    return extras


_TIME_ONLY_REQUESTS = (
    re.compile(
        r"^(?:напомни(?:\s+мне)?(?:\s+пожалуйста)?|"
        r"поставь(?:\s+мне)?(?:\s+пожалуйста)?\s+напоминание|"
        r"создай(?:\s+мне)?(?:\s+пожалуйста)?\s+напоминание)"
        r"\s+(?:на|в)\s*(?P<hour>[01]?\d|2[0-3])[:.](?P<minute>[0-5]\d)\s*[.!?]?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:remind\s+me(?:\s+please)?|set(?:\s+me)?(?:\s+please)?\s+(?:a\s+)?reminder)"
        r"\s+(?:at|for)\s*(?P<hour>[01]?\d|2[0-3])[:.](?P<minute>[0-5]\d)\s*[.!?]?$",
        re.IGNORECASE,
    ),
)


def parse_time_only_request(text, offset_hours, now=None):
    """Recognize an unmistakable reminder request that supplies only HH:MM.

    This deterministic narrow path keeps ``напомни в 21:15`` from depending on
    model confidence.  Requests that contain a subject deliberately do not match
    and continue through the normal router.  A time already passed locally rolls
    to tomorrow, which is the only future interpretation of a time-only request.
    """
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    match = None
    for pattern in _TIME_ONLY_REQUESTS:
        match = pattern.fullmatch(value)
        if match is not None:
            break
    if match is None:
        return None
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    offset = timedelta(hours=float(offset_hours))
    local_now = now + offset
    local_due = local_now.replace(hour=int(match.group("hour")),
                                  minute=int(match.group("minute")),
                                  second=0, microsecond=0)
    due = local_due - offset
    if due <= now:
        due += timedelta(days=1)
    return {"due_utc": due.isoformat(), "recurrence": "none"}


def title_from_forward(text):
    """Turn forwarded text into a reminder title without executing its wording.

    Only cosmetic request framing is removed.  The result remains untrusted data
    and is shown back in the normal reminder confirmation before any reminder is
    created.
    """
    original = re.sub(r"\s+", " ", str(text or "")).strip()
    if not original:
        return ""
    title = re.sub(
        r"^напомни(?:\s+мне)?(?:\s+пожалуйста)?\s+", "", original,
        count=1, flags=re.IGNORECASE,
    )
    if title != original:
        title = re.sub(
            r"^(?:(?:сегодня|завтра|послезавтра)\s+)?"
            r"(?:утром|дн[её]м|вечером|ночью)\s+(?:у\s+тебя\s+)?",
            "", title, count=1, flags=re.IGNORECASE,
        )
    else:
        title = re.sub(
            r"^remind\s+me(?:\s+please)?\s+", "", original,
            count=1, flags=re.IGNORECASE,
        )
        if title != original:
            title = re.sub(
                r"^(?:(?:today|tomorrow|tonight|this\s+evening)\s+)?(?:to\s+)?",
                "", title, count=1, flags=re.IGNORECASE,
            )
    return (title.strip(" .,!?:;—-") or original)[:MAX_TITLE_CHARS]


_CALENDAR_DATE = re.compile(
    r"(?<!\d)(?P<day>[0-3]?\d)[./-](?P<month>[01]?\d)"
    r"(?:[./-](?P<year>\d{2}|\d{4}))?(?!\d)")
_CALENDAR_TIME_WITH_PREP = re.compile(
    r"\b(?:в|at)\s*(?P<hour>[01]?\d|2[0-3])"
    r"(?:(?::|\.)(?P<minute>[0-5]\d))?"
    r"(?:\s*(?P<meridiem>am|pm|утра|дня|вечера|ночи)\b)?\b"
    r"(?![:.]\d)", re.IGNORECASE)
_CALENDAR_TIME_CLOCK = re.compile(
    r"(?<![\d.])(?P<hour>[01]?\d|2[0-3])[:.]"
    r"(?P<minute>[0-5]\d)(?:\s*(?P<meridiem>am|pm|утра|дня|вечера|ночи)\b)?"
    r"(?!\d)", re.IGNORECASE)


def _valid_calendar_date_candidate(match):
    """Reject clock-like DD.MM matches that cannot be calendar dates.

    A bare dotted clock such as ``19.00`` matches the deliberately permissive
    numeric-date regex too.  Validate candidates before the ambiguity check so
    ``25.08 19.00`` has one date and one clock, while real competing dates still
    make the forward ambiguous.  Year-specific validity is checked later using
    the actual/inferred year; 2000 keeps an otherwise valid 29.02 candidate.
    """
    try:
        datetime(2000, int(match.group("month")), int(match.group("day")))
    except (TypeError, ValueError):
        return False
    return True


def calendar_details_from_forward(text, offset_hours, now=None):
    """Extract one numeric local date/time and a title from forwarded DATA.

    This deliberately is not a general instruction parser. It exists only after
    the owner opened a calendar draft; fixed numeric fields are interpreted and
    the remaining text becomes an inert event title. No wording in the forward
    is executed. Missing/invalid fields return an empty/partial dict.
    """
    original = re.sub(r"\s+", " ", str(text or "")).strip()
    prepared_times = list(_CALENDAR_TIME_WITH_PREP.finditer(original))
    # A dotted clock inside an explicit «в 19.00» also resembles DD.MM. Do not
    # count that same span as a second date when deciding ambiguity.
    date_matches = [
        candidate for candidate in _CALENDAR_DATE.finditer(original)
        if _valid_calendar_date_candidate(candidate)
        and not any(candidate.start() < tm.end() and tm.start() < candidate.end()
                    for tm in prepared_times)
    ]
    if len(date_matches) != 1:
        return {}
    date_match = date_matches[0]
    time_matches = [
        candidate for candidate in prepared_times
        if candidate.end() <= date_match.start() or candidate.start() >= date_match.end()
    ]
    # Collect clocks even when one explicit в/at clock exists: «в 19 или
    # 20:00» is still ambiguous. Overlapping spans are the SAME clock written
    # with a preposition and must be deduplicated.
    for candidate in _CALENDAR_TIME_CLOCK.finditer(original):
        if not (candidate.end() <= date_match.start()
                or candidate.start() >= date_match.end()):
            continue
        if any(candidate.start() < current.end()
               and current.start() < candidate.end() for current in time_matches):
            continue
        time_matches.append(candidate)
    # Never guess which pair wins in a reschedule/change forward. It is safer to
    # keep the draft and ask than create the old of two explicitly named events.
    if len(time_matches) != 1:
        return {}
    time_match = time_matches[0]
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    offset = timedelta(hours=float(offset_hours))
    local_now = now + offset
    year_raw = date_match.group("year")
    year = local_now.year if not year_raw else int(year_raw)
    if year_raw and len(year_raw) == 2:
        year += 2000
    try:
        hour = int(time_match.group("hour"))
        meridiem = str(time_match.group("meridiem") or "").casefold()
        if meridiem in {"pm", "дня", "вечера"} and hour < 12:
            hour += 12
        elif meridiem in {"am", "утра", "ночи"} and hour == 12:
            hour = 0
        local_due = datetime(
            year, int(date_match.group("month")), int(date_match.group("day")),
            hour, int(time_match.group("minute") or 0),
            tzinfo=timezone.utc,
        )
    except ValueError:
        return {}
    due = local_due - offset
    if not year_raw and due <= now:
        try:
            local_due = local_due.replace(year=year + 1)
        except ValueError:  # 29 February into a non-leap year
            return {}
        due = local_due - offset
    # Delete only the two parsed spans; everything else is an inert title.
    chars = list(original)
    for match in (date_match, time_match):
        for i in range(match.start(), match.end()):
            chars[i] = " "
    title = re.sub(r"\s+", " ", "".join(chars)).strip(" .,!?:;—-👇")
    return {
        "title": title[:MAX_TITLE_CHARS],
        "due_utc": due.isoformat(),
        "recurrence": "none",
    }


def parse_iso_utc(value):
    """Parse an ISO timestamp to aware-UTC datetime; None when invalid."""
    try:
        parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def validate_draft(params, now=None):
    """Validate router params into a reminder draft; None when unusable."""
    now = now or datetime.now(timezone.utc)
    title = str(params.get("title") or "").strip()[:MAX_TITLE_CHARS]
    due = parse_iso_utc(params.get("due_utc"))
    recurrence = str(params.get("recurrence") or "none").strip().lower()
    if recurrence not in RECURRENCES:
        recurrence = "none"
    if not title or due is None:
        return None
    if due < now - timedelta(minutes=1):
        # A RECURRING reminder whose time-of-day has already passed today starts at the NEXT
        # occurrence, not rejected as "past" — fixes a daily "на 22:00" set after 22:00 that
        # looped asking for the time. A one-shot in the past is still unusable.
        if recurrence in ("daily", "weekly"):
            due = parse_iso_utc(next_due(due.isoformat(), recurrence, now))
        else:
            return None
    return {"title": title, "due_utc": due.isoformat(), "recurrence": recurrence}


def next_due(due_iso, recurrence, now=None):
    """Next occurrence after firing; None for one-shot reminders."""
    if recurrence not in ("daily", "weekly"):
        return None
    now = now or datetime.now(timezone.utc)
    step = timedelta(days=1 if recurrence == "daily" else 7)
    due = parse_iso_utc(due_iso)
    while due <= now:
        due += step
    return due.isoformat()


def roll_forward(due, now, max_days=400):
    """Push a past `due` forward by whole days until it's in the future (preserving the
    local time-of-day, since a whole-day step keeps UTC↔local aligned). Used when a
    reschedule resolves to a time that's already passed (e.g. a misparsed 'today' at a
    late hour) so the reminder lands in the future instead of re-firing immediately."""
    d = due
    for _ in range(max_days):
        if d > now:
            return d
        d += timedelta(days=1)
    return d


def fmt_local(due_iso, offset_hours):
    due = parse_iso_utc(due_iso)
    local = due + timedelta(hours=offset_hours)
    return local.strftime("%Y-%m-%d %H:%M")


def _row_get(row, key):
    try:                                  # sqlite3.Row -> IndexError; dict -> KeyError
        return row[key]
    except (KeyError, IndexError):
        return None


def reminder_status_mark(row, lang, now=None):
    """A short status marker (with its own icon) for a reminder a list shows:
    a fired-but-unconfirmed one-shot ('⚠️ сработало, ждёт «готово»'), one moved by a
    reschedule and re-armed ('🔄 перенесено' — NOT a warning), or one simply overdue
    ('⚠️ просрочено'). '' for a normal pending/recurring one. So a moved/overdue reminder
    never looks the same as a fresh future one, and a reschedule reads as done — not as
    'сработало'."""
    now = now or datetime.now(timezone.utc)
    fired = _row_get(row, "last_fired_at")
    if row["recurrence"] == "none" and fired:
        return "⚠️ " + T(lang, "reminder_mark_fired")
    due = parse_iso_utc(row["due_utc"])
    # Moved by a reschedule (prev_due_utc set), re-armed (not fired) and still ahead. Only
    # for one-shots — a recurring reminder sets prev_due_utc every time it auto-re-arms,
    # which is NOT a reschedule and must not read as 'перенесено'.
    if (row["recurrence"] == "none" and _row_get(row, "prev_due_utc")
            and not fired and due is not None and due > now):
        return "🔄 " + T(lang, "reminder_mark_rescheduled")
    if due is not None and due <= now:
        return "⚠️ " + T(lang, "reminder_mark_overdue")
    return ""


def format_list(rows, offset_hours, lang, now=None):
    if not rows:
        return T(lang, "reminder_list_empty")
    lines = [T(lang, "reminder_list_header")]
    for i, row in enumerate(rows, start=1):  # contiguous 1..N display numbers
        suffix = "" if row["recurrence"] == "none" else f" ({T(lang, 'recurrence_' + row['recurrence'])})"
        mark = reminder_status_mark(row, lang, now)
        if mark:
            suffix += f" — {mark}"   # mark carries its own icon (⚠️ / 🔄)
        lines.append(f"  #{i} {fmt_local(row['due_utc'], offset_hours)} — {row['title']}{suffix}")
    return "\n".join(lines)


def find_by_query(rows, params):
    """Find a reminder by its display number (1..N position in `rows`, the
    boss-facing active list) or title substring. `rows` MUST be in display order
    (store.reminders_active) so the position matches what the boss sees."""
    # Same normalization as the note path (store.note_no_value): the router
    # emits «#2» / «2.» / « 2 » for reminders too, and a bare int() turned
    # «перенеси #2 на 17:00» into a false not-found on a reminder that is
    # right there in the list (2026-07-27).
    pos = store.note_no_value(params.get("id"))
    if pos is not None:
        return rows[pos - 1] if 1 <= pos <= len(rows) else None
    query = str(params.get("title_query") or params.get("title") or "").strip().casefold()
    if not query:
        return None
    for row in rows:
        if query in row["title"].casefold():
            return row
    return None
