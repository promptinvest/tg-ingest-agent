# ADR-0003: «Overdue» means fired but unacknowledged; expiry is announced and reversible

- Status: Implemented 0a2639a
- Date: 2026-09-07
- Phase: Phase A — the daily loop
- Source: 2026-09-07 review of architecture, code and live behavior against build e3208b8 (report kept off-repo; findings are referenced by id)

## Context

proactive._overdue_reminders shares the fire sweep's predicate, so a fired one-shot can never count and the nudge has been dead since 2026-07-10; check_reminder_expiry only logs; 12 of 90 one-shots expired silently, including the most-snoozed ones.

## Decision

overdue = status active, recurrence none, last_fired_at set and older than 2 h, plus unfired-overdue past the defer valve; review.collect uses the same predicate; before expiry one re-ping at the next local 09:00 with the title and the three options; on expiry one line «Закрыла как просроченные: … — вернуть?» with a reopen path; keep REMINDER_FIRED_EXPIRE_DAYS; the notice is suppressible by preference.

## Consequences

CARA.md's «self-clears» wording is replaced; the urgent nudge becomes live again and bypasses the daily cap as designed; store.reminder_reopen is new.

## Resolves

reminders-time-proactive#2, #5, dialog-aug2#4, product-ux#1

## Depends on

nothing

## Status log

- 2026-09-07: proposed by the review; not yet discussed with the owner.
- 2026-09-07: implemented in `0a2639a` (Phase A batch) — `store.reminders_overdue` via `proactive.overdue_rows` (fired one-shot unacked > 2 h, or unfired past the defer valve) shared by heartbeat, review, brief and working history; one re-ping at the next local 09:00; expiry announced with a «Вернуть» button / typed «верни» (`store.reminder_reopen`, re-armed at the next local 09:00); `reminder_expiry_notice` pref.
