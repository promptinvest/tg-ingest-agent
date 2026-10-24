# CF-03 — Adaptive grounded replies, corrections and safe cleanup

- Status: Implementation in progress; first business release accepted; full package acceptance remains open.
- Priority: P1.
- Depends on CF-02's answer contract; the sanitizer fix and shared style review can start independently.
- Size: Medium; cleanup, response contract and real-model qualification are separate increments.

## Owner intent and verified failures

The owner explicitly said Cara can use her LLM for free-form queries to understand the required reply format. Do not substitute fixed templates or exact phrases for that ability.

Postacceptance owner questions about projects/providers/IPs received diagnostics. A confirmed preference “Отвечай прямо на вопрос, без длинных справок” was stored before the provider/IP failures. After a correction, a project-answer sentence lost its emphasized project name and retained an empty asterisk placeholder. The missing name is a deterministic cleanup defect; the unsupported word “only” is a separate grounding/completeness defect.

See review-evidence.json for public-safe reproduction context and regression-cases.json for synthetic fixtures. Exact production references stay in the private operator supplement; no production transcript is required to implement this contract.

## Causes and source map

fleet_report.py:228-252 prints all coverage findings, then repeated highlights/hypotheses and a universal disclaimer. Its model contract:222-225 selects IDs only. The fleet composition path at fleet.py:550-571 bypasses normal preference/persona handling. skill_manifest.py:74-78 lacks the persona context marker, but adding the marker alone does not wire those preferences into this path.

converse_svc.py:25-35 removes every asterisk-delimited span as roleplay; the inner span of a bold name is erased. Full cleanup:488-525 accepts a remaining fluent sentence. This was already documented in docs/cara-fleet-answerfix-2026-10-23.md:12-17 and bypassed only for narrow direct views.

fleet.py:40-53,91-96 supplies host names/roles to conversation and guards only narrow inventory contexts. General correction replies can lose authoritative project grounding.

## Required design

1. Supply the LLM with original/latest question, CF-02 obligations and scope, typed facts/observations, source dates/windows, relevant confirmed preferences and requested detail.
2. Let it choose short prose, lists, a table or an expanded explanation. Default to the direct answer and material exceptions. Explicit requests for detail override default brevity for that turn.
3. Use one shared explanatory-answer policy across fleet, grounded KB answers, conversation and summaries. Avoid adding an LLM call to every transactional acknowledgement or changing numbered lists/cards/receipts whose structure matters.
4. Keep complete evidence and requested coverage internally. Ordinary output need not enumerate every observation or service. Explicit all-item requests retain every requested item; an overview may account for the whole scope with concise coverage plus exceptions.
5. Keep typed evidence, owner-bound scope, unknown/not-configured distinctions, dates/windows, budgets and durable per-page delivery from ADR-0032. Document the proposed change to its exhaustive presentation contract rather than rewriting accepted history.
6. Demonstrate prevention/detection of unsupported semantic claims. Merely attaching an existing evidence ID to free prose is insufficient: CPU cannot justify backup success, active services cannot prove user journeys, and absent probes cannot prove project absence.
7. Use a concise question-aligned fallback on model/validation failure. An IP lookup failure cannot fall back to a full host health report.
8. Constrain hypotheses to relevant diagnostic questions as well as valid prerequisites. Keep technical detail available on request, without automatic PIDs, hashes, every unit/container or broad irrelevant disclaimers.

## Corrections and cleanup

“Не так отвечаешь на вопрос”, “That's not what I asked”, “just the names”, “make it shorter” and a requested table retain the original factual request and valid owner scope. Rewrite/reinterpret from available facts before collecting anything else. Prior assistant inventions cannot become authority; unrelated owner tasks end scope as before.

Preserve meaningful names/content in ordinary emphasis, including bold and single-asterisk emphasis. Continue suppressing actual narrated gestures/roleplay through a content-preserving output strategy. Validate the final sent text, after cleanup. Do not simply disable all output safeguards or delete all asterisk spans.

## Acceptance criteria

- Public synthetic full-dispatch reproductions and held-out RU/EN paraphrases answer requested fields with supported facts or precise gaps.
- The saved brevity preference affects fleet and other explanatory paths. Explicit detail/table requests work without requiring a memorized command.
- Project/provider/IP answers contain required entities, not unrelated resource statistics.
- A scoped Project Alpha response excludes unrelated Project Beta observations.
- Names in “**Project Alpha**” and “**Project Beta**” survive full cleanup. Mixed meaningful emphasis and each existing narrated-gesture example preserves names and ordinary answer text while suppressing gestures.
- Empty emphasis placeholders, missing required entities and unsupported “only project” claims fail validation.
- Corrections retain subject/obligations, do not repeat a full diagnostic report and do not automatically re-probe.
- Grounding counterfactuals reject backup success from CPU, whole-application health from active units, absence from missing probes and stability from short samples.
- Requested full reports still retain complete scope and durable page receipts.
- Confirmation cards, callback bindings, item numbering, transactions, source edits, budgets and ambiguous-delivery no-replay behavior remain intact.

