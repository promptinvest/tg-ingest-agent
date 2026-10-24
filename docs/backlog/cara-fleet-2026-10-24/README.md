# Cara fleet and project development backlog — October 24, 2026

Status: Implementation authorized October 24, 2026; work in progress. No package is complete.
Parent tracker: [CF-00 / issue #7](https://github.com/promptinvest/tg-ingest-agent/issues/7).

## Start here

This directory is the durable developer handoff for the October 24 review of Cara's fleet knowledge, question handling, reply style, responsiveness and architecture for future direct project work. The chat is not required. Read this index, the relevant complete package spec and its linked source/tests before implementing.

Canonical code repository: promptinvest/tg-ingest-agent; use its independent checkout, not the Daily Lingo application or the archived mixed workspace. Read [AGENTS.md](../../../AGENTS.md), the full [CLAUDE.md](../../../CLAUDE.md), [CARA.md](../../../CARA.md), [SOLUTION.md](../../../SOLUTION.md) and [ADR backlog](../../adr/README.md).

The review baseline was documentation commit afacec85602aa5c7a6d141201f3e2d78c698b8f9 and runtime source e925af390f52d4efa973619c4ab8e6b6320af8c1. These identify historical inspected source, not an instruction to reset or redeploy it. Inspect current source/instructions and preserve unrelated work before implementation.

## Owner requirements and authorization

The owner reported repeated remote access for stable KB facts, overly technical replies, and a poor answer about projects on a server, and requested a thorough architecture review before expanding capabilities.

The owner explicitly clarified: Cara can use her LLM for free-form queries to understand the required reply format. Therefore:
- Use the existing budgeted LLM for semantic interpretation and appropriate presentation.
- Do not make exact regex phrases or fixed templates the supported interaction model.
- Known descriptive facts require zero host probes; that does not forbid LLM calls.
- Keep grounding, scope, freshness and effect permissions enforced independently from natural-language freedom.
- Preserve working behavior outside the approved change.

The current request is to create a backlog for further development. All seven packages remain Proposed. It does not itself start implementation, grant a new runbook/account connection, create schedules, authorize paid evaluation/corpus work or deploy software. When the owner later requests implementation, the standing implementation-includes-deployment rule applies within that scope, with all original checks; do not invent a separate hash/window approval requirement.

No missing product decision blocks offline work on these specifications. Routine module/interface choices belong to the developer under these invariants. A materially broader behavior or authority change follows the owner's existing approval rule. Operator secrets/access and independently verified real deployment inputs remain outside public source control.

## Business goals and preservation gate

The backlog exists to help the owner understand and manage his fleet and projects through ordinary conversation. The owner explicitly requested that adding MCP preserve the existing business purposes and goals. MCP is a downstream integration serving those outcomes.

| Owner outcome | Required acceptance |
|---|---|
| Get known facts without unnecessary checks | Address/provider/location/project-placement answers use published facts with zero host inspections, including when MCP or the executor is unavailable. Unknowns remain specific. |
| Ask naturally | The existing budgeted LLM interprets RU/EN wording, context and requested format; exact phrases and fixed templates remain optional shortcuts. |
| Receive useful project information | Requested fields and project outcomes are answered with relevant evidence, exceptions and uncertainty; service/process output cannot substitute for the answer. |
| Control detail and corrections | Confirmed preferences apply across explanatory paths; names, short/detailed/table requests and legitimate correction scope survive every integration path. |
| Keep using Cara during work | Owner updates and reminders remain responsive during slow models, tools, queues and backend outages. |
| Manage individual projects safely | Deployment, current operation and development progress remain distinct; reads stay within selected projects and installed capabilities; future actions retain reviewed authority/recovery. |
| Retain working capabilities | Notes, reminders, calendar, journals, speech, research, memory, budgets, history, backups and confirmations preserve behavior outside explicitly approved changes. |

CF-01–CF-06 keep their original priorities, dependencies, scope and definitions of done. None depends on CF-07, MCP availability or protocol adoption. Their fixes may be implemented, qualified and released without MCP. Report those business milestones separately from interface readiness.

Preserve the original 47 regression specifications. Run applicable cases through internal and MCP paths and keep unrelated baseline cases passing. Add protocol/failure cases separately; successful discovery, connection or tool execution cannot close a business requirement whose requested answer or authorized outcome is wrong.

## Work packages

| ID / tracker | Priority | Scope and complete specification | Dependencies | Implementation size |
|---|---|---|---|---|
| [CF-01 / #1](https://github.com/promptinvest/tg-ingest-agent/issues/1) | P1 | [Sanitized facts and local-to-Cara synchronization](01-fleet-facts-and-sync.md) | none | Large; schema/bootstrap and publisher increments |
| [CF-02 / #2](https://github.com/promptinvest/tg-ingest-agent/issues/2) | P1 | [Semantic modes, evidence selection and answer obligations](02-semantic-queries-and-evidence.md) | CF-01 | Medium |
| [CF-03 / #3](https://github.com/promptinvest/tg-ingest-agent/issues/3) | P1 | [Adaptive grounded answers, corrections and output cleanup](03-adaptive-grounded-replies.md) | CF-02; cleanup can start independently | Medium |
| [CF-04 / #4](https://github.com/promptinvest/tg-ingest-agent/issues/4) | P1 | [Responsive model work, completion handling and queue lifecycle](04-responsive-durable-execution.md) | none; coordinate CF-02/03 | Large; separate wakeup/inference/queue increments |
| [CF-05 / #5](https://github.com/promptinvest/tg-ingest-agent/issues/5) | P1 | [Project-scoped read adapters and operational meaning](05-project-read-capabilities.md) | CF-01/02/04 | Large; one read adapter per increment |
| [CF-06 / #6](https://github.com/promptinvest/tg-ingest-agent/issues/6) | P2; gate before broader writes | [Shared capability/effect and content lifecycle](06-governed-capabilities-and-lifecycle.md) | CF-04/05; purge fixtures can start independently | Large; staged compatibility migration |
| [CF-07 / #8](https://github.com/promptinvest/tg-ingest-agent/issues/8) | P2 downstream integration | [Read-only MCP interface and Cara client](07-mcp-fleet-interface-and-client.md) | design now; facts/client after CF-01–04; project reads after relevant CF-05 adapters; CF-06 gates future writes | Staged adapter/client increments |

Top immediate outcomes: CF-01 supplies missing known facts; CF-02 prevents unnecessary investigations and wrong answers; CF-03 makes replies useful and preserves names. CF-04 runs in parallel because polling/model latency affects every later integration.

Suggested sequence:
1. CF-01 contract/bootstrap and CF-04 completion responsiveness in parallel; isolate the CF-03 cleanup regression.
2. Integrate CF-02 semantic plans with CF-01 facts; CF-03 consumes those obligations and CF-04's nonblocking inference path.
3. Stabilize project IDs/read protocol and deliver CF-05 adapters individually. CF-02 validates project selection/answer filtering independently; end-to-end collection isolation is a combined CF-02/05 integration gate.
4. Complete CF-06 shared lifecycle and effect-readiness gates before adding new project writes.
5. Design CF-07 contracts in parallel without delaying these packages; deliver its read-only facts/client path after CF-01–04, and expose relevant CF-05 reads as ready. MCP never gates the original business milestones. A2A and wider clients/transports remain future scoped work.

Keep increments independently reviewable, roughly one to three development days where feasible. These are planning sizes, not delivery promises. Do not implement a whole-runtime rewrite to complete one package.

## Review findings and design direction

| Finding | Source-level evidence at review baseline | Owner impact | Package |
|---|---|---|---|
| F01 | fleet.py:21-37 only recognizes narrow list/identity forms;194-249 requires operational facets; fleet_protocol.py:47-80 lacks general descriptive metadata | Known facts lead to remote checks and still remain unanswered | CF-01/02 |
| F02 | fleet_report.py:184-225 validates evidence selection without requested-field obligations | Truthful diagnostics can be irrelevant to the question | CF-02/03 |
| F03 | fleet_report.py:228-252 prints all findings plus repeated highlights/hypotheses; fleet path bypasses ordinary preferences | Technical, repetitive replies | CF-03 |
| F04 | converse_svc.py:25-35 deletes inner asterisk spans from bold content | Project names vanish into empty emphasis | CF-03 |
| F05 | tg_ingest_agent.py:1007 ignores pending fleet work; fleet.py:371/550 performs synchronous model calls | Avoidable waiting and blocked owner/reminder work | CF-04 |
| F06 | fleet_protocol.py:104-107 omits project scope from inspect; gateway/report loop over all host projects | Colocated projects leak into unrelated questions | CF-05 |
| F07 | queue timeout lacks start distinction; generic broker/purge omit fleet integration | Scalability, recovery and content-lifecycle gaps | CF-04/06 |

[review-evidence.json](review-evidence.json) retains a public-safe baseline and reproductions. Production samples are not a long-term latency percentile. Typed evidence and restricted execution from the accepted release are valuable and remain requirements.

## Required architecture boundaries

- Extend the existing fleet skill and shared response layer. Add small cohesive modules only where they clarify responsibility. Keep the credential-owning executor separate; the research worker stays networkless.
- Descriptive facts, observations, development evidence and actions are separate contracts. Display addresses never become execution targets.
- Let the LLM understand what to answer and choose useful wording/format. Validate requested fields, factual support, scope, certainty and final output. Existing citation IDs alone do not prove semantic entailment.
- Preserve canonical owner-bound context, source edits/staleness, explicit new targets and unrelated-task scope termination. Assistant narrative is not scope authority.
- Keep observations' sources, dates/windows and availability. Not-configured, unavailable, not-found, expected idle and actual failure have distinct meanings.
- Preserve complete requested coverage and retained evidence; ordinary replies do not have to expose every observation.
- Keep current paid-call limits/reservations, exact confirmation cards, source/inventory/fingerprint bindings, single-use effect tokens and conservative uncertain outcomes.
- No new public listener, arbitrary shell/log/environment access, second model provider, replacement fleet monitor or broad account permission is part of this backlog.
- Existing application-specific release rules and containment remain authoritative for future adapters.

ADR-0032's evidence/scope safeguards stay intact. CF-03 deliberately proposes changing its mandatory diagnostic presentation, not declaring that change already deployed. Existing historical ADRs/releases must remain unmodified evidence.

## Fixtures and verification

- [bootstrap-facts.json](bootstrap-facts.json): explicitly synthetic proposed facts shape, example unknowns, distinct address types, project colocation and lifecycle exclusions. Documentation-only addresses; never install as real config.
- [regression-cases.json](regression-cases.json):47 machine-readable test specifications. They are not an implemented runner or a claim of passing tests.
- [mcp-regression-cases.json](mcp-regression-cases.json): 15 additional MCP interface/business-parity specifications. They extend, and do not replace, the original 47.
- [review-evidence.json](review-evidence.json): code baseline and failure-to-package mapping without fleet identity data.

The original corpus covers flexible RU/EN catalog wording, address/provider tables, host/project context, static-plus-live requests, unavailable sources, stale/conflicting evidence, application-versus-process health, backup receipts, expected idle units, corrections/preferences, emphasis cleanup, false cited claims, async responsiveness, queue deadlines, recovery and purge.

Assert semantic obligations and tool selection, not one exact sentence. Use frozen facts/observations/model outputs for deterministic regressions, fake clocks and synchronization for scheduling. Add held-out paraphrases and bounded accounted actual-model qualification for model behavior during an authorized implementation. Mocks and “no raw JSON” checks alone do not qualify natural replies.

Relevant suites: test_fleet.py, test_fleet_report.py, test_assistant.py, test_tasking.py, test_task_runtime.py, test_gateway.py and test_backup.py.

Current required checks are defined by CLAUDE.md, deploy.sh, verify-cara-runtime.sh and .github/workflows/test.yml. At this review baseline:
- CI compiles Python, runs pinned Ruff 0.17.1 F821/F822/F823, normal and seed 20261007 shuffled run_tests.py, shell syntax and shellcheck.
- Linux test stage uses the service distro interpreter and optional package behavior that CI alone does not cover. Use deploy.sh --test for tests only when appropriate to the authorized task.
- Tests must stay off the OneDrive-synced working checkout. Do not install/restart to run a unit fixture.
- Release gates include current exact-source checks, runtime/sandbox/spool denial, original data parity, recoverable backups and preservation of other applications/monitors/alerts.
- Do not relax tests to bless an incidental regression. Explicitly replace old tests that enforce blanket project probes or diagnostic presentation only as part of the specified approved behavior change, retaining their underlying safety coverage.

Documentation-only backlog publication needs link/JSON/scope/content validation; it does not justify runtime changes or a maintenance demonstration.

## Data handling and operator handoff

This public repository contains only code-level requirements and synthetic examples. Actual fleet addresses, topology, source-specific identity/lifecycle evidence, private transcripts, credentials and deployment recovery details are not in these files or GitHub issues.

The owner's local operator supplement preserves exact review evidence and source/bootstrap notes privately. Its stable handoff ID is cara-fleet-backlog-20261024 under the operator-material root documented in CLAUDE.md; its README indexes the evidence and bootstrap files. It supplies installation-specific values, not additional execution authority. Public developers can implement and verify every offline contract here without that supplement; authenticated production binding follows the established operator process.

Never publish private operational inputs in issue comments, PR descriptions, logs or fixture refreshes. Do not read credential files to implement a data contract or test. Source-derived facts must be allowlisted, dated and reviewed before real publication to Cara.

## Status and completion procedure

Canonical specification files live here; GitHub issues track discussion and completion. Update both when scope/criteria change. For each package record implementation commit, test evidence, known limits, deployed/accepted status where applicable and remaining work separately.

Update CARA.md, SOLUTION.md, the ADR status/index and operator runbook in every implementing commit. Commit and publish repository changes through this repository's existing workflow; do not use the archived mixed checkout.

Do not mark an issue implemented because its fixtures or documentation exist. Close it only after required behavior, regressions, compatibility and release acceptance are satisfied. Keep CF-00 open until all required packages are complete; any owner-approved deferral must be explicit and linked.

## Implementation progress — October 24

Owner request: implement the backlog, test, publish and deploy through existing
gates. The standing implementation-includes-deployment rule applies; no separate
hash-specific approval is claimed. Planning-era authorization statements above
remain historical descriptions of the backlog-creation request.

Increment 01 isolates CF-03 content-preserving emphasis cleanup and CF-04 fleet
completion polling. CF-01 facts contract/export work proceeds independently.
[Increment evidence and remaining work](../../cara-fleet-increment-01-2026-10-24.md).
Neither unit fixtures nor protocol checks close any package; the preserved 47+15
specifications retain all original assertions and independent acceptance criteria.
CF-01 contract/publisher: 53 schema/history and 17 exporter/publication Linux cases pass. Reviewed source-hash export and root-only sanitized installation are implemented; real publication, synchronization operations and full acceptance remain pending. No package is complete.

Integrated business candidate: local obligations/semantic formats, durable nonblocking phases, queue starts/cancellation, shared lookup and typed content lifecycle are implemented. Focused Linux evidence and remaining model/release gates are in [the increment record](../../cara-fleet-increment-01-2026-10-24.md). No package is complete or deployed yet.


Accepted first integrated business release: source99037c5/buildf54906e304fe,
10:46:31UTC. Known-fact obligations, responsive durable phases, grounded replies,
scoped reads and lifecycle coverage are live. All original release/preservation
gates pass; retained operator failures/continuations and exact evidence are in the
[increment record](../../cara-fleet-increment-01-2026-10-24.md). MCP remains disabled.
No package is complete; independent project/shared-task/client increments continue.
