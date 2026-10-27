# Cara functional review — October 27, 2026

Status: deployed and accepted October 27 at 14:06:34 UTC, source `3140081`.
Qualification, installed acceptance, monitoring and independent audit passed.

The owner requested a current functional review and fixes for bugs found.
This continues the observed false error reply to “А сейчас?” after the earlier
installed-layout repair. That update completed successfully; its generated
reply repeated an older failure notice. The prior release and all failure
evidence remain intact.

## Confirmed defects and compatible corrections

| Area | Reproduced outcome | Correction |
| --- | --- | --- |
| Conversation | Old operational notices replayed as ordinary assistant turns; a successful request received an invented current failure. | Persist native notice provenance through the outbox; fence historical notices as data; preserve raw history and dated recall. Check initial and repaired current-status claims with the existing bounded correction path. |
| Clarification | A useful router question was discarded before warm conversation. | Carry the bounded candidate as unverified context through the existing guarded conversation path, restoring per-turn context afterward. |
| Media capture | Old buttons or explicit replies confirmed or discarded a newer staged card. | Bind new confirmations to their actually delivered card; retain the current staged set when an older card is answered. |
| Reminder undo | A missing explicit number/title fell back to an unrelated uniquely moved reminder. | Preserve the explicit target; use the unique fallback only for a genuinely unspecified target. |
| Notes | Old confirmations overwrote changed summaries, deleted reused SQLite row IDs, or archived a partially replaced batch. | Pin identity/content versions for new offers and recheck the entire batch before mutation; bind explicit replies to the delivered question. |
| Edited source notes | An older edit button applied or discarded newer staged text, including a different offer for the same row. | Bind private staged text to the delivered card and saved-note version; reject stale or undelivered new offers and invalid callback decisions. Independent offers for different notes remain answerable. |
| Voice | Pre-submission failures leaked files/reservations; definitely unsent or completed noisy transcription retained incorrect unknown usage; cleanup failure retained completed jobs. | Settle only proven outcomes, retain unknown submitted usage, and complete cleanup/job removal on the relevant failure paths. |
| Task delivery | An artifact was marked delivered before a valid Telegram acknowledgment or committed final-task state. | Validate the positive integer message ID, then atomically bind task and artifact delivery. Ambiguous sends remain terminal for automatic resend. |
| Task synthesis | One profile invocation could make several native calls while counting once and checking task cost afterward. | Guard each native reservation before transport with the existing task limits; bind exact usage rows, reconcile known cost, and refuse automatic replacement of unknown paid outcomes. |
| Issue reporting | New processing-status guard outcomes needed the existing actionable/reporting lifecycle. | Add translated diagnostic categories; completed rewrites remain history rather than new unresolved monthly problems. |

## Compatibility and scope

No provider, model, profile, price, budget, schema, scheduling, retention or alert
preference is changed. Existing data/history and legacy pending shapes remain
readable. New version/card metadata applies to newly issued offers; older offers
cannot acquire provenance retrospectively and keep their original expiry.
Operational history is retained, including legacy notices whose native origin
cannot be proved. Owner quotations and forwarded data retain their own origins.

The review includes conversation/memory, notes/media, reminders/calendar,
journals, confirmation/purge, tasks/analysis/fleet, voice and inference accounting.
Existing calendar, journal and typed-purge checks did not expose another confirmed
defect. This records tested behavior, not a guarantee that every possible defect
has been eliminated.

## Verification and release requirements

Meaningful failing reproductions precede the fixes. Existing outcome assertions
remain; the media callback fixture uses its own fake transport's delivered ID.
The original completed-handling fixture explicitly seeds its original three
records; a separate registry-wide test verifies every current handled category
and exact unchanged historical rows. Every original assertion is preserved.
The first candidate's CI failure and full verification evidence remain; its
production bytes are unchanged by these fixture corrections. Run the full
normal/shuffled Linux gates and the exact installed `agent.py` artifact gate.
Model qualification uses synthetic state and fake Telegram, the native profiles
and meter, originally a USD0.25/max20-native-call ceiling, and unchanged native
daily/monthly/task limits. The later explicit owner extension is recorded below;
stop if any bound or outcome check fails.

