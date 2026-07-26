# Cara (tg-ingest-agent) — PRD & Technical Specification snapshot

> **Purpose of this document.** A self-contained product + technical spec of the
> Cara project, for analysis by an external LLM (improvement planning). First
> generated 2026-07-17 at commit `f408e52`; **regenerated 2026-07-17 after the
> notes-lifecycle + structured-journals plan v1.1 shipped in full** (Phase 0 +
> Batches 1–4: NTE-001…006, JRN-001…006, MET-001). It condenses the two
> maintained specs — `CARA.md` (capabilities/architecture) and `SOLUTION.md`
> (design rationale) — plus the 2026-07-16 five-dimension project review. This
> file is a **snapshot, not a maintained spec**: the source of truth remains
> `CARA.md` + `SOLUTION.md`.

---

## 1. Product definition (PRD)

### 1.1 What Cara is

Cara (`@cara_assist_bot`) is a **single-owner personal assistant** living in
Telegram. She talks like a warm human (Russian/English), ingests and organizes
what her owner ("the boss") forwards her, runs reminders, answers questions
from his own saved notes, tracks AI spend against a hard budget, learns from
how they work together, and reports on her own performance — all from **one
Python process on one small VPS**, with no inbound ports.

### 1.2 Users and access

- Exactly **one user** (the owner). Every inbound Telegram update is
  double-keyed (chat id AND sender id) against an allowlist; strangers, groups,
  channels, bots are dropped. Empty allowlist = refuse to start.
- Non-goal: multi-user, teams, public deployment.

### 1.3 Product principles (the "why")

1. **Truthfulness above charm.** Cara never states a wrong stored fact, never
   claims an action she didn't perform, never invents tool results or files.
   Templates carry the deterministic voice; free-form replies are guarded
   (see action-truth, §5).
2. **Human persona, no AI disclaimers** (owner decision 2026-07-02). She is a
   consistent fictional person; non-deceptive because she is owner-only.
   Register is **friendly-only** (owner decision 2026-07-06): no flirtation or
   intimacy — that entire domain lives in the sibling product "Nikki" (forked
   2026-07-03; separate repo/process/DB).
3. **Closed world.** An LLM router classifies each message into a fixed action
   set; anything else falls to warm conversation. The LLM never gets open-ended
   authority; destructive actions are confirm-gated deterministically.
4. **Budget is a hard ceiling.** All model calls go through one gateway with
   metering, warn-at-80%, and a hard stop.
5. **One box, one file.** stdlib-only Python 3, SQLite (WAL), systemd; no
   frameworks, no external services beyond DigitalOcean Gradient inference
   (+ optional DO Spaces, Google Calendar SA).
6. **Boss data is sacred:** spend history (`llm_usage`) and identity
   (`preferences`) are never purged; conversation history is deleted only by
   an explicit "purge all" whose preview discloses it; daily encrypted off-box
   backups.

### 1.4 Capabilities inventory (user-visible)

