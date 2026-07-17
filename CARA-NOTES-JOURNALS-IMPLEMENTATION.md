# Cara — Notes Lifecycle and Structured Journals Implementation Specification

```yaml
document:
  id: cara-notes-journals-implementation
  version: 1.1
  date: 2026-07-17
  status: approved-for-implementation
  supersedes: v1.0 (external draft; corrected — see "Changes from v1.0")
  audience: implementation agent working in this repository
  source_of_truth:
    - CARA.md
    - SOLUTION.md
  scope:
    - Phase 0 prerequisite bug fixes (journal-kind merge loss, scorecard metric,
      forwarded-album durability)
    - a useful lifecycle for saved notes (purpose, active/inbox/archive, review)
    - deterministic note review and contextual resurfacing
    - structured event journals with a closed type registry
    - Gratitude as the first built-in structured journal type
  excluded:
    - retiring forwarded-album ingest (KEPT — owner decision 2026-07-17)
    - general autonomous planning, tasks/projects subsystem
    - browser/shell automation, multi-user support
    - automatic deletion of user content
```

## Changes from v1.0 (authoritative corrections)

1. **D-01 rewritten.** v1.0 proposed retiring ALL Telegram album persistence.
   That conflated two things. The owner's actual decisions:
   - The boss's **own photos** are never stored — ALREADY SHIPPED 2026-07-16
     (`f408e52`): `_own_photo_turn` guard, `own_photo_not_stored` decline
     template, dead `_turn_media_parts` removed. Do NOT redo or extend this.
   - **Forwarded albums remain a saveable unit** ("album = one note" via
     `finalize(parts)`). This is core inbox behavior — most rich channel posts
     arrive as media groups. What v1.0 read as "legacy" is actually an open
     **durability** finding; Phase 0 fixes it (see P0-3).
2. **Single pending slot designed in.** `pending_actions` is one row per chat
   (PK = chat_id). The one-card capture flow must sequence pendings, never
   stack them (§8.2).
3. **No `CHANGELOG.md`.** The two-maintained-docs rule stands (owner decision;
   a third spec doc was retired earlier). Historical/retired details stay in
   `SOLUTION.md` as dated rows marked historical.
4. **Cascades are manual.** The repo does not rely on SQLite FK enforcement;
   `store.delete_message`/purge paths cascade explicitly. `journal_entries`
   cleanup extends those paths — do not depend on `ON DELETE CASCADE`.
5. **"Active context" concept removed.** v1.0's `[Link]`-to-active-context
   button referenced an entity that does not exist. Out of scope (§20 stands).
6. **Journal numbering committed.** Journal entries are messages and reuse the
   existing lazy stable note number: user-visible form `J#<note_no>`. No
   second counter, no renumbering.
7. **v1.0's Russian strings were mojibake** (encoding corruption). All RU
   copy in this file is authoritative; do not copy strings from v1.0.

## 1. Execution contract

Implement in dependency order. Do not redesign unrelated parts of Cara.

Before changing code: read the current `CARA.md`, `SOLUTION.md`, `CLAUDE.md`,
`store.py` migrations (`_migrate`), the router action set, `skill_manifest.py`,
`action_truth.py`, and the relevant tests. Preserve user changes.

Every batch must:

- remain one stdlib-only Python 3 process with SQLite WAL; no pip deps;
- preserve the owner-only gate and all prompt-injection fencing;
- keep `skill_manifest.assert_covers(router.ACTIONS)` passing;
- perform no inferred state change without the existing confirmation flow;
- keep destructive operations typed-phrase-gated; preview == execute;
- preserve stable note numbers, raw text, provenance, attachments;
- use additive, idempotent migrations only; no LLM/network calls in migrations;
- update `CARA.md` + `SOLUTION.md` in the same commit as behavior;
- add offline tests (mocked LLM/Telegram/time/network); new final-verb
  templates need `action_truth.TEMPLATE_STATES` entries or the catalogue test
  fails closed;
- run the full suite on the VPS stage (`deploy.sh --test`) before install;
  deploy via `deploy.sh`; commit + push (`origin/main`) every batch; update the
  PD-VPS KB for every deploy.

If repository details differ from this document, adapt the mechanics while
preserving behavior and acceptance criteria; record material deviations in
`SOLUTION.md`.

## 2. Product decisions

