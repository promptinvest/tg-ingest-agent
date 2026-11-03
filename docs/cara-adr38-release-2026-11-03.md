# ADR-0038 — durable handoffs and boundary validation

Status: **deployed and accepted; separate audit, physical closure and production
tag verified**. Exact source `edf5d81e259a5530b01595395aefc007a792aa63`, archive
SHA256 `fd406d16459d582f72072d4d7d312e1c2e3837c3aaf2cda244eb04cee9ec19cf`,
build `7c37b7fc8e7d`, deployment `2adbff506160eedd86df7095`. The verified native
manifest is `3929e80d81064a395658bad144f0f28d2a3906e5e91aace1e6d99050aa30e5e6`.
[PR 20](https://github.com/promptinvest/tg-ingest-agent/pull/20) merged as
`51d95516fac427bd64003dc451c13ca3e237c149`. Detailed operational receipts remain
private; the [verification summary](cara-adr38-release-verification-2026-11-03.json)
records public hashes and the limits of each result.

Voice downloads and transcription use one admitted transport with overflow in
the durable owner-scoped inbox. Accounting and transcript handoff commit
together; a completed result survives a SQLite outage, and cached routing
retries use the existing update cap. A durable remote-dispatch marker prevents
an interrupted paid transcription from authorizing another dispatch. Transport
threads receive captured inputs and no SQLite connection.

Scheduled occurrences retain per-recipient delivery identity through uncertain
outcomes and known-unsent retries. Raw observed transitions distinguish later
health episodes, disk hysteresis retains its existing margin, and grouped model
state stamps commit atomically after acknowledged delivery. Expired fleet cards
leave ordinary unquoted confirmations available while exact-card responses and
live ambiguity checks remain. Knowledge searches apply confirmed-note
eligibility before the result limit. The gateway validates response shape and
strict nonnegative bounded integer usage before settlement; invalid data retains
its unknown reservation. Existing missing/null/zero estimates and prices remain.

Exact-source Linux verification passed 224 focused checks and 3,516 full checks
in each normal/shuffled order, with nine skips retained. All 393 committed source
files match tested bytes; all 120 original test/support files and assertions
remain unchanged. Forty-six new application tests cover the fixes. Ruff,
compile, shell syntax, ShellCheck, helper suites and all five installed-layout
scenarios passed. [Source CI](https://github.com/promptinvest/tg-ingest-agent/actions/runs/41612475526)
passed. The original strict operator passed 139 cases, its unchanged historical
suite 139, transport 27, two nonroot roles and three actual cgroup cleanup cases.

The first native attempt remains **FAILED**, at 10:11:05.098105 UTC, after 16
phases. Its strict current-process unknown guard rejected the then-live snapshot;
canonical failure SHA256 is
`f7eae52c613657419939a704b555306ca176d9b5adaa77d67a4218200d86195b` and
containment is `820856df9fa7d25d92b8ccc9f66ab20568508783bbd277f09c8e642934902f9e`.
Main and fleet were physically paused, the candidate and data preserved, and
independent failed-process closure
`6a29b1ae73bfb7b09fd99d8d9a4efd9ebc1001f0d6e71ccc25de04d5c482a695`
confirmed the original generation ended. No acceptance or successful audit was
created for that attempt.

A later read-only contained check passed the original strict-zero guard and
fixed historical 40-row fingerprint, preserving original usage. This observation
does not identify the row seen by the failed check or establish a provider fault.
Gateway reservations are temporarily unknown before their returned outcome is
settled. The fresh forward operator permits at most 30 seconds of read-only
observation for the exact current-unknown rejection, using a fresh transaction
and the same process generation each time. It retains the complete original
validator; persistent unknowns, changed history, generation/window changes and
other failures still block. The read-only observer never edits reservation rows,
grants refunds or exempts unknown outcomes; ordinary application settlement
remains in force.

The same installed candidate then completed a fresh 13-phase forward chain,
preserving the original 16 successful phase receipts and every remaining native
gate. All 1,062 failed-root files and the separate custody inventory retain exact
bytes and modes. New qualification passed the unchanged 139 original cases,
54 forward/support cases and static checks. Twelve additional read-only observer
cases passed. The first new-qualification fixture failure remains preserved;
its corrected inert `assert_stopped` mock kept all assertions. The original
verified marker was observed read-only, and the existing deployment notice
remains sent once. No installation, mark-verified call, restore or controller
replay occurred in the forward completion.

Canonical acceptance completed at **10:55:20.036644 UTC**, SHA256
`c006b324bc3c38133d2414fe3d17728d62c8afc6593afdc3397762ebc0710b3a`.
The independent read-only audit passed at **10:55:59.330167 UTC**, SHA256
`d7571560b10170578590621b48c730509d3c9e05d2fd04b88613a985121ec646`.
Independent physical closure passed at **10:56:50.037273 UTC**, SHA256
`7fee2f66c8aadee5a17124b519f7943ca929c1ea52d6e96490346a9a6c3eb16f`.
Actual controller, supervisor, guardian, audit and foreground results were zero.
All four recorded actors/groups, the outer process and owned tool cgroup were
absent; all 429 sealed package files remained exact. Four services were active
with zero recorded restarts at closure. These are dated observations.

Annotated tag
[`production-2026-11-03-adr38-durable-handoffs`](https://github.com/promptinvest/tg-ingest-agent/tree/production-2026-11-03-adr38-durable-handoffs),
object `e8e77761836de29c28e3cbca35b749367873b131`, was published and remotely
verified at 10:57:43.817926 UTC to peel to exact source `edf5d81e`. All 16 earlier
tags and 31 earlier tag refs remain exact; only the new tag and its peeled ref were
added. Publication proof is
`97b4c38dc32cddc3b6e69b033a2289a51d7998447042b5510621b842b924c142`.

Models, prompts, effective USD2/day, USD12/month and USD0.15/task limits, schema
56, stored history/cache, protected configuration, neighboring services and
notification preferences remain preserved. Current unknown usage was zero and
all 40 historical unknown rows stayed exact. No new paid qualification or manual
message was authorized by the release; ordinary application accounting remains
in force. Independent backup-key custody and host-loss recovery remain unproved.
ADR-0022 stays postponed; response-byte caps and other unselected improvements
remain outside this release. This documentation closeout changes no runtime file.
