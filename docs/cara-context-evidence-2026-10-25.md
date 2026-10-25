# Cara context and evidence release — October 25, 2026

Application accepted **2026-10-25T16:21:02.879450+00:00**, source `17083d6`,
build `23cada405758`, deployment `88a0cb2a8c15f71ac6fc5d4c`. The companion observer
passed its actual sandbox first tick and was installed at **16:21:33.658649 UTC**.
Independent final audit and remote release-tag verification passed.

## Resulting behavior

The owner approved the first architecture release under [ADR-0035](adr/ADR-0035-evidence-and-context-continuity.md).
Eight existing modules preserve persona, consent, action guards and stored history:

| Module | Change |
| --- | --- |
| `converse.py` | Freezes the current request, evidence and preferences for correction. Unambiguous owner reads return verified chat-scoped recorded status, actual next step or delivery; ambiguous, edited, forwarded and compound requests retain the model path. |
| `converse_svc.py` | Replays the same evidence; explicit current 1–8-item requests share bounded correction. Extra items/outside prose fail the format guard. Direct reads use normal reply finalization without inference or task execution. |
| `knowledge.py` | Keeps up to two distinct passages per note within the shared budget; quotations must occur in an actual selected passage. |
| `hermes.py` | Keyword retrieval can select a verbatim late-hit window, retaining original score and coverage gates. |
| `boss_model.py` | Optional memory context adds fenced certainty/source/date/quote metadata; confirmed evidence outranks inference. Default interfaces and selection remain compatible. |
| `memory_curator.py` | Rejects clear quotation, polarity or subject errors while preserving correctly attributed named/family/compound facts; sensitive facts still require confirmation. |
| `review.py` | Repeated format mismatches and failed corrections enter actionable patterns; only failed corrections add the existing failure-health line. |
| `texts.py` | Three format outcomes have readable Russian/English report labels. |

A recorded completion does not prove delivery or reading; a record read grants no
execution authority. Ordinary initial generation plus correction uses two profile
calls. The existing degenerate retry plus action/reference correction can use
three; the new format guard adds no further retry after a degenerate retry.

## Qualification

- The Linux suite passes: **3,055 tests run** in each normal/shuffled order, **nine existing skips** each and no failures; all **78** pre-`0a0dd9b` test files retain their bytes.
- [CI run 40207619599](https://github.com/promptinvest/tg-ingest-agent/actions/runs/40207619599) passes for the installed source. The final private forward package passes **70 isolated operator checks**.
- Independent review passes **nine outcomes**: six direct record reads, two actual conversation responses and one actual grounded-KB response, using **three actual model calls**.
- Direct first/repair-labeled fixtures cover pending work, exact two items and ambiguous delivery. Their repair labels do not mean a model correction occurred. Russian first/repair responses retain the actual next step, full source date, exact two items and data-only source handling. Only Russian repair injects a synthetic rejected draft before one actual corrective response. The KB case quotes the second selected passage.

The first architecture cohort on `1672744` failed four outcomes. Its raw replies,
criteria and charges remain; corrected qualification preserves those criteria.
Total accounted qualification cost is **USD0.056133184**: failed **USD0.041772224**
plus corrected **USD0.014360960**, within the shared **USD0.15** ceiling, with zero
new unknown charges. This is gateway accounting, not a reconciled provider
invoice. Earlier model-role trial evidence and charges remain separately retained.

The public continuation helper now includes the exact qualified pause schema.
Its **21 nonroot, networkless Linux tests** preserve all 19 original test methods
and add valid-pause preservation plus malformed/out-of-epoch/overlap refusal.
This operator-contract maintenance is not installed in the application; the
application source, archive, full-suite and model proofs remain unchanged.

## Retained failures and accepted forward completion

Attempt1 stopped before any application transition because its operator expected
stale source-output lineage; pinned accepted ancestry corrected that expectation.
Attempt2 installed the exact candidate and passed both **56-table prestart parity**
gates, then the unchanged verifier failed because its command PATH omitted
`/usr/sbin/runuser`. Main/fleet were forward-paused at **15:54:21.327691 UTC**.
Attempt3 stopped before resumption on retired Mentor configuration preservation.

The unchanged installer had regenerated the retired Mentor environment from the
main trial environment. Exact canonical derivation proved only `DO_CHAT_MODEL`
differed. Attempt4 required both retired units inactive and disabled, privately
retained the generated image, then used metadata/inode compare-and-swap to restore
only that retired file's original bytes, owner/group/mode at **16:18:28.735197 UTC**.
Main trial configuration stayed unchanged; the final protected map returned to
its baseline. No application/database restore, reinstall or historical controller,
publisher or gateway replay occurred. All failed attempts and evidence remain.

The accepted completion passed original namespace/source readiness, MCP60,
scoped9 reads, preservation, quiescent final restart and normal-notice gates.
Three final health samples at **16:20:46.878294, 16:20:52.173112 and 16:20:57.447141 UTC** show the four
existing services active with zero automatic restarts. Source first exposure stays
**15:53:26.953420 UTC**; the recorded pause is **15:54:21.327691–16:18:45 UTC**.
The latter endpoint is the actual systemd start timestamp, not the later receipt time.

## Trial continuity and evidence

The [original model trial](cara-model-trial-2026-10-25.md) still ends
**2026-10-28T13:36:17.924552+00:00**; the final timer entry remains **October28
13:36:18 UTC**. Completion resets neither its 72-hour clock nor source exposure.
The new observer records the pause and source-change confound, runs read-only
every six hours, makes no model call/message and performs no automatic quality
judgment or reversion. Its first sandbox snapshot is hash-bound in the receipt.

**USD2/day and USD12/month**, profile controls, fallbacks and per-task limits remain.
Claude Haiku5.5 handles routing/ingest/memory candidates, Sonnet5.5 KB/consolidation,
GPT-6.1 Sol planning/synthesis. Conversation stays DeepSeek v4 Pro (**320 tokens**),
KB **500 tokens**, vision Llama4 Maverick, embeddings BGE-M3 and speech local Whisper;
the evaluator remains dormant. Conditional model-only reversion merges the four
model keys and preserves later unrelated edits, installed source and data/history.
Both future revert verification paths carry the command-local PATH correction.

The [sanitized verification record](cara-context-evidence-2026-10-25-verification.json)
contains full source/archive/acceptance/authorization/continuation/monitor and
Mentor-restoration hashes, plus qualification/accounting and preservation counts.
It contains no raw dialogue, environment values or protected-file inventories.

Nine synthetic outcomes do not establish production completion rates, general
reasoning quality or ChatGPT-agent “IQ” parity. Memory guards are bounded checks,
not general entailment; retrieval remains partial. Adaptive tools, ordinary-chat
research/orchestration and broader synthesis remain subsequent work.

## Publication completion

Read-only final audit passed at **2026-10-25T16:33:56.459536+00:00**. Receipt: `caa9dfb0d7f4249cfd08ab918a04cafa1ca91d456f704508f05fe12959c571e5`.
Published tag: `production-2026-10-25-context-evidence`, targeting app source `17083d6`.
Verified remote tag object: `86cf4e612a8ba02130b7f9352b704c9e634204dc`;
peeled commit: `17083d6ee4647de4bcaf03808cac0649996f7652`.

The tag names the verified installed application. The later repository publication
updates documentation and the nested inert continuation contract only; it does
not install another application revision. Private operational evidence remains
under `/root/cara-capability-20261025/release-attempt4`; consumed attempts are
historical and must never be replayed. The exact package/window was selected under
the owner's implementation request, without claiming separate hash-specific approval.
