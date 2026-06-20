# Cara — Solution Specification

**Cara** (`@cara_assist_bot`) is a personal conversational AI assistant living
in Telegram, self-hosted on a 1 vCPU / 2 GB DigitalOcean droplet (Pilot-VPS).
All model inference runs on DigitalOcean Gradient serverless inference. Her
owner ("boss") talks to her in free-form **Russian or English — text or voice,
no slash commands**. She **converses warmly like a person**, and for anything
operational a closed-world router assigns the request to a specialized skill;
anything that changes state is confirmed conversationally before it becomes
final. She replies only to her owner, in whichever language he wrote in.

Stdlib-only Python 3, long polling (no inbound ports), one systemd service.

---

## 1. Design principles

1. **One bot, one process, skills as modules.** Telegram allows a single
   `getUpdates` poller per token, and a 1-vCPU box does not need a fleet of
   daemons. The orchestrator is an intent router inside one systemd service;
   each skill is a Python module behind it. Background work (curation, retries,
   cleanup) runs as durable jobs in the same process.
2. **Store first, think second.** Every inbound message is persisted to SQLite
   before any model call. LLM outages, budget stops, and restarts never lose
   data — pending work is retried on a sweep. Every model call funnels through
   one gateway (`llm.py`) that converts *all* transport faults — HTTP errors,
   unreachable hosts, **and bare socket read-timeouts** (`response.read()` raises
   a raw `TimeoutError`, not a `URLError`) — into a single `LLMError` type, so a
   slow embedding/vision call can never escape a skill's `except LLMError` and
   crash the whole update handler (which would leave the boss with no reply).
3. **Suggest, then confirm.** The model proposes (a category, a parsed reminder,
   a learned fact, a bulk delete); nothing enters the taxonomy, the schedule, the
   calendar, or durable memory without confirmation — by natural reply («да»,
   «нет, лучше крипта», «через полчаса») or an inline button.
4. **Conversational, but safe by construction.** Cara talks like a real person —
   warm, free-form, with her own life (§5). What stays locked down no matter how
   human she sounds: every state change is confirmed; a **permission manifest**
   gates what each skill may do; forwarded/quoted content is untrusted; budgets
   hard-stop; and she truthfully never claims a real task was done when it
   wasn't. Conversation and grounded Q&A are LLM-generated; **transactional and
   system messages stay deterministic templates.**
5. **Owner-only.** Cara answers exactly one person: a message is acted on only
   when **both** the chat and the sender's account are on the allowlist. A
   stranger sharing a chat can't reach her, and the owner's account isn't acted
   on in any other chat (e.g. a group the bot is added to).
6. **Speak his language.** Each reply matches the language of the incoming
   message (Cyrillic → Russian, Latin → English), falling back to the stored
   preference (default Russian) only when a message has no letters to judge by.
7. **Every token is metered.** All chat / STT / embedding calls pass through one
   budget-guarded gateway that prices and logs them, with model profiles and
   failover; daily and monthly budgets warn at 80 % and hard-stop at 100 %.
   Per-model prices (DO Gradient rates) live in `llm.DEFAULT_PRICING`, overridable
   per-deploy via `PRICING_JSON`; **a model slug missing from the table is billed
   at the conservative $3/$15 default**, which over-counts cheap open-weight models
   3-10× and trips the budget on phantom dollars — so every model Cara runs must
   be listed (regression-tested).
8. **Zero inbound surface.** Long polling only: the host firewall stays SSH-only,
   no webhooks, no reverse proxy, no Docker, no pip dependencies.
9. **Auditable.** Every inbound update and scheduler tick runs under a trace id
   that stamps its model calls and issues; failures, fallbacks, and proactive
   decisions are all logged.
10. **Analyze → argue → build.** New capabilities are assessed against the
    architecture first; the agent-or-not question is asked per feature; genuine
    options or guardrail changes are surfaced for sign-off before coding.
    (Operator working agreement, `CLAUDE.md`.)

---

## 2. Architecture