Freeze the exact canonical source/archive, CI and qualification receipts before
the guarded operator transition. Preserve backups, stopped 56-table parity,
protected artifacts, readiness checks and source-bound monitor/reverter evidence.
The fifth trial segment keeps the original October28 end. No manual message,
production-update replay, data restore or model-only revert is part of this work.

## Actual response qualification correction

The published candidate `f279f13` passed 3,199 tests in both root Linux orders
(nine skips), the corresponding nonroot suites, and CI. Its first actual model
cohort passed nine of fifteen semantic cases and failed six, so it is blocked
from deployment. Ten known native calls cost USD0.04818656; accounting reconciled
without changing earlier rows or historical unknowns. All raw replies, rejected
drafts and independent reviews remain preserved.

The failures cover an unrelated action-denial fallback after a valid recorded
status, unsupported broad recovery claims, refusal of an exact historical
quotation, and converting unverified old notices into proven failure events.
The corrected candidate must cover these through the actual conversation and
recall handlers while preserving new-action guards and private-life language.
The global action-truth rules remain unchanged. Exact canonical notice reads
requested by the current owner retain the native conversation path, with a
source-bound literal contract. Existing deterministic task-status reads remain
separate from model-quality evidence. A failed corrective template cannot satisfy semantic qualification.
At that checkpoint, the same fifteen cases and cumulative USD0.25/twenty-native-
reservation ceiling remained; the corrected combined artifact still needed qualification.

The next published candidate `d984f44` passed all 3,218 root Linux tests in both
orders (nine skips), CI and fourteen of fifteen actual response cases. Its dated
answer still called the notices a code placeholder that proved a scenario ran;
two of those rows have unverified legacy provenance. This remains a semantic
failure and blocks deployment. Nine additional known calls bring the cumulative
cohort to nineteen reservations and USD0.090743872, with no unknown outcomes.
The next correction grounds recall in a compact origin summary and rejects
unsupported code-execution claims, while preserving valid dated attributions,
native notice evidence and the exact earlier regression tests. The selected
twenty-call bound remained at that checkpoint; that implementation record
authorized no extra paid requests.


## Final source qualification and approved extension

The owner explicitly approved “Extend to 30 calls; finish qualification and deploy
if all checks pass” on October 27. The extension changed only the cumulative test
call cap from twenty to thirty; total test spending stayed below USD0.25. Existing
native USD2/day, USD12/month, USD0.15/task and four-call task limits remain. The
prior nineteen reservations and every failed response were retained.