### D-01 — Forwarded albums stay; own photos stay retired (REVISED)

- Forwarded media groups keep saving as **one note** (existing behavior).
- The boss's own photos are never stored (shipped 2026-07-16) — unchanged.
- Phase 0 makes forwarded-album ingest **durable** (P0-3): no silent loss on
  crash-in-settle-window or flush error.

### D-02 — Category is not note lifecycle

Category answers "what is this about?". Purpose/lifecycle answer: why saved,
useful now?, revisit when?, active/temporary/archived, did it lead to action?
Notes get separate topic, purpose, and lifecycle dimensions.

### D-03 — No automatic content deletion

Cara may recommend archiving/deletion; age, non-use, or model-assessed low
value never auto-deletes. Archive is reversible and searchable. Deletion uses
existing confirmation rules.

### D-04 — Journals are semantic entities

`categories.kind='journal'` remains for compatibility during migration, but
journal behavior is represented by explicit journal definitions + entries.

### D-05 — Gratitude is the first built-in structured entry type.

### D-06 — Journal prompts are opt-in

No unsolicited journal conversations. Any journal prompt honors quiet hours,
weekday prefs, global proactive caps, per-journal disable.

### D-07 — Derived structure never replaces source truth

Summaries, purposes, payload fields are derived metadata; original text,
timestamp, provenance stay authoritative and available.

## 3. Phase 0 — prerequisite fixes (ship first, one batch)

### P0-1 `merge_categories` must not strip journal protection

Current: `merge_categories` (store.py) creates/uses dst via `ensure_category`
(default `kind='inbox'`) and deletes the src row — a journal merged into a new
name silently loses dated recall and its purge exemption.

Fix: journal-ness is **contagious on merge** — if src `kind='journal'`, set dst
`kind='journal'` after the move (whether dst was just created or already
existed). Protection is never silently dropped; the boss can still unset via
the existing «X больше не дневник» flow. Tests: merge journal→new name keeps
kind; merge journal→existing inbox category upgrades it; merge inbox→inbox
unchanged.

### P0-2 First-guess category metric must be computed from messages

Current: scorecard prints `confirmed_count - corrections_count` where
corrections are ALL period feedback rows (recategorizing old notes included) —
can go negative («категорий с первого раза: -8/2»).

Fix: compute directly from `messages` for the period: K = confirmed messages
received in period with `category = suggested_category`; M = confirmed
messages received in period. Print `K/M` (0 ≤ K ≤ M always); the markdown
scorecard's "corrected" becomes `M-K`. Keep the separate corrections listing
as-is (it is period activity, correctly labeled). Tests: bulk-recategorizing
old notes does not affect K/M; a corrected fresh note decrements K.

### P0-3 Forwarded-album durability (no silent loss)

Current: album parts buffer in memory; each part's `telegram_updates` row is
marked `done` at buffer time and the offset advances — a crash inside the
settle window (~3 s) loses the album with no retry/reply/issue; a finalize
exception during flush only logs.

Fix (consistent with the existing durable-inbox design):

1. Buffering an album part returns a **defer** signal: the update row stays
   `pending` (attempts already incremented); the offset may still advance.
2. The album buffer records each part's `update_id`; successful flush marks
   all part rows `done`.
3. **Startup replay**: on Agent start, load `telegram_updates` rows with
   `status='pending'` (oldest first, bounded), and re-handle their payloads
   before polling; attempts-guarded so a poison album dead-letters after
   `UPDATE_MAX_ATTEMPTS` like any update. Re-buffered parts dedupe by
   `message_id`.
4. A finalize exception during flush: honest failure reply to the boss
   (existing `stored_retry`/`llm_error`-class template), `issue_add`, and the
   part rows dead-letter via `telegram_update_fail(terminal=...)` per attempts
   — never a silent log-only drop.
5. Graceful shutdown keeps the existing `flush_albums(force=True)`.

Tests: buffered part rows stay pending until flush; flush marks done; simulated
restart (new Agent on same DB) replays pending parts and files ONE album note;
flush exception replies + dead-letters; replay dedupes; poison album stops
after max attempts.

## 4. Target user experience

### 4.1 Saving a normal reference

> Boss: «Сохрани эту статью про AI-ready data.»
> Cara: «Положу в „Data platforms" как справочный материал — похоже, пригодится
> для работы над платформой. [Сохранить] [Временно] [Не сохранять]»