- **Inbox/notes:** forwarded posts and typed notes auto-filed with LLM-suggested
  category (confirm-before-store), summary, up to 5 key facts; duplicates
  detected; stable note numbers (#N); link-aware ingest (fetches a link-centric
  note's URL, SSRF guarded); list/detail/show-media/edit/recategorize/merge/
  delete/discard; bulk purge by scope behind a typed confirmation phrase.
  **Own photos are never stored** (retired 2026-07-16) — they are conversation
  (vision-described reaction); own text/PDF docs still file via caption.
- **Note lifecycle & one-card capture (2026-07-17, NTE-001…003):** beside its
  category every note carries a knowledge state (inbox → active → archived,
  reversible, never auto-deleted) and purpose (reference/source/idea/decision/
  temporary/actionable); the capture card shows the source-grounded WHY +
  conditional buttons (Temporary-30d, Don't-save, and — when the content itself
  carries a validated future date — Save+reminder, which commits the note then
  stages a normal reminder draft in the single pending slot). Real-use
  accounting: only a detail open / citation in a delivered answer / delivered
  export / accepted resurfacing counts.
- **Review & resurfacing (2026-07-17, NTE-004…006):** «покажи, что стоит
  пересмотреть» → ≤3 items with deterministic reasons, snapshot-bound ordinal
  follow-ups («второе в архив»); lifecycle state views + notes overview; at
  most ONE related-note hint after a delivered KB answer; the proactive
  "unsorted" nudge became the note-review invitation.
- **Structured journals + Gratitude (2026-07-17, JRN-001…006):** journals are
  semantic entities (`journal_definitions`/`journal_entries`); a CLOSED
  in-code entry-type registry (only gratitude active); gratitude capture with
  extracted core fields on the card (lexical-support enforced, invented names
  rejected, draft-only until confirm, raw text immutable); legacy history
  migrated deterministically (`legacy_unstructured`); entries display as
  **J#N** (the message's stable number); person/tag filters + deterministic
  person stats with citations; per-journal Markdown export; journal-specific
  typed purge phrase (diary survives); opt-in per-journal evening prompts
  (confirm-gated enable, off by default).
- **Knowledge Q&A (`ask`):** semantic retrieval (BGE-M3 embeddings, cosine,
  keyword fallback) over the boss's own notes/documents; grounded answers
  citing note ids; refuses when the KB has nothing.
- **Reminders:** natural-language times (RU/EN), one-shot/daily/weekly, fire
  from the poll loop (~1 min precision), survive restarts; fired one-shots stay
  open until an explicit "готово"; snooze/reschedule/rename/undo; partial
  drafts ("напомни в 17:00" → asks the subject); deterministic fired-reminder
  follow-ups; recurring snooze = one-time echo (anchor never drifts).
- **Calendar:** `.ics` export or Google Calendar via service account.
- **Spend & budget:** per-skill/model reports; runtime budget override by chat.
- **Reviews & exports:** weekly performance review — **saved-to-used outcomes
  lead (2026-07-17, MET-001)**: saved · actually used · turned into reminders ·
  archived/restored · awaiting triage/review-due · upcoming reviews/expiring
  temporaries · journal entries per journal, with operational metrics (issues,
  spend, fallbacks, first-guess accuracy, memory) in a Cara-health tail; the
  engineering Markdown adds the KPI `capture_to_use_rate` + secondary outcome
  metrics (never optimized toward more saves); on-demand review; real Markdown
  file exports via sendDocument.
- **Memory & learning:** structured boss profile (confirmed vs inferred,
  sensitivity gates); memory candidates proposed from evidence with verbatim
  non-forward quotes required, confirm-before-store; corrections that stick
  (injected into prompts, reported in reviews); working history from real
  logged events; memory provenance answers ("откуда ты это знаешь?").
- **Proactive (suggestion-only):** overdue-reminder nudges, memory-candidate
  nudges, the note-review invitation, and opt-in per-journal prompts
  (throttled, quiet-hours 22–08, weekday prefs, ≤N non-urgent/day, per-key
  daily dedupe, delivery-gated); opt-in morning brief; model-health monitor
  with transition-only alerts.
- **Voice & media:** own voice notes transcribed (Whisper local/local-server/
  remote modes); forwarded voice/files stored unparsed but readable on request
  ("что в этом голосовом?"); photo vision via `VISION_MODEL`
  (`llama-4-maverick`) with garbled-read and no-read hallucination guards.
- **Ops surface for the boss:** VPS stats, trace explanations ("почему ты так
  решила?"), problem reports ("запиши в проблемы"), capabilities answer
  generated from the live permission manifest.

### 1.5 Non-goals

- Multi-step task execution in one message (`multi_action` → "давай по одному").
- Romance/intimacy register (Nikki's domain).
- Group chats, inline mode, non-Telegram surfaces.
- Vector DB / external search infra (in-process cosine is deliberate at
  current scale).

---

## 2. System architecture

### 2.1 Topology

- **Host:** DigitalOcean droplet (2 vCPU / 4 GB — resized from the original
  2 GB; the hostname still says `2gb`), Ubuntu, hardened; service
  `tg-ingest-agent` under systemd (dedicated user, `ProtectSystem=strict`,
  `NoNewPrivileges`, `PrivateTmp`, `Restart=always`).
- **No inbound ports:** Telegram long polling only
  (`allowed_updates = message, callback_query, message_reaction`).
- **State:** one SQLite DB (WAL) + media dir under `/var/lib/tg-ingest-agent`.
- **Inference:** DigitalOcean Gradient (OpenAI-compatible API), stdlib
  `urllib` client.

### 2.2 Process model

One long-poll loop (`tg_ingest_agent.py`, installed as `agent.py`):
per-update dispatch → ~18 scheduler ticks (reminders fire/expiry, weekly
review, morning brief, proactive heartbeat, model health, budget notice, daily
backup job, durable-job drain, housekeeping/telemetry pruning, album flush) →
graceful SIGTERM shutdown. Every inbound update is persisted to a durable
`telegram_updates` inbox before processing; unexpected failures retry up to 3
attempts, then dead-letter with the raw payload while the offset advances
(poison updates can't wedge the queue). Delivery-gated sends: reminders/notices
mark done only after Telegram acknowledges (at-least-once).

### 2.3 Module map

| Module | Responsibility |
|---|---|
| `tg_ingest_agent.py` | poll loop, owner gate, dispatch table, pending-action resolution, scheduler ticks, finalize/ingest, albums, voice, housekeeping |
| `router.py` | closed-world LLM intent router: JSON-only, fixed `ACTIONS`, confidence gate (default 0.6 → falls to converse), untrusted-content fencing, smalltalk shortcut, recent-item/context hints |
| `hermes.py` | business register: `ACTIONS` domain set + Hermes persona prompt + `HermesMixin` (KB ask/fetch, budget_set, review, export) |
| `notes_svc.py` | `NotesMixin`: notes/inbox handlers — lists, detail, show media, discard/recategorize/merge, purge staging + typed-phrase resolve, note lifecycle/review, journals (recall/filters/stats/prompts), problem log |
| `journals.py` | structured journals: closed entry-type registry (gratitude active), payload validation with lexical support, extraction, person stats, per-journal md export, prompt-config validation |
| `reminders_svc.py` | `ReminderMixin`: reminder CRUD + partial drafts + deterministic fired follow-ups + fire/expiry sweeps |
| `converse.py` | free-form warm Cara (persona `CHARACTER` prompt, grounding, reaction tags) |
| `ingest.py` | parsing, UTF-16-safe URL extraction, category/facts/summary suggestion |
| `knowledge.py` | chunking, cosine retrieval, grounded-answer prompt |
| `reminders.py` | NL time parsing, recurrence math, local rendering |
| `llm.py` | budget-guarded gateway: named chat profiles + failover + cooldowns, embeddings, STT, pricing table, transport-error taxonomy |
| `store.py` | SQLite schema + helpers; additive migrations only |
| `memory_curator.py` / `boss_model.py` / `self_model.py` / `persona.py` / `relationship.py` | evidence-gated memory pipeline, boss profile, deterministic self-knowledge, prompt hints, working history |
| `proactive.py` | suggestion-only heartbeat (throttle, quiet hours, per-key dedupe) |
| `skill_manifest.py` | per-action policy registry: risk, confirmation mode (`False`/`True`/`typed_phrase`), proactive eligibility; startup asserts it covers `router.ACTIONS` |
| `trace.py` / `events.py` / `jobs.py` / `runtime.py` | one trace per update/tick; durable job queue (daily memory curation, backups) drained on a sweep tick, reclaimed on restart |
| `action_truth.py` | final-verb/lifecycle guard for templates + free-form action/artifact claim detection |
| `fetch.py` | SSRF-guarded URL reader (IP pinning, per-hop redirect re-validation, proxy disabled, metadata/private ranges blocked) |
| `backup.py` | daily consistent snapshot; off-box only as AES-256-CBC/PBKDF2 (200k iters) `.db.gz.enc` |
| `storage.py` | binary backend: local default; DO Spaces S3 SigV4 in stdlib (dormant until configured) |
| `gcal.py`, `spend.py`, `review.py`, `sysinfo.py`, `pdftext.py`, `tg_api.py`, `texts.py`, `common.py` | calendar, spend, reviews/exports, /proc stats, PDF text, Telegram client, bilingual templates, config |

Handler mixins run on the single `Agent` object (pure relocation; `self` is the
Agent everywhere).

### 2.4 Router action set (closed world)

`ingest, reminder_create, reminder_list, reminder_cancel, reminder_reschedule,
reminder_rename, reminder_undo, list_files, calendar_add, spend, budget_set,
stats, categories, help, overview, list_items, item_detail, item_delete,
note_edit, recategorize, note_lifecycle, note_review, merge_categories,
show_media, read_media, discard, vps_stats, purge, fetch, ask, issues_report,
report_problem, multi_action, set_journal, journal_show, journal_prompt,
review, export, working_history, converse, persona,
smalltalk, out_of_scope, self_query, boss_query, memory_why, proactive_prefs,
boss_memory_update, style_update, trace_query, memory_review, memory_cleanup,
memory, remember, forget, confirm, amend, cancel, recall_conversation` —
unknown actions rejected; low confidence falls to `converse`. Several flows are
resolved **deterministically before the router** (typed purge phrase, explicit
"Категория — X", fired-reminder acks, time-only reminder commands, "Давай md").

### 2.5 LLM gateway

- **Profiles:** named per-purpose model profiles (router, ingest,
  converse_warm, ask_grounded, curator…) with primary + fallback chain,
  env-overridable via `LLM_PROFILES_JSON`. Default chat model
  `deepseek-4-flash`; ultimate fallback `openai-gpt-oss-20b`; vision
  `llama-4-maverick` (strong closed models are 403/tier-locked on this DO tier).
- **Resilience:** transient 429/5xx/timeout → one quick same-model retry then
  failover with a short (≤20 s) cooldown; hard errors (401/403) fail over
  immediately with the full cooldown. Truncated/malformed bodies
  (`IncompleteRead`, mid-multibyte `UnicodeDecodeError`) wrap as `LLMError`
  (2026-07-16) so failover always engages.
- **Metering:** every call logged to `llm_usage` (tokens estimated from text
  length when the provider omits usage; metering happens even for
  billed-but-empty responses). Pricing table `DEFAULT_PRICING` must contain
  every live slug (unpriced slugs bill at a conservative $3/$15 default — a
  known operational trap). Budget: daily + monthly caps, warn at 80%, hard
  `BudgetExceeded` stop that sits above failover; runtime override via chat.
- **STT:** local whisper binary / local whisper-server / remote (DO) modes.

### 2.6 Data model (SQLite, WAL, additive migrations)

Tables: `messages` (notes; forward origin, category, summary, status,
stable note numbers; separate knowledge lifecycle 2026-07-17 —
`knowledge_state`/`note_purpose`/`saved_reason`/`review_at`/`expires_at`/
`use_count`/`last_used_at`/`archived_at`) · `facts` · `urls` · `images` ·
`files` · `chunks` (embeddings for retrieval) · `categories` ·
`journal_definitions` + `journal_entries` (structured journals 2026-07-17;
manual cascades) · `reminders` + `reminder_events` ·
`conversation` (verbatim dialog, source-tagged boss/forward; never pruned) ·
`pending_actions` (single confirmation slot per chat, TTL) · `preferences` ·
`kv` · `llm_usage` (never purged) · `model_cooldowns` · `boss_profile_items` ·
`memory_candidates` (evidence, source trace, recurrence) · `self_facts` ·
`cara_life` · `relationship_events` · `issues` (immutable incidents,
`status=observed`) · `issue_patterns` (normalized open/resolved/legacy
lifecycle) · `traces` + `trace_events` · `events` · `jobs` · `proactive_log` ·
`list_views` (pagination tokens) · `telegram_updates` (durable inbox) ·
`feedback` (category corrections). Telemetry pruned past 90 days
(`TELEMETRY_RETENTION_DAYS`); spend/conversation/issues/memory never pruned.

### 2.7 Security & guardrails

- **AuthZ:** double-keyed owner gate on messages, callbacks, reactions;
  fail-closed empty allowlist; stranger updates only touch the durable inbox
  (bounded growth) and are dropped.
- **Prompt injection:** forwards never reach the router as instructions
  (ingest-only, one narrow title-datum carve-out); every LLM surface fences
  untrusted content (`<message>`/"DATA ONLY" labels); conversation replay tags
  forwarded turns; memory extraction requires verbatim quotes from
  **non-forward** boss turns (a forwarded post cannot poison the profile).
- **Action truth:** template catalogue guard (final verbs require a declared
  lifecycle state, enforced at runtime AND by a catalogue-wide test); free-form
  converse replies are scanned for action/artifact claims and fail closed to an
  honest template; only deterministic handlers may send documents.
- **Destructive ops:** purge = preview (== execute, disclosed) → exact typed
  phrase (5-min TTL, single-shot, matched deterministically pre-router);
  item deletes confirm-gated; `llm_usage`/`preferences` always preserved.
- **SSRF (fetch):** scheme whitelist, credential-URL block, DNS-rebinding
  defense via IP-pinned connections, per-hop redirect re-validation (cap 5),
  env-proxy disabled, private/link-local/metadata/IPv6 ranges blocked.
- **Secrets:** untracked `.env` (0600); no secrets in repo (verified by
  review); backups encrypted before leaving the box; recovery key held
  off-VPS/off-repo.

### 2.8 Ops & delivery

- **Deploy:** `deploy.sh` — ONE ssh connection per mode (hardened box trips on
  rapid connections): tar-push working tree → run full test suite on the box →
  idempotent installer (backs up replaced files, preserves env, `py_compile`
  gate, module allowlist `MODULES` guarded by an AST test) → restart → verify
  `is-active`. `--test` (no install), `--pull` (git, provable deployed==commit),
  `--rollback <ref>`.
- **Testing:** 575 offline unit tests (no network — all seams mocked;
  deterministic injected clocks; golden transcripts that fail on unscripted LLM
  calls). Windows workstation has no Python; tests always run on the VPS stage.
- **Observability:** per-update traces; immutable incidents + actionable issue
  patterns; weekly review surfaces both; model-health tick; deploy build notice
  auto-posted to a fleet ops Telegram bot on version change.
- **Backups:** daily job; local gzip rotation (keep 7) + encrypted off-box copy.

---

## 3. Quality status (as of 2026-07-16 review + fix batch)

A five-dimension review (core loop, skills, security, spec consistency,
tests/deploy) found the codebase unusually defensive for its class; all seven
hard rules verified enforced in code with regression tests. The top findings
were fixed and deployed same-day (commit `f408e52`): purge preview/execute
alignment, note-number vs DB-id mixup, own-photo storage retirement, LLM
transport taxonomy, reminder follow-up seams (partial-draft loop, «завтра в N
часов» parsing, recurrence-advance undo), boss-memory digit grab, installer
REPLACE_ME anchor, spec-consistency sweep.

### 3.1 Known gaps — open, candidate improvement backlog

> **2026-07-17 update:** the notes/journals plan v1.1 shipped in full the same
> day (Phase 0 + Batches 1–4). Items **2, 3, 5** below were its Phase-0 fixes
> and are now CLOSED (kept struck-through for context); the plan also delivered
> note lifecycle/review/resurfacing, structured journals + Gratitude, and the
> saved-to-used review metrics described in §1.4.

**Correctness / durability (from the review):**
1. Forwarded voice/audio STT (`do_read_media`) bills remote transcription at
   `duration=0` → metered as 1 second regardless of length (budget undercount).
2. ~~Weekly-review scorecard can print a negative "first-guess categories"
   score~~ — **fixed 2026-07-17 (P0-2)**: the metric derives from the period's
   own messages, always 0 ≤ K ≤ M.
3. ~~A forwarded album is acked before it's persisted~~ — **fixed 2026-07-17
   (P0-3)**: parts stay `pending` in the durable inbox until the album files;
   startup replay + honest flush-failure reply + dead-letter.
4. Durable-job retries have zero backoff: both attempts burn within one drain
   pass, so transient failures (e.g. a network blip during backup upload)
   become terminal in under a second.
5. ~~`merge_categories` into a new name silently strips `kind='journal'`~~ —
   **fixed 2026-07-17 (P0-1)**: journal kind is contagious on merge; a
   structured-journal definition follows its category too.
6. Embedding-cache fingerprint `(COUNT, MAX(id), SUM(id))` is defeated by
   SQLite rowid reuse on re-index — `ask` can serve pre-edit chunks.
7. Two same-second `fetch` calls collide on the synthetic message id; the
   second is silently dropped.
8. `chat_profile` can try the same model twice when primary == fallback
   (no dedup of the model list).
9. Overview shows a fired/overdue reminder as "next"; `do_reschedule` ignores
   a one-element `ids` list; backslash-unescape ordering bugs in PDF/JSON
   salvage paths (garbled fallback text only).

**Security (low, one-line fixes):**
10. `100.64.0.0/10` (CGNAT) passes the SSRF block.
11. Replayed forwarded text isn't newline-collapsed inside prompt fences
    (fake-turn fabrication; impact capped by confirm gates).

**Tests / deploy hygiene:**
12. The long-poll loop itself (`Agent.run`, offset persistence, SIGTERM) and
    album settling have no test coverage.
13. Golden harness doesn't patch `tg_send_photo`/`tg_send_document`/
    `tg_download`; no suite-wide network kill-switch.
14. One vacuous test (proactive cap logs the real date, not the simulated day).
15. Deploy tar-push mode never cleans the remote stage dir (stale/renamed test
    files keep running); the installer's env heredoc is a stale subset of
    `tg-ingest-agent.env.example`; the repo's `tg-ingest-agent.service` is a
    dead duplicate of the installer heredoc; ~15 config knobs undocumented in
    env.example; installer backups dir grows unboundedly.
16. Dead Pilot-era files to archive: `migrate-cara-to-pd.sh`,
    `split-cara-nikki.sh`, `split-*.json`, `known_hosts_pilot_rnd`,
    `stt_probe.py`. (2026-07-26: the split script + its two JSONs moved to
    `archive/2026-07-03-cara-nikki-split/`; the rest are still at the root.)

**Product backlog (owner-acknowledged, ask before building):**
17. Memory consolidation/forgetting: candidate dedup/contradiction shipped;
    decay, transcript pruning, episodic memory still open.
18. Cara business/persona logical split plan (approved 2026-06-21, not built;
    meetings/relationship off-limits post-Nikki).
19. Dormant knobs that exist but are off by default: DO Spaces storage backend,
    morning-brief tuning, work-register window tuning.

### 3.2 Operating constraints for any proposal

- stdlib-only Python 3 (no pip deps on the box), single process, SQLite.
- 4 GB RAM droplet (2 vCPU; ~3.8 GiB usable) shared with Nikki's process.
- Model access limited to DO Gradient open-weight tier (Claude/GPT-4o 403).
- Hard rules (§1.3) are non-negotiable; every change must update
  `CARA.md` + `SOLUTION.md` in the same commit, extend tests, and deploy via
  `deploy.sh` (tests run on the box).

---

## 4. Questions this analysis should answer

1. **Prioritization:** rank §3.1 items 1–16 by user-visible risk vs effort;
   which belong in the next batch?
2. **Durability architecture:** is the album-buffer ack-before-persist (item 3)
   worth a durable redesign (e.g. buffering album parts in `telegram_updates`
   until settle), or is a narrower fix enough?
3. **Job runner:** minimal backoff design (item 4) consistent with the
   single-tick drain model?
4. **Retrieval:** at what KB size does the in-process cosine + rowid-fingerprint
   cache (item 6) need replacing, and with what (still stdlib-only)?
5. **Test strategy:** cheapest way to cover the poll loop / album settling /
   network kill-switch (items 12–13) within the offline-unittest constraint?
6. **Product direction:** given the friendly-register scope and the closed
   action set, what new user-visible capabilities have the best value/effort —
   e.g. richer calendar, spaced-repetition memory review, better weekly
   analytics, multi-item batch operations (currently `multi_action` declines)?
7. **Memory evolution:** a safe design for decay/forgetting (item 17) that
   preserves the confirm-before-store and provenance rules?
