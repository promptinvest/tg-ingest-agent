# Cara current-request and ping flow repair — October 27, 2026

Status: candidate `2aeecb9` blocked by actual semantic qualification; focused
correction implemented and under offline verification. Not deployed.

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
  model topic-change cases. This was the approved boundary before the recorded
  cohort below; it did not reset earlier usage.

- Candidate `6c278518`/archive`45103ae7` remains preserved. CI40537529032
  completed the full test discovery but failed the unchanged living-spec rule
  against a historical "3,227 tests" phrase introduced in the previous
  documentation closeout. The two living specifications now say "full offline
  suite"; the original exact historical count remains in the dated functional
  release record. No runtime, test expectation or quality gate changed.
  The new candidate requires fresh exact-source verification. The original
  installed-layout proof68c0f476 passed; it is retained as earlier-source evidence.


## Rejected actual qualification and focused correction

Candidate `2aeecb9a4349972ad90ed80d140dc84a8a8a168d`, archive
`bb1b8990e28f35d0e20aa6523675e89a0fe56f02824f916dba98718452261392`,
completed nineteen outcomes with twelve new native calls. Independent semantic
review passed fifteen and failed four. The two real-dispatch ping cases used no
model, routing, embedding or curation and each completed one durable reply. Both
new substantive topic changes produced useful native answers in the requested
structure. This does not measure model routing: those two routes were explicitly
stubbed to conversation.

The four failures still block deployment. Two Russian recovery paths exhausted
the existing correction on unsupported broad health claims and ended in canned
containment, which is not a semantic pass. An English answer inferred that an
earlier glitch had recovered without evidence. A source-grounded task answer
retained the absolute meeting date but incorrectly called it yesterday; the
supplied current local calendar day was October 27 and that source date was
October 28. All original native drafts and fallback outcomes remain evidence.

The native accounting receipt passed. Cumulative own-skill usage is now
**41 reservations / USD0.199338752**, with no own-skill unknown outcomes. The
47-call / USD0.25 limit remains controlling; a full fresh nineteen-outcome cohort
needs at least eleven new calls and cannot fit the six remaining reservations.
An additional call/spend scope is only a proposal until explicitly approved.
Native daily/monthly/task limits remain unchanged.

The first qualification launch stopped before its cohort because a new external
healthcheck unknown had appeared beside the twenty-nine previously recorded
external unknowns. A read-only baseline amendment pinned all thirty exact IDs and
full accounting rows, preserved the new unknown's cost and kind, and kept the
native budget evaluator and unknown-outcome handling unchanged. No reservation
was made by that stopped attempt. A fresh read-only capture rehearsal and a
separately reviewed second launch preceded the completed cohort. This accounting
baseline does not authorize a deployment quiescence exception or settle any
unknown outcome.

| Evidence | SHA256 |
| --- | --- |
| Actual cohort raw | `b27e9a1631eb44d31f7f66d1bb3e5da6fc5a71c5d0ef22446abd58d97b065da1` |
| Native accounting receipt | `0dcf06841425ba69eac3a789a6b8db3bf6ccbcedb33761cb10f669fda9e920bb` |
| Root failed semantic review | `1804d2ec8f793ae9236c48a70656e3cc9dca722ff62055a5731282b5a5d570b8` |
| Independent failed semantic review | `d460c811a554e68bb4d53e23f56891cee8a4ca4595395cb9890896476079b458` |

The focused correction keeps all history, persona, source fences and prior guard
assertions. A short positive facts contract sits beside the verified current
request: one frozen local date/time, this text received/readable, earlier outcomes
and global health unverified, and the complete requested facts/format preserved.
The repair retains the entire original frozen prefix and repeats the same facts
beside its final request wrapper; rejected draft wording stays data. A scoped
operational-recovery check also catches implied restoration, while preserving
negation, uncertainty, quotations and reports. The calendar check is limited to
an explicit absolute-date equals today/yesterday/tomorrow assertion; it does not
reinterpret quoted, translated, hypothetical or narrated dates, or a discussion
that happened yesterday about a future date.

Four actual failed-output witnesses reproduced offline before this correction.
Seven independent compatibility methods cover frozen clocks, accurate calendar
relations, literal/narrative dates, operational uncertainty and unrelated mood or
advice. Factual repairs use the same bounded correction path; another unsupported
claim ends in neutral failure and remains a failed quality outcome. The existing
reporting components localize these claim/retry/repaired/failure categories and
keep completed repairs out of unresolved issue digests. Seven runtime modules are
in scope for the combined ping, conversation and reporting correction. The
existing bounded repair path remains; no new agent or model stage, provider/profile
change, schema or stored-history migration is included. Exact-source full gates and fresh actual semantic qualification remain
required before deployment.


The focused correction passed 113 Linux checks at 16:50:05 UTC, covering the four
actual failure witnesses, seven independent compatibility methods and the
existing current-request, frozen-context, operational, dated-recall and source
qualification regressions. Original assertions were retained. This run used
synthetic model outputs and no paid calls; full corrected-source gates and real
semantic qualification are still pending. Evidence remains under
`/root/cara-current-request-focus-20261027/facts-correction-attempt1/green1`,
log `2476e0cd5dc9fb2955b64f427472d42771466e0cac105da5dac4d7bb2870281d`.
