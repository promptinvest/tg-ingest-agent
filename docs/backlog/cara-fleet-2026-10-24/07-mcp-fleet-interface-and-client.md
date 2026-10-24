# CF-07 — Reusable MCP fleet interface and Cara client

- Status: Implementation in progress; published facts/retained foundation and durable-client candidate; MCP disabled in production.
- Priority: P2 integration. CF-01–CF-06 keep their existing business priorities.
- Planning: capability and client/server contracts can be designed immediately.
- Production prerequisites: CF-01/02/03/04 for the facts/client path; the applicable CF-05 adapter for each project-read capability. CF-06 gates any future write exposure, which is outside this package.
- No CF-01–CF-06 package depends on CF-07, MCP availability or MCP adoption.
- Size: separate contract, read-only adapter, Cara-client and project-read increments.

## Business purpose and boundaries

Give Cara and future approved clients a standard way to reuse established fleet knowledge and project observations. The owner explicitly requires preservation of the existing backlog's business functionality and goals.

The primary outcomes remain: answer known facts without host probes; understand natural questions and requested format using the existing budgeted LLM; give direct useful answers; preserve responsiveness; distinguish project operation from development progress; and expand project work under existing authority/recovery rules.

MCP discovery, connection and successful tool calls are transport checks. They do not satisfy a requested-answer or project-outcome criterion. This work cannot replace or postpone CF-01–CF-06, weaken their acceptance criteria, or require a protocol migration before shipping their fixes.

This package is part of the proposed roadmap; optional means Cara can operate with the MCP runtime path disabled, not that the item silently disappears from the roadmap. A2A stays deferred until a concrete independent project agent needs delegated goals.

## Architecture and source map

Use a thin adapter over the same capability implementation that supports Cara's internal path:

    Cara / authorized client -> MCP adapter -> shared capability API
    Cara internal path -> the same shared capability API
    Shared capability API -> local CF-01 facts (catalog; zero host inspections)
    Shared capability API -> existing durable executor/spool -> restricted gateways (live reads)

Extend the existing fleet/capability modules; do not replace the bot, router, knowledge system, task runtime or host gateways. Prefer a central local interface over per-host MCP servers. Preserve pinned SSH, root-owned gateway policy and the separate credential-owning executor. The research worker remains networkless.

Inspect fleet.py:331-592, fleet_protocol.py:47-120, cara_fleet.py:64-133, fleet_host.py, tool_broker.py:99/193, skill_manifest.py and task_runner.py. Bounded I/O/accounting patterns are in voice_svc.py, inference_io.py and call_budget.py; durable requests/evidence/backup in worker_client.py, store.py and backup.py. Line numbers refer to the original review baseline and may move.

Keep Cara's core stdlib behavior compatible. Any selected MCP SDK and transitive dependencies belong in an isolated, pinned adapter environment unless a later explicit implementation design justifies a core change. No new model/provider or model call inside the fleet server is required.

## Initial read interface

Expose only compiled, allowlisted capabilities whose implementation is installed and qualified:

| Proposed interface | Source / meaning | Required behavior |
|---|---|---|
| Fleet/project resource or fleet_lookup tool | CF-01 descriptive facts, relationships and completeness | Zero host inspections; source/date/conflict/unknown metadata preserved |
| project_observe | Applicable CF-05 project capability | Validated project scope; only requested observations; explicit freshness |
| investigation_start | CF-04 bounded durable read request | Return a request handle without blocking Cara; no project state mutation |
| investigation_status / investigation_result | The same durable request/evidence | Caller-bound access, honest phase/status, timestamps/windows and retained uncertainty |

Names above are proposed application API names, not claims that these tools already exist. Schema/policy and permission rules are shared with the internal path. A stable resource or equivalent lookup tool may support a client's feature set without changing meaning.

Read-only refers to protected project/business state: ordinary durable job/evidence/accounting records are still required. Do not expose maintenance/restart, deployment, cleanup, backup-start, generation, project-database modification, repository write or messaging tools in CF-07. Preserve the currently supported approved restart path unchanged; do not expose it through MCP as a demonstration.

