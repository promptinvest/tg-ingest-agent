# CF-01 — Fleet facts and synchronization

- Status: Proposed; backlog only.
- Priority: P1, prerequisite for correct static answers.
- Dependencies: none; coordinate with CF-02 and CF-05.
- Size: Large; schema/bootstrap and publication are separate reviewable increments.

## Problem and sources

The current readable inventory contains labels, roles, aliases, permitted maintenance and project mappings, but lacks a general descriptive-facts contract and a local-KB synchronization path. Repeated operational inspection cannot answer missing provider/address metadata. The personal knowledge module searches saved note chunks, not the authoritative operational KB set.

Inspect fleet_protocol.py:47-80, fleet.py:100-105, knowledge.py:70-119,226-278 and ops/fleet/inventory.json. The existing external vault sync is not a Cara facts publisher.

## Scope and deliverables

Extend the existing fleet skill with a versioned non-executable facts artifact, separate from privileged transport and executable-target policy. A small facts module is appropriate; no new daemon is required.

Include stable existing host/project IDs, aliases, lifecycle and project-to-host/dependency relationships. Allowlist descriptive public/service addresses, outbound addresses where different, provider, region, role, project purpose and repository identity. Unknown values remain unknown. Real fleet values belong only in the owner's private operator data; tracked fixtures use synthetic identities and documentation-only address ranges.

Scalar facts, relationships, lifecycle and catalog-completeness claims all need equivalent provenance: fact_id or relation_id, subject_id, predicate, typed value, source_ids, evidence_at (or explicit unknown date), provenance/status and supported supersession/conflict relationships. Artifact metadata needs schema version, monotonic publication revision, generated time, source manifest/digests and content integrity. Keep evidence date, source check time, generation time and successful installation time separate.

Add scoped catalog completeness (complete, partial or unknown), its evidence date and source coverage for host/project enumeration. Default to unknown. “Only” and “all” claims require matching completeness evidence; a registry/probe subset alone is insufficient.

Maintain a normalized private local projection, its update ownership, reproducible export/publish commands and pending-source-change state. Resolve sources through the owner's operational source register and applicable project instructions. A source export is an allowlisted projection, not a copy of raw KB Markdown.

Publish one-way through an authenticated operator-owned configuration channel after schema/provenance/secret checks, then install atomically. Reject older revisions and conflicting same-revision bytes; intentional recovery uses a new forward revision. Retain last good facts, prior artifacts and publication receipts on failure. Do not claim continuous synchronization or create a schedule merely by installing an exporter.

## Authority, history and conflicts

Current explicit owner decisions and verified identity/lifecycle restrictions govern interpretation. Newer accepted subject-specific evidence supersedes historical evidence only for that property. Obsidian/derived dashboards, heading position, file modification time and a recent sync do not override newer authoritative evidence.

Preserve historical source material, retired/excluded tombstones and unresolved contradictions. Unverified/testing records cannot become executable targets through a facts update. Runtime observations answer runtime questions; they do not overwrite documented purpose, ownership or development state.

Differentiate contracted provider from network allocation/ASN or an inferred hosting style. Differentiate server/service/public address from outbound/egress address. Preserve absent evidence dates rather than inventing observation times.

## Acceptance criteria

- Known descriptive facts and project placements are readable with every remote connection unavailable.
- Display data never changes transport endpoints, command paths, restart allowlists or approval authority. Preserve existing address/command/key/shell rejection semantics through separate contracts.
- Older historical topology and lifecycle claims cannot silently reactivate excluded targets or overwrite accepted current facts.
- Unknown/conflicting values survive export, publication and retrieval without guesses.
- Secret canaries, credential paths, tokens, private links, user identifiers, environment values and operational commands never enter published facts or model context.
- Invalid schema/relations, partial writes, failed authentication and revision rollback preserve the last good snapshot.
- Refreshing a documentation/sync date cannot refresh an old observation or erase uncertainty.
- Publication status and relevant lag are accessible without cluttering ordinary answers.
- Existing IDs, legacy receipts, budgets, data, authority and ordinary skills remain compatible.

## Implementation and tests

Touch facts/config loading, a narrow exporter/publisher, installer/runtime validation and operator runbook. Inspect fleet.py, fleet_protocol.py, ops/fleet, deploy.sh and verify-cara-runtime.sh. Add focused schema/provenance/publication tests and extend test_fleet.py.

Use bootstrap-facts.json for synthetic contract examples. Private operational source data is not a prerequisite for developing offline contracts and tests; installation requires the owner's authenticated sources and access through the established operator workflow.

Out of scope: whole-KB RAG, credential synchronization, raw-source rewrites, monitoring replacement, new execution authority, schedules, repository/CI accounts, unrelated application deployments or new project write runbooks. The facts publisher's installation is included when its implementation is later authorized.

## Tracking

Parent roadmap: [#7](https://github.com/promptinvest/tg-ingest-agent/issues/7). Blocked by: none. Blocks: [#2](https://github.com/promptinvest/tg-ingest-agent/issues/2), [#5](https://github.com/promptinvest/tg-ingest-agent/issues/5). Track the partial-independent increments described above separately; no dependency implies new runtime permission.

## Development contract

This is proposed development work recorded on October 24, 2026. The owner requested a thorough review and a backlog for further development; implementation, new capabilities and production changes are not represented as completed or authorized by creating this issue. A later implementation request follows the owner's standing implementation-includes-deployment rule within its explicit scope and all existing release gates.

Read AGENTS.md, the full CLAUDE.md, CARA.md, SOLUTION.md, docs/adr/README.md and docs/backlog/cara-fleet-2026-10-24/README.md. Canonical repository: promptinvest/tg-ingest-agent, local projects/tg-ingest-agent; projects/DLingo is another application. Preserve working notes, reminders, calendar, journals, speech, research, memory, budgets, history, backups and confirmations. The existing research worker remains networkless; fleet credentials stay in its separate executor; model output never supplies executable addresses, paths or commands.

Use the existing budgeted LLM gateway for free-form interpretation and appropriate reply format. Zero host probes for catalog questions does not mean zero model calls. Do not introduce a required exact-phrase interface, another routing stage/provider/daemon, or a generic shell tool as an incidental remedy.

Update BOTH CARA.md and SOLUTION.md, relevant ADR/backlog status and operator docs in every implementing commit. Tests run in an isolated Linux/VPS stage, not the OneDrive checkout. Preserve full required Linux/CI, installed-source/runtime, sandbox-denial, stopped-data parity, backup, monitor/alert and guarded-release checks. No manual Telegram test sends, historical update/controller replay, live maintenance demonstration, unrelated paid work or change to another project's release rules.

Full handoff, source map, sanitized production evidence, bootstrap facts and semantic regression corpus:
https://github.com/promptinvest/tg-ingest-agent/tree/main/docs/backlog/cara-fleet-2026-10-24

### CF-01 contract/publication increment

`fleet_facts.py` defines exact descriptive provenance, uncertainty, completeness,
retained history and lifecycle exclusions. `fleet_facts_publish.py` verifies the
reviewed workstation source register before export. The receiving root-owned
publisher accepts a validated sealed export without requiring raw KB files. It
retains immutable revision history, an atomic current view and publication receipt;
failed or changed inputs leave the last good view intact. Tests use synthetic data.

The 53 schema/history and 17 publisher cases pass in isolated Linux focused runs.
The later combined integration check passed those cases again; installer checks
remain a separate gate. No new fact artifact is installed in production yet.
Source synchronization is an explicit review/export/status workflow at this stage,
not a claim of an automatic scheduler. No CF-01 completion is claimed.