# Cara personal-assistant reliability — October 25, 2026

## Scope and authority

Owner request: “implement these fixes”, following the current architecture/code
review and the clarification that Cara's primary purpose is a personal AI
assistant. The standing implementation-includes-deployment rule applies; no
separate hash-specific approval is claimed. ADR-0034 records the design.

The review baseline was a88ec30; the then-accepted installed runtime was dcdd5ee. The
review independently confirmed the relevant installed modules match the checkout,
and reproduced reminder replay/confirmation failures, lost reply/expiry delivery,
fleet queue starvation and newest-preference exclusion in networkless scratch.
Existing focused regressions passed; reproductions are synthetic, not claims
about incidents in the owner's real data.

## Implemented behavior

Source-bound action outcomes and outbound delivery records; atomic reminder
effects/confirmation consumption; conservative uncertain-send containment;
purge-compatible new copies; responsive owner-settled maintenance and inference
heartbeat; recent-preference ordering; current-work projection and requested
answer formats; fair fleet scanning and valid-owner terminal stale statuses;
explicit independent-artifact recovery verification; truthful legacy rollback
test-receipt wording. Existing limits, policies and consent remain.

## Accepted release

Deployed and accepted **2026-10-25 11:37:18.325751 UTC** (14:37 Moscow).
All 90 installed modules match runtime source
`ee1a319ce53ddc941ebf4b760c084abd865afd19`; build `de3e62ab43f8`, deployment
`ad778b10272c813105b7080a`. Runtime verification completed at
11:36:11.335587 UTC. The annotated production tag
`production-2026-10-25-assistant-reliability` is published and verified:
object `031a4045a8acb5c5e05015c6c5e59079b61fe221` peels to that runtime commit.

| Evidence | SHA-256 |
|---|---|
| Canonical source archive | `bcfe0116df6e9c8785a6ae0a3dd96382f08305acdce1aaacd75c01d25d57269e` |
| Full isolated Linux verification | `81db5055c27968a304aad77b8ff78443cb81c18931f32d70ef8a7ef3e97094ba` |
| Exact-head CI receipt | `42c974ccb980e9435c0dd2e327ed2456e3e7266f57af6f97b5d1e49de13bd0bb` |
| Final model proof | `75d3414dd32e44e610b22ab7d292f744b1eb86b6e1eb7d1e6bc0e4d54522a561` |
| V6 operator qualification | `321b6d17b2ef34ebcadf83e495382e23c2da65ce58051a4460a0e1089499abef` |
| Forward helper qualification | `63050dff841e91f6f52feb57c05fd871b1559e24d80549860f1e1b7e484811fb` |
| Forward authority | `f41ec4ddd2555428efe445aad7f58dcf682c51e29c7532e8444253eee545c9b9` |
| Final forward acceptance | `dab90aa8735c942b83dddb59b5f29635b4f7d81d1ec7276dba38cf18234dbb0a` |