## Protocol, client and transport contracts

Record and pin the MCP protocol/SDK combinations actually supported. The verified reference at planning time is MCP 2026-07-28; recheck official specifications and actual SDK/client compatibility before implementation. Do not silently fall back to a version that loses scope, freshness, authorization or result semantics.

Keep MCP protocol version separate from application capability/schema/policy versions. Version/capability discovery is not permission to import arbitrary tools or instructions into Cara's closed action set. Check returned schemas and capability identity against the reviewed manifest.

Initial transport is local stdio through an unprivileged adapter, preserving the existing fleet boundary. A future private HTTP or remote-client deployment needs an explicitly scoped client authentication/authorization and deployment design; no public listener or per-VPS server is part of this package.

Preserve caller identity and allowed project/read scope from trusted client policy, never from a model-supplied principal or approved flag. Never pass SSH keys, privileged transport configuration, application secrets or an unrestricted inherited environment to the adapter.

Return bounded structured observations and sources, not a new owner-facing diagnostic report. Cara's existing LLM/planner and shared reply policy choose relevant evidence and natural presentation. No extra synthesis/model call is added merely to translate protocol envelopes.

## Asynchronous work and conservative outcomes

Before investigation_start, the client durably creates a caller-bound idempotency key. The server binds that key to the trusted principal, input hash and capability/policy revisions, returning the same durable handle for a matching retry and rejecting conflicting reuse. This allows reconciliation when the initial response is lost before the client learns the server handle.

Map calls to CF-04's existing durable read jobs, with request/phase identity, nonce/input hash, owner-source binding, capability/policy revision, project scope, deadline, timestamps and windows. Do not create a competing MCP execution ledger or persistent transcript store.

Base start/status/result operations must work without MCP Tasks. If Tasks support is selected, explicitly negotiate it and map its handles/statuses to the same durable jobs. Protocol cancellation is a request; do not claim that a real operation stopped until its lifecycle confirms it.

On timeout/disconnect, reconcile by durable request identity before retrying or changing transport. One logical request cannot become two investigations, two paid calls or duplicate deliveries. Bound per-client and aggregate calls, queueing, output and deadlines under the existing budgets/concurrency policy.

Authentication alone never establishes owner approval for an effect. A future write interface requires CF-06, an explicitly scoped owner-requested operation, exact reviewed authority/confirmation and that application's release/containment rules. A protocol task, resource, tool annotation or client-supplied approval flag grants none of these.

## Business continuity and migration

MCP disabled, incompatible or unavailable must leave the established internal capability path and ordinary Cara functions operational. Preserve an equivalent internal read path and the local last-good descriptive facts needed for known-fact answers.

Fallback is allowed only with equivalent target scope, provenance, freshness and policy; reuse the existing durable request after ambiguous submission. If equivalent current observation is unavailable, give a specific observation gap. Never substitute unrelated host scans, stale current-health claims or generic technical dumps.

Preserve prior request/event/receipt interpretation, cards, approvals, history, budgets, notes, reminders, calendar, journals, speech, research, memory and checked backups. No new adapter-owned content persistence or hidden retention. Use existing records and integrate with the applicable content/purge policy; CF-06's remaining lifecycle obligations remain tracked separately.

## Delivery increments and dependencies

1. Contract design can start now: define shared capability/client boundary, version matrix, allowed tools and internal/MCP parity fixtures. This does not block any original package.
2. Facts/client MVP after CF-01/02/03/04: local read-only interface and explicit Cara client integration; facts work during MCP outage. Keep owner language and detail preferences intact.
3. Project-read increments after relevant CF-05 adapters: expose them one at a time, with scoped freshness and the same business assertions as internal calls. A missing adapter remains a capability gap, not a prerequisite to publishing unrelated fixes.
4. Wider clients/transports and write exposure are future scoped work. Write exposure additionally requires CF-06. They are not part of CF-07's read-only definition of done.

