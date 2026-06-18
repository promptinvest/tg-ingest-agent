# Cara — Capabilities, Features & Architecture

**Cara** (`@cara_assist_bot`) is a personal, conversational AI assistant that lives
in Telegram and is self-hosted on Pilot‑VPS (a 1 vCPU / 2 GB DigitalOcean droplet).
She talks like a warm human, ingests and organizes what her owner ("boss") forwards
her, runs reminders, answers from his own notes, learns from how they work together,
and quietly flags things worth attention — all from **one stdlib‑only Python process**
with **no inbound network ports**.

This document is the complete reference: what she can do, how she behaves, and how
she's built. For the design rationale see [SOLUTION.md](SOLUTION.md); this file is
the exhaustive feature + architecture map.

---

## 1. At a glance

| | |
|---|---|
| **Surface** | Telegram bot, single owner, free‑form Russian/English, text + voice + forwards |
| **Runtime** | one systemd service, stdlib‑only Python 3, long polling (no webhooks/ports) |
| **Inference** | DigitalOcean Gradient (chat `anthropic-claude-haiku-4.5`, fallback `openai-gpt-4o`, embeddings `BGE‑M3`); STT local `whisper.cpp` |
| **Storage** | SQLite (WAL) + local media dir; optional DO Spaces (dormant) |
| **Persona** | a warm, loyal human companion with her own (fictional) life; never breaks character; matches the boss's language |
| **Safety spine** | owner‑only access · permission manifest · confirm‑before‑state‑change · budget caps · SSRF guard · action‑truth · full tracing |

---

## 2. How she decides what to do (request flow)

```
Telegram update (owner-only: chat AND sender must be on the allowlist)
   │
   ├─ message_reaction → note the boss's reaction (log, learn, surface next chat)
   ├─ callback_query   → inline-button confirmations
   └─ message
        │  own voice NOTE → transcribe (whisper) → treat transcript as the text
        │  forward / photo / document / media / audio·video·voice attachment → CONTENT
        │
        ├─ CONTENT ──────────────► ingest (no router): parse text first; analyze
        │                          images (vision) & PDFs (text); store every other
        │                          file as fetchable; suggest a category to confirm
        │
        └─ free text ───────────► router.py  (closed-world LLM intent, JSON only,
                                   confidence-gated, recent-conversation context)
                                        │
             ┌──────────────────────────┼───────────────────────────────────┐
             ▼                          ▼                                     ▼
        a skill (below)          converse (warm free-form Cara)        confirm/amend/cancel
                                 ← low-confidence falls here, not        (pending actions)
                                   a cold "уточни"
```

- **Owner‑only:** a message/reaction/button is acted on **only** when the chat id
  *and* the sender's user id are both allowlisted. Strangers, and the owner in any
  other chat (e.g. a group), are ignored.
- **State changes are confirmed** conversationally ("да", "нет, лучше крипта",
  "через полчаса") or via inline buttons. Bulk **purge** needs an exact typed phrase.
- **When unsure, she talks** — a low‑confidence read drops to warm `converse` (where
  she can answer or ask naturally), never the old cold clarify template.

---

## 3. Capabilities (what you can ask)

### Conversation & personality
- **Free‑form chat** (`converse`): greetings, smalltalk, anything personal/emotional
  or not a concrete task → a warm reply in Cara's own voice (LLM‑generated), in the
  language you wrote in.
- **Human persona, no disclaimers:** she presents as a real person with her own
  (fictional, evolving) private life — interests, friends, plans — and never breaks
  character with "I'm an AI." This is safe because she's reachable only by you.
- **Self‑knowledge** (`self_query`) and **about‑me/persona** answers come through her
  own voice; she never recites architecture or invents technical specifics.
- **Adjust her tone** (`style_update`): "говори теплее", "будь покороче".
- **Reactions (both ways):** she may react to your message with a fitting emoji
  (sparingly), and she *sees* your reactions — a 👍/❤️ is logged as positive, a 👎 as
  a negative signal, and the latest is surfaced into her next reply.