## Tests and qualification

Extend test_fleet.py, test_fleet_report.py and test_assistant.py; add focused shared-policy tests if a module is extracted. Existing page-length/no-JSON assertions alone do not establish useful answers.

Use frozen model completions for deterministic regressions. A later implementation's required bounded accounted real-model qualification must assess paraphrases, format, brevity, factual entailment and omissions; mocks alone do not qualify model behavior. Record supported rates/cases and retained failures without claiming universal natural-language correctness. Authorized operator qualification may replay the exact private exchanges against isolated frozen inputs when available, without sending messages; private inputs are not prerequisites for public offline implementation.

If operative persona wording changes, edit converse.CHARACTER and hermes.PERSONA and mirror prompts/cara_persona.md; editing that descriptive Markdown alone changes no runtime behavior.

Out of scope: changing Cara's relationship/personality register, transactional confirmation semantics, new model/provider or weakened evidence/authority constraints.

## Tracking

Parent roadmap: [#7](https://github.com/promptinvest/tg-ingest-agent/issues/7). Blocked by: [#2](https://github.com/promptinvest/tg-ingest-agent/issues/2). Blocks: none. Track the partial-independent increments described above separately; no dependency implies new runtime permission.

## Development contract

This is proposed development work recorded on October 24, 2026. The owner requested a thorough review and a backlog for further development; implementation, new capabilities and production changes are not represented as completed or authorized by creating this issue. A later implementation request follows the owner's standing implementation-includes-deployment rule within its explicit scope and all existing release gates.

Read AGENTS.md, the full CLAUDE.md, CARA.md, SOLUTION.md, docs/adr/README.md and docs/backlog/cara-fleet-2026-10-24/README.md. Canonical repository: promptinvest/tg-ingest-agent, local projects/tg-ingest-agent; projects/DLingo is another application. Preserve working notes, reminders, calendar, journals, speech, research, memory, budgets, history, backups and confirmations. The existing research worker remains networkless; fleet credentials stay in its separate executor; model output never supplies executable addresses, paths or commands.

Use the existing budgeted LLM gateway for free-form interpretation and appropriate reply format. Zero host probes for catalog questions does not mean zero model calls. Do not introduce a required exact-phrase interface, another routing stage/provider/daemon, or a generic shell tool as an incidental remedy.

Update BOTH CARA.md and SOLUTION.md, relevant ADR/backlog status and operator docs in every implementing commit. Tests run in an isolated Linux/VPS stage, not the OneDrive checkout. Preserve full required Linux/CI, installed-source/runtime, sandbox-denial, stopped-data parity, backup, monitor/alert and guarded-release checks. No manual Telegram test sends, historical update/controller replay, live maintenance demonstration, unrelated paid work or change to another project's release rules.

Full handoff, source map, sanitized production evidence, bootstrap facts and semantic regression corpus:
https://github.com/promptinvest/tg-ingest-agent/tree/main/docs/backlog/cara-fleet-2026-10-24


## Implementation progress — October 24

CF-03 cleanup increment in progress; adaptive replies and qualification remain.
See [increment 01](../../cara-fleet-increment-01-2026-10-24.md). Original acceptance criteria remain required.

Integrated business candidate: local obligations/semantic formats, durable nonblocking phases, queue starts/cancellation, shared lookup and typed content lifecycle are implemented. Focused Linux evidence and remaining model/release gates are in [the increment record](../../cara-fleet-increment-01-2026-10-24.md). No package is complete or deployed yet.


## Accepted business baseline ? October 24

Source99037c5/buildf54906e304fe was accepted at10:46:31UTC after the full
Linux/CI, accounted semantic qualification, original data/backup/runtime/sandbox
and scoped-read gates. This is an incremental business baseline; no full package
completion is claimed. MCP remains disabled. [Exact evidence and remaining work](../../cara-fleet-increment-01-2026-10-24.md).

## Increment 03 project-answer checkpoint

Candidate detailed tables retain format and full evidence. Scoped recorded-evidence
footers preserve each source's dates, scope and gaps instead of denying valid
recorded backup evidence; the legacy diagnostic footer remains exact. Proxy current
connections/cumulative counters and Actions/issue source-update dates keep their
meaning. Existing name-preserving cleanup and confirmed guidance are retained.

Project-only focused Linux 201 passes in 0.622 s. Fresh bounded entailment/omission/
format qualification and full compatibility/release checks remain required.
Original criteria stay intact; CF-03 remains open independently of MCP.
[Increment 03](../../cara-fleet-increment-03-2026-10-24.md).
