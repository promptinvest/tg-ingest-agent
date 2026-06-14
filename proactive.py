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

import skill_manifest
import store
from texts import T

CHECK_SKILL = "proactive_heartbeat"


def _tz_offset(conn, cfg):
    try:
        return int(store.pref_get(conn, "timezone_offset", cfg.timezone_offset))
    except (TypeError, ValueError):
        return cfg.timezone_offset


def in_quiet_hours(cfg, conn, now):
    """True if the boss's local time is inside the quiet window (handles a
    window that wraps midnight, e.g. 22:00–08:00)."""
    start, end = cfg.quiet_start, cfg.quiet_end
    if start == end:
        return False
    hour = (now + timedelta(hours=_tz_offset(conn, cfg))).hour
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end  # wraps midnight


# -- checks: each returns (key, text, urgent) or None -------------------------

def _overdue_reminders(conn, cfg, lang, now):
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM reminders WHERE status = 'active' AND due_utc < ?",
        (now.isoformat(),),
    ).fetchone()["n"]
    return ("overdue", T(lang, "nudge_overdue", n=n), True) if n else None


def _memory_candidates(conn, cfg, lang, now):
    n = len(store.candidates_pending(conn, limit=50))
    return ("candidates", T(lang, "nudge_candidates", n=n), False) if n else None


def _items_need_category(conn, cfg, lang, now):
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM messages WHERE status = 'suggested'"
    ).fetchone()["n"]
    return ("unsorted", T(lang, "nudge_unsorted", n=n), False) if n else None


# urgent first, then by usefulness
CHECKS = (_overdue_reminders, _memory_candidates, _items_need_category)


def run(conn, cfg, lang, reply_fn, now=None):
    """Evaluate the checks and send at most ONE nudge. Returns the sent check
    key, or None. Every outcome is logged to proactive_log."""
    skill_manifest.assert_proactive_allowed(CHECK_SKILL)  # gate (raises if misconfigured)
    if not cfg.proactive_enabled:
        return None
    now = now or datetime.now(timezone.utc)

    day = now.strftime("%Y-%m-%d")
    hit = None
    for check in CHECKS:
        hit = check(conn, cfg, lang, now)
        if hit:
            break
    if not hit:
        store.proactive_log_add(conn, "none", "nothing due", day=day)
        return None

    key, text, urgent = hit
    quiet = in_quiet_hours(cfg, conn, now)

    if quiet and (not urgent or not cfg.proactive_urgent_bypass_quiet):
        store.proactive_log_add(conn, key, "suppressed", reason="quiet hours", day=day)
        return None
    if store.proactive_key_sent_today(conn, day, key):
        store.proactive_log_add(conn, key, "suppressed", reason="already sent today", day=day)
        return None
    if not urgent and store.proactive_sent_count(conn, day) >= cfg.proactive_max_per_day:
        store.proactive_log_add(conn, key, "suppressed", reason="daily cap", day=day)
        return None

    reply_fn(text)
    store.proactive_log_add(conn, key, "sent", sent=True, day=day)
    return key
