# ADR-0024: Pre-router precedence is a table pinned by a matrix test; every immediate action has an inverse

- Status: Implemented/verified 2b6da6ee, 2026-11-02; rollout failed before installation, release held
- Date: 2026-09-07
- Phase: Phase E — maintainability and documentation
- Source: 2026-09-07 review of architecture, code and live behavior against build e3208b8 (report kept off-repo; findings are referenced by id)

## Context

dispatch is a hand-ordered chain of about fourteen guards justified by incidents; a mis-read reminder_cancel executes immediately by display position and cannot be undone; the numbered-delete shortcut is off whenever any card is pending.

## Decision

commit 1 writes a matrix test asserting today's winner for each pending kind and sample message; commit 2 introduces PRE_ROUTER = [(name, method, allowed_pending_kinds)] iterated in dispatch with the guards moved verbatim; store.reminder_reopen plus a kv last_action with the inverse operation written by cancel, reschedule, rename, recategorize and note_lifecycle, and a deterministic «отмени / верни как было / не то» shortcut valid for 10 min; the ordinal-close and the question-aware ack of ADR-0006 become rows; skill_manifest gains a per-action param contract applied by validate_route with an examples-conformance test.

The owner's 2 November selection preserves the existing meanings of «отмени»,
«верни как было» and «не то». Generic undo instead uses the unambiguous
«отмени последнее действие» / “undo last action” command with a free pending slot
and no competing task approval or quoted/replied-card binding. Existing targeted
reminder and learning undo remain intact. The latest supported mutation has a
durable per-chat ten-minute inverse with stable target identities and an expected
state comparison. Batch inverse is all-or-none, with original history retained.
Restoration refuses changed targets or overdue rearming. Category inverse restores
exact journal membership, payload and date; it does not erase correction history,
issued note numbers or newly created categories. A recurring deferral's unchanged
new echo is cancelled while its series anchor and history remain.

This implementation extends the existing dispatcher, manifest and skill/storage
modules; it creates no new process, schema, model call or router prompt. The
parameter contract retains optional defaults, existing coercions, handler-safe
fallbacks and ignored extension keys while rejecting unusable supplied shapes.

## Consequences

the most incident-laden function becomes reviewable; ADR-0020 step 1 and ADR-0011 hang off the table.

## Resolves

router-dispatch#4, #6, #12, architecture#6 in part

## Depends on

nothing

## Status log

- 2026-11-02: exact source `2b6da6ee` passed 208 focused checks and 3,450
  checks in each normal/shuffled Linux run, with nine skips retained, plus static,
  installed-layout and CI gates. [PR 15](https://github.com/promptinvest/tg-ingest-agent/pull/15)
  is merged. The fresh rollout failed before installation because only the existing
  `kv.learning_last_scan` value advanced between online and stopped
  backups; the frozen stopped backup and live database matched across all 56
  tables. No guard was waived. Accepted `6e947694` services were resumed and
  independently verified. A new reviewed attempt needs an explicit decision on
  [the narrow guard proposal](../cara-adr24-release-guard-proposal-2026-11-02.md).
  The failed attempt remains consumed; [release evidence](../cara-adr24-release-2026-11-02.md).

- 2026-11-02: owner requested “implement next Cara ADR”; the current reconciliation
  selects ADR-0024. Owner selected preserving existing phrases and adding
  «отмени последнее действие» for generic undo. The original dispatch matrix is
  characterized before extraction. Release verification remains separate; no
  consumed healthcheck exception or prior controller is reused.

- 2026-09-07: proposed by the review; not yet discussed with the owner.
- 2026-10-19: owner requested fixing three confirmed reminder defects. Implemented
  delivered-nudge reply bindings, exact-card draft callbacks and reminder schedule
  validation, with focused regression cases. This does not implement or approve
  the general precedence table, other action contracts or new inverse operations.
  See [release evidence](../cara-reminder-safety-2026-10-19.md).
- 2026-10-19: the focused fixes are deployed and accepted as `bc67c89`, build
  `c605cb100b37`, receipt `3d54e432e890e1b38f779a41`; the full 1,763-test gate
  and live source/data/config/recovery/runtime checks pass. General table and
  inverse-operation work remain proposed.
- 2026-10-19: owner requested all four follow-ups from the additional review.
  Explicit draft/partial text replies now check prompt identity; delivered-alarm
  replies precede reminder pending guards without losing unrelated drafts. Source
  repeat detection covers Russian number words and «раз в месяц». General table
  and inverse-operation work remain proposed. See the
  [follow-up evidence](../cara-reply-completeness-2026-10-19.md).
- 2026-10-19: follow-up deployed and accepted as `6a430ad`, build `4cc84f818306`,
  receipt `f69a035e89d8b23ac681a9ff`; 56 focused and 1,776 full Linux cases pass,
  with original source/data/config/recovery/runtime gates preserved.
