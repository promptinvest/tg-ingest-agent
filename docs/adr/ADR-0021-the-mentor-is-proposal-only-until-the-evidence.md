# ADR-0021: The Mentor is proposal-only until the evidence and the model earn a candidate phase

- Status: Proposed
- Date: 2026-09-07
- Phase: Phase D — decisions the owner must make
- Source: 2026-09-07 review of architecture, code and live behavior against build e3208b8 (report kept off-repo; findings are referenced by id)

## Context

7 cycles, 0 usable output: 4 timeouts each consumed the weekly ledger because the slot is reserved before the HTTP call and the review phase has no reachable retry; 2 died in 0.0 s because notes_svc.py, texts.py and media.py exceed the 64 KiB cap while being in the allowlist; evidence omits issue detail and context; drafts are never surfaced and an accept attempt answers "not found"; the blocklist rejects any added re.compile(; a flash-class model is asked for a byte-exact diff over 140 KB.

## Decision

candidate construction and the runner phase sit behind a flag, default off, and cara-mentor-runner is stopped and disabled; evidence includes redacted detail and a bounded context excerpt, and weekly_analysis runs only when there is at least one detailed unresolved issue or one feedback row; the ledger is reserved after a successful response, timeouts and transport errors are refunded; the Mentor gets its own timeout ceiling up to 600 s; mentor_tick retries the review phase once when retryable; improvement.py replay <period> is an operator command documented in CARA §9; the allowlist sent to the model is size-filtered and oversize targets produce source_too_large; the patch blocklist becomes token-aware with re.compile( allowed; new drafts appear in the weekly review and accept-on-draft answers honestly; the unreachable evaluation replay gate, regression ceilings and the improvement_evaluator profile are deleted; re-enable candidates only with a stronger MENTOR_MODEL and a structured-edit patch format.

## Consequences

one weekly inference service instead of two active units; the proposal half gains real input; the immutable evidence boundary is kept.

## Resolves

proactive-jobs-logs#2, task-runtime-mentor#1, #2, #4, #5, #6, #7, #8, #10, #12

## Depends on

owner decision

## Status log

- 2026-09-07: proposed by the review; not yet discussed with the owner.
