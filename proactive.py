#!/usr/bin/env python3
"""Proactive private-aide heartbeat: gentle, SUGGESTION-ONLY nudges.

Cara may quietly point out things worth attention — memory candidates waiting,
saved items that still need a category, reminders gone overdue — but she never
acts on them herself. Safety rails:
  * Suggestion-only. A nudge never changes state; it asks the boss to act.
  * Manifest-gated. Only the `proactive_heartbeat` skill (read_only_suggestion,
    allowed_proactive=True) runs here; a destructive/external skill can't.
  * Throttled. At most cfg.proactive_max_per_day non-urgent nudges per day, and
    the same nudge is never repeated within a day.
  * Quiet hours. No non-urgent nudge during the boss's configured quiet window;
    urgent ones (overdue reminders) only bypass it if explicitly allowed.
  * Audited. Every evaluation is written to proactive_log (sent or suppressed).

Budget warnings stay in their own notifier (check_budget_notice); the weekly
digest auto-sends on schedule — neither is duplicated here.
"""
from datetime import datetime, timezone, timedelta

import journals
import skill_manifest
import store
from texts import T

CHECK_SKILL = "proactive_heartbeat"


def _tz_offset(conn, cfg):
    try:
        return int(store.pref_get(conn, "timezone_offset", cfg.timezone_offset))
    except (TypeError, ValueError):
        return cfg.timezone_offset


def local_day(conn, cfg, now):
    """The boss's LOCAL calendar day for a UTC instant — the one bucket the daily
    cap, the per-key dedup, quiet hours and off-days all share."""
    return (now + timedelta(hours=_tz_offset(conn, cfg))).strftime("%Y-%m-%d")


def settings(conn, cfg):
    """Effective proactivity settings: a preference the boss set (in plain
    language, via proactive_prefs) overrides the configured defaults."""
    def _int(key, default):
        try:
            return int(store.pref_get(conn, key) or default)
        except (TypeError, ValueError):
            return default
    enabled_pref = store.pref_get(conn, "proactive_enabled")
    enabled = (enabled_pref.lower() == "true") if enabled_pref else cfg.proactive_enabled
    return {
        "enabled": enabled,
        "quiet_start": _int("quiet_start", cfg.quiet_start),
        "quiet_end": _int("quiet_end", cfg.quiet_end),
        "max_per_day": _int("proactive_max_per_day", cfg.proactive_max_per_day),
        "days": (store.pref_get(conn, "proactive_days") or "all").lower(),  # all|weekdays|weekends
        # Deliberately opt-in. Saved notes stay available to manual review and
        # contextual retrieval; only the recurring three-note advertisement is off.
        "note_review": note_review_enabled(conn),
    }


def note_review_enabled(conn):
    value = str(store.pref_get(conn, "proactive_note_review") or "off").strip().casefold()
    return value in {"1", "true", "yes", "да", "on"}


def in_quiet_hours(cfg, conn, now, cfg_settings=None):
    """True if the boss's local time is inside the quiet window (handles a
    window that wraps midnight, e.g. 22:00–08:00)."""
    s = cfg_settings or settings(conn, cfg)
    start, end = s["quiet_start"], s["quiet_end"]
    if start == end:
        return False
    hour = (now + timedelta(hours=_tz_offset(conn, cfg))).hour
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end  # wraps midnight


def _wrong_day(conn, cfg, now, days):
    """True if today isn't an allowed day for non-urgent nudges."""
    if days not in ("weekdays", "weekends"):
        return False
    weekday = (now + timedelta(hours=_tz_offset(conn, cfg))).weekday()  # 0=Mon..6=Sun
    is_weekend = weekday >= 5
    return (days == "weekdays" and is_weekend) or (days == "weekends" and not is_weekend)


# -- checks: each returns (key, text, urgent) or None -------------------------

def _overdue_reminders(conn, cfg, lang, now, chat_id=None):
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM reminders WHERE status='active' AND due_utc<?"
        + (" AND chat_id=?" if chat_id is not None else "")
        + " AND (last_fired_at IS NULL OR last_fired_at<due_utc)",
        ((now.isoformat(), int(chat_id)) if chat_id is not None
         else (now.isoformat(),)),
    ).fetchone()["n"]
    return ("overdue", T(lang, "nudge_overdue", n=n), True) if n else None