- **Time‑of‑day aware:** she knows your local time and part of day (and her own, if
  her timezone is set different) and adapts ("так поздно?", "доброе утро").

### Inbox: ingest, files, retrieval
- **Ingest forwards/notes:** forwarded posts and typed notes (text, URLs, photos;
  an album = one item) are saved with forward origin, t.me source link, post date.
  A vision LLM suggests a **category** (from your taxonomy), a **summary**, and up to
  5 **key facts** — strictly in the source language. Duplicates are detected.
- **Forwarded‑message rules:** **text is parsed first**; only **images** (vision) and
  **PDFs** (text extraction — pdfminer.six, with a stdlib regex fallback) are analyzed;
  **every other file** (voice, audio, video, documents…) is **stored**, fetchable later
  — not parsed.
- **Files:** any attached document/media is kept by Telegram `file_id`; "покажи файл
  #N" re‑sends it (free, no re‑upload).
- **Browse & detail:** "покажи заметки" (a clean card list), "что в категории crypto",
  "найди про DeepSeek", "детали #2" / "покажи заметку 11" (full card + re‑sends the
  attached photos/files; a bare "заметка N" reference resolves by number regardless of
  phrasing).
- **Note numbers** are a contiguous **1…N** position (oldest first) shown everywhere
  the boss sees or types a note number; they **compact automatically on deletion** (no
  gaps). The number is a display position, not the immutable internal id — so
  attachments, embeddings and memory links never break, but a given number isn't
  permanent (deleting an earlier note shifts the later ones down). **Reminder numbers
  work the same way** — a contiguous 1…N position in the active list (soonest-due
  first) that compacts as reminders fire/cancel; "#N" in reschedule/cancel/undo
  resolves to that position.
- **Journals (long‑term areas):** mark a category as a journal — "веди Благодарности
  как дневник" / "сделай X журналом" — and it becomes append‑only: each note acks as a
  dated entry ("запись за 18.06, всего N"), "покажи дневник благодарности [за неделю/
  месяц]" replays it as a **day‑grouped series**, a "📔 Дневники" digest appears in the
  weekly review and morning brief, and a "clear all notes" purge **spares it**. Turn it
  back off with "X больше не дневник". One‑time notes behave exactly as before.
- **Overview & stats:** "что у тебя есть?" → a digest (counts, reminders, memory,
  spend); per‑status/category **stats** (`stats`) and the **category list**
  (`categories`).
- **Re‑categorize** (`recategorize`): "поменяй категорию #2 на Документы", "переложи
  это в Чеки" (most recent), "переложи всё из crypto в news" (bulk). Logged as a
  correction so it feeds learning.
- **Delete / discard / purge:** delete by id/ids/count/query; decline a fresh
  suggestion; bulk purge by scope (all / category / stats / reminders / messages /
  issues) behind a typed phrase.

### Knowledge & answers
- **Ask (KB Q&A)** (`ask`): "когда мой рейс?", "что по плану на сегодня?" → semantic
  retrieval (BGE‑M3) over *your own stored notes*, then a grounded answer in the
  question's language citing `(#id)`; refuses if it isn't in your notes.
- **Fetch a link** (`fetch`): "прочитай https://…" → reads a public page (SSRF‑guarded)
  and ingests its text.