```
 Telegram (text · voice · forwarded posts · photos · documents)
        │  long polling, no inbound ports · owner-only (chat AND sender)
        ▼
 ┌─ agent.py ─ poll loop · album buffering · pending actions · scheduler · jobs ──┐
 │                                                                                │
 │  voice ─► STT (local whisper-server) ─► text ─┐                                │
 │  forwarded / photo / document ────────────────┼─► ingest skill (no router)     │
 │  free text / transcribed voice ─► router.py (closed-world LLM intent)          │
 │                     │  (recent-turn context; per-message language)             │
 │   ┌──────────┬──────┴───┬─────────┬─────────┬─────────┬─────────┬─────────┐    │
 │   ▼          ▼          ▼         ▼         ▼         ▼         ▼         ▼    │
 │ converse  ingest    reminders   spend    review    ask      fetch    sysinfo  │
 │ (warm     +facts    +calendar   +budget  +schedule (KB Q&A) (URL)   (vps stat)│
 │  chat)    +files    (gcal)                +export                             │
 │   │   self · persona · boss profile · memory review · working history · trace  │
 │   └─── show_media · discard · item_delete · purge · issues · stats · export ───┘
 │                          │                                                     │
 │  skill_manifest.py — permission gate (risk · confirmation · proactive)         │
 │  trace.py — one trace per update/tick   runtime.py — durable job drain         │
 │                          │                                                     │
 │                  llm.py — budget-guarded gateway (profiles + failover)         │
 │              (chat · embeddings BGE-M3 · STT; prices + logs every call)         │
 │                          │                                                     │
 │           store.py — SQLite (WAL)        storage.py — binaries                 │
 │                                          (local default / DO Spaces)           │
 └────────────────────────────────────────────────────────────────────────────────┘
                            │
              DigitalOcean Gradient serverless inference
   (chat: anthropic-claude-haiku-4.5 · fallback openai-gpt-4o · embeddings: BGE-M3)
```

### Module map

| Module | Responsibility |
|---|---|
| `tg_ingest_agent.py` | entry point: poll loop, dispatch, pending-action resolution, scheduler ticks, maintenance jobs (installed as `agent.py`) |
| `router.py` | closed-world intent router (LLM, JSON-only, confidence gate, context recall) |
| `converse.py` | **free-form warm conversation as Cara** — persona, her evolving life, boss facts, language matching. A reply may open with a `[[react:emoji]]` tag → a Telegram reaction; the handler also tolerates models that instead return a `["emoji", "text"]` array, unwrapping it to a reaction + clean text so the raw literal never reaches the boss. |
| `ingest.py` | message parsing, URL extraction (UTF-16-safe), category + facts + summary suggestion |
| `pdftext.py` | best-effort PDF text-layer extraction (pdfminer.six, with a stdlib regex fallback) |
| `knowledge.py` | document chunking, cosine retrieval, grounded-answer prompt (the `ask` skill) |
| `reminders.py` | reminder drafts, recurrence, local-time rendering |
| `gcal.py` | Google Calendar (service-account JWT) + .ics export |
| `spend.py` | AI-usage aggregation and reports |
| `review.py` | performance review (chat + Markdown exports), weekly schedule, trace summary |
| `self_model.py` | Cara's deterministic self-knowledge (capabilities/limits, never invented) |
| `boss_model.py` | structured boss profile (confirmed vs inferred, sensitivity floors, address resolution) |
| `memory_curator.py` | proposes memory candidates from evidence + learns from conversation |
| `relationship.py` | grounded working history (real events, never fabricated) |
| `persona.py` | prompt-layer ordering (persona sits below all rule layers) |
| `proactive.py` | suggestion-only heartbeat (nudges, throttle, quiet hours) |
| `skill_manifest.py` | permission registry: per-skill risk, confirmation mode, proactive eligibility |
| `trace.py` | structured tracing (one trace per update/tick; stages) |
| `events.py` / `jobs.py` / `runtime.py` | durable event/job tables + handler registry + drain |
| `action_truth.py` | guard so "done/saved/scheduled" wording can't precede the DB write |
| `sysinfo.py` | read-only host stats from `/proc` + statvfs (no root, no shell) |
| `fetch.py` | remote URL reader with SSRF guard |
| `storage.py` | binary backend: local default; DO Spaces (S3 SigV4 in stdlib), dormant |
| `llm.py` | DO Gradient gateway: chat profiles + failover + cooldowns, embeddings, STT, pricing, budgets |
| `store.py` | SQLite schema + helpers; additive migrations |
| `tg_api.py` | Telegram Bot API client (send message/photo/document, **reactions**, getFile) |
| `texts.py` | bilingual (ru/en) reply templates with tone/intensity variants — Cara's transactional voice |
| `common.py` | config loading, language detection, shared helpers |

### Skill permission model

