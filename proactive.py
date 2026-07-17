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
    }


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

def _overdue_reminders(conn, cfg, lang, now):
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM reminders WHERE status='active' AND due_utc<?"
        " AND (last_fired_at IS NULL OR last_fired_at<due_utc)",
        (now.isoformat(),),
    ).fetchone()["n"]
    return ("overdue", T(lang, "nudge_overdue", n=n), True) if n else None


def _memory_candidates(conn, cfg, lang, now):
    n = len(store.candidates_pending(conn, limit=50))
    return ("candidates", T(lang, "nudge_candidates", n=n), False) if n else None


def _notes_review_due(conn, cfg, lang, now):
    """The review INVITATION (plan v1.1 §9.3): replaces the old generic
    "unsorted pile" nudge — untriaged items still surface here, as one of the
    deterministic review reasons, framed as a decision worth a minute."""
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
CHECKS = (_overdue_reminders, _memory_candidates, _notes_review_due, _journal_prompts)
# The "≤ max_per_day non-urgent" cap counts only the NON-URGENT heartbeat nudges.
# Urgent ones (overdue) bypass the cap, so they must not consume it either.
NONURGENT_KEYS = ("candidates", "note_review")


def _nonurgent_keys(conn):
    """Static non-urgent keys plus the per-journal prompt keys (dynamic), so an
    enabled journal prompt counts against the same daily cap."""
    return NONURGENT_KEYS + tuple(
        f"journal:{d['slug']}" for d in store.journal_defs(conn))


def run(conn, cfg, lang, reply_fn, now=None):
    """Evaluate the checks and send at most ONE nudge. Returns the sent check
    key, or None. Every outcome is logged to proactive_log."""
    skill_manifest.assert_proactive_allowed(CHECK_SKILL)  # gate (raises if misconfigured)
    s = settings(conn, cfg)
    if not s["enabled"]:
        return None
    now = now or datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")
    quiet = in_quiet_hours(cfg, conn, now, s)
    wrong_day = _wrong_day(conn, cfg, now, s["days"])

    # Consider the checks in order and send the first ELIGIBLE one — don't commit to the
    # first hit and then bail. A persistent overdue reminder (always the first hit, and
    # already sent today) used to short-circuit here and starve every other nudge type
    # for the whole day; now an ineligible hit is skipped so a waiting memory candidate
    # / uncategorized item still gets its turn.
    any_hit = False
    for check in CHECKS:
        hit = check(conn, cfg, lang, now)
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
