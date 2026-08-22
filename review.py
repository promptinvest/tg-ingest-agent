#!/usr/bin/env python3
"""Performance review skill.

Cara reports how she did over a period: activity, what she learned
(categories, corrections, habits, preferences), recorded communication
issues, and spend — as a chat message in the boss's language, and as an
exportable Markdown report meant to be fed back into VS Code to improve
the solution. Deterministic (no LLM tokens): everything comes straight
from the database.
"""
from datetime import datetime, timedelta, timezone
import json
import re
import statistics

import llm
import store
from common import scrub_secrets
from texts import TEXTS

PERIOD_DAYS = {"day": 1, "week": 7, "month": 30}


def similar_categories(conn):
    """Pairs of existing categories whose names look like variants of one
    another ("AI tools" ↔ "AI Tools & Resources") — surfaced in the review so
    the boss can fold them with «объедини X в Y». Deterministic, no LLM."""
    names = [r["name"] for r in store.category_counts(conn)]
    pairs = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if llm.match_category_fuzzy(a, [b]):
                pairs.append((a, b))
    return pairs


def journal_digest(conn, lang, days=7):
    """One-line rollup of journal activity over the last `days`, or None when
    there are no journals / no entries. Shared by the weekly review and the
    morning brief."""
    journals = store.journal_categories(conn)
    if not journals:
        return None
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    parts = []
    for name in journals:
        # COUNT, not len(rows): the row helper caps at 200, so a busy journal
        # reported exactly «200» in every digest once it passed that.
        n = store.journal_count(conn, name, since)
        if n:
            parts.append(f"{name} — {n}")
    if not parts:
        return None
    ru = lang == "ru"
    return ("📔 Дневники: " if ru else "📔 Journals: ") + "; ".join(parts)


# Weekday names in the form that reads naturally after Russian "в …" / English
# "on …" (Russian uses the accusative: в понедельник / в среду / в пятницу).
WEEKDAY_NAMES = {
    "ru": ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу",
           "воскресенье"],
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
}


def weekday_name(lang, weekday):
    names = WEEKDAY_NAMES.get(lang, WEEKDAY_NAMES["en"])
    return names[weekday % 7]


def next_review_utc(now_utc, tz_offset, weekday, hour):
    """UTC datetime of the next weekly review occurrence strictly after now,
    given the boss's local timezone offset and the configured local weekday/hour."""
    local = now_utc + timedelta(hours=tz_offset)
    target = local.replace(hour=hour, minute=0, second=0, microsecond=0) \
        + timedelta(days=(weekday - local.weekday()) % 7)
    if target <= local:
        target += timedelta(days=7)
    return target - timedelta(hours=tz_offset)


def normalize_period(value):
    value = str(value or "week").strip().lower()
    aliases = {"today": "day", "сегодня": "day", "день": "day",
               "неделя": "week", "weekly": "week",
               "месяц": "month", "monthly": "month"}
    value = aliases.get(value, value)
    return value if value in PERIOD_DAYS else "week"


def _percentile(values, quantile):
    """Linear-interpolated percentile for a small deterministic latency set."""
    ordered = sorted(float(v) for v in values if v is not None)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * float(quantile)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _latency_summary(rows):
    values = [r["seconds"] for r in rows if r["seconds"] is not None]
    return {"calls": len(values), "p50": _percentile(values, 0.50),
            "p95": _percentile(values, 0.95)}