`skill_manifest.py` is the canonical permission registry; startup fails if any router
action lacks a policy (`assert_covers(router.ACTIONS)`). Each action declares its risk,
whether it uses an LLM / writes state / is destructive / needs confirmation / may run
proactively / wants persona context, and a bilingual title.

Risk levels: `read_only` · `read_only_suggestion` · `draft_write` · `state_write` ·
`network_read` · `external_write` · `destructive` · `meta`. Key policies: `purge` is
`destructive` (typed-phrase confirm); `calendar_add` is `external_write` (confirm);
`ingest` / `fetch` / `remember` / `reminder_create` / `boss_memory_update` /
`style_update` are confirmation- or consent-gated; `set_journal`/`report_problem` are
plain `state_write`; `memory_curator` / `proactive_heartbeat` are internal,
suggestion-only; proactive execution is gated by `assert_proactive_allowed()`.

### Core flows

**Command lifecycle:** update via `getUpdates` → owner check (chat *and* sender id) →
own voice transcribed if enabled → text stored → deterministic short-circuits (pending
purge, explicit category, greetings → `converse`) → `router.route()` → output parsed as
JSON, validated against the closed action set, confidence-gated → dispatcher consults
`skill_manifest.get_policy()` → skill runs or stages a pending action for confirmation →
reply sent; conversation, trace and issue tables updated.

**Ingest lifecycle:** forwards / photos / documents / albums bypass the router to
`finalize()` → albums buffered by `media_group_id` → text & markdown read as UTF-8, PDFs
extracted (pdfminer.six → regex fallback; scanned / glyph-coded → no text, so the
filename becomes the searchable text) → rows stored → duplicate channel posts detected
by forward ids (no LLM) → `ingest.suggest()` proposes category / alternatives / summary /
≤5 facts (existing categories preferred, confirmed corrections fed back) → facts stored,
text chunked + embedded for `ask` → suggestion shown with buttons + conversational
confirm → confirmation finalises the category (a journal category acks as a dated
entry), logs any correction, and may propose an auto-confirm habit.

Ask, reminders/calendar, memory/learning and the proactive heartbeat are detailed in
§3–§7.

---

## 3. Capabilities