The card shows: suggested category; proposed purpose; a short source-grounded
reason; only context-relevant buttons. No long forms, no five generic buttons.

### 4.2 Saving something actionable

Forward contains a deadline →

> «Сохранила бы в „Кипр". Вижу возможное действие: проверить дедлайн подачи
> до 1 сентября. [Сохранить + напоминание] [Только сохранить] [Не сохранять]»

Rules: the detected follow-up is data, not an instruction; no reminder exists
until the boss confirms a normal reminder draft; deterministic date parsing
validates the model proposal; with no reliable time Cara may offer review
placement, never an invented date.

**Pending-slot sequencing (binding):** the capture card occupies the single
pending slot as today (`kind='category'`-family). Choosing «Сохранить +
напоминание» must (a) commit the note atomically, (b) clear the capture
pending, then (c) open the reminder draft as a NEW pending
(`reminder`/`reminder_partial`) — one slot at a time, never stacked, never
clobbering an unrelated in-flight confirmation (reuse the `present_suggestion`
slot-guard convention).

### 4.3 Reviewing notes

> Boss: «Покажи, что стоит пересмотреть.»

At most three snapshotted items, each with a deterministic reason (review due /
temporary expiring / actionable without follow-up / untriaged / old unused /
likely duplicate). Actions: open, keep active, review later, archive, restore
(in archive view), create reminder (existing flow), delete (existing
confirmation).

### 4.4 Contextual resurfacing

After a grounded answer, at most ONE compact suggestion («Ты ещё сохранял #41
про это в мае — открыть?»), only above the relevance threshold, never during
personal/emotional conversation; archived material surfaces only for explicit
KB search. "Used" is logged only on actual cite/open/delivery.

### 4.5 Recording gratitude

> Boss: «Я благодарен Вере за помощь с подготовкой презентации.»
> Cara: «Добавить в Благодарность за сегодня?
> Кому: Вера · За что: помогла подготовить презентацию
> [Добавить] [Изменить] [Отмена]»

After confirmation the source text is unchanged; structured fields are derived
metadata.

## 5. Data model (additive migrations, repo `_migrate` conventions)

### 5.1 `messages` additions (knowledge lifecycle — separate from ingest `status`)

| Column | Type | Meaning |
|---|---|---|
| `knowledge_state` | TEXT | `inbox` / `active` / `archived` (NULL for non-note rows) |
| `note_purpose` | TEXT | `reference` / `source` / `idea` / `decision` / `temporary` / `actionable` |
| `saved_reason` | TEXT | short source-grounded likely-use note (length-bounded ≤200) |
| `review_at` | TEXT UTC | optional next review time |
| `expires_at` | TEXT UTC | advisory expiry for `temporary`; never auto-delete |
| `last_used_at` | TEXT UTC | last real delivery/open/citation |
| `use_count` | INTEGER DEFAULT 0 | real uses |
| `archived_at` | TEXT UTC | archive time |
| `archive_reason` | TEXT | explicit/deterministic reason (length-bounded ≤200) |

Validation: closed enums, unknown model values fall back (`reference`/`none`);
`expires_at` only for `temporary`; archive/restore set state + fields
atomically; `raw_text` never touched by triage; journal-backed messages are
excluded from ordinary lifecycle views.

### 5.2 Backfill (deterministic, no LLM)

Confirmed non-journal → `active`/`reference`; suggested/pending non-journal →
`inbox`; failed/duplicate keep ingest status, excluded from review; journal
rows handled by §7; NO automatic `review_at` on existing notes.

### 5.3 Note-use events

Reuse the existing `events` infrastructure (`events.record_done`) with kinds:
`note_triaged, note_opened, note_cited, note_resurfaced,
note_resurface_accepted, note_kept, note_review_deferred, note_archived,
note_restored, note_reminder_proposed, note_reminder_created` — each with
message_id, trace id, bounded metadata. No new table unless the existing model
proves inadequate (document why if so).

### 5.4 `journal_definitions`

Columns (adapt syntax to repo conventions): `id PK, slug UNIQUE NOT NULL,
display_name NOT NULL, entry_type NOT NULL, sensitivity DEFAULT 'personal',
active DEFAULT 1, proactive_enabled DEFAULT 0, prompt_config_json,
created_at, updated_at`. Slug stable; entry types from the closed registry
(§6); proactive off by default; prompt schedule is validated config, never
executable text.

