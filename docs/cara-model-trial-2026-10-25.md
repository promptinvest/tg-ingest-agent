# Cara model trial - October 25, 2026

Owner request: try the proposed models, monitor for a few days and revert if needed.
This is a reversible configuration trial through the existing DigitalOcean
Gradient gateway. It extends the existing named profiles; no new application
skill, provider integration or runtime process is needed.

## Selected configuration

| Work | Primary model |
| --- | --- |
| Routing, ingest, memory candidates | Claude Haiku 5.5 |
| Grounded KB answers, memory consolidation | Claude Sonnet 5.5 |
| Task planning and synthesis | GPT-6.1 Sol |
| Conversation | DeepSeek v4 Pro, exact existing profile |

The dormant improvement-evaluator profile also selects Sol; it has no production
caller and is not claimed as functionally qualified. Existing fallback lists keep
their original order; DeepSeek Flash is appended where absent. Vision remains
Llama 4 Maverick; embeddings remain BGE-M3 and speech remains local Whisper.
Conversation retains its entire original effective profile and 320-token cap.
Sonnet320 truncated measured replies; Sonnet640 and Sol640 lost task context
through the existing corrective path, or returned a generic containment response.
All failed cohorts remain retained and are excluded from the live trial. The
owner was offered a qualified-role trial versus a separate compatibility fix;
the qualified-role trial preserves current conversation while proceeding with
the authorized model switch elsewhere. All other token limits, timeouts, temperatures, prompts,
validators, confirmation rules and tool permissions remain exact.

Budget guards remain USD2 per UTC day and USD12 per month. Existing authoritative
SQLite preference overrides remain. To preserve safe reservations across context
tiers with the unchanged flat-rate gateway, trial accounting uses upper rates per
million input/output tokens: Haiku USD0.50/2.50, Sonnet USD2/10 and Sol USD4/15.
These are conservative application estimates, not provider invoice amounts.

## Evidence and execution

The installed accepted runtime remains `ee1a319ce53ddc941ebf4b760c084abd865afd19`,
build `de3e62ab43f8`, deployment `ad778b10272c813105b7080a`. Its full normal/shuffled
Linux qualification is retained from the accepted reliability release. This trial
installs no application code, changes no native deployment marker and performs
no migration. The model-only operator verifies all 91 runtime counterparts and
78 protected files, with only the environment's four selected model keys allowed
to differ. Online/stopped backups, exact stopped 56-table schema/row parity,
actual loaded model keys, unchanged peer processes and final health checks are
required. No operator Telegram message or message replay is included.

Private evidence root: `/root/cara-model-trial-20261025`. `attempt1` contains the
retained 320-token failure and paired baseline; it is superseded and must never
activate. `attempt2` retains the failed Sonnet640 cohort. `attempt3` retains failed Sol640 evidence. `attempt4` is the selected partial trial. Original environment hash
`e1ae7f6c65e4076a1a6fb93a87ecf200eb59745f048183acbbea389cfa64b733`;
selected candidate hash is recorded with acceptance below.
The [operator runbook](../ops/model-trial/README.md) contains guarded commands.
Started controllers/cohorts are one-shot; read receipts and never replay them.

The original 24 synthetic RU/EN cases cover eight active profiles through real
prompt builders and isolated handler/validator replay. Host execution and outbound
messages are mocked. The selected partial cohort covers exactly 21 cases across
seven changed active profiles. Each carried raw answer retains exact input,
profile, model, source and paid-ledger bindings, and is revalidated under the final
configuration without new model calls. Conversation is separately proven unchanged;
its three failed new-model cases are not counted as passes. The dormant evaluator
is configured but not functionally qualified. All charged attempts remain within
the same USD5 aggregate ceiling and ordinary production budget guards.

A minimal compatibility fix would retain the first-pass task context and current
requested structure in the existing repair prompt, which currently discards both.
That application change is not part of this configuration-only trial.

The first preparation commit's CI caught an unrelated fixture expiry: its mocked
reminder deadline was fixed at October25 12:00 UTC, and became past during this
session. The fixture now selects a future timestamp; every original assertion and
all application behavior remain unchanged. Failed CI evidence is retained.

## Observation and reversion

A seven-day metadata baseline is retained. Read-only reports run every six hours
through exactly 72 hours after acceptance, then the dedicated timer stops. Reports
separate ordinary, health-check and qualification calls; include ledger estimates,
unknown reservations, fallback/failure events, processing latency, delivery states
and service restarts; and contain no conversation bodies. The monitor makes no
model calls or owner messages. Metadata cannot judge answer quality; fewer than
20 ordinary calls leaves descriptive p95 unestablished. Trial evaluation must also
consider actual conversations, format following and unnecessary repair/fallbacks.

Reversion is conditional, not automatically scheduled. The guarded command
restores only trial-changed model keys and leaves the original conversation profile intact, preserving
later unrelated environment edits, all stored data, usage and conversation history.
It takes a fresh stopped backup and never restores the old database or application.
Conflicting later model/runtime changes require review before any overwrite.

Status: accepted; exact qualification, activation and monitoring evidence follows.

