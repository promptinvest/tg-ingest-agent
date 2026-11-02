# Cara ADR reconciliation — 1 November 2026

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
| 0018 | Daily local/weekly off-box backups and independent-recovery rehearsal tooling exist. Independent key escrow/off-box provenance remains unproved. The old failed-inbox pruning proposal conflicts with the later explicit preservation of terminal recovery evidence in `store.prune_telemetry`; do not implement that deletion. Explicit secure-delete configuration needs a separate scoped assessment. |
| 0022 | Postponed by the owner's no-web-search decision. Later task/broker/fleet work makes the original dormant-runtime premise stale. The echo worker remains; scheduler order now also affects durable recovery/delivery. Reassess the design before changing capability manifests, scheduling or worker state. |
| 0024 | Reminder bindings/schedule guards, reschedule undo, expired reopening and learning undo already exist. The general precedence matrix/table and per-action parameter contracts remain. Broader inverse operations need defined semantics preserving existing undo flows. |
| 0026 | Complete 2026-11-01: full F-rule lint, exact synthetic prompt snapshots and compatible legacy task/env helper consolidation passed exact-source verification, native acceptance and independent audit. The contained first deployment failure and forward continuation are retained in the release record. |
| 0027 | Complete 2026-11-02: curated module/service/retention maps, synthetic schema guards, current contracts and one operations runbook are verified. Byte-exact previous specs retain historical evidence; runtime unchanged. |
| 0028 | Media/conversation moves are complete. Pending-resolver table and general mixin-composition checks remain, after precedence is pinned. |

The next sequence is narrowly selected 0018 follow-ups, then 0024 and 0028.
ADR0022 stays postponed pending a current design decision.
No failed-inbox deletion, worker dormancy, scheduler reorder, new undo feature,
backup destination/schedule, escrow claim or prompt tuning is included here.

## 2 November - ADR0027 complete

The owner selected the next ADR. ADR-0027 completes the current documentation
contract and checkout coverage, with byte-exact historical specs retained. Full
CI normal/shuffled gates and 41 native Linux checkout checks pass. Production
remains accepted ADR0026; no installation or restart is required. See the
[implementation and verification record](cara-adr27-2026-11-02.md). Next is the
narrow ADR0018 remainder with the exclusions above; this completion selects no
backup policy, key custody claim or deletion of failed recovery evidence.

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