def collect(conn, period):
    period = normalize_period(period)
    days = PERIOD_DAYS[period]
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).isoformat()
    data = {"period": period, "days": days, "since": since, "now": now.isoformat()}
    data["messages"] = conn.execute(
        "SELECT status, COUNT(*) AS n FROM messages WHERE received_at >= ?"
        " GROUP BY status ORDER BY status", (since,),
    ).fetchall()
    data["conversation_turns"] = conn.execute(
        "SELECT role, COUNT(*) AS n FROM conversation WHERE ts>=?"
        " GROUP BY role ORDER BY role", (since,),
    ).fetchall()
    data["new_categories"] = [r["name"] for r in conn.execute(
        "SELECT name FROM categories WHERE created_at >= ? ORDER BY created_at", (since,),
    ).fetchall()]
    data["corrections"] = conn.execute(
        "SELECT suggested, corrected, COUNT(*) AS n FROM feedback WHERE ts >= ?"
        " GROUP BY suggested, corrected ORDER BY n DESC LIMIT 10", (since,),
    ).fetchall()
    data["habits"] = [r for r in store.pref_all(conn) if r["key"].startswith("auto_cat:")]
    data["new_prefs"] = conn.execute(
        "SELECT key, value FROM preferences WHERE updated_at >= ?"
        " AND key NOT LIKE 'auto_cat%' ORDER BY key", (since,),
    ).fetchall()
    data["reminders_set"] = conn.execute(
        "SELECT COUNT(*) AS n FROM reminders WHERE created_at >= ?", (since,),
    ).fetchone()["n"]
    data["issue_counts"] = store.issue_counts(conn, since)
    data["issue_examples"] = store.issues_recent(conn, since, limit=10)
    # `converse_action_claim_retry` and `_repair_failed` belong here for the same
    # reason as the claim itself: a model that claims an action TWICE in one turn,
    # or a repair pass that cannot produce an honest reply, is the strongest signal
    # there is that the guard is misfiring — and it is exactly what the weekly
    # pattern list exists to surface.
    actionable = ("unclear_request", "out_of_scope", "stt_failed", "ingest_failed",
                  "converse_artifact_claim", "converse_action_claim",
                  "converse_action_claim_retry", "converse_action_repair_failed",
                  "converse_ungrounded_number", "correction_unresolved")
    data["open_issue_patterns"] = store.issue_open_patterns(conn, actionable, limit=20)
    data["resolved_issue_patterns"] = store.issues_resolved(conn, since, limit=20)
    data["spend_by_skill"] = conn.execute(
        "SELECT skill, COUNT(*) AS calls, COALESCE(SUM(cost_usd), 0) AS cost"
        " FROM llm_usage WHERE ts >= ? GROUP BY skill ORDER BY cost DESC", (since,),
    ).fetchall()
    data["healthcheck_calls"] = sum(
        r["calls"] for r in data["spend_by_skill"] if r["skill"] == "healthcheck")
    data["healthcheck_cost"] = sum(
        r["cost"] for r in data["spend_by_skill"] if r["skill"] == "healthcheck")
    data["functional_calls"] = sum(
        r["calls"] for r in data["spend_by_skill"] if r["skill"] != "healthcheck")
    data["functional_cost"] = sum(
        r["cost"] for r in data["spend_by_skill"] if r["skill"] != "healthcheck")
    latency_rows = conn.execute(
        "SELECT skill, seconds FROM llm_usage WHERE ts>=?"
        " AND kind IN ('chat','embed') AND seconds IS NOT NULL ORDER BY id",
        (since,),
    ).fetchall()
    functional_latency_rows = [r for r in latency_rows if r["skill"] != "healthcheck"]
    health_latency_rows = [r for r in latency_rows if r["skill"] == "healthcheck"]
    data["functional_latency"] = _latency_summary(functional_latency_rows)
    data["healthcheck_latency"] = _latency_summary(health_latency_rows)
    latency_by_skill = {}
    for row in functional_latency_rows:
        latency_by_skill.setdefault(row["skill"], []).append(row)
    data["latency_by_skill"] = [
        {"skill": skill, **_latency_summary(rows)}
        for skill, rows in sorted(latency_by_skill.items())
    ]
    data["ask_count"] = conn.execute(
        "SELECT COUNT(*) AS n FROM llm_usage WHERE skill='ask' AND kind='chat' AND ts >= ?",
        (since,),
    ).fetchone()["n"]
    data["confirmed_about_you"] = store.boss_items(conn, "confirmed")
    data["pending_candidates"] = store.candidates_pending(conn, limit=20)
    # saved items by category (confirmed only)
    data["by_category"] = conn.execute(
        "SELECT category, COUNT(*) AS n FROM messages WHERE received_at >= ?"
        " AND status='confirmed' AND category IS NOT NULL"
        " GROUP BY category ORDER BY n DESC LIMIT 15", (since,),
    ).fetchall()
    # facts learned this period (extracted at ingest)
    data["facts_extracted"] = conn.execute(
        "SELECT COUNT(*) AS n FROM facts f JOIN messages m ON f.message_id = m.id"
        " WHERE m.received_at >= ?", (since,),
    ).fetchone()["n"]
    data["facts_learned"] = data["facts_extracted"]  # compatibility for older callers
    data["reminder_closures"] = conn.execute(
        "SELECT COALESCE(close_reason, status) AS reason, COUNT(*) AS n FROM reminders"
        " WHERE closed_at>=? GROUP BY COALESCE(close_reason, status) ORDER BY reason",
        (since,),
    ).fetchall()
    data["reminder_events"] = conn.execute(
        "SELECT event, COUNT(*) AS n FROM reminder_events"
        " WHERE ts>=? GROUP BY event ORDER BY event",
        (since,),
    ).fetchall()
    data["reminder_closure_counts"] = {
        r["reason"]: r["n"] for r in data["reminder_closures"]}
    data["reminder_event_counts"] = {
        r["event"]: r["n"] for r in data["reminder_events"]}
    data["reminders_done"] = sum(
        r["n"] for r in data["reminder_closures"] if r["reason"] == "done"
    )
    data["reminders_fired_unacked"] = conn.execute(
        "SELECT COUNT(*) AS n FROM reminders WHERE status='active' AND recurrence='none'"
        " AND last_fired_at IS NOT NULL",
    ).fetchone()["n"]
    data["reminders_overdue"] = conn.execute(
        "SELECT COUNT(*) AS n FROM reminders WHERE status='active' AND due_utc<?"
        " AND (last_fired_at IS NULL OR last_fired_at<due_utc)",
        (data["now"],),
    ).fetchone()["n"]
    # model fallback incidents (logged to trace_events by the llm gateway)
    data["fallback_count"] = conn.execute(
        "SELECT COUNT(*) AS n FROM trace_events WHERE stage = 'llm.fallback' AND ts >= ?",
        (since,),
    ).fetchone()["n"]
    data["fallbacks"] = conn.execute(
        "SELECT ts, skill, message FROM trace_events WHERE stage = 'llm.fallback'"
        " AND ts >= ? ORDER BY id DESC LIMIT 10", (since,),
    ).fetchall()
    data["fallback_trace_count"] = conn.execute(
        "SELECT COUNT(DISTINCT trace_id) AS n FROM trace_events"
        " WHERE stage='llm.fallback' AND ts>=?", (since,),
    ).fetchone()["n"]
    data["failover_served_count"] = conn.execute(
        "SELECT COUNT(*) AS n FROM trace_events"
        " WHERE stage='llm.failover_served' AND ts>=?", (since,),
    ).fetchone()["n"]
    data["failover_failed_count"] = conn.execute(
        "SELECT COUNT(*) AS n FROM trace_events"
        " WHERE stage='llm.failover_failed' AND ts>=?", (since,),
    ).fetchone()["n"]
    data["fallback_legacy_trace_count"] = conn.execute(
        "SELECT COUNT(DISTINCT f.trace_id) AS n FROM trace_events f"
        " WHERE f.stage='llm.fallback' AND f.ts>=?"
        " AND NOT EXISTS (SELECT 1 FROM trace_events o WHERE o.trace_id=f.trace_id"
        " AND o.stage IN ('llm.failover_served','llm.failover_failed'))", (since,),
    ).fetchone()["n"]
    # trace summary: how many units of work, by outcome
    data["trace_status"] = conn.execute(
        "SELECT kind, CASE WHEN status IN ('ok','finished') THEN 'ok' ELSE status END AS status,"
        " COUNT(*) AS n FROM traces WHERE started_at>=? GROUP BY kind,"
        " CASE WHEN status IN ('ok','finished') THEN 'ok' ELSE status END"
        " ORDER BY kind, status", (since,),
    ).fetchall()
    data["rel_events"] = store.rel_recent(conn, since, limit=8)
    # corrections Cara has learned (standing guidance from his feedback) and ones
    # that recur despite being learned (flagged as needing a code fix)
    data["corrections_learned"] = [
        r for status in ("inferred", "confirmed")
        for r in store.boss_items(conn, status, limit=50)
        if r["source_table"] == "correction"]
    data["corrections_unresolved"] = conn.execute(
        "SELECT detail, occurrences AS n, first_seen_at, last_seen_at"
        " FROM issue_patterns WHERE kind='correction_unresolved' AND status='open'"
        " AND occurrences>=2 ORDER BY occurrences DESC LIMIT 10",
    ).fetchall()
    # scorecard: how well she did, not just how much
    status_counts = {r["status"]: r["n"] for r in data["messages"]}
    data["confirmed_count"] = status_counts.get("confirmed", 0)
    data["corrections_count"] = sum(r["n"] for r in data["corrections"])
    # First-guess success computed from THIS period's messages themselves —
    # feedback rows also cover recategorized OLD notes, so subtracting them
    # produced a negative «категорий с первого раза: -8/2».
    data["first_guess_kept"] = conn.execute(
        "SELECT COUNT(*) AS n FROM messages WHERE received_at >= ?"
        " AND status = 'confirmed' AND category IS NOT NULL"
        " AND category = suggested_category", (since,),
    ).fetchone()["n"]
    data["unclear_count"] = sum(r["n"] for r in data["issue_counts"] if r["kind"] == "unclear_request")
    data["proactive_by_type"] = conn.execute(
        "SELECT check_name, COUNT(*) AS n FROM proactive_log"
        " WHERE sent_message=1 AND ts>=? GROUP BY check_name ORDER BY n DESC", (since,),
    ).fetchall()
    data["proactive_sent"] = sum(r["n"] for r in data["proactive_by_type"])
    data["mem_confirmed"] = len(store.boss_items(conn, "confirmed", limit=200))
    data["mem_inferred"] = len(store.boss_items(conn, "inferred", limit=200))
    data["mem_candidates"] = len(data["pending_candidates"])
    data["task_status_counts"] = {
        row["status"]: row["n"] for row in conn.execute(
            "SELECT status, COUNT(*) AS n FROM assistant_tasks"
            " WHERE created_at>=? GROUP BY status", (since,))
    }
    data["task_total"] = sum(data["task_status_counts"].values())
    data["task_completed_period"] = conn.execute(
        "SELECT COUNT(*) AS n FROM assistant_tasks"
        " WHERE status='completed' AND completed_at>=?", (since,)
    ).fetchone()["n"]
    data["task_avg_completion_seconds"] = conn.execute(
        "SELECT AVG((julianday(completed_at)-julianday(created_at))*86400.0) AS n"
        " FROM assistant_tasks WHERE completed_at>=? AND status='completed'",
        (since,),
    ).fetchone()["n"]
    data["task_attempts"] = conn.execute(
        "SELECT COUNT(*) AS n FROM task_step_attempts WHERE started_at>=?",
        (since,),
    ).fetchone()["n"]
    data["task_retries"] = conn.execute(
        "SELECT COUNT(*) AS n FROM task_step_attempts"
        " WHERE started_at>=? AND attempt_no>1", (since,),
    ).fetchone()["n"]
    data["task_receipts"] = conn.execute(
        "SELECT COUNT(*) AS n FROM tool_receipts WHERE created_at>=?", (since,)
    ).fetchone()["n"]
    data["task_delivered"] = conn.execute(
        "SELECT COUNT(*) AS n FROM assistant_tasks"
        " WHERE delivered_at>=? AND delivery_status='delivered'", (since,)
    ).fetchone()["n"]
    feedback = conn.execute(
        "SELECT COUNT(*) AS n, AVG(rating) AS avg_rating FROM task_feedback"
        " WHERE created_at>=?", (since,)
    ).fetchone()
    data["task_feedback_count"] = feedback["n"]
    data["task_avg_rating"] = feedback["avg_rating"]
    data["task_note_uses"] = conn.execute(
        "SELECT COUNT(*) AS n FROM task_note_uses WHERE created_at>=?", (since,)
    ).fetchone()["n"]
    data.update(collect_note_outcomes(conn, since, now))
    return data


