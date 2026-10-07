"""Owner-requested monthly open-issue reminder; no model or automatic fixes."""
import calendar
import re
from datetime import datetime, timedelta, timezone

import store
import tasking
from texts import TEXTS

FIRST_MONTH = "2026-10"
MAX_MESSAGE_CHARS = 3800
# These observations record a completed repair or a safety rule working as
# intended. They remain in the journal, but are not unresolved defects.
COMPLETED_HANDLING = frozenset({
    "correction", "converse_action_repaired", "curation_skipped_after_fabrication",
})
OWNER_EXAMPLES = frozenset({
    "boss_reported", "correction_unresolved", "unclear_request",
    "router_invalid_output", "out_of_scope",
})
LABELS = {
    "boss_reported": ("Проблемы, о которых ты сообщил", "Problems you reported"),
    "router_invalid_output": ("Запросы, которые я не смогла разобрать",
                              "Requests I could not understand"),
    "llm_error": ("Сбои при подготовке ответа", "Failures while preparing a reply"),
    "sched_send_failed": ("Сообщения, которые не удалось отправить",
                          "Messages I could not deliver"),
    "backup_failed": ("Неудачные резервные копии", "Failed backups"),
    "db_stalled": ("Задержки при сохранении данных", "Delays while saving data"),
    "dead_letter": ("Запросы, которые не удалось завершить",
                    "Requests I could not complete"),
    "other": ("Другие проблемы для проверки", "Other problems to review"),
}


def _utc(value):
    try:
        value = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _previous_month(now, offset):
    local = now + timedelta(hours=offset)
    year, month = (local.year, local.month - 1) if local.month > 1 else (local.year - 1, 12)
    return local.replace(year=year, month=month,
                         day=min(local.day, calendar.monthrange(year, month)[1])) - timedelta(hours=offset)


def _label(kind, ru):
    if kind in LABELS:
        return LABELS[kind][0 if ru else 1]
    entry = TEXTS.get("issue_kind_" + kind)
    return entry["ru" if ru else "en"] if entry else LABELS["other"][0 if ru else 1]


def _example(kind, detail, ru):
    detail = tasking.redact_derived_text(detail or "")
    if kind not in OWNER_EXAMPLES:
        # Diagnostic bodies are never chat examples. Only a bounded status
        # number can supplement the translated category.
        match = re.search(r"\bHTTP\s+([45]\d{2})\b", detail, re.I)
        return (("Код ошибки: " if ru else "Error code: ") + match[1] + ".") if match else ""
    if re.search(r"<[/!a-z]|\bTraceback\b|\b(?:router|inference)\s*:|^\s*[\[{]", detail, re.I):
        return ""
    detail = re.sub(r"https?://\S+", "[ссылка]" if ru else "[link]", detail)
    detail = " ".join(detail.split())
    if len(detail) > 160:
        detail = detail[:157].rsplit(" ", 1)[0] + "…" if " " in detail[:157] else ""
    return (("Пример: «" if ru else "Example: “") + detail + ("»." if ru else "”.")) if detail else ""


def text(conn, lang, since=None, *, now=None, tz_offset=0):
    now = _utc(now) or datetime.now(timezone.utc)
    start = _utc(since) or _previous_month(now, tz_offset)
    if start > now:
        start = _previous_month(now, tz_offset)
    ru = lang == "ru"
    rows = conn.execute("SELECT kind,detail,last_seen_at FROM issue_patterns"
                        " WHERE status='open' ORDER BY kind,last_seen_at DESC").fetchall()
    rows = [row for row in rows if row["kind"] not in COMPLETED_HANDLING]
    if not rows:
        return ""
    groups = ({}, {})
    for row in rows:
        observed = _utc(row["last_seen_at"])
        bucket = 0 if observed and start <= observed <= now else 1
        kind = row["kind"]
        if kind not in LABELS and "issue_kind_" + kind not in TEXTS:
            kind = "other"
        groups[bucket].setdefault(kind, []).append(row)
    resolved = sum(
        1 for row in conn.execute("SELECT kind,resolved_at FROM issue_patterns WHERE status='resolved'")
        if row["kind"] not in COMPLETED_HANDLING
        and (at := _utc(row["resolved_at"])) and start <= at <= now)
    dates = [(date + timedelta(hours=tz_offset)).strftime("%d.%m.%Y") for date in (start, now)]
    period = ("С прошлого отчёта" if ru else "Since the previous digest") if since else (
        "За последний месяц" if ru else "Over the last month")
    lines = ["Мой отчёт о проблемах:" if ru else "My issue check-in:",
             f"{period}: {dates[0]}–{dates[1]}."]
    footer = []
    if resolved:
        footer.append((f"За этот период отмечено закрытыми проблем: {resolved}." if ru
                       else f"Issue entries marked resolved during this period: {resolved}."))
    footer.append("Скажи, с какой проблемы начать." if ru else "Tell me which problem to start with.")
    more = "Ещё открытых записей: {n}. Полный список — «покажи проблемы»." if ru else (
        'Other open entries: {n}. Ask "show problems" for the full list.')
    reserve = len("\n".join(footer + [more.format(n=len(rows))])) + 2
    omitted = 0
    for bucket, grouped in enumerate(groups):
        heading = (("Новые или повторившиеся за этот период:" if ru else "New or repeated during this period:")
                   if bucket == 0 else ("Ранее записанные, ещё не отмеченные закрытыми:" if ru
                                        else "Earlier entries still marked open:"))
        heading_added = False
        for kind, entries in grouped.items():
            count = len(entries)  # Open entries, not lifetime occurrence counts.
            block = [f"• {_label(kind, ru)} — " + (
                f"открытых записей: {count}." if ru else f"open entries: {count}.")]
            observed = [_utc(row["last_seen_at"]) for row in entries]
            observed = [at for at in observed if at and at <= now]
            if observed:
                stamp = (max(observed) + timedelta(hours=tz_offset)).strftime("%d.%m.%Y")
                block.append(("  Последний случай: " if ru else "  Last observed: ") + stamp + ".")
            example = next((value for row in entries
                            if (value := _example(kind, row["detail"], ru))), "")
            if example:
                block.append("  " + example)
            addition = ([heading] if not heading_added else []) + block
            if len("\n".join(lines + addition)) + reserve > MAX_MESSAGE_CHARS:
                omitted += count
                continue
            lines.extend(addition)
            heading_added = True
    if omitted:
        lines.append(more.format(n=omitted))
    return "\n".join(lines + footer)


class IssueDigestMixin:
    def check_monthly_issue_digest(self):
        now = datetime.now(timezone.utc)
        local = now + timedelta(hours=self.tz_offset())
        month = local.strftime("%Y-%m")
        if month < FIRST_MONTH or local.day < 7 or local.hour < 10:
            return
        if store.kv_get(self.conn, "issue_digest_month") == month:
            return
        if self._sched_backing_off("issue_digest", now):
            return
        body = text(self.conn, self.lang(), store.kv_get(self.conn, "issue_digest_delivered_at"),
                    now=now, tz_offset=self.tz_offset())
        if not body or self._send_all(body):
            store.kv_set(self.conn, "issue_digest_month", month)
            if body:
                store.kv_set(self.conn, "issue_digest_delivered_at", now.isoformat())
            store.kv_set(self.conn, "issue_digest_fails", "0")
        elif self._sched_send_gave_up("issue_digest"):
            store.kv_set(self.conn, "issue_digest_month", month)
