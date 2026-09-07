# ADR-0027: Documentation contract: present tense, guarded maps, one runbook

- Status: Proposed
- Date: 2026-09-07
- Phase: Phase E — maintainability and documentation
- Source: 2026-09-07 review of architecture, code and live behavior against build e3208b8 (report kept off-repo; findings are referenced by id)

## Context

CLAUDE.md says one process and omits three units and fourteen modules; media.py is in no map; 16 of 47 tables are undocumented and the guarded purge text does not disclose the task/Mentor wipe; SOLUTION §3 is 174 KB of dated batch rows with 16 KB cells; two plans still say "APPROVED / build after"; SOLUTION §10 contradicts §9 on key escrow.

## Decision

CLAUDE.md gains the rule "spec text is present tense; a change replaces the sentence, never appends a dated paragraph; the why goes in the commit and, if needed, the ADR"; CARA §6 and SOLUTION §8 name every DDL table with a retention class and the purge row discloses scope «all»; both module maps and the CLAUDE.md snapshot are completed and pinned by a checkout-only test over installer MODULES and every .service file; the five SOLUTION §3 rows over 8 KB are rewritten as present-tense rows; plan documents carry a status banner; CARA §9 becomes the runbook with unit, schedule and state-root tables and the working rollback; SOLUTION §10's escrow sentence is corrected; trailing fences removed and *.md text eol=lf; ADRs live in docs/adr/ and each implementing commit still updates both specs.

## Consequences

the specs stay the source of truth and stop growing by accretion; no ADR tree for history, no generated catalogues.

## Resolves

docs-truth#1, #4, #5, #6, #7, #9, store-data#2, ops-deploy-ci#14

## Depends on

nothing

## Status log

- 2026-09-07: proposed by the review; not yet discussed with the owner.
