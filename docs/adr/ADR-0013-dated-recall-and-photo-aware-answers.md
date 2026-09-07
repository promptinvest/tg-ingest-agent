# ADR-0013: Dated recall and photo-aware answers

- Status: Proposed
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
