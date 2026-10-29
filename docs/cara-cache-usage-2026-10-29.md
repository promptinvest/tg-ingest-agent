# Cara prompt-cache reporting — October 29, 2026

Status: implemented and published in PR9; exact runtime candidate `e834c29`
passed full application verification. The owner approved the exact five-record
reservation exception and guarded deployment. Migration/install completed;
main and fleet are forward-paused at a scoped-read gate. Production acceptance
remains pending at this checkpoint.

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

## Deployment decision required

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

## Private release readiness

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
