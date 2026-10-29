# Cara prompt-cache reporting — October 29, 2026

Status: **deployed and accepted 2026-10-29 10:32:20.166933 UTC**, exact runtime
`e834c2927b411302acd1d015e9e621f89e37eb19`, build `94d4bd4840c4`, deployment
`26718b31810856a7bfcdfbf5`. Attempt5 and the independent final audit exited 0;
the audit passed at 10:33:03.995636 UTC. The normal completion notice is recorded
as sent with one attempt. Earlier failed checkpoints below are retained history,
superseded by this acceptance; none of their controllers was replayed.

The [sanitized deployment receipt](cara-cache-deployment-2026-10-29.json), SHA256
`739276ca1c06f5e9e5f2c4afca4ab009d85446f9aabef214907269183a7152a9`, is copied
byte-for-byte from the reviewed closeout collector. It records receipt evidence,
not a new live probe or a cache-savings measurement. No invoice or realized-savings
claim is made. Published tag
[production-2026-10-29-cache-reporting](https://github.com/promptinvest/tg-ingest-agent/tree/production-2026-10-29-cache-reporting)
points to the exact runtime. Remote readback verified annotated tag object
`4d4117db06f08708056acc98fae93c8fc58ee600` resolves to
`e834c2927b411302acd1d015e9e621f89e37eb19`.

The owner's “proceed” selects cache usage reporting and price reconciliation
from the preceding DigitalOcean GA review. This extends existing gateway/storage/
spend/review modules; prompt optimization and optional cache keys stay deferred.
See [ADR-0037](adr/ADR-0037-cache-usage-reporting.md).

## Recorded baseline and fresh read-only checks

Candidate base is documentation HEAD `e60af04`, recording accepted runtime
`83e8162` on October 27. An authenticated October 29 read-only check confirms
main, worker, fleet and speech active with zero service restarts. The loaded
role models and three commercial price overrides match the recorded trial.
The stored daily preference is USD2; it overrides the older environment value.
Monthly remains USD12. No source/environment/service/database mutation occurred
in that check. These are dated observations, not continuing health claims.

The older operator's acceptance window and within-trial checks cannot authorize
or verify a new October 29 release. Completed/failed packages and timers must
remain intact. No started controller is replayed.

## What changes

Each new returned chat usage block gets bounded versioned cache metadata, stored
atomically alongside the existing settlement. Cache read aliases are validated,
not added; Anthropic exclusive input categories and hosted/GPT inclusive counts
are normalized separately. Missing counters, unknown model/endpoint identities
and ambiguous write TTLs retain incomplete coverage. A cached read count of zero
is a measured miss; absent data is not.

Published-rate cost and net savings are frozen at response time. Write premiums
can make savings negative. Spend reports show measured-call and priced-call
coverage, token-weighted reuse and per-model counts in Russian/English. Review
exports use their rolling timestamp window, not spend's UTC calendar week.
Estimates are separate from the configured-rate budget ledger and provider bills.

Only one nullable TEXT column is added, with no backfill. Historical costs and
unknown reservations remain exact. Budget reservations assume no cache savings;
discount estimates never lower an existing or prospective reserved charge.
Models, prompts, payload fields, token/context limits, retries and action/delivery
behavior remain unchanged. The new module is included in the installer manifest.

## Price reconciliation

[DO's published standard prices](https://docs.digitalocean.com/products/inference/details/pricing/),
checked October 29, are USD per million tokens:

| Existing model slug | Previous input/output default | New input/output default |
| --- | --- | --- |
| `deepseek-v4-pro` | 1.392 / 2.784 | 1.74 / 3.48 |
| `deepseek-4-flash` | 0.112 / 0.224 | 0.14 / 0.28 |
| `deepseek-3.2` | 0.425 / 1.36 | 0.50 / 1.60 |
| `kimi-k2.6` | 0.76 / 3.20 | 0.95 / 4.00 |

These rates apply to future calls lacking an explicit override. Existing
`PRICING_JSON` values win, including the deliberate Haiku/Sonnet/Sol ceilings.
Effective dollar limits remain unchanged; correcting understated defaults can
make a limit bind earlier. No historical row is repriced. Old GPT-OSS cache-read
prices are not established by the current table, so positive cache reads there
remain unpriced instead of borrowing another model's discount.

## Verification evidence

- New tests first reproduced missing parser/storage/report behavior and stale
  prices against the original code in a fresh network-isolated VPS scratch root.
- First focused candidate found a review-export name collision; the original
  failure is retained and the import is corrected without weakening its test.
- Focused attempt 2 passed all 74 selected gateway/reporting cases and the
  existing 154 model-trial, 21 continuation, 6 epoch helper cases plus 27 initial
  cache-release cases. Later preservation checks extend that helper suite.
- First full/CI candidate `21cf2c9` found a report-label regression: cache appendix
  model labels shadowed the existing default-price warning when selected by
  label. Cache lines now have a distinct prefix; the original assertion remains.
  Failed full/CI evidence is retained. The local SSH connection outlived the
  completed VPS failure receipt and was closed only after its exit code 1 was
  confirmed; no server controller was replayed.
- Subsequent focused/full/CI/layout and release checks are recorded below only
  after completion. No production acceptance is implied by source publication.

Private scratch evidence remains on the VPS under `/root/cara-cache-20261029/`.
Only sanitized counts, hashes and outcome summaries may be published here.

## Completed application verification

The [sanitized verification receipt](cara-cache-usage-verification-2026-10-29.json)
binds the exact source, archives, both execution runners and retained test logs.

The exact candidate is `e834c2927b411302acd1d015e9e621f89e37eb19`.
Both executed archives have SHA256
`c064f6b3ddef0787713369cdec5cfb5d816fe3733aab3bb2addd894251572863`.
An independent comparison against the Git archive confirms all 350 file bytes
match; archive container metadata differs. The canonical Git archive hash is
`b6db2a5cb3347536402e070f1dc5e8f5221e9072857fe3637a168b062d6a53c2`.

- Linux normal order: 3,299 tests, nine platform skips, 229.135 seconds, exit 0.
- Linux shuffled order, seed 20261007: 3,299 tests, the same nine skips,
  225.193 seconds, exit 0.
- Exact installed-layout gate passed both `agent` and `__main__` entrypoints
  and all five retained dispatch/delivery scenarios. Compilation, all five root
  shell syntax checks and helper suites 154/21/6/29 passed.
- Normal order ran under `full2`; remaining gates completed once in
  `full2-continuation1` after the original SSH transport ended. The original
  normal result is preserved; it was not rerun. Evidence binds both actual runners.
- Compile, normal, shuffled and layout gates used root, cleared environment,
  isolated networking, tmpfs, 360 CPU seconds, 1 GiB address space, 480 wall
  seconds, nice10 and ionice3. Helper/static checks do not claim those resource bounds.
- [CI run 40840016002](https://github.com/promptinvest/tg-ingest-agent/actions/runs/40840016002)
  passed compilation, undefined-name checks, helpers, both unit orders, shell syntax
  and ShellCheck. CI tested the standard PR merge of exact head `e834c29` into
  unchanged main `e60af04`; each order passed 3,299 tests with 62 platform skips.

The gateway still uses Chat Completions. Cache-counter alias tests cover that
API; this change neither migrates to nor claims a tested Responses API parser.
Documentation-only follow-up commits do not replace the verified runtime archive.

## Historical checkpoint: deployment decision required (superseded)

Read-only checks at October29 08:27:21 UTC found main/worker/fleet/speech active
with zero restarts, no claimed jobs, pending replies, started task attempts or
claimed approvals, and no cache column installed. The existing guard nonetheless
rejects five current-process `unknown_chat` rows. They are Kimi2.6 health checks
from October28 14:39:41.428833 through 22:44:07.740478 UTC, with zero recorded tokens
and USD0.0020408 reserved in total. These are unresolved charges, not measured
spend or proof of continuing provider work. The 30 older unknown records remain.

At 08:29:26 UTC, the five complete records hashed to
`1ef181aa028690eaed4dca0e383fb2f71edf36992fdaedb106199e222da05748`.
No raw records, identifiers or provider content are exported here. The old
single-health-check deployment exception does not cover these records.
No controller/window/authorization has been selected and no service restart,
canonical migration, notification or live backup has occurred.

The proposed decision is narrowly to allow deployment with those exact five
old records preserved as unresolved historical reservations, retaining their
charges and stopping on any additional unknown or active work. This exception
has **not** been approved or applied. If selected, its implementation must receive
fresh private operator qualification and a new live preflight before rollout.

## Historical checkpoint: private v4 release readiness

Final operator package v4 passed all 294 isolated tests: 215 inherited tests and
79 additional cache/release cases, plus four actual isolated kernel/import probes.
Independent review confirmed the source/archive protections and strict unknown
guard remain intact. Package SHA256:
`95e16c3554302d1bdffac038bcbc79c2d70fe93cd7f8d7413eb79c5fc3277951`;
qualification receipt:
`158784ce0b41069fcb1bbc7c09e753be6c38d9ec466e5f24ba2c38e57e283f86`.
Failed v1 and v3 preparations and passed v2 remain preserved. The v3 fixture had
incorrectly expected Python's tar reader to retain redundant terminal directory
slashes; the corrected test accepts that harmless normalization while retaining
traversal, duplicate, link and file/directory conflict rejection.

Data-only evidence preparation completed under private `evidence-v1`, producing
eight source-bound receipts and an explicit `offline_evidence_prepared_live_blocked`
readiness record. The full verification receipt is
`4aded823bf9cae52bc402ae808b4abf0b92987fd5c1e0f04c5001ce09d4e9307`.
It selects no authorization, nonce, window or controller. Live backup, migration,
readiness and acceptance gates remain pending behind the unresolved-record decision.

## Owner decision: narrow exception and deployment approved

The owner answered **“Approve the narrow exception and deployment”** to the
explicit question covering the five October28 Kimi health-check records and
USD0.0020408 reserved charge, fresh release checks and the usual single completion
notice. This supersedes the pending-decision checkpoint above. It does not claim
separate owner approval of an operator-selected hash or window.

The approved exception preserves those exact complete records, unresolved kinds
and charges. It does not settle/reprice them, reuse the previous one-row exception,
permit a new unknown call, change budgets/models, or authorize paid qualification.
All 30 earlier unknown records remain separately preserved. A read-only recheck
at08:55:40UTC confirmed the exact same five-record hash; at08:56:09UTC the original
four services were active with zero restarts and no claimed jobs, pending replies,
started task attempts or claimed approvals. These are dated predeployment checks.

Only the private deployment guard changes, with failure tests and independent
review before a fresh package is selected. Original tests, closed trial evidence,
backups, stopped database parity, both readiness phases and final audit remain.
The prior v4 package and pending readiness record remain unchanged historical evidence.

## Deployment interruption: original state preserved

Private operator v5 passed 309 tests with the approved fixed-cutoff five-record
exception. Attempt1 failed fresh preflight before any production mutation because
four protected Daily Lingo monitor hashes had legitimately changed in its separately
accepted release. Read-only verification authenticated the current four hashes and
all other 116 protected files. The old baseline and failed attempt remain intact.

Operator v6 passed all 317 tests and independent review. Its exact package is
`a3926f5a31df02e2c6d7979b6dfefd3b06aabf4b6cbb9127fd2db0c9903ab70d`;
qualification receipt `92b90222a972030284d052f295ea2ced68944a9da047fbe4c062b7c8f9728ed0`.
Attempt2 launched once at09:26:38.374014UTC under the selected09:26:22–10:26:22UTC
window. It passed fresh preflight, staged installed-layout, online/stopped backups,
stopped parity and closed-trial preservation, then failed at09:27:11.571170UTC.

The migration snapshot correctly rejected a backup with a WAL sidecar. The private
tool had passed a WAL-mode backup directly to its standalone-copy reader; its
SQLite connection context managers also did not close backup handles. No canonical
migration intent, rehearsal, source installation or completion notice occurred.
Forward containment stopped main and fleet while preserving current source and data.
The failed controller and its authorization must never be replayed or rewritten.

Read-only verification at09:32:02.616970UTC confirmed the original94 runtime
artifacts/source`83e8162`, no cache module/column, all56 database tables, all120
protected files and all35 unknown records intact. The exact approved five-record
hash and USD0.0020408 reserve were unchanged, with no active jobs/replies/attempts/
approvals. Worker and speech remained active with zero restarts; main/fleet were
inactive. Both retained backup hashes matched their original receipts; the probe
did not open them through SQLite or alter their metadata.

A new phase-specific continuation is being prepared to authenticate the failed
attempt, prove fresh paused-state parity, and perform only migration and remaining
release phases. It will retain the strict standalone-copy guard, use WAL-aware
SQLite backup into new private copies with explicit connection closure, and leave
original/live sidecars intact. Acceptance is still absent at this checkpoint.

## Forward continuation: migration/install passed, scoped read failed

Operator v7 passed all330 tests in56.458 seconds: all317 prior tests plus five
WAL regression and eight continuation cases. Independent review passed. Package
`1fa90893d42e9d66a11d21125e260010940429997791770959081166f0265212` contains199 files;
qualification proof `f809768a02f980c6e291b8380a0f6e19022e37760735ef746051858f991072af`.
The failing reproduction is preserved separately; it is not passing evidence.

Attempt3 launched once at09:45:00.326100UTC with a new09:44:43–10:44:43UTC window.
Fresh paused preflight passed at09:45:18.598787UTC, proving all56 original table
projections and the approved reservations unchanged. Retained backup/WAL/SHM files
were copied into the new private root before SQLite access; originals remained exact.
The live atomic migration completed once at09:45:48.393984UTC, preserving original
columns/rows, all35 unknown records and zero historical cache values. Its receipt is
`4f3ee642811c8b1b1998578dd630b272f19455157414994806059df83cd560b5`.

Native installation and both prestart checks passed, followed by the first actual
namespace readiness and private MCP fixture. The inherited scoped-read child then
returned nonzero at09:47:54.093089UTC. Its command helper did not persist stderr,
so the exact assertion is not established by the generic traceback. Investigation
must inspect existing durable requests/results; these checks must not be replayed.

Physical forward containment is confirmed at09:49:15.724309UTC: main and fleet are
inactive/PID0; worker and speech remain active with zero unexpected restarts. The
controller reported an additional logical-containment error after stopping services;
the retained scoped-read request may account for it and is not to be cleared silently.
Failure receipt `cec052ca9adfb527380913eb314919e108dd9d7feb7b2eeada8fa3e04ee4039d`
and the original exception trace remain. Acceptance, final audit and completion
notice have not occurred. Preserve installed source and migrated data; never rerun
migration, installation or either completed controller as recovery.

## Installed-state diagnosis and preserved qualification history

The retained terminal result established that the failed scoped read was the
Daily Lingo runtime capability. Its typed result was valid, but Cara's fixed
read-only policy still named the October25 peer release. A read-only adapter
check reported `observer release binding mismatch`; the independently accepted
October29 peer receipts authenticated the successor. This did not establish a
Daily Lingo health failure. The original child stderr remains unavailable.

The compatible correction changes exactly nine release-identity leaves in
Cara's policy: three runtime identifiers and six accepted-release identifiers.
Paths, source ID, targets, 90/150-second freshness limits and truthful stale-gap
reporting remain unchanged. The exact policy bytes change from `42e119d2` to
`452ca5f2`; Daily Lingo configuration, services and data are untouched.

Each private operator version and failed qualification remains immutable:

| Version | Qualification outcome | Change or retained finding |
| --- | --- | --- |
| v1 | Failed: 74 tests ran, 15 errors | Import order around the offline socket patch and a schema-AST guard mismatch; original log retained. |
| v2 | Passed 275 | Corrected those preparation issues. |
| v3 | Failed one of 292 | Fixture incorrectly rejected tarfile's harmless terminal-directory normalization. |
| v4 | Passed 294 | Corrected that fixture; traversal, links, duplicates and limits remain rejected. |
| v5 | Passed 309 | Exact owner-approved five-record guard exception. |
| v6 | Passed 317 | Authenticated four current peer-monitor pins; preserved the original baseline. |
| v7 | Passed 330 | Closed standalone SQLite copies and phase-specific continuation; migration/install ran once in attempt3. |
| v8 | Passed 352 | Installed-state completion and exact policy/owned-terminal reconciliation. |
| v9 | Passed 355 | Early single-use rejection and exclusive command intents/diagnostics; used by attempt4. |
| v10 | Passed 356 | Explicit rejection of the real `.started.json` execution marker; qualification only. |
| v11 | Passed 363 | Exact authenticated peer-receipt hardlink pair and retained attempt4 ancestry; used by accepted attempt5. |

Attempt4 launched once at 10:17:56 UTC and failed fresh paused preflight at
10:18:07 UTC, before policy correction, acknowledgement or service start. The
generic single-link guard rejected Daily Lingo's legitimate canonical acceptance
receipt, published as a hardlink to its retained staging receipt. Physical
containment was verified; its failure and all 179 recorded artifacts remain.
The failed attempt was not resumed or rewritten.

V11 permits only those two authenticated aliases of the same root-owned mode 0600
inode, with exact link count, metadata and accepted hash. It reads both with
`O_NOFOLLOW` and checks their identity before and after reading. The generic
single-link guard remains unchanged for all other inputs. The actual read-only
input check passed at 10:26:16.633640 UTC, validating peer receipts, proposed
policy bytes, the retained terminal request/result, all 95 installed artifacts,
the current 56-table snapshot and approved reservations. It invoked no controller,
service action, acknowledgement or model call. Full qualification then passed
363 tests in 69.211 seconds, retaining all 215 original operator tests and all
intermediate regression cases, with zero failures/errors/skips or live actions.

## Accepted installed-state completion and final audit

Attempt5 used a fresh 10:29:13–11:29:13 UTC window and exact v11 package
`5dabab41f7cd467698219cb727b9f17d41232cbd0cb248c57ce4a966623a8825`.
Its qualification receipt is
`000c34022bc2fd668a1a1911bfd7b61a456566efd486b5f1618e8d1df7676b8b`;
the actual-input read-only proof is
`8d30ba383b469ca5816ccc14763a2aa24141e0ee82638462bc1be478af830904`.
Source and archive remained the exact application already verified above.

Fresh paused preflight and a new closed SQLite backup preceded the exact policy
correction. The one completed synthetic request was checked against its original
nonce/task/result hashes and absence of owner-work references, archived, then
acknowledged once through the normal protocol. Its original result remains; no
old scoped request ID was replayed. Fresh scoped checks used a new identity.

The continuation started the same installed runtime and passed native runtime
verification, all 60 facts cells, six MCP tools and nine private fixture scenarios
in each actual non-root readiness phase. All nine scoped reads passed. Quiescent
final restart produced a fresh process, followed by the second readiness phase,
three health samples and exact preservation checks. The native manifest was
marked verified once; the usual completion notice has status `sent`, attempts1.
No manual message, notification replay, paid qualification, second migration,
reinstall, database restore or earlier controller replay occurred.

Acceptance completed at **10:32:20.166933 UTC**, receipt
`33c233dcbb1601a925890ec47ecb5464d8d35d3cca76657f83930477b1f95a79`.
The separate read-only audit passed at **10:33:03.995636 UTC**, receipt
`2241154d04d11e302c192aa9ed60db742daf4c215241faec56751e82a109a6a9`.
Both process exit codes were independently checked as 0. The audit preserved
release diagnostics and verified the durable single-send row without sending.

At audit, all four services were active with zero systemd restart counts.
The 95 native artifacts and 126 protected files matched the selected maps; the 122
pre-continuation protected files include the retained original fixture pair,
and only four fresh fixture policies were added alongside the exact existing-file
policy correction. Migration preserved all 56 original tables/projections and
added only nullable `llm_usage.cache_usage_json`; no old rows were backfilled.
All 35 unknown records remain, including the exact approved five and their
USD0.0020408 unresolved reserve. No charge was refunded, settled or repriced by
the exception. Daily/monthly/task ceilings remain USD2/USD12/USD0.15; prompts,
models, commercial overrides, request payloads and action behavior are unchanged.

The completed model trial's original October28 13:36:17.924552 UTC end, evidence
and inactive observers remain intact, with zero new epochs or observer actions.
All prior failures, backups and observation pauses remain; this is dated
acceptance evidence, not an uninterrupted-availability claim. Cache reporting is
now deployed, but the closeout measures neither realized savings nor provider
invoices. Those remain distinct from the frozen published-rate estimates.