# The CLOSED vocabulary of the outcome ledger: `note_events` filters on it, so a
# label missing here lands in a durable, purge-surviving table that nothing ever
# reads. ('note_edited' — he corrected a saved note's source message and
# confirmed the update — is observable here; it is not a USE, so it stays out of
# REAL_USE_OUTCOME_KINDS below.)
NOTE_EVENT_KINDS = ("note_opened", "note_cited", "note_resurfaced",
                    "note_resurface_accepted", "note_archived", "note_restored",
                    "note_kept", "note_review_deferred", "note_triaged",
                    "note_review_shown", "note_reminder_proposed",
                    "note_reminder_created", "note_edited",
                    "deleted_used", "deleted_unused")
REAL_USE_OUTCOME_KINDS = ("first_used", "note_opened", "note_cited",
                          "note_resurface_accepted")


def _parse_iso(value):
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def collect_note_outcomes(conn, since, now):
    """Saved-to-used OUTCOME metrics (plan v1.1 §16, MET-001) — deterministic,
    from real recorded events and lifecycle fields; never a pile-size pressure
    number, never something to maximize (more saves/nudges/entries is not the
    goal — notes that get USED is)."""
    out = {}
    now_iso = now.isoformat()
    out["notes_saved"] = conn.execute(
        "SELECT COUNT(*) AS n FROM note_outcomes"
        " WHERE event='captured' AND occurred_at>=?", (since,)
    ).fetchone()["n"]
    # distinct notes with a REAL use this period (open/citation/delivered
    # export/accepted resurfacing bump last_used_at; ranking never does)
    use_marks = ",".join("?" for _ in REAL_USE_OUTCOME_KINDS)
    out["notes_used_period"] = conn.execute(
        f"SELECT COUNT(*) AS n FROM (SELECT chat_id, note_no FROM note_outcomes"
        f" WHERE occurred_at>=? AND event IN ({use_marks})"
        f" GROUP BY chat_id, note_no)", (since, *REAL_USE_OUTCOME_KINDS)
    ).fetchone()["n"]
    marks = ",".join("?" for _ in NOTE_EVENT_KINDS)
    out["note_events"] = {r["kind"]: r["n"] for r in conn.execute(
        f"SELECT event AS kind, COUNT(*) AS n FROM note_outcomes WHERE occurred_at >= ?"
        f" AND event IN ({marks}) GROUP BY event", (since, *NOTE_EVENT_KINDS))}
    out["lifecycle_counts"] = store.notes_lifecycle_counts(conn)
    week_ahead = (now + timedelta(days=7)).isoformat()
    out["reviews_upcoming"] = conn.execute(
        "SELECT COUNT(*) AS n FROM messages WHERE knowledge_state = 'active'"
        " AND review_at > ? AND review_at <= ?", (now_iso, week_ahead)).fetchone()["n"]
    out["temp_expiring"] = conn.execute(
        "SELECT COUNT(*) AS n FROM messages WHERE knowledge_state IS NOT NULL"
        " AND knowledge_state != 'archived' AND note_purpose = 'temporary'"
        " AND expires_at IS NOT NULL AND expires_at <= ?", (week_ahead,)).fetchone()["n"]
    oldest = conn.execute(
        "SELECT MIN(received_at) AS t FROM messages WHERE knowledge_state = 'inbox'"
    ).fetchone()["t"]
    oldest_dt = _parse_iso(oldest)
    out["inbox_oldest_days"] = max(0, (now - oldest_dt).days) if oldest_dt else 0
    # KPI: capture_to_use_rate = distinct notes used after saving / distinct
    # notes confirmed (lifecycle notes, all-time — journals are outside).
    out["capture_confirmed_total"] = conn.execute(
        "SELECT COUNT(*) AS n FROM (SELECT chat_id, note_no FROM note_outcomes"
        " WHERE event='captured' GROUP BY chat_id, note_no)"
    ).fetchone()["n"]
    out["capture_used_total"] = conn.execute(
        "SELECT COUNT(*) AS n FROM (SELECT chat_id, note_no FROM note_outcomes"
        " WHERE event='first_used' GROUP BY chat_id, note_no)"
    ).fetchone()["n"]
    out["first_use_approx_count"] = conn.execute(
        "SELECT COUNT(*) AS n FROM note_outcomes"
        " WHERE event='first_used' AND source='migration_approx'"
    ).fetchone()["n"]
    out["archived_unused"] = conn.execute(
        "SELECT COUNT(*) AS n FROM messages WHERE knowledge_state = 'archived'"
        " AND use_count = 0").fetchone()["n"]
    # Exact all-time median from durable milestones; neither generic telemetry
    # pruning nor deleting the source note can erase this history.
    captured = {}
    first_use = {}
    for r in conn.execute(
            "SELECT chat_id, note_no, event, MIN(occurred_at) AS ts"
            " FROM note_outcomes WHERE event IN ('captured','first_used')"
            " GROUP BY chat_id, note_no, event"):
        key = (r["chat_id"], r["note_no"])
        (captured if r["event"] == "captured" else first_use)[key] = r["ts"]
    deltas = []
    for key, ts in first_use.items():
        used_at = _parse_iso(ts)
        saved_at = _parse_iso(captured.get(key))
        if used_at and saved_at and used_at >= saved_at:
            deltas.append((used_at - saved_at).total_seconds() / 3600)
    # A real median: `sorted(x)[n // 2]` is the UPPER-middle element for an even
    # sample ([1, 100] -> 100), which is not what "exact median" means.
    out["median_first_use_hours"] = statistics.median(deltas) if deltas else None
    # journal entries per journal this period (structured journals by entries,
    # remaining journal categories by dated messages)
    per_journal = []
    seen = set()
    for d in store.journal_defs(conn):
        n = store.journal_entries_count_for(conn, d["id"], since_iso=since)
        name = d["category"] or d["display_name"]
        seen.add(name.casefold())
        if n:
            per_journal.append((name, n))
    for name in store.journal_categories(conn):
        if name.casefold() in seen:
            continue
        n = store.journal_count(conn, name, since)   # exact, uncapped
        if n:
            per_journal.append((name, n))
    out["journal_entries_period"] = per_journal
    return out


