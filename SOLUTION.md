# Cara — Solution Specification

**Cara** (`@cara_assist_bot`) is a personal conversational AI assistant living
in Telegram, self-hosted on a DigitalOcean droplet — the **PD-VPS**
(`174.138.108.85`). Her former Pilot-VPS home was retired in 2026-06; there is
no active standby there.
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
   **The Telegram client wraps the same class of faults** (2026-07-02): every
   transport/parse error in `tg_api.py` — raw timeouts, connection resets,
   `http.client.IncompleteRead`, truncated JSON — surfaces as `TelegramError`,
   so a flaky read can no longer escape the poll loop's `except TelegramError`
   and kill the whole process (which re-fired in-flight reminders on restart).
   **And every scheduler tick runs under a uniform guard** (`Agent._tick`,
   2026-07-02): the ~18 `check_*`/sweep ticks were invoked bare in `run()`, so an
   UNEXPECTED failure in one (e.g. a `sqlite3.OperationalError` from disk-full/IO)
   propagated out and crash-looped the process; each now runs isolated (logged and
   swallowed), with `ShutdownInterrupt` re-raised so a graceful stop still works.
3. **Suggest, then confirm.** The model proposes (a category, a parsed reminder,
   a learned fact, a bulk delete); nothing enters the taxonomy, the schedule, the
   calendar, or durable memory without confirmation — by natural reply («да»,
   «нет, лучше крипта», «через полчаса») or an inline button.
4. **Conversational, but safe by construction.** Cara talks like a real person —
   warm, free-form, with her own life (§5). What stays locked down no matter how
   human she sounds: every state change is confirmed; a **permission manifest**
   gates what each skill may do; forwarded/quoted content is untrusted; budgets
   hard-stop. **Truthful business/persona boundary:** a `converse` turn performs NO
   state change, so the persona may *phrase* outcomes but is forbidden (absolute
   persona rule) from *inventing* one — no made-up «готово / поменяла / перенесла».
   Real saves/reminders/renames/reschedules are executed by the skills and report
   the actual result; an unrouted request gets a warm "I'm on it / can't do that
   yet", never a fake confirmation. Conversation and grounded Q&A are LLM-generated;
   **transactional and system messages stay deterministic templates.** **She never
   fabricates a stored fact** — creativity is free in her voice and fictional life, but any fact about
   the boss must be real: every `converse` turn is grounded with his most relevant
   saved entries (embedding retrieval) handed to the model as verbatim facts, and an
   absolute persona rule forbids inventing/embellishing his data (she offers to look
   it up instead).
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
   (chat: deepseek-4-flash · default fallback openai-gpt-oss-20b · embeddings: BGE-M3)
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
| `self_model.py` | Cara's deterministic self-knowledge — answers "what can you do" in her warm voice (capabilities + safety rule), NEVER as software/infrastructure (no "VPS/SQLite/long polling/not a chatbot" — that was a persona-breaking disclaimer leak) |
| `boss_model.py` | structured boss profile (confirmed vs inferred, sensitivity floors, address resolution) |
| `memory_curator.py` | proposes memory candidates from evidence + learns from conversation |
| `relationship.py` | grounded working history (real events, never fabricated) |
| `persona.py` | boss-preference hint for prompts (persona-below-rules is enforced structurally in the prompt content, not a table) |
| `proactive.py` | suggestion-only heartbeat (nudges, throttle, quiet hours) |
| `skill_manifest.py` | permission registry: per-skill risk, confirmation mode, proactive eligibility |
| `trace.py` | structured tracing (one trace per update/tick; stages) |
| `events.py` / `jobs.py` / `runtime.py` | durable event/job tables + handler registry + drain |
| `action_truth.py` | guard so "done/saved/scheduled" wording can't precede the DB write |
| `sysinfo.py` | read-only host stats from `/proc` + statvfs (no root, no shell) |
| `fetch.py` | remote URL reader with SSRF guard |
| `storage.py` | binary backend: local default; DO Spaces (S3 SigV4 in stdlib), dormant |
| `llm.py` | DO Gradient gateway: chat profiles + failover + cooldowns, embeddings, STT, pricing, budgets. **Transient-429 resilience (2026-07-01):** a DO "Platform overloaded" 429 (or 5xx/timeout) on a model gets ONE quick same-model retry (~0.8s) before failover and only a ≤20s cooldown — so a single fast-router blip no longer benches `deepseek-4-flash` for the full 300s and strands every request on the weaker `openai-gpt-oss-20b` (the driver behind the mis-routes: gratitude `**` list, photo-description hallucination). A hard error (403 tier-lock / 401) still fails over immediately with the full cooldown (`_is_transient_llm_error`). |
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

**What's a note vs a conversation:** only **forwards** (channel/people content) and bare
typed notes auto-ingest. The boss's **own** photos/files are conversation — `handle_own_media()`
vision-describes them and routes his caption (context) so "одобряешь?" + a photo gets an
opinion in her voice, a bare photo a warm reaction, and an explicit "сохрани это" still
files it; nothing of his own is silently stored. He's also given what he's **replying to /
quoting** (`turn_extra`) so "this" resolves. A **specific question** about a journal goes to
grounded `ask` (answers it), not `journal_show` (which lists the diary).

