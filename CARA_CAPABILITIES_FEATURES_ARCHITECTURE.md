# Cara Capabilities, Features, and Architecture

Last reviewed: 2026-06-15

This document describes Cara as implemented in the `tg-ingest-agent` repository.
It is based on the current code, `README.md`, `SOLUTION.md`, `CLAUDE.md`, and
`prompts/cara_persona.md`. Codegraph was attempted for this sibling repository,
but it is not initialized there, so this document was prepared from direct code
and documentation inspection.

No secret values are included here.

## Executive Summary

Cara (`@cara_assist_bot`) is a private Telegram assistant for one operator, called
the "boss" in the product language. She runs as a single stdlib-only Python 3
systemd service on Pilot-VPS, uses Telegram long polling rather than webhooks,
and has no inbound web surface. The assistant accepts Russian or English text,
voice notes, forwarded posts, photos, documents, URLs, and Telegram reactions.

The central architecture is:

1. Store inbound content first in SQLite.
2. Route ordinary text and transcribed voice through a closed-world intent router.
3. Dispatch to explicit skill modules only.
4. Confirm state-changing work conversationally before it becomes final.
5. Send every model, embedding, and STT call through one budget-guarded LLM gateway.
6. Keep persona warm and human-like in style while safety, permissions, budgets,
   confirmations, and truthfulness remain hard rules.

Cara is not a generic unrestricted Telegram GPT wrapper. Current code does include
a bounded `converse` action for warm free-form conversation, but it is read-only:
real actions still go through the skill dispatcher and confirmation flows.

## Runtime Topology

```text
Telegram private chat
  text, voice notes, forwarded posts, albums, photos, documents, reactions
        |
        | long polling via getUpdates, no webhook, owner-only gate
        v
tg_ingest_agent.Agent
  poll loop, album buffering, scheduler ticks, dispatch, pending actions
        |
        +-- forwarded/photo/document/attachment content -> ingest flow directly
        |
        +-- own voice note -> STT -> transcript -> router
        |
        +-- ordinary text -> closed-world router
        |
        v
skill modules
  ingest, reminders, calendar, spend, review, ask, fetch, sysinfo,
  memory, boss profile, export, conversation, delete/purge, media
        |
        v
shared services
  llm.py budget gateway, store.py SQLite, storage.py media backend,
  trace.py tracing, runtime.py durable jobs, texts.py templates
```

Deployment shape:

- Host: Pilot-VPS.
- Service: `tg-ingest-agent`.
- App path: `/opt/tg-ingest-agent/`.
- Entrypoint on host: `/opt/tg-ingest-agent/agent.py`.
- State path: `/var/lib/tg-ingest-agent/`.
- Database: `/var/lib/tg-ingest-agent/ingest.db`.
- Media: `/var/lib/tg-ingest-agent/media/`.
- Env file: `/etc/tg-ingest-agent.env`, mode `0600`.
- Unit file: `/etc/systemd/system/tg-ingest-agent.service`.

The service runs as the non-root `tg-ingest` user with `NoNewPrivileges=yes`,
`ProtectSystem=strict`, `ProtectHome=yes`, `PrivateTmp=yes`, and write access
limited to `/var/lib/tg-ingest-agent`.

## Primary Capabilities

