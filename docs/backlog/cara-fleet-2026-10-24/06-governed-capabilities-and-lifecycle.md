# CF-06 — Governed task capabilities and fleet content lifecycle

- Status: Implementation in progress; first business release accepted; full package acceptance remains open.
- Priority: P2 for lifecycle integration; required P1 gate before adding broader project effects.
- Depends on CF-04/CF-05 contracts for execution integration. Purge analysis/fixtures can start independently.
- Size: Large; separate lifecycle/purge, read-broker adapter and existing-effect compatibility increments.

## Problem and source evidence

Fleet has its own requests/events, approval, budget/recovery and delivery state machine. tool_broker's registry/execution sites do not include fleet; task_runner's approved effects currently support local reminder creation. New cross-project workflows would otherwise duplicate authority and recovery rules.

Fleet payloads retain original questions, answers, context, evidence and approvals. Existing all-content purge preview/execution and telemetry code omit fleet_requests/fleet_events and spool quiescence. This is a source-confirmed integration omission; it was not exercised as a destructive production test.

Sources: tool_broker.py:99,193; task_runner.py:1020,1558; fleet.py:331-592; fleet_protocol.py:14-27; store.py:3895-3919,3955-4009,4244-4259.

## Required capability foundation

- Add reviewed fleet/project capability adapters to the existing durable task model, retaining a separate credential-owning fleet executor and networkless research worker.
- Share source binding, attempts, risk/permission policy, cancellation, receipts and delivery/reconciliation primitives incrementally.
- Initially adapt supported read capabilities and preserve the existing cara-worker restart. Do not choose a new effect as a demonstration.
- Version target/input/output/provenance/deadline/implementation contracts. Add per-project/resource concurrency locks and durable execution-start boundaries.
- An unknown outcome requires reconciliation. Lease expiry, crash or connection loss cannot authorize another effect attempt.
- Existing cards/tasks cannot approve each other; preserve source/inventory/fingerprint binding, expiry, double token consumption and at-most-once attempt semantics.

A later owner-requested operation must declare exact project/resource/operation, reviewed runbook and policy version, current preconditions/artifact fingerprint, authorization/confirmation semantics, deadline/cancellation boundary, bounded execution/verification, immutable outcome receipt and application-specific containment/recovery.

No generic deploy/cleanup/shell/database/messaging tool. Knowledge prose and LLM output never grant authority.

## Content lifecycle and purge

Extend existing typed all-content purge preview and execution to questions/answers/context/reports/approval content/events and transport copies. Define exact retained operational evidence, its policy and disclosure before implementation. Preserve existing spend/preferences exclusions and backup/recovery policies; do not silently introduce retention.

Quiesce/cancel outstanding fleet work through the durable purge pattern. Ensure late outputs cannot recreate deleted content. Make interrupted purge recoverable and match preview counts/scope.

Keep minimal non-content consumed-token tombstones indefinitely so deleting conversation content cannot re-enable an effect. Do not erase historical release evidence as an incidental cleanup.

## Compatibility and migration

Legacy rows, events, cards, receipts and consumed markers remain readable and unchanged. Prefer references/adapters over rewriting historical state. Pending work completes under its original contract or gets a fresh proposal when that contract is no longer valid; no implicit permission upgrade.

Preserve executor/main/worker sandbox and credential separation. Preserve all existing application release gates, monitor/alert opt-outs, exact-card behavior, backup restore and ambiguous delivery rules.

## Acceptance criteria

- Registry/permission-manifest mismatch fails safely at startup or invocation.
- Model/forwarded/external content cannot supply effect authority.
- Wrong/edited/expired source, user/card, fingerprint, artifact/policy and duplicate callback cannot execute.
- Two operations on the same protected resource cannot run concurrently.
- Crash injection before/after token consumption, dispatch, effect completion and receipt publication preserves at-most-once attempts and truthful ambiguity.
- Unsupported effects explain the missing runbook; no restart substitution.
- Legacy fleet and task/reminder cards preserve their meanings and cannot cross-authorize.
- Purge preview counts every disclosed fleet content copy; execution matches the preview after quiescence.
- Late/duplicate results cannot recreate purged content. Interrupted purge resumes safely.
- Consumed tombstones survive purge and continue to reject replay.
- Checked backup/restore preserves new contracts/lifecycle without changing existing recovery semantics.
- Full task/fleet/approval/delivery/purge/backup/sandbox regressions pass.

## Implementation and tests

Inspect tool_broker.py, task_runner.py, tasking.py, runtime.py, fleet.py, fleet_protocol.py, store.py, worker_client.py and backup.py. Extend test_tasking.py, test_task_runtime.py, test_fleet.py, test_backup.py and purge assistant cases. Publish compatibility and recovery design before replacing shared machinery; avoid a whole-runtime rewrite.

Out of scope: authorizing any new project write, deleting live data for validation, retroactive history changes or executing historical deployment helpers.

## Shared policy for future interface consumers (nonblocking)

Preserve this package's original task/effect and content-lifecycle obligations. Keep the authoritative capability policy, source/approval bindings, resource locks, attempts, receipts and recovery callable through a transport-independent internal API. A later MCP consumer uses the same rules; it does not create another approval or execution system.

