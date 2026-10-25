# ADR-0036 — Read-only grounded analysis in ordinary requests

Status: Implemented e242516; deployed and accepted2026-10-25T20:04:47.643688+00:00; actual observer and independent audit passed.

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
- 2026-10-25: candidate e242516 passes exact-source normal/shuffled Linux,
  exact-head CI40225722321 and isolated operator checks. All six final-source
  outcomes pass independent semantic review;20 new calls add USD0.152374.
  All57 known qualification rows total USD0.421429; earlier failed candidates
  retain their37 rows/USD0.269055 and29 historical unknown rows remain unchanged,
  with no new unknown charges. The pinned operator-selected USD0.60 cumulative
  evaluation amendment preserves those rows, all six semantic criteria and
  application USD2/day, USD12/month, USD0.15/task and four-call limits. An operator
  proof-sealer failure and its narrow redaction-equivalence correction remain
  retained. Final proof8768ac83 was sealed19:44:26UTC without further app/model
  calls; manual review28d4ed20 supports all six outcomes. Guarded acceptance,
  third-source observer and independent live audit remain pending. No deployment is claimed here.

- 2026-10-25: data-only materialization passed; the operator selected
  19:45:30.518099-20:45:30.518099UTC under the owner implementation/timing
  request. Fresh release-attempt1 controller launched once (PID3308942), with
  preflight/backups/installed gates running. Application acceptance, actual third
  source epoch and observer/audit remain pending; never replay the controller.

- 2026-10-25: first controller forward-paused19:47:22UTC before new worker/main
  startup. The operator shim generated seven legacy guard-counted caches plus
  eight root caches; all65 original members/all15 caches, new source and56-table
  stopped data remain exact. A new forward-only package adds single-argument
  -IB/kernel execution qualification; original installer/controller are not
  replayed. Fresh completion/acceptance and source first-start remain pending.

- 2026-10-25: forward package qualified on actual isolated Linux; fresh root2
  materialization/prepared checks passed. Controller launched once(PID3317152)
  in operator-selected20:01:11.094085-21:01:11.094085UTC, authorization5be45645.
  Original source/full/CI/model evidence and trial deadline remain; acceptance,
  actual first analysis exposure and sandbox observer/audit are pending.

- 2026-10-25: accepted20:04:47.643688UTC, sourcee242516/build77f6380d9912/
  deployment73e0133c10ad848334a43e76, acceptance02ddfe8d. Forward completion
  retained all failed65+15 operator/cache artifacts and all56-table data without
  reinstall or DB restore. The third source starts20:02:28.895163UTC after the
  recorded918.431712-second source gap; prior pause and October28 deadline remain.
  Actual sandbox observer installed20:05:42UTC; independent read-only audit
  c500fede passed20:06:41UTC, all93 runtime/99 protected files and original
  model/budget/peer/alert/retention settings preserved. One normal notice attempt;
  no manual messages or historical/controller/source-publisher replay.

- 2026-10-25: production tag `production-2026-10-25-grounded-analysis` published
  and remotely verified: annotated object5138ea99, peeled sourcee242516. The
  documentation publication preserves that exact installed runtime.