| Capability | What it does | Confirmation |
|---|---|---|
| **Conversation** | Greetings, smalltalk, anything personal/emotional or not a concrete task → a warm, free-form reply in Cara's own voice (`converse`), in the boss's language. Reads recent context; never changes state. | — (read-only) |
| **Ingest** | Stores forwarded posts and notes (text, URLs, photos; an album = one message) with forward origin, **t.me source link**, and post date. A vision-capable LLM suggests a category from the confirmed taxonomy (matched by meaning across RU/EN), a summary, and up to 5 **key facts** in the source language. Re-forwarded posts are deduplicated. A **referential save** (a thin typed "save a note about *this*" with no subject of its own) is enriched with the recent conversation so the note captures the actual resolved subject, not the literal command. | Category confirmed by reply or button; corrections logged as feedback. |
| **Files & forward rules** | For a forward, the **text is parsed first**; only **images** (vision) and **PDFs** (text extraction — pdfminer.six with a stdlib regex fallback) are analyzed; **every other attachment** (voice, audio, video, documents…) is **stored** by `file_id`, fetchable later — never parsed. Only the boss's *own* voice notes are transcribed (commands); forwarded voice is stored, not transcribed. "покажи детали"/"покажи файл" re-sends the actual file. | — |
| **Re-categorize** | "поменяй категорию #2 на Документы", "переложи это в Чеки" (most recent), "переложи всё из crypto в news" (bulk). Logged as a correction → feeds learning. | — |
| **Note & reminder numbering** | The number the boss sees/types (`#N`) is a contiguous **1..N display position** — over visible notes (oldest first, from the immutable `messages.id`) and, analogously, over active reminders (soonest-due first, from `reminders.id`). It **compacts** on deletion / fire / cancel (no gaps) and is used for both display and resolution everywhere (`find_by_query` maps a reminder `#N` to its position in the active list); the stable ids keep every attachment/embedding/memory/calendar/fired-pending reference intact. Trade-off: a number isn't permanent — removing an earlier item shifts later ones down. | — |
| **Journals (long-term areas)** | A category can be marked a **journal** ("веди Благодарности как дневник") — append-only, recalled as a **dated day-grouped series** ("покажи дневник благодарности за месяц"), summarised by a "📔 Дневники" digest in the review/brief, and **spared by a 'clear all notes' purge**. Entries reuse `messages`; the only new state is `categories.kind` (`inbox`\|`journal`). One-time notes are unchanged. | Mark/unmark explicit; entries acked as dated. |
| **Ask (KB Q&A)** | "когда мой рейс?", "что у нас по плану?" → semantic retrieval (BGE-M3) over stored notes, then a **grounded** answer in the question's language citing `(#id)`; refuses if it isn't in the notes. | — (read-only) |
| **Reminders** | NL time parsing (RU/EN), one-shot / daily / weekly, fired from the poll loop (~1 min precision); survives restart & nightly reboot. **Snooze** a fired one by minutes/hours/absolute ("отложи на час", "до завтра в 9"). **Reschedule** by id/title (an unmatched explicit title is reported, never silently moves another); **undo** the last move ("верни предыдущее время", via `reminders.prev_due_utc`). A **half-specified** create ("напомни в 17:00") asks the missing piece and stitches it in. "напоминание по заметке N" uses note N's real subject. | Draft echoed before scheduling. |
| **Calendar** | "добавь в календарь…" → .ics file (no setup) or direct Google Calendar via a service account; `auto_calendar` syncs every confirmed reminder. | Uses confirmed reminders / explicit times. |
| **Spend** | "сколько потратили за месяц?" → totals + breakdown by skill & model + budget status. | — |
| **Budget control** | "подними дневной лимит до $3" / "set the monthly AI budget to 20" → changes the cap at runtime (stored override, enforced by the gateway). | — (explicit request) |
| **Reactions** | Cara may react to a message with a fitting emoji (sparingly), and *sees* the boss's reactions — positive/negative is logged and surfaced into her next reply. | — |
| **Self & persona** | "что ты умеешь?" → capabilities generated from the manifest (dormant features named as dormant); "расскажи о себе / какая ты?" → in-character. | — |
| **Boss profile & memory** | "запомни: …", "что ты обо мне знаешь?" (a warm, deduped summary), "забудь…", "как меня зовут?". Confirmed vs inferred kept separate; sensitive facts gated. | Consent-first; auditable & deletable. |
| **Memory provenance** | "откуда ты это знаешь?" / "почему ты это помнишь?" → she cites *how* she learned a fact, in character ("ты сам мне это сказал", "заметила из наших разговоров", with the date). | — |
| **Corrections that stick** | When the boss corrects her behavior she **says** she learned it, **applies** it (injected into her prompt), and **reports** it in the review; a *recurring* correction is flagged as **needing a code fix** rather than pretended-fixed. | — |
| **Memory review** | Cara proposes durable-memory **candidates** from evidence; "обзор памяти" lists them with confirm/skip buttons. A learned fact that **contradicts a confirmed one** is proposed, not auto-stored. | Durable memory only after a yes. |
| **Working history** | "как ты мне помогала?" → a grounded summary of real confirmed actions (saves, reminders, corrections, reviews, exports). | — |
| **Review** | "как ты поработала за неделю?" → digest with a **scorecard** (first-guess category accuracy, unclear-request count, proactive nudges, memory counts) and a **📔 Дневники** journal-activity rollup; "когда следующий performance review?" → next scheduled date; weekly review runs on a fixed schedule. Markdown exports for VS Code. | — |
| **Show media** | "покажи фото/файл из #2" → re-sends stored photos and documents by `file_id` (no re-upload). | — |
| **Fetch** | "прочитай https://…" → fetches a public page (or public t.me web view), extracts text, ingests it. SSRF-guarded. | As ingest. |
| **VPS stats** | "как сервер?" → CPU load, memory, disk, uptime, Cara's own footprint. | — |
| **Discard / delete / purge** | Decline a fresh suggestion (`discard`); delete stored items by id/ids/count (`item_delete`); **bulk purge** by scope (all / category / stats / reminders / messages / issues) with a **typed confirmation phrase**. | Discard immediate; delete & purge confirmed (purge requires the exact phrase). |
| **Proactive nudges** | Gentle, suggestion-only heads-up (overdue reminders, memory candidates waiting, items needing a category) — throttled and quiet-hours-aware (§6). Tunable in plain language ("пиши только по выходным", "не беспокой до 10", "отключи напоминания", "можно почаще"). | Suggestion-only; never acts. |
| **Trace / why** | "почему ты так решила?" → the last trace timeline. Issues are logged; weekly digest + trace-summary export. | — |
| **Report a problem** | "запиши в проблемы" / "добавь в ошибки" logs a boss-reported issue (`boss_reported`, surfaces in the review) — distinct from the issues report, which only shows them. | — |
| **One at a time** | A message bundling two+ distinct commands ("первое закрой, второе напомни…") is recognised (`multi_action`) and Cara asks to take them one at a time. Full multi-step execution is intentionally out of scope for the single-action router. | — |
| **Model-health monitor** | A scheduler tick (`MODEL_HEALTH_INTERVAL_SECONDS`, default 30 min) verifies Cara's models (chat/converse/vision) are reachable via a tiny call; on a **state change** it messages the boss the moment a model becomes inaccessible (e.g. a provider/tier 403) or recovers — alerts only on transitions, recorded in `kv` (`mh:<model>`). | — (proactive) |
| **Time-aware voice** | Conversation tone tracks the boss's local clock — fresh in the morning, breezy by day, unwinding in the evening, and **playful/intimate with a hint of flirty humour at night** (warm, never crude). Low-confidence/`clarify` turns stay in her warm voice (never a formal templated menu). | — |
| **Daily good-morning** | She never reaches out FIRST after a night without an **inventive, in-voice good-morning** (no template): the first proactive contact of a new day (boss-local, past the morning hour, proactivity on, outside quiet hours) leads with it, before any brief or nudge. Skipped when the boss already messaged first that day (`kv` `greeted_day`). | — (proactive) |
| **Photo vision (when configured)** | When the chat model isn't vision-capable, a `VISION_MODEL` (e.g. `nemotron-3-nano-omni`) **describes** a forwarded photo and the description is folded into the ingest text; with no vision model, photos categorize text-only from the caption (never stuck). | — |

