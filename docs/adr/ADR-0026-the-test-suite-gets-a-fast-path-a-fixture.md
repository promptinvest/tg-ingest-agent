# ADR-0026: The test suite gets a fast path, a fixture library and a measured gate

- Status: Proposed
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
