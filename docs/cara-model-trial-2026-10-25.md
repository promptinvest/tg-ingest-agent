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
| Conversation, task planning and synthesis | GPT-6.1 Sol |

The dormant improvement-evaluator profile also selects Sol; it has no production
caller and is not claimed as functionally qualified. Existing fallback lists keep
their original order; DeepSeek Flash is appended where absent. Vision remains
Llama 4 Maverick; embeddings remain BGE-M3 and speech remains local Whisper.
Sol conversation alone uses 640 output tokens. Sonnet at320 truncated two
measured answers; at640 its original corrective path lost task context or returned
a generic containment response. Both failed cohorts remain retained. All other token limits, timeouts, temperatures, prompts,
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
activate. `attempt2` retains the failed Sonnet640 cohort. `attempt3` is the Sol640 trial. Original environment hash
`e1ae7f6c65e4076a1a6fb93a87ecf200eb59745f048183acbbea389cfa64b733`;
selected candidate hash is recorded with acceptance below.
The [operator runbook](../ops/model-trial/README.md) contains guarded commands.
Started controllers/cohorts are one-shot; read receipts and never replay them.

Twenty-four synthetic RU/EN cases cover eight active profiles through real prompt
builders and isolated handler/validator replay. Host execution and outbound
messages are mocked. The corrected cohort carries only 21 cases with exact
unchanged input/profile/source bindings and makes three fresh conversation calls;
where required, it uses the existing corrective prompt and guard. Original failed
raw answers and all charged attempts remain in the shared USD5 qualification
ceiling, also subject to ordinary production caps. Transport success alone is
insufficient; complete final answers require separate semantic review.

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
restores only trial-changed model keys and the original conversation cap, preserving
later unrelated environment edits, all stored data, usage and conversation history.
It takes a fresh stopped backup and never restores the old database or application.
Conflicting later model/runtime changes require review before any overwrite.

Status: prepared; final qualification, acceptance and monitor receipts are recorded
below only after their checks pass.
