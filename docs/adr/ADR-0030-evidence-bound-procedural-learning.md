# ADR-0030: Evidence-bound procedural learning inside Cara

- Status: Implemented and deployed 2026-10-13
- Date: 2026-10-13
- Source: owner requested self-improvement, reviewed Hermes Agent, then said "go".

## Decision

Add learning.py as a module within the existing owner-facing process. Reuse the
current curator, task receipts, SQLite store and poll-loop scheduler. Retain the
monthly digest, original Mentor source/results and disabled Mentor units.

Capture normal, supported owner corrections from exact non-forwarded owner quotes.
Learn input-free workflow recipes only from completed, successfully delivered
tasks with canonical source hashes, fully successful bound receipts and current
tool contracts. Exclude failed, partial, ambiguous, negatively rated or corrected
tasks. Query relevant lessons for conversation, router and planner; existing normal
owner guidance also reaches relevant router/planner requests without historical
profile backfill or edits. Guidance is advisory and cannot override hard rules,
current owner instructions, source bindings, permissions or fresh write approvals.

Store immutable versions, source/evidence hashes, structural validation outcomes,
and owner undo events. /learning lists the new lessons; /learning undo N vV
disables only that exact current version and never edits the original memory.
Disabled rules are not reactivated by automatic capture. Purge-all discloses and
removes derived learning; independent spend/preferences/history rules remain.

Learning performs no additional model calls. Its bounded networkless scan handles
at most five newly delivered tasks per minute. The startup marker excludes old
completed tasks; completed historical Mentor controllers are never replayed.
Existing successful curator calls still learn normal owner facts/corrections under
their unchanged budget gateway. Versioned guidance can change future decisions;
executors and compiled validators remain the permission boundary.

## Validation and limits

Provenance and tool-contract checks establish eligible evidence, not an empirical
quality improvement. The report states that practical improvement remains unmeasured.
Focused tests cover learning, retrieval, source invalidation, negative feedback,
exact-version undo/restart, forward/secret/policy exclusions and purge disclosure.
The full existing suite remains a release gate.

Automatically generated source-code fixes and general new executable skills are
a subsequent separately verified scope. This release does not reactivate the retired
patch-generation pipeline or add an external Hermes runtime dependency. Hermes
inspired procedural memory; no upstream code was copied.

## Relationship to ADR-0029

Supplement ADR-0029 with active procedural learning. Preserve the monthly issue
digest and its delivery receipts. Retained Mentor inference/runner units remain
stopped and disabled; retiring their weekly patch pipeline is still in force.

## Accepted release

Source `7de363c`, build `05f8e80d1687`, receipt `387cd0c8d238f0b087484eaa`.
Exact 1,743-case deployment gate passed with nine unchanged skips.
[Record](../cara-learning-2026-10-13.md).
