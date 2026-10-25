# ADR-0036 — Read-only grounded analysis in ordinary requests

Status: Accepted for implementation, 2026-10-25; qualification/deployment pending.

## Context

The owner approved the next increment after ADR-0035: use Cara's existing
read-only tools to compare saved options, inspect available project facts and
produce a sourced recommendation. The owner also asked why October28 was a
prerequisite. That date was a suggested stable model-observation checkpoint,
not an application dependency. Implementation and qualification can proceed
independently; earlier deployment requires a separately recorded runtime epoch
while preserving the original model-trial deadline and evidence.

Ordinary conversation currently assembles context without adaptive tool
selection. The existing task ledger, broker, scoped readers, synthesis profiles,
delivery records and cancellation already provide the required foundations.

## Decision

Extend the existing task skill and router's `task_start` contract with an
explicit analysis mode. Preserve the legacy route and handler behavior when
that mode is absent. A code-owned source-bound plan invokes a compiled
`analysis.reason` tool; an internal `analysis_engine.py` helper owns the
bounded decision/receipt/synthesis phases, within the existing service.

The first release admits only `knowledge.search`, `knowledge.read`,
`reminders.read` and `fleet.lookup` against the current owner's data and
published facts. Each selection is checked through the existing broker and
provenance rules. Source content remains data. Local reads keep immutable
receipts in the same task ledger; no new schema or historical backfill is
planned. Preserve source dates, hashes, evidence gaps and truncation metadata
in the additive analysis contract without changing legacy reader schemas.

Use existing Sol task-planning and task-synthesis profiles. Normally perform
an initial selection, an adaptive decision after evidence, and final synthesis;
one bounded correction may fit inside the existing four-profile-call limit.
The parent orchestration plus child reads remain inside the existing total
step limit and USD0.15/task, USD2/day and USD12/month controls. Reserve and
settle through the existing gateway; unknown outcomes remain accounted.

Facts must be tied to actual source quotations. Conclusions/recommendations
are explicitly distinguished from recorded facts and disclose contradictions
or missing evidence. Citation identity alone is not semantic verification.
Follow-ups bind to an owner-scoped exact task/result; ambiguous references
ask for clarification rather than selecting another task. Reuse cancellation,
restart recovery and delivery ambiguity containment; never blindly resend a
lost paid phase or a possibly delivered reply.

## Compatibility and acceptance

Ordinary chat, direct recorded-work reads, quote-only KB answers, existing
task plans, reminders, confirmations, stored history and model profiles remain.
Preserve the current runtime, data, protected files, source publications,
fleet gateways, grants, alert opt-outs, retention and neighboring services
until guarded acceptance of the exact qualified candidate.

Required qualification includes normal/shuffled Linux regressions, exact-source
CI, injected sources, cross-chat/edited references, unavailable evidence,
contradictory dated notes, actual adaptive additional reads, requested format,
budget exhaustion, cancellation, lost paid phases and delivery ambiguity.
Real-model outcome review must exercise the adaptive path and distinguish
actual model calls from deterministic fixtures. Measure usefulness, factual
support, latency and spend; finite results do not establish general IQ parity.

Any transition during the ongoing model trial preserves its October28 end,
old receipts and all source epochs/pauses. Qualify a new chained continuation,
fresh observer and conditional model-only reverter for the new source. Never
replay a consumed controller or restore an old database.

## Status log

- 2026-10-25: owner said “go” to the concrete read-only analysis proposal and
  questioned the suggested October28 hold. Implementation started in an
  isolated checkout. No new deployment is claimed by this record.
- 2026-10-25: owner explicitly selected “Deploy as soon as qualification passes;
  record a separate trial segment”. Preserve the original October28 model-trial
  deadline and model/budget settings; the qualified operator selects the exact
  package and bounded active-session window without claiming hash-specific
  owner approval.
