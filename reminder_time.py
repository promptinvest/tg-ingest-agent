"""Source-based reminder time checks; model dates never supply missing evidence."""
import re
from datetime import datetime, timedelta, timezone

WEEKDAYS = {
    "понедельник": 0, "monday": 0, "вторник": 1, "tuesday": 1,
    "среда": 2, "среду": 2, "wednesday": 2, "четверг": 3, "thursday": 3,
    "пятница": 4, "пятницу": 4, "friday": 4, "суббота": 5,
    "субботу": 5, "saturday": 5, "воскресенье": 6, "sunday": 6,
}
_DAY = re.compile(r"\b(" + "|".join(WEEKDAYS) + r")\b", re.I)
_CLOCK = re.compile(r"(?<![\d.])([01]?\d|2[0-3])[:.]([0-5]\d)(?!\d)")
_HOUR = re.compile(r"\b(?:в|at)\s+([01]?\d|2[0-3])(?:\s*(am|pm|утра|вечера|дня))?\b", re.I)
_NUM = {"один": 1, "одного": 1, "одну": 1, "one": 1,
        "два": 2, "две": 2, "двух": 2, "two": 2,
        "три": 3, "three": 3, "четыре": 4, "four": 4,
        "пять": 5, "five": 5, "неделю": 7, "a": 1}
_BEFORE = re.compile(
    r"\b(?:за\s+(\d{1,2}|один|одного|одну|два|две|двух|три|четыре|пять)"
    r"\s+(день|дня|дней|час|часа|часов)\s+до\b|"
    r"(\d{1,2}|one|two|three|four|five|a)\s+(days?|hours?)\s+before\b)", re.I)
