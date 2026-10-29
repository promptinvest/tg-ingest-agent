# ADR-0037 — Measured prompt-cache usage and dated price estimates

Status: Implemented and verified 2026-10-29; owner approved the exact five-record
reservation exception and guarded deployment. Exception qualification and production
acceptance remain pending at this checkpoint.

## Context

The owner asked whether DigitalOcean's prompt-caching GA needs changes in Cara,
then selected the recommendation to implement usage reporting and reconcile
prices before considering prompt optimization. Eligible DO-hosted requests
already cache automatically. The current gateway discards cache counters and
prices each input token at a single configured rate. Commercial role-model
overrides deliberately use upper context tiers; they must not be replaced by
lower base-tier prices.

## Decision

Extend the existing gateway, usage storage and spend/review surfaces. No new
agent, service, model, request parameter, prompt ordering or context expansion.

- Append nullable `llm_usage.cache_usage_json` through the existing atomic
  migration. Store only versioned numeric usage, fixed status/reason codes and
  the frozen dated tariff/estimates. Historical rows remain NULL and keep every
  original token count, charge, unknown outcome and timestamp.
- Validate exact DO-hosted and selected commercial-model semantics. Hosted and
  GPT totals include cache reads; Anthropic's documented prompt counter excludes
  cache reads/writes. Alias counters must agree and are never summed. Missing,
  malformed, contradictory, unsupported or identity-mismatched data cannot
  establish savings. Write TTL uncertainty can leave measured tokens unpriced.
- Settle the existing charge and cache metadata in one SQL update/commit. Keep
  budget reservations and settlement at configured flat rates; anticipated
  savings never release a reservation. Preserve missing-usage and failure rules.
- Freeze per-call published-rate estimates. Include charged cache writes in net
  savings, allowing negative savings. These are estimates, not invoice amounts
  or retrospectively discounted budget charges.
- Reconcile four stale open-model defaults with DO's published standard tariffs
  on October 29. Preserve explicit `PRICING_JSON` overrides, commercial ceilings,
  effective daily/monthly/task limits and all historical accounting.
- Append bilingual measured-call coverage, token-weighted reuse, per-model
  coverage and separately priced-call coverage/cost/savings to spend. Add the
  same evidence to the engineering review using its own rolling time window.
  Historical missing data remains unknown rather than counted as cache misses.

## Compatibility and verification

Request bodies, prompts, model profiles, retries, quotas, actions and delivery
remain unchanged. Full offline and installed-layout checks remain required.
Tests exercise provider fixtures, migration rollback/row preservation, atomic
settlement, historical/unknown accounting, request identity and both report
languages. No paid qualification or model call is needed to validate this change.

The old release operator is bound to a now-ended model trial and unchanged
schema. A new release must preserve original trial evidence without rearming its
observer, prove original-column/row parity across all 56 tables, and permit only
the named nullable column. Passing pure preparation checks is not deployment.

## Sources

- [DO prompt caching](https://docs.digitalocean.com/products/inference/how-to/use-prompt-caching/)
- [DO pricing](https://docs.digitalocean.com/products/inference/details/pricing/)
- [Implementation and verification record](../cara-cache-usage-2026-10-29.md)

## Status log

- 2026-10-29: Owner selected implementation; source and offline verification in
  progress. No production transition or claim of observed savings.