---

## 4. Conversation, grounded Q&A, and persona

Cara has **two LLM-generated reply paths**, both bounded:

- **`converse`** — warm, free-form conversation in Cara's own voice. Used for
  greetings, smalltalk, anything personal or not a concrete task. It reads recent
  conversation and her profile of the boss, **never changes state**, treats any
  quoted/forwarded text as untrusted, and won't claim a real task was done. (If
  the boss wants something done, he just asks and the relevant skill handles it,
  with confirmation.)
- **`ask`** — grounded Q&A over the boss's own stored notes (BGE-M3 retrieval),
  in the question's language, citing `(#id)`; says "не нашла в твоих заметках"
  when absent. Never uses outside knowledge; stored content is wrapped as
  untrusted data.

**Persona.** Cara presents as a real person with her own (fictional) private life
— interests, friends, plans — that **grows from conversation** and stays
consistent (`cara_life`). She is warm and loyal (the boss is her *boss*; never
romantic/possessive), matches his language, and **does not break character with
AI disclaimers** — a deliberate owner decision, made safe by owner-only access
(§5). The persona is a *style* layer that sits **below** every operational rule
(`persona.py` pins the order: security → tools → router → confirmation → memory →
budget → persona), so it can never override safety, confirmation, or truth.
Transactional/system messages remain deterministic `texts.py` templates with
tone variants; only conversation and grounded answers are free-form.

---

## 5. Memory & learning

- **Boss profile (`boss_model`).** Confirmed facts vs inferred patterns kept
  separate and shown separately; a sensitivity floor by kind (e.g. personal facts
  → sensitive) means sensitive items are never surfaced casually or auto-stored.
  Names resolve per language (`owner_name_ru`/`owner_name_en`), falling back to
  «босс»/"boss" — never invented.
- **Curation (`memory_curator`).** Proposes durable-memory **candidates** from
  evidence (repeated corrections, confirmed source habits) that the boss confirms
  via memory review. From ongoing conversation it also learns: **benign** facts
  are stored as correctable *inferred* items; **sensitive** ones — and any fact
  that **contradicts something already confirmed** — become confirm-first
  candidates, never auto-stored. It also captures **behavioral corrections** as
  standing guidance (injected into her prompt) and escalates recurring ones as
  "needs a code fix".
- **Provenance (`boss_model.explain`).** "Откуда ты это знаешь?" cites how a fact
  was learned, in character (the source she stored + the date) — memory you can
  inspect, not magic.
- **Cara's life (`cara_life`).** Her own evolving fictional life, seeded and
  grown from conversation so her persona stays coherent across chats.