### Time & money
- **Reminders:** natural‑language times (RU/EN), one‑shot / daily / weekly, fired from
  the poll loop (~1 min precision), survive restarts/reboots.
  - **Snooze a fired reminder** by minutes, hours, or an absolute time ("через
    полчаса", "отложи на час", "до завтра в 9").
  - **Reschedule / undo:** "перенеси напоминание про банк на пятницу" moves it; an
    explicit title that matches nothing active is reported (never silently moves a
    different one); "верни предыдущее время" / "отмени перенос" undoes the last move.
  - **Complete a half‑specified reminder:** "напомни в 17:00" → she asks the subject,
    stitches your answer in, then confirms — the partial isn't lost.
  - **From a note:** "поставь напоминание по заметке N" uses note N's real subject as
    the title (not a literal "Заметка N").
- **Calendar:** "добавь в календарь" → `.ics` file (no setup) or Google Calendar via a
  service account; `auto_calendar` syncs every confirmed reminder.
- **Spend report:** "сколько потратили за месяц?" → totals by skill & model + budget
  status.
- **Budget control:** "подними дневной лимит до $3" / "set the monthly AI budget to
  20" → changes the cap at runtime (stored override, enforced by the gateway).

### Memory & self‑improvement
- **Boss profile:** "что ты знаешь обо мне?" → a warm, deduped summary (confirmed vs
  sensed); "запомни про меня …", "забудь …", "как меня зовут?". Sensitive facts are
  gated.
- **Memory candidates:** she proposes durable memories from evidence; "обзор памяти"
  lists them with confirm/skip buttons. Durable memory only after a yes; benign facts
  learned from chat are stored as correctable "inferred" items — **but a fact that
  contradicts something you already confirmed is proposed for confirmation, not
  silently auto‑stored**.
- **Memory provenance** (`memory_why`): "откуда ты это знаешь?" / "почему ты это
  помнишь?" → she cites *how* she learned it, in character ("ты сам мне это сказал",
  "ты меня поправил", "заметила из наших разговоров", with the date).
- **Corrections that stick:** when you correct her behavior she **says** she learned
  it, **applies** it (injected into her prompt), and **reports** it in the review. If
  the same correction recurs she flags it as **needing a code fix** instead of
  pretending to fix it.
- **Working history:** "как ты мне помогала?" → a grounded summary of real actions
  (saves, corrections, reminders, reviews, exports) — never fabricated.
- **Settings memory** (`memory`): "запомни: отвечай по‑английски", "что ты помнишь из
  настроек?" — language, timezone, auto‑calendar, named notes.

### Reporting & ops
- **Weekly performance review:** runs on a fixed schedule (default **Monday 10:00
  local**); "когда следующий review?" tells you the date; "как ты поработала?" runs it
  on demand. Includes a **scorecard** — first‑guess category accuracy, unclear‑request
  count, proactive nudges sent, and memory counts — plus a **📔 Дневники** journal‑
  activity rollup. Markdown exports for VS Code:
  review, self, boss profile, working history, memory candidates, trace summary.
- **Proactive heartbeat:** gentle, suggestion‑only nudges — overdue reminders, memory
  candidates waiting, items needing a category — throttled (≤1 non‑urgent/day),
  quiet‑hours‑aware (22:00–08:00), fully audited; never acts.
- **Tune her proactivity** (`proactive_prefs`): "пиши только по выходным", "не беспокой
  до 10", "отключи напоминания", "можно почаще" → stored overrides (on/off, days,
  quiet window, frequency) the heartbeat honors.
- **Issues report:** "какие были проблемы на этой неделе?" → a summary of logged
  communication issues (unclear/out‑of‑scope/STT/corrections…).
- **Report a problem** (`report_problem`): "запиши в проблемы" / "добавь в ошибки" logs
  a boss‑reported issue (surfaces in the review) — distinct from the issues report,
  which only *shows* them.
- **One at a time** (`multi_action`): a message bundling two+ distinct commands ("первое
  закрой, второе напомни…") is recognised and she asks to take them one at a time,
  rather than silently misfiring (full multi‑step execution is intentionally out of
  scope for the single‑action router).
- **VPS stats:** "как сервер?" → CPU/mem/disk/uptime + her own footprint.
- **Why did you do that** (`trace_query`): replays the last trace timeline.
- **Deploy notice:** after a new build is installed she says "обновления установлены"
  once (quiet on plain reboots).

---

## 4. Persona & honesty rules

- Warm, loyal human companion (the boss is her *boss*); never romantic/possessive.
- **Never breaks character** as an AI — owner‑only access makes this non‑deceptive.
- **Matches the message's language per turn** (word‑based detection: a Russian
  sentence with an English term stays Russian; Russian is the uncertain fallback).