| Capability | Current behavior | State change and confirmation |
|---|---|---|
| Conversation | Warm free-form conversation as Cara in the boss's language. Uses recent turns, boss profile hints, time-of-day context, review schedule, and recent reactions. | Read-only. No state change. |
| Ingest | Stores forwarded posts, notes, photos, albums, URLs, file metadata, and supported text/PDF document text. Suggests category, summary, alternatives, and key facts. | Category must be confirmed unless an auto-confirm habit exists. |
| URL fetch | Reads public HTTP(S) pages or public `t.me` web views, extracts text, then sends it through the same ingest flow. | Fetch creates a stored item and category suggestion. |
| Semantic ask | Answers questions from the boss's own saved notes/documents only. Uses BGE-M3 embeddings, cosine retrieval, context budget, and keyword fallback. | Read-only. Refuses/qualifies when no context is found. |
| Reminders | Natural-language reminder drafts in RU/EN, one-shot/daily/weekly recurrence, firing from the poll loop, snooze/done replies after firing. | Reminder draft requires confirmation. |
| Calendar | Sends `.ics` files without setup; can insert into Google Calendar via service-account JWT if configured. | Calendar writes require explicit request/confirmed reminder. |
| Spend and budgets | Tracks model/STT/embedding cost by day/month, skill, model, and call type. Warns at 80 percent, stops model calls at 100 percent. Runtime budget overrides are supported. | Budget override writes preferences directly after explicit request. |
| Memory | Stores preferences, notes, language/timezone, owner name, auto-calendar preference, and boss-profile items. Sensitive facts require confirmation. | Consent-first for sensitive/personal memory. |
| Memory review | Daily curator proposes durable memory candidates from repeated corrections, habits, and conversation evidence. Boss confirms or skips via buttons. | Candidates become confirmed memory only after acceptance. |
| Working history | Summarizes real stored actions: filed messages, reminders, confirmed memory, relationship events. | Read-only and evidence-based. |
| Performance review | Generates deterministic daily/weekly/monthly chat reports and Markdown exports for engineering feedback. Weekly review can run on schedule. | Read-only export/write to local review/export files. |
| Media recall | Re-sends stored photos and documents by Telegram `file_id`; shows item details with links, source, dates, facts, and attachments. | Read-only Telegram sends. |
| Recategorize | Changes category for one or multiple stored items and records correction feedback for learning. | State write; explicit requested action. |
| Delete/discard | Discards a fresh suggestion, deletes selected items, or deletes multiple selected recent/id-matched items. | Item delete requires confirmation. |
| Purge | Bulk clears scopes such as all, category, messages, reminders, stats, or issues. Preserves spend history and identity preferences. | Destructive typed-phrase confirmation. |
| VPS stats | Reports read-only server load, memory, disk, uptime, agent RSS, and media footprint from `/proc`/`statvfs`. | Read-only. |
| Trace/why | Shows last routed action, confidence, and trace id; traces also stamp LLM usage and issues. | Read-only. |
| Proactive heartbeat | Suggestion-only nudges for overdue reminders, pending memory candidates, or unsorted items. Quiet-hours and daily caps apply. | Never acts by itself. |
| Reactions | Cara can react to boss messages via an optional `[[react:emoji]]` tag from conversation output; incoming boss reactions are logged and surfaced as context. | Read-only except trace/relationship/issue logs. |

## Router Actions

The router has a fixed action set. Unknown actions are rejected by validation,
and startup fails if a router action lacks a permission policy in
`skill_manifest.py`.

User-facing actions:

- `ingest`
- `reminder_create`
- `reminder_list`
- `reminder_cancel`
- `reminder_reschedule`
- `reminder_undo`
- `calendar_add`
- `spend`
- `budget_set`
- `stats`
- `categories`
- `help`
- `overview`
- `list_items`
- `item_detail`
- `item_delete`
- `recategorize`
- `list_files`
- `set_journal`
- `journal_show`
- `show_media`
- `discard`
- `vps_stats`
- `purge`
- `fetch`
- `ask`
- `issues_report`
- `report_problem`
- `proactive_prefs`
- `memory`
- `remember`
- `forget`
- `self_query`
- `persona`
- `boss_query`
- `memory_why`
- `boss_memory_update`
- `style_update`
- `trace_query`
- `memory_review`
- `working_history`
- `export`
- `review`
- `converse`

Pending/conversational glue:

- `confirm`
- `amend`
- `cancel`
- `smalltalk`
- `clarify`
- `multi_action`
- `out_of_scope`

Internal/manifest-only actions:

- `router`
- `memory_curator`
- `proactive_heartbeat`

## Skill Permission Model