def morning_brief(conn, cfg, lang, tz_offset, owner, chat_id=None):
    """A warm daily brief — today's reminders, overdue ones, open threads.
    Returns None when there's genuinely nothing worth a ping."""
    import relationship
    ru = lang == "ru"
    now = datetime.now(timezone.utc)
    now_naive = now.replace(tzinfo=None)
    today_local = (now + timedelta(hours=tz_offset)).date()
    overdue, fired_unacked, today = [], [], []
    for r in conn.execute(
            "SELECT title, due_utc, recurrence, last_fired_at FROM reminders"
            " WHERE status = 'active'"
            + (" AND chat_id = ?" if chat_id is not None else "")
            + " ORDER BY due_utc LIMIT 50",
            ((int(chat_id),) if chat_id is not None else ())):
        try:
            due = datetime.fromisoformat(r["due_utc"])
        except (ValueError, TypeError):
            continue
        if due.tzinfo is not None:
            due = due.astimezone(timezone.utc).replace(tzinfo=None)
        if r["recurrence"] == "none" and r["last_fired_at"]:
            fired_unacked.append(r["title"])
        elif due < now_naive:
            overdue.append(r["title"])
        elif (due + timedelta(hours=tz_offset)).date() == today_local:
            today.append(((due + timedelta(hours=tz_offset)).strftime("%H:%M"), r["title"]))
    # The morning brief must not become a second route for the disabled recurring
    # saved-note advertisement. Other real open loops still appear normally.
    note_review_on = str(store.pref_get(
        conn, "proactive_note_review") or "off").strip().casefold() in {
            "1", "true", "yes", "да", "on"}
    threads = relationship.ongoing_threads(
        conn, lang, chat_id=chat_id, include_suggested=note_review_on)
    task_rows = conn.execute(
        "SELECT id, objective, status, due_at, updated_at, delivery_status"
        " FROM assistant_tasks"
        " WHERE (status NOT IN ('completed','failed','cancelled')"
        " OR delivery_status IN ('ambiguous','failed'))"
        + (" AND chat_id = ?" if chat_id is not None else "")
        + " ORDER BY COALESCE(due_at, updated_at), id LIMIT 8",
        ((int(chat_id),) if chat_id is not None else ()),
    ).fetchall()
    task_open, task_overdue = [], []
    for task in task_rows:
        objective = str(task["objective"] or "")
        if ru and not re.search(r"[А-Яа-яЁё]", objective):
            objective = "Запрос босса"
        elif not ru and not re.search(r"[A-Za-z]", objective):
            objective = "Boss request"
        state = (
            "delivery needs explicit resend"
            if task["delivery_status"] in {"ambiguous", "failed"}
            else task["status"])
        label = f"#{task['id']} {objective[:120]} [{state}]"
        due = None
        try:
            due = datetime.fromisoformat(task["due_at"]) if task["due_at"] else None
            if due is not None and due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            due = None
        (task_overdue if due is not None and due <= now else task_open).append(label)
    if not (overdue or fired_unacked or today or threads or task_open or task_overdue):
        return None
    lines = [f"Доброе утро, {owner} ☀️" if ru else f"Good morning, {owner} ☀️"]
    if today:
        lines.append("Сегодня:" if ru else "Today:")
        lines += [f"  • {t} — {title}" for t, title in today]
    if overdue:
        lines.append(("⏰ Просрочено: " if ru else "⏰ Overdue: ") + ", ".join(overdue[:5]))
    if fired_unacked:
        lines.append(("✅ Ждёт подтверждения «готово»: " if ru
                      else "✅ Awaiting acknowledgement: ") + ", ".join(fired_unacked[:5]))
    if threads:
        lines.append(("🗂 Ещё открыто: " if ru else "🗂 Still open: ") + "; ".join(threads))
    if task_overdue:
        lines.append(
            ("⏰ Задачи с прошедшим сроком: " if ru else "⏰ Tasks past due: ")
            + "; ".join(task_overdue[:5]))
    if task_open:
        lines.append(
            ("🧭 Открытые задачи: " if ru else "🧭 Open task loops: ")
            + "; ".join(task_open[:5]))
    digest = journal_digest(conn, lang)
    if digest:
        lines.append(digest)
    lines.append("Чем помочь?" if ru else "Anything I can take off your plate?")
    return "\n".join(lines)


def corrections_report(conn, lang):
    """What Cara has corrected herself on, and what still needs a code fix."""
    ru = lang == "ru"
    learned = [
        r for status in ("inferred", "confirmed")
        for r in store.boss_items(conn, status, limit=50)
        if r["source_table"] == "correction"]
    unresolved = conn.execute(
        "SELECT detail, occurrences AS n FROM issue_patterns"
        " WHERE kind='correction_unresolved' AND status='open' AND occurrences>=2"
        " ORDER BY occurrences DESC LIMIT 10",
    ).fetchall()
    lines = ["📝 " + ("Корректировки по твоим замечаниям:" if ru
                      else "Corrections I've learned from you:")]
    if learned:
        lines.append("✅ " + ("применяю:" if ru else "applying now:"))
        for row in learned:
            provenance = "evidence" if row["evidence"] else "legacy-unverified"
            lines.append(f"  • [{provenance}] {row['value']}")
    else:
        lines.append("  — " + ("пока ничего" if ru else "nothing yet"))
    if unresolved:
        lines.append("🔧 " + ("повторяется — нужна правка кода (передала инженерам):" if ru
                              else "recurring — needs a code fix (flagged for engineering):"))
        lines.extend(f"  • {r['detail']} ×{r['n']}" for r in unresolved)
    return "\n".join(lines)


def _issue_label(kind, lang):
    entry = TEXTS.get(f"issue_kind_{kind}")
    return (entry.get(lang) or entry["en"]) if entry else kind