- **Never fabricates specifics** — IDs, numbers, trace codes, prices, dates, model
  names; if unsure she says so.
- **Action‑truth:** she won't claim a real task was done unless the code did it; the
  `action_truth` guard keeps "done/saved/scheduled" wording out of draft templates.
- **Persona sits below the rules:** `persona.py` pins the prompt‑layer order
  (security → tools → router → confirmation → memory → budget → persona), so charm can
  never override safety, confirmation, or truth.
- Conversation and grounded answers are LLM‑generated; **transactional/system messages
  are deterministic `texts.py` templates** (bilingual, with tone variants).

---

## 5. Architecture

### One process, modules behind a router

```
agent.py (tg_ingest_agent.py) — poll loop · owner gate · dispatch · pending actions ·
                                 scheduler ticks · durable-job drain · reactions
   │
   ├─ router.py        closed-world LLM intent (JSON, confidence gate, context, recent-item hint)
   ├─ converse.py      free-form warm Cara (persona, life, boss facts, time, reactions)
   ├─ ingest.py        parsing, UTF-16-safe URL extraction, category+facts+summary
   ├─ pdftext.py       best-effort PDF text-layer extraction (stdlib only)
   ├─ knowledge.py     chunking + cosine retrieval + grounded-answer prompt (ask)
   ├─ reminders.py     NL time parsing, recurrence, local rendering
   ├─ gcal.py          Google Calendar (SA JWT) + .ics export
   ├─ spend.py         usage aggregation + budget status
   ├─ review.py        weekly schedule, digest, Markdown exports, trace summary
   ├─ self_model.py    deterministic self-knowledge (never invented)
   ├─ boss_model.py    boss profile (confirmed/inferred, sensitivity floors, dedup, address)
   ├─ memory_curator.py memory candidates + conversation learning + corrections
   ├─ relationship.py  grounded working history
   ├─ persona.py       prompt-layer ordering (persona below rules)
   ├─ proactive.py     suggestion-only heartbeat (throttle, quiet hours, gating)
   ├─ skill_manifest.py permission registry (risk · confirmation · proactive)
   ├─ trace.py         one trace per update/tick; staged events
   ├─ events.py/jobs.py/runtime.py  durable event/job queue + handler drain
   ├─ action_truth.py  final-verb / state wording guard
   ├─ sysinfo.py       read-only host stats (/proc, statvfs)
   ├─ fetch.py         SSRF-guarded URL reader
   ├─ storage.py       binary backend (local; DO Spaces S3 SigV4, dormant)
   ├─ llm.py           budget-guarded gateway: chat profiles + failover + cooldowns,
   │                   embeddings, STT (local/local_server/remote), pricing, budgets
   ├─ store.py         SQLite schema + helpers + additive migrations
   ├─ tg_api.py        Telegram client (sendMessage/photo/document, reactions, getFile)
   ├─ texts.py         bilingual templates (tone/intensity variants)
   └─ common.py        config, language detection, reactions/time helpers, STT-noise filter
          │
   DigitalOcean Gradient inference  ·  local whisper-server  ·  SQLite + media
```

### LLM gateway (`llm.py`)
- **Model profiles** with primary + fallback + per‑profile temperature/max‑tokens/
  json‑required: `router_fast`, `ingest_balanced`, `ask_grounded`, `converse_warm`,
  `memory_curator`, `review_balanced`. Failover to a different‑family model
  (`openai-gpt-4o`) on error/invalid‑JSON, with per‑model cooldowns.
- **Budget‑guarded:** every chat/STT/embedding call is priced and logged to
  `llm_usage`; daily/monthly caps warn at 80% and **hard‑stop** at 100% (above
  failover). Caps are overridable at runtime via `budget_set`.

