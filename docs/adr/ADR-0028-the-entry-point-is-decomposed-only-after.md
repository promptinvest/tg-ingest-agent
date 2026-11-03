# ADR-0028: The entry point is decomposed only after behaviour work settles

- Status: Implemented/deployed/accepted 125aaefe, 2026-11-03; independent audit, physical closure and production tag verified
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

- 2026-11-03: source `125aaefe` is merged in PR18 and installed as build
  `2fec08f110b0`. All 21 native release phases passed; canonical acceptance is
  `39d6f0f3`, 08:04:41.793556 UTC, and the separate independent audit passed as
  `a73d7c78`, 08:07:18.879686 UTC. Strict accounting retained all 40 historical
  unknown rows with zero current unknowns; original usage/cache/schema,
  configuration, models, budgets and peer state remain preserved. Exact-source
  216 focused and 3,470 normal/shuffled checks passed with nine retained skips.
  Failed qualification/preparation artifacts remain unchanged. A fresh reviewed
  preparation continuation passed 45 checks and added only four hash-bound
  accepted ADR0024 metadata entries to the exact original 136. The native
  controller, supervisor, guardian and foreground waits returned zero. Separate
  physical closure `951bb162` proved recorded actors/groups and the owned tool
  cgroup absent, with four healthy services. The earlier physical observer's
  standard-I/O guard failure remains preserved. Production tag
  `production-2026-11-03-adr28-pending-resolvers` peels to exact source `125aaefe`;
  all 15 previous tags and 29 previous refs remain exact. See the
  [release record](../cara-adr28-release-2026-11-03.md). No consumed exception or
  old controller was reused.

- 2026-11-03: owner requested "implement next Cara ADR" after accepted ADR0024.
  The selected remainder is the pending-resolver table and general mixin contract
  guard. All thirteen resolver bodies are pure moves on the existing Agent;
  journal-correction precedence, shared stale-card checks, cancellation cleanup,
  unknown-kind fallback and the outer reminder delivery wrapper remain intact.
  No new runtime module, installer entry, schema, prompt or service is needed.
  Baseline characterization passed nineteen checks and failed only the missing
  table coverage check as expected. Exact-source full verification and guarded
  release remain pending; previous failed/completed controllers stay consumed.

- 2026-09-07: proposed by the review; not yet discussed with the owner.

- 2026-10-07: owner authorized "fix all & deploy" following the code/live review. Partially implemented 2026-10-07 (media and converse pure moves; pending resolver table remains). Verification and deployment evidence: `../../docs/cara-review-fixes-2026-10-07.md`.

- 2026-10-07: shipped in reviewed release `7995fad` and corrected release `36f2075`; live build `9d041a123f25`, receipt `0f839bc1e8c673d4a6f196df`. Remaining subtasks named in the status line stay open.
