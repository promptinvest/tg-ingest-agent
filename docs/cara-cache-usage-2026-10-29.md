# Cara prompt-cache reporting — October 29, 2026

Status: implemented candidate; full verification/publication and production
acceptance pending. No cache warming, paid model call or manual message.

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
- Subsequent focused/full/CI/layout and release checks are recorded below only
  after completion. No production acceptance is implied by source publication.

Private scratch evidence remains on the VPS under `/root/cara-cache-20261029/`.
Only sanitized counts, hashes and outcome summaries may be published here.
