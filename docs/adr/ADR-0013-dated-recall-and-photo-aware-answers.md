# ADR-0013: Dated recall and photo-aware answers

- Status: Implemented 501b5f9
- Date: 2026-09-07
- Phase: Phase B — honesty and memory
- Source: 2026-09-07 review of architecture, code and live behavior against build e3208b8 (report kept off-repo; findings are referenced by id)

## Context

48 of 70 notes are dated journal entries but the ask context hides dates and journal_show has no date parameter, so the documented «за что я был благодарен 17 июня?» cannot be answered; a photo with a question routed to ask never sees the vision description; do_ask marks all six ranked notes as used; an embedder error fails ask outright although keyword search needs no model.

## Decision

render «· YYYY-MM-DD» in the ask context head; journal_show gains date/since/until with a RU/EN date parser reused from the reminder machinery, and the router example moves to it; when an own-photo turn routes to ask, dispatch falls through to converse, which grounds on notes and sees the photo; only notes whose #N appears in the delivered answer are marked used; the embed runs in its own try and _keyword_context always runs, results fused by rank; grounding.ranked logs top-k scores; the 0.25 floor is calibrated offline before any change, then a relative gate and a per-note cap.

## Consequences

the KB path becomes honest about dates and photos at low cost; note-outcome metrics stop counting retrieval as use.

## Resolves

ingest-notes-knowledge#1, #2, #3, #4, #5, memory-knowledge#5, #6

## Depends on

nothing

## Status log

- 2026-09-07: proposed by the review; not yet discussed with the owner.
- 2026-09-07: implemented in `501b5f9` (Phase B batch) — «· YYYY-MM-DD» in the ask context head; `journal_show` gains `date`/`since`/`until` parsed by `reminders.parse_day_phrase` / `journal_window` (RU/EN month names, DD.MM, «17-го», вчера/позавчера; a future day with no year reads as last year's), threaded through the list-view token; the router examples moved to it; a picture-only turn routed to `ask` falls through to `converse`; only notes whose #N the delivered answer names are marked used (`knowledge.cited_note_ids`); the embedding runs in its own try, `_keyword_context` always runs, results fused by rank (`fuse_contexts`); `grounding.ranked` logs the top-k scores; `rank_chunks` adds a relative gate (0.15 under the best) and a per-note cap (2) on top of the kept 0.25 floor — the floor was NOT re-calibrated (the scores are now logged so it can be).
