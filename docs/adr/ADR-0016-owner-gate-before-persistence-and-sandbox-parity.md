# ADR-0016: Owner gate before persistence, and sandbox parity for the main unit

- Status: Implemented 8e25b1c
- Date: 2026-09-07
- Phase: Phase C — robustness, operations, security
- Source: 2026-09-07 review of architecture, code and live behavior against build e3208b8 (report kept off-repo; findings are referenced by id)

## Context

non-owner updates are written to telegram_updates, traces, events and the journal before is_owner runs; the main unit is the only one without MemoryMax/TasksMax although it parses untrusted PDFs on a shared box; the fence sanitizer misses DeepSeek's fullwidth-bar delimiters; bearer keys follow redirects; validate_url raises bare ValueError; failed-fetch URLs are stored verbatim forever.

## Decision

process_update_batch derives chat and sender for the four allowed update kinds and checks is_owner before telegram_update_receive and trace.start; strangers advance the offset, bump a kv counter and produce at most one stranger_traffic issue per day; tg-ingest-agent.service gains MemoryHigh=768M, MemoryMax=1G, TasksMax=64 and the siblings' Protect* directives, not ProcSubset because sysinfo reads /proc; _FENCE_TAG_RE accepts bar look-alikes and start_of_turn/end_of_turn with a per-model-family test table kept beside DEFAULT_PRICING; a common credential_opener with no redirects and no proxies serves every Bearer request; validate_url's error contract is total; URLs are redacted in issues and logs; the three hand-made plaintext DB copies are removed or encrypted.

## Consequences

the in-handler gate stays as defence in depth; one voice note via the cold CLI and one PDF ingest are verified after deploy.

## Resolves

security#1–#9

## Depends on

nothing

## Status log

- 2026-09-07: proposed by the review; not yet discussed with the owner.
- 2026-09-08: implemented in `8e25b1c` — shipped: `process_update_batch` derives chat + sender for all four update kinds (`Agent._update_sender_id`) and checks `is_owner` BEFORE `telegram_update_receive` and `trace.start`; strangers advance the offset, bump kv `stranger_updates`, one `stranger_traffic` issue per UTC day; the unit gains MemoryHigh/MemoryMax/TasksMax and the siblings' Protect*/RestrictRealtime/RemoveIPC (no ProcSubset); `_FENCE_TAG_RE` accepts bar look-alikes and start/end_of_turn, tested per family from `llm.CHAT_TEMPLATE_DELIMITERS`; `common.credential_open` (no redirects, no env proxies) serves the three Bearer sites; `fetch.validate_url` turns a bad port into `fetch_blocked`; `common.redact_url` in hermes' fetch issue/log; the three plaintext DB copies under the state dir's `backups/` were encrypted with the backup format (decrypt + integrity verified) and the plaintext removed.
