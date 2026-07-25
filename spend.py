#!/usr/bin/env python3
"""Spend skill: AI usage aggregation and reporting."""
import store

PERIODS = ("day", "week", "month")
PERIOD_LABELS = {
    "day": {"ru": "за сегодня", "en": "today"},
    "week": {"ru": "за неделю", "en": "this week"},
    "month": {"ru": "за месяц", "en": "this month"},
}


def normalize_period(value):
    value = str(value or "month").strip().lower()
    aliases = {"today": "day", "сегодня": "day", "день": "day",
               "неделя": "week", "weekly": "week",
               "месяц": "month", "monthly": "month"}
    value = aliases.get(value, value)
    return value if value in PERIODS else "month"


def default_priced_models(conn, cfg, model_names, period="month"):
    """Of `model_names`, those billed at the punitive DEFAULT_CHAT_PRICE because the
    slug is missing from the pricing table. Only CHAT rows can be default-priced —
    STT is charged per audio minute and embeddings have their own rate, so those
    model names are legitimately absent from that table and must NOT be flagged.

    Scoped to the SAME window the report is showing (`store.usage_period_filter`):
    a slug whose only rows this week are STT must not carry the flag because of a
    chat row from three months ago. The flag's whole value is being trustworthy."""
    import llm
    table = llm.pricing_table(cfg)
    names = {m for m in model_names if m and m not in table}
    if not names:
        return set()
    where, value = store.usage_period_filter(period)
    marks = ",".join("?" for _ in names)
    return {row[0] for row in conn.execute(
        f"SELECT DISTINCT model FROM llm_usage WHERE {where} AND kind = 'chat'"
        f" AND model IN ({marks})",
        (value,) + tuple(names))}


def format_spend(conn, period, cfg, lang):
    period = normalize_period(period)
    by_skill = store.usage_breakdown(conn, period, by="skill")
    by_model = store.usage_breakdown(conn, period, by="model")
    label = PERIOD_LABELS[period].get(lang) or PERIOD_LABELS[period]["en"]
    total = sum(row["cost"] for row in by_skill)
    calls = sum(row["calls"] for row in by_skill)
    if not by_skill:
        return ("Расходов на AI " + label + " нет.") if lang == "ru" else ("No AI spend " + label + ".")
    # An unpriced slug bills at $3/$15 and inflates this very report — say so here,
    # where the boss actually looks at the numbers (the 2026-06-19 budget lock).
    defaulted = default_priced_models(conn, cfg, [row["k"] for row in by_model], period)
    header = (f"Расходы на AI {label}: ${total:.3f} ({calls} вызовов)" if lang == "ru"
              else f"AI spend {label}: ${total:.3f} ({calls} calls)")
    lines = [header, "По агентам:" if lang == "ru" else "By skill:"]
    for row in by_skill:
        lines.append(f"  {row['k']}: ${row['cost']:.3f} ({row['calls']})")
    lines.append("По моделям:" if lang == "ru" else "By model:")
    for row in by_model:
        tokens = (row["tin"] or 0) + (row["tout"] or 0)
        flag = " (default-priced!)" if row["k"] in defaulted else ""
        lines.append(f"  {row['k']}: ${row['cost']:.3f}, {tokens} tok{flag}")
    day_total = store.usage_total(conn, "day")
    month_total = store.usage_total(conn, "month")
    import llm
    daily_cap, monthly_cap = llm.budget_limits(cfg, conn)  # honors a runtime override

    def cap(limit):
        # 0 means NO CAP (llm.budget_limits enforces only `limit > 0`). Rendering it
        # as "/0.00" reads as a blown budget — the exact misreading the cap-disable
        # wording exists to avoid, in the place the boss looks at the numbers.
        if limit > 0:
            return f"/{limit:.2f}"
        return " (без лимита)" if lang == "ru" else " (no limit)"

    budget_line = (
        f"Бюджет: день ${day_total:.2f}{cap(daily_cap)}, "
        f"месяц ${month_total:.2f}{cap(monthly_cap)}"
        if lang == "ru" else
        f"Budget: day ${day_total:.2f}{cap(daily_cap)}, "
        f"month ${month_total:.2f}{cap(monthly_cap)}"
    )
    lines.append(budget_line)
    return "\n".join(lines)
