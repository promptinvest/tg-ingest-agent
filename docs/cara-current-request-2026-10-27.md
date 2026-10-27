# Cara current-request and ping flow repair — October 27, 2026

Status: corrected source `83e8162` deployed and accepted at
2026-10-27 17:28:51.589018 UTC. The server observer passed its actual sandbox
first sample; the final independent live audit passed at 17:32:20 UTC. Candidate
`2aeecb9`
and its failed semantic outcomes remain historical evidence.

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
  At that reproduction checkpoint, candidate publication, complete verification
  and deployment were pending.

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

Those four failures blocked deployment of that candidate. Two Russian recovery paths exhausted
the existing correction on unsupported broad health claims and ended in canned
containment, which is not a semantic pass. An English answer inferred that an
earlier glitch had recovered without evidence. A source-grounded task answer
retained the absolute meeting date but incorrectly called it yesterday; the
supplied current local calendar day was October 27 and that source date was
October 28. All original native drafts and fallback outcomes remain evidence.

At that rejected-candidate checkpoint, the native accounting receipt passed.
Cumulative own-skill usage was
**41 reservations / USD0.199338752**, with no own-skill unknown outcomes. The
47-call / USD0.25 limit remained controlling; a full fresh nineteen-outcome cohort
needs at least eleven new calls and cannot fit the six remaining reservations.
An additional call/spend scope was still a proposal at that checkpoint.
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
change, schema or stored-history migration is included. At that correction
checkpoint, exact-source full gates and fresh actual semantic qualification
remained required before deployment.


The focused correction passed 113 Linux checks at 16:50:05 UTC, covering the four
actual failure witnesses, seven independent compatibility methods and the
existing current-request, frozen-context, operational, dated-recall and source
qualification regressions. Original assertions were retained. This run used
synthetic model outputs and no paid calls; full corrected-source gates and real
semantic qualification were still pending at that focused-only checkpoint. Evidence remains under
`/root/cara-current-request-focus-20261027/facts-correction-attempt1/green1`,
log `2476e0cd5dc9fb2955b64f427472d42771466e0cac105da5dac4d7bb2870281d`.

## Corrected-source qualification and accepted release

The owner approved a cumulative limit of 59 calls and total test spending
strictly below USD0.35. This replaced the earlier 47-call/USD0.25 test boundary;
it did not reset the existing 41 calls/USD0.199338752 or alter Cara's native
USD2/day, USD12/month, USD0.15/task or four task-profile calls. The actual scope
receipt is joined to the operator's separately selected source; it does not
claim a hash-specific owner approval.