## Acceptance criteria

- Retain all 47 original regression specifications and original package definitions of done. Run applicable business cases through both paths; unrelated transactional/lifecycle cases still pass unchanged.
- Catalog lookup through either path makes zero SSH calls and supplies requested facts or field-specific unknown/conflict. MCP outage still permits known-fact answers.
- Cara's free-form RU/EN meaning, context, requested tables/detail, concise preferences, corrections and required entities remain correct; protocol JSON never substitutes for the answer.
- Only selected project observations are collected/reported. Server discovery does not broaden project or host scope.
- Slow/failed protocol/model/tool work does not block owner updates or reminders, and does not reintroduce the normal idle-poll delay.
- Disabled/incompatible MCP preserves baseline flows and equivalent internal reads; no automatic downgrade of policy/freshness/schema.
- Reconnect/fallback uses one logical investigation/request and cannot duplicate paid work or result delivery.
- Unnegotiated Tasks support is not required for the base asynchronous path; optional Tasks map to the same durable record.
- Unapproved principal/project, unknown tool/schema, injected server instructions and model approval flags cannot expand reads or grant effects.
- No maintenance/write tools are exposed; existing exact approvals and secret/sandbox boundaries remain unchanged.
- Adapter cancellation, late results and existing lifecycle handling remain truthful; no hidden persistent content or content recreation after applicable purge.
- Business acceptance, protocol compatibility and operational release evidence are recorded separately. Protocol tests alone cannot close CF-07.

