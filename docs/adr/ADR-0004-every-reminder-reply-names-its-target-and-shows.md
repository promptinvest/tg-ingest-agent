# ADR-0004: Every reminder reply names its target and shows human time; lists never go stale

- Status: Implemented 0a2639a
- Date: 2026-09-07
- Phase: Phase A — the daily loop
- Source: 2026-09-07 review of architecture, code and live behavior against build e3208b8 (report kept off-repo; findings are referenced by id)

## Context

done/snooze replies are anonymous while the binding can be a stale one-shot hours old; a reschedule renumbers the list silently and «Закрой #2» cancelled the wrong reminder; «завтра вечером» as a snooze re-arms at 09:00; the owner cancels and re-creates instead of editing (12 twin groups).

## Decision

reminder_done/skipped/snoozed/snooze_past include the title and display number; a fmt_relative helper renders «сегодня 18:00», «завтра 09:00», «ср 14:00»; reschedule, rename and create re-render the list exactly as cancel does; part-of-day words map to 09:00/13:00/19:00/22:00 defaults, otherwise she asks; reminder_create offers «уже есть #N на HH:MM — перенести?» when an active or fired-unacked title matches; a subject-less «напомни завтра в 10» after a stale one-shot older than 3 h opens a new partial instead of snoozing.

## Consequences

more text in confirmations; one extra list read per mutation.

## Resolves

dialog-aug2#3, reminders-time-proactive#3, #4, reminders#8

## Depends on

nothing

## Status log

- 2026-09-07: proposed by the review; not yet discussed with the owner.
- 2026-09-07: implemented in `0a2639a` (Phase A batch) — replies name title + display number; `reminders.fmt_relative`; create/reschedule/rename re-render the list; part-of-day defaults 09/13/19/22 — a bare «завтра» deliberately keeps the documented 09:00; twin offer with a «Перенести существующее» button; a subject-less create after a >3 h stale one-shot opens a partial.
