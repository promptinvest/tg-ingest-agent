# ADR-0011: Post-window follow-ups bind deterministically; degenerate replies never ship

- Status: Implemented 501b5f9
- Date: 2026-09-07
- Phase: Phase B — honesty and memory
- Source: 2026-09-07 review of architecture, code and live behavior against build e3208b8 (report kept off-repo; findings are referenced by id)

## Context

«Через час» 116 min after a fire fell through the LLM router, was rejected at confidence 0.0, and converse fabricated «напомню снова в 16:11»; a 4-token converse reply 'article\nYes' reached the owner with no issue row.

## Decision

snooze/ack/done phrases that arrive after the 30-min window bind to the last fired one-shot through the deterministic parser (subject guard kept), never to the LLM router with pending=None; validate_route returns a rejection reason (non_json, invalid_action:<name>, pending_only_without_pending) stored on the trace and logged as router_invalid_output instead of unclear_request; a converse output guard rejects replies under two words or in the wrong script for the owner's language, retries once, else sends llm_error and files converse_degenerate.

## Consequences

one deterministic path more before the router; the issues taxonomy separates model output defects from genuine ambiguity.

## Resolves

routing-traces#1, #2, dialog-aug2#2, issues#4

## Depends on

ADR-0024 for the precedence table, but can ship as a guard first

## Status log

- 2026-09-07: proposed by the review; not yet discussed with the owner.
- 2026-09-07: implemented in `501b5f9` (Phase B batch) — after the pending window a bare snooze/ack binds to the last FIRED one-shot (`reminders_fired_unacked`) when `last_reminder_id` no longer resolves — subject guard and the Phase A stale-create guard kept; `router.validate_route_reason` → `non_json` / `invalid_action:<name>` / `pending_only_without_pending`, put on the trace (`router.invalid_output`) and filed as `router_invalid_output` (not `unclear_request`); `Agent._degenerate_reply` (under two words, or no word in his script) → one retry, then `llm_error` + `converse_degenerate`. The precedence table itself is still ADR-0024.
