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

## Retained integrated-guard verification failure

Candidate `1ca8e22` / archive `90669e1d` failed CI
[41013429287](https://github.com/promptinvest/tg-ingest-agent/actions/runs/41013429287)
and the fresh full Linux verifier at October 30 06:37:10 UTC. The model-trial
helper suite ran 154 tests with two failures; later full gates did not run.
Both failed repair fixtures used the truthful retained `RAW_THREE` reply as a
trigger for a corrective call. The approved noun-phrase fix now correctly accepts
that first reply, so the repair-only assertions were never reached.

The correction preserves `RAW_THREE` byte-for-byte and every existing assertion.
A separately named synthetic draft adds an actual false save claim for those two
repair tests, while new regressions prove that the truthful original passes with
one request and the synthetic false claim requests the unchanged correction.
The approved application guard and all other runtime bytes remain unchanged.
Failure `cdc850c2`, helper log `f3b88146` and supervisor result `3230e231` remain.
Fresh 06:38:13 UTC observation confirmed all five owned PIDs/groups absent.
A new immutable source and new full attempt are required; neither failed attempt
may be replayed or treated as verification success.

## Successor application and separate operator

Frozen application `93bf1a6042af5b1651a747c230533e7c008de52a`, archive
`165723ab9872ff227f944ea8b461acbe3a06442f4de12bb0a52f5ce0ec1c2e9b`,
contains the reviewed repair-fixture correction. All 156 model-trial helper tests
passed locally. The new full verifier launched once October 30 06:42:39 UTC under
`full4`, supervised by `full-supervisor4`, PID/group 1526904; manifest `2c9aa345`.
It remains a verification attempt, not release acceptance. Preparation of its
fresh 43-case model plan `96d615f1` completed with zero paid calls, unchanged
coverage/caps/temperatures/timeouts and candidate environment `96db9e09`.

The separate operator permits only common.py, llm.py, cache_usage.py and the
exact approved action_truth.py change, with shared validation and fixed guard
before/after hashes. Its first-pass model gates now bind the separate operator
revision and full package hash just as the distinct full-flow mode does. Original
qualification and unknown-charge validation functions remain exact.

Peer v2 verifies the actual Daily Lingo acceptance continuation, canonical exact
copy, and consumed nine-leaf policy updater. Only the four monitor units and
/etc/cara-fleet-host.json may differ from the original 126-file protected set.
The policy transition must retain every other byte and ownership/mode; the
remaining protected ancestry is reconstructed exactly. Actual read-only proof
passed 06:41:51 UTC (`f8172168`); the earlier failed adapter proof remains.
All 125 local operator tests passed except two explicitly skipped Linux retained
fixtures; independent source review passed. Fresh Linux qualification is pending.

A separate fresh preflight found one overnight Kimi health-check unknown row
(25046, October 30 04:41:07 UTC), reserved USD0.0005102. The original 35 rows remain
exact and nullable cache values remain unknown. No transport/provider receipt
establishes its outcome; it is not relabeled, deleted or refunded. The unchanged
zero-current-unknown release gate blocks on this row. It is unrelated to the two
settled migration calls (USD0.0028614). No exception has been selected or applied;
any proposed one-release allowance must remain private and require owner approval.

## Verified application and retained qualification prefix, October 30

The successor application `93bf1a6` / archive `165723ab` passed exact-source CI,
3315 normal and shuffled Linux tests, all helper suites, installed layout and
shell syntax on October 30 at 06:50:41 UTC (verification `c48e678d`). The separate
operator `6412604` also passed its isolated Linux suite: 125 checks, with only
two intended retained-evidence fixture skips. Production remains unchanged.

The fresh actual-model attempt retained seven native passes and stopped at its
eighth response when the unchanged guard requested a correction for the phrase
"delivery field is marked". All eight answers are semantically sound, but that
is partial evidence, not qualification acceptance. Its eight charges plus the
two earlier charges total USD0.0135208, with zero unresolved migration charges.
A distinct operator companion carries those eight exact responses and their
original ledger prefixes, permits only case eight's one existing correction,
and then tests the untouched 35 first requests. It binds the verified application,
original prompts, profile controls and the same cumulative USD0.25 allowance.
The failed first-pass verdict remains unchanged; a third request or deterministic
containment fails the continuation. Independent full semantic review remains required.

The stopped qualification3 supervisor returned one (`3229f3c1`), retaining
raw `c51793ab` and all eight case artifacts. At 07:01:43 UTC, a fresh read-only
check found all four owned PIDs/groups absent. Offline replay `fff6e434` proves
the existing second request with protected synthetic state unchanged and no paid
call. Partial independent review `e9ff91cb` does not claim full acceptance.

The continuation uses new `qualify_retirement93.py` and `retirement93_gate.py`
helpers; old qualifiers and validators remain byte-exact. The new supervisor
`9c121d9a` binds full4/source, archive `165723ab`, full verification `c48e678d`,
and the exact failed qualification3 plan, inputs and raw result. Sixteen isolated
Linux supervisor checks passed, with independent review `aa009827`. A prepared
plan, frozen helper package, offline helper qualification and reviewed concrete
launch manifest are still required before a paid continuation.

The separate Kimi health-check reservation remains unknown and reserved. The
strict current-unknown release gate is unchanged; its private one-release
exception proposal has not been approved or applied.

The new companion, gate and mode dispatch passed all 141 local operator checks
(139 passed, two unchanged retained-evidence fixture skips). Source review covers
the exact eight-response reuse, one correction boundary, complete native outcome
and cumulative 46-row accounting expected only after all 43 cases pass.