`skill_manifest.py` is the canonical permission registry. Each action declares:

- risk level
- whether it uses LLMs
- whether it writes state
- whether it is destructive
- whether confirmation is required
- whether it may run proactively
- whether persona context is relevant
- capability title in English and Russian

Risk levels used:

- `read_only`
- `read_only_suggestion`
- `draft_write`
- `state_write`
- `network_read`
- `external_write`
- `destructive`
- `meta`

Important policies:

- `purge` is `destructive` and requires `typed_phrase`.
- `calendar_add` is `external_write` and requires confirmation.
- `ingest`, `fetch`, `remember`, `reminder_create`, `boss_memory_update`, and
  `style_update` require confirmation or consent-aware handling.
- `memory_curator` and `proactive_heartbeat` are internal/suggestion-only.
- Proactive execution is manifest-gated by `assert_proactive_allowed()`.
- Router coverage is enforced at startup by `assert_covers(router.ACTIONS)`.

## Core User Flows

### Text Or Voice Command Flow

1. Telegram update arrives through `getUpdates`.
2. `Agent.is_owner()` requires both chat id and sender id to be allowlisted.
3. Own voice notes are downloaded and transcribed when STT is enabled.
4. Transcript or text is stored in the conversation table.
5. Pending purge and explicit category responses are resolved deterministically.
6. Obvious greetings/thanks/how-are-you can shortcut to `converse`.
7. Other text goes through `router.route()`.
8. Router output is parsed as JSON, validated against the action set, and gated
   by confidence.
9. Dispatcher consults `skill_manifest.get_policy()`.
10. Skill code runs or creates a pending action for confirmation.
11. Replies are sent through Telegram and conversation history is updated.
12. Trace and issue tables record the path.

### Ingest Flow

1. Forwarded content, photos, documents, albums, and non-command attachments
   bypass the router and go straight to `finalize()`.
2. Albums are buffered by `media_group_id` for about `ALBUM_SETTLE_SECONDS`.
3. Supported documents are read:
   - `.md`, `.markdown`, `.txt`, and `text/*` are read as UTF-8 text.
   - PDFs get best-effort text-layer extraction through `pdftext.py`.
   - Scanned/image-only PDFs return no extracted text rather than guessed text.
4. If a file-only item has no text, attachment names become the searchable text.
5. Message row, URLs, images, files, and metadata are stored in SQLite.
6. Duplicate forwarded channel posts are detected by forward chat/message id and
   marked `duplicate` without another LLM call.
7. `ingest.suggest()` asks the LLM for category, alternatives, summary, and up
   to five key facts. Existing categories are preferred by semantic match and
   confirmed corrections are included as feedback.
8. Facts are stored, text is chunked and embedded for the `ask` skill, and the
   suggestion is shown with inline buttons plus conversational confirmation.
9. Confirmation finalizes the category, records corrections, updates the
   suggestion message, and may propose an auto-confirm habit after repeated
   same-category confirmations from one source.

### Ask Flow

1. Router selects `ask` for questions about the boss's own saved notes/plans.
2. Question is embedded with the configured embedding model.
3. Stored chunks are ranked by cosine similarity.
4. Context is capped by `ASK_TOP_K` and `ASK_CONTEXT_CHARS`.
5. If semantic search yields nothing, keyword search over saved messages is used.
6. `knowledge.build_ask_messages()` tells the model to answer only from provided
   notes and cite `#id` where useful.
7. If no context exists, an `ask_no_context` issue is logged.

### Reminder And Calendar Flow

1. Router emits a reminder draft with title, due time in UTC, and recurrence.
2. Draft is validated by `reminders.validate_draft()`.
3. Cara asks for confirmation with local-time rendering.
4. On confirmation, `reminders` row is inserted.
5. Poll-loop scheduler fires due reminders, creates a `reminder_fired` pending
   action, and either advances recurrence or marks one-shot reminders done.
