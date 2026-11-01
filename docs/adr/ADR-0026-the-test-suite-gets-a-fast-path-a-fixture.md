# ADR-0026: The test suite gets a fast path, a fixture library and a measured gate

- Status: Implemented and deployed 75f0f34; accepted 2026-11-01; independent audit passed
- Date: 2026-09-07
- Phase: Phase E — maintainability and documentation
- Source: 2026-09-07 review of architecture, code and live behavior against build e3208b8 (report kept off-repo; findings are referenced by id)

## Context

one 25.9k-line file, 118 classes, helpers re-implemented per class, a 700 s deploy gate whose cause is unmeasured, no lint, twelve unused imports, duplicated serializers and a drifted env writer.

## Decision

deploy.sh --test accepts unittest targets with a loud subset banner while plain deploy keeps full discovery; a per-test timing report prints the 20 slowest after the standard summary the runner parses; TMPDIR=/dev/shm and PRAGMA synchronous=OFF are tried in the fixture before any template-DB work; testlib.py and top-level test_<subject>.py files replace the monolith in batches, never a tests/ package because deploy.sh FILES and the stage dir would miss it; ruff F-rules run in CI only; golden prompt snapshots for converse, router and hermes; _canonical and the env writer are consolidated with a worker-versus-agent hash test; a shuffled-order CI job proves order independence; nice/ionice on the on-box test stage.

## Consequences

the box stays stdlib-only; the deploy gate is measured before it is optimized.

## Resolves

tests-quality#1, #2, #4, #6, #10, #11, #12, ops-deploy-ci#6

## Depends on

nothing

## Status log

- 2026-09-07: proposed by the review; not yet discussed with the owner.

- 2026-10-07: owner authorized "fix all & deploy" following the code/live review. Partially implemented 2026-10-07 (timed gate, subsets, shared fixtures, split suites, tmpfs and shuffle; lint/serializer follow-ups remain). Verification and deployment evidence: `../../docs/cara-review-fixes-2026-10-07.md`.

- 2026-10-07: shipped in reviewed release `7995fad` and corrected release `36f2075`; live build `9d041a123f25`, receipt `0f839bc1e8c673d4a6f196df`. Remaining subtasks named in the status line stay open.
- 2026-10-19: owner requested adding undefined-name lint from the follow-up review.
  CI pins Ruff 0.17.1 and enables F821/F822/F823 without runtime dependencies.
  Wider F-rule cleanup, prompt snapshots and serializer consolidation remain open.

- 2026-11-01: owner selected the reconciled completion stage. Full F-rule checks,
  exact synthetic prompt fixtures and compatible task/env helper consolidation
  are implemented in preparation; full verification and runtime deployment remain
  pending. [Reconciliation and evidence](../cara-adr-reconciliation-2026-11-01.md).

- 2026-11-01: selected completion deployed and accepted at 11:36:09.901372 UTC
  in exact source `75f0f34`, build `931c14f8bb74`; the separate audit passed at
  11:37:06.442988 UTC. CI checks all F rules; synthetic snapshots pin the existing
  conversation/router/Hermes prompts; shared task/environment helpers retain the
  legacy byte/hash/wrapper/sync contracts. Full Linux normal and shuffled runs
  passed 3,320 cases each (nine skipped per order), with exact-source CI and
  installed-layout checks. Operator suites passed 94 original, 12 forward and
  eight MCP cases; 14 transport and three real cgroup cases passed. Isolated MCP
  qualification passed six tools and nine scenarios. A contained first attempt
  and its proofs remain. An operator-fixture principal repair and fresh
  same-installed-state continuation completed only the missing gates, without
  reinstalling, migrating or replaying an earlier controller. The original
  56-table schema, history and cache metadata are preserved. The remotely verified
  annotated tag [production-2026-11-01-adr26-tooling](https://github.com/promptinvest/tg-ingest-agent/tree/production-2026-11-01-adr26-tooling)
  points to exact source `75f0f34`; earlier tags remain. Acceptance
  `30ced828` and separate audit `92fc0992` are recorded in the
  [release](../cara-adr26-release-2026-11-01.md) and
  [sanitized verification](../cara-adr26-verification-2026-11-01.json).
