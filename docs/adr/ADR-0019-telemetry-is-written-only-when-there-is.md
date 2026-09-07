# ADR-0019: Telemetry is written only when there is something to say

- Status: Proposed
- Date: 2026-09-07
- Phase: Phase C — robustness, operations, security
- Source: 2026-09-07 review of architecture, code and live behavior against build e3208b8 (report kept off-repo; findings are referenced by id)

## Context

three maintenance jobs every 300 s produce 70.5k of 70.7k job rows and 70.7k of 74.8k traces as no-ops, half the DB file and every backup; the events claim-queue duplicates the inbox and nothing claims it; router.completed stores neither params nor the raw output; 38 percent of August inbound traces have no routing record; album processing runs outside any trace.

## Decision

enqueue retry_sweep only when pending or unindexed rows exist, pending_expire only when an expired pending exists, media_cleanup hourly via available_at; keep JOB_KINDS and runtime.drain tracing; delete events.claim_next/complete/fail/reclaim and record Stage C as retired, keeping record_done for the note-outcome mirror; router.completed carries clipped params, the raw JSON up to 400 chars and the rejection reason; a route_corrected issue is filed when a correction follows a state-write route within 10 min, and the weekly review gains a routing block; every deterministic resolver and callback handler emits router.completed with source=<branch>; flush_albums opens an inbound trace; unused trace stage constants are removed.

## Consequences

about 24 jobs per day instead of 860; the mis-route metric that any router redesign needs finally exists; the 5-minute retry promise is kept.

## Resolves

store-data#1, proactive-jobs-logs#4, architecture#5, router-dispatch#3, routing-traces#3, #5, #8

## Depends on

nothing

## Status log

- 2026-09-07: proposed by the review; not yet discussed with the owner.