6. Boss can reply "done" or snooze naturally.
7. Calendar requests become either `.ics` document sends or Google Calendar API
   inserts when `GCAL_CALENDAR_ID` and a service-account key are configured.

### Memory And Learning Flow

Memory has several layers:

- `preferences`: direct settings and notes, including language, timezone,
  owner name, auto-calendar, runtime budget overrides, and general remembered
  notes.
- `boss_profile_items`: structured facts about the boss with kind, status,
  confidence, sensitivity, source, and evidence.
- `memory_candidates`: reviewable candidates that become confirmed only after
  acceptance.
- `cara_life`: fictional persona continuity facts for Cara's own life.
- `relationship_events`: evidence-based working history.

Learning sources:

- Explicit "remember" requests.
- Repeated category corrections.
- Confirmed source auto-confirm habits.
- Conversation curation every few turns.
- Immediate curation when the boss appears to correct Cara's behavior.

Safety behavior:

- `personal_fact` has a sensitivity floor of `sensitive`.
- `identity` has a sensitivity floor of `private`.
- Sensitive/secret-like content is not casually surfaced.
- Sensitive memory becomes a confirm-first candidate or pending action.
- Prompt personalization includes only confirmed normal-sensitivity preferences.

### Proactive Flow

The proactive heartbeat is implemented in `proactive.py` and called from
`Agent.check_proactive()` on a configured interval. `PROACTIVE_ENABLED` defaults
to true in `common.py`, so deployment config should explicitly disable it if the
desired operating mode is reply-only.

The heartbeat checks:

- overdue reminders, urgent
- pending memory candidates, non-urgent
- suggested items still needing category confirmation, non-urgent

Safety rails:

- One nudge max per run.
- No state changes.
- Manifest-gated.
- Daily cap for non-urgent nudges.
- No repeat of the same nudge key in a day.
- Quiet hours by local timezone, with optional urgent bypass.
- Every send or suppression goes to `proactive_log`.

## LLM, STT, Embeddings, And Cost Control

`llm.py` is the single gateway for model usage. Skills do not call model APIs
directly.

Supported call types:

- chat completions
- embeddings
- remote STT through an OpenAI-compatible `/audio/transcriptions` endpoint
- local STT through `whisper.cpp` CLI
- local server STT through warm `whisper-server`

Default model profile behavior:

- `router_fast`: router model, JSON required, fallback `openai-gpt-4o`
- `ingest_balanced`: primary chat model, JSON required, fallback `openai-gpt-4o`
- `ask_grounded`: primary chat model, no fallback by default
- `converse_warm`: primary chat model, fallback `openai-gpt-4o`, temperature `0.7`
- `memory_curator`: primary chat model, JSON required, fallback `openai-gpt-4o`
- `review_balanced`: primary chat model, no fallback by default

Default models and pricing controls:

- Chat default: `anthropic-claude-haiku-4.5`.
- Router default: same as chat unless `ROUTER_MODEL` is set.
- Embedding default: `BGE-M3`.
- Known chat model prices are in `DEFAULT_PRICING`.
- Unknown chat models are priced conservatively.
- STT is priced per audio minute for remote mode.
- Local STT logs usage with zero model cost.
- Embeddings log estimated token cost.
- `PRICING_JSON` can override pricing.
- `LLM_PROFILES_JSON` can override model profiles.

Budget behavior:

- Daily/monthly defaults come from `BUDGET_DAILY_USD` and
  `BUDGET_MONTHLY_USD`.
- Runtime overrides can be stored in preferences by `budget_set`.
- At 80 percent of daily or monthly budget, Cara sends one warning per period.
- At 100 percent, LLM/STT/embedding calls stop with `BudgetExceeded`.
- Budget hard-stop does not trigger model fallback.
- Stored messages remain stored and retry later.

## Data Model

The SQLite database uses WAL mode via `store.open_db()`. Schema migrations are
additive through `_migrate()`.

Core tables:

- `kv`: generic key/value runtime state, offsets, deploy version, schedules.
- `categories`: confirmed taxonomy with casefolded `norm_key`.
- `messages`: stored Telegram/web items and lifecycle status.
- `urls`: URLs per message.
- `images`: Telegram photo/image metadata, local path, optional object key.
- `files`: Telegram document/attachment metadata and file ids.
- `facts`: key facts extracted during ingest.
- `chunks`: embedded text chunks for semantic ask.
- `preferences`: direct memory/preferences/settings.
- `pending_actions`: per-chat pending confirmation with TTL.
- `conversation`: recent user/bot turns.
- `reminders`: active/done/cancelled reminders.
- `feedback`: operator corrections for learning.

Spend and model reliability:

- `llm_usage`: model/STT/embed usage, tokens, seconds, cost, trace id.
- `model_cooldowns`: profile/model failover cooldowns.

Personality, memory, and relationship:

- `self_facts`: deterministic Cara self-knowledge.
- `boss_profile_items`: confirmed/inferred/deprecated facts about the boss.
- `memory_candidates`: reviewable memory candidates.
- `relationship_events`: evidence-based working history.
- `cara_life`: fictional continuity facts for Cara's persona.

Observability and background work:

- `traces`
- `trace_events`
- `issues`
- `events`
- `jobs`
- `proactive_log`

Message lifecycle:

- `pending`: stored, waiting for LLM suggestion or retry.
- `suggested`: suggestion sent, waiting for confirmation.
- `confirmed`: category final.
- `failed`: LLM attempts exhausted.
- `duplicate`: re-forward of an already stored channel post.

Purge behavior:

- `all` clears messages, reminders, categories, issues, feedback, and related
  rows/media but preserves `llm_usage` and preferences.
- `messages` clears saved items while preserving categories, reminders, spend,
  and preferences.
- `category` clears messages in one category and prunes that category.
- `stats` clears categories/feedback style stats without deleting messages.
- `reminders` clears reminders.
- `issues` clears issue records.

## Module Map

Core runtime:

- `tg_ingest_agent.py`: main `Agent`, poll loop, dispatch, scheduler ticks,
  pending actions, ingest finalization, Telegram send helpers.
- `common.py`: config loading, language detection, logging, trace context,
  multipart builder, STT-noise detection, reaction palette.
- `tg_api.py`: minimal Telegram Bot API client and file/photo/document helpers.
- `texts.py`: bilingual deterministic reply templates and tone intensity.
- `store.py`: SQLite schema, migrations, and persistence helpers.

Routing and permissions:

- `router.py`: closed-world LLM intent router and route validation.
- `skill_manifest.py`: permission/risk registry and capability titles.
- `action_truth.py`: test-only guard for final-action wording.

Model and AI:

- `llm.py`: DO Gradient gateway, budgets, pricing, profiles, failover,
  embeddings, local/remote STT, JSON parsing.
- `knowledge.py`: chunking, cosine ranking, grounded ask prompt.
- `pdftext.py`: best-effort stdlib PDF text-layer extraction.

Skills:

- `ingest.py`: URL extraction, forward-origin parsing, LLM category prompt,
  suggestion keyboards.
- `reminders.py`: reminder validation, recurrence, local-time formatting.
- `gcal.py`: `.ics` export and Google Calendar service-account insert.
- `spend.py`: AI usage summaries.
- `review.py`: performance reviews and Markdown exports.
- `fetch.py`: SSRF-guarded HTTP(S)/public Telegram page fetch.
- `sysinfo.py`: read-only VPS resource report.
- `storage.py`: local media and optional DO Spaces S3-compatible offload.

Persona and memory:

- `converse.py`: free-form warm conversation prompt and Cara life seed/context.
- `persona.py`: prompt layer ordering and safe boss preference hints.
- `self_model.py`: deterministic self/capability answer.
- `boss_model.py`: confirmed/inferred boss profile, sensitivity handling.
- `memory_curator.py`: deterministic and LLM-assisted memory candidate curation.
- `relationship.py`: evidence-based working history.
- `proactive.py`: suggestion-only heartbeat.