def _memory_candidates(conn, cfg, lang, now):
    n = len(store.candidates_pending(conn, limit=50))
    return ("candidates", T(lang, "nudge_candidates", n=n), False) if n else None


def _notes_review_due(conn, cfg, lang, now):
    """The review INVITATION (plan v1.1 §9.3): replaces the old generic
    "unsorted pile" nudge — untriaged items still surface here, as one of the
    deterministic review reasons, framed as a decision worth a minute."""
    if not note_review_enabled(conn):
        return None
    batch = store.notes_review_candidates(conn, now=now, limit=3)
    return (("note_review", T(lang, "nudge_note_review", n=len(batch)), False)
            if batch else None)


def _journal_prompts(conn, cfg, lang, now):
    """Opt-in journal invitation (plan v1.1 §D-06, JRN-006): only for journals
    the boss explicitly enabled prompts on; only past the configured local
    hour; only when today has no entry yet. Suggestion-only, non-urgent — so
    quiet hours, off-days and the daily cap in run() all apply on top."""
    offset = _tz_offset(conn, cfg)
    local = now + timedelta(hours=offset)
    for d in store.journal_defs(conn, active_only=True):
        if not d["proactive_enabled"]:
            continue
        hour = journals.validate_prompt_config(d["prompt_config_json"]).get("hour", 21)
        if local.hour < hour:
            continue
        day_start_utc = (datetime(local.year, local.month, local.day,
                                  tzinfo=timezone.utc)
                         - timedelta(hours=offset)).isoformat()
        today = store.journal_entries_for(conn, d["id"], since_iso=day_start_utc)
        if today:
            continue
        category = d["category"] or d["display_name"]
        return (f"journal:{d['slug']}", T(lang, "nudge_journal", category=category),
                False)
    return None


# urgent first, then by usefulness
def _task_open_loops(conn, cfg, lang, now, chat_id=None):
    stale_cutoff = (now - timedelta(hours=24)).isoformat()
    attention_cutoff = (now - timedelta(hours=2)).isoformat()
    row = conn.execute(
        "SELECT id, objective, status, due_at, delivery_status"
        " FROM assistant_tasks"
        " WHERE (status IN ('planned','running','waiting_approval','blocked')"
        " OR delivery_status IN ('ambiguous','failed'))"
        + (" AND chat_id = ?" if chat_id is not None else "")
        + " AND (delivery_status IN ('ambiguous','failed')"
        " OR (status IN ('planned','running') AND updated_at <= ?)"
        " OR (status IN ('waiting_approval','blocked') AND updated_at <= ?)"
        " OR (due_at IS NOT NULL AND due_at <= ?))"
        " ORDER BY CASE WHEN due_at IS NOT NULL AND due_at <= ? THEN 0 ELSE 1 END,"
        " updated_at, id LIMIT 1",
        ((int(chat_id),) if chat_id is not None else ())
        + (stale_cutoff, attention_cutoff, now.isoformat(), now.isoformat()),
    ).fetchone()
    if row is None:
        return None
    if row["delivery_status"] in {"ambiguous", "failed"}:
        text = (
            f"У задачи #{row['id']} не доставлен подтверждённый итог. Команда "
            "task resume повторно отправит только результат, не запуская шаги."
            if lang == "ru" else
            f"Task #{row['id']} has no confirmed result delivery. Use task resume "
            "to resend only the outcome; no steps will run again."
        )
        key = (
            f"proactive_candidate:task_open_loop:{int(chat_id)}"
            if chat_id is not None else "proactive_candidate:task_open_loop")
        store.kv_set(conn, key, str(row["id"]))
        return "task_open_loop", text, False
    due = bool(row["due_at"] and row["due_at"] <= now.isoformat())
    objective = str(row["objective"] or "")
    if lang == "ru" and not any("Ѐ" <= c <= "ӿ" for c in objective):
        objective = "Запрос босса"
    elif lang != "ru" and not any(
            ("A" <= c <= "Z") or ("a" <= c <= "z") for c in objective):
        objective = "Boss request"
    if lang == "ru":
        text = (
            f"Задача #{row['id']} вышла за указанный срок: {objective}"
            if due else
            f"Открытая задача #{row['id']} ждёт внимания [{row['status']}]: "
            f"{objective}"
        )
    else:
        text = (
            f"Task #{row['id']} is past its explicit due time: {objective}"
            if due else
            f"Open task #{row['id']} needs attention [{row['status']}]: "
            f"{objective}"
        )
    key = (
        f"proactive_candidate:task_open_loop:{int(chat_id)}"
        if chat_id is not None else "proactive_candidate:task_open_loop")
    store.kv_set(conn, key, str(row["id"]))
    return "task_open_loop", text, False