_MONTHS = {"января": 1, "февраля": 2, "марта": 3, "апреля": 4,
           "мая": 5, "июня": 6, "июля": 7, "августа": 8,
           "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12}
_DATE = re.compile(r"\b([0-3]?\d)\s+(" + "|".join(_MONTHS) + r")(?:\s+(20\d\d))?\b", re.I)
_EVIDENCE = re.compile(
    r"\b(?:сегодня|завтра|послезавтра|утром|вечером|дн[её]м|ночью|полдень|"
    r"сейчас|немедленно|через|ежедневно|кажд\w*|today|tomorrow|tonight|"
    r"morning|evening|noon|now|immediately|daily|weekly|in\s+\d+)\b", re.I)

_RU_MULTI_NUMBER = (r"(?:[2-9]|[1-9]\d+|два|две|три|четыре|пять|шесть|семь|восемь|девять|"
                    r"десять|одиннадцать|двенадцать|тринадцать|четырнадцать|пятнадцать|"
                    r"шестнадцать|семнадцать|восемнадцать|девятнадцать|двадцать|тридцать|"
                    r"сорок|пятьдесят|шестьдесят|полтора|полторы)")
_RU_NUMBER = rf"(?:\d+|один|одну|одно|{_RU_MULTI_NUMBER})(?:\s+{_RU_MULTI_NUMBER})?"
_UNSUPPORTED_REPEAT = re.compile(
    r"\b(?:hourly|minutely|monthly|yearly|annually|ежечасно|ежемесячно|ежегодно)\b|"
    r"\b(?:every|each)\s+(?:(?:\d+|other|an?|one|two|three|four|five)\s+)?"
    r"(?:minutes?|hours?|months?|years?)\b|"
    rf"\bкажд\w*\s+(?:(?:{_RU_NUMBER}|втор\w*|друг\w*)\s+)?"
    r"(?:минут\w*|час\w*|месяц\w*|год\w*)\b|"
    rf"\b(?:every|each|кажд\w*)\s+(?:{_RU_MULTI_NUMBER}|other|two|three|втор\w*)\s+"
    r"(?:days?|weeks?|дн\w*|недел\w*)\b|"
    rf"\b(?:{_RU_NUMBER}\s+)?раз(?:а)?\s+в\s+(?:{_RU_NUMBER}\s+)?"
    r"(?:минут\w*|час\w*|месяц\w*|год\w*)\b|"
    rf"\b{_RU_MULTI_NUMBER}\s+раз(?:а)?\s+в\s+(?:день|сутки|недел\w*)\b|"
    rf"\bраз\s+в\s+{_RU_MULTI_NUMBER}\s+(?:дн\w*|недел\w*)\b|"
    r"\b(?:daily|weekly|ежедневно|еженедельно|every\s+(?:day|week)|"
    r"кажд\w*\s+(?:день|недел\w*)).{0,80}\b(?:until|through|до)\s+"
    r"(?:\d|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"понедельник|вторник|сред|четверг|пятниц|суббот|воскресень)", re.I)
_UNSUPPORTED_SCHEDULE_FIELDS = {
    "until", "until_utc", "end_utc", "end_date", "ends_at", "count", "repeat_count",
    "interval", "interval_minutes", "repeat_interval", "repeat_every", "frequency", "rrule",
}


def unsupported_schedule(params, text=None, *, reschedule=False):
    """Reject schedule semantics the reminder executor cannot carry out in full.

    The draft normalizer's historical fallback remains available to its other
    callers. State-changing reminder entry points apply this gate before it.
    """
    recurrence = str(params.get("recurrence") or "none").strip().casefold()
    allowed = {"none"} if reschedule else {"none", "daily", "weekly"}
    source = re.sub(r'«[^»]*»|“[^”]*”|"[^"]*"', "", str(text or ""))
    return (recurrence not in allowed
            or any(params.get(key) not in (None, "") for key in _UNSUPPORTED_SCHEDULE_FIELDS)
            or bool(_UNSUPPORTED_REPEAT.search(source)))


def clock(text, default=None):
    match = _CLOCK.search(text)
    if match:
        return int(match[1]), int(match[2])
    match = _HOUR.search(text)
    if match:
        hour = int(match[1])
        if (match[2] or "").lower() in ("pm", "вечера", "дня") and hour < 12:
            hour += 12
        elif (match[2] or "").lower() in ("am", "утра") and hour == 12:
            hour = 0
        return hour, 0
    return default


def weekday_due(text, offset_hours, now=None, default_hour=9):
    """One named weekday, next future occurrence; retain the established clock default."""
    matches = list(_DAY.finditer(str(text)))
    if len(matches) != 1:
        return None
    now = now or datetime.now(timezone.utc)
    offset = timedelta(hours=float(offset_hours))
    local = now + offset
    hour, minute = clock(text, (default_hour, 0))
    due = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    due += timedelta(days=(WEEKDAYS[matches[0][1].lower()] - local.weekday()) % 7)
    if due <= local:
        due += timedelta(days=7)
    return (due - offset).isoformat()


def checked_params(text, params, offset_hours, now=None):
    """Correct explicit event offsets; otherwise ask when the request contains no time.

    Ambiguous offsets deliberately clear the model's guessed date. Unrelated
    temporal expressions and existing day-part defaults retain their normal path.
    """
    out = dict(params)
    text = str(text or "")
    now = now or datetime.now(timezone.utc)
    offsets = list(_BEFORE.finditer(text))
    if not offsets and re.search(r"\bза\b.{0,70}\bдо\b|\b(?:days?|weeks?|hours?)\s+before\b", text, re.I):
        out.pop("due_utc", None)
        return out
    if offsets:
        out.pop("due_utc", None)
        if len(offsets) != 1:
            return out
        match = offsets[0]
        value, unit = (match[1], match[2]) if match[1] else (match[3], match[4])
        count = int(value) if value.isdigit() else _NUM[value.lower()]
        event_clock = clock(text[:match.start()])
        if event_clock is None:
            return out
        dates = list(_DATE.finditer(text[:match.start()]))
        if dates:
            if len(dates) != 1:
                return out
            date = dates[0]
            local_now = now + timedelta(hours=float(offset_hours))
            try:
                local = datetime(int(date[3] or local_now.year), _MONTHS[date[2].lower()],
                                 int(date[1]), *event_clock, tzinfo=timezone.utc)
                if not date[3] and local <= local_now:
                    local = local.replace(year=local.year + 1)
                event = local - timedelta(hours=float(offset_hours))
            except ValueError:
                return out
        else:
            raw = weekday_due(text[:match.start()], offset_hours, now)
            if raw is None:
                return out
            event = datetime.fromisoformat(raw)
        due = event - (timedelta(hours=count) if unit.lower().startswith(("час", "hour"))
                       else timedelta(days=count))
        reminder_clock = clock(text[match.end():])
        if reminder_clock:
            offset = timedelta(hours=float(offset_hours))
            local = due + offset
            due = local.replace(hour=reminder_clock[0], minute=reminder_clock[1]) - offset
        if due > now:
            out["due_utc"] = due.isoformat()
        return out
    has_time = bool(clock(text) or _DAY.search(text) or _DATE.search(text)
                    or _EVIDENCE.search(text)
                    or re.search(r"\b\d{1,2}[/-]\d{1,2}\b", text))
    if not has_time:
        out.pop("due_utc", None)
    return out
