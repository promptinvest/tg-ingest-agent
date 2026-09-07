# ADR-0006: Bare acknowledgements never reach the LLM

- Status: Proposed
- Date: 2026-09-07
- Phase: Phase A — the daily loop
- Source: 2026-09-07 review of architecture, code and live behavior against build e3208b8 (report kept off-repo; findings are referenced by id)

## Context

«да», «давай», «ага» with nothing pending go to the router and then to converse, where a history-less repair can deny an action she just performed; confirm is the third most common router action.

## Decision

when a reminder draft or partial is pending, yes/no/«через N»/«в HH» are parsed deterministically before router.route; with nothing pending «да/давай/ага/угу/+» join the silent-ack set (optionally a 👍 reaction); an ack after a bot turn ending in «?» is routed instead of dropped; the proactive follow-up regex accepts ок/окей/хорошо/ладно.

## Consequences

fewer router calls; _honest_action_reply also gains the last four turns and the active-reminders block so a repair can say «это уже стоит — #2».

## Resolves

product-ux#3, #8, router-dispatch#5, dialog-aug1#1 residue

## Depends on

ADR-0001 for buttons

## Status log

- 2026-09-07: proposed by the review; not yet discussed with the owner.
