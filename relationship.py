#!/usr/bin/env python3
"""Relationship continuity: evidence-based working history, never fabricated.
Every line traces to a real stored row (messages, reminders, events). Used by
the working_history action and the weekly digest.
"""
from datetime import datetime, timedelta, timezone

import store
from texts import T


def log_event(conn, kind, summary, importance=1, source_table=None, source_id=None, title=None):
    store.rel_add(conn, kind, summary, importance, source_table, source_id, title=title)


def render_working_history(conn, lang, days=30):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    ru = lang == "ru"
    confirmed = conn.execute(
        "SELECT COUNT(*) AS n FROM messages WHERE status='confirmed' AND received_at >= ?",
        (since,),
    ).fetchone()["n"]
    reminders_n = conn.execute(
        "SELECT COUNT(*) AS n FROM reminders WHERE created_at >= ?", (since,)
    ).fetchone()["n"]
    cats = conn.execute("SELECT COUNT(*) AS n FROM categories").fetchone()["n"]
    confirmed_prefs = len(store.boss_items(conn, "confirmed"))
    events = store.rel_recent(conn, since, limit=6)
    lines = [T(lang, "working_history_header", days=days)]
    if ru:
        lines.append(f"• Сохранила и разложила: {confirmed} сообщений по {cats} категориям")
        lines.append(f"• Поставила напоминаний: {reminders_n}")
        lines.append(f"• Запомнила о вас (подтверждено): {confirmed_prefs}")
    else:
        lines.append(f"• Saved & filed: {confirmed} messages across {cats} categories")
        lines.append(f"• Reminders set: {reminders_n}")
        lines.append(f"• Confirmed things about you: {confirmed_prefs}")
    if events:
        lines.append(T(lang, "working_history_moments"))
        lines.extend(f"  – {e['summary']}" for e in events)
    if confirmed == 0 and reminders_n == 0 and not events:
        return T(lang, "working_history_empty")
    return "\n".join(lines)
