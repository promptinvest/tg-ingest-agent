# Cara ADR reconciliation — 1 November 2026

This is a source/documentation reconciliation against documentation head
`75ec468ef22311897483928045db94143aef0046` and accepted runtime `e834c292`.
It is not a population-quality, backup-escrow or uninterrupted-health claim.

| ADR | Verified current remainder |
| --- | --- |
| 0018 | Daily local/weekly off-box backups and independent-recovery rehearsal tooling exist. Independent key escrow/off-box provenance remains unproved. The old failed-inbox pruning proposal conflicts with the later explicit preservation of terminal recovery evidence in `store.prune_telemetry`; do not implement that deletion. Explicit secure-delete configuration needs a separate scoped assessment. |
| 0022 | Postponed by the owner's no-web-search decision. Later task/broker/fleet work makes the original dormant-runtime premise stale. The echo worker remains; scheduler order now also affects durable recovery/delivery. Reassess the design before changing capability manifests, scheduling or worker state. |
| 0024 | Reminder bindings/schedule guards, reschedule undo, expired reopening and learning undo already exist. The general precedence matrix/table and per-action parameter contracts remain. Broader inverse operations need defined semantics preserving existing undo flows. |
| 0026 | Full F-rule lint, exact synthetic prompt snapshots and compatible legacy task/env helper consolidation are selected for this implementation. Verification/publication and any runtime deployment are separate evidence gates below. |
| 0027 | Current operations/service/state-root descriptions and Markdown LF rules already exist. Complete schema/retention maps and module/service coverage guards remain; retain all historical evidence during later documentation restructuring. A lexical inventory of literal table declarations is not a live schema count. |
| 0028 | Media/conversation moves are complete. Pending-resolver table and general mixin-composition checks remain, after precedence is pinned. |

Recommended sequence remains 0026, 0027, narrowly selected 0018 follow-ups, then
0024 and 0028. ADR0022 stays postponed pending a current design decision.
No failed-inbox deletion, worker dormancy, scheduler reorder, new undo feature,
backup destination/schedule, escrow claim or prompt tuning is included here.

## ADR0026 implementation and verification

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
snapshot and sensitivity checks then passed. Complete release verification and
production acceptance are not yet claimed.

Private evidence root: `/root/cara-adr26-20261101/`. Every completed/failed run is
retained. No old controller or paid qualification is reused.