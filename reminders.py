#!/usr/bin/env python3
"""Reminders skill: draft validation, recurrence, local-time rendering."""
from datetime import datetime, timedelta, timezone

from texts import T

RECURRENCES = ("none", "daily", "weekly")
MAX_TITLE_CHARS = 200


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
    if not title or due is None or due < now - timedelta(minutes=1):
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


def reminder_status_mark(row, lang, now=None):
    """A short status marker for a reminder a list shows: a one-shot that already
    fired but wasn't confirmed ('сработало, ждёт «готово»') or one simply overdue
    ('просрочено'). '' for a normal pending/recurring one. So an overdue reminder in
    the list never looks the same as a future one — the boss isn't left guessing why
    yesterday's reminder is still there."""
    now = now or datetime.now(timezone.utc)
    try:                                  # sqlite3.Row -> IndexError; dict -> KeyError
        fired = row["last_fired_at"]
    except (KeyError, IndexError):
        fired = None
    if row["recurrence"] == "none" and fired:
        return T(lang, "reminder_mark_fired")
    due = parse_iso_utc(row["due_utc"])
    if due is not None and due <= now:
        return T(lang, "reminder_mark_overdue")
    return ""


def format_list(rows, offset_hours, lang, now=None):
    if not rows:
        return T(lang, "reminder_list_empty")
    lines = [T(lang, "reminder_list_header")]
    for i, row in enumerate(rows, start=1):  # contiguous 1..N display numbers
        suffix = "" if row["recurrence"] == "none" else f" ({T(lang, 'recurrence_' + row['recurrence'])})"
        mark = reminder_status_mark(row, lang, now)
        if mark:
            suffix += f" — ⚠️ {mark}"
        lines.append(f"  #{i} {fmt_local(row['due_utc'], offset_hours)} — {row['title']}{suffix}")
    return "\n".join(lines)


def find_by_query(rows, params):
    """Find a reminder by its display number (1..N position in `rows`, the
    boss-facing active list) or title substring. `rows` MUST be in display order
    (store.reminders_active) so the position matches what the boss sees."""
    pos = params.get("id")
    if pos is not None:
        try:
            pos = int(pos)
        except (TypeError, ValueError):
            pos = None
    if pos is not None:
        return rows[pos - 1] if 1 <= pos <= len(rows) else None
    query = str(params.get("title_query") or params.get("title") or "").strip().casefold()
    if not query:
        return None
    for row in rows:
        if query in row["title"].casefold():
            return row
    return None
