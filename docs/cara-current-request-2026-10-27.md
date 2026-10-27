# Cara current-request and ping flow repair — October 27, 2026

Status: implementation and qualification; not deployed.

## Problem and authorized scope

After accepted source `3140081`, the owner sent a standalone test and clarified
that it was only a ping. The first turn selected Haiku for routing, BGE-M3 for
retrieval and DeepSeek for conversation plus repair. Both generated drafts
incorrectly claimed an older gratitude entry had been saved. The strict action
checks blocked these real fabrications, then delivered an irrelevant action-denial
fallback. The second turn answered correctly. This was a context/answer-quality
failure, not provider failover or Telegram delivery failure. Each router request
had about 19,000 input tokens; the turns took about 18.2 and 10.5 seconds.

Owner authorization: "fix & deploy". Further steering asks us to assess whether
conversation architecture needs correction, rather than requiring a redesign.
The earlier accepted release and model qualification remain valid historical
evidence, but their fifteen cases missed this transition.

## Architecture decision

Extend existing conversation/dispatcher components. Make current-request identity
explicit regardless of preceding stored role sequence. Preserve frozen evidence
for the bounded repair and distinguish message-receipt intent from action intent.
Use a narrow, complete bilingual receipt-probe parser to skip irrelevant router
and retrieval work only for independent own-text probes. Do not introduce another
model, classifier call, process, skill or history migration.

Pending drafts/cards, task approvals, open conversational questions, media,
forwarded or quoted data, compound work and real commands keep their existing
interpretation. Greetings remain warm generated conversation. Receipt wording
confirms only that the message arrived; it does not attest global recovery.
The action-truth guard remains strict. Ordinary KB grounding, dated recall and
Sol task analysis retain their existing paths.

## Verification and release requirements

Reproduce the failed-history → ping → clarification flow in a scratch database
through real dispatch and durable delivery, with fake model/Telegram boundaries.
Assert zero model/embedding/curation calls, no action effect, original history and
failed attempts preserved, and one reply per update even on redelivery. Include
RU/EN variants, command/data negatives, drafts/approvals, delayed gratitude,
ordinary greetings and known-unsent delivery recovery. Test non-ping topic changes,
owner provenance and initial/repair current-request binding separately.

Preserve all original tests and assertions. Run the Linux full normal/shuffled,
static/CI, exact packaged and installed entrypoint, native runtime and guarded
operator checks. A fresh actual-model qualification must use an explicitly
bounded, approved cumulative ledger; the earlier 29 calls/USD0.139814352 are not
reset or relabelled. Native model profiles and USD2/day, USD12/month, USD0.15/task
and four task-profile calls remain unchanged.

Deployment uses a fresh source/operator/window and protected backup with stopped
state parity; never replay a completed controller or restore old data. Continue
the descriptive model trial as a separate source segment while retaining earlier
segments and the original October28 13:36:17.924552UTC end. No automatic model
reversion or manual Telegram test/replay is included.

## Evidence so far

- Original source reproduction retained at `/root/cara-ping-flow-20261027/red2`:
  the exact failed-history sequence and English probe both fail the zero-model
  receipt contract. The earlier red1 fixture-name mistakes are retained.
- Ping implementation checks are isolated Linux runs under the same new root.
  Candidate publication, complete verification and deployment are pending.

- Final focused integration passed 141 cases on Linux at16:04:09UTC, including
  23 new regressions (14 dispatch/delivery, nine current-request/repair), ordinary
  analysis routing, reminder conversation, dated recall and all prior qualification
  regressions. Earlier overlapping 150/139 case runs remain separate evidence.
- Independent review identified a further repair edge: complete pings were not
  classified as receipt checks by the operational-status guard. The retained red3
  reproduction showed a false broad health reply escaping. The shared receipt
  intent now reaches that guard, and repeated false health replies use the same
  narrow receipt fallback. Unrelated recipe/mood and quotation behavior remains.
- The owner explicitly approved "Extend to 47 calls; qualify and deploy if all
  checks pass". The cumulative ledger keeps the prior29 calls/USD0.139814352,
  with at most18 more calls and totaltestspend belowUSD0.25. Nineteen outcomes
  comprise the original15, two zero-model dispatch ping cases and two new actual
  model topic-change cases. No paid call has run for this candidate yet.
