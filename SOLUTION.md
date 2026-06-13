# Cara — Solution Specification

**Cara** (`@cara_assist_bot`) is a personal conversational AI assistant living
in Telegram, self-hosted on a 1 vCPU / 2 GB DigitalOcean droplet (Pilot-VPS).
All model inference runs on DigitalOcean Gradient serverless inference. The
operator ("boss") talks to her in free-form **Russian or English — text or
voice — with no slash commands**; a closed-world router assigns each request to
a specialized skill, and anything that changes state is confirmed
conversationally before it becomes final.

Stdlib-only Python 3, long polling (no inbound ports), one systemd service.

---

## 1. Design principles

1. **One bot, one process, skills as modules.** Telegram allows a single
   `getUpdates` poller per token, and a 1-vCPU box does not need a fleet of
   daemons. The orchestrator is an intent router inside one systemd service;
   each skill is a Python module behind it. A skill graduates to its own
   process only when it earns it.
2. **Store first, think second.** Every inbound message is persisted to SQLite
   before any model call. LLM outages, budget stops, and restarts never lose
   data — pending work is retried on a sweep.
3. **Suggest, then confirm.** The model proposes (a category, a parsed
   reminder, a learned habit, a bulk delete); nothing enters the taxonomy, the
   schedule, the calendar, or memory without operator confirmation — by natural
   reply («да», «нет, лучше крипта», «через полчаса») or an inline button.
4. **Scoped, not chatty.** The router has a closed action set with **no generic
   "chat" action**, so Cara cannot drift into open-ended GPT conversation. The
   LLM emits JSON into validated slots and all user-facing text comes from
   bilingual templates — with **one deliberate, fenced exception**: the `ask`
   skill (§4) returns a grounded free-form answer from the operator's own notes.
5. **Every token is metered.** All chat / STT / embedding calls pass through one
   budget-guarded gateway that prices and logs them; daily and monthly budgets
   warn at 80 % and hard-stop at 100 %.
6. **Zero inbound surface.** Long polling only: the host firewall stays
   SSH-only, no webhooks, no reverse proxy, no Docker, no pip dependencies.
7. **Analyze → argue → build.** New capabilities are assessed against the
   architecture first; the agent-or-not question is asked per feature; genuine
   options or guardrail changes are surfaced for sign-off before coding.
   (Operator working agreement, `CLAUDE.md`.)

---

## 2. Architecture

```
 Telegram (text · voice · forwarded posts · photos · .md/.txt documents)
        │  long polling, no inbound ports
        ▼
 ┌─ agent.py ── poll loop · album buffering · pending actions · scheduler ──────┐
 │                                                                              │
 │  voice ──► STT (local whisper.cpp) ──► text ─┐                               │
 │  forwarded / photo / document ───────────────┼──► ingest skill (no router)   │
 │  free text / transcribed voice ──► router.py (closed-world LLM intent)       │
 │                     │  (14 turns of context; resolves references)            │
 │   ┌─────────┬───────┼────────┬─────────┬─────────┬──────────┬─────────┐      │
 │   ▼         ▼       ▼        ▼         ▼         ▼          ▼         ▼      │
 │ ingest  reminders  spend   review   memory     ask      fetch    sysinfo    │
 │ +facts  +calendar  +budget +export  +habits  (KB Q&A)  (URL)   (vps stats)  │
 │   │         (gcal)                            knowledge.py  fetch.py         │
 │   └──────── show_media · discard · item_delete · purge · issues · stats ─────┘
 │                          │                                                   │
 │                  llm.py — budget-guarded gateway                             │
 │              (chat · embeddings(BGE-M3) · STT; prices + logs every call)      │
 │                          │                                                   │
 │           store.py — SQLite (WAL)        storage.py — binaries               │
 │                                          (local default / DO Spaces)         │
 └──────────────────────────────────────────────────────────────────────────────┘
                            │
              DigitalOcean Gradient serverless inference
        (chat: anthropic-claude-haiku-4.5 · embeddings: BGE-M3)
```

### Module map

