# Cara ADR-0018 release — 2 November 2026

**Accepted at 09:25:38.717106 UTC; independently audited at 09:27:18.598737 UTC.**
The separate audit and final observation found all four native services active
with zero automatic restarts.

Source `6e947694518659e4a41d516f6f35c4f3012f49f6` completes the narrow ADR0018
source follow-ups, deployed as build `ee4aa3446c7d`, deployment
`7189e6368a3717ed1fa129ec`. The optional, empty-by-default fingerprint
configuration compares exact bounded regular key-file bytes during the existing
monthly restore check. Its diagnostic cannot replace the actual restore
outcome. Explicit `secure_delete=ON` pins the native SQLite build's existing
enabled default. The release adds no dependency, schema change, backup
destination, schedule or retention policy. Failed inbox rows remain recovery
evidence. Fingerprint configuration remains unset; independent key custody,
off-box provenance and recovery after host loss remain unproved.

Published tag
[`production-2026-11-02-adr18-backup`](https://github.com/promptinvest/tg-ingest-agent/tree/production-2026-11-02-adr18-backup)
has annotation `232f9ab593654bfd16b7bb8c7cfa5418339d37f4` and peels to this exact
source. Independent remote verification confirmed all 25 prior tag references
across 13 earlier tags unchanged.

The exact source archive is
`15c50f3137b33539c420c14de64b4fa452bc6b1974762b5e0ebef505d26d21a9`.
A fresh native gate against this exact source passed 3,357 tests in normal order
and 3,357 shuffled tests, retaining nine skips in each run. The 154 focused
checks retain one skip; full F rules, compile, shell syntax, shellcheck, four
helper suites and all five installed-layout scenarios pass. The actual parent
wait returned zero. Exact-source [CI run 41414212959](https://github.com/promptinvest/tg-ingest-agent/actions/runs/41414212959)
also passes, with 62 environment skips retained in each order.

The original 110 test/support files and all 22 reviewed policy, prompt,
serializer and hook files are unchanged. The DDL/migration bodies, router,
capabilities, models, prompts and budgets retain their existing behavior.
Operator qualification passes 111 checks against a closed 13-file map,
19 transport fault tests, both nonroot kernel import probe roles and three
actual isolated cgroup cases. Earlier failed preparation evidence remains.
The [implementation record](cara-adr18-2026-11-02.md) preserves the first
configuration-example failure and the pre-approval release hold separately.

The owner's later “I approve” authorized one exact healthcheck exception for
this release. The approval-recorded time is 08:48:34 UTC; it is not a claimed
message timestamp. That exception is consumed; the earlier exception was not
reused. Retained records are unchanged, and every new unknown remains blocking.
This approval creates no standing exception or recovery-policy change.

All 21 native release phases passed, including fresh preflight, staged and
installed layout, online/stopped backups, paused preservation, exact
installation, native security/readiness, private MCP checks, scoped reads,
final restart and healthy samples. Both MCP gates passed their nine scenarios
across six tools; all nine scoped reads passed. Stopped-state checks compared
all 56 tables, rows and cache fields. All 132 original protected files remained
exact; four new fixture policies bring the protected inventory to 136. Existing
settings, environment, models, prompts, budgets, history, closed-trial evidence
and timers remain preserved. Ordinary runtime progress and the existing
telemetry retention continue; this is not a frozen-byte claim for every row of
the running database.

The actual guardian, controller and separate audit returned zero. Final closure
at 09:28:50 UTC found all four native services healthy, with zero automatic
restarts, and all recorded release actors/groups absent. A fresh 09:30:08 UTC
check found the owned tool cgroup empty. Every completed one-shot remains
consumed; no controller replay, reset, reclassification or recovery retry occurred.

Acceptance receipt: `03924d31c01c4b4d25d74bf92e8221c90dbb8e2f5a8a96d3b7bf44a74502ec9b`.
Independent audit: `4cad48416da34d80f02eb3063e19699f0498357acfe6c760a46eb1d38cee0d2b`.
The acceptance receipt's original pending-audit field remains unmodified;
completion is proved by the separate audit receipt.

ADR0018's narrow implementation and release are complete. Independent custody
and off-box provenance still require separate evidence; failed-inbox deletion
is excluded. The next reconciled source item is ADR-0024, then ADR-0028; neither
remainder is implemented by this release.

Machine-readable evidence: [verification record](cara-adr18-release-verification-2026-11-02.json).