### Voice (STT)
- DO has no transcription model, so Cara runs **whisper.cpp locally**: a warm
  `whisper-server` (`STT_MODE=local_server`, OpenBLAS, `ggml-small-q5_1`) keeps the
  model resident (~12 s/note on 1 vCPU). Language is **pinned to Russian**
  (`STT_LANGUAGE=ru`) to avoid wrong‑language hallucinations. (These two are set in
  the box env; the code defaults are `remote` / `auto`.) a non‑speech
  hallucination filter ("[Subscribe]", "[Music]", "Спасибо за просмотр"…) and a
  too‑big (>20 MB) message keep garbage out of dispatch.
- Only the **boss's own voice notes** are transcribed (commands/questions); forwarded
  voice/audio is stored, not transcribed.

### Durable runtime & observability
- **Permission manifest** (`skill_manifest`) is enforced live: startup fails fast if a
  router action lacks a policy; dispatch records each action's risk on the trace;
  destructive actions must be typed‑phrase‑gated; proactive code calls its gate.
- **Tracing:** one trace per inbound update and scheduler tick; trace ids stamp
  `llm_usage` and `issues`. "почему ты так решила?" replays the last trace.
- **Events & jobs:** background work (daily memory curator, pending‑ingest retry
  sweep, media cleanup, expiring stale pending actions) runs as durable jobs that
  survive restart, retry on failure, and run under their own traces. The live
  request→reply path stays synchronous by design (single‑user, low volume).

---

## 6. Data model (SQLite, WAL)

Core inbox: `messages` (lifecycle `pending → suggested → confirmed`, `failed`/
`duplicate`; forward origin, dates) · `urls` · `images` · `files` (any attachment by
file_id) · `facts` · `chunks` (BGE‑M3 embeddings) · `categories` (Cyrillic‑safe;
`kind` = `inbox`|`journal`) · `reminders` (incl. `prev_due_utc` for undo) · `feedback` ·
`preferences` (identity/config + budget overrides) · `pending_actions` (TTL) ·
`conversation` (recent turns) · `kv`.

Spend & reliability: `llm_usage` · `model_cooldowns`.

Personality & memory: `self_facts` · `boss_profile_items` (status + sensitivity) ·
`memory_candidates` · `relationship_events` (title + trace) · `cara_life`.

Observability: `traces` · `trace_events` · `issues` · `events` · `jobs` ·
`proactive_log`.

Cascade deletes + purge scopes keep rows and media consistent. **`llm_usage` (spend
history) and `preferences` (identity) are never purged.** The user-facing note number
is a **contiguous 1…N display position** over visible notes (oldest first), computed
from the stable `messages.id`; it compacts on deletion and never alters the id that
attachments/embeddings/memory reference. **Reminder numbers** are the analogous 1…N
position in the active list (due order), computed from the stable `reminders.id`.

---

## 7. Security & safety

- **Owner‑only** access on both chat and sender id, for messages, reactions, buttons.
- Closed router action set; JSON‑only router output; untrusted‑content delimiters for
  forwarded/quoted text and stored notes (prompt‑injection defense); confidence gate.
- **Fetch SSRF guard:** http/https only, no URL creds, every URL + redirect hop
  rejected if it resolves to a private/loopback/link‑local/reserved IP or the cloud
  metadata endpoint.
