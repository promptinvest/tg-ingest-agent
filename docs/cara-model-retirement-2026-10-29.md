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

## First frozen candidate and retained CI failure

Candidate `c4cdb748` (archive `8ff2b0b5`) is published in [PR10](https://github.com/promptinvest/tg-ingest-agent/pull/10).
Its migration helper suite passed 84 checks. The full CI run reported 3305 tests
with one failure in the historical outage-alert fixture and 62 skips; all compile,
undefined-name and helper steps passed. The original [failed run](https://github.com/promptinvest/tg-ingest-agent/actions/runs/40943965967) remains.

The fixture set the old primary and overrode only the router fallback; health
combines all profiles sharing a primary, so the new default OSS120B remained
healthy outside the supposed two-model outage. The repair explicitly sets its
router primary and all same-primary profile fallbacks, asserts the complete chain,
and preserves all existing outage/recovery assertions. Focused red/green reproduced
the failure and then passed. No production runtime changed in this repair.

The first frozen Linux verifier and its original receipts remain separate.
The 43-case qualification plan for that source was prepared without paid calls
and was not executed; a new frozen source requires fresh preparation and gates.

## Frozen application and distinct native-flow qualification

Application source `c25038e8efd4c466d5ad557faed565ebdfd7ff74` and archive
`2fa941e8ce655e2a4cf040304a82728ed383bb1582875bfc208172ffb1b5ecc1`
remain immutable. [Exact-head CI](https://github.com/promptinvest/tg-ingest-agent/actions/runs/40945045503) passed.
The fresh Linux verifier passed 3305 normal and 3305 shuffled tests (nine skips
in each), five helper suites (154/21/6/29/84), installed layout and shell syntax.
Verification `3d1276dc` completed 20:08:58 UTC; the supervisor returned zero,
and all 16 owned process groups were absent in the later read-only observation
`0f8ba8a8`. The first Linux failure remains unchanged.

The first paid replacement-model attempt (`qualification2`) stopped at its first
response and remains FAILED: `deepseek-v4.1-flash-1017` returned the correct
planned/pending facts but Cara's existing action-truth matcher flagged the noun
phrase `saved reminders`. The native caller requested its ordinary correction,
where the single-response test stopped. Exact retained-response replay proved
that path without changing guards or returning a fabricated passing answer.
The settled charge is USD0.0012636, with zero new unknown charges; prior history
and the consumed attempt are preserved. No production configuration changed.

A distinct operator-only companion measures the complete existing native flow.
It uses the existing pure `repair_cases.py` replay for the three supported
conversation cases, carries the failed first response and original ledger row
exactly, and permits at most the caller's one original corrective request. All
other cases keep their first-pass checks. Deterministic containment is a failed
answer; transport retries, third calls, altered prompts/guards/caps and repeated
first-response sampling are forbidden. Every request, response, native result
and settled charge must be retained and independently reviewed. The original
first-pass validator and its verdict stay unchanged. This is qualification of
existing application behavior, not a change to application policy or reuse of
an earlier trial exception. The aggregate USD0.25 ceiling includes the failed
first-pass attempt. This distinct full-flow check is not yet accepted.

The application archive stays `c25038e8`; any new qualification/adapter helpers
are a separately committed, hashed and tested operator package. Source review
and operator qualification bind that separate revision and package hash.

## Retained native-flow failure

The separate native-flow attempt `nativeflow1` failed October 29 at 20:25:46 UTC.
It reused the exact paid first response and made only the existing caller's one
corrective request. The correction again gave the correct planned/pending facts
but included "the saved reminders". The unchanged action-truth guard again read
`saved` as a completed-action verb and produced deterministic containment. The
final answer did not answer the requested status and next step, so the case is
failed; it is not a model-quality acceptance or a reason to relax the check.

The original qualification cost USD0.0012636 (ledger 24976); the native correction
cost USD0.0015978 (ledger 24980). The aggregate settled charge is USD0.0028614,
with zero unknown outcomes and the same USD0.25 aggregate ceiling. Only the first
of 43 cases ran; OSS120B, the remaining Flash roles and vision are unqualified.
The raw native-flow result is `8ee21067`, the supervisor result is `5d448dd9`
(return code 1). A fresh 20:32:42 UTC read-only observation confirmed both owned
PIDs and process groups absent. No deployment or production configuration change
has started. Both failed attempts and their exact original evidence are retained;
no third call, replay, changed prompt or repeated first-response sampling follows.

A private, unapplied proposal will distinguish this attributive phrase from real
completed-action claims, with regressions for both retained responses and genuine
claims. It is outside the requested model-ID/pricing change: the owner's standing
preserve-existing-behavior rule requires approval before applying that behavior
change. Preparation does not authorize application or deployment of the guard fix.

## Approved guard correction, October 30

The owner replied "go" to the explicit request to apply the narrow saved-reminders
fix, complete fresh qualification and deploy the model migration. This approves
the reviewed behavior change; it does not change the primary set, model controls,
budgets, history, prompts or existing genuine-action checks. The exact reviewed
guard is `9d75bfad` (base `e36c0df2`, patch `5c8e1d1a`). Before the correction,
both truthful retained status answers triggered a false action-denial response.
After the correction, the exact attributive noun phrase can pass the existing
caller, while true save claims, completion headers and other completed verbs in
the same sentence remain blocked. All original regexes and other exemptions remain.

The private proposal reproduced the false positives against unchanged source,
then passed 51 unchanged existing guard tests and ten new focused methods. Both
exact retained response strings independently reached the native reply with one
mocked response, no additional request and protected synthetic state unchanged.
Independent review passed. Application integration adds portable pure and caller
regressions; the exact private retained-plan proof remains separate. These offline
results do not convert either paid failure into a pass. Fresh source-bound full
checks, 43-case actual-model qualification, independent semantic review and all
normal preflight/deployment/acceptance gates still precede production change.
The prior total USD0.0028614 remains in the shared USD0.25 qualification accounting.
