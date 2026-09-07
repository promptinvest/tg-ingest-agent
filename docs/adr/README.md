# Cara improvement backlog (ADRs)

Proposed architecture decision records from the 2026-09-07 review of Cara (architecture, code, live behavior; deployed build `e3208b8`). Each ADR is one file: Status, Context, Decision, Consequences, Resolves (review finding ids), Depends on, Status log.

**Rules**

- Status lifecycle: `Proposed` → `Accepted` (owner agreed) → `Implemented <commit>` or `Rejected <why>` or `Superseded by ADR-NNNN`. Update the status line and the status log in the ADR when it changes.
- An ADR never replaces the spec rule: the implementing commit updates `CARA.md` and `SOLUTION.md` in the same commit; the ADR records why.
- Phases are the recommended order (value for effort for a single busy owner). Phase D items need an explicit owner decision before any code.
- Do not re-propose the rejected ideas listed at the end without new evidence.

| ADR | Title | Phase | Depends on | Status |
|---|---|---|---|---|
| [ADR-0001](ADR-0001-inline-buttons-are-a-first-class-control-for.md) | Inline buttons are a first-class control for reminder state changes | Phase A | nothing | Implemented 0a2639a |
| [ADR-0002](ADR-0002-simultaneous-fires-are-delivered-as-one-numbered.md) | Simultaneous fires are delivered as one numbered card with batch close | Phase A | ADR-0001 | Implemented 0a2639a |
| [ADR-0003](ADR-0003-overdue-means-fired-but-unacknowledged.md) | «Overdue» means fired but unacknowledged; expiry is announced and reversible | Phase A | nothing | Implemented 0a2639a |
| [ADR-0004](ADR-0004-every-reminder-reply-names-its-target-and-shows.md) | Every reminder reply names its target and shows human time; lists never go stale | Phase A | nothing | Implemented 0a2639a |
| [ADR-0005](ADR-0005-the-gratitude-ritual-is-journal-aware-and.md) | The gratitude ritual is journal-aware and deterministic | Phase A | nothing | Implemented 0a2639a |
| [ADR-0006](ADR-0006-bare-acknowledgements-never-reach-the-llm.md) | Bare acknowledgements never reach the LLM | Phase A | ADR-0001 for buttons | Implemented 0a2639a |
| [ADR-0007](ADR-0007-she-always-signals-that-she-is-working.md) | She always signals that she is working | Phase A | nothing | Implemented 0a2639a |
| [ADR-0008](ADR-0008-alerts-reach-the-owner-only-when-he-is-affected.md) | Alerts reach the owner only when he is affected | Phase A | nothing | Implemented 0a2639a |
| [ADR-0009](ADR-0009-the-weekly-review-is-written-for-the-owner-the.md) | The weekly review is written for the owner; the ops dump moves to the export | Phase A | nothing | Implemented 0a2639a |
| [ADR-0010](ADR-0010-action-truth-guard-v3-tighten-the-bypass-carve.md) | Action-truth guard v3: tighten the bypass, carve out honest offers, align the prompt | Phase B | nothing | Implemented 501b5f9 |
| [ADR-0011](ADR-0011-post-window-follow-ups-bind-deterministically.md) | Post-window follow-ups bind deterministically; degenerate replies never ship | Phase B | ADR-0024 for the precedence table, but can ship as a guard first | Implemented 501b5f9 |
| [ADR-0012](ADR-0012-memory-consolidation-may-fold-only-like-into.md) | Memory consolidation may fold only like into like, and it is a durable job | Phase B | nothing | Implemented 501b5f9 |
| [ADR-0013](ADR-0013-dated-recall-and-photo-aware-answers.md) | Dated recall and photo-aware answers | Phase B | nothing | Implemented 501b5f9 |
| [ADR-0014](ADR-0014-llm-failover-contract-every-model-of-a-profile.md) | LLM failover contract: every model of a profile must be able to answer in the profile's shape | Phase C | nothing | Proposed |
| [ADR-0015](ADR-0015-startup-and-poll-failures-are-visible-failures.md) | Startup and poll failures are visible failures | Phase C | nothing | Proposed |
| [ADR-0016](ADR-0016-owner-gate-before-persistence-and-sandbox-parity.md) | Owner gate before persistence, and sandbox parity for the main unit | Phase C | nothing | Proposed |
| [ADR-0017](ADR-0017-deploys-are-recoverable-and-honest-about-their.md) | Deploys are recoverable and honest about their outcome | Phase C | nothing | Proposed |
| [ADR-0018](ADR-0018-backup-posture-daily-local-weekly-off-box.md) | Backup posture: daily local, weekly off-box, verified before rotation | Phase C | owner confirms the 2026-08-21 weekly decision concerned the off-box post, not local disk | Proposed |
| [ADR-0019](ADR-0019-telemetry-is-written-only-when-there-is.md) | Telemetry is written only when there is something to say | Phase C | nothing | Proposed |
| [ADR-0020](ADR-0020-compound-commands-one-at-a-time-then-splitter.md) | Compound commands go back to «давай по одному», then to a deterministic splitter | Phase D | ADR-0024 for step 1 | Implemented 04f69a7 |
| [ADR-0021](ADR-0021-the-mentor-is-proposal-only-until-the-evidence.md) | The Mentor is proposal-only until the evidence and the model earn a candidate phase | Phase D | owner decision | Superseded by ADR-0029 |
| [ADR-0022](ADR-0022-the-worker-is-dormant-until-it-has-a-real-tool.md) | The worker is dormant until it has a real tool; research is either real or absent | Phase D | owner decision on the Brave key | Proposed |
| [ADR-0023](ADR-0023-voice-transcription-leaves-the-poll-thread-with.md) | Voice transcription leaves the poll thread, with the DB staying on it | Phase D | ADR-0007 first; owner decision | Accepted 2026-09-07 (deferred) |
| [ADR-0024](ADR-0024-pre-router-precedence-is-a-table-pinned-by-a.md) | Pre-router precedence is a table pinned by a matrix test; every immediate action has an inverse | Phase E | nothing | Proposed |
| [ADR-0025](ADR-0025-the-router-prompt-pays-for-the-gist-of-history.md) | The router prompt pays for the gist of history, not its bytes | Phase E | nothing | Proposed |
| [ADR-0026](ADR-0026-the-test-suite-gets-a-fast-path-a-fixture.md) | The test suite gets a fast path, a fixture library and a measured gate | Phase E | nothing | Proposed |
| [ADR-0027](ADR-0027-documentation-contract-present-tense-guarded.md) | Documentation contract: present tense, guarded maps, one runbook | Phase E | nothing | Proposed |
| [ADR-0028](ADR-0028-the-entry-point-is-decomposed-only-after.md) | The entry point is decomposed only after behaviour work settles | Phase E | everything above | Proposed |
| [ADR-0029](ADR-0029-cara-records-her-issues-and-reminds-the-owner-monthly.md) | Cara records her issues and reminds the owner of them once a month (first digest 2026-10-07); supersedes the Mentor | Phase D | nothing (deferred by owner: do not start now) | Accepted 2026-09-07 (deferred) |

## Phases

- Phase A — the daily loop
- Phase B — honesty and memory
- Phase C — robustness, operations, security
- Phase D — decisions the owner must make
- Phase E — maintainability and documentation

## Rejected during the review (do not re-propose without new evidence)

two-stage routing, a second LLM provider, typed dataclass Config, HTTP keep-alive, an ADR tree for past history, generated config/actions catalogues, synchronous=NORMAL, a daily fleet digest, dirty-tree refusal in deploy.sh, a category foreign key, migration-step table, whisper -t nproc-1.