def chat_text(conn, cfg, lang, period="week"):
    data = collect(conn, period)
    ru = lang == "ru"
    period_label = {"day": ("за сегодня", "today"), "week": ("за неделю", "this week"),
                    "month": ("за месяц", "this month")}[data["period"]][0 if ru else 1]
    lines = [("Мой отчёт " if ru else "My review ") + period_label + ":"]
    # Outcomes first (MET-001): what the saved material actually DID — used,
    # turned into reminders, triaged — never a bare pile-size number.
    ev = data["note_events"]
    saved_line = (f"📥 Сохранено: {data['notes_saved']}"
                  f" · пригодилось (открыты/процитированы): {data['notes_used_period']}"
                  if ru else
                  f"📥 Saved: {data['notes_saved']}"
                  f" · actually used (opened/cited): {data['notes_used_period']}")
    if ev.get("note_reminder_created"):
        saved_line += (f" · в напоминания: {ev['note_reminder_created']}" if ru
                       else f" · turned into reminders: {ev['note_reminder_created']}")
    lines.append(saved_line)
    counts = data["lifecycle_counts"]
    triage = (f"🗂 В архив: {ev.get('note_archived', 0)}"
              f" · восстановлено: {ev.get('note_restored', 0)}"
              f" · ждут разбора: {counts.get('inbox', 0)}"
              f" · на пересмотр: {counts.get('review_due', 0)}"
              if ru else
              f"🗂 Archived: {ev.get('note_archived', 0)}"
              f" · restored: {ev.get('note_restored', 0)}"
              f" · awaiting triage: {counts.get('inbox', 0)}"
              f" · review due: {counts.get('review_due', 0)}")
    deleted = ev.get("deleted_used", 0) + ev.get("deleted_unused", 0)
    if deleted:
        triage += (f" · удалено: {deleted}"
                   f" (использовано {ev.get('deleted_used', 0)})" if ru else
                   f" · deleted: {deleted}"
                   f" (used {ev.get('deleted_used', 0)})")
    lines.append(triage)
    if data["reviews_upcoming"] or data["temp_expiring"]:
        lines.append((f"🔜 Пересмотр на неделе: {data['reviews_upcoming']}"
                      f" · временных истекает: {data['temp_expiring']}" if ru else
                      f"🔜 Reviews this week: {data['reviews_upcoming']}"
                      f" · temporary expiring: {data['temp_expiring']}"))
    rem = (f"⏰ Напоминаний поставлено: {data['reminders_set']}" if ru
           else f"⏰ Reminders set: {data['reminders_set']}")
    closures = data["reminder_closure_counts"]
    lifecycle = (("выполнено", "completed", closures.get("done", 0)),
                 ("отменено", "cancelled", closures.get("cancelled", 0)),
                 ("пропущено", "skipped", closures.get("skipped", 0)),
                 ("истекло", "expired", closures.get("expired", 0)),
                 ("отложено", "snoozed", data["reminder_event_counts"].get("snoozed", 0)))
    for ru_label, en_label, count in lifecycle:
        if count:
            rem += f" · {ru_label if ru else en_label}: {count}"
    lines.append(rem)
    lines.append((f"  Сейчас: просрочено {data['reminders_overdue']} · "
                  f"сработало, ждёт подтверждения {data['reminders_fired_unacked']}" if ru else
                  f"  Now: overdue {data['reminders_overdue']} · "
                  f"fired, awaiting acknowledgement {data['reminders_fired_unacked']}"))
    if data["ask_count"]:
        lines.append((f"❓ Ответила по базе: {data['ask_count']}" if ru
                      else f"❓ Answered from your KB: {data['ask_count']}"))
    learned = []
    if data["new_categories"]:
        learned.append(("новые категории: " if ru else "new categories: ")
                       + ", ".join(data["new_categories"][:8]))
    if data["corrections"]:
        pairs = "; ".join(f"«{r['suggested']}»→«{r['corrected']}»" for r in data["corrections"][:5])
        learned.append(("твои поправки: " if ru else "your corrections: ") + pairs)
    if data["habits"]:
        learned.append((f"авто-привычки: {len(data['habits'])}" if ru
                        else f"auto-habits: {len(data['habits'])}"))
    if data["new_prefs"]:
        learned.append((f"новое в памяти: {len(data['new_prefs'])}" if ru
                        else f"new memory entries: {len(data['new_prefs'])}"))
    lines.append("🧠 " + (("Чему я научилась: " if ru else "What I learned: ")
                          + ("; ".join(learned) if learned else ("пока ничему новому" if ru else "nothing new yet"))))
    digest = journal_digest(conn, lang, days=PERIOD_DAYS.get(data["period"], 7))
    if digest:
        lines.append(digest)
    dupes = similar_categories(conn)
    if dupes:
        pretty = "; ".join(f"«{a}» ↔ «{b}»" for a, b in dupes[:3])
        lines.append(("🗂 Похожие категории: " + pretty
                      + " — скажи «объедини X в Y», и я их сложу." if ru else
                      "🗂 Similar categories: " + pretty
                      + " — say \"merge X into Y\" and I'll fold them."))
    # Operational metrics live in the Cara-health tail (plan v1.1 §16) — the
    # outcome block above is about HIS knowledge, this part is about HER.
    lines.append("⚙️ " + ("Как я работала:" if ru else "Cara health:"))
    turns = sum(r["n"] for r in data["conversation_turns"])
    lines.append((f"  Реплик в диалоге: {turns}" if ru
                  else f"  Conversation turns: {turns}"))
    task_counts = data["task_status_counts"]
    avg_seconds = data["task_avg_completion_seconds"]
    avg_text = (
        f"{avg_seconds / 60:.1f}m" if avg_seconds is not None else "—")
    lines.append(
        (f"  🧭 Задачи: {data['task_total']} · выполнено "
         f"{task_counts.get('completed', 0)} · заблокировано "
         f"{task_counts.get('blocked', 0)} · отменено "
         f"{task_counts.get('cancelled', 0)} · среднее {avg_text}"
         f" · завершено за период {data['task_completed_period']}"
         if ru else
         f"  🧭 Tasks: {data['task_total']} · completed "
         f"{task_counts.get('completed', 0)} · blocked "
         f"{task_counts.get('blocked', 0)} · cancelled "
         f"{task_counts.get('cancelled', 0)} · avg {avg_text}"
         f" · completed this period {data['task_completed_period']}"))
    lines.append(
        (f"  🔧 Попытки: {data['task_attempts']} · повторы "
         f"{data['task_retries']} · квитанции {data['task_receipts']} · "
         f"доставлено {data['task_delivered']} · использовано заметок "
         f"{data['task_note_uses']}"
         if ru else
         f"  🔧 Attempts: {data['task_attempts']} · retries "
         f"{data['task_retries']} · receipts {data['task_receipts']} · "
         f"delivered {data['task_delivered']} · task-mediated note uses "
         f"{data['task_note_uses']}"))
    if data["issue_counts"]:
        issues = ", ".join(f"{_issue_label(r['kind'], lang)}: {r['n']}" for r in data["issue_counts"])
        lines.append(("  ⚠️ Проблемы: " if ru else "  ⚠️ Issues: ") + issues)
    else:
        lines.append("  ✅ " + ("Проблем не было" if ru else "No issues"))
    if data["open_issue_patterns"]:
        lines.append((f"  🧩 Открытых паттернов: {len(data['open_issue_patterns'])}" if ru
                      else f"  🧩 Open issue patterns: {len(data['open_issue_patterns'])}"))
    spend = sum(r["cost"] for r in data["spend_by_skill"])
    lines.append((f"  💸 Расходы AI: ${spend:.3f} · рабочих вызовов: "
                  f"{data['functional_calls']} · проверок моделей: "
                  f"{data['healthcheck_calls']} (${data['healthcheck_cost']:.3f})" if ru else
                  f"  💸 AI spend: ${spend:.3f} · work calls: {data['functional_calls']} · "
                  f"model-health probes: {data['healthcheck_calls']} "
                  f"(${data['healthcheck_cost']:.3f})"))
    latency = data["functional_latency"]
    if latency["calls"]:
        lines.append((f"  ⏱ Задержка рабочих AI-вызовов: "
                      f"p50 {latency['p50']:.2f}с · p95 {latency['p95']:.2f}с "
                      f"({latency['calls']})" if ru else
                      f"  ⏱ Work-call latency: p50 {latency['p50']:.2f}s · "
                      f"p95 {latency['p95']:.2f}s ({latency['calls']})"))
    if data["failover_served_count"]:
        lines.append((f"  🔁 Резервная модель успешно ответила: {data['failover_served_count']}" if ru
                      else f"  🔁 Backup model successfully served: {data['failover_served_count']}×"))
    if data["failover_failed_count"]:
        lines.append((f"  ⚠️ Цепочек моделей не справилось: {data['failover_failed_count']}" if ru
                      else f"  ⚠️ Model chains failed: {data['failover_failed_count']}×"))
    if data["fallback_legacy_trace_count"]:
        lines.append((f"  🔁 Старых переключений без подтверждённого исхода: "
                      f"{data['fallback_legacy_trace_count']}" if ru else
                      f"  🔁 Legacy failovers with unknown outcome: "
                      f"{data['fallback_legacy_trace_count']}"))
    if data["corrections_learned"] or data["corrections_unresolved"]:
        c = (f"  📝 Корректировки: применяю {len(data['corrections_learned'])}" if ru
             else f"  📝 Corrections: applying {len(data['corrections_learned'])}")
        if data["corrections_unresolved"]:
            c += (f", нужен код-фикс {len(data['corrections_unresolved'])}" if ru
                  else f", need a code fix {len(data['corrections_unresolved'])}")
        lines.append(c)
    lines.append((f"  📊 Память: {data['mem_confirmed']} подтверждено, "
                  f"{data['mem_inferred']} наблюдений; категорий с первого раза: "
                  f"{data['first_guess_kept']}/{data['confirmed_count']}"
                  if ru else
                  f"  📊 Memory: {data['mem_confirmed']} confirmed, {data['mem_inferred']} sensed; "
                  f"first-guess categories: "
                  f"{data['first_guess_kept']}/{data['confirmed_count']}"))
    if data["pending_candidates"]:
        n = len(data["pending_candidates"])
        lines.append((f"📋 Хочу уточнить ({n}) — скажи «обзор памяти»" if ru
                      else f"📋 I'd like to confirm {n} — say \"memory review\""))
    lines.append(("Скажи «сделай отчёт файлом» — пришлю .md для VS Code." if ru
                  else "Say \"export the review as md\" and I'll send a .md for VS Code."))
    return "\n".join(lines)


