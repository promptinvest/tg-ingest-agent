# Solution Description: tg-ingest-agent

A personal conversational AI assistant living in Telegram (`@cara_assist_bot`),
self-hosted on a 1 vCPU / 2 GB DigitalOcean droplet (Pilot-VPS), with all
model inference on DigitalOcean Gradient serverless inference. The operator
talks to it in free-form Russian or English — text or voice — with no slash
commands; a closed-world router assigns each request to a specialized skill,
and anything that changes state is confirmed conversationally before it
becomes final.

## Design principles

1. **One bot, one process, skills as modules.** Telegram allows a single
   `getUpdates` poller per token, and a 1-vCPU box does not need a fleet of
   daemons. The orchestrator is an intent router inside one systemd service;
   each skill is a Python module behind it. A skill graduates to its own
   process only when it earns it.
2. **Store first, think second.** Every inbound message is persisted to
   SQLite before any model call. LLM outages, budget stops, and restarts
   never lose data — pending work is retried on a sweep.
3. **Suggest, then confirm.** The model proposes (a category, a parsed
   reminder, a learned habit); nothing enters the taxonomy, the schedule, or
   the bot's memory without operator confirmation — by natural reply
   («да», «нет, лучше крипта», «через полчаса») or an inline button.
4. **Scoped, not chatty.** The router has a closed action set with no
   generic "chat" action; the LLM only ever emits JSON into validated slots,
   and all user-facing text comes from bilingual templates. Forwarded content
   is wrapped in delimiters and treated as untrusted data (prompt-injection
   defense). Off-topic requests get a one-line capabilities reply.
5. **Every token is metered.** All chat/STT calls pass through one gateway
   that prices them against a model price table and logs them; daily and
   monthly budgets warn at 80% and hard-stop at 100%.
6. **Zero inbound surface.** Long polling only: the host firewall stays
   SSH-only, no webhooks, no reverse proxy, no Docker. Stdlib-only Python —
   no pip dependencies to patch.

## Architecture

```
 Telegram (text / voice / forwarded posts)
        │  long polling, no inbound ports
        ▼
 ┌─ agent.py ── poll loop · album buffering · pending actions · scheduler ─┐
 │                                                                         │
 │  voice ──► STT (DO inference)*──► text ─┐                               │
 │  forwarded/media ──────────────────────┼──► ingest skill (no router)   │
 │  free text ──► router.py (closed-world LLM intent classification)      │
 │                   │                                                     │
 │     ┌─────────────┼──────────────┬───────────────┬──────────────┐      │
 │     ▼             ▼              ▼               ▼              ▼      │
 │  ingest.py    reminders.py   spend.py      memory (prefs)   gcal.py    │
 │  categorize   NL-parsed      usage stats   remember/forget  .ics or    │
 │  + summarize  one-shot/      + budgets     language, tz,    Google     │
 │  + confirm    recurring                    notes, habits    Calendar   │
 │     └─────────────┴──────────────┴───────────────┴──────────────┘      │
 │                          │                                              │
 │                  llm.py — budget-guarded gateway                        │
 │                  (prices + logs every call to llm_usage)                │
 │                          │                                              │
 │                  store.py — SQLite (WAL)                                │
 └─────────────────────────────────────────────────────────────────────────┘
                            │
              DigitalOcean Gradient serverless inference
              (default anthropic-claude-haiku-4.5, vision-capable)
```

\* STT pending: DO's catalog currently exposes no transcription model to
this key; voice degrades gracefully (see Known gaps).

## Skills