Durable background work and observability:

- `trace.py`: trace ids and stage events.
- `events.py`: persistent event/audit queue primitive.
- `jobs.py`: persistent background job queue.
- `runtime.py`: job handler registry and drain loop.

Operational helper scripts:

- `deploy.sh`: one-connection deploy/test/pull/rollback workflow.
- `install-tg-ingest-agent-pilot-remote.sh`: idempotent remote installer.
- `install-whisper-pilot-remote.sh`: local Whisper setup.
- `apply_token.py`, `apply_do_key.py`, `bootstrap_chat_id.py`: one-shot secret
  or allowlist helpers.
- `list_models.py`, `stt_probe.py`: inference/STT diagnostics.

## Configuration Surface

Required:

- `TELEGRAM_BOT_TOKEN`
- `ALLOWED_CHAT_IDS`
- `DO_MODEL_ACCESS_KEY`

Conversation and routing:

- `BOT_LANGUAGE`
- `TIMEZONE_OFFSET_HOURS`
- `CARA_TIMEZONE_OFFSET_HOURS`
- `ROUTER_CONFIDENCE_THRESHOLD`
- `ROUTER_MODEL`

Model/inference:

- `DO_CHAT_MODEL`
- `DO_EMBEDDING_MODEL`
- `DO_INFERENCE_BASE_URL`
- `LLM_TIMEOUT_SECONDS`
- `LLM_MAX_ATTEMPTS`
- `LLM_PROFILES_JSON`
- `LLM_FALLBACK_COOLDOWN_SECONDS`
- `PRICING_JSON`

Voice:

- `STT_ENABLED`
- `STT_MODE` (`local`, `local_server`, or `remote`)
- `STT_MODEL`
- `STT_LANGUAGE`
- `WHISPER_BIN`
- `WHISPER_MODEL`
- `WHISPER_SERVER_URL`
- `STT_LOCAL_TIMEOUT_SECONDS`

Budgets:

- `BUDGET_DAILY_USD`
- `BUDGET_MONTHLY_USD`

Storage and data:

- `DB_PATH`
- `MEDIA_DIR`
- `STORAGE_BACKEND`
- `SPACES_REGION`
- `SPACES_BUCKET`
- `SPACES_ENDPOINT`
- `SPACES_KEY`
- `SPACES_SECRET`
- `SPACES_PREFIX`

Ingest:

- `CATEGORIES`
- `CATEGORIES_FILE`
- `FALLBACK_CATEGORY`
- `ALBUM_SETTLE_SECONDS`
- `MAX_LLM_IMAGES`
- `RETRY_INTERVAL_SECONDS`
- `HABIT_THRESHOLD`
- `CHUNK_CHARS`

Ask:

- `ASK_TOP_K`
- `ASK_CONTEXT_CHARS`

Fetch:

- `FETCH_ENABLED`
- `FETCH_TIMEOUT_SECONDS`
- `FETCH_MAX_BYTES`

Calendar:

- `GCAL_CALENDAR_ID`
- `GCAL_SA_KEY_FILE`
- `EVENT_DURATION_MINUTES`

Review and proactive:

- `REVIEW_WEEKDAY`
- `REVIEW_HOUR`
- `REVIEW_KEEP`
- `PROACTIVE_ENABLED`
- `QUIET_HOURS_START`
- `QUIET_HOURS_END`
- `PROACTIVE_MAX_PER_DAY`
- `PROACTIVE_URGENT_BYPASS_QUIET`
- `PROACTIVE_INTERVAL_SECONDS`

Persona:

- `PERSONALITY_INTENSITY`

Telegram polling:

- `POLL_TIMEOUT_SECONDS`

## Security And Guardrails

Access control:

- A message/callback/reaction is processed only if both chat id and sender user
  id are allowlisted.
- This prevents strangers in the private chat from using the bot and prevents
  the boss's account from controlling the bot in another chat/group.