- **Bulk purge** requires a typed confirmation phrase (handled before the router, so a
  stray "да" can't wipe data); pending actions carry a TTL and are swept when abandoned.
- **Truthfulness:** action‑truth guard + no‑fabrication persona rule.
- Secrets in `/etc/tg-ingest-agent.env` (0600), staged via files (never argv/journal);
  access keys redacted from logged HTTP errors. Dedicated bot token + DO key.
- systemd hardening: non‑root user, `NoNewPrivileges`, `ProtectSystem=strict`,
  `PrivateTmp`, writable only in `/var/lib/tg-ingest-agent`.
- Housekeeping: voice notes & orphaned media auto‑purged; review/export files trimmed.

---

## 8. Configuration (env)

Required: `TELEGRAM_BOT_TOKEN`, `ALLOWED_CHAT_IDS` (owner only), `DO_MODEL_ACCESS_KEY`.

Common optional (defaults): `BOT_LANGUAGE=ru` · `TIMEZONE_OFFSET_HOURS=3` ·
`CARA_TIMEZONE_OFFSET_HOURS` (= boss's) · `BUDGET_DAILY_USD=1.0` /
`BUDGET_MONTHLY_USD=15.0` (runtime‑overridable) · `DO_CHAT_MODEL=anthropic-claude-haiku-4.5`
· `ROUTER_MODEL` · `DO_EMBEDDING_MODEL=BGE-M3` · `ROUTER_CONFIDENCE_THRESHOLD=0.6`.

STT (code defaults shown; the box overrides the first two): `STT_MODE` (default
`remote`, box `local_server`) · `STT_LANGUAGE` (default `auto`, box `ru`) ·
`WHISPER_SERVER_URL` · `WHISPER_MODEL` · `STT_ENABLED=true`.

Schedules & proactivity: `REVIEW_WEEKDAY=0` (Mon) / `REVIEW_HOUR=10` ·
`PROACTIVE_ENABLED=true` · `QUIET_HOURS_START=22` / `QUIET_HOURS_END=8` ·
`PROACTIVE_MAX_PER_DAY=1` · `PROACTIVE_INTERVAL_SECONDS=3600`.

Optional integrations (dormant until configured): `GCAL_CALENDAR_ID` /
`GCAL_SA_KEY_FILE` (Calendar) · `STORAGE_BACKEND=spaces` + `SPACES_*` (DO Spaces) ·
`FETCH_ENABLED` · `CATEGORIES`/`CATEGORIES_FILE`.

---

## 9. Operations

- **Host:** Pilot‑VPS, SSH key‑only on a non‑standard port. Service
  `tg-ingest-agent`; app `/opt/tg-ingest-agent/`; state `/var/lib/tg-ingest-agent/`.
- **Deploy:** single‑connection `deploy.sh` (tar → test → install → verify) with an
  idempotent installer (backs up, preserves env, `py_compile` gate, restarts only when
  secrets are complete); `--pull` / `--rollback <sha>` supported. The installer stamps
  a content‑hash `VERSION` so Cara announces real code changes (not reboots).
- **Repo:** `git@github.com:promptinvest/tg-ingest-agent.git` (own deploy key); pushed
  after every commit.
- **Tests:** 278 offline unit tests (no network; temp SQLite), run on the box as part
  of every deploy — including a **golden‑transcript harness** that replays end‑to‑end
  scenarios through `handle_update` (LLM scripted per skill, Telegram captured) and
  asserts replies, DB writes, and **no state change before confirmation**; an
  un‑scripted LLM call fails the scenario.
- **Observability:** journald (routing decisions with risk + confidence, per‑row
  lifecycle), `traces`/`trace_events`, `llm_usage` (spend), `issues` + `proactive_log`
  (behavior), weekly digest + trace‑summary export.
- **Footprint:** tens of MB RSS; disk a small fraction of the 48 GB volume.

---

## 10. Known limits & roadmap

- **PDF text** uses pdfminer.six (apt `python3-pdfminer`, kept current by the nightly
  updater) with a stdlib regex fallback. **Scanned / no‑ToUnicode (glyph‑coded) PDFs**
  still yield no text layer — reading them needs **OCR**, out of scope here; such files
  are stored and re‑sendable.
- **Compound commands** (two+ distinct actions in one message) are recognised but not
  executed as a batch — she asks to take them one at a time.
- A Telegram bot can't read arbitrary chat history or private‑channel links by URL —
  **forwarding** remains the path; bot file downloads are capped at **~20 MB**.
- Reminders are daily/weekly; remote fetch is HTML/text + public t.me only.
- **Dormant** until configured: Google Calendar sync, DO Spaces storage.
- **Deferred by design** (single‑user posture): multi‑channel adapters, any web
  console/webhooks, MCP adapter, independent multi‑agent processes, plugin marketplace,
  shell/browser automation.
```
