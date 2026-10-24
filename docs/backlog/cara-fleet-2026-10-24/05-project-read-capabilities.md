# CF-05 — Project-scoped read capabilities

- Status: Implementation in progress; first business release accepted; full package acceptance remains open.
- Priority: P1 before broader project operations.
- Depends on CF-01/CF-02 contracts and CF-04's bounded I/O for integrated release.
- Size: Large; scope contract and individual adapters are separate reviewable increments.

## Problem and sources

Plans carry project IDs but inspect envelopes carry only hosts and facets. fleet_host reads all configured project probes on the host; fleet_report similarly reports them all. A request for one project can include another merely through colocation.

Sources: fleet.py:226,387-389; fleet_protocol.py:104-107; fleet_host.py:109-157; fleet_report.py:98-103,154-158. Present probes cover manifests, selected container state and limited HTTP health. Those do not establish user journeys, development progress, verified backups or proxy client load.

## Contracts and implementation

Carry project IDs and selected capabilities through validated planning, protocol, executor, gateway, typed evidence and answer validation. A small reviewed adapter registry specifies:
- Stable project/resource/capability IDs and implementation/policy versions.
- Allowed input/output fields and execution location.
- Freshness/window, cost/time/size limits and redaction policy.
- Root-owned mappings to fixed endpoints/resources; no model paths/URLs/commands.
- Observed/unavailable/not-found/not-configured/recorded statuses.

Version the wire capability contract and discover installed gateway versions. Upgrade gateways before enabling requests that need them. An older gateway returns a scoped capability gap; it cannot silently broaden one-project reads to the entire host. Keep legacy supported request/receipt interpretation explicit.

Support host infrastructure, one project, a multi-host project and explicit all-project scope separately. Preserve named-project scope changes and owner-source binding.

## Initial adapter backlog

Deliver each independently without triggering the observed operation:

1. Daily Lingo: sanitized application-owned summary of existing database/delivery/intake health, generation enabled/paused, reserve/target coverage, budget guard and latest accepted release. No generation, paid corpus work, user data or new notifications.
2. Cara: existing runtime/worker state, accepted release and available verified backup evidence.
3. Proxy fleet: consume existing authenticated route-monitor results/timestamps. Actual clients/traffic/throughput need explicit corresponding evidence; host CPU is not proxy load.
4. Backups: last successful verified-backup receipts and coverage/age. Timer activity and configured policy alone cannot establish success; missing receipts stay unknown.
5. Development: define read-only repository/CI/backlog adapter contract and truthful unavailable behavior. Only connect repositories/accounts that a subsequent implementation scope explicitly includes; no new broad account permissions are implied here.

Service interpretation must include expected role/state: a successful idle one-shot, deliberate disabled component and standby node are not failed daemons. Distinguish process activity, application readiness and demonstrated end-user outcomes.

## Acceptance criteria

- Single-project requests collect/report no unrelated colocated-project metadata.
- Cara-only, host-level, explicit all-project and multi-host project requests retain distinct exact coverage.
- Unknown project/capability/version, arbitrary paths/URLs/fields and cross-project resources fail closed.
- Unhealthy application evidence overrides a superficial running-container impression; uncertainty remains explicit.
- Active timers without verified results cannot establish backup success.
- Failed/stale/missing route evidence cannot establish client-route health.
- Expected idle/disabled/standby states are interpreted correctly; not-found and unavailable remain distinct.
- Unconnected repository/CI/tasks cannot become claims about completion/backlog.
- No environment, secret, private link, wallet address or log MESSAGE text is exposed.
- Gateway updates preserve current host identities, original monitors/timers/alerts, backups, application behavior and retired/testing-host exclusions.

## Implementation and test map

Inspect fleet_protocol.py, fleet.py, fleet_host.py, cara_fleet.py, fleet_report.py and ops/fleet. Extend test_fleet.py and test_fleet_report.py plus adapter contracts/fixtures. Existing gateway rollout, source-bound policy/package verification and isolated denied-access probes remain required.

For Daily Lingo source work, read that repository's current AGENTS.md, release plan and recovery rules first. Refresh that project’s current accepted settings and containment rules from its own instructions and release records; do not hard-code a review-time operational snapshot as eternal.

Out of scope: starting backups/probes as actions, deployments, generation, paid trials, protocol traffic changes, new notifications, general shell or repository writes.

