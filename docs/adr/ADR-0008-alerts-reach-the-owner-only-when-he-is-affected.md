# ADR-0008: Alerts reach the owner only when he is affected

- Status: Implemented 0a2639a
- Date: 2026-09-07
- Phase: Phase A — the daily loop
- Source: 2026-09-07 review of architecture, code and live behavior against build e3208b8 (report kept off-repo; findings are referenced by id)

## Context

45 of 639 bot turns are model down/back notices; the fallback held every time; the 2×30-min debounce already exists; the owner cannot act on «загляни в доступ к моделям».

## Decision

model_down is sent only when the fallback probe also fails or the profile has no fallback, or STT is down with no backup; one message per transition listing all affected models; provider-side 5xx/overload transitions go to the fleet notification chat; model_back only if a user-affecting model_down was sent; disk, DB-stall, backup and watchdog alerts unchanged; routine flaps appear in the weekly ops tail.

## Consequences

the health monitor stays; the owner sees fewer ⚠️ messages so the ones that remain keep their weight.

## Resolves

dialog-trends#3, product-ux#5, llm-gateway#3 in part

## Depends on

nothing

## Status log

- 2026-09-07: proposed by the review; not yet discussed with the owner.
- 2026-09-07: implemented in `0a2639a` (Phase A batch) — chain-based: fallbacks probed in the same sweep, the boss is told only when a whole chain (primary + fallbacks) is dead or speech is down with no on-disk whisper-cli; one message per transition listing the primaries; flaps a fallback absorbs go to the fleet chat (`_notify_fleet`) and to `events(kind='model_health')` for the markdown «Model health» section.