Final source `3140081dce9fae6e01cbf355393bcdc05b68492e`, archive
`ffa57ed6f289d3ffa2062fc620135804d09f7218fddc5067abfaa182105e82c1`, passed
3,227 root Linux tests in each normal/shuffled order (nine skips each),
[CI 40506161328](https://github.com/promptinvest/tg-ingest-agent/actions/runs/40506161328)
(normal/shuffled suites, 62 expected skips), and canonical source review.
All 11,339 original assertions were preserved. The exact 94-file installed
layout passed both module entry names (`agent` and `__main__`) and all five
dispatch/delivery scenarios.
Private operator qualification passed all 187 cases and three actual Linux
kernel/shebang/import probes.

All fifteen final response outcomes passed. Six deterministic record reads are
explicitly separate from model-quality evidence. One initial Russian all-clear
was rejected and corrected through the actual bounded native repair path; this
was not fifteen clean first replies. Ten new native calls brought the cumulative
ledger to **29 calls / USD0.139814352**, with no unknown outcomes. No final
fallback template was accepted as semantic qualification. Earlier rejected
cohorts and raw replies remain unchanged.

## Forward deployment and accepted runtime

The single-use controller at
`/root/cara-functional-fixes-20261027/release-attempt1` launched 13:40:37 UTC and
stopped 13:41:33 UTC: its 373-character private release summary exceeded the
unchanged native 300-character limit. All 94 runtime files had been copied and
the installed-layout gate passed; the old verified marker remained intact.
Main/fleet were paused. Fresh read-only checks proved all 56 stopped tables/schema,
108 prior protected files and the original environment exact; worker and Whisper
remained healthy. All failure artifacts and original phase receipts remain.

A separately reviewed forward continuation at
`/root/cara-functional-fixes-20261027/forward-completion-v1` resumed only the
first unexecuted native manifest write and exact installer tail. Its 196-character
test summary and the original 469-character verification summary passed the
actual unchanged native validators before mutation. Seven added boundary checks
cover the original length failure, 300/500 limits, normalization and Unicode,
exact unexecuted shell/AST tails and local bindings; an independent synthetic run
also exercised the actual derived controller tail. The 134-file operator package
was authenticated before import, and the partial runtime/database/protected state
was checked before completion. Both original startup guards and all original
readiness, six MCP tools / 60 fact cells, two private phases, nine scoped reads,
preservation, final-restart and health checks
passed before the normal one-shot deployment notice and acceptance.

The exact accepted source is `3140081`, build `df7a5715a810`, deployment
`fac7939fdd950ba5e237d163`. Acceptance was **14:06:34.824392 UTC**;
forward completion was recorded 14:06:35.882690 UTC. The actual sandbox observer
passed at 14:07:26.290588 UTC; independent read-only final audit passed
**14:08:44.425238 UTC**. The verified annotated tag
`production-2026-10-27-functional-fixes` points to the exact source, separately
from the documentation closeout commit.

| Evidence | Hash |
| --- | --- |
| Acceptance | `9ee3d7f2c57ad8e02f65389bd6a53b7f01ef6300443902a25751f8d71dec55b8` |
| Verified native marker | `f2ee88e4d3e5491f7a8b9c7196c4068fbee24e1b10ea55bf71d323099a9b7653` |
| Forward helper | `d192271f3106088d014205e365326ceef50c45f7c0a0dfc5e5892c47fc82599f` |
| Independent helper/tail review | `9e9dbcf56b351577b9f401277da976ae481e0aa47b25601b2071909f447365db` |
| Trial continuation | `9366b8d4ad6424c87474f23ef731c22b3880c60ef1fb5227713b157e0657a797` |
| Observer installation | `763545616e25559a1563acdfa966299b89704b5beb2b3330af831fd8eb3cf7dc` |
| Observer manifest | `8a2a6b13c2c470c2eee304c74280ec377c3f75a6604f38dc23f918f326140ce3` |
| Independent final audit | `9f78c1a7cfbbad2a90a16acf923c6949083ac04289e51081d07f0f403b60a57f` |
| Remote annotated tag object | `de1f362c8a2300228efe61708e70048f951c5b6f` |

The fifth source epoch began 14:04:20.390431 UTC. Earlier observations and the
paused transition remain visible; the original trial end is unchanged at
**2026-10-28 13:36:17.924552 UTC**. Application changes confound a model-only
comparison; there is no automatic reversion. No controller, production update or
manual message was replayed. No database or old application source was restored.
Models, native budgets, schema, scheduling, retention and alert preferences remain.

The independent final audit verified all 94 runtime files, 112 protected
configuration files plus the two observer units, both readiness namespaces,
six MCP tools / 60 fact cells, two private phases and nine scoped reads. All 33
original failed-release artifacts and the five failed updates / fifteen attempts
remain unchanged. Main, worker, fleet and Whisper were active with zero restarts;
the normal deployment notice has exactly one recorded attempt.

A later private builder guard passed six additional local metadata checks (193
total, preserving the earlier 187). It is separate from the deployed package and
requires fresh Linux qualification before future use.