CF-06 has no dependency on CF-07 or MCP adoption. Its internal business/safety/lifecycle improvements can ship without a protocol interface. CF-07's initial read-only scope does not expose the existing restart or any new project effect.

Any later write exposure requires this package's qualified authority foundation plus the particular owner-requested operation, reviewed runbook and application release/recovery rules. Authentication, tool annotations, tasks and model/client approval flags cannot substitute for those requirements. Reuse disclosed storage/lifecycle rather than adding hidden interface transcripts.

## Tracking

Parent roadmap: [#7](https://github.com/promptinvest/tg-ingest-agent/issues/7). Blocked by: [#4](https://github.com/promptinvest/tg-ingest-agent/issues/4), [#5](https://github.com/promptinvest/tg-ingest-agent/issues/5). Blocks: none. Track the partial-independent increments described above separately; no dependency implies new runtime permission.

## Development contract

This is proposed development work recorded on October 24, 2026. The owner requested a thorough review and a backlog for further development; implementation, new capabilities and production changes are not represented as completed or authorized by creating this issue. A later implementation request follows the owner's standing implementation-includes-deployment rule within its explicit scope and all existing release gates.

Read AGENTS.md, the full CLAUDE.md, CARA.md, SOLUTION.md, docs/adr/README.md and docs/backlog/cara-fleet-2026-10-24/README.md. Canonical repository: promptinvest/tg-ingest-agent, local projects/tg-ingest-agent; projects/DLingo is another application. Preserve working notes, reminders, calendar, journals, speech, research, memory, budgets, history, backups and confirmations. The existing research worker remains networkless; fleet credentials stay in its separate executor; model output never supplies executable addresses, paths or commands.

Use the existing budgeted LLM gateway for free-form interpretation and appropriate reply format. Zero host probes for catalog questions does not mean zero model calls. Do not introduce a required exact-phrase interface, another routing stage/provider/daemon, or a generic shell tool as an incidental remedy.

Update BOTH CARA.md and SOLUTION.md, relevant ADR/backlog status and operator docs in every implementing commit. Tests run in an isolated Linux/VPS stage, not the OneDrive checkout. Preserve full required Linux/CI, installed-source/runtime, sandbox-denial, stopped-data parity, backup, monitor/alert and guarded-release checks. No manual Telegram test sends, historical update/controller replay, live maintenance demonstration, unrelated paid work or change to another project's release rules.

Full handoff, source map, sanitized production evidence, bootstrap facts and semantic regression corpus:
https://github.com/promptinvest/tg-ingest-agent/tree/main/docs/backlog/cara-fleet-2026-10-24

Integrated business candidate: local obligations/semantic formats, durable nonblocking phases, queue starts/cancellation, shared lookup and typed content lifecycle are implemented. Focused Linux evidence and remaining model/release gates are in [the increment record](../../cara-fleet-increment-01-2026-10-24.md). No package is complete or deployed yet.


## Accepted business baseline ? October 24

Source99037c5/buildf54906e304fe was accepted at10:46:31UTC after the full
Linux/CI, accounted semantic qualification, original data/backup/runtime/sandbox
and scoped-read gates. This is an incremental business baseline; no full package
completion is claimed. MCP remains disabled. [Exact evidence and remaining work](../../cara-fleet-increment-01-2026-10-24.md).

## October 24 - Shared task and optional MCP facts candidate

Existing task steps now wait on a retained source-bound fleet read with original
call/cost reservations, one immutable receipt and existing task delivery. Shared
retained status/result and a nonblocking optional MCP facts path preserve owner,
source, freshness and purge policy. Default MCP remains disabled; creation/Tasks/
executing cancellation are explicit gaps. Original47+15 specifications remain
unchanged and the crosswalk distinguishes active cases from unqualified gaps.
Focused Linux passes do not replace full release and actual-model qualification.
See [increment02](../../cara-fleet-increment-02-2026-10-24.md); all packages stay open.

The retained first actual task-plan qualification rejected invented runtime catalog
fields and rewritten source. Fleet-only planner guidance is now explicit; validators
and budgets remain unchanged. Old fixture defaults are isolated from installed
facts without changing app defaults or original answer assertions. The failed paid
attempt and full-gate logs remain; fresh qualification is required.

Literal-bound task plans receive the exact source length after an accounted model candidate miscounted its span; out-of-range input remains rejected. The remaining full-Agent fixture now carries its isolated facts path; original expectations and production defaults stay exact.

## Increment 04 shared-lifecycle checkpoint

Client intent and server admission reuse existing fleet/task authority, rows,
budget accounting, retained evidence and delivery. Prepared/admitted states join
original capacity, shared reads, checked restore and typed all-content purge.
Prepared work is definitely unsent; started cancellation needs real settlement.
Lost proof is unknown and cannot permit another attempt.

No new table/outbox/content ledger, effect tool or retention policy. Original
spend/preferences, non-content consumed tokens, protected facts/configuration and
backup/release evidence remain. Live purge is not a verification mechanism.

Focused Linux 227 passes, including 16 before-send/restore/purge intent cases.
The frozen taskbridge suite separately passes 45 tests in 22.201 s in isolated
Linux; failed originals remain. Final full qualification and independent shared-
authority/resource-lock/task/card/effect/sandbox acceptance audit remain pending
before any broader effect. [Increment 04](../../cara-fleet-increment-04-2026-10-24.md).

## Increment 05 existing-restart resource checkpoint

The existing allowlisted restart now uses a per-operation/unit nonblocking host
lock. A different token for an occupied unit returns resource_busy before host-
token consumption, fingerprint collection or systemctl. Existing duplicate-token
ambiguity remains. A durable executing/unknown hold blocks new attempts even after
process death releases the OS lock. A new token, fingerprint or elapsed lease
cannot make the earlier outcome known.

Automatic settlement requires the same held token's durable known final receipt
for the same unit. Missing, unsafe, malformed or ambiguous evidence fails closed.
Other operator reconciliation requires actual evidence of the prior outcome and
fresh original approval before a later effect; no clear-unknown tool is added.
The root-owned no-follow ledger retains only version, fixed service, token, state
and time as new resource evidence, beside original consumed-token tombstones.
This is non-content operational residue preserved through purge/recovery, with no
automatic expiry or implicit deletion policy.

Original cards/source/fingerprint/expiry/double-token checks, bounded 20-second
restart and existing capability allowlists remain. Authorization expiry is checked
again before dispatching the effect. Closed RU/EN replies say the new request did
not start due to busy, prior unknown outcome or expiry. No new effect or MCP write.

Published increment04 is `319dd95`. Network-isolated Linux passes 80 tests in
4.645 s: 20 new resource guards plus 60 existing fleet cases; failed originals
remain. This does not close every CF-06 crash/recovery criterion. Final full/model/
CI, installed ledger/sandbox/backup/recovery checks and independent CF-06
acceptance remain open.
No live restart or purge is used as a demonstration. Exact source/archive/operator/
window: UNSELECTED; production remains accepted `99037c5`, MCP disabled.
[Increment 05](../../cara-fleet-increment-05-2026-10-24.md).

## Increment 06 legacy and crash-boundary compatibility

The same locked restart boundary now checks old token history before fingerprint
or effect. Legacy executing/ambiguous same-unit records block a new token even
without a modern resource hold. Known final receipts permit a fresh sequential
approval; valid other-unit history is independent. Malformed/unsafe/unscoped
history fails closed under 4,096-entry/4-MiB-total/16-KiB-record bounds, without
rewriting, migrating, refreshing or deleting old evidence. Time/fingerprint/new
approval cannot clear an unknown result. No new clear/effect API is added.

Thirteen new legacy tests plus 20 resource, 60 existing fleet and ten original/
new polling cases pass 103 tests in 6.298 s in isolated Linux. Three separate real
fork/os._exit fixtures pass in 0.052 s at durable-hold-before-token, durable-token-
before-dispatch and effect-complete-before-final-receipt boundaries. New tokens
remain blocked without another fingerprint/effect/token; consumed originals stay
ambiguous and evidence bytes remain. Existing known-final-receipt reconciliation
and checked application-backup/purge/tombstone assertions are retained.

Normal DB backup does not stand for host-ledger recovery. Actual gateway release
must preserve complete protected host token/resource state and explicit absence,
with checked backups/full manifests and no rollback/recreation. That operator gate
and final full/model/CI/installed/sandbox/lifecycle acceptance remain pending.
No live maintenance or purge demonstration. Production remains accepted `99037c5`,
MCP disabled, all packages open. [Increment 06](../../cara-fleet-increment-06-2026-10-24.md).

## Increment 07 qualification correction

The legacy-history fixture cleanup uses equivalent explicit if/else instead of
a conditional expression. No assertion, unknown-outcome refusal, immutable
history, token or resource check changes.

Complete `12a47d7` Linux/CI qualification failed two fixture checks; normal ran
2,769 tests with two failures and nine baseline skips, shuffled not reached.
The failed originals remain. No application/budget/authority change. Focused
project integration/parity pass 42/ 7.357 s and a separate legacy/resource/fleet/
poll/wait/exact-hygiene run passes 104/ 7.342 s; fresh whole-source/full/model/CI
and original installed/operator release remain required. Production stays
accepted `99037c5`, MCP disabled; no package completion.
[Increment 07](../../cara-fleet-increment-07-2026-10-24.md).

## Increment 08 shared task planner guidance

The accounted model cohort for published `8cbf06f` stops at third-phase task
admission despite successful CI and two passing catalog phases. Strict existing
validation rejects an array input; malformed source binding is not repaired.
Clarify object input/provenance and complete owner-clause instructions using a
fully valid illustrative JSON step. Preserve existing validators, correction
attempts, model/provider/budget, authority and all original assertions. Focused
isolated task/source/hygiene checks pass; fresh full/CI/model/installed release
acceptance remains required. All packages stay open; production `99037c5` / MCP
remains disabled. [Increment 08](../../cara-fleet-increment-08-2026-10-24.md).