| Skill | What it does | Confirmation |
|---|---|---|
| **Ingest** | Stores forwarded posts/notes: text, URLs (UTF-16-entity-safe extraction), photos (albums = one logical message), forward origin. LLM suggests a category from the operator-confirmed taxonomy (matching by meaning across RU/EN) + a summary in the source language. Duplicates of re-forwarded posts are detected via forward origin and skip the LLM. | Category confirmed by reply or button; corrections are logged as feedback. |
| **Reminders** | NL time parsing in both languages («послезавтра в 15», "every Monday at 9"), one-shot/daily/weekly, fired from the poll loop (~1 min precision), snooze/close by natural reply. Survives restarts and the nightly host reboot. | Draft echoed with local time before scheduling. |
| **Calendar** | "добавь в календарь..." — sends an .ics file (works with zero Google setup) or inserts directly into Google Calendar via a service account (RS256 JWT signed with the system openssl; dormant until a key file + calendar id are configured). `auto_calendar` preference syncs every confirmed reminder. | Uses confirmed reminders or explicit times. |
| **Spend** | Answers "сколько потратили на AI за месяц?" with totals, per-skill and per-model breakdowns, and budget status. Budgets enforced in the gateway: warn at 80%, hard stop at 100% (messages still stored; processing resumes after reset). | — |
| **Memory** | "запомни: ...", "что ты обо мне знаешь?", "забудь...". Special keys: `language` (ru/en), `timezone_offset`, `auto_calendar`; everything else stored as notes. | Explicit request = consent; entries are listable and deletable. |
| **Introspection** | "что ты умеешь?" (capabilities), "что у тебя есть?" (digest: message counts, top categories, reminders, memory size, spend), "покажи сохранённое про X / в категории Y" (browse stored items). | — |
| **Issue report** | Logs every communication failure (out-of-scope, unclear requests, failed transcriptions, model errors, budget stops, failed classifications, calendar errors) and summarizes them — automatically every 7 days and on demand ("какие были проблемы на неделе?"). | — |

## Learning (in-context, no model training)

- **Correction feedback**: when the operator overrides a suggested category,
  the pair (suggested → corrected) is stored and injected into future
  suggestion prompts as few-shot examples.
- **Source habits**: after N (default 10) consecutive same-category
  confirmations from one channel, the bot offers to auto-confirm that source;
  accepted habits become preferences, declined ones are not re-asked.
- **Preferences**: distilled facts (reply language, timezone, notes) injected
  into prompts; fully auditable and deletable by the operator.
- **Issue log**: the out-of-scope/unclear stream doubles as a backlog —
  recurring refused requests indicate the next skill worth building.

## Data model (SQLite, WAL)

`messages` (status: pending → suggested → confirmed | failed | duplicate;
unique per chat+message id for redelivery dedup) · `urls` · `images` ·
`categories` (canonical names; dedup via Python `casefold` — SQLite NOCASE is
ASCII-only and would split «Крипта»/«крипта») · `reminders` ·
`llm_usage` (ts/skill/kind/model/tokens/cost) · `feedback` · `preferences` ·
`pending_actions` (per-chat, TTL) · `conversation` (last 30 turns/chat for
router context) · `issues` · `kv` (poll offset, flags).

## Security

- Chat-ID allowlist; unknown senders are logged and ignored.
- Closed router action set; JSON-only model output; template-only replies;
  untrusted-content delimiters; confidence gate (clarify below threshold).
- Secrets in `/etc/tg-ingest-agent.env` (0600), staged via files during
  rotation — never in command lines, shell history, or the journal; access
  keys redacted from logged HTTP errors.
- systemd hardening: non-root user, `NoNewPrivileges`, `ProtectSystem=strict`,
  `PrivateTmp`, writable only in `/var/lib/tg-ingest-agent`.
- Dedicated bot token and dedicated DO inference key (independent billing
  attribution and revocation).

## Operations

- Deploy: `scp` + idempotent installer (backs up replaced files, preserves
  env, `py_compile` gate, auto-restarts only when secrets are complete).
- Tests: 63 offline unit tests (no network; temp SQLite), run on the VPS.
- Observability: journald logs (routing decisions with confidence, per-row
  lifecycle), `llm_usage` for spend, `issues` for failure modes, weekly
  issue digest pushed to the operator.
- Runtime footprint: ~14 MB RSS, far below the 2 GB host.

## Known gaps / roadmap

- **Voice (STT)**: DO serverless inference does not currently expose a
  transcription model to this key (`/v1/models` lists none); voice notes are
  stored and politely declined. Candidates: DO adding Whisper/fal audio
  models, `nemotron-3-nano-omni` audio input via chat, or an external STT
  key. Code path exists (`llm.transcribe`) and activates via `STT_MODEL`.
- Google Calendar sync dormant until the service-account key is provisioned.
- Recurrence limited to daily/weekly; media disk pruning not yet needed;
  image-as-document files stored metadata-only.
- Semantic search over stored items (BGE-M3 embeddings, multilingual) is the
  natural next step for "найди тот пост про..." beyond substring matching.
