# ADR-0009: The weekly review is written for the owner; the ops dump moves to the export

- Status: Proposed
- Date: 2026-09-07
- Phase: Phase A — the daily loop
- Source: 2026-09-07 review of architecture, code and live behavior against build e3208b8 (report kept off-repo; findings are referenced by id)

## Context

9 sent, 2 answered; two task-runtime lines print zeros every week for a feature never used; the journal appears as a bare count.

## Decision

chat_text becomes ≤6 lines: reminder outcomes with the most-snoozed title, journal entries in his own words when ≤7, what she learned, one health line only when something failed, pending memory count, new improvement proposals if any; every block is gated on non-zero data; no send when the week had no owner turns; the markdown export keeps the full ops tail.

## Consequences

review.py chat_text/markdown split; the dedicated weekly notifier stays.

## Resolves

dialog-trends#12, reminders-time-proactive#6, product-ux#10, task-runtime-mentor#8 in part

## Depends on

nothing

## Status log

- 2026-09-07: proposed by the review; not yet discussed with the owner.
