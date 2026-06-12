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


def fmt_local(due_iso, offset_hours):
    due = parse_iso_utc(due_iso)
    local = due + timedelta(hours=offset_hours)
    return local.strftime("%Y-%m-%d %H:%M")


def format_list(rows, offset_hours, lang):
    if not rows:
        return T(lang, "reminder_list_empty")
    lines = [T(lang, "reminder_list_header")]
    for row in rows:
        suffix = "" if row["recurrence"] == "none" else f" ({T(lang, 'recurrence_' + row['recurrence'])})"
        lines.append(f"  #{row['id']} {fmt_local(row['due_utc'], offset_hours)} — {row['title']}{suffix}")
    return "\n".join(lines)


def find_by_query(rows, params):
    """Find a reminder by explicit id or title substring."""
    rid = params.get("id")
    if rid is not None:
        try:
            rid = int(rid)
        except (TypeError, ValueError):
            rid = None
    if rid is not None:
        for row in rows:
            if row["id"] == rid:
                return row
        return None
    query = str(params.get("title_query") or params.get("title") or "").strip().casefold()
    if not query:
        return None
    for row in rows:
        if query in row["title"].casefold():
            return row
    return None
