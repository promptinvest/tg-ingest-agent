# CF-02 — Semantic questions and evidence selection

- Status: Implementation in progress; first business release accepted; full package acceptance remains open.
- Priority: P1.
- Depends on CF-01's facts contract; planning fixtures can start in parallel.
- Size: Medium; query contract/routing and full-dispatch qualification are separate increments.

## Problem and evidence

“Какие у меня VPS?” misses direct_view although “Какие VPS у меня?” matches. Provider, IP and project-location requests have no catalog mode. The fleet planner requires at least one live facet and asks for fresh follow-ups. A resource-only plan is forcibly expanded with project checks. The result may be factually supported and still answer none of the requested fields.

Sources: fleet.py:21-37,194-249,370-392; fleet_context.py:52-92; fleet_protocol.py:83-107. Existing test_fleet.py:63-76 covers narrow phrases and private endpoint exclusion;105-107 currently requires project observations on every investigation.

## Product requirement and contract

Use the budgeted LLM to interpret arbitrary RU/EN wording, context and requested format. Extend the existing closed router/planner contract rather than adding a mandatory second router stage. Deterministic shortcuts may remain optimizations.

A plan result is either a validated answer plan or an inert clarification. Clarification is a result kind, not an evidence mode, and submits zero host probes/effects.

A validated query plan must represent:
- One or more modes: catalog, runtime, history, development or action.
- Canonical host/project scope and its owner-bound explicit/inherited source.
- Requested fields or answer obligations for each requested entity.
- Time/freshness requirements and sources needed for those fields.
- Presentation intent: concise prose, list, table or requested diagnostics.
- Permitted existing tools/capabilities; unknown capability is a gap, never invented authority.

Recorded facts and runtime observations remain distinct. A mixed question may combine modes with separate evidence/tool requirements.

## Required behavior

- Catalog: answer IPs/providers/locations/roles/known project placement from CF-01. Submit zero host jobs, including when the executor is unavailable. A missing field yields a specific unknown/conflict, not a substitute fleet scan.
- Runtime: collect only evidence needed for the question, using project scope from CF-05 when installed. “Check now” requires fresh evidence. An explicitly cached result must keep its source timestamp and policy-defined age.
- History: use compatible dated observations; missing intervals stay missing. Two snapshots cannot establish sustained stability.
- Development: distinguish deployed, responding, under development and complete. Use connected repository/task/CI evidence only; a disconnected source yields a targeted gap.
- Action: invoke only installed reviewed runbooks under existing permissions. Free text/KB facts cannot authorize effects.
- Mixed request “Перечисли VPS и проверь Project Alpha”: local roster plus a Project-Alpha-only operational selection.
- Resolve “на этом сервере”, “по ним”, “а IP?” against a valid structured owner frame. Explicit new project overrides inherited scope. Unrelated owner tasks and edited/stale sources keep their existing invalidation behavior.
- “Какие проекты работают?” uses context to distinguish deployment, current operation and development. Ask a focused clarification only when unresolved ambiguity changes the answer.
- Preserve a distinction between host resource load and actual proxy/application load.

## Answer obligations

For every requested entity/field, the answer must contain the supported fact or a specific unknown/conflicting/unavailable status. CPU statistics cannot satisfy an IP request. An absent probe cannot imply an absent project or an exhaustive catalog. Required coverage must survive selection, formatting, cleanup and delivery.

Use request semantics to constrain hypotheses and additional reads. IP/provider queries do not acquire resource hypotheses merely because such evidence is present.

## Acceptance criteria

- Full-dispatch replay of the public synthetic reproductions in regression-cases.json; both Russian list word orders and held-out RU/EN paraphrases work.
- IP/provider/catalog cases submit zero host probes; budgeted LLM use is allowed.
- Provider list and requested table contain each requested field or its field-specific gap.
- target-host project listings use the relationship registry; no resource/process dump.
- A single named project does not expand to unrelated applications sharing its host.
- Corrections/reformatting retain owner intent and legitimate scope without redundant reads.
- Static+live mixed requests invoke only the relevant live capabilities.
- Explicit freshness, stale cache, conflict, no model, no executor and ambiguous scope produce relevant truthful outcomes.
- Untrusted notes/results cannot switch tools, add targets or grant actions.
- Existing reminder/calendar/note/budget/confirmation routes remain intact; exact matching is not the supported language interface.

## Implementation and tests

Inspect router.py, fleet.py, fleet_context.py, fleet_protocol.py, skill_manifest.py and relevant tests. Replace the blanket every-question project-facet requirement with a request-driven evidence contract; revise its test explicitly while preserving real release/health coverage where asked. Keep private transport rejection intact.

