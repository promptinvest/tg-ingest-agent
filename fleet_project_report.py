"""Presentation of validated project runtime records; no collection or prose authority."""
from datetime import datetime, timezone

import fleet_monitor_adapters as monitors
import fleet_protocol as protocol

ANSWER_FIELD_ORDER = (
    "generation", "reserves", "day_budget", "target", "budget_guard",
    "application_health", "unfinished_work", "accepted_release", "project_metrics",
)
ANSWER_FIELDS = frozenset(ANSWER_FIELD_ORDER)
_DEFAULT = ("generation", "reserves", "day_budget")


def validate_answer_fields(value):
    if (not isinstance(value, list) or not 1 <= len(value) <= len(ANSWER_FIELDS)
            or any(not isinstance(item, str) or item not in ANSWER_FIELDS for item in value)
            or len(set(value)) != len(value)):
        raise ValueError("invalid project answer fields")
    return list(value)


def _date(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _dates(dates, ru):
    """Merge equal dates only; never replace source measurement time with read time."""
    grouped = {}
    for label, value in dates:
        if value is not None:
            grouped.setdefault(_date(value), []).append(label)
    if not grouped:
        return ""
    if len(grouped) == 1:
        return ("Данные: " if ru else "Evidence: ") + next(iter(grouped)).strftime("%Y-%m-%d %H:%M:%S UTC")
    days = {value.date() for value in grouped}
    if len(days) == 1:
        return ("Данные за " if ru else "Evidence dated ") + next(iter(grouped)).strftime("%Y-%m-%d UTC: ") + "; ".join(
            "/".join(labels) + " " + value.strftime("%H:%M:%S") for value, labels in grouped.items())
    return ("Даты UTC: " if ru else "Evidence dates UTC: ") + "; ".join(
        "/".join(labels) + " " + value.strftime("%Y-%m-%d %H:%M:%S") for value, labels in grouped.items())


def _cells(value):
    return ", ".join(cell + "=" + str(count) for cell, count in value.items())


def render(observation, fields=None, lang="en", detail="concise"):
    """Return a field-scoped summary, or None for other capability implementations.

    Original observations and
    exhaustive findings remain unchanged for retained evidence and full reports.
    """
    selected = validate_answer_fields(fields) if fields is not None else list(_DEFAULT)
    if observation.get("capability") != "runtime":
        return None
    if protocol.adapter_kind("runtime", observation.get("implementation_version")) != "daily_lingo_observer":
        if fields is None:
            return None
        ru = lang == "ru"
        labels = dict(zip(ANSWER_FIELD_ORDER,
            ("генерация", "резервы", "дневной лимит", "проверенная цель", "бюджетный монитор",
             "состояние приложения", "незавершённая работа", "принятый релиз", "метрики проекта") if ru else
            ("generation", "reserves", "daily limit", "verified target", "budget guard",
             "application health", "unfinished work", "accepted release", "project metrics")))
        reason = "выбранный источник не предоставляет это поле" if ru else "not exposed by selected runtime source"
        return "; ".join(labels[field] + ": " + reason for field in selected)
    state = observation["value"]
    monitors.validate_observation("daily_lingo_observer", state)
    ru = lang == "ru"
    if state["status"] != "recorded":
        labels = dict(zip(ANSWER_FIELD_ORDER,
            ("генерация", "резервы", "дневной лимит", "проверенная цель", "бюджетный монитор",
             "состояние приложения", "незавершённая работа", "принятый релиз", "метрики проекта") if ru else
            ("generation", "reserves", "daily limit", "verified target", "budget guard",
             "application health", "unfinished work", "accepted release", "project metrics")))
        reason = {"not_configured": "не подключено" if ru else "not connected",
                  "unavailable": "наблюдение недоступно" if ru else "observation unavailable",
                  "not_found": "наблюдение источника не найдено" if ru else "source observation not found"}[state["status"]]
        return "; ".join(labels[field] + ": " + reason for field in selected)
    parts, dates = [], []
    requested = set(selected)
    source_stale_relevant = bool(requested & {"generation", "application_health", "unfinished_work"})
    generation, reserves, guard = state["generation"], state["reserves"], state["budget_guard"]
    for field in selected:
        if field == "generation":
            parts.append(("генерация " if ru else "generation ") +
                         (("включена" if generation["enabled"] else "приостановлена") if ru else
                          ("enabled" if generation["enabled"] else "paused")))
            dates.append(("генерация" if ru else "generation", generation["as_of"]))
            if generation["stop_recorded"]:
                parts.append("есть запись об остановке" if ru else "a stop is recorded")
        elif field == "reserves":
            parts.append(("резервы: " if ru else "reserves: ") + (_cells(reserves["cells"]) or ("неизвестны" if ru else "unknown")))
            dates.append(("резервы" if ru else "reserves", reserves["as_of"]))
            if "reserve_coverage" in state["gaps"]:
                parts.append("резервы: неполная выборка" if ru else "reserves: partial coverage")
        elif field == "day_budget":
            limit = guard["daily_limit_usd"] if guard is not None else None
            parts.append(("дневной лимит: " if ru else "daily limit: ") +
                         ("USD" + str(limit) + ("/сутки UTC" if ru else "/UTC day") if limit is not None else ("неизвестен" if ru else "unknown")))
            if guard is not None:
                dates.append(("лимит" if ru else "limit", guard["probe_at"]))
        elif field == "target":
            target = state["target"]
            parts.append(("проверенная цель: " if ru else "verified target: ") +
                         (_cells(target["cells"]) if target is not None else ("не подключена" if ru else "not connected")))
            if target is not None:
                dates.append(("цель" if ru else "target", target["verified_at"]))
        elif field == "budget_guard":
            if guard is None:
                parts.append("бюджетный монитор не подключен" if ru else "budget guard not connected")
            else:
                parts.append(("бюджетный монитор " if ru else "budget guard ") +
                             (("активен" if guard["active"] else "неактивен") if ru else ("active" if guard["active"] else "inactive")) +
                             ("; " + guard["outcome"] if guard["outcome"] is not None else ""))
                dates.append(("бюджетное наблюдение" if ru else "budget observation", guard["probe_at"]))
                if detail == "detailed":
                    dates.append(("сигнал монитора" if ru else "guard heartbeat", guard["heartbeat_at"]))
                if guard["terminal_outcome"] is not None:
                    parts.append(("итог монитора: " if ru else "guard terminal outcome: ") + guard["terminal_outcome"])
        elif field == "application_health":
            labels = (("app_health", "приложение" if ru else "application"), ("postgres_health", "PostgreSQL"))
            for key, label in labels:
                parts.append(label + ": " + state["application"].get(key, "неизвестно" if ru else "unknown"))
            dates.append(("состояние" if ru else "health", state["source_observed_at"]))
        elif field == "unfinished_work":
            for key, label in (("unfinished_calls", "незавершённые вызовы" if ru else "unfinished calls"),
                               ("unfinished_refills", "незавершённые пополнения" if ru else "unfinished refills")):
                parts.append(label + ": " + str(state["application"].get(key, "неизвестно" if ru else "unknown")))
            dates.append(("работа" if ru else "work", state["source_observed_at"]))
        elif field == "project_metrics":
            parts.append("это наблюдение не содержит метрик CPU/RAM/диска и запросов/трафика проекта" if ru else
                         "this observation contains no project CPU/RAM/disk measurements or request/traffic load metrics")
        elif field == "accepted_release":
            release = state["accepted_release"]
            parts.append(("принятый релиз: " if ru else "accepted release: ") +
                         (release["revision"][:12] if release is not None else ("не подключен" if ru else "not connected")))
            if release is not None:
                dates.append(("принятие релиза" if ru else "release accepted", release["accepted_at"]))
    # Only exceptions relevant to the requested business facts are automatic.
    if requested & {"generation", "reserves"} and "application_health" not in requested:
        for key, label in (("app_health", "приложение" if ru else "application"), ("postgres_health", "PostgreSQL")):
            if state["application"].get(key) in {"unhealthy", "starting"}:
                parts.append(label + ": " + state["application"][key])
                source_stale_relevant = True
                dates.append(("состояние" if ru else "health", state["source_observed_at"]))
    if requested & {"day_budget", "budget_guard"}:
        if guard is None:
            if "budget_guard" not in requested:
                parts.append("бюджетный монитор не подключен" if ru else "budget guard not connected")
        else:
            if not guard["active"] and "budget_guard" not in requested:
                parts.append("бюджетный монитор неактивен" if ru else "budget guard inactive")
            if state["gaps"].get("budget_guard") == "observation_unavailable":
                parts.append("бюджетное наблюдение недоступно" if ru else "budget observation unavailable")
            elif state["gaps"].get("budget_guard") == "stale":
                parts.append("бюджетные данные устарели" if ru else "budget evidence is stale")
    if source_stale_relevant and not state["fresh"]:
        parts.append("наблюдение устарело" if ru else "observation is stale")
    if "reserves" in requested and not reserves["fresh"] and (state["fresh"] or not source_stale_relevant):
        parts.append("данные резервов устарели" if ru else "reserve evidence is stale")
    if not dates:
        dates.append(("наблюдение" if ru else "observation", state["source_observed_at"]))
    parts.append(_dates(dates, ru))
    if detail == "detailed":
        parts.append(("прочитано " if ru else "read ") + _date(state["observed_at"]).strftime("%Y-%m-%d %H:%M:%S UTC"))
    return "; ".join(dict.fromkeys(part for part in parts if part))