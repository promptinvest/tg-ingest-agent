#!/usr/bin/env python3
"""Agreements skill: commitments the boss and Cara make to each other — short-term (with an
optional target time) or long-term/open-ended. PASSIVE memory by design: a dated agreement is
never turned into a reminder/nudge; Cara only surfaces it naturally in conversation. Validation,
local-time rendering, and lookup helpers (the store holds the rows)."""
from datetime import datetime, timedelta, timezone

from texts import T

PARTIES = ("boss", "cara", "both")
MAX_TEXT_CHARS = 400


def parse_iso_utc(value):
    """Parse an ISO timestamp to aware-UTC datetime; None when invalid/empty."""
    try:
        parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_party(value):
    v = str(value or "both").strip().lower()
    if v in ("he", "boss", "owner", "ты", "он", "босс"):
        return "boss"
    if v in ("she", "cara", "кара", "я"):
        return "cara"
    return "both"


def validate_add(params, now=None):
    """Validate router params into an agreement draft; None when unusable (no text)."""
    text = str(params.get("text") or "").strip()[:MAX_TEXT_CHARS]
    if not text:
        return None
    due = parse_iso_utc(params.get("due_utc"))
    # A past target time is harmless for a passive agreement, but a clearly-past date is more
    # likely a misparse than a real intent — keep it only if it's roughly in the future.
    if due is not None and now is not None and due < now - timedelta(hours=12):
        due = None
    return {"text": text, "party": normalize_party(params.get("party")),
            "due_utc": due.isoformat() if due else None}


def party_label(party, lang):
    ru = lang == "ru"
    return {"boss": ("ты" if ru else "you"),
            "cara": ("я" if ru else "I"),
            "both": ("мы" if ru else "we")}.get(party, "we")


def fmt_due(due_iso, offset_hours):
    due = parse_iso_utc(due_iso)
    if due is None:
        return ""
    local = due + timedelta(hours=offset_hours)
    return local.strftime("%Y-%m-%d %H:%M")


def format_list(rows, offset_hours, lang, now=None):
    if not rows:
        return T(lang, "agreement_list_empty")
    lines = [T(lang, "agreement_list_header")]
    for i, row in enumerate(rows, start=1):  # contiguous 1..N display numbers
        who = party_label(row["party"], lang)
        when = fmt_due(row["due_utc"], offset_hours)
        when_part = f" · {when}" if when else ""
        lines.append(f"  #{i} [{who}] {row['text']}{when_part}")
    return "\n".join(lines)


def find(rows, params):
    """Find an agreement by display number (1..N position in `rows`) or text substring.
    `rows` MUST be in the boss-facing display order (store.agreements_open)."""
    pos = params.get("id")
    if pos is not None:
        try:
            pos = int(pos)
        except (TypeError, ValueError):
            pos = None
    if pos is not None:
        return rows[pos - 1] if 1 <= pos <= len(rows) else None
    query = str(params.get("query") or params.get("text") or "").strip().casefold()
    if not query:
        return None
    for row in rows:
        if query in (row["text"] or "").casefold():
            return row
    return None
