# Cara: November DigitalOcean model retirement migration

Status: implementation and fresh qualification in progress; not deployed.

Owner request: "update the models to be retired, keep the current primary set.
Update pricing". This selects the narrow migration reviewed in the same session.
The supplied DigitalOcean notices give November 9, 2026 as the retirement date.
The public support timeline did not yet list these five dates when checked.

## Selected scope

| Existing use | Selected replacement | USD per million input/output/cache-read tokens |
| --- | --- | --- |
| `deepseek-4-flash` fallbacks | `deepseek-v4.1-flash-1017` | 0.30 / 1.20 / 0.006 |
| `openai-gpt-oss-20b` fallbacks | `openai-gpt-oss-120b` | 0.10 / 0.70 / unpublished |
| `llama-4-maverick` vision | `deepseek-v4.1-flash` | 0.30 / 1.20 / 0.006 |

The two DeepSeek IDs are tested separately; no alias equivalence is assumed.
Current Haiku routing/ingestion/memory, Sonnet grounded answers/consolidation,
Sol task roles, DeepSeek V4 Pro conversation, and Kimi fallback remain. Every
profile limit, timeout, temperature, ordering, schema and action guard remains.
USD2/day, USD12/month and USD0.15/task limits remain, including the authoritative
daily SQLite preference. Explicit commercial tariff ceilings remain unchanged.
New calls receive the new tariffs; existing rows, cache snapshots and unresolved
reservations are never repriced or removed. Embeddings, local speech, history,
retired Mentor/Nikki and Daily Lingo remain outside the transition.

Gemma and Qwen were absent from current Cara profiles and had only one historical
health-check call each in the seven-day review. They require no role migration.
The retired PrismData application is not reactivated or rewritten by this release.

## Evidence and verification boundary

Read-only observations on October 29 at 19:15-19:17 UTC confirmed the active
profiles, Maverick vision, the existing accepted Cara process and Daily Lingo's
direct OpenAI endpoint. Authenticated DigitalOcean `/v1/models` returned HTTP200
and included the three selected replacement IDs. Catalog presence is not
functional or semantic model qualification.

New focused regressions failed on unchanged source in the fresh isolated root
`/root/cara-model-retirement-20261029/qa-red-1`, proving missing replacement
tariffs/cache coverage and retired defaults. The original failure remains.

The focused Linux run passed 120 application regressions and 17 existing
qualification-library tests. Both early shell wrappers subsequently exited with
a PowerShell BOM/CR error; those original wrapper failures remain and are not
release acceptance. A separate fresh Python-orchestrated, network-isolated run
passed 41 helper tests, and six synthetic verifier process-cleanup tests passed.
Final exact-source full Linux checks and CI, bounded actual-model qualification,
independent raw-response review, fresh release preflight and guarded
deployment/acceptance remain required. Existing one-shot controllers,
trial observers and the consumed one-release Kimi exception are not replayed.
New qualification preserves native reservation/settlement and production budget
checks, uses synthetic inputs and an aggregate USD0.25 ceiling, and stops on an
unknown outcome. No historical messages or actions are replayed.

Sources checked October 29:
[model catalog](https://docs.digitalocean.com/products/inference/details/models/),
[pricing](https://docs.digitalocean.com/products/inference/details/pricing/),
[support policy](https://docs.digitalocean.com/products/inference/details/model-support-policy/).
