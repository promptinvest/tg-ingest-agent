# ADR-0002: Simultaneous fires are delivered as one numbered card with batch close

- Status: Proposed
- Date: 2026-09-07
- Phase: Phase A — the daily loop
- Source: 2026-09-07 review of architecture, code and live behavior against build e3208b8 (report kept off-repo; findings are referenced by id)

## Context

fire_due_reminders sends one message per row and each later fire overwrites the reminder_fired pending, so a bare «готово» closes only the last alarm; morning clusters of 3–4 alarms were cancelled wholesale; cancel/close has no all/ids while reschedule does.

## Decision

when a sweep fires two or more reminders, send one card «⏰ Сейчас: 1) … 2) …» with per-item buttons and «Все готово»; the pending payload holds reminder_ids; «готово» closes the set, «готово 2»/«закрой второе» a member; add all/ids to reminder_cancel and the fired-close path; recurring rows still advance individually.

## Consequences

one message instead of N; the reminder_fired payload schema changes and the reply-to binding maps a message id to an id set.

## Resolves

reminders-time-proactive#1, product-ux#7, reminders#3

## Depends on

ADR-0001

## Status log

- 2026-09-07: proposed by the review; not yet discussed with the owner.