def _safe_fallback_message(message):
    """Compact both new structured traces and noisy legacy provider errors."""
    text = scrub_secrets(str(message or "").replace("\n", " "))
    text = re.sub(r"(\bHTTP\s+\d{3})\s*[:.]\s*.*$", r"\1", text,
                  flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:180]


def markdown(conn, cfg, period="week"):
    """English Markdown report for engineering use (VS Code)."""
    data = collect(conn, period)
    lines = [
        f"# Cara performance review — last {data['days']} day(s)",
        f"Generated: {data['now'][:16]}Z · period since {data['since'][:16]}Z · "
        f"model `{cfg.do_model}` · budgets ${cfg.budget_daily_usd}/day ${cfg.budget_monthly_usd}/month",
    ]
    total = sum(r["n"] for r in data["messages"])
    turns = sum(r["n"] for r in data["conversation_turns"])
    spend = sum(r["cost"] for r in data["spend_by_skill"])
    allowance = cfg.budget_daily_usd * data["days"]
    open_patterns = len(data["open_issue_patterns"])
    lines += [
        "",
        "## Executive summary",
        f"- **{total}** knowledge item(s) saved · **{turns}** conversation turn(s)",
        f"- **{open_patterns}** open actionable issue pattern(s) · "
        f"**{len(data['resolved_issue_patterns'])}** resolved this period",
        f"- AI spend **${spend:.4f}** of the ${allowance:.2f} rolling daily allowance "
        f"({(100 * spend / allowance) if allowance else 0:.1f}%) · "
        f"{data['functional_calls']} work call(s) + {data['healthcheck_calls']} health probe(s)",
        f"- model routing: {data['failover_served_count']} successfully served by backup · "
        f"{data['failover_failed_count']} failed chain(s) · "
        f"{data['fallback_legacy_trace_count']} legacy chain(s) with unknown outcome",
        "",
        "## Activity",
        f"- saved knowledge items: **{total}**",
    ]
    for row in data["messages"]:
        lines.append(f"  - {row['status']}: {row['n']}")
    lines.append(f"- conversation turns: **{turns}**")
    for row in data["conversation_turns"]:
        lines.append(f"  - {row['role']}: {row['n']}")
    if data["by_category"]:
        lines.append("- saved items by category:")
        for row in data["by_category"]:
            lines.append(f"  - {row['category']}: {row['n']}")
    lines.append(f"- facts extracted from saved items: {data['facts_extracted']}")
    lines.append(f"- reminders: {data['reminders_set']} created · {data['reminders_done']} completed · "
                 f"{data['reminders_fired_unacked']} fired/awaiting acknowledgement · "
                 f"{data['reminders_overdue']} overdue/unfired")
    for row in data["reminder_closures"]:
        if row["reason"] != "done":
            lines.append(f"  - closed as {row['reason']}: {row['n']}")
    if data["reminder_events"]:
        lines.append("- reminder lifecycle events:")
        for row in data["reminder_events"]:
            lines.append(f"  - {row['event']}: {row['n']}")
    task_counts = data["task_status_counts"]
    avg_seconds = data["task_avg_completion_seconds"]
    lines += [
        "",
        "## Assistant-task outcomes",
        f"- created: **{data['task_total']}** · completed: "
        f"**{task_counts.get('completed', 0)}** · blocked: "
        f"**{task_counts.get('blocked', 0)}** · cancelled: "
        f"**{task_counts.get('cancelled', 0)}** · failed: "
        f"**{task_counts.get('failed', 0)}**",
        f"- completed during this report period (all creation cohorts): "
        f"**{data['task_completed_period']}**",
        f"- average completed elapsed time: "
        f"{(avg_seconds / 60):.2f} minutes"
        if avg_seconds is not None else "- average completed elapsed time: n/a",
        f"- attempts: {data['task_attempts']} · retries: {data['task_retries']} · "
        f"immutable receipts: {data['task_receipts']}",
        f"- final results delivered: {data['task_delivered']} · "
        f"task-mediated note uses: {data['task_note_uses']}",
        f"- explicit feedback: {data['task_feedback_count']} · "
        + (f"average rating: {data['task_avg_rating']:.2f}/5"
           if data["task_avg_rating"] is not None else "average rating: n/a"),
    ]
    lines.append("")
    lines.append("## Learning")
    lines.append("- new categories: " + (", ".join(data["new_categories"]) or "none"))
    if data["corrections"]:
        lines.append("- categorization corrections (suggested → confirmed):")
        for row in data["corrections"]:
            lines.append(f"  - \"{row['suggested']}\" → \"{row['corrected']}\" ×{row['n']}")
    else:
        lines.append("- categorization corrections: none")
    if data["habits"]:
        lines.append("- active auto-confirm habits:")
        for row in data["habits"]:
            lines.append(f"  - source {row['key'].split(':', 1)[1]} → \"{row['value']}\"")
    if data["new_prefs"]:
        lines.append("- preferences added/updated this period:")
        for row in data["new_prefs"]:
            lines.append(f"  - {row['key']}: {row['value']}")
    lines.append("")
    lines.append("## Communication incidents observed this period")
    if data["issue_counts"]:
        for row in data["issue_counts"]:
            lines.append(f"- {row['kind']}: {row['n']}")
        lines.append("")
        lines.append("### Examples (most recent)")
        for row in data["issue_examples"]:
            detail = (row["detail"] or "").replace("\n", " ")
            state = row["status"] if "status" in row.keys() else "open"
            lines.append(f"- `{row['ts'][:16]}` **{row['kind']}** [{state}] — {detail}")
    else:
        lines.append("- none recorded")
    lines.append("")
    lines.append("## AI spend")
    for row in data["spend_by_skill"]:
        lines.append(f"- {row['skill']}: ${row['cost']:.4f} ({row['calls']} calls)")
    if not data["spend_by_skill"]:
        lines.append("- none")
    latency = data["functional_latency"]
    if latency["calls"]:
        lines.append(f"- functional chat/embed latency: p50 {latency['p50']:.2f}s · "
                     f"p95 {latency['p95']:.2f}s ({latency['calls']} calls)")
        for row in data["latency_by_skill"]:
            lines.append(f"  - {row['skill']}: p50 {row['p50']:.2f}s · "
                         f"p95 {row['p95']:.2f}s ({row['calls']})")
    health_latency = data["healthcheck_latency"]
    if health_latency["calls"]:
        lines.append(f"- model-health latency: p50 {health_latency['p50']:.2f}s · "
                     f"p95 {health_latency['p95']:.2f}s ({health_latency['calls']} probes)")
    lines.append("")
    lines.append("## Improvement backlog (open patterns)")
    if data["open_issue_patterns"]:
        for row in data["open_issue_patterns"]:
            lines.append(
                f"- **{row['kind']}** ×{row['n']} · last `{row['last_seen_at'][:16]}` — "
                f"{row['detail']}"
            )
    else:
        lines.append("- nothing currently open")
    if data["resolved_issue_patterns"]:
        lines.append("")
        lines.append("### Resolved this period")
        for row in data["resolved_issue_patterns"]:
            lines.append(f"- **{row['kind']}** — {row['detail']} ({row['resolution']})")
    lines.append("")
    lines.append("## What I learned about you")
    lines.append(f"- questions answered from your KB: {data['ask_count']}")
    if data["confirmed_about_you"]:
        for r in data["confirmed_about_you"]:
            provenance = "evidence" if r["evidence"] else "confirmed-legacy"
            suffix = f" — evidence: “{r['evidence']}”" if r["evidence"] else ""
            lines.append(f"  - [{r['kind']}; {provenance}] {r['value']}{suffix}")
    else:
        lines.append("  - nothing confirmed yet")
    lines.append("")
    lines.append("## Pending memory candidates (awaiting your confirmation)")
    if data["pending_candidates"]:
        for c in data["pending_candidates"]:
            count = c["recurrence_count"] or 1
            evidence = f" — evidence: “{c['evidence']}”" if c["evidence"] else ""
            lines.append(
                f"- #{c['id']} [{c['kind']}] {c['proposed_text']} ×{count}{evidence}"
            )
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Behavioral instructions")
    if data["corrections_learned"]:
        lines.append("- active rules:")
        for row in data["corrections_learned"]:
            provenance = "evidence" if row["evidence"] else "legacy-unverified"
            suffix = f" — evidence: “{row['evidence']}”" if row["evidence"] else ""
            lines.append(f"  - [{provenance}] {row['value']}{suffix}")
    else:
        lines.append("- active rules: none")
    if data["corrections_unresolved"]:
        lines.append("- **recurred at least twice → needs a code fix:**")
        lines.extend(
            f"  - {r['detail']} ×{r['n']} · {r['first_seen_at'][:10]}–{r['last_seen_at'][:10]}"
            for r in data["corrections_unresolved"]
        )
    lines.append("")
    lines.append("## Working history (recent grounded moments)")
    if data["rel_events"]:
        for e in data["rel_events"]:
            lines.append(f"- {e['title'] or e['kind']}: {e['summary']}")
    else:
        lines.append("- none recorded")
    lines.append("")
    lines.append("## Model failover")
    lines.append(f"- successfully served by backup: {data['failover_served_count']}")
    lines.append(f"- failed model chains: {data['failover_failed_count']}")
    lines.append(f"- legacy chains with unknown outcome: {data['fallback_legacy_trace_count']}")
    if data["fallbacks"]:
        lines.append(f"- {data['fallback_count']} low-level failed-attempt event(s):")
        for row in data["fallbacks"]:
            msg = _safe_fallback_message(row["message"])
            lines.append(f"  - `{row['ts'][:16]}` {row['skill'] or '-'} — {msg}")
    else:
        lines.append("- no low-level failed attempts")
    lines.append("")
    lines.append("## Trace summary")
    if data["trace_status"]:
        for row in data["trace_status"]:
            lines.append(f"- {row['kind']} · {row['status']}: {row['n']}")
    else:
        lines.append("- no traces this period")
    lines.append(f"- failed model attempts: {data['fallback_count']} · "
                 f"issues logged: {sum(r['n'] for r in data['issue_counts'])}")
    lines.append("")
    lines.append("## Notes outcomes (saved-to-used, MET-001)")
    ev = data["note_events"]
    used_n, conf_n = data["capture_used_total"], data["capture_confirmed_total"]
    rate = (100 * used_n / conf_n) if conf_n else 0.0
    lines.append(f"- **KPI capture_to_use_rate: {used_n}/{conf_n} ({rate:.0f}%)** — "
                 "distinct notes with a real use / distinct notes confirmed (all-time; "
                 "never optimize for more saves)")
    lines.append(f"- this period: saved {data['notes_saved']} · used {data['notes_used_period']} · "
                 f"reminders from notes {ev.get('note_reminder_created', 0)} "
                 f"(proposed {ev.get('note_reminder_proposed', 0)}) · "
                 f"archived {ev.get('note_archived', 0)} · restored {ev.get('note_restored', 0)} · "
                 f"edited after a message edit {ev.get('note_edited', 0)} · "
                 f"deleted used/unused {ev.get('deleted_used', 0)}/"
                 f"{ev.get('deleted_unused', 0)}")
    if data["median_first_use_hours"] is not None:
        provenance = (f"durable ledger; {data['first_use_approx_count']} legacy "
                      "first-use time(s) approximated from last use"
                      if data["first_use_approx_count"] else
                      "durable all-time ledger")
        lines.append(f"- median capture→first-use: {data['median_first_use_hours']:.1f} h "
                     f"({provenance})")
    counts = data["lifecycle_counts"]
    lines.append(f"- inbox: {counts.get('inbox', 0)} awaiting triage "
                 f"(oldest {data['inbox_oldest_days']} d) · review due: {counts.get('review_due', 0)} · "
                 f"upcoming 7d: {data['reviews_upcoming']} reviews, {data['temp_expiring']} temporary expiring")
    arch_total = counts.get("archived", 0)
    if arch_total:
        lines.append(f"- archived unused: {data['archived_unused']}/{arch_total} "
                     f"({100 * data['archived_unused'] / arch_total:.0f}%)")
    shown = ev.get("note_review_shown", 0)
    acted = sum(ev.get(k, 0) for k in ("note_archived", "note_restored", "note_kept",
                                       "note_review_deferred", "note_triaged"))
    if shown:
        lines.append(f"- review batches shown: {shown} · triage actions taken: {acted}")
    resurfaced = ev.get("note_resurfaced", 0)
    if resurfaced:
        lines.append(f"- resurfacing: {resurfaced} offered · "
                     f"{ev.get('note_resurface_accepted', 0)} accepted")
    if data["journal_entries_period"]:
        lines.append("- journal entries this period: "
                     + "; ".join(f"{name} — {n}" for name, n in data["journal_entries_period"]))
    lines.append("")
    lines.append("## Scorecard")
    kept = data["first_guess_kept"]
    lines.append(f"- categorizations: {data['confirmed_count']} confirmed "
                 f"({kept} kept as suggested, {data['confirmed_count'] - kept} corrected)")
    open_unclear = sum(
        1 for row in data["open_issue_patterns"] if row["kind"] == "unclear_request"
    )
    lines.append(f"- unclear-request incidents observed: {data['unclear_count']} · "
                 f"open patterns: {open_unclear}")
    lines.append(f"- proactive messages sent: {data['proactive_sent']}")
    for row in data["proactive_by_type"]:
        lines.append(f"  - {row['check_name']}: {row['n']}")
    lines.append(f"- memory: {data['mem_confirmed']} confirmed · {data['mem_inferred']} sensed · "
                 f"{data['mem_candidates']} awaiting confirmation")
    lines.append("")
    lines.append("## System health")
    try:
        import sysinfo
        snap = sysinfo.collect(str(cfg.db_path.parent))
        lines.append(f"- load {snap['load'][0]:.2f} · "
                     f"mem {sysinfo.fmt_bytes(snap['mem_used'])}/{sysinfo.fmt_bytes(snap['mem_total'])} · "
                     f"disk {sysinfo.fmt_bytes(snap['disk_used'])}/{sysinfo.fmt_bytes(snap['disk_total'])} · "
                     f"agent {sysinfo.fmt_bytes(snap['agent_rss'])}")
    except Exception as exc:  # never let a stats read break the report
        lines.append(f"- (unavailable: {exc})")
    return "\n".join(lines) + "\n"