Network posture:

- Telegram long polling only.
- No webhook.
- No inbound application port.
- Pilot-VPS firewall can remain SSH-only for this service.

Prompt-injection defenses:

- User/router input is wrapped in `<user_request>`.
- Forwarded/stored/message content is wrapped as untrusted data.
- Stored notes in ask prompts are untrusted and cannot issue instructions.
- Router outputs JSON actions only and never answers the user directly.

Confirmation model:

- Category suggestions, reminders, calendar writes, memory, delete, and purge
  use pending actions or explicit deterministic checks.
- Bulk purge is confirmed only by the exact typed phrase, not by a generic yes.
- Pending actions expire and are swept by maintenance.

Truthfulness:

- Persona must not claim real task completion before deterministic state writes.
- `action_truth.py` pins template wording in tests.
- Cara may be warm and characterful, but operational claims must stay grounded.

Budget safety:

- Budget checks run before model/STT/embedding calls.
- Budget stop is authoritative and cannot be bypassed by fallback.
- Warnings are deduped per budget period.

SSRF safety:

- `fetch.py` allows only HTTP(S).
- URL credentials are blocked.
- DNS resolution is checked.
- Private, loopback, link-local, reserved, multicast, unspecified, and metadata
  IPs are rejected.
- Every redirect hop is revalidated.
- Unsupported content types are rejected.

Secrets:

- Secrets belong in `/etc/tg-ingest-agent.env` or other configured secret files.
- The installer preserves existing env files and sets env mode `0600`.
- HTTP errors redact bearer/access keys where applicable.
- This document intentionally contains no raw secret material.

## Observability

Cara records:

- one trace per inbound update and scheduler/job unit
- trace events for routing, fallbacks, issues, and completion
- model usage rows with trace id, skill, kind, model, tokens/seconds, and cost
- issue rows by kind and detail
- relationship events for meaningful grounded work
- proactive send/suppression decisions
- durable job status and retry errors
- completed/failed Telegram-message events

User-facing observability:

- "why did you do that" shows the latest trace id, action, and confidence.
- Issues reports summarize recent failures.
- Performance review reports activity, learning, issues, model fallbacks, spend,
  trace outcomes, and improvement backlog.
- Exports can produce Markdown for self profile, boss profile, working history,
  memory candidates, trace summary, or review.

## Deployment And Operations

`deploy.sh` supports:

- default deploy: tar working tree to stage, run tests, install, verify service
- `--test`: stage and run tests only
- `--pull`: host pulls `origin/main`, tests, installs, verifies
- `--rollback <sha|branch>`: host checks out a ref, installs, verifies

Installer behavior:

- Requires root.
- Verifies required staged modules exist.
- Backs up env, unit, and app directory under
  `/root/codex-hardening-backups/<timestamp>-tg-ingest-agent/`.
- Installs Python 3, CA certificates, and SQLite.
- Creates non-root `tg-ingest` user if absent.
- Installs `tg_ingest_agent.py` as `agent.py` plus all modules.
- Writes a content hash to `VERSION`; Cara announces only real code changes.
- Creates env file with placeholders only if absent.
- Compiles installed Python files.
- Enables systemd service.
- Stops service if `=REPLACE_ME` placeholders remain; otherwise restarts it.

Operational gotchas:

- Only one poller can use a Telegram bot token. Running tests or manual
  `getUpdates` against the production token can cause HTTP 409 conflicts.
- Local Windows Python may be unavailable in the operator environment; repo
  guidance prefers running tests on the VPS stage dir.
- Do not push or print raw bot tokens, DO keys, Google keys, chat IDs, or other
  secrets.

## Tests

The repository currently has 241 offline unit tests across `test_*.py`.

Test coverage includes:

