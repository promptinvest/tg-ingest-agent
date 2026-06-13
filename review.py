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

import store
from texts import TEXTS

PERIOD_DAYS = {"day": 1, "week": 7, "month": 30}


def normalize_period(value):
    value = str(value or "week").strip().lower()
    aliases = {"today": "day", "сегодня": "day", "день": "day",
               "неделя": "week", "weekly": "week",
               "месяц": "month", "monthly": "month"}
    value = aliases.get(value, value)
    return value if value in PERIOD_DAYS else "week"


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
    data["spend_by_skill"] = conn.execute(
        "SELECT skill, COUNT(*) AS calls, COALESCE(SUM(cost_usd), 0) AS cost"
        " FROM llm_usage WHERE ts >= ? GROUP BY skill ORDER BY cost DESC", (since,),
    ).fetchall()
    data["ask_count"] = conn.execute(
        "SELECT COUNT(*) AS n FROM llm_usage WHERE skill='ask' AND kind='chat' AND ts >= ?",
        (since,),
    ).fetchone()["n"]
    data["confirmed_about_you"] = store.boss_items(conn, "confirmed")
    data["pending_candidates"] = store.candidates_pending(conn, limit=20)
    return data


def _issue_label(kind, lang):
    entry = TEXTS.get(f"issue_kind_{kind}")
    return (entry.get(lang) or entry["en"]) if entry else kind


def chat_text(conn, cfg, lang, period="week"):
    data = collect(conn, period)
    ru = lang == "ru"
    period_label = {"day": ("за сегодня", "today"), "week": ("за неделю", "this week"),
                    "month": ("за месяц", "this month")}[data["period"]][0 if ru else 1]
    lines = [("Мой отчёт " if ru else "My review ") + period_label + ":"]
    total = sum(r["n"] for r in data["messages"])
    by_status = ", ".join(f"{r['status']}: {r['n']}" for r in data["messages"]) or "—"
    lines.append((f"📥 Сообщений: {total} ({by_status})" if ru
                  else f"📥 Messages: {total} ({by_status})"))
    lines.append((f"⏰ Напоминаний поставлено: {data['reminders_set']}" if ru
                  else f"⏰ Reminders set: {data['reminders_set']}"))
    if data["ask_count"]:
        lines.append((f"❓ Ответила по базе: {data['ask_count']}" if ru
                      else f"❓ Answered from your KB: {data['ask_count']}"))
    learned = []
    if data["new_categories"]:
        learned.append(("новые категории: " if ru else "new categories: ")
                       + ", ".join(data["new_categories"][:8]))
    if data["corrections"]:
        pairs = "; ".join(f"«{r['suggested']}»→«{r['corrected']}»" for r in data["corrections"][:5])
        learned.append(("ваши поправки: " if ru else "your corrections: ") + pairs)
    if data["habits"]:
        learned.append((f"авто-привычки: {len(data['habits'])}" if ru
                        else f"auto-habits: {len(data['habits'])}"))
    if data["new_prefs"]:
        learned.append((f"новое в памяти: {len(data['new_prefs'])}" if ru
                        else f"new memory entries: {len(data['new_prefs'])}"))
    lines.append("🧠 " + (("Чему я научилась: " if ru else "What I learned: ")
                          + ("; ".join(learned) if learned else ("пока ничему новому" if ru else "nothing new yet"))))
    if data["issue_counts"]:
        issues = ", ".join(f"{_issue_label(r['kind'], lang)}: {r['n']}" for r in data["issue_counts"])
        lines.append(("⚠️ Проблемы: " if ru else "⚠️ Issues: ") + issues)
    else:
        lines.append("✅ " + ("Проблем не было" if ru else "No issues"))
    spend = sum(r["cost"] for r in data["spend_by_skill"])
    calls = sum(r["calls"] for r in data["spend_by_skill"])
    lines.append((f"💸 Расходы AI: ${spend:.3f} ({calls} вызовов)" if ru
                  else f"💸 AI spend: ${spend:.3f} ({calls} calls)"))
    if data["pending_candidates"]:
        n = len(data["pending_candidates"])
        lines.append((f"📋 Хочу уточнить ({n}) — скажите «обзор памяти»" if ru
                      else f"📋 I'd like to confirm {n} — say \"memory review\""))
    lines.append(("Скажите «сделай отчёт файлом» — пришлю .md для VS Code." if ru
                  else "Say \"export the review as md\" and I'll send a .md for VS Code."))
    return "\n".join(lines)


