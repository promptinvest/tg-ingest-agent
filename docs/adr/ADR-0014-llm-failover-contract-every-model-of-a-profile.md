# ADR-0014: LLM failover contract: every model of a profile must be able to answer in the profile's shape

- Status: Proposed
- Date: 2026-09-07
- Phase: Phase C — robustness, operations, security
- Source: 2026-09-07 review of architecture, code and live behavior against build e3208b8 (report kept off-repo; findings are referenced by id)

## Context

the router's only fallback returns truncated non-JSON at its 200-token cap in 7 of 13 calls; a converse call hung 181 s before failover; all-benched profiles are tried anyway; a router 503 reached the owner as an error for a phrase the deterministic parser handles.

## Decision

env first: router_fast fallbacks ["deepseek-v4-pro","openai-gpt-oss-20b"] with max_tokens 400; then per-profile timeouts (router 30 s, ingest 45 s, memory 60 s, converse 60 s, task 90 s) passed from chat_profile to chat; one model and one attempt when every model is benched, with an all_benched trace; finish_reason=length on a json_required profile retries the same model once with a doubled cap and never benches it; when router_fast fails entirely the deterministic parsers run before llm_error; check_model_health probes every configured slug, fallbacks every fourth sweep, labelled as such.

## Consequences

worst-case routed turn falls from ~6 min to ~2 min; a dead fallback is discovered by the monitor, not during an outage; response bodies bounded at 2 MB chat / 8 MB embed.

## Resolves

llm-cost-health#2, llm-gateway#1, #3, #4, #6, routing-traces#7

## Depends on

nothing

## Status log

- 2026-09-07: proposed by the review; not yet discussed with the owner.
