# Cache reporting: post-trial release preparation

This directory provides **data-only preparation and offline preservation
contracts**, not a qualified production controller. No command here can install,
restart, send a message, call a model, alter the production database or accept a
release. `prepare` deliberately writes `prepared_not_deployable`, with every live
gate pending and no deployment authority or selected window.

The October 27 operator is retained unchanged. It assumes no schema change and a
window ending before October 28 at 13:36:17.924552 UTC. Those assumptions cannot
authorize an October 29 release. Do not substitute hashes into that controller,
modify its completed receipts, relax its gates or replay it.

## Authorized application scope

The cache work extends existing gateway/accounting/reporting components. It adds
`cache_usage.py` and one nullable `llm_usage.cache_usage_json TEXT` column. There
are still 56 tables. All old columns, row values, unknown reserves, original
receipts, history, model profiles, request payloads, prompts and native budget
limits stay intact. No backfill or historical repricing is permitted. Only the
reviewed DO-hosted default-price updates change forward estimates; the commercial
`PRICING_JSON` overrides remain exact.

`verify_source_delta` permits only those runtime modules and the installer list,
alongside tests/docs and this inert preparation package. Both living specs must
change. An independent source review must establish unchanged prompts, payloads,
model profiles, budget limits and commercial overrides. No new paid qualification
is included: gateway/accounting fixtures exercise the changed response handling.
Historical model qualification allowances are not reused.

## Offline checks

Run outside OneDrive, preferably in the same hermetic Linux verification root as
the application:

```sh
cd ops/cache-release
python3 -B -m unittest discover -p 'test_*.py'
```

The application still needs exact-source full Linux normal/shuffled discovery,
compile/static checks, exact-head CI and packaged/installed `agent.py` behavior.
Original test assertions and failed receipts remain. This helper suite does not
replace any existing gate and is not a live migration/backup proof.

## Stopped database preservation

`snapshot` opens a standalone stopped backup with `mode=ro&immutable=1`,
`query_only=ON` and one read transaction. It refuses a neighboring WAL or rollback
journal, so pending committed data cannot be silently ignored and SQLite cannot
write a shared-memory sidecar. Do not point it at an active production database.
It validates integrity/FKs and emits schema plus counts/hashes, not stored row
values. The caller independently proves quiescence, protects and pins backups,
and authenticates snapshot receipts. Never treat a read-only snapshot as proof
that services were stopped.

```sh
python3 -B cache_release.py snapshot --db /protected/stopped-before.db > before.json
# A separately reviewed operator invokes the canonical additive migration once.
python3 -B cache_release.py snapshot --db /protected/stopped-after.db \
  --original before.json > after.json
python3 -B cache_release.py verify-parity --before before.json --after after.json
```

Parity requires exact original table set and original-column projections, all
rows and duplicate cardinalities unchanged, all unknown rows/charges exact,
indexes/triggers unchanged, same application user_version, and exactly the one
nullable TEXT column with every historical value NULL. SQLite itself computes
the expected `ALTER TABLE` DDL in an in-memory database. There is no mutation
command here. Fresh before/after receipts must be bound to the selected source,
backup, stopped identities and actual single migration in the live operator.

## Completed model trial

Preparation pins the accepted source/archive/acceptance and sixth-source
continuation. It requires a terminal observer receipt for the exact original end
and fresh inactive states plus unit-file hashes for every original/continuation
observer timer. The last stopped timer may remain enabled; preserve that state.
Elapsed time alone is not a terminal receipt. Missing final evidence or an active
observer requires investigation, not a rerun to manufacture evidence.

No seventh trial epoch is appended; no original end is reset; no observation is
replayed; no timer is rearmed. New source operation after the trial is separately
recorded by the future release operator and cannot be presented as part of the
closed model-quality comparison. No model reversion is implicit.

## Data-only preparation

`prepare --help` lists exact file/hash inputs. It accepts the original pinned
baseline archive, acceptance and continuation, a new source archive/full commit,
and hash-bound preparation inputs. The inputs contain normalized exact-source
receipts for `linux_full`, `ci`, `installed_layout`, `cache_accounting` and
`source_review`, together with `observer_states` and `terminal_observation`.
They must be produced by independently verified checks, never by editing a failed
status into a pass. The preparation directory must be fresh; failed/partial
directories remain evidence and are never overwritten.

## Remaining live operator work

The mutation backend is **not implemented or qualified here**. Before any launch,
a fresh independently reviewed package must retain the full existing release
sequence and add the stopped migration contract. `REQUIRED_RELEASE_GATES` lists
the pending obligations: current accepted runtime/environment/idle/peer checks,
online and stopped backups, exact installer/source/layout checks before both
starts, native runtime/security, both actual namespace readiness phases and
private MCP fixtures, all nine scoped reads, original protected files and peers,
quiescent final restart, three healthy samples, one durable native terminal
notice, closed-trial preservation and independent final live audit.

That package must also prove fail-closed forward containment after each possible
failure. It must never restore an older database or use the prior one-time
unknown-healthcheck exception as a general bypass. Existing unknown rows keep
their full reserved costs. No missing gate is waived by this preparatory record.
