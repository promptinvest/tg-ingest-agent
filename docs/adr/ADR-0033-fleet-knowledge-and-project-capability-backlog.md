# ADR-0033: Fleet knowledge and project capability development backlog

- Status: Accepted for implementation October 24, 2026; packages in progress.
- Owner request: create a backlog for further development after a fleet behavior/architecture review.
- Implementation authorization: no implementation is claimed by this planning record.

## Context and direction

The review identified missing descriptive fleet facts/synchronization, inspection-only query handling, rigid technical reports, content-erasing output cleanup, poll/model responsiveness gaps and incomplete project/task lifecycle boundaries.

The owner explicitly wants Cara to use the existing LLM for free-form question interpretation and an appropriate reply format. Known facts should not require host probes. The proposed architecture retains closed tools, grounded evidence, owner-bound scope, budget/approval/recovery rules and existing behavior outside approved changes.

Extend the existing fleet skill with a sanitized facts view, semantic query/answer contracts, adaptive grounded presentation, responsive durable I/O and project-scoped read adapters. Integrate task/effect lifecycle before broader project writes. No new daemon, provider or generic shell tool is a prerequisite.

## Backlog and consequences

[Complete developer handoff](../backlog/cara-fleet-2026-10-24/README.md);
[parent tracker CF-00](https://github.com/promptinvest/tg-ingest-agent/issues/7).
The handoff contains the original six business package specs plus downstream CF-07, dependency/release requirements, the preserved 47 business regression specifications and 15 additional MCP/parity specifications.

This record leaves ADR-0031/0032 and historical release claims intact. CF-03 proposes revising exhaustive report presentation while preserving ADR-0032's factual/scope/uncertainty safeguards. Each implementing change must separately record its accepted decision and update both primary specifications.

Public files contain no real fleet bindings or private operational evidence; production inputs remain operator-owned. Creating this backlog changes neither runtime nor permissions.

## MCP extension and preservation of business goals

The owner requested adding MCP planning while preserving the backlog business purposes and goals. CF-07 adds a thin read-only interface and explicit Cara client over shared capabilities. CF-01–CF-06 retain their original priorities, acceptance criteria and independent release gates; none depends on MCP. CF-05/06 expose reusable capability/policy contracts without adding protocol prerequisites. A2A and new write interfaces remain future scoped work.

## Resolves and dependencies

Tracks review findings F01-F07 through CF-01..CF-06; CF-07 consumes their qualified outcomes. Dependencies and independently startable increments are in the handoff. It supplements ADR-0031/0032 and coordinates with the existing responsiveness, routing, test and documentation backlog.

## Status log

- 2026-10-24: owner requested durable development backlog; proposed work, fixtures and trackers prepared. No runtime implementation, paid qualification or deployment.
- 2026-10-24: owner requested the MCP backlog extension with explicit preservation of existing business goals; CF-07 and additional parity scenarios added. Planning only.

- 2026-10-24: owner requested implementing, testing, publishing and deploying the complete backlog. First independent cleanup/completion-poll increments begin; no package completion or release is inferred. Existing authority and compatibility gates remain.

- 2026-10-24: CF-01 descriptive contract and reviewed source-hash exporter implemented, with separate root-only sealed-export installation and retained history. Seventy focused Linux cases pass; production publication and full acceptance remain pending. Facts confer no execution authority.

- 2026-10-24: shared LLM preparation/transport/settlement supports a bounded owner-thread profile pump; one background slot preserves foreground capacity. Eighteen Linux cases pass. Durable integration/release acceptance remain pending; no increased model or budget authority.

- 2026-10-24: scoped project reads use a separate additive protocol/capability contract. Project-only collection cannot use a broad fallback; code derives multi-host coverage and preserves scope on extra reads. Thirty-two Linux cases pass; gateway rollout and application-specific adapters remain unaccepted.

- 2026-10-24: descriptive answer obligations and presentation share one validated query contract. Model plans choose fields/format; typed facts own every cell and no factual model prose is admitted. Thirty-four Linux cases pass; semantic model/full-dispatch acceptance remains separate.

- 2026-10-24: offline facts-only MCP adapter/client preserves shared cells/provenance and trusted caller scope;27 Linux cases pass. It is neither installed/enabled nor a durable/project interface. CF-01–06 still have independent gates; A2A and writes remain excluded.

- 2026-10-24: integrated local catalog/semantic formats and question-aligned rendering, owner-thread durable inference, source-bound executor queue/start/cancellation, shared read-only lookup and typed fleet content lifecycle. ADR-0032's factual/scope/evidence safeguards remain; its exhaustive ordinary presentation is replaced only in the new concise path, with explicit full detail retained. Exact combined release/actual-model gates remain pending; no package completion.


- 2026-10-24T10:46:31UTC: first combined business release accepted, source99037c5,
  buildf54906e304fe. Descriptive obligations, async durable phases/queue, scoped
  reads, adaptive presentation, shared lookup and typed fleet lifecycle are live.
  Six gateway source/policy-preservation and all original app/data/backup/runtime
  gates pass. Retained operator failures required fresh guarded continuations;
  no completed controller/installer/gateway replay. MCP remains disabled and no
  package-completion claim is made. See the increment release record.

- 2026-10-24: opt-in typed application/backup/development observations preserve
  fixed-resource authority, implementation discovery, provenance and explicit
  source gaps. A manual read-only maintenance-job publisher provides backup
  evidence without starting operations. Legacy protocol limits remain exact;
  authenticated binding and complete release acceptance are pending.

- 2026-10-24: retained investigations reuse existing fleet/task rows, durable
  original-budget reservations and one task receipt/delivery. Source/authority,
  cancellation, unknown-paid and purge fences remain. Optional MCP facts uses a
  bounded asynchronous slot with equivalent internal recovery. Shared retained
  reads remain read-only at the API; no OS-level DB-write denial is inferred.
  Full release/model gates and MCP creation/lifecycle qualification remain open.

- 2026-10-24: actual task-plan qualification rejected an invented live catalog field and invalid literal binding. Fleet-only tool descriptions now expose exact closed input meaning; validation is unchanged. Test fixtures explicitly isolate installed facts; runtime defaults remain.


## Increment 03 typed project-read decision and checkpoint

Keep monitor parsing pure and fixed privileged file I/O separate. Additive observer/
route implementations consume existing evidence without starting its producer.
The manual public-GitHub export/install path preserves Actions source-update dates
without inventing completion; protected atomic publication retains last good data.
Detailed tables keep full evidence. Old v1 payloads and only explicitly qualified
historical append-only read contracts remain compatible.

Project-only focused Linux 201 passes in 0.622 s, including 32 development-publisher,
46 pure-monitor and 19 integration cases within that total. It is not a full frozen
release receipt. Freeze and qualify the project source, actual fixed
bindings/service identity and all original release/preservation gates separately.
Source/archive/operator/window are UNSELECTED; no package is complete.

Durable MCP/client-intent/cancellation and the completed15-case runner remain
UNCOMMITTED parallel scope for a separate increment. CF-01 through CF-06 keep their own
criteria and no MCP prerequisite. See [increment 03](../cara-fleet-increment-03-2026-10-24.md).

## Increment 04 durable client/read decision and checkpoint

Persist the optional client's exact source/key/input/principal/policy/format intent
before first stdio in the existing fleet ledger, not a new outbox. Prepared work is
inert until matching server promotion; source expiry/edits, policy, capacity,
cancellation, typed purge and checked restore share existing authority/accounting.
Base start/status/result/cancel retain one handle and useful answer. Running
cancellation waits for settlement; lost started proof is unknown and never replayed.
Tasks is explicitly unsupported. New protected project effects remain excluded.

All 47 original and 15 additional source IDs are executable without lifecycle
skips. Focused Linux 227 passes in 14.395 s, including 16 client-intent cases;
17 pure operator/orchestration fixtures pass in 0.017 s as unbound-draft evidence.
The lossless task-page/managed-artifact adapter preserves exact answers and
original limits. Its frozen 45-test suite, including ten new actual pipeline
regressions, passes isolated Linux in 22.201 s; failed originals remain.
Final frozen-source full/model/CI and actual identity/policy/readiness remain open.
Source/archive/operator/window: UNSELECTED; no package completion or deployment.

[Increment 04](../cara-fleet-increment-04-2026-10-24.md) records independent criteria
and release gates. CF-01 through CF-06 retain no MCP prerequisite, while CF-07 uses
their shared policy/evidence/lifecycle rather than creating competing authority.