- config parsing
- URL extraction with UTF-16 Telegram offsets
- LLM JSON parsing and category matching
- ingest prompt construction, image caps, facts, retry/fallback
- callback keyboards
- forward-origin parsing and source links
- SQLite lifecycle, migrations, purge, duplicates, facts, chunks, habits
- templates and bilingual formatting
- budget states and usage aggregation
- local/remote/local-server STT dispatch
- model profile failover and cooldowns
- router validation, pending action guards, smalltalk shortcuts
- reminders and recurrence
- calendar `.ics`, Google payloads, JWT construction
- reviews and exports
- item listing/details/media resend
- recategorization, delete, typed purge confirmation
- fetch SSRF guards and text extraction
- skill manifest coverage and proactive safety
- trace lifecycle and current trace stamping
- persona layer ordering and action-truth constraints
- free-form conversation dispatch
- name handling and language detection
- boss profile sensitivity handling
- STT hallucination/noise rejection
- conversation learning and correction escalation
- proactive quiet hours, caps, urgency, logging
- durable jobs and maintenance
- reaction handling and context
- document/PDF ingestion
- access control
- self model and memory curator
- event/job queues
- semantic ask and embeddings
- storage signing/offload
- sysinfo parsing/reporting

Standard test command:

```powershell
python -m unittest discover -p "test_*.py" -v
```

Project-local guidance says to run this on the VPS stage dir when the Windows
workstation lacks a real Python interpreter or OneDrive performance is poor:

```bash
python3 -m unittest discover -p 'test_*.py'
```

## Known Limits And Dormant Features

Current known limits:

- Telegram bots cannot read arbitrary private channel history by URL; forwarding
  remains the reliable path.
- Only one Telegram poller may run per bot token.
- Reminder recurrence is limited to one-shot, daily, and weekly.
- Images sent as document-style image files are stored metadata-only and not sent
  to the vision LLM.
- Non-image attachments such as voice/audio/video are stored as fetchable files
  when forwarded, not parsed as commands.
- Own voice notes are commands and are transcribed; forwarded voice/audio is
  content and is not transcribed.
- PDF extraction is best-effort text-layer extraction, not OCR.
- Fetch supports public HTTP(S) text/HTML and public Telegram web views, not
  private channels, binaries, or file shares.
- Local media is the default durable store; remote object storage is optional.

Dormant/config-gated features:

- Google Calendar direct sync is dormant until calendar id and service-account
  key are configured. `.ics` export works without it.
- DO Spaces media offload is dormant until `STORAGE_BACKEND=spaces` and
  `SPACES_*` credentials are configured.
- Remote STT depends on an available OpenAI-compatible transcription endpoint.
- Local Whisper modes require host setup through the Whisper installer.

Potential documentation drift to watch:

- `CLAUDE.md` says the proactive heartbeat is intentionally not enabled and Cara
  is reply-only. Current code calls `check_proactive()` and `common.py` defaults
  `PROACTIVE_ENABLED` to true. Treat the live deployment setting as controlled by
  `/etc/tg-ingest-agent.env` and update `CLAUDE.md` if the intended posture is
  now proactive.

## Source Files Inspected

Primary product docs:

- `README.md`
- `SOLUTION.md`
- `CLAUDE.md`
- `prompts/cara_persona.md`

Primary code:

- `tg_ingest_agent.py`
- `router.py`
- `skill_manifest.py`
- `store.py`
- `common.py`
- `llm.py`
- `ingest.py`
- `knowledge.py`
- `fetch.py`
- `memory_curator.py`
- `proactive.py`
- `review.py`
- `gcal.py`
- `storage.py`
- `runtime.py`
- `events.py`
- `jobs.py`
- `pdftext.py`
- `boss_model.py`
- `self_model.py`
- `persona.py`
- `action_truth.py`
- `relationship.py`
- `sysinfo.py`
- `trace.py`
- `tg_api.py`

Operational files:

- `tg-ingest-agent.env.example`
- `tg-ingest-agent.service`
- `deploy.sh`
- `install-tg-ingest-agent-pilot-remote.sh`

Test inventory:

- `test_tg_ingest_agent.py`
- `test_assistant.py`
