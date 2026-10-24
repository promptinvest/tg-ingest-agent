# Cara fleet increment 03 - October 24, 2026

Status: typed project-read implementation candidate; not deployed. This increment
extends published `8f50282`. Production remains the accepted `99037c5` / build `f54906e304fe`
business baseline. [Accepted baseline](cara-fleet-increment-01-2026-10-24.md) and
[published foundations](cara-fleet-increment-02-2026-10-24.md) retain their evidence
and failed attempts.

The owner's implementation request and standing implementation-includes-deployment
rule authorize completing this guarded release within scope. No separate
hash-specific approval is claimed.

| Release field | Current state |
|---|---|
| Exact target commit and canonical archive | UNSELECTED |
| Full normal/shuffled Linux, pinned lint and CI | PENDING for the frozen target |
| Required bounded accounted model qualification | PENDING for the frozen target |
| Real source/policy bindings and service-identity readiness | PENDING |
| Backup/parity, runtime/sandbox, monitor/alert/recovery and preservation gates | PENDING |
| Operator/controller and active-session window | UNSELECTED |
| Deployment and acceptance | Not started |

## Useful project evidence from existing monitors

Selected-project runtime reads can consume existing monitor results without
starting a monitor or inspecting unrelated colocated applications. Policy selects
the exact implementation and fixed resource; neither model output nor a display
fact can supply a path, address or effect.

project-daily-lingo-observer/v1 preserves generation state, reserve cells with
coverage/date, application/database state, unfinished work, and the budget guard's
ceiling, heartbeat, probe date and outcome. A ceiling is not measured spend.
Accepted release and target stock appear only with a separately verified root
binding, never from a running container. Missing, stale and partial evidence stays
explicit. Private profiles, jobs, assignments, errors and raw rows are discarded.

project-authenticated-proxy-route/v1 reads one existing authenticated host receipt
or one bound collector file. It checks the configured profile/protocol/route proofs
and retains the inner host sample date separately from the collector date. A fresh
outer collector cannot refresh an old sample. Current connections and cumulative
connection counters remain distinct. Neither proves throughput, unique users,
long-term stability or unsampled client-route health. Endpoints, ports, boot IDs,
configuration hashes and private messages are not exported.

The pure monitor module does no I/O. The gateway reader retains root ownership,
non-writable ancestry, descriptor-relative no-symlink regular-file reads,
duplicate/nonfinite JSON rejection and output bounds. Legacy reads keep their
64 KiB default. Only these two monitor sources opt into a 2 MiB input bound; each
output remains10 KiB. The observer reads fixed latest, one validated timestamp
snapshot and latest again. A changed pointer yields unavailable, without following
a second snapshot, scanning history or refreshing a producer.

## Reviewed public GitHub development capture

fleet_development_publish.py accepts a bounded operator capture of one fixed public
repository, branch, exact revision, up to six matching Actions runs and an explicit
bounded issue list. The existing authenticated connector supplies those fixed
responses. Neither exporter nor app opens a network connection, discovers accounts,
stores credentials, creates a schedule or writes a repository. The first Cara
repository capture is within this implementation scope; other connections are not
implied.

Root policy pins repository/ref/revision, exact URLs, per-source and aggregate
canonical JSON digests, source ID, issue scope, output path and maximum age.
Repository public identity, stable before/after branch head and capture dates must
agree. The operator also rechecks public visibility/name at the end and retains
that review privately; the exporter itself validates its captured repository record
rather than claiming a remote visibility check. Digests establish reviewed-input
integrity, not raw HTTP signatures or independent GitHub authentication.

Only revision/commit date, typed Actions state and sampled issue number/title/state/
update date survive. Raw actor/email/body/message/token/configuration data is
discarded. The additive project-development-actions/v1 contract preserves Actions
updated_at as a source update date; completed status does not invent completed_at.
Issue updated_at, capture, export, source evidence and read dates remain distinct.
Existing generic development v1 is unchanged.

CI/head mismatch, stale or disconnected evidence and incomplete coverage stay
explicit. CI and issues are bounded samples with complete=false. A successful run,
closed issue or deployed process cannot establish overall development completion,
deployment or health.

Root-only install-export validates the sealed artifact and policy before atomic
publication. Invalid policy/source/revision, backwards capture/export dates and
conflicting same-date documents preserve last good output. An identical document
is idempotent, retaining its identity and installed metadata. Existing trusted
ownership/mode is preserved. A post-replacement durability failure is explicitly
ambiguous: inspect the current publication, with no rollback or blind retry.
Normal backup and recovery policy is unchanged.

## Answer and historical compatibility

Project answers preserve useful monitor/development facts, source dates and precise
gaps. Detailed table requests retain their table format and full evidence. Scoped
recorded-evidence footers preserve each source's dates, scope and gaps instead of
denying valid recorded backup evidence; the legacy diagnostic footer stays exact.
Planner descriptions identify runtime application/route summaries accurately. Proxy
connection observations can answer their actual metric fields; unrelated host
CPU/RAM/disk, request traffic and throughput remain unavailable without their own
evidence. Process activity and a fresh outer collector cannot fill those gaps.

