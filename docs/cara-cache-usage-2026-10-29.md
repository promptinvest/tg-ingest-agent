# Cara prompt-cache reporting — October 29, 2026

Status: implemented and published in PR9; exact runtime candidate `e834c29`
passed full application verification. The owner approved the exact five-record
reservation exception and guarded deployment. The private exception is being
qualified; production acceptance remains pending at this checkpoint.

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