Add full-dispatch tests asserting both answer obligations and selected tools/hosts/projects/facets, not just successful route names. Use the paired synthetic fixtures and source cases in regression-cases.json. CF-03 owns presentation/semantic output checks; CF-04 owns asynchronous execution. CF-02 owns validated project selection and answer filtering; CF-05 owns project enforcement through the executor/gateway. Their end-to-end collection-isolation tests are an integration gate after CF-05, not a circular prerequisite to completing CF-02's catalog/planning contract. Interim releases retain supported legacy reads without claiming collection-level project isolation or broadening authority.

Out of scope: new provider/model, generic shell, unrelated router redesign and direct project maintenance.

## Tracking

Parent roadmap: [#7](https://github.com/promptinvest/tg-ingest-agent/issues/7). Blocked by: [#1](https://github.com/promptinvest/tg-ingest-agent/issues/1). Blocks: [#3](https://github.com/promptinvest/tg-ingest-agent/issues/3), [#5](https://github.com/promptinvest/tg-ingest-agent/issues/5). Track the partial-independent increments described above separately; no dependency implies new runtime permission.

## Development contract

This is proposed development work recorded on October 24, 2026. The owner requested a thorough review and a backlog for further development; implementation, new capabilities and production changes are not represented as completed or authorized by creating this issue. A later implementation request follows the owner's standing implementation-includes-deployment rule within its explicit scope and all existing release gates.

Read AGENTS.md, the full CLAUDE.md, CARA.md, SOLUTION.md, docs/adr/README.md and docs/backlog/cara-fleet-2026-10-24/README.md. Canonical repository: promptinvest/tg-ingest-agent, local projects/tg-ingest-agent; projects/DLingo is another application. Preserve working notes, reminders, calendar, journals, speech, research, memory, budgets, history, backups and confirmations. The existing research worker remains networkless; fleet credentials stay in its separate executor; model output never supplies executable addresses, paths or commands.

Use the existing budgeted LLM gateway for free-form interpretation and appropriate reply format. Zero host probes for catalog questions does not mean zero model calls. Do not introduce a required exact-phrase interface, another routing stage/provider/daemon, or a generic shell tool as an incidental remedy.

Update BOTH CARA.md and SOLUTION.md, relevant ADR/backlog status and operator docs in every implementing commit. Tests run in an isolated Linux/VPS stage, not the OneDrive checkout. Preserve full required Linux/CI, installed-source/runtime, sandbox-denial, stopped-data parity, backup, monitor/alert and guarded-release checks. No manual Telegram test sends, historical update/controller replay, live maintenance demonstration, unrelated paid work or change to another project's release rules.

Full handoff, source map, sanitized production evidence, bootstrap facts and semantic regression corpus:
https://github.com/promptinvest/tg-ingest-agent/tree/main/docs/backlog/cara-fleet-2026-10-24

## October 24 - Descriptive query and answer obligations

The shared CF-02 catalog query contract lets the existing planner select canonical
subjects, requested fields, detail and prose/list/table/names presentation. Code
resolves inherited/all scope and placement relationships, renders every requested
cell from validated facts, and preserves conflicts, specific unknowns, completeness
and evidence dates. Model prose cannot supply a fact or execution authority.
Optional no-model shortcuts provide question-aligned fallback during outages.

Thirty-four isolated Linux query cases pass, including frozen obligations from
17 catalog specifications in the unchanged original corpus. This is contract
qualification, not a claim that free-form model behavior or all 47+15 scenarios pass.
Full-dispatch integration and actual-model/release acceptance remain separate gates.
Integrated business candidate: local obligations/semantic formats, durable nonblocking phases, queue starts/cancellation, shared lookup and typed content lifecycle are implemented. Focused Linux evidence and remaining model/release gates are in [the increment record](../../cara-fleet-increment-01-2026-10-24.md). No package is complete or deployed yet.


## Accepted business baseline ? October 24

Source99037c5/buildf54906e304fe was accepted at10:46:31UTC after the full
Linux/CI, accounted semantic qualification, original data/backup/runtime/sandbox
and scoped-read gates. This is an incremental business baseline; no full package
completion is claimed. MCP remains disabled. [Exact evidence and remaining work](../../cara-fleet-increment-01-2026-10-24.md).

## Increment 03 project-read checkpoint

Candidate plans consume existing observer/route and reviewed public-development
evidence. Runtime summary meaning is explicit; proxy connection metrics cannot be
replaced by host CPU, and CI/issue state cannot establish completion. Local catalog
obligations and selected-project enforcement remain independent of MCP.

Project-only focused Linux 201 passes in 0.622 s. Fresh bounded RU/EN/paraphrase/context/
correction and final requested-answer qualification, exact full/CI and protected
real bindings/release gates remain pending. Original47+15 specifications are
unchanged. No CF-02 completion. [Increment 03](../../cara-fleet-increment-03-2026-10-24.md).