Use [mcp-regression-cases.json](https://github.com/promptinvest/tg-ingest-agent/blob/main/docs/backlog/cara-fleet-2026-10-24/mcp-regression-cases.json) for additional interface/parity scenarios and [regression-cases.json](https://github.com/promptinvest/tg-ingest-agent/blob/main/docs/backlog/cara-fleet-2026-10-24/regression-cases.json) for the preserved original business corpus. These are test specifications, not implemented tests or passing qualification.

## Implementation and verification

Extend existing fleet, gateway, task and assistant suites; add focused MCP client/server contract tests in an isolated Linux stage. Use synthetic local peers and documentation-only facts. A later implementation's real-model/release qualification follows existing authorization/budget gates, not an unbounded new evaluation.

Update CARA.md, SOLUTION.md, the ADR/backlog, installed-source/runtime checks and the operator runbook in the same implementing change. Preserve all original release, backup, stopped-data parity, monitor/alert and other-application checks. Public tests/issues contain no real fleet identities or operational secrets.

## Primary protocol references

Verified October 24, 2026; pin the implementation's tested versions separately:
- [MCP specification](https://modelcontextprotocol.io/specification/2026-07-28): resources and tools with version/capability contracts.
- [Standard transports](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports): stdio and Streamable HTTP bindings.
- [Optional Tasks extension](https://modelcontextprotocol.io/extensions/tasks/overview): durable asynchronous handles and explicitly supported capabilities.

## Tracking and authority

Tracker: [CF-07 / #8](https://github.com/promptinvest/tg-ingest-agent/issues/8).
Parent roadmap: https://github.com/promptinvest/tg-ingest-agent/issues/7.
Upstream facts/client production phase: [CF-01/#1](https://github.com/promptinvest/tg-ingest-agent/issues/1), [CF-02/#2](https://github.com/promptinvest/tg-ingest-agent/issues/2), [CF-03/#3](https://github.com/promptinvest/tg-ingest-agent/issues/3), [CF-04/#4](https://github.com/promptinvest/tg-ingest-agent/issues/4). Project-read phase also consumes relevant [CF-05/#5](https://github.com/promptinvest/tg-ingest-agent/issues/5) adapters. [CF-06/#6](https://github.com/promptinvest/tg-ingest-agent/issues/6) gates future out-of-scope write exposure. CF-01–CF-06 acquire no reverse dependency on this interface.

This owner request updates the backlog only. A subsequent implementation request follows the standing implementation-includes-deployment rule within its scope and original gates. This planning update authorizes no runtime interface, new client/account, live check, effect or paid qualification.

## October 24 - Offline MCP facts parity contract

CF-07 now has an optional stdio facts adapter/client over the same validated query
cells. The pinned modern protocol uses per-request version metadata and discovery;
trusted local root-owned configuration binds caller scope. Discovery cannot grant
new tools or authority. Scope denials are not bypassed, and interface failure uses
the same already-bound internal facts and answer obligations. Transport has bounded
output/deadline/concurrency, an explicit restricted environment and no extra model.

Twenty-seven isolated Linux checks pass. This is an offline facts-only increment:
no production endpoint/client is enabled, no investigation/project API or Tasks is
advertised, and no new content ledger exists. Durable read parity, unprivileged
installation, owner-loop responsiveness and complete CF-07 acceptance remain open.

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

## Increment 04 durable base/client checkpoint

Candidate base investigation_start/status/result/cancel uses the existing ledger
and ordinary async Agent workflow. prepare_start/start_investigation commits the
exact key/source/input/principal/policy/format intent before first stdio. Prepared
work is inert; only matching server promotion admits execution. Restart/lost
responses recover one handle. Scope/owner/source/policy checks apply before
dispatch and on results, without fallback bypass.

Explicit cancellation is requested while a future runs, settled only on actual
confirmation, and unknown/ambiguous after started proof is lost. Protocol
notifications do not confirm cancellation. Tasks is unsupported/not advertised;
a Tasks-capable caller still uses working base async. No both-peers Tasks, new
project write, listener or A2A claim.

All 15 MCP and 47 original IDs are executable without lifecycle skips. Focused
Linux 227 passes in 14.395 s, including 16 durable-intent cases and protected stdio.
Seventeen pure operator/orchestration fixtures are offline unbound-draft evidence.
The frozen lossless task-page/artifact suite passes 45 tests in 22.201 s in isolated
Linux, including ten new actual pipeline regressions; failed originals remain.
Exact full/model/CI and actual installed identity/policy/readiness remain pending.

Source/archive/operator/window remain UNSELECTED. Default MCP stays disabled;
facts-only, retained and admission activation require their own qualified policies.
No package completion; CF-01 through CF-06 retain independent acceptance.
[Increment 04](../../cara-fleet-increment-04-2026-10-24.md).

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

## Increment 09 operational scope and minimal focus

The accounted `9566d1b` model cohort fails phase 8 despite Linux/CI success: a new
named-project dated-observation question requests inheritance with no canonical
prior. An earlier memory answer passed weak relevance assertions; review finds
unrequested broad diagnostics. Strict rejection and all charges remain retained.
Clarify operational explicit/inherited scope and minimum metric focus, preserving
local catalog prior inheritance, valid broad reports, validators/renderers/age,
authority and model budgets. Focused actual Agent/runtime/catalog/relevance
regressions pass in isolated Linux. Fresh full/CI/model/installed release gates
remain required; every package stays open, production `99037c5` / MCP disabled.
[Increment 09](../../cara-fleet-increment-09-2026-10-24.md).

## Increment 10 narrow semantic reads and paired business proof

Correct a remaining collection defect: new explicitly narrow host metric plans
without optional read_scope still inherited the legacy blanket project facet.
The semantic mixin now derives the existing host-only contract when no project
or inherited scoped obligation applies. Executor/gateway project reads are then
excluded. Base legacy parsing/wire, stored observations, broad resource/overview,
project scopes and exact restart confirmations remain unchanged.

Additional source-linked parity tests exercise original negative runtime,
conflict/completeness/date, mixed and preference assertions through internal and
MCP reads with exact observations/final pages. Preserve both original corpora.
These tests do not constitute model or installed qualification; current-source
complete Linux/CI, bounded accounted requested answers and every backup/readiness/
monitor/recovery gate remain required. Production remains accepted `99037c5`,
MCP disabled; every package stays open.
