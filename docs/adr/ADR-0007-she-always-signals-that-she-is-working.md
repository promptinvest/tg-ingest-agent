# ADR-0007: She always signals that she is working

- Status: Implemented 0a2639a
- Date: 2026-09-07
- Phase: Phase A — the daily loop
- Source: 2026-09-07 review of architecture, code and live behavior against build e3208b8 (report kept off-repo; findings are referenced by id)

## Context

no typing indicator before the router or ingest call, so a forward is silent 9–23 s; a voice note shows 5 s of typing and then silence while whisper blocks the loop about 1 min per 30 s of audio.

## Decision

send_chat_action at the top of dispatch and at the start of suggest_row; for voice longer than 15 s reply «🎤 Слушаю (~N с)…» with record=False and keep a state-free keepalive thread re-sending typing every 4 s while llm.transcribe runs; the thread touches no DB and no shared state.

## Consequences

one extra Telegram call per turn; the STT wait is still synchronous (see ADR-0023).

## Resolves

product-ux#6, architecture#1 in part

## Depends on

nothing

## Status log

- 2026-09-07: proposed by the review; not yet discussed with the owner.
- 2026-09-07: implemented in `0a2639a` (Phase A batch) — typing at the top of `dispatch` and `suggest_row`; «🎤 Слушаю (~N с)…» + a state-free typing keepalive thread for voice notes > 15 s; STT itself still synchronous (ADR-0023).