### 5.5 `journal_entries`

`id PK, journal_id → journal_definitions, message_id UNIQUE → messages,
occurred_at NOT NULL, payload_json DEFAULT '{}', extraction_status DEFAULT
'complete', created_at, updated_at`.

- One source message per entry; message keeps raw text/attachments/provenance.
- `occurred_at` = explicit reliable event time else message time.
- Payload validated against the entry type; unknown fields dropped; invalid
  payload degrades to `{}` (entry text preserved).
- Excluded from ordinary note lists.
- **Deletion cascades through the existing MANUAL cascade paths**
  (`delete_message`, purge) — extend them; do not rely on FK pragmas.

### 5.6 Stable addressing (committed)

Journal entries reuse the linked message's lazy stable note number; UI form
`J#41`. Resolver accepts `J#41` and legacy `#41` when unambiguous. Never
renumber. No second counter.

### 5.7 Indexes

`messages(knowledge_state, review_at)` · `messages(knowledge_state,
expires_at)` · `journal_entries(journal_id, occurred_at)` · unique
`journal_entries(message_id)` · unique `journal_definitions(slug)`. Check
existing indexes/query plans before adding others.

## 6. Closed journal type registry (in code)

| Entry type | Structured fields | Sensitivity floor |
|---|---|---|
| `gratitude` | subject, reason, impact, people, tags, follow_up | personal |
| `win` | achievement, effort, significance, people, tags | personal |
| `lesson` | event, insight, future_application, tags | personal |
| `decision` | decision, rationale, revisit_at, tags | personal |
| `memorable_moment` | event, people, place, meaning, tags | personal |
| `mood` | label, intensity, context, tags | sensitive |
| `health` | event, severity, context, tags | sensitive |
| `mistake` | event, lesson, prevention, tags | sensitive |
| `idea` | idea, hypothesis, next_experiment, tags | personal |
| `generic_event` | title, context, meaning, tags | personal |