| Module | Responsibility |
|---|---|
| `tg_ingest_agent.py` | entry point: poll loop, dispatch, pending-action resolution, scheduler ticks, housekeeping (installed as `agent.py`) |
| `router.py` | closed-world intent router (LLM, JSON-only, confidence gate, context recall) |
| `ingest.py` | message parsing, URL extraction (UTF-16-safe), category + facts + summary suggestion |
| `knowledge.py` | document chunking, cosine retrieval, grounded-answer prompt (the `ask` skill) |
| `reminders.py` | reminder drafts, recurrence, local-time rendering |
| `gcal.py` | Google Calendar (service-account JWT) + .ics export |
| `spend.py` | AI-usage aggregation and reports |
| `review.py` | performance review (chat + Markdown export) |
| `sysinfo.py` | read-only host stats from `/proc` + statvfs (no root, no shell) |
| `fetch.py` | remote URL reader with SSRF guard |
| `storage.py` | binary backend: local default; DO Spaces (S3 SigV4 in stdlib), dormant |
| `llm.py` | DO Gradient gateway: chat, embeddings, local/remote Whisper STT, pricing, budgets |
| `store.py` | SQLite schema + helpers; additive migrations |
| `tg_api.py` | Telegram Bot API client (send message/photo/document, getFile) |
| `texts.py` | bilingual (ru/en) reply templates — Cara's voice |
| `common.py` | config loading, shared helpers |

---

## 3. Capabilities

| Capability | What it does | Confirmation |
|---|---|---|
| **Ingest** | Stores forwarded posts and notes (text, URLs, photos; an album = one message) with forward origin, **t.me source link**, and post date. A vision-capable LLM suggests a category from the operator-confirmed taxonomy (matched by meaning across RU/EN), a summary, and up to 5 **key facts** — all strictly in the source language. Re-forwarded posts are deduplicated. | Category confirmed by reply or button; corrections logged as feedback. |
| **Documents** | Send a `.md`/`.txt` file (e.g. a trip plan) → full text stored and indexed; flows through the same categorization. | As ingest. |
| **Ask (KB Q&A)** | "когда мой рейс?", "что у нас по плану на сегодня?" → semantic retrieval (BGE-M3) over stored notes, then a **grounded free-form answer** in the question's language, citing `(#id)`; refuses if the answer isn't in the KB. | — (read-only) |
| **Reminders** | NL time parsing (RU/EN), one-shot / daily / weekly, fired from the poll loop (~1 min precision), snooze by natural reply; survives restart & nightly reboot. | Draft echoed before scheduling. |
| **Calendar** | "добавь в календарь…" → .ics file (no setup) or direct Google Calendar via a service account; `auto_calendar` syncs every confirmed reminder. | Uses confirmed reminders / explicit times. |
| **Spend** | "сколько потратили за месяц?" → totals + breakdown by skill & model + budget status. Budgets enforced in the gateway. | — |
| **Memory & learning** | "запомни: …", "что ты обо мне знаешь?", "забудь…". Owner name auto-captured. Category corrections feed back into prompts; after N consistent confirmations from a source, Cara offers to auto-confirm it. | Consent-first; auditable & deletable. |
| **Introspection** | "что ты умеешь?" (capabilities), "что у тебя есть?" (KB digest), "покажи сохранённое про X / в категории Y" (browse). | — |
| **Show media** | "покажи фото из #2" → re-sends stored photos by Telegram `file_id` (no re-upload, free). | — |
| **Fetch** | "прочитай https://…" → fetches a public page (or public t.me web view), extracts text, ingests it. SSRF-guarded. | As ingest. |
| **VPS stats** | "как сервер?" → CPU load, memory, disk, uptime, Cara's own footprint. | — |
| **Discard / delete / purge** | Decline a fresh suggestion (`discard`); delete a stored item (`item_delete`); **bulk purge** by scope (all / category / stats / reminders) with a **typed confirmation phrase**. | Discard immediate; delete & purge confirmed (purge requires the exact phrase). |
| **Issues & review** | Every failure (out-of-scope, unclear, STT, model error, budget stop, fetch, no-KB-match) is logged; weekly digest + on-demand performance review with a Markdown export for VS Code. | — |

Persona: Cara is a warm, loyal "private aide" (the operator is her *boss*),
specified in `prompts/cara_persona.md` and enforced structurally — the voice
lives in templates, not in unconstrained model output.

---

## 4. The one free-text exception (grounded Q&A)

`ask` is the only action that returns generated prose. It is fenced so it
cannot become a general chatbot:

- answers **only** from the operator's own stored notes (retrieved by BGE-M3
  cosine similarity, keyword+recency fallback);