- **Relationship (`relationship`).** Grounded working history: every entry traces
  to a real row (a confirmed save/correction, a reminder, a saved document, a
  review, an export). Never fabricated.

---

## 6. Proactive heartbeat

A periodic, **suggestion-only** check (`proactive.py`) that may gently flag
overdue reminders (urgent), memory candidates waiting, or items still needing a
category. Rails:

- **Suggestion-only** — a nudge asks the boss to act; it never changes state.
- **Manifest-gated** — only the `proactive_heartbeat` skill runs here; a
  destructive/external skill cannot.
- **Throttled** — at most `PROACTIVE_MAX_PER_DAY` (default 1) non-urgent nudges
  per day, never repeating the same nudge in a day; urgent (overdue) may bypass
  the cap.
- **Quiet hours** — no non-urgent nudge inside the configured window (default
  22:00–08:00 local, wraps midnight); urgent ones only if explicitly allowed.
- **Audited** — every evaluation (sent or suppressed, with reason) is written to
  `proactive_log`, under its own trace, and never crashes the loop.
- **Tunable in plain language** — "пиши только по выходным", "не беспокой до 10",
  "отключи напоминания", "можно почаще" store overrides (on/off · days · quiet
  window · frequency) that the heartbeat honors.

Budget warnings and the weekly digest keep their own dedicated notifiers.

---

## 7. Durable runtime & observability

- **Permission manifest (`skill_manifest`).** Single source of truth for every
  action's risk, whether it writes state, its confirmation mode, and whether it
  may run proactively. Enforced live: startup fails fast if a router action lacks
  a policy; dispatch records each action's risk on the trace; destructive actions
  must be typed-phrase-gated (tested); proactive code calls its gate.
- **Tracing (`trace`).** One trace per inbound update and per scheduler tick,
  with staged events; the trace id stamps `llm_usage` and `issues`. "почему ты
  так решила?" replays the last trace.
- **Events & jobs (`events`/`jobs`/`runtime`).** Durable, retryable background
  work: the daily memory curator and maintenance (pending-ingest retry sweep,
  media cleanup, expiring abandoned pending actions) run as jobs that survive
  restart, retry on failure, and run under their own traces. The live
  request→reply path stays synchronous by design (single-user, low volume).

---

## 8. Data model (SQLite, WAL)

Core: `messages` (lifecycle `pending → suggested → confirmed`, plus `failed` /
`duplicate`; unique per chat+message id; forward origin, username, dates) ·
`urls` · `images` (`local_path`, `object_key`) · `files` (forwarded documents by
`file_id`) · `facts` · `chunks` (BGE-M3 embeddings) · `categories` (Cyrillic-safe
dedup; `kind` = `inbox`|`journal`) · `reminders` (`prev_due_utc` enables reschedule
undo) · `feedback` · `preferences` (identity/config) · `pending_actions` (per-chat,
TTL) · `conversation` (recent turns) · `kv`.

Spend & reliability: `llm_usage` (ts/skill/kind/model/tokens/cost/trace) ·
`model_cooldowns` (failover).

Personality & memory: `self_facts` · `boss_profile_items` (status + sensitivity)
· `memory_candidates` · `relationship_events` (title + trace) · `cara_life`.

Observability: `traces` · `trace_events` · `issues` · `events` · `jobs` ·
`proactive_log`.

Cascade deletes and the `purge` scopes keep related rows and media consistent;
**`llm_usage` (spend history) and `preferences` (identity) are never purged.** The
user-facing note number is a contiguous **1..N display position** over visible notes
(oldest first), computed from the stable `messages.id`; **reminder numbers** are the
analogous position over active reminders (due order, from `reminders.id`). Both compact
automatically and never change the ids that attachments/embeddings/memory/calendar/
fired-pending references rely on (see Capabilities → Note & reminder numbering).

---

## 9. Voice & storage

- **Voice (STT):** DO's serverless catalog exposes no transcription model, so
  Cara runs **whisper.cpp locally** on the VPS. A warm `whisper-server`
  (`STT_MODE=local_server`, OpenBLAS build, `ggml-small-q5_1`) keeps the model
  resident, transcribing a short note in ~12 s on 1 vCPU. The language is **pinned
  to Russian** (`STT_LANGUAGE=ru`) to avoid wrong-language hallucinations; a
  non-speech hallucination filter ("[Subscribe]", "Спасибо за просмотр"…) and an
  honest >20 MB ("too big") message keep garbage out of dispatch. Only the boss's
  **own** voice notes are transcribed; forwarded voice/audio is stored, not read.
