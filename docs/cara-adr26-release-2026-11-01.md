# Cara ADR-0026 release — 1 November 2026

**Accepted at 11:36:09.901372 UTC; independently audited at 11:37:06.442988 UTC.**
The audit observed all four native services active with zero restarts.

Source `75f0f345a6a0195bcad519045ab8473abcfa72d5` completes ADR-0026's remaining
test-maintenance work: full Ruff F-rule checks, six exact synthetic request
snapshots and nine snapshot/sensitivity tests for router/converse/Hermes, and shared task-serialization and environment
writer helpers. Existing prompt bytes, task encoding and environment-writer
behavior are preserved. The runtime remains standard-library-only.

Published tag
[`production-2026-11-01-adr26-tooling`](https://github.com/promptinvest/tg-ingest-agent/tree/production-2026-11-01-adr26-tooling)
has annotation `a3e220c74a05b79d27e32bfc29376aa8f2511d85` and peels to this exact
source. Remote verification confirmed all 23 earlier tag references unchanged.

The exact source archive is
`e56bfe71c5b7edd609bb7ee46776c4334304b4312b7debd943e0202efdfa9282`.
Linux normal and shuffled discovery each completed 3,320 tests successfully,
retaining nine skips per run. The 28 focused regressions, original helper suites,
full F rules, compile, shell checks and five installed-layout scenarios passed.
Exact-head [CI run 41291859096](https://github.com/promptinvest/tg-ingest-agent/actions/runs/41291859096)
also passed. Operator qualification covered 94 original, 12 forward-continuation
and eight MCP-harness tests; transport qualification covered 14 fault tests and
three actual isolated cgroup cases.

The first deployment completed fresh preflight, staged-layout checks, online and
stopped backups, preservation checks, installation, native security verification
and initial nonroot readiness. Its private MCP fixture then failed before
creating its synthetic database: the operator harness appended a 64-character
nonce to principal prefixes, producing 78- and 84-character values against the
existing 64-character limit. Forward containment paused the application and
executor, preserving the installed candidate and every failed receipt.

The repair changes only fixture principal construction. A bounded suffix derived
from the complete nonce preserves distinct principals; private paths and receipt
bindings retain the full nonce. The application, principal validator, exact MCP
check, nine scenarios and existing timeouts remain unchanged. A fresh isolated
kernel probe reproduced the invalid-principal rejection and passed all nine
original scenarios across six tools with the repaired harness.

A new forward continuation launched once at **11:33:15 UTC** against the same
installed deployment `9a7da34f2a7761ce681a1c23` (build `931c14f8bb74`). It passed
fresh readiness, private MCP, scoped reads, preservation, final restart and health
gates, then reached native acceptance and passed the independent audit. The
application was not reinstalled and no data rollback occurred. Stopped-state
checks compared all 56 tables, rows and cache fields. Schema, original usage/cache
history, personal content and protected settings were preserved. Running services
continued the existing 90-day telemetry retention; original stopped snapshots and
recovery evidence remain protected.

Acceptance receipt: `30ced8280f3a5478a0d2174271ec9f52923e488d38d6c5cd0247da3eecae1fb7`.
Independent audit: `92fc0992edba2d6a82573c80dff60e38936bf4300b319e78ce44a54b4d1dbf47`.
ADR-0026 is complete; ADR-0027 is the next reconciled item.

Machine-readable evidence: [verification record](cara-adr26-verification-2026-11-01.json).