- **never** uses outside/general knowledge; says "не нашла в твоих заметках"
  when absent (and logs an `ask_no_context` issue);
- replies in the question's language; cites the source `(#id)`;
- stored content is wrapped as untrusted data (prompt-injection defense).

This relaxation was an explicit operator decision; every other interaction
remains template-only.

---

## 5. Data model (SQLite, WAL)

`messages` (lifecycle `pending → suggested → confirmed`, plus `failed` /
`duplicate`; unique per chat+message id for redelivery dedup; forward origin,
username, dates) · `urls` · `images` (`local_path`, `object_key`) · `facts` ·
`chunks` (text + BGE-M3 embedding for semantic search) · `categories`
(canonical names; Cyrillic-safe dedup via Python `casefold`) · `reminders` ·
`llm_usage` (ts/skill/kind/model/tokens/cost) · `feedback` · `preferences`
(identity/config) · `pending_actions` (per-chat, TTL) · `conversation` (last 30
turns for router context) · `issues` · `kv` (poll offset, flags).

Cascade deletes and the `purge` scopes keep related rows and media consistent;
**`llm_usage` (spend history) and `preferences` (identity) are never purged.**

---

## 6. Voice & storage

- **Voice (STT):** DO's serverless catalog exposes no transcription model, so
  Cara runs **whisper.cpp locally** on the VPS (`ggml-small-q5_1`, ffmpeg
  OGG→WAV), free, ~1 min per 30 s note on 1 vCPU. `STT_MODE` switches to a
  remote OpenAI-compatible endpoint if one becomes available.
- **Binary storage:** local files under `MEDIA_DIR` by default; an optional
  **DO Spaces** backend (S3 Signature V4 in pure stdlib, validated against
  AWS's published vectors) uploads photos for durability. Built and tested,
  **dormant** until `SPACES_*` is configured.

---

## 7. Security

- Chat-ID allowlist; unknown senders logged and ignored.
- Closed router action set; JSON-only model output; template-only replies (one
  fenced grounded-answer exception); untrusted-content delimiters; confidence
  gate (clarify below threshold).
- **Fetch SSRF guard:** http/https only, no URL credentials, every URL and
  redirect hop resolved and rejected if it maps to a private/loopback/
  link-local/reserved IP or the cloud metadata endpoint `169.254.169.254`.
- **Bulk purge** requires a typed confirmation phrase (handled deterministically
  before the router, so a stray "да" can't wipe data); 5-min TTL.
- Secrets in `/etc/tg-ingest-agent.env` (0600), staged via files during
  rotation — never in argv, shell history, or the journal; access keys redacted
  from logged HTTP errors.
- systemd hardening: non-root user, `NoNewPrivileges`, `ProtectSystem=strict`,
  `PrivateTmp`, writable only in `/var/lib/tg-ingest-agent`.
- Dedicated bot token and dedicated DO inference key (independent billing &
  revocation).
- **Housekeeping:** voice notes and orphaned media are auto-purged after
  processing; review exports trimmed — disk stays bounded.

---

## 8. Operations

- **Host:** Pilot-VPS, `209.38.175.16:49191` (SSH key-only). systemd service
  `tg-ingest-agent`, app `/opt/tg-ingest-agent/`, state `/var/lib/tg-ingest-agent/`.
- **Deploy:** `scp` + idempotent installer (backs up replaced files, preserves
  env, `py_compile` gate, restarts only when secrets are complete).
- **Repo:** `git@github.com:promptinvest/tg-ingest-agent.git` (own deploy key);
  pushed after every commit.
- **Tests:** 119 offline unit tests (no network; temp SQLite), run on the VPS.
- **Observability:** journald (routing decisions with confidence, per-row
  lifecycle), `llm_usage` for spend, `issues` for failure modes, weekly digest.
- **Footprint:** ~20 MB RSS; disk ~10 % of 48 GB.

---

## 9. Roadmap / known gaps

- Google Calendar sync dormant until a service-account key is provisioned
  (.ics export works now).
- DO Spaces dormant until a Space + keys are configured (local storage works).
- A Telegram bot cannot read arbitrary chat history or private-channel links by
  URL — forwarding remains the path for those.
- Recurrence limited to daily/weekly; image-as-document files stored
  metadata-only; remote fetch is HTML/text + public t.me only (file shares
  deferred).
