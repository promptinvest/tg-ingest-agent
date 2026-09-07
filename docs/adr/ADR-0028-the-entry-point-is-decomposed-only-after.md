# ADR-0028: The entry point is decomposed only after behaviour work settles

- Status: Proposed
- Date: 2026-09-07
- Phase: Phase E — maintainability and documentation
- Source: 2026-09-07 review of architecture, code and live behavior against build e3208b8 (report kept off-repo; findings are referenced by id)

## Context

tg_ingest_agent.py is 6.6k lines with undeclared cross-mixin self.<x> dependencies; the 2026-07-01 extractions of notes_svc and reminders_svc worked.

## Decision

last in the sequence, three pure-move commits each gated by the full VPS suite: media_svc.py MediaMixin, converse_svc.py ConverseMixin, then a PENDING_RESOLVERS table mirroring _DISPATCH; each new module is added to installer MODULES in the same commit; a static test asserts every self.<x> used by a mixin is defined on the composed class; string-form mock.patch targets are grepped before each move.

## Consequences

maintainability only; must not collide with ADR-0001 to ADR-0024 in the same regions.

## Resolves

architecture#6, tests-quality#8 in part

## Depends on

everything above

## Status log

- 2026-09-07: proposed by the review; not yet discussed with the owner.
