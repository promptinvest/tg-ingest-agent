# ADR-0025: The router prompt pays for the gist of history, not its bytes

- Status: Proposed
- Date: 2026-09-07
- Phase: Phase E — maintainability and documentation
- Source: 2026-09-07 review of architecture, code and live behavior against build e3208b8 (report kept off-repo; findings are referenced by id)

## Context

the router replays 14 turns of up to 4096 chars each plus the current turn twice; SOLUTION §12 already names the per-row clip as the surgical fix; two-stage routing and prompt reordering were rejected as premature or unverifiable.

## Decision

in router.route skip the newest history row when it equals the request and clip every replayed row to about 400 chars with a marker, forwarded rows keeping their fence; converse's 20-turn replay stays verbatim; router avg and p95 tokens_in appear in the spend report with a warning on a 25 percent rise; the dependency rule is restated in CLAUDE.md, SOLUTION §1.8 and CARA §8 as "no pip; apt packages and system binaries allowed under the unit sandbox with a stdlib fallback or honest refusal".

## Consequences

smaller prefill on every routed turn; a reference buried deep inside a long forward may be lost to routing hints only.

## Resolves

router-dispatch#2, llm-cost-health#1, architecture#10 wording

## Depends on

nothing

## Status log

- 2026-09-07: proposed by the review; not yet discussed with the owner.
