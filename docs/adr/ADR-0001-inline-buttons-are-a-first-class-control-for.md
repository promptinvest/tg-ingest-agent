# ADR-0001: Inline buttons are a first-class control for reminder state changes

- Status: Proposed
- Date: 2026-09-07
- Phase: Phase A — the daily loop
- Source: 2026-09-07 review of architecture, code and live behavior against build e3208b8 (report kept off-repo; findings are referenced by id)

## Context

the fire card offers only «через 30 минут» (used 0 of 22 times), every snooze is a typed phrase through two parsers, 31 of 35 drafts are confirmed by a typed «да» that costs a 13k-token router call; CARA.md already permits inline buttons for state changes.

## Decision

the fired card and the draft card carry inline keyboards (Готово · +1 ч · Завтра 09:00 · День… on fires; Ставлю · Другое время · Не надо on drafts) with callback_data bound to the reminder id or draft; callbacks are handled deterministically and edit the original message to show the outcome; text follow-ups stay supported; after the third snooze in a day the card escalates to «перенести на день или закрыть?».

## Consequences

no LLM on the most common interaction; the single pending slot is bypassed for button acks; templates gain reply_markup and a callback handler; texts must stay in her voice.

## Resolves

product-ux#1, #3, reminders-time-proactive P1, dialog-aug2#5

## Depends on

nothing

## Status log

- 2026-09-07: proposed by the review; not yet discussed with the owner.
