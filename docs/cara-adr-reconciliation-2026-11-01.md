# Cara ADR reconciliation — 1 November 2026

ADR-0018's narrow follow-ups are deployed and accepted as source `6e947694`,
build `ee4aa3446c7d`, deployment `7189e6368a3717ed1fa129ec`. Native acceptance
completed on 2 November at 09:25:38.717106 UTC; the separate audit passed at
09:27:18.598737 UTC. See the [release record](cara-adr18-release-2026-11-02.md)
and [verification](cara-adr18-release-verification-2026-11-02.json). Independent
key custody and off-box provenance remain unproved; failed-inbox deletion is excluded.

ADR-0026 is complete: source `75f0f345`, build `931c14f8bb74`, deployment
`9a7da34f2a7761ce681a1c23` was accepted on 1 November at 11:36:09.901372 UTC;
the independent audit passed at 11:37:06.442988 UTC. See the
[release record](cara-adr26-release-2026-11-01.md) and
[verification](cara-adr26-verification-2026-11-01.json). ADR-0027 is complete in the subsequent 2 November checkpoint below.
Published tag
[`production-2026-11-01-adr26-tooling`](https://github.com/promptinvest/tg-ingest-agent/tree/production-2026-11-01-adr26-tooling)
was remotely verified to peel to the exact accepted source.

The initial source/documentation reconciliation used documentation head
`75ec468ef22311897483928045db94143aef0046` and then-accepted runtime `e834c292`.
The preparation history below predates this accepted release. This record does
not establish population quality, backup escrow or uninterrupted health.

| ADR | Verified current remainder |
| --- | --- |
| 0018 | Narrow follow-ups deployed/accepted as `6e947694`, 2026-11-02, with a separate passing audit: optional operator-confirmed fingerprint comparison and explicit existing secure-delete mode. Independent custody/off-box provenance remain unproved; failed-inbox deletion stays excluded. [Release evidence](cara-adr18-release-2026-11-02.md). |
| 0022 | Postponed by the owner's no-web-search decision. Later task/broker/fleet work makes the original dormant-runtime premise stale. The echo worker remains; scheduler order now also affects durable recovery/delivery. Reassess the design before changing capability manifests, scheduling or worker state. |
| 0024 | Implemented/deployed/accepted 2b6da6ee: precedence table/matrix, compatible route contracts and explicit generic undo. Existing phrases and history preserved; [release](cara-adr24-release-2026-11-02.md). |
| 0026 | Complete 2026-11-01: full F-rule lint, exact synthetic prompt snapshots and compatible legacy task/env helper consolidation passed exact-source verification, native acceptance and independent audit. The contained first deployment failure and forward continuation are retained in the release record. |
| 0027 | Complete 2026-11-02: curated module/service/retention maps, synthetic schema guards, current contracts and one operations runbook are verified. Byte-exact previous specs retain historical evidence; runtime unchanged. |
| 0028 | Media/conversation moves are complete. Pending-resolver table and general mixin-composition checks remain, after precedence is pinned. |

ADR0018's narrow implementation and release are complete. ADR0024 is implemented, deployed and accepted as recorded above. ADR0028 is the
next source remainder; it is not selected by this closure.
ADR0022 stays postponed pending a current design decision.
No failed-inbox deletion, worker dormancy, scheduler reorder, broader backup
destination/schedule, escrow claim or prompt tuning is selected by this reconciliation.

## 2 November - ADR0027 complete

The owner selected the next ADR. ADR-0027 completes the current documentation
contract and checkout coverage, with byte-exact historical specs retained. Full
CI normal/shuffled gates and 41 native Linux checkout checks pass. Production
remains accepted ADR0026; no installation or restart is required. See the
[implementation and verification record](cara-adr27-2026-11-02.md). Next is the
narrow ADR0018 remainder with the exclusions above; this completion selects no
backup policy, key custody claim or deletion of failed recovery evidence.

## 2 November - historical ADR0018 source checkpoint; release held before approval

Source `0be55ff` passes 3,357 native checks in both normal and shuffled order
with nine retained skips, plus focused/static/operator/installed-layout gates.
Exact-source CI passes both orders with its 62 environment skips retained.
The [implementation record](cara-adr18-2026-11-02.md) and
[verification](cara-adr18-verification-2026-11-02.json) preserve the first
configuration-example failure separately. The earlier ADR0027 checkpoint above
selected this work; it no longer describes the current next source item.

All independently implementable ADR0018 source work is complete. The strict
unknown-inference release guard requires an owner decision before installation;
no old exception or controller may be reused. Accepted production remains ADR0026.
Actual independent custody/provenance still requires separate evidence, and the
old failed-inbox deletion proposal remains superseded by preservation rules.
No ADR0024/0028 implementation or broader recovery policy is selected by this
closure.

## 2 November - ADR0018 narrow follow-ups accepted

The owner's later “I approve” authorized one exact healthcheck exception for
this release. Fresh exact-source normal/shuffled checks, all 21 native release
phases and the separate audit passed. Source `6e947694` is the accepted runtime;
the [release record](cara-adr18-release-2026-11-02.md) supersedes the held release
checkpoint above while preserving its original source/failure evidence. The
one-release exception is consumed, retained records are unchanged and every new
unknown remains blocking. Independent custody/provenance still requires real
evidence; no failed-inbox deletion or broader backup policy was selected.
ADR0024 is the next reconciled source item and remains unimplemented here.

## ADR0026 preparation history

The selected change extends existing test and deployment tooling; it is not a new
skill/process. Legacy task `_canonical` entry points retain exact UTF-8/hash
behavior, including existing nonfinite-number handling. Fleet serializers keep
their distinct strict contracts. Operator wrappers retain their existing APIs and
file/directory-sync differences. Original regression assertions remain.

The initial scratch audit found 248 F-rule diagnostics (217 F401, 5 F811, 26 F841).
The first tool setup failed because the host has no ensurepip; the second wheel
extraction used an incorrect executable suffix. Both failures remain in the fresh
private audit root. The third audit used the project-pinned Ruff wheel and verified
its published SHA256. No system packages or production files changed.

Prompt fixtures were generated from accepted prompt source in an isolated,
networkless Linux scratch checkout. Missing-fixture checks failed first; all nine
snapshot and sensitivity checks then passed. At that preparation checkpoint,
complete release verification and production acceptance were still pending;
both subsequently completed as recorded above.

Every completed and failed run is retained privately. No completed controller was
replayed. The failed first deployment was contained; the successful continuation
verified the same installed candidate without reinstalling it or rolling back data.
