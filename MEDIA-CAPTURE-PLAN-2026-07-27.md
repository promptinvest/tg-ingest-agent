# Media capture from photos — implementation plan (2026-07-27)

Status: **APPROVED by the operator** (design agreed in-session 2026-07-27). Build AFTER the
round-2 fix batches land and deploy.

## The feature, in the operator's terms

Cara parses any photo the boss sends and understands what it is. If it shows a movie or a
book (or a list of them), she extracts the entries, shows a confirmation card, and on
confirm stores them — categorized — for long-term retrieval and later md export.

## Owner decisions that bind this design (recorded 2026-07-27)

1. **No media is stored at all.** Not the boss's photos, not even for recognized
   movies/books. The photo is processed transiently (downloaded to tmp, vision-parsed,
   deleted). Only the parsed ENTRIES persist. The 2026-07-16 own-photo retirement therefore
   stays fully intact — this feature does not reverse it.
   *Scope note:* this applies to the boss's own photos in this flow. Forwarded-post media
   storage (documented CARA.md behavior) is unchanged.
2. **Entries live in the existing notes system**, in English categories **`Movies`** and
   **`Books`** — long-term storage. RU titles stay RU («Мастер и Маргарита» in category
   `Books`); only the category names are English.
3. **Md export on request** («дай md по Movies») — at arbitrary points in time.
4. No status lifecycle (watched/read) in v1 — the operator did not ask for one. An entry is
   a catalog row, not a task.

## Architecture (agreed)

**Entries are notes.** Each confirmed entry = one confirmed note: summary = title,
category = `Movies`/`Books`, structured fields as facts (`creator: …`, `year: …`,
`genre: …`, `from photo: <visible comments>`), purpose `reference` (no expiry nudges),
chunked+embedded so «ask» finds them. This buys for free: list/detail/delete/recategorize,
purge protection semantics, ask retrieval, the pending-card correction flow.

**One new module `media.py`** (vision classification + extraction + enrichment prompts +
the OpenLibrary/Wikipedia lookups). Handler glue stays in the agent. `media.py` MUST be
added to the installer MODULES list (the AST test enforces).

**No new router actions.** Photos bypass the router (media path). Text-side flows ride
existing actions (`list_items`, `item_detail`, `item_delete`, `purge category`). The only
router-adjacent change: `export` gains `what="category"` + `category` param (few-shot
example + manifest note), rendering an md catalog of that category's notes via the existing
`tg_send_document` path.

## Flow

```
own photo arrives → tmp download → vision CLASSIFY
  ├─ media (movie/book/list)
  │    → EXTRACT verbatim: titles + visible comments (photo-sourced)
  │    → ENRICH per title: creator/year/genre
  │         order: OpenLibrary (books) / Wikipedia API (movies+books) via the existing
  │         SSRF-guarded fetch → LLM knowledge fallback → honestly missing
  │         every field provenance-tagged: photo | lookup | model | missing
  │    → CONFIRMATION CARD (single pending slot, buttons + reply-to-correct):
  │         «Вижу 4 книги: … Год для №3 не нашла. Сохранить все?»
  │    → on confirm: notes in Movies/Books; dedup on (category, normalized title) → merge
  │    → tmp photo deleted (also deleted on every error path — try/finally)
  ├─ document/text-heavy → existing document path (unchanged)
  └─ anything else → she says what she sees; stores nothing
```

## Hard rules that apply

- Extraction vs enrichment are separate LLM calls with separate provenance; the card must
  never present a looked-up/guessed field as photo-sourced (action-truth discipline).
- All model calls through `llm.py` (budget-guarded, priced); vision uses `VISION_MODEL`.
- Lookups go through `fetch.py`'s guarded fetch (SSRF pinning, deadline). OpenLibrary and
  Wikipedia are keyless JSON APIs; NO general web-search API in v1 (owner may add later on
  evidence).
- Untrusted text (photo comments, lookup results) is neutralized before reaching any prompt
  (WP8 sanitizers) and stored verbatim only as note facts.
- Every change updates CARA.md + SOLUTION.md in the same commit (incl. recording decision
  1's scope note against the 2026-07-16 retirement).

## Batches (same pipeline as the fix programme: implement → dual review → revert-proof)

**B1 — capture core:** `media.py` (classify + extract prompts, tmp-file hygiene), the
own-photo path change (classify-then-route instead of flat «фото не сохраняю»; the refusal
template remains for non-media photos), confirmation card + corrections, notes storage +
dedup, Movies/Books auto-categories, tests (golden-transcript: photo → card → confirm →
notes; RU titles; list photo; dedup merge; non-media photo stores nothing; tmp deleted on
error paths).

**B2 — enrichment + export:** OpenLibrary/Wikipedia lookups with provenance + LLM fallback,
field-source rendering in the card, `export what="category"`, docs, tests (lookup mocked at
the network boundary; provenance labels asserted in card text; export md golden file).

Estimated one long session for both. Deploy + verify + TG notice per standing discipline.