Only `gratitude` fully exposed in the first release; others are the extension
contract (inactive until copy + tests exist). Custom journal names map to a
registered type. The LLM cannot invent schemas or validators. Numeric
intensity/severity only when explicitly supplied/confirmed. No medical,
psychological, or relationship diagnoses; analytics descriptive, citing
entries («чаще всего в записях появлялась Вера», never "is what makes you
happy").

## 7. Gratitude built-in + migration

Definition: slug `gratitude`, display «Благодарность» (EN render "Gratitude"
per message language — ONE journal), entry_type `gratitude`, sensitivity
`personal`, proactive off.

**Recognition:** explicit forms — «запиши в благодарности …», «добавь в
благодарность …», "add this to gratitude", «я благодарен/благодарна …» when
clearly an entry intent or an active opt-in gratitude context. Casual «спасибо»
(incl. thanks to Cara) and reactions are conversation, never entries.

**Extraction:** model proposes `{subject, reason, impact, people, tags,
follow_up}`; enforcement — exact source text preserved; lexical support
required between source and every non-null field; bounded lengths; invented
names rejected; core fields shown before save; «Изменить» edits the pending
draft; write only after confirmation.

**Migration (idempotent, additive, no bulk LLM):**

1. Discover existing gratitude categories: normalized stem `благодар`, EN
   aliases gratitude/grateful/thankfulness, existing journal-kind data.
   Multiple candidates: P0-1 must already be shipped; select canonical
   deterministically; preserve originals in provenance; never renumber.
2. Each confirmed historical message in the canonical journal → one
   `journal_entries` row, `occurred_at` = message time, `payload_json={}`,
   `extraction_status='legacy_unstructured'`; readable/exportable from raw
   text. Later enrichment only on demand, confirm-gated, never rewriting
   source text.
3. Compatibility: «покажи благодарности» keeps working; singular/plural
   aliases → one journal (reuse the existing canonical-journal alias fix); no
   parallel containers; general notes list excludes entries; notes purge
   spares journals; journal purge gets its own typed phrase.

**Retrieval/analysis:** day/week/month/range views; filter by person/tag;
«за что я был благодарен в июле?»; deterministic person counts from validated
fields; descriptive themes with `J#N` citations; per-journal Markdown export;
`follow_up` → normal reminder draft.

## 8. Capture and triage

### 8.1 Ingest suggestion schema extension (optional fields)

```json
{"category": "...", "alternatives": [], "summary": "...", "facts": [],
 "note_purpose": "reference", "saved_reason": "...",
 "review_policy": "none", "action_candidate": null}
```

`review_policy` ∈ none/review_7d/review_30d/temporary_30d/temporary_90d.
Unknown purpose → `reference`; unknown policy → `none`; `saved_reason` must
describe likely use, not the act of saving (extend the existing
`_is_meta_summary` guard class); `action_candidate` dates re-parsed by
deterministic reminder code; ingest executes no actions.

### 8.2 One confirmation card (single pending slot!)

One compact card, committed atomically with the existing category-save
confirmation. Conditional buttons:

- reliable action/date → `Сохранить + напоминание / Только сохранить / Не сохранять`
- journal intent → `Добавить в журнал / Изменить / Отмена`
- ordinary → `Сохранить / Временно / Не сохранять`

Sequencing per §4.2. Callback tokens short, backed by pending/list-view
storage; never raw user text in callback data.

### 8.3 Triage commands — ONE new router action

```json
{"action": "note_lifecycle",
 "params": {"operation": "archive|restore|keep|set_purpose|review_later|make_temporary",
            "ids": [41], "purpose": null, "when": null}}
```

Covers: keep/archive/restore #N; make #N temporary for 30 days; review #N next
month; change purpose; show inbox/active/archive; show review-due (views may
reuse `list_items` params instead — decide against repo conventions, keep the
action count minimal). Bulk archive stays confirmation-gated per manifest; a
single reversible archive follows the explicit-command convention and offers
undo.

## 9. Review and resurfacing

**Selection (deterministic, max 3):** review_at due → temporary expiring →
actionable without follow-up → untriaged inbox → old active `use_count=0` →
likely duplicate (existing similarity logic). Exclude journal entries,
failed/duplicate/deleted, recently shown, items in a live snapshot, sensitive
material from unsolicited review.

**Snapshot:** reuse `list_views`-style durable tokens; ordinal follow-ups
(«второе в архив») resolve against the snapshot, not a recomputed list. TTL:
24 h for explicit review, 15 min for a proactive nudge follow-up (existing
`proactive_context` convention).

**Proactive:** replaces the generic "unsorted" pressure with a review
invitation («Нашла три сохранёнки, по которым стоит принять решение —
показать?»). Suggestion-only; honors global enable/quiet hours/days/daily cap
(`NONURGENT_KEYS` accounting); no same-item repeats per day; "sent" only after
delivery; declined batch suppressed for a configurable period; journal prompts
have separate per-journal opt-in.

**Usage accounting:** `use_count`/`last_used_at` increment ONLY on detail
open, citation in a delivered grounded answer, inclusion in a delivered
export/synthesis, or accepted resurfacing. Ranking/embedding retrieval does
not count.

## 10. Lists and retrieval

- Default «что у тебя есть?» becomes a compact overview: inbox / active /
  review-due / archived counts, recent useful notes, journals separate; full
  paginated list still available.
- Active ranks normally; inbox searchable, labeled untriaged; archived
  included for explicit KB searches (modest rank penalty ok); journal entries
  retrieved only for journal-targeted questions; synthesized claims cite
  stable references; raw lists stay deterministic (never free-texted).
- Related-note suggestion: one, from real retrieval metadata, deterministic
  identity, or nothing.

## 11. Actions and permission manifest

Reuse `set_journal`, `journal_show`, `export`, existing confirmation infra.
New logical operations and policies:

| Operation | Risk | Confirmation |
|---|---|---|
| show/list journal | read-only | no |
| propose journal entry | draft write | yes before save |
| edit pending entry | draft write | pending context |
| create custom journal | state write | explicit confirmation |
| enable scheduled prompt | proactive pref | explicit confirmation |
| export journal | read-only artifact | explicit request |
| delete entry | destructive | existing item-delete confirmation |
| purge journal | destructive | exact typed phrase |

`skill_manifest` covers every new router-visible action and proactive check;
startup coverage assertions keep passing.

## 12. Templates and truthfulness

Bilingual deterministic templates for: note archived/restored/kept/deferred;
no notes due; review intro; gratitude draft/confirmed/cancelled; journal
created; prompt enabled/disabled; journal purge preview; legacy-unstructured
label; invalid-structure fallback. (No album templates — albums stay.)

Action-truth: final verbs («сохранила», «в архиве», «удалила», …) only after
the committed transition, each template mapped in `TEMPLATE_STATES`; free-form
converse cannot claim a journal entry/file was created (existing guards);
failed extraction may still save the confirmed raw entry but must not claim
structured fields were understood; delivery failures never mark proactive or
scheduled sends done.

## 13. Documentation

- `CARA.md` + `SOLUTION.md` updated per batch (same commit): new lifecycle,
  review/resurfacing, journals, Gratitude entity, journal export/purge, opt-in
  prompts, the P0 fixes, one authoritative journal-numbering rule (§5.6).
- Retired/changed behavior recorded as dated historical rows in
  `SOLUTION.md` — **no `CHANGELOG.md`**.
- Regenerate the PRD snapshot only after code + maintained docs agree
  (final batch).

## 14. Dependency-ordered task list

```yaml
tasks:
  - id: P0-1  # merge_categories journal-kind preservation
    depends_on: []
  - id: P0-2  # first-guess metric from messages
    depends_on: []
  - id: P0-3  # forwarded-album durability (defer done, startup replay, honest flush errors)
    depends_on: []
  - id: NTE-001  # lifecycle schema + deterministic backfill
    depends_on: [P0-1]
  - id: NTE-002  # lifecycle CRUD + usage accounting (events)
    depends_on: [NTE-001]
  - id: NTE-003  # ingest suggestion extension + one-card confirmation (pending-slot sequencing)
    depends_on: [NTE-002]
  - id: NTE-004  # deterministic three-item review + stable snapshot
    depends_on: [NTE-002]
  - id: NTE-005  # state-aware lists/overview/retrieval
    depends_on: [NTE-002, NTE-004]
  - id: NTE-006  # contextual resurfacing + proactive review invitation
    depends_on: [NTE-004, NTE-005]
  - id: JRN-001  # journal_definitions + journal_entries schema
    depends_on: [NTE-001]
  - id: JRN-002  # closed type registry (gratitude active)
    depends_on: [JRN-001]
  - id: JRN-003  # gratitude capture + confirmation
    depends_on: [JRN-002, NTE-003]
  - id: JRN-004  # legacy gratitude migration (no bulk LLM)
    depends_on: [JRN-002, P0-1]
  - id: JRN-005  # journal recall/filters/export/typed purge
    depends_on: [JRN-003, JRN-004, NTE-005]
  - id: JRN-006  # opt-in journal prompts
    depends_on: [JRN-003, NTE-006]
  - id: MET-001  # saved-to-used metrics replace pile-size metrics
    depends_on: [NTE-005, NTE-006, JRN-005]
  - id: DOC-001  # final doc sweep (CARA.md, SOLUTION.md)
    depends_on: [MET-001, JRN-006]
  - id: DOC-002  # regenerate PRD snapshot
    depends_on: [DOC-001]
  - id: REL-001  # final verification + deploy + smoke test
    depends_on: [DOC-002]
```

Deploy batches: **Batch 0** = P0-1..3 · **Batch 1** = NTE-001..003 ·
**Batch 2** = NTE-004..006 · **Batch 3** = JRN-001..006 · **Batch 4** =
MET/DOC/REL. Each batch: tests green on VPS → install → verify → commit →
push → KB update.

## 15. Test plan (per batch, offline, mocked seams)

**Phase 0:** journal-kind survives merge (new name / existing inbox dst);
K/M metric ignores old-note recategorization, always 0≤K≤M; album part rows
pending until flush → done after; restart replay files ONE album note; flush
exception replies + dead-letters; replay dedupes by message_id; poison album
stops at max attempts.

**Migrations:** fresh DB creates everything; existing DB migrates once;
idempotent restarts; backfill per §5.2; journal entries out of note lifecycle;
no numbers/FKs change; manual cascades correct; no LLM/network in migration.

**Lifecycle:** archive/restore reversible; archive keeps chunks/attachments;
archived out of default list, present in explicit search; enums reject
invalid; temporary expiry recommends, never deletes; bulk ops confirm-gated;
final-verb templates only after commit; undo targets the right item.

**Ingest:** purpose/saved_reason persist after confirmation; nothing before;
malformed JSON falls back; meta saved-reason rejected; action candidate never
auto-creates a reminder; forwarded injection can't become an action; invalid
dates rejected; callback tokens carry no raw text.

**Review/resurfacing:** ≤3 items; deterministic priority; ordinal follow-ups
resolve against snapshot; recompute doesn't mutate a live batch; recent items
suppressed; journal/sensitive excluded; ranked-not-delivered ≠ use;
cited/opened counts once; personal conversation gets no resurfacing; sends
logged only after delivery; caps and quiet hours honored.

**Journals/Gratitude:** built-in created exactly once; RU/EN aliases → one
definition; source text unchanged; entries out of note lists; malformed
payload degrades; unknown fields dropped; date filters deterministic; export
cites entries; purge preview == execute; note purge spares journals;
action-truth blocks false claims; explicit request → draft; casual thanks =
conversation; unsupported/invented fields rejected; edit touches pending
draft only; legacy migration idempotent; person counts from validated fields;
follow_up → draft only; prompts off by default.

**Golden transcripts (bilingual):** forwarded album saved as one note
(durability path); normal save; actionable save + reminder sequencing; archive
+ restore; three-item review with ordinal follow-up; resurfacing; gratitude
draft/edit/confirm; casual thanks not saved; historical recall; export; purge
refusal/confirmation. Unscripted LLM/network calls fail the scenario.

## 16. Review metrics (MET-001)

User-facing weekly review reports outcomes: saved; actually used/cited/opened;
converted to reminders; archived/restored; awaiting triage/review; journal
entries by journal; optional descriptive Gratitude themes with citations;
upcoming reviews/temporary items. Operational metrics (first-guess accuracy,
fallbacks, incidents, proactive delivery, spend, model health) stay in the
Cara-health section.

KPI: `capture_to_use_rate = distinct notes used after saving / distinct notes
confirmed`. Secondary: median capture→first-use; review acceptance rate; %
linked to reminder/decision; % archived unused; inbox age/count; repeated
resurfacing rejections. Never optimize for more saves/nudges/entries.

## 17. Rollout & safety

1. Batch 0 (P0) → deploy.
2. Additive schema lands dark (NTE-001) → migration rehearsed against a copied
   production DB (off-OneDrive, per backup conventions) before install.
3. Lifecycle + explicit review on (Batches 1–2).
4. Resurfacing after usage accounting verified.
5. Journals + Gratitude migration (Batch 3).
6. Journal prompts stay disabled until the boss enables them per journal.
7. Metrics/docs/PRD (Batch 4) + smoke test.

Feature flags optional (`NOTE_LIFECYCLE_ENABLED`, `NOTE_REVIEW_ENABLED`,
`NOTE_RESURFACING_ENABLED`, `STRUCTURED_JOURNALS_ENABLED` env knobs fit the
existing config style); remove permanently-dormant flags after stabilization.
Rollback never drops columns/tables; old code tolerates additive schema.

## 18. Definition of done

- Phase 0: journal merges preserve protection; K/M metric always valid;
  forwarded albums survive crash/error paths with honest failure replies.
- Notes have independent lifecycle/purpose; deterministic no-LLM migration;
  overview distinguishes inbox/active/review-due/archive; review shows ≤3
  stable items; archived remains searchable/restorable; usage counts real
  events only.
- Gratitude = one built-in structured journal; legacy entries intact and
  readable; new entries keep raw text + validated fields; entries out of
  general lists/metrics; prompts opt-in; separate recall/export/purge.
- Forwarded albums still save as one note. Own photos stay retired.
- Action-truth, owner gate, fencing, budget, typed-phrase purges intact.
- All tests + golden transcripts pass offline on the VPS stage;
  `CARA.md`/`SOLUTION.md`/PRD snapshot agree with code; deploy + Telegram
  smoke test succeed.

## 19. Out of scope

Projects/goals/tasks subsystems; multi-step plans; dynamic journal schemas;
sentiment/mental-health inference; automatic deletion; bulk LLM enrichment of
history; external calendar expansion; OCR/TTS; browser/shell/MCP/multi-agent
execution. Schema may permit later note→task links; build no unused
infrastructure now.