Corrected source `83e8162611e34e4cf3b387a8b782160f8a71b8b7`, archive
`3f7c3b9766c94b0ec429ed593a341e237d4470cdd507b90ff78053bfef05cdb1`, passed
both exact-source Linux root test orders: 3,266 cases with nine existing skips
each, at the original 360-second CPU, 1 GiB memory and 480-second wall limits.
Normal and shuffled runs completed in 215.849 and 233.984 seconds; compile and
shell checks passed, with source bytes unchanged. Exact-head
[CI run 40545457748](https://github.com/promptinvest/tg-ingest-agent/actions/runs/40545457748)
passed both orders with its 62 expected skips each. The exact installed layout
passed five scenarios through both entry names, with the packaged entry module
absent and all 94 runtime artifacts exact. All existing tests and assertions
remain preserved. Focused reporting checks separately passed 47 cases; these
counts overlap the full suite and are not added to it.

Native qualification launched once at 17:09:59.251292 UTC and finished at
17:10:30.061383 UTC. The unchanged nineteen-case rubric passed root and
independent semantic review. Six record-read cases and two actual dispatch ping
cases are functional evidence. Eleven other outcomes used actual native calls,
including the two substantive topic changes with explicitly stubbed conversation
routing; they do not measure router intelligence. Three rejected drafts were
synthetic repair stimuli and are labeled as such. No canned failure response
counted as a semantic pass. Source-date replies kept October 28 absolute or
correctly related it to the frozen October 27 clock. Recovery replies limited
reassurance to reading the current text, without claiming earlier faults or all
services had recovered.

Cumulative own-skill accounting is **52 reservations / USD0.254401792**, including
the exact prior 41 rows and eleven new native transports; no own-skill outcomes
are unknown. All thirty external unknown rows retain their full reserved charges unchanged,
including the new healthcheck row; none was settled, relabeled or deleted. A
separately approved, exact-row deployment exception is recorded in the private
operator material and does not relax future unknown-outcome handling. The failed
candidate's fifteen-pass/four-fail semantic record, stopped capture and all
original raw replies, charges and controller evidence remain intact.

| Evidence | SHA256 |
| --- | --- |
| Exact-source Linux verification | `4eca4b8b84ccf27dd906b642ccf46ef455d5b93b45e0c844a7f71624e425ec86` |
| Actual native cohort raw | `b4a84cdfc39a3e55cdbc92036e48097e3a5f759ed378d022107f7e1615e7e79e` |
| Native accounting receipt | `9e29c1fd48cd64352bb0c872b6b3025bc86ac55f4464e104f2d3d8d795013d86` |
| Root semantic review | `af20dd42ae96a25b6c8a00d296fea1e50d196d5ee030ba813e0adb167653007b` |
| Independent semantic review | `d52b09f77ab8a978fd3e2ad4b1c512ae90fe90a05d87bf2592920d695e921187` |
| Closed model proof | `7c897719b7f316e8d9c1ebf4a93170acea2a6baa6fe6aba028a6062572026227` |
| Observer installation | `ec0c4b1e70456abe5c4068d20bbc7e1a5d60912d825edae68d187dd9582aa3cc` |
| Actual sandbox first sample | `e157cea57b5ff8f11442e9c7fe954ba8deff2e5e8bfb5bf29e246451db8ab065` |
| Final independent live audit | `4697a38a73e3cdbd349a4ddeb2c0709f2f8d2c7fd2947da3d22aa40bbeba4f34` |

The release was accepted **2026-10-27 17:28:51.589018 UTC**, build `ca7db0315cbc`,
deployment `ed6cc206f136827168490016`, acceptance `256368fa3f2ac26f2a5010ef37db289113912666a902d83a93a939c7bcf4cddf` and native
marker `4fa81c3487b2dc88e931c0b59595add900f4ca2779428ab0e49ba248aca58e75`. The exact-source production tag
`production-2026-10-27-current-request` has annotated object `5143d5bd1a3a641e06834df3864a3b3a73a01f60` and peeled commit
`83e8162611e34e4cf3b387a8b782160f8a71b8b7`. The reviewed operator package
`9eeb5b004365a48f0484c47fecab21e41ed1694df22fd8958cd050d607d6ffc7` passed 215 Linux fixtures and three actual kernel probes before its single launch.
Stopped-data parity preserved all 56 tables/columns/rows and original history;
all 94 installed runtime artifacts and the protected environment remained exact.
Original readiness, MCP fact/private fixture, scoped tool and peer-preservation
gates passed. Both readiness phases, both private MCP fixtures, the scoped tool projection and
final healthy samples passed. The normal native deployment notice was recorded
once. The server observer passed an actual sandbox first sample. The independent
read-only audit passed at 17:32:20.646155 UTC: 94 runtime artifacts, 118 protected
files plus two observer units, 56 schema tables, all thirty unchanged unknown rows and their reserved charges,
the exact native environment/budgets and active main, fleet, worker and Whisper
services with zero unexpected restarts.

Continuation `54d21267e864c286d270c319d321a13899a4f3d70c5d518eb3d3db66ddd8e2fe` records the sixth source segment beginning
at `2026-10-27 17:26:37.784040 UTC`, including its observed transition gap. The
observer was rebound and its actual sandbox tick passed at
`2026-10-27 17:29:53.247227 UTC`; audit
`4697a38a73e3cdbd349a4ddeb2c0709f2f8d2c7fd2947da3d22aa40bbeba4f34` binds the final live identities and health. The original trial end remains
**2026-10-28T13:36:17.924552 UTC**; acceptance does not restart exposure or imply a
clean model-only comparison. Native budgets, models, unrelated host/peer policy
and data remain unchanged. No manual message, business replay, database restore,
unknown settlement was performed. Deployment required no model calls beyond the
reviewed qualification cohort.

The finite qualification supports this correction; it is not proof of ChatGPT
capability parity or of every future answer. Ordinary traffic observation remains
necessary, with a separately identified source segment and preserved failed
outcomes. Follow the current accepted forward-only operator path if containment
is required; never replay a started/completed controller or restore old data.