The retained conversation identity is the verified explicit `deepseek-v4-pro`,
with the original Kimi/Flash/OSS fallback order. An initial partial-proof helper
incorrectly assumed the default Flash primary and rejected before its phase marker,
output or paid call; that failed helper evidence remains retained. The corrected
proof checks the actual Pro identity and retains all original profile-equality checks.

## Accepted trial and bounded observation

Accepted **2026-10-25T13:36:17.924552+00:00**. Observation ends
**2026-10-28T13:36:17.924552+00:00**; the last absolute timer entry is rounded up
to 13:36:18 UTC (16:36 Moscow). The [sanitized verification receipt](cara-model-trial-2026-10-25-verification.json)
records every binding without raw conversations or credentials.

- Candidate environment: `46d9c31a9dd24770b51f9830a6150ce407700912a04304abf1c153c29d1f9db5`.
- Acceptance: `e67a3251c6591197dabab561a52307ea6436480da92cb1a429a19bbaa9f68c1e`.
- Qualification: `3dda2a884f12e6ad49655d84008219cb83c7256f26bdc342db39468f8d04d8a1`.
- Frozen model operator package: `6e8ca8736dbdde7b0bcf38c47ee244af306ec7d01dcf2f429e014e2a106230df`.
- Independent observer package: `2dbd5ab869052edc9bf3aab64b623f6d0986a5a7a25613f1f7b9e2a225c2ad7a`.
- Observer manifest: `c50b91764b72cbc9f0045af574e76fd357899055828e8158ddab630ce395e0dd`.

The model operator remains frozen separately from the observer. Preflight found
the private database directory mode 0700 and SQLite/WAL/SHM mode 0600 owned by Cara.
The observer therefore needs `CAP_DAC_READ_SEARCH` with fixed root identity and
read-only mounts; it has no write-bypass capability. No ACL, ownership or data-mode
change was made. The independent observer's 26 Linux tests pass and its actual
sandboxed initial snapshot succeeds. Its twelve absolute dates cover +6 through +72h.
No paid probes, owner messages or automatic model reversion are included.

Selected runtime/source parity, online/stopped backups and exact stopped 56-table
data/schema preservation pass. Only the main process restarted; all peer processes,
native deployment marker, 91 installed runtime files, 78 protected identities except
the selected environment hash, and native deployment-notification receipt remain.
Three final samples and a fresh post-monitor check pass; main PID 3143963 has zero
automatic restarts. No database restore, migration or application install occurred.

All 21 changed-role cases pass finite functional and independent semantic review.
The final replay makes zero new paid calls; 59 total qualification calls across
baseline, failed and selected evidence cost USD0.357269084 in the conservative
application ledger, with zero unknown charges. This is not a provider invoice.
Both native Linux orders pass 2,987 tests/nine existing skips; 154 model-operator
checks and 26 observer checks pass. [Exact model-operator/source CI](https://github.com/promptinvest/tg-ingest-agent/actions/runs/40197607609)
passes at af3dcb7350358009dae7350f7f22b30b7a8be9c2. The earlier failed CI and all
failed/consumed originals remain retained. Never replay started controllers.

The first observation correctly reports insufficient ordinary traffic. Metadata
does not establish answer quality, long-term reliability or end-to-end delivery
latency. Review actual conversations together with the 72-hour reports before
choosing whether to keep or revert the new roles.

Fresh 13:40:31 UTC checks retain the same main PID and zero automatic restarts,
zero unknown inference outcomes since acceptance, unchanged runtime/protected files
and the active timer. No ordinary model calls had occurred yet.

## Context/evidence release continuation

The [context/evidence release](cara-context-evidence-2026-10-25.md) was accepted
at **2026-10-25T16:21:02.879450+00:00**, source `17083d6`, build `23cada405758`.
All model roles, profile controls, fallbacks and USD2/day / USD12/month remain.
The source change prevents treating later cumulative reports as model-only
comparison evidence. Original reports and failed trials remain unchanged.

The authenticated continuation is
`860b5f62ac51a2ae3b86950f4e12d64b6a405c82851b8eb71c5d5557121e25f4` under
`/root/cara-capability-20261025/release-attempt4`. Its old-source boundary is
15:53:05.997074 UTC; first new-source exposure is 15:53:26.953420 UTC. The explicit
pause **15:54:21.327691–16:18:45 UTC** remains recorded; acceptance resets neither
source exposure nor the **October28 13:36:17.924552 UTC** deadline.

The original observer timer is disabled; its unit files, receipts and historical
snapshots remain. The separately qualified
`cara-model-trial-20261025-continuation-observer.service/.timer` retains six-hour
absolute dates and the final 13:36:18 UTC entry. Its real first sandbox run passed
at 16:21:33 UTC and reports insufficient ordinary traffic. It makes no model
calls/messages or automatic quality/reversion decision; no data permission changes.

The original frozen reverter correctly refuses the new source and must not be
used for this runtime. The accepted root's `trial_runtime_operator.py` provides
conditional `revert-preflight` then `revert`: bind a fresh PID/idle/backup/source
preflight, continuation SHA above and the exact preflight SHA. It compare-and-swaps
only the four original model keys, preserving later unrelated environment edits,
the installed source and all data/history. Both verifier paths retain their
command-local PATH fix. Reversion has not been run. Never replay a consumed
release controller or restore an old database.