# -- additional exports (spec §30.2) -----------------------------------------

EXPORT_KINDS = ("review", "self", "profile", "history", "candidates", "trace")


def export_document(conn, cfg, what, lang="en", period="week", full=False):
    """Build a Markdown export. Returns (filename, text). Boss-profile export
    is default-deny: only 'normal' values appear; private/sensitive are
    withheld (label only) unless full=True; secret is never exported."""
    what = str(what or "review").strip().lower()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    if what == "self":
        import self_model
        rows = store.self_facts(conn)
        body = ["# CARA_SELF", ""] + [f"- **{r['key']}**: {r['value']}" for r in rows]
        body += ["", "## Capabilities", self_model.answer_self_query(conn, "en", cfg)]
        return f"cara-self-{stamp}.md", "\n".join(body) + "\n"
    if what == "profile":
        body = ["# BOSS_PROFILE", "", "## Confirmed"]
        body += [ln for r in store.boss_items(conn, "confirmed", limit=100)
                 if (ln := _redacted_profile_line(r, full))]
        body += ["", "## Inferred (unconfirmed)"]
        body += [ln for r in store.boss_items(conn, "inferred",
                                              sensitivities=("normal", "private"), limit=100)
                 if (ln := _redacted_profile_line(r, full))]
        return f"boss-profile-{stamp}.md", "\n".join(body) + "\n"
    if what == "history":
        import relationship
        text = relationship.render_working_history(conn, "en", days=90)
        return f"working-history-{stamp}.md", "# WORKING_HISTORY\n\n" + text + "\n"
    if what == "candidates":
        body = ["# MEMORY_CANDIDATES", ""]
        for c in store.candidates_pending(conn, limit=100):
            evidence = f" · evidence: “{c['evidence']}”" if c["evidence"] else ""
            body.append(
                f"- #{c['id']} [{c['kind']}] {c['proposed_text']} ×{c['recurrence_count'] or 1}"
                f"  _(reason: {c['reason']})_{evidence}"
            )
        if len(body) == 2:
            body.append("- none pending")
        return f"memory-candidates-{stamp}.md", "\n".join(body) + "\n"
    if what == "trace":
        data = collect(conn, period)
        body = [f"# CARA_TRACE_SUMMARY — last {data['days']} day(s)", ""]
        body.append("## Units of work (traces)")
        body += [f"- {r['kind']} · {r['status']}: {r['n']}"
                 for r in data["trace_status"]] or ["- none"]
        body += ["", "## Model failover outcomes",
                 f"- successfully served by backup: {data['failover_served_count']}",
                 f"- failed model chains: {data['failover_failed_count']}",
                 f"- legacy chains with unknown outcome: {data['fallback_legacy_trace_count']}",
                 f"- low-level failed attempts: {data['fallback_count']}"]
        if data["fallbacks"]:
            for r in data["fallbacks"]:
                msg = _safe_fallback_message(r["message"])
                body.append(f"- `{r['ts'][:16]}` {r['skill'] or '-'} — {msg}")
        else:
            body.append("- none")
        body += ["", "## Issues by kind"]
        body += [f"- {r['kind']}: {r['n']}" for r in data["issue_counts"]] or ["- none"]
        return f"cara-trace-summary-{stamp}.md", "\n".join(body) + "\n"
    # default: the full weekly review
    return f"cara-review-{normalize_period(period)}-{stamp}.md", markdown(conn, cfg, period)


def _redacted_profile_line(row, full=False):
    s = row["sensitivity"]
    if s == "secret":
        return None  # never exported
    if s in ("sensitive", "private") and not full:
        return f"- #{row['id']} [{row['kind']}] _({s} — withheld; ask for a full export)_"
    return f"- #{row['id']} [{row['kind']}] {row['value']}"