def markdown(conn, cfg, period="week"):
    """English Markdown report for engineering use (VS Code)."""
    data = collect(conn, period)
    lines = [
        f"# Cara performance review — last {data['days']} day(s)",
        f"Generated: {data['now'][:16]}Z · period since {data['since'][:16]}Z · "
        f"model `{cfg.do_model}` · budgets ${cfg.budget_daily_usd}/day ${cfg.budget_monthly_usd}/month",
        "",
        "## Activity",
    ]
    total = sum(r["n"] for r in data["messages"])
    lines.append(f"- messages ingested: **{total}**")
    for row in data["messages"]:
        lines.append(f"  - {row['status']}: {row['n']}")
    lines.append(f"- reminders set: {data['reminders_set']}")
    lines.append("")
    lines.append("## Learning")
    lines.append("- new categories: " + (", ".join(data["new_categories"]) or "none"))
    if data["corrections"]:
        lines.append("- operator corrections (suggested → confirmed):")
        for row in data["corrections"]:
            lines.append(f"  - \"{row['suggested']}\" → \"{row['corrected']}\" ×{row['n']}")
    else:
        lines.append("- operator corrections: none")
    if data["habits"]:
        lines.append("- active auto-confirm habits:")
        for row in data["habits"]:
            lines.append(f"  - source {row['key'].split(':', 1)[1]} → \"{row['value']}\"")
    if data["new_prefs"]:
        lines.append("- preferences added/updated this period:")
        for row in data["new_prefs"]:
            lines.append(f"  - {row['key']}: {row['value']}")
    lines.append("")
    lines.append("## Communication issues")
    if data["issue_counts"]:
        for row in data["issue_counts"]:
            lines.append(f"- {row['kind']}: {row['n']}")
        lines.append("")
        lines.append("### Examples (most recent)")
        for row in data["issue_examples"]:
            detail = (row["detail"] or "").replace("\n", " ")
            lines.append(f"- `{row['ts'][:16]}` **{row['kind']}** — {detail}")
    else:
        lines.append("- none recorded")
    lines.append("")
    lines.append("## AI spend")
    for row in data["spend_by_skill"]:
        lines.append(f"- {row['skill']}: ${row['cost']:.4f} ({row['calls']} calls)")
    if not data["spend_by_skill"]:
        lines.append("- none")
    lines.append("")
    lines.append("## Improvement backlog (for VS Code)")
    backlog = []
    for row in data["issue_counts"]:
        if row["kind"] == "out_of_scope" and row["n"]:
            backlog.append(f"- {row['n']} out-of-scope request(s) — candidates for new skills"
                           " (see examples above)")
        if row["kind"] == "unclear_request" and row["n"]:
            backlog.append(f"- {row['n']} unclear request(s) — consider new router examples"
                           " or actions")
        if row["kind"] == "stt_failed" and row["n"]:
            backlog.append(f"- {row['n']} failed transcription(s) — check whisper setup/model size")
        if row["kind"] == "ingest_failed" and row["n"]:
            backlog.append(f"- {row['n']} failed classification(s) — check model/prompt or retries")
    if data["corrections"]:
        backlog.append("- repeated category corrections above may deserve prompt few-shots"
                       " or seed categories")
    lines.extend(backlog or ["- nothing actionable this period 🎉"])
    lines.append("")
    lines.append("## What I learned about you")
    lines.append(f"- questions answered from your KB: {data['ask_count']}")
    if data["confirmed_about_you"]:
        for r in data["confirmed_about_you"]:
            lines.append(f"  - [{r['kind']}] {r['value']}")
    else:
        lines.append("  - nothing confirmed yet")
    lines.append("")
    lines.append("## Pending memory candidates (awaiting your confirmation)")
    if data["pending_candidates"]:
        for c in data["pending_candidates"]:
            lines.append(f"- #{c['id']} [{c['kind']}] {c['proposed_text']}")
    else:
        lines.append("- none")
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

EXPORT_KINDS = ("review", "self", "profile", "history", "candidates")


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
            body.append(f"- #{c['id']} [{c['kind']}] {c['proposed_text']}  _(reason: {c['reason']})_")
        if len(body) == 2:
            body.append("- none pending")
        return f"memory-candidates-{stamp}.md", "\n".join(body) + "\n"
    # default: the full weekly review
    return f"cara-review-{normalize_period(period)}-{stamp}.md", markdown(conn, cfg, period)


def _redacted_profile_line(row, full=False):
    s = row["sensitivity"]
    if s == "secret":
        return None  # never exported
    if s in ("sensitive", "private") and not full:
        return f"- #{row['id']} [{row['kind']}] _({s} — withheld; ask for a full export)_"
    return f"- #{row['id']} [{row['kind']}] {row['value']}"
