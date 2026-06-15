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


def format_spend(conn, period, cfg, lang):
    period = normalize_period(period)
    by_skill = store.usage_breakdown(conn, period, by="skill")
    by_model = store.usage_breakdown(conn, period, by="model")
    label = PERIOD_LABELS[period].get(lang) or PERIOD_LABELS[period]["en"]
    total = sum(row["cost"] for row in by_skill)
    calls = sum(row["calls"] for row in by_skill)
    if not by_skill:
        return ("Расходов на AI " + label + " нет.") if lang == "ru" else ("No AI spend " + label + ".")
    header = (f"Расходы на AI {label}: ${total:.3f} ({calls} вызовов)" if lang == "ru"
              else f"AI spend {label}: ${total:.3f} ({calls} calls)")
    lines = [header, "По агентам:" if lang == "ru" else "By skill:"]
    for row in by_skill:
        lines.append(f"  {row['k']}: ${row['cost']:.3f} ({row['calls']})")
    lines.append("По моделям:" if lang == "ru" else "By model:")
    for row in by_model:
        tokens = (row["tin"] or 0) + (row["tout"] or 0)
        lines.append(f"  {row['k']}: ${row['cost']:.3f}, {tokens} tok")
    day_total = store.usage_total(conn, "day")
    month_total = store.usage_total(conn, "month")
    import llm
    daily_cap, monthly_cap = llm.budget_limits(cfg, conn)  # honors a runtime override
    budget_line = (
        f"Бюджет: день ${day_total:.2f}/{daily_cap:.2f}, "
        f"месяц ${month_total:.2f}/{monthly_cap:.2f}"
        if lang == "ru" else
        f"Budget: day ${day_total:.2f}/{daily_cap:.2f}, "
        f"month ${month_total:.2f}/{monthly_cap:.2f}"
    )
    lines.append(budget_line)
    return "\n".join(lines)