CHECKS = (
    _overdue_reminders, _task_open_loops, _memory_candidates,
    _notes_review_due, _journal_prompts)
# The "≤ max_per_day non-urgent" cap counts only the NON-URGENT heartbeat nudges.
# Urgent ones (overdue) bypass the cap, so they must not consume it either.
NONURGENT_KEYS = ("candidates", "note_review", "task_open_loop")


def _nonurgent_keys(conn):
    """Static non-urgent keys plus the per-journal prompt keys (dynamic), so an
    enabled journal prompt counts against the same daily cap."""
    return NONURGENT_KEYS + tuple(
        f"journal:{d['slug']}" for d in store.journal_defs(conn))


def run(conn, cfg, lang, reply_fn, now=None, chat_id=None):
    """Evaluate the checks and send at most ONE nudge. Returns the sent check
    key, or None. Every outcome is logged to proactive_log."""
    skill_manifest.assert_proactive_allowed(CHECK_SKILL)  # gate (raises if misconfigured)
    s = settings(conn, cfg)
    if not s["enabled"]:
        return None
    now = now or datetime.now(timezone.utc)
    # ONE calendar for everything this function throttles by. Quiet hours and
    # off-days have always read the boss's LOCAL time, but the daily cap and the
    # "same nudge at most once a day" dedup bucketed by the UTC day — so with the
    # MSK default (+3) his allowance rolled over at 03:00 local: nudges spent
    # during the evening freed up in the middle of the night, and a nudge sent at
    # 01:00 could repeat at 03:01. The bucket is now the local day too.
    day = local_day(conn, cfg, now)
    quiet = in_quiet_hours(cfg, conn, now, s)
    wrong_day = _wrong_day(conn, cfg, now, s["days"])

    # Consider the checks in order and send the first ELIGIBLE one — don't commit to the
    # first hit and then bail. A persistent overdue reminder (always the first hit, and
    # already sent today) used to short-circuit here and starve every other nudge type
    # for the whole day; now an ineligible hit is skipped so a waiting memory candidate
    # / uncategorized item still gets its turn.
    any_hit = False
    for check in CHECKS:
        hit = (
            check(conn, cfg, lang, now, chat_id)
            if check in {_overdue_reminders, _task_open_loops}
            else check(conn, cfg, lang, now))
        if not hit:
            continue
        any_hit = True
        key, text, urgent = hit
        if (quiet or wrong_day) and (not urgent or not cfg.proactive_urgent_bypass_quiet):
            store.proactive_log_add(conn, key, "suppressed",
                                    reason="quiet hours" if quiet else "off-day", day=day)
            continue
        if store.proactive_key_sent_today(conn, day, key):
            store.proactive_log_add(conn, key, "suppressed", reason="already sent today", day=day)
            continue
        if not urgent and store.proactive_sent_count(conn, day, _nonurgent_keys(conn)) >= s["max_per_day"]:
            store.proactive_log_add(conn, key, "suppressed", reason="daily cap", day=day)
            continue
        # Log "sent" only on a SUCCESSFUL delivery: reply_fn (agent.reply) swallows a
        # TelegramError and returns None, and proactive_key_sent_today would otherwise
        # block the retry for the rest of the day on a transient send failure.
        if reply_fn(text):
            store.proactive_log_add(conn, key, "sent", sent=True, day=day)
            return key
        store.proactive_log_add(conn, key, "send failed", reason="delivery error", day=day)
        return None
    if not any_hit:
        store.proactive_log_add(conn, "none", "nothing due", day=day)
    return None