## Reusable capabilities for CF-07 (downstream, nonblocking)

Keep this package's project-read behavior, adapter scope and acceptance criteria intact. Expose its qualified reads through a small transport-independent capability API usable by Cara's internal path and a later MCP adapter. Share validation, project scope, typed evidence, freshness and gaps; avoid a duplicate implementation.

This API expresses project/business capabilities, not MCP wire messages or SDK types. No MCP server, client, SDK, transport or CF-07 completion is a prerequisite for CF-05. Individual business adapters can ship as soon as their own existing gates pass.

CF-07 consumes each adapter only after that adapter is ready. Missing MCP support cannot make a working project read unavailable. Protocol conformance does not replace application-outcome, selected-project isolation or original regression qualification.

## Tracking

Parent roadmap: [#7](https://github.com/promptinvest/tg-ingest-agent/issues/7). Blocked by: [#1](https://github.com/promptinvest/tg-ingest-agent/issues/1), [#2](https://github.com/promptinvest/tg-ingest-agent/issues/2), [#4](https://github.com/promptinvest/tg-ingest-agent/issues/4). Blocks: [#6](https://github.com/promptinvest/tg-ingest-agent/issues/6); supplies qualified project reads to [CF-07/#8](https://github.com/promptinvest/tg-ingest-agent/issues/8) downstream. CF-05 is not blocked by CF-07. Track the partial-independent increments described above separately; no dependency implies new runtime permission.

## Development contract

This is proposed development work recorded on October 24, 2026. The owner requested a thorough review and a backlog for further development; implementation, new capabilities and production changes are not represented as completed or authorized by creating this issue. A later implementation request follows the owner's standing implementation-includes-deployment rule within its explicit scope and all existing release gates.

Read AGENTS.md, the full CLAUDE.md, CARA.md, SOLUTION.md, docs/adr/README.md and docs/backlog/cara-fleet-2026-10-24/README.md. Canonical repository: promptinvest/tg-ingest-agent, local projects/tg-ingest-agent; projects/DLingo is another application. Preserve working notes, reminders, calendar, journals, speech, research, memory, budgets, history, backups and confirmations. The existing research worker remains networkless; fleet credentials stay in its separate executor; model output never supplies executable addresses, paths or commands.

Use the existing budgeted LLM gateway for free-form interpretation and appropriate reply format. Zero host probes for catalog questions does not mean zero model calls. Do not introduce a required exact-phrase interface, another routing stage/provider/daemon, or a generic shell tool as an incidental remedy.

Update BOTH CARA.md and SOLUTION.md, relevant ADR/backlog status and operator docs in every implementing commit. Tests run in an isolated Linux/VPS stage, not the OneDrive checkout. Preserve full required Linux/CI, installed-source/runtime, sandbox-denial, stopped-data parity, backup, monitor/alert and guarded-release checks. No manual Telegram test sends, historical update/controller replay, live maintenance demonstration, unrelated paid work or change to another project's release rules.

Full handoff, source map, sanitized production evidence, bootstrap facts and semantic regression corpus:
https://github.com/promptinvest/tg-ingest-agent/tree/main/docs/backlog/cara-fleet-2026-10-24

## October 24 - Closed project-read contracts and dispatch adapter

CF-05 adds a separate versioned read scope with canonical project IDs and a closed
capability registry. Selected projects derive their full configured host union;
project-only reads collect only their root-mapped files, containers and components.
The executor discovers the scoped gateway contract before reading and reports a
capability gap on older gateways, with no broad legacy retry. Existing host/all
observations and the exact worker-restart card/wire retain their original meaning.

Release, application checks, containers, runtime components, verified backups and
development evidence stay distinct. Missing backup/development adapters return
not-configured; process activity does not establish project health or progress.
Thirty-two isolated Linux contract/dispatch cases pass. Runtime composition,
production gateway binding and full acceptance remain pending; CF-05 is partial.

## Accepted business baseline ? October 24

Source99037c5/buildf54906e304fe was accepted at10:46:31UTC after the full
Linux/CI, accounted semantic qualification, original data/backup/runtime/sandbox
and scoped-read gates. This is an incremental business baseline; no full package
completion is claimed. MCP remains disabled. [Exact evidence and remaining work](../../cara-fleet-increment-01-2026-10-24.md).
