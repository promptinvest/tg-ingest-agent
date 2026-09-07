# ADR-0012: Memory consolidation may fold only like into like, and it is a durable job

- Status: Implemented 501b5f9
- Date: 2026-09-07
- Phase: Phase B — honesty and memory
- Source: 2026-09-07 review of architecture, code and live behavior against build e3208b8 (report kept off-repo; findings are referenced by id)

## Context

11 of 30 owner corrections are merged, 0 avoidance rules are live, the 2026-08-24 run merged nine distinct rules, five of them demoted by _tidy_inferred with no keeper; the guidance block is "newest 8" because every correction sits at 0.8; the tick stamps the week before running and swallows LLM/budget failures.

## Decision

source_table=correction rows fold only into a correction or confirmed keeper; _tidy_inferred is gated on deterministic topical overlap with the confirmed fact and skips GUIDANCE_KINDS; add a merged_into column and print keep→drop pairs in the journal line; one-off repair flips rows 24, 85, 94, 96, 106, 108, 132, 157, 177, 180 back to inferred and reactivates 184, one of 182/183, and 137; a repeated correction bumps recurrence and confidence instead of being discarded; standing_guidance orders by confidence, recurrence, recency and reserves two slots for tone rules; the persona's emoji-close invitation is dropped when a no-emoji rule is live; consolidation becomes a JOB_KINDS entry that stamps memory_consolidate_at only on success.

## Consequences

SOLUTION.md §5's "never merges distinct facts" becomes true; consolidation gains a trace and retry; batch rotation uses the enqueue time.

## Resolves

memory-knowledge#1, #2, architecture#8, architecture-P10

## Depends on

nothing

## Status log

- 2026-09-07: proposed by the review; not yet discussed with the owner.
- 2026-09-07: implemented in `501b5f9` (Phase B batch) — correction rows fold only into a correction or a confirmed keeper; `_tidy_inferred` skips GUIDANCE_KINDS and requires `boss_model.topical_overlap` (0.2) with a confirmed fact, which becomes the keeper; `merged_into` column + `store.boss_merge_into`; keep→drop pairs in the job log line; a repeated correction `boss_bump`s recurrence/confidence; `standing_guidance` orders by confidence · recurrence · recency with `TONE_SLOTS`=2; `converse.no_emoji_rule` drops the reaction invitation; consolidation is a `JOB_KINDS` entry stamping `memory_consolidate_at` only on success (24 h enqueue cooldown); the one-off repair (`_repair_consolidation_2026_09_07`) flipped rows 24, 85, 94, 96, 106, 108, 132, 157, 177, 180 to inferred, 137 to confirmed, 184 and the later-seen of 182/183 to inferred — guarded by a kv marker, the exact pre-state and a ≥100-row profile.
