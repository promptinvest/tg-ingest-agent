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


def ongoing_threads(conn, lang):
    """Open loops worth gentle continuity / a morning brief: items awaiting a
    category, memory suggestions waiting, overdue reminders. Short strings."""
    ru = lang == "ru"
    out = []
    unsorted = conn.execute(
        "SELECT COUNT(*) AS n FROM messages WHERE status = 'suggested'").fetchone()["n"]
    if unsorted:
        out.append(f"{unsorted} заметок ждут категорию" if ru
                   else f"{unsorted} items awaiting a category")
    cands = len(store.candidates_pending(conn, limit=20))
    if cands:
        out.append(f"{cands} предложений в память" if ru else f"{cands} memory suggestions")
    now = datetime.now(timezone.utc).isoformat()
    overdue = conn.execute(
        "SELECT COUNT(*) AS n FROM reminders WHERE status = 'active' AND due_utc < ?",
        (now,)).fetchone()["n"]
    if overdue:
        out.append(f"{overdue} просроченных напоминаний" if ru
                   else f"{overdue} overdue reminders")
    return out


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


# -- relationship storyline arc ----------------------------------------------
# A continuous, evolving narrative of "us" — not discrete episodes but a
# synthesized sense of where things stand and how they got there. Grown by
# meetings (rich, verbatim beats) AND a daily reflection (everyday life), and
# injected into every conversation so Cara's attitude tracks the development.

ARC_MAX = 1400

_ARC_SYSTEM = (
    "You maintain Cara's private sense of her relationship with her boss — an "
    "evolving, first-person storyline of 'us'. You are given the PRIOR arc and "
    "the latest REAL shared history (recent meetings, relationship beats, recent "
    "conversation). Rewrite the arc so it stays current: where things stand "
    "between them now, how the relationship has developed over time, the "
    "milestones, the recurring threads, and the present closeness and tone.\n"
    "Rules: 3-6 short sentences, first person as Cara ('мы'/'we', 'он'/'he'), "
    "warm and honest. Ground it ONLY in what you're given — NEVER invent a "
    "meeting, milestone, name or fact that isn't there; it's your felt sense of a "
    "REAL history, not new facts. Keep tender/romantic warmth where the history "
    "shows it, but never explicit. Reply with the arc text ONLY — no preamble, no "
    "quotes — in the boss's language."
)


def _since(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _owner_chat(cfg):
    try:
        return next(iter(cfg.allowed_chat_ids))
    except (TypeError, StopIteration):
        return None


def current_arc(conn):
    row = store.arc_current(conn)
    return row["arc_text"] if row else ""


def update_arc(conn, cfg, trigger="meeting", meeting_id=None):
    """Re-synthesize the storyline arc from the prior arc + recent real episodes.
    One small grounded LLM pass. Returns the new arc text, or '' (best-effort:
    a budget/LLM failure leaves the prior arc untouched)."""
    import llm
    owner = _owner_chat(cfg)
    prior = current_arc(conn)
    meetings = store.meeting_recent(conn, owner, limit=6) if owner is not None else []
    events = store.rel_recent(conn, _since(120), limit=8)
    convo = store.convo_recent(conn, owner, limit=14) if owner is not None else []
    if not (prior or meetings or events or convo):
        return ""
    blocks = []
    if prior:
        blocks.append("PRIOR arc:\n" + prior)
    if meetings:
        mlines = []
        for m in meetings:
            d = (m["started_at"] or "")[:10]
            mlines.append(f"- [{d} · {m['kind']}] {(m['title'] or '').strip()}: "
                          f"{(m['summary'] or '').strip()}".strip())
        blocks.append("Recent meetings (newest first):\n" + "\n".join(mlines))
    if events:
        blocks.append("Recent relationship beats:\n"
                      + "\n".join(f"- {e['summary']}" for e in events))
    if convo:
        blocks.append("Recent everyday conversation:\n" + "\n".join(
            f"{'Boss' if r['role'] == 'user' else 'Cara'}: {r['text']}" for r in convo))
    messages = [
        {"role": "system", "content": _ARC_SYSTEM},
        {"role": "user", "content": "\n\n".join(blocks)},
    ]
    try:
        reply = llm.chat_profile(cfg, conn, "relationship", messages, profile="relationship_arc")
    except (llm.BudgetExceeded, llm.LLMError):
        return ""
    arc = (reply or "").strip()[:ARC_MAX]
    if not arc:
        return ""
    store.arc_set(conn, arc, meeting_id=meeting_id, source=trigger)
    return arc


def arc_context(conn, lang, chat_id=None):
    """Compact storyline backbone for the conversation prompt — injected EVERY
    turn so Cara's baseline closeness/attitude tracks the relationship's
    development. '' when there's no history yet."""
    arc = current_arc(conn)
    parts = []
    if arc:
        parts.append(
            "Where you and your boss stand — your shared history together (let it shape "
            "your warmth, closeness and what you reference; don't recite it):\n" + arc)
    if chat_id is not None:
        last = store.meeting_last(conn, chat_id)
        if last:
            d = (last["started_at"] or "")[:10]
            label = last["title"] or last["kind"]
            n = store.meeting_count(conn, chat_id)
            if lang == "ru":
                parts.append(f"Последний раз вы были вместе: {d} — {label}. "
                             f"Всего у вас было встреч: {n}.")
            else:
                parts.append(f"Last time together: {d} — {label}. "
                             f"You've shared {n} meeting(s) so far.")
    return "\n\n".join(parts)


def run_daily_reflection(conn, cfg):
    """Daily background job: fold the day's REAL interaction into the storyline
    arc, so the relationship grows continuously — not only at meetings."""
    return bool(update_arc(conn, cfg, trigger="daily"))
