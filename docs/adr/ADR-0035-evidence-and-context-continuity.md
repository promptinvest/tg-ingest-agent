# ADR-0035 — Evidence and context continuity

Status: Accepted for implementation, 2026-10-25; qualification/deployment pending.

## Context

The owner requested a full agentic architecture review and approved proceeding
with the proposed first release. Failed conversation qualifications showed that
the existing repair path discarded current-work context and requested structure.
Retrieval discarded a second relevant chunk from the same note and keyword
fallback always used the document prefix. Memory candidate validation accepted
some clearly opposite statements when they shared a word with real evidence.

## Decision

Extend existing conversation, knowledge and memory modules, with no new service,
provider, schema, tool permission or model-profile change. Preserve one immutable
turn context through initial generation and repair. Keep all action/reference
and artifact guards. Retain bounded, source-separated passages when fusing
retrieval and select real keyword windows. Preserve existing extractive KB
answers and reject invented or cross-passage quotations. Show memory certainty
and provenance in prompt context; reject clear evidence contradictions without
claiming lexical checks are a general semantic verifier.

Start with these foundations and outcome regressions. Adaptive tool reasoning,
grounded analysis, broader tools and asynchronous research remain subsequent
work; this release does not claim ChatGPT capability parity or a measured IQ.

## Compatibility and acceptance

Existing user flows, history, reminders, confirmations, persona, budgets and
current model trial remain. No memory backfill or automatic change to existing
records. Public helper defaults remain compatible. Exact-source Linux normal
and shuffled regression gates, CI and bounded real-model qualification precede
guarded installation. Preserve backups, exact stopped schema/data, original
readiness/facts/scoped reads, protected files, alert/retention policy and forward
containment. The ongoing model trial needs a new authenticated observation
segment and model-only revert bound to the new runtime; old evidence stays
unchanged and the original observation end remains.

## Status log

- 2026-10-25: owner said “go” after the architecture review and recommended first
  release. Implementation underway; no deployment claimed by this record.

- 2026-10-25: first actual-model cohort rejected: context was present, but four
  answers lost a required field or structure. Preserve that failed cohort and
  its charges. Add narrowly bound code-owned recorded-work reads and a shared
  format correction; mixed/ambiguous requests retain inference. Independent
  holdouts protect identity, untrusted/edited sources and extra intents.
  No model, budget, schema or tool permission change. New qualification pending.

- 2026-10-25: source review closed new format-event reporting: additive RU/EN
  labels and existing actionable/failure report integration. No authority
  or model change; these two report modules join the explicit runtime delta.
