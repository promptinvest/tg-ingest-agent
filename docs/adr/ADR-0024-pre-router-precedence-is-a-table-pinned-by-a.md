# ADR-0024: Pre-router precedence is a table pinned by a matrix test; every immediate action has an inverse

- Status: Proposed
- Date: 2026-09-07
- Phase: Phase E — maintainability and documentation
- Source: 2026-09-07 review of architecture, code and live behavior against build e3208b8 (report kept off-repo; findings are referenced by id)

## Context

dispatch is a hand-ordered chain of about fourteen guards justified by incidents; a mis-read reminder_cancel executes immediately by display position and cannot be undone; the numbered-delete shortcut is off whenever any card is pending.

## Decision

commit 1 writes a matrix test asserting today's winner for each pending kind and sample message; commit 2 introduces PRE_ROUTER = [(name, method, allowed_pending_kinds)] iterated in dispatch with the guards moved verbatim; store.reminder_reopen plus a kv last_action with the inverse operation written by cancel, reschedule, rename, recategorize and note_lifecycle, and a deterministic «отмени / верни как было / не то» shortcut valid for 10 min; the ordinal-close and the question-aware ack of ADR-0006 become rows; skill_manifest gains a per-action param contract applied by validate_route with an examples-conformance test.

## Consequences

the most incident-laden function becomes reviewable; ADR-0020 step 1 and ADR-0011 hang off the table.

## Resolves

router-dispatch#4, #6, #12, architecture#6 in part

## Depends on

nothing

## Status log

- 2026-09-07: proposed by the review; not yet discussed with the owner.
