# Cara model trial ? October 25, 2026

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

Status: prepared; final qualification, acceptance and monitor receipts are recorded
below only after their checks pass.

The retained conversation identity is the verified explicit `deepseek-v4-pro`,
with the original Kimi/Flash/OSS fallback order. An initial partial-proof helper
incorrectly assumed the default Flash primary and rejected before its phase marker,
output or paid call; that failed helper evidence remains retained. The corrected
proof checks the actual Pro identity and retains all original profile-equality checks.