The exact source passes 2,987 tests in each normal and shuffled Linux order,
with nine existing skips per order, and
[CI run 40187166216](https://github.com/promptinvest/tg-ingest-agent/actions/runs/40187166216).
Original operator and notification regressions remain; the new operator package
passes 48 cases and the actual selected-input handoff. Independent forward-helper
qualification passes 17 cases with network/host commands denied and zero model
or production actions. Final finite model qualification cost USD0.016221024;
preserved rejected trials cost USD0.032152976, total USD0.048374 under the same
USD0.15 ceiling. Sealed carried evidence retains its original source and age;
it is not relabeled as fresh calls.

## Guarded transition and forward completion

The original selected root is
`/root/cara-assistant-reliability-20261025/release-attempt1`, authorized by
`0174ea8b3b3313a3fb909e9a5772c0f0ab20797f7c5d05298e14cd439b6a27e0`.
Protected backups and stopped migration/native-prestart checks preserve original
rows, columns and schema across all 54 existing tables. Only `action_receipts`
and `reply_outbox` are added, initially empty; the final database has 56 tables.
The source-defined new index and idempotent additive migration are checked.
Normal runtime may advance existing operational records after those stopped
boundaries; acceptance does not claim frozen live row hashes.

The first transition failed at strict scoped project reads, before mark-verified,
at 11:16:36 UTC. Cara's existing host adapter still bound Daily Lingo's October21
release, while that peer had an independently verified October25 acceptance.
The adapter correctly returned an observer release-binding mismatch; the typed
read was not relaxed. Main and fleet services were independently confirmed
loaded, inactive and PID0. The failed command, failed containment assertion and
independent stopped-state evidence remain. Its completed prefix was never
replayed; no rollback, database restore or historical user update was performed.

A new single-use forward completion under
`/root/cara-assistant-reliability-20261025/forward-completion-attempt1` launched
once at 11:34:33 UTC, within its separately recorded window ending 12:09:33 UTC.
Helper SHA-256 is
`21206039cfd272f0d4ef9de7701c3151c86e3ce4a1f1f5f2bd35271b2e2015de`.
It authenticates the consumed original identity, failed prefix, exact stopped
backup and installed source, then writes only new continuation evidence. It
archives and acknowledges only the exact failed synthetic scoped-read request,
after proving its request/result hashes and absence of a canonical owner task;
that old unavailable result cannot qualify a fresh read. All nine scoped reads
are then rerun with a fresh identity. There is no reinstall, canonical migration,
additional qualification model call, gateway updater or source publisher.

One necessary integration correction changes exactly nine release-binding leaf
values in `/etc/cara-fleet-host.json`: the outer and nested image, manifest and
paused-step5 receipt, plus the nested accepted revision, time and verification.
They bind only the independently accepted Daily Lingo October25 release
`9bd065c6`, image `dbb8cfe0`, verification `a93bc8e8`, acceptance `097c5b2b`.
Policy SHA-256 changes from
`8cf7ba6edf680e3bb48da95423018b1336bdd36eb47a253e62165b6a433f6bcb`
to `42e119d2262b9ed877224affc4af3f8ca002b5c5f61182b425d99daf8e256bf2`;
the exact reconciliation proof is
`78f3f0b4b6d2fb5cbf7beb7d2ab42517f93d5a2033d7ffd4761140748cd30ffb`.
Every other policy byte, root:root mode0600, paths, grants, targets, source ID,
90/150-second freshness caps and alert preferences remain unchanged. Recorded
observations retain truthful stale gaps between ordinary five-minute samples.
Preflight first authenticates all original 72 protected pins, then reconciles
exactly four peer unit pins against independently accepted Daily Lingo receipts,
without mutating those units. Apart from the explicit policy correction, the
other 71 preflight entries are exact. Two retained original and four fresh isolated MCP fixture
policies bring the complete protected map to 78 known entries. Independent
postacceptance verification receipt
`a3e76e095821240325927da3778af60e736b03c10d659396cbd62ccb43f1608d`
confirms complete map digest
`763f79140a92fbc2376bf004f217bfb7aab65d7b9d636500a70cb7ac33aa56da`.

Fresh actual nonroot namespace readiness and MCP60 gates run before verification
and after a quiescent final restart with a new PID. Isolated admission fixtures,
all scoped reads, source/schema/idle preservation and three final health samples
pass. All 191 captured continuation commands exit zero, with no continuation
failure. The native notice is recorded sent with exactly one attempt. The frozen
native deployment summary retains its original installer wording about unchanged
policy; the explicit forward acceptance above records the authoritative current
exception: one integration policy update, zero gateway updater invocations.
Original release evidence remains unchanged and no completed controller is replayed.

## Preserved verification history

All earlier attempts remain preserved. The first full disk-backed run was
stopped during slow isolated fixture I/O; it did not qualify the candidate.
The next full run completed with two failures: an administrative purge preview
counted its own delivery error, and the historical restart fixture did not
distinguish a legacy send from a send with durable delivery evidence. The purge
flow now retains delivery evidence without generating that self-referential
issue. The restart fixture explicitly creates a pre-outbox send and retains
every original assertion. Separate regressions cover new uncertain sends.

Legacy reminders with no recorded delivery retain the existing recovery path.
For newly recorded sends, an interrupted send remains uncertain and is not
automatically sent again. Known undelivered replies can retry without replaying
their action. Interactive prompts requiring an immediate result or a current
confirmation context cannot be delivered later as an unbound ordinary reply.

The first published candidate `9ae0cb3` passed exact-head CI. Its root Linux
harness incorrectly assigned its temporary parent to the non-root test account,
so protected-root ancestry checks failed; the original logs are preserved.
The corrected harness uses root-owned fixtures for root gates. A subsequent
source review also found a preparation error could repeat a cost reservation.
The corrected candidate terminally contains that error and releases only an
exact provably unsent reservation. A fresh canonical full gate is required.

Candidate `d9a2e14` passed canonical compile, normal and shuffled Linux gates
(2,982 tests in each order, nine existing skips), static checks and exact-head
CI. Its finite five-case model trial cost USD0.01560504 with no unknown attempts.
Manual review rejected the current-work answer: it called the recorded step
pending while also saying the owner had just performed it. The structured reply
also added unsolicited opening/closing text. Those raw responses and charges
remain preserved; no passing model proof or application deployment is claimed.
The prompt correction explicitly separates displayed context from performed work
and makes exact requested structures exclusive. Fresh exact-source qualification
must include the prior charge within the same total USD0.15 ceiling.

The `72a9e23` source also passed canonical full gates and CI, but its model trial
(USD0.016547936, no unknowns) still answered an older request alongside the latest
one. Manual review rejected it; both trials total USD0.032152976. Investigation
confirmed that real task acknowledgements use `record=False`, leaving consecutive
owner requests in prompt history. The correction retains all existing history
and final text, and adds a static current-turn boundary only for consecutive
owner turns. No user content is promoted into a system instruction. Review also
found the work projection used `completed` instead of the runner's successful
step state `succeeded`; successful steps are now excluded from pending next steps.
The uncertainty fixture must represent completed work with successful steps,
rather than a completed task containing unperformed planned steps. Original
inputs/raw/failed verdicts remain; strict quality assertions and the same total
USD0.15 ceiling remain required for fresh qualification.

Prompt/context changes are explicit. Existing sealed model evidence remains
bound to its original source and cannot be relabeled fresh candidate evidence.
The bounded paid qualification and preserved charges above do not authorize
additional or unbounded trials. Acceptance includes the normal once-only native
notice, with no manual Telegram test message, historical replay, facts/source
refresh, recovery-key copying or historical data repair.

The owner does not know the independently retained recovery artifact/key
locations. Recovery after host loss remains unverified; a working-copy
decryption test alone does not prove provenance or survival. This release does
not copy keys or establish a new backup destination.