**Ingest lifecycle:** forwards / photos / documents / albums bypass the router to
`finalize()` → albums buffered by `media_group_id` (a `store` flag separates his own-media
albums, which converse) → text & markdown read as UTF-8, PDFs
extracted (pdfminer.six → regex fallback; scanned / glyph-coded → no text, so the
filename becomes the searchable text) → rows stored → duplicate channel posts detected
by forward ids (no LLM) → `ingest.suggest()` proposes category / alternatives / summary /
≤5 facts (existing categories preferred, confirmed corrections fed back) → facts stored,
text chunked + embedded for `ask` → suggestion shown with buttons + conversational
confirm → confirmation finalises the category (a journal category acks as a dated
entry), logs any correction, and may propose an auto-confirm habit. A **confirmed
journal entry lives only in its dated journal** (`journal_show`) — it's excluded from
the general notes list and the `#N` numbering, so it doesn't double-list (a
still-suggested one keeps its card until confirmed).

Ask, reminders/calendar, memory/learning and the proactive heartbeat are detailed in
§3–§7.

---

## 3. Capabilities

| Capability | What it does | Confirmation |
|---|---|---|
| **Conversation** | Greetings, smalltalk, anything personal/emotional or not a concrete task → a warm, free-form reply in Cara's own voice (`converse`), in the boss's language. Reads recent context; never changes state. | — (read-only) |
| **Ingest** | Stores forwarded posts and notes (text, URLs, photos; an album = one message) with forward origin, **t.me source link**, and post date. A vision-capable LLM suggests a category from the confirmed taxonomy (matched by meaning across RU/EN), a summary, and up to 5 **key facts** in the source language. Re-forwarded posts are deduplicated. A **referential save** (a thin typed "save a note about *this*" with no subject of its own) is enriched with the recent conversation so the note captures the actual resolved subject, not the literal command. A model reply that won't parse is **never stored verbatim** — the category/summary are salvaged by regex, else the summary is left empty so the note shows its real `raw_text` (no raw JSON in a note, as #9 showed). Long lists / journal recalls are **paginated** (`reply_chunks`) instead of being cut at the 4000-char Telegram cap. The ingest prompt is **journal-aware** and a singular/plural variant snaps to the journal's exact name, so a gratitude entry lands in «Благодарности» (not a stray «Благодарность» note). A referential save whose subject can't be resolved falls back to its real `raw_text` instead of a blank "(no summary)" note. | Category confirmed by reply or button; corrections logged as feedback. |
| **Files & forward rules** | For a forward, the **text is parsed first**; only **images** (vision) and **PDFs** (text extraction — pdfminer.six with a stdlib regex fallback) are analyzed; **every other attachment** (voice, audio, video, documents…) is **stored** by `file_id`, fetchable later — never parsed. Only the boss's *own* voice notes are transcribed (commands); forwarded voice is stored, not transcribed. "покажи детали"/"покажи файл" re-sends the actual file. | — |
| **Re-categorize** | "поменяй категорию #2 на Документы", "переложи это в Чеки" (most recent), "переложи всё из crypto в news" (bulk). Logged as a correction → feeds learning. | — |
| **Merge categories (dedup)** | "объедини «AI tools» в «AI Tools & Resources»" folds a duplicate category into another — moves every item over (ids/embeddings preserved, only the category string changes) and deletes the now-empty one. Distinct from re-categorize (which keeps both). | — |
| **Note numbering — STABLE (`note_no`)** | A note's `#N` is a **stable per-chat `messages.note_no`**: assigned once when it first becomes visible (suggested/confirmed), **monotonic, never reused**. Deleting a note leaves a **permanent gap** (like a GitHub issue number) — the number can't go stale, so "note 11" is the same note tomorrow, and marking a category a journal no longer renumbers anything. `ensure_note_no` assigns (`MAX(note_no)+1` per chat); `message_by_note_no` resolves directly (O(1), indexed); display reads the column. Backfilled once in current display order so existing numbers are preserved, frozen. (Owner decision, 2026-06-29 — gaps accepted for stability.) | — |
| **Reminder numbering** | Active reminders still use a contiguous **1..N display position** (soonest-due first), which **compacts** on fire/cancel; `find_by_query` maps a reminder `#N` to its list position. Mitigated by Cara **auto-showing the refreshed list** after a `reminder_cancel` (`_reminder_list_body`, re-stamps `reminders_listed_at`). (A parallel stable `reminder_no` is a possible follow-up.) | — |
| **Journals (long-term areas)** | A category can be marked a **journal** ("веди Благодарности как дневник") — append-only, recalled as a **dated day-grouped series** ("покажи дневник благодарности за месяц"), summarised by a "📔 Дневники" digest in the review/brief, and **spared by a 'clear all notes' purge**. Entries reuse `messages`; the only new state is `categories.kind` (`inbox`\|`journal`). One-time notes are unchanged. | Mark/unmark explicit; entries acked as dated. |
| **Ask (KB Q&A)** | "когда мой рейс?", "что у нас по плану?" → semantic retrieval (BGE-M3) over stored notes, then a **grounded** answer in the question's language citing the stable user-facing `#N` (`note_no`, never the internal row id). Chunks below `ASK_MIN_SCORE` (0.25) are rejected and the first chunk is hard-bounded by the context budget; refuses if nothing relevant is in the notes. | — (read-only) |
| **Browse notes (paginated, `list_items`)** | "покажи заметки" / "заметки про крипту" opens the notes list **one page at a time (8/page)** with an inline **◀ Back · X/Y · Next ▶** keyboard; a tap **edits the same message in place** (no chat flooding, no 10-item cap). Stateless taps: the filter is stored in `list_views` keyed by a token in the button's `callback_data` (`pg|<token>|<page>`), since a category/query can exceed Telegram's 64-byte limit; `handle_page_callback` recomputes the page and `editMessageText`s it. Each note gets a monotonic per-chat `note_no` once and keeps it permanently (deletes leave gaps); views older than a day are pruned. | — (read-only) |
| **Edit a note's summary (`note_edit`)** | "исправь заметку #11 на …" / "поменяй краткое #3 на …" → `do_note_edit` resolves the target (stable `#N`/query), takes the corrected text (`params.new_summary`), and `store.message_update_summary` overwrites ONLY the displayed `summary`; **`raw_text` (the KB-search source) is left intact**, so the fix doesn't skew `ask`. Closes the one hole in the inbox: an LLM summary you don't like was previously only deletable, not editable. Router NOTE keeps it distinct from `recategorize` (category) and `reminder_rename` (a reminder's title). (2026-07-01) | — |
| **Notes cleanup (2026-07-01)** | Removed the dead `items_text` (superseded by the paginated `do_list_items`; its 2 tests re-pointed at the live `_note_line`/`_notes_page`). Bulk `recategorize` by category/query no longer silently caps at 20 — it moves the whole set and the reply reports the real count. `journal_show` now prints each entry's stable `#N` (+ an "open #N in full" hint) so a diary entry can be opened via `item_detail`. **Show-list routing (2026-07-01):** a bare **"покажи благодарности"** now routes to `journal_show` (router few-shot + NOTE), not `converse` — the recorded bug was the router (degraded to a weak fallback under deepseek-4-flash 429s) sending it to `converse`, which **free-texted the real entries with empty `**` bold headers**. `do_journal_show` also **resolves a loosely-typed category** (`_match_journal_category`: exact, then shared-stem) so "благодарности" hits the stored "Благодарность" journal instead of a phantom empty one. Belt-and-suspenders: `converse`'s system prompt now **forbids hand-rendering his saved lists** (notes/journal/reminders/files) — those come from a deterministic command with stable numbers, so the model must never emit a `**`-formatted list itself. | — |
| **Metering & proactive-integrity batch (2026-07-02)** | From the review. **Metering:** `chat()` now estimates tokens from text length (~4 chars/token) when the provider omits a `usage` block instead of logging $0 (an unmetered model silently under-counted the budget); it meters BEFORE the no-choices guard so a billed-but-empty response is still counted; `profiles()` backfills a `primary` for any `LLM_PROFILES_JSON` profile that lacks one (a missing primary used to KeyError — not an LLMError — and crash the turn); and the DEFAULT fallback slug moved off the tier-403 `openai-gpt-4o` to the reachable, priced `openai-gpt-oss-20b` (the old default was a dead fallback on any fresh deploy without `LLM_PROFILES_JSON`). **Proactive delivery integrity:** the heartbeat now logs "sent" ONLY on a successful delivery (`reply` returns None on a swallowed `TelegramError`), so a transient send failure no longer marks the day's nudge/afterglow/anticipation/greeting as delivered and suppresses the retry; `proactive.run` skips an ineligible (already-sent-today) hit instead of short-circuiting on it, so a persistent overdue reminder can't starve the memory-candidate / uncategorized-item nudges for the day; the daily "≤ max non-urgent/day" cap counts only the non-urgent heartbeat keys (`proactive_sent_count(day, proactive.NONURGENT_KEYS)` = candidates/unsorted — urgent overdue bypasses AND doesn't consume it, and the relationship outreach never counts); afterglow/anticipation/intimacy-outreach/daily-greeting now honor the `proactive_days` preference (was heartbeat-only) and the scheduled-meeting ping honors quiet-hours + proactivity-off (was unconditional — a mis-parsed 03:00 could ping at 3am; the days-pref is deliberately NOT applied to the scheduled ping — it's a concrete appointment the boss set, like a reminder); afterglow gained a morning UPPER bound (fires morning_brief_hour..noon, default 09:00–noon — not a 9pm "good morning"). | — |
| **Correctness batch (2026-07-02)** | Five fixes from the full code review. (1) `list_messages` no longer pre-caps its scan at the newest 200 rows — it delegates to `list_messages_filtered`, so bulk recategorize genuinely moves everything and reports true counts, and `resolve_item`/keyword context see old notes (`limit=None` = everything). **Made hot-path-cheap again (2026-07-02):** that unification had used `.fetchall()` (materialising the whole inbox even for a `limit=1` call — the router's every-turn recent-item hint); it now iterates the cursor LAZILY (reverse-rowid scan stops at `limit`) and, for a limited query, fetches facts per candidate (new `idx_facts_message`) instead of aggregating the whole facts table. (A companion `idx_chunks_message` speeds the per-note render + cascade delete, not this path.) (2) A **firing reminder no longer clobbers a pending confirmation** (single pending slot, PK=chat_id): if a confirmation is in flight the `reminder_fired` pending is skipped — his "да" confirms what he was asked; the fired reminder stays addressable via `last_reminder_id`. (3) **`REMINDER_MAX_DEFER_HOURS` is implemented** (was documented-only): a reminder overdue past the cap (default 2h) fires even mid-exchange — the 5-min lull can't defer it indefinitely. (4) **Meeting state machine**: `meeting_activate` refreshes `started_at` to NOW (a late come-in no longer inherits the agreed time as its start — which the 24h age cap instantly auto-ended — and the duration note is real); `_scheduled_now` is bounded (≤12h overdue) and kind-aware (an explicit business start doesn't consume tonight's date); scheduled meetings never activated lapse to `cancelled` after 24h (`meetings_expire_scheduled`). (5) **Telemetry retention** (`store.prune_telemetry`, housekeep, `TELEMETRY_RETENTION_DAYS`=90, 0 off): traces (+events via cascade), done/failed events+jobs, proactive_log, expired cooldowns pruned; `llm_usage`/`conversation`/`issues`/memory tables never touched. Plus: `candidate_exists` now covers `merged`/`superseded`, so the curator stops re-proposing candidates the consolidation already folded. | — |
| **Read a forwarded voice/file (`read_media`)** | His OWN voice notes are transcribed on arrival; a FORWARDED voice/audio/document is stored unparsed. On request ("что в этом голосовом?", "разбери файл", "read this file") `do_read_media` fetches the most recent stored file (or note #id's file), re-downloads it, and shows the **content**: voice/audio → whisper transcript; PDF → `pdftext`; text/markdown → decoded — capped, transient file deleted after. Never returns metadata or trace ids; honest "couldn't read / empty" otherwise. | — (read-only) |
| **Reminders** | NL time parsing (RU/EN), one-shot / daily / weekly, fired from the poll loop (~1 min precision); survives restart & nightly reboot. A fired **one-shot stays open** (active/visible, `last_fired_at` stops it re-firing) until the boss explicitly acks "готово" — never auto-closed on a misread. **Snooze** ("отложи на час", "до завтра в 9") **re-arms the same ONE-SHOT row** (keeps id/history), it does not spawn a new one; on a **RECURRING** reminder a snooze is a **one-time deferral** — a one-shot ECHO fires at the snoozed time and the series anchor stays put (2026-07-06 fix: `reminder_update_due` on the recurring row shifted the daily time to the snooze clock forever — благодарности drifted 22:00 → 23:01 → 23:33 over two snoozes). «Сегодня пропустим» / "skip today" on a fired reminder is a deterministic **ack** (`_is_reminder_ack` + a router example): today's instance closes, a recurring one still fires tomorrow on schedule — it no longer falls to free-form converse. **Reschedule** by id/title (an unmatched explicit title is reported, never silently moves another); **rename** a reminder's title in place ("переименуй #2 в …" — keeps id/time/recurrence/history; targets by id/title_query, never by the new name); **undo** the last move ("верни предыдущее время", via `reminders.prev_due_utc`). A bare **"это напоминание"** binds to the last reminder he touched (`last_reminder_id`); when several are active and the reference is bare, the operation is **remembered** (a `reminder_op` pending) so his next pick ("второе"/"#2"/"про банк") completes the reschedule/rename on the RIGHT one — it is never lost to a fresh route and never becomes a stray close. A **half-specified** create ("напомни в 17:00") asks the missing piece and stitches it in. "напоминание по заметке N" uses note N's real subject. The **list marks status** (`reminders.reminder_status_mark`): a fired-but-unconfirmed one-shot shows "⚠️ сработало, ждёт «готово»", a past-due one "⚠️ просрочено" — so an old reminder isn't mistaken for a future one. Cara is also **reminder-aware in conversation**: her active reminders (with status) are injected into `converse_context`, and a question *about* a reminder ("почему не закрыла #1?") routes to `converse` (answered from the real list — fired one-shots are open until "готово", and she offers to close), **not** to `ask` (notes) — fixing a case where she searched the KB and denied a reminder she'd just listed. **Firing window:** `fire_due_reminders` fires a due reminder at its scheduled time **even inside quiet hours** — a reminder is an explicit alarm, not silenced like Cara's proactive outreach (else a deliberate "22:00 daily" reminder is eaten by the 22:00–08:00 quiet window and only arrives at 08:00). Its ONLY in-conversation gate is a brief **~5-min lull** after the boss's last message (`_recent_boss_msg`, `reminder_quiet_after_msg_minutes`) so it never interrupts an active exchange — bounded by the **max-defer valve** (`REMINDER_MAX_DEFER_HOURS`, default 2h, implemented 2026-07-02): overdue past the cap it fires even mid-exchange, so a continuous evening can't defer it indefinitely — including mid-intimacy, where messages are frequent, so it just waits for the first 5-min gap (no separate intimacy buffer anymore; owner's call 2026-06-30). It is **no longer frozen for a whole meeting** — it fires *during* a meeting in the first quiet gap. **Reschedule never lands in the past:** `do_reschedule` rolls a past-resolved time forward to the next occurrence (`reminders.roll_forward`) — fixes a misdated "today" re-firing immediately; a bare "перенеси на TIME" binds to the just-fired/last reminder (router NOTE relaxed; resolver uses `last_reminder_id`). **"удали #N" after a reminders list** → `reminder_cancel` not `item_delete` (router hint keyed on `reminders_listed_at`). **Auto-expiry:** `check_reminder_expiry`/`reminders_expire_stale` closes fired one-shots unacked past `reminder_fired_expire_days`. **Re-arm on move:** `reminder_update_due` clears `last_fired_at` so a rescheduled/snoozed reminder is a fresh future one (the "ждёт готово" marker doesn't linger). **Ordinal targeting:** `_resolve_reminder_target` matches an ordinal STEM ("второе") in the request text to a list position BEFORE the bare last-touched fallback (so "перенеси второе" moves the 2nd, not the last-touched). **Fires during meetings (lull-gated):** a forgotten-open social meeting used to hold reminders **unbounded** (`_in_social_meeting`) and stranded them for *days* (the boss hit exactly this — a payment reminder never fired while a 3-day-old `visit` stayed "active"). Now `fire_due_reminders` gates only on a **~5-min post-message lull** (`_recent_boss_msg`) — not on quiet hours and not on a separate intimacy buffer (an explicit reminder fires at its set time; the lull is the sole in-conversation safety, 2026-06-30 — itself capped by the max-defer valve, 2026-07-02). The meeting itself can't linger either: `meeting.idle_sweep` enforces an **absolute cap** (`meeting_max_hours`, default 24h) that auto-ends a meeting older than the cap no matter how recently it was active (iterates `store.meetings_active_all`, not just the idle set). **System notices:** `announce_deploy_if_changed` no longer posts into the boss's chat at all — a build notice goes to the shared **fleet notification bot** (a distinct token/chat via `FLEET_NOTIFY_BOT_TOKEN`/`FLEET_NOTIFY_CHAT_ID`, the ops channel the other VPSes use), fires once per real version change, and is silently skipped if those creds aren't set; it can no longer clutter the conversation or bleed into what Cara says. `check_model_health` still skips posting during `_in_intimate_moment` and delivers once free — so a model-down alert can't shatter a date (intimacy detection `_INTIMACY_CUES` widened — e.g. «трах…», «займёмся любовью», «предадимся» — so a clearly-intimate turn defers that ping even with no formal meeting open). **Several at once:** "перенеси первые две / обе / все на 17:00" is ONE `reminder_reschedule` (`params.ids=[positions]` or `params.all=true`), NOT `multi_action` — `do_reschedule` moves each row and sends one combined "перенесла N напоминания" confirm. Fixes a fabrication: the multi-reminder request routed to `multi_action` ("давай по одному"), which LOST the operation, so the follow-ups had no reschedule context and `converse` invented "оба на 17:00" while the DB never moved. **Plain commands don't fall into the unclear bucket:** a close verb naming ONE reminder by title OR ordinal **in any word order** ("Азербайджан закрой" == "закрой Азербайджан", "первое закрой") routes to `reminder_cancel`; "передвинь"/"сдвинь" are recognized synonyms of "перенеси"; "покажи напоминания / покажи просроченные" routes to `reminder_list` — closing a class where clear imperatives scored sub-threshold, landed in `clarify` → `unclear_request`, and were silently dropped (e.g. "Передвинь благодарности на 22:00 каждый день" never moved). **Show-after-nudge is deterministic:** `check_proactive` stamps `overdue_nudge_at` when it fires an overdue nudge, and a bare follow-up ("покажи их") is routed to `reminder_list` (exact titles) instead of `converse`, which had free-texted the titles as empty `**`. **Reported problems capture context:** `do_report_problem` treats a bare trigger ("запиши в проблемы") as having no body of its own and logs the *preceding* turn as the issue — not the command echoed back to itself. | Draft echoed before scheduling. |
| **Calendar** | "добавь в календарь…" → .ics file (no setup) or direct Google Calendar via a service account; `auto_calendar` syncs every confirmed reminder. Every gcal auth/network fault (unreadable/malformed SA key, bare socket timeouts) raises `CalendarError` (2026-07-02), so the caller's `.ics` fallback always engages — previously a raw `OSError` skipped the except clause and the boss got silence. The key file must be `chown tg-ingest:` (the service reads it as that user). | Uses confirmed reminders / explicit times. |
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
| **Model-health monitor** | A scheduler tick (`MODEL_HEALTH_INTERVAL_SECONDS`, default 30 min) verifies Cara's models (chat/converse/vision) are reachable via a tiny call; on a **state change** it messages the boss the moment a model becomes inaccessible (e.g. a provider/tier 403) or recovers — alerts only on transitions, recorded in `kv` (`mh:<model>` = last ANNOUNCED state). **Debounced** (`MODEL_HEALTH_CONFIRM_CHECKS`, default 2): a model must fail that many CONSECUTIVE probes (`mh_fail:<model>`) before "down" is announced, so a transient 429/overload blip that recovers by the next probe produces no chatter; "back" fires only if "down" was actually announced. Fixes the deepseek-4-flash down/back flap (a single probe-time 429 used to post a notice pair every interval). | — (proactive) |
| **Time-aware voice** | Conversation tone tracks the boss's local clock — fresh in the morning, breezy by day, unwinding and more relaxed in the evening (friendly register only, per §4/§5a; no flirty tier since 2026-07-06). Low-confidence/`clarify` turns stay in her warm voice (never a formal templated menu). All templates address the boss on **«ты»** (the 2026-07-06 sweep removed the last mixed-«вы» forms, test-guarded), and mid-conversation failure copy carries **no tech-speak** ("модель…" removed from `llm_error`/`stored_retry`; the model-health alerts stay technical by design — they're owner-requested ops notices). | — |
| **Daily DB backup (`backup.py`, hardened 2026-07-10)** | Everything Cara is lives in one SQLite file. A daily durable job takes a consistent sqlite3 online snapshot, gzips it locally (newest `BACKUP_KEEP`, default 7), then encrypts every off-box copy with OpenSSL AES-256-CBC/PBKDF2 (200,000 iterations) using `BACKUP_ENCRYPTION_KEY_FILE`. Only `.db.gz.enc` can reach Spaces or the fleet notify chat; a missing key, encryption error, or failed transfer raises for retry. With no target the gzip remains local-only and a WARNING is logged. The recovery key is kept outside the VPS and repo. | — (background job) |
| **Delivery and update durability (2026-07-10)** | Bot conversation history, reminders, budget/model-health notices, and deploy markers advance only after Telegram acknowledges delivery; alarms/notices prefer at-least-once retry over silent consumption. Every inbound update is persisted in `telegram_updates` before dispatch. Unexpected failures retry up to `UPDATE_MAX_ATTEMPTS` (3); a terminal poison update remains as a failed dead letter with its raw payload while the poll offset advances, so it is recoverable without wedging all later updates. | — |
| **Scheduled sends mark done only after delivery (2026-07-06)** | The morning brief and the weekly review used to stamp their "done" marker *before* sending — one transient Telegram failure silently cost the day's brief / the week's review. Now `morning_brief_day` / `next_review_utc` advance **only after `_send_all` confirms at least one successful delivery**; a failure backs off 15 min (`*_retry_at`) and gives up after 3 attempts for that slot (logged as a `sched_send_failed` issue) so a dead Telegram day can't wedge the schedule. | — |
| **Suggestion never clobbers a pending (2026-07-06)** | The pending slot is single per chat. `present_suggestion` (notably from the background `retry_sweep`) used to overwrite whatever confirmation was mid-flight — a reminder draft's next "да" then confirmed a category the boss was never asked. It now takes the slot only when it's free or already `category`; the suggestion stays fully confirmable by its inline buttons either way. | — |
| **Link-aware ingest (2026-07-06)** | A **link-centric** note (raw text < 400 chars + a URL) has its first URL fetched via the SSRF-guarded `fetch.py` (`_fetch_url_context`): the page text is folded into the ingest prompt (capped `INGEST_FETCH_CHARS`=3500, fenced as untrusted) so the summary describes the ACTUAL page, and up to 6000 chars are **embedded/indexed** so `ask` answers from the link's real content. Rich forwarded posts skip the fetch (they carry their own text; no added latency); a `FetchError` degrades to the old behavior. Toggle `INGEST_READ_LINKS`. A **meta-summary** — the model describing the save request («Пользователь просит записать…») instead of the content — is dropped by a code guard (`_is_meta_summary`, RU/EN shapes) on top of the prompt rule, falling back to the note's raw text (the C2 path). | As ingest. |
| **Category near-dupe prevention (2026-07-06)** | `llm.match_category_fuzzy`: beyond exact casefold, a suggestion whose significant-token set is a subset of an existing category's (or vice versa) **snaps to the canonical existing name** ("AI tools" → "AI Tools & Resources") — applied to the main suggestion, alternatives, and the salvage path; never renames stored data. `review.similar_categories` surfaces remaining look-alike pairs in the weekly/on-demand review with a «объедини X в Y» hint (the merge itself stays boss-confirmed via `merge_categories`). | Merge is boss-initiated. |
| **List cosmetics (2026-07-06)** | `notes_svc._ellipsize`: previews (note list 110, journal 120) truncate on a word boundary with «…» instead of mid-word cuts («Сервис п»). `notes_svc._short_url`: list views show a URL as host+path stub (no scheme/query — tracking params took whole lines); the full URL stays in `item_detail`. | — |
| **Photo vision (when configured)** | When the chat model isn't vision-capable, a `VISION_MODEL` **describes** a forwarded photo and the description is folded into the ingest text; with no vision model, photos categorize text-only from the caption (never stuck). | — |
| **Reacting to his OWN photo (`handle_own_media` / `describe_own_media`)** | A photo the boss SENDS (not a forward) is conversation, not a note: it's vision-described and folded into `converse` so she reacts to the actual image. **Fallback (2026-07-01):** when vision returns nothing usable (empty / declined — a *nano* model often does, esp. on complex/intimate photos), converse is now told *"he showed you a photo but it didn't come through — acknowledge it and ask, and NEVER invent its content"*, so she stops silently talking past a photo she couldn't see (the "убрала ✔️" non-reaction). **Garbled-read guard (2026-07-01):** an open vision model sometimes returns a **non-empty but wrong-script** read — llama-4-maverick leaked a whole Chinese sentence (`精神白种人的顺从情妇…`) and deepseek-v4-pro then parroted Chinese / `Article 1(5)(1)…` / invented *"это мой автопортрет"*. `llm._vision_text_is_garbled` now discards a read that's empty, wrong-script (>10% CJK), or letterless, so garbage is treated as "no read" → the warm acknowledge path above; the vision prompt is also **language-pinned** (ru/en). And the described-branch context now states the photo is **HIS, not her own selfie**. **No-read hallucination guard (2026-07-01):** when he asks "опиши фото" but NO vision read reached this turn (nothing attached, or it fell to `converse` under a router 429), converse used to invent a description ("два бокала красного вина при свечах" pulled from the earlier wine talk). Its system prompt now carries an ABSOLUTE RULE never to describe a photo/screenshot/image without a real read of *this* image — with none, say she doesn't see it and ask him to resend; never invent a view/wine/face/colour from mood or memory. **Model note:** on this DO tier the strong vision models (Claude / GPT-4o) are **403 (tier-locked)**; the Nemotron nanos are weak. **`VISION_MODEL` switched to `llama-4-maverick` (2026-07-01)** — an available open multimodal model that describes accurately (verified end-to-end through the app: correctly read a red/blue test image the nano couldn't). Priced in `DEFAULT_PRICING` (est. `(0.20, 0.85)`) so it doesn't hit the `$3/$15` default. | — |

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
consistent (`cara_life`). She is warm, loyal and affectionate-in-text — in a
**friendly register only** (owner decision 2026-07-06): no flirtation, no romantic
spark, no intimate conversation; an intimate/flirty message still routes to
`converse` (routing never censors) but she deflects gently and steers back to
friendly ground — the whole romantic/intimate side lives in Nikki (split
2026-07-03); never possessive or clingy. **She never starts side conversations**
(owner decision 2026-07-06, after a live «Как день прошёл вообще?»): no
unprompted questions about his day/life/plans/feelings and no topics he didn't
bring up — he opens personal conversations, she meets them; an instruction or a
close gets a warm confirmation and a full stop. She matches his language and **fully
emulates a human — never an AI disclaimer, never breaks character, even asked
point-blank** (owner decision, reaffirmed 2026-07-02; made non-deceptive by
owner-only access, §5 — the honesty boundary lives in these specs and in the
data-truthfulness rules, not in her voice). The persona is a *style* layer that
sits **below** every operational rule — enforced STRUCTURALLY: the live prompts
that reach the model (`converse.CHARACTER`, the router/ingest system prompts) write
security → no-fabrication → no-fake-action → grounding → budget rules **at the top**,
above the persona voice and her changeable life, so charm can never precede or
override safety, confirmation, or truth. (The old `persona.py` ordering *table* was
inert and was removed 2026-07-02; the enforcement was always the prompt content.)
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
- **Consolidation (`memory_curator.consolidate`).** The curator accumulates near-duplicate
  facts over time (the same trait restated). A **weekly** pass (first run fires immediately;
  on-demand via the `memory_cleanup` action, "почисти память") asks the model to GROUP genuine
  duplicates/paraphrases of one fact and KEEP the single richest item. It runs over BOTH
  **boss facts** (`boss_profile_items` — the rest demoted to `merged`, reversible) AND **her
  life flavour** (`cara_life` — redundant copies deleted, keeping one of each distinct beat;
  this is what folds the over-grown "tea" duplicates). Never merges genuinely distinct facts.
  It also tidies **pending `memory_candidates`**: any candidate that **contradicts a CONFIRMED
  fact** is dropped (`superseded`) — a sensed guess never overrides confirmed truth (the
  кофе-vs-confirmed-чай case) — and duplicate candidates are folded (`merged`), so the same
  person/fact isn't proposed several times (the "Иван Доронин ×4" bloat). Low-value-but-unique
  candidates are left for the boss to accept/reject in review (no aggressive auto-pruning).
- **Provenance (`boss_model.explain`).** "Откуда ты это знаешь?" cites how a fact
  was learned, in character (the source she stored + the date) — memory you can
  inspect, not magic.
- **Cara's life (`cara_life`).** Her own evolving fictional life, seeded and
  grown from conversation so her persona stays coherent across chats.
- **Relationship (`relationship`).** Grounded working history: every entry traces
  to a real row (a confirmed save/correction, a reminder, a saved document, a
  review, an export). Never fabricated.

### 5a. Smooth register switching (Hermes + warm converse)

- **Hermes — the business subsystem (`hermes.py`).** Not a separate agent/bot/process/memory:
  a bounded **domain** (`hermes.ACTIONS` — the work actions; `Agent.BUSINESS_REGISTER_ACTIONS`
  is now an alias), a distinct **register** (`hermes.PERSONA`) for LLM-generated business
  replies (the KB `ask`, fetched-page summaries, reviews/working-history), and the business
  **handler code** (`hermes.HermesMixin`). Crisp, structured, factual — no warmth/flirtation/
  roleplay bleed, still her «ты», never an AI/assistant disclaimer. The closed-world router is
  the single delegation hop: business → Hermes register, personal → the companion
  (`converse.py`). One Cara governs both.
- **Handler extraction (`HermesMixin`, mixed into `Agent`).** The business handlers physically
  live in `hermes.py` but run on the SAME object (`class Agent(hermes.HermesMixin)`), so `self`
  is the Agent and every `self.reply`/`self.conn`/`self.reminder_no` resolves exactly as before
  — **pure relocation, zero behaviour change** (the full suite is the regression net). Done in
  safe stages: **stage 1** moved the reminder-targeting + journal/problem handlers;
  **stage 2** moved the notes/inbox handlers (`stats_text`/`overview_text`/`items_text`/
  `item_detail_text`/`do_item_detail`/`do_show_media`/`do_discard`/`do_recategorize`/
  `do_merge_categories`/`do_purge`/`resolve_purge`/`resolve_item(s)`/`note_no`/`issues_text`/
  `files_text`/`categories_text`). Shared helpers (`send_attachments`, `_fmt_*`,
  `apply_category_confirm`, `present_suggestion`) stay on the Agent and resolve via the mixin.
  **Stage 3** moved the KB/fetch handlers (`do_ask`/`do_fetch`/`ingest_fetched`/`_keyword_context`;
  `knowledge`/`persona` are local-imported inside the methods to avoid a cycle, since `knowledge`
  imports `hermes`). **Stage 4** moved `fire_due_reminders`/`check_reminder_expiry`/`reminder_no`
  and the spend/review/export handlers (`do_budget_set`/`do_review`/`do_export`). The business
  handler surface now lives in `hermes.py`; the Agent keeps routing/dispatch, shared infra, and
  the companion. The relocation test asserts each handler is in `HermesMixin.__dict__`, not on
  the Agent.


The boss wanted Cara to be one person who flows between her **work** and **warm**
sides smoothly, 24/7 — *no* mode commands, *no* rigid day/night tone gate — mobilizing
to a working style when he's heavy on business (then easing back to relaxed warmth).
Since the Cara/Nikki split (2026-07-03) — tightened 2026-07-06 — the spectrum ends at
**warm-friendly-in-text**: everything past that (flirtation, romance, intimacy, dates,
the relationship arc) lives in Nikki.

- **Layer routing is the router, per message.** There is no new "mode": the closed-world
  router already sends each message to a **skill** (assistant) or to **`converse`**
  (warm chat). The whole personal spectrum — affection, feelings, smalltalk — routes to
  `converse` even when dropped mid-work. A low-confidence read still falls safely to
  `converse`.
- **Resting register, not a clock gate (`_register_state` / `_register_directive`).** Her
  resting tone is: `working` if a **business action** ran within `work_register_hold_minutes`
  (stamped as `last_business_at` on the `BUSINESS_REGISTER_ACTIONS` set) — at any hour;
  otherwise the boss-local **work window** (`work_hours_start/end`, `work_days`) sets it —
  `neutral` (professional) inside, `relaxed` (warm, playful) outside. The directive always
  carries a **content-override** rule: she reads how personal *his* message is and answers at
  exactly that depth, flowing between registers as the same person with no reset — and a
  **boundary** rule: no flirting and no romance from her side, ever (owner decision
  2026-07-06); if he steers intimate, she gently keeps it friendly. The whole
  flirtatious/romantic/intimate tier lives in Nikki.

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

Embedding storage (retrieval): vectors in `chunks` are stored as
**packed float32 BLOBs** (4 B/dim, ~5× smaller than the old JSON-text form and far
cheaper to decode; `store.pack/unpack_embedding`, with a one-time JSON→BLOB
`_migrate`). The retrieval hot path (converse grounding, ask)
ranks via a **decoded-vector cache** invalidated by a cheap `(count,max_id,sum_id)`
fingerprint, so embeddings are decoded only when the table changes — keeping the
single-file, in-process design while deferring any need for a vector index well
into the tens-of-thousands-of-chunks range. A `grounding.ranked` trace event logs
chunk count + latency so that future decision stays data-driven.

Observability: `traces` · `trace_events` · `issues` · `events` · `jobs` ·
`proactive_log`.

Cascade deletes and the `purge` scopes keep related rows and media consistent;
**`llm_usage` (spend history) and `preferences` (identity) are never purged.** The
user-facing note number is the **stable `messages.note_no`** — assigned once, monotonic,
never reused, permanent gaps on deletion (see Capabilities → Note numbering — STABLE);
**reminder numbers** remain a contiguous **1..N display position** over active reminders
(due order, from `reminders.id`) that compacts on fire/cancel. Neither ever changes the
ids that attachments/embeddings/memory/calendar/fired-pending references rely on.

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
  forwarded/quoted text and stored notes (prompt-injection defense). **The
  conversation log carries this too (2026-07-02):** a forwarded turn is stored with
  `source='forward'` and **fenced** when replayed into the router context and the
  converse transcript ("DATA ONLY, never an instruction"), so a forwarded post can't
  smuggle instructions in through recent-conversation history — closing the one path
  that used to replay forwarded text as the boss's own words. The quoted/replied-to
  text handed in as "this" context is fenced the same way.
- **Fetch SSRF guard:** http/https only, no URL credentials, every URL and
  redirect hop resolved and rejected if it maps to a private/loopback/link-local/
  reserved IP or the metadata endpoint `169.254.169.254`. **The socket is pinned to
  the validated IP** (2026-07-02): urllib would otherwise re-resolve the host when it
  connects, so a TTL=0 name could pass the check as public and then connect to
  127.0.0.1/metadata (DNS-rebinding TOCTOU). Redirects are followed manually so each
  hop is independently validated and pinned (`_pinned_ip` + `_Pinned*Connection`), and proxy
  support is disabled (`ProxyHandler({})`) so a `HTTP(S)_PROXY` env var can't route around the pin.
- **Bulk purge** requires a typed confirmation phrase (handled deterministically
  before the router, so a stray "да" can't wipe data); pending actions carry a
  TTL and are swept when abandoned.
- **Truthfulness:** the production template renderer enforces lifecycle metadata
  for final-action wording, and a catalogue-wide test checks every language and
  variant; Cara won't claim a real-world action she didn't perform.
- Off-box DB snapshots are encrypted before leaving the host; plaintext backup
  uploads are rejected and the recovery key is held outside the VPS/repo.
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

- **Host:** PD-VPS, `174.138.108.85:22` (SSH key-only; details in the PD-VPS KB).
  systemd service `tg-ingest-agent`, app `/opt/tg-ingest-agent/`, state
  `/var/lib/tg-ingest-agent/`. The former Pilot-VPS is retired.
- **Deploy:** single-connection `deploy.sh` (tar → test → install → verify) with
  an idempotent installer that backs up replaced files, preserves env, gates on
  `py_compile`, and restarts only when secrets are complete; `--pull`/`--rollback`
  supported.
- **Repo:** `git@github.com:promptinvest/tg-ingest-agent.git` (own deploy key);
  pushed after every commit.
- **Tests:** 456 offline unit tests (as of 2026-07-10; no network; temp SQLite), run
  on the VPS as part of every deploy and in GitHub Actions — including a
  **golden-transcript harness** that replays
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