The capability registry adds exact alternative implementation versions rather than
changing old payloads. Previously qualified append-only read-contract digests are
accepted explicitly for retained/inherited reads; arbitrary hashes still fail.
Original v1 observations, source dates, card/restart semantics, host/all scope,
legacy depth/size limits and existing source/authority fences remain compatible.

## Verification checkpoint and limits

The project-only focused isolated Linux run passed 201 tests in 0.622 s. It includes
32 development-publisher, 46 pure-monitor and 19 monitor-integration cases alongside
adapter/dispatch/answer regressions; these groups are within the 201 total.
It excludes the pending durable-client increment. Earlier new-fixture/input and
test-module selector failures remain logged; the fixture correction supplies the
failed attempt its unchanged assertions require. No rollout used those failures.

This supporting checkpoint is not a full frozen-source release receipt. The final
project-only canonical target still requires full normal/shuffled Linux, pinned
lint/CI and required fresh bounded accounted model qualification.

The [original 47 regression](backlog/cara-fleet-2026-10-24/regression-cases.json) and
[additional 15 MCP](backlog/cara-fleet-2026-10-24/mcp-regression-cases.json) specification
files are unchanged.
All 47 have source-linked executable business assertions. A parallel uncommitted
MCP runner replaces the remaining three lifecycle skips; its all 15 source-ID
qualification belongs to the separate next increment, not this publication.

Frozen model completions prove deterministic grounding/presentation paths, not
free-form model quality. Synthetic project inputs do not establish live source
bindings, current accepted project settings, end-user health or production latency.
Real private capture/source policies and actual unprivileged reads are pending;
no readiness or package completion is inferred from offline tests.

## Independent package status

No package is complete. This increment advances CF-05 and relevant CF-02/CF-03
answer behavior without depending on MCP.

| Package | Current basis | Remaining acceptance |
|---|---|---|
| [CF-01](backlog/cara-fleet-2026-10-24/01-fleet-facts-and-sync.md) | Accepted descriptive publication and reviewed manual source workflow, unchanged here | Own source-status/lag, failure/history/privacy and compatibility acceptance audit; review later source changes explicitly |
| [CF-02](backlog/cara-fleet-2026-10-24/02-semantic-queries-and-evidence.md) | Accepted semantic catalog/obligations and scoped plans; richer candidate evidence | Fresh RU/EN/paraphrase/context/correction and requested-answer qualification for changed paths, plus full release gates |
| [CF-03](backlog/cara-fleet-2026-10-24/03-adaptive-grounded-replies.md) | Accepted name-preserving cleanup and confirmed guidance; candidate detailed tables and dated summaries | Fresh entailment/omission/format qualification and complete compatibility/release evidence |
| [CF-04](backlog/cara-fleet-2026-10-24/04-responsive-durable-execution.md) | Accepted nonblocking phases, pending cadence and bounded queue/start/cancel, unchanged by this project increment | Independent all-phase/accounting/recovery and installed readiness acceptance; no new budget/transport limits |
| [CF-05](backlog/cara-fleet-2026-10-24/05-project-read-capabilities.md) | Accepted selected-project enforcement; candidate observer/route/public-development sources | Protected real bindings, actual service-UID reads, useful dated answers/gaps and each gateway's preservation/full release gates |
| [CF-06](backlog/cara-fleet-2026-10-24/06-governed-capabilities-and-lifecycle.md) | Accepted typed fleet lifecycle and consumed-token retention; published shared read/task foundation | Independent shared-authority/resource-lock and full task/card/effect/purge/restore compatibility audit before broader effects |
| [CF-07](backlog/cara-fleet-2026-10-24/07-mcp-fleet-interface-and-client.md) | Published optional facts/retained foundations; default disabled | Separate uncommitted durable-client/lifecycle work, exact business/full/model and real unprivileged readiness/activation; Tasks/write/A2A not claimed |

## Release and operator handoff

Freeze and publish the project-only source before exact qualification. Use a fresh,
single-use bounded-window controller after full Linux/CI/model checks and fresh
preflight. Preserve protected online/stopped backups, original rows/columns and
environment/configuration, loaded-source/runtime/readiness, sandbox/spool denial,
monitor/alert/retention/recovery and neighboring-application identity gates.

Install only reviewed sanitized snapshots/fixed policies after code qualification.
Verify exact discovery and selected reads under the real service identity and
namespace. Keep raw captures, bindings, fleet identities and operator evidence out
of Git/issues. Use atomic incoming publication for watched operator artifacts.

No new monitor, probe, schedule, model/provider, budget, write capability or
notification. No manual Telegram test, historical update/controller replay, live
purge/maintenance demonstration or paid corpus work. Failure preserves current
image/data/evidence and pauses forward under existing recovery rules.

Parallel durable MCP admission, prepared-before-send intent, cancellation and
source-linked15-case runner changes remain UNCOMMITTED separate work. They are
excluded from this project's implementation/publication claim and require their
own coherent primary-spec/ADR/backlog/runbook record and qualification.
