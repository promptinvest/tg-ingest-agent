# ADR-0005: The gratitude ritual is journal-aware and deterministic

- Status: Implemented 0a2639a
- Date: 2026-09-07
- Phase: Phase A — the daily loop
- Source: 2026-09-07 review of architecture, code and live behavior against build e3208b8 (report kept off-repo; findings are referenced by id)

## Context

the daily fires the generic reminder card; the owner types «В благодарности -» on most nights; «готово» on that card records nothing yet answers «закрыла»; each entry costs router + ingest summary + extraction, median 28 s, and the card shows a third-person paraphrase.

## Decision

a fired reminder whose title resolves to an active journal renders a journal variant («за что сегодня? напиши строкой, запишу»); a bare «готово» there means skip and says so; a message starting with «в благодарност…» or any non-command text under a fired gratitude context goes straight to the forced journal category with no router call and no ingest summary call; the card shows raw_text verbatim; journals.extract stays on the card so confirmed fields remain visible; an opt-in preference allows auto-save with an «отменить #N» hint.

## Consequences

two model calls and roughly 20 s removed from the nightly path; the ingest summary prompt is untouched for other categories; _META_SUMMARY_RE widened for the remaining cases.

## Resolves

reminders#6, product-ux#2, #4, dialog-aug2#8, ingest-notes-knowledge P5

## Depends on

nothing

## Status log

- 2026-09-07: proposed by the review; not yet discussed with the owner.
- 2026-09-07: implemented in `0a2639a` (Phase A batch) — journal variant of the fired card; «готово» there = skip today; plain text under the card and the «В благодарности —» prefix go to `_deterministic_capture` (no router, no summary call; the prefix is stripped from the stored note text only); the imperative guard was widened; opt-in `gratitude_autosave` pref with the «убери J#N» hint; `_META_SUMMARY_RE` widened.