- **Binary storage:** local files under `MEDIA_DIR` by default; an optional **DO
  Spaces** backend (S3 Signature V4 in pure stdlib) uploads photos for
  durability. Built and tested, **dormant** until `SPACES_*` is configured.

---

## 10. Security

- **Owner-only:** a message/callback is processed only when both the chat id and
  the sender's user id are on the allowlist; everyone else is logged and ignored.
- Closed router action set; JSON-only router output; confidence gate (converse on
  low confidence rather than a cold rejection); untrusted-content delimiters for
  forwarded/quoted text and stored notes (prompt-injection defense).
- **Fetch SSRF guard:** http/https only, no URL credentials, every URL and
  redirect hop resolved and rejected if it maps to a private/loopback/link-local/
  reserved IP or the metadata endpoint `169.254.169.254`.
- **Bulk purge** requires a typed confirmation phrase (handled deterministically
  before the router, so a stray "да" can't wipe data); pending actions carry a
  TTL and are swept when abandoned.
- **Truthfulness:** the action-truth guard prevents "done/saved/scheduled"
  wording before the DB write; Cara won't claim a real-world action she didn't
  perform.
- Secrets in `/etc/tg-ingest-agent.env` (0600), staged via files during rotation
  — never in argv, shell history, or the journal; access keys redacted from
  logged HTTP errors.
- systemd hardening: non-root user, `NoNewPrivileges`, `ProtectSystem=strict`,
  `PrivateTmp`, writable only in `/var/lib/tg-ingest-agent`.
- Dedicated bot token and dedicated DO inference key (independent billing &
  revocation).
- **Housekeeping:** voice notes and orphaned media auto-purged after processing;
  review/export files trimmed — disk stays bounded.

---

## 11. Operations

- **Host:** Pilot-VPS, `209.38.175.16:49191` (SSH key-only). systemd service
  `tg-ingest-agent`, app `/opt/tg-ingest-agent/`, state `/var/lib/tg-ingest-agent/`.
- **Deploy:** single-connection `deploy.sh` (tar → test → install → verify) with
  an idempotent installer that backs up replaced files, preserves env, gates on
  `py_compile`, and restarts only when secrets are complete; `--pull`/`--rollback`
  supported.
- **Repo:** `git@github.com:promptinvest/tg-ingest-agent.git` (own deploy key);
  pushed after every commit.
- **Tests:** 250 offline unit tests (no network; temp SQLite), run on the VPS as
  part of every deploy — including a **golden-transcript harness** that replays
  end-to-end scenarios through `handle_update` (LLM scripted per skill, Telegram
  captured) and asserts replies, DB writes, and **no state change before
  confirmation** (an un-scripted LLM call fails the scenario).
- **Observability:** journald (routing decisions with risk + confidence,
  per-row lifecycle), `traces`/`trace_events`, `llm_usage` for spend, `issues`
  and `proactive_log` for behavior, weekly digest + trace-summary export.
- **Footprint:** tens of MB RSS; disk a small fraction of 48 GB.

---

## 12. Roadmap / known gaps

- Google Calendar sync dormant until a service-account key is provisioned (.ics
  export works now); DO Spaces dormant until configured (local storage works).
- A Telegram bot cannot read arbitrary chat history or private-channel links by
  URL — forwarding remains the path for those.
- Recurrence limited to daily/weekly; remote fetch is HTML/text + public t.me only.
- **PDFs:** extracted via pdfminer.six (apt `python3-pdfminer`, nightly-updated) with
  a stdlib regex fallback; **scanned / no-ToUnicode (glyph-coded) PDFs still yield no
  text** (would need OCR, out of scope) — they're stored and re-sendable.
  Image-as-document files are kept metadata-only as images.
- **Compound commands** (two+ distinct actions in one message) are recognised and
  declined gracefully ("one at a time"), not executed as a batch — a deliberate limit
  of the single-action router.
- **Journals:** deferred for now — an optional daily "record today's entry?" nudge and
  per-journal markdown export (both straightforward follow-ons).
- **TTS** (Cara replying with voice) is the one researched-but-unbuilt item —
  parked pending a decision (a real engine install on a small box).
- Deferred by design (single-user posture): multi-channel adapters, any web
  console/webhooks, MCP adapter, independent multi-agent processes, plugin
  marketplace, and shell/browser automation — none are implemented.
```
