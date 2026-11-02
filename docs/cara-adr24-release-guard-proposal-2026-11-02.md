# ADR-0024 release guard proposal — pending owner decision

ADR-0024 source is implemented and verified. The first rollout failed before
installation; accepted source `6e947694` is running. The
[release record](cara-adr24-release-2026-11-02.md) preserves that failure and the
separate availability recovery. This proposal selects no new attempt.

## Concrete decision

Authorize a new independently reviewed rollout whose online-to-stopped comparison
permits only an advance of the existing `kv.learning_last_scan` timestamp during
the authenticated capture-to-stop interval. Every other schema object, table,
row, column, KV value, usage record, charge and cache field remains exact. Every
stopped-backup/live and post-install comparison remains the original strict
whole-database equality check.

The current comparison requires literal equality between an online snapshot and
a later stopped snapshot. The failure investigation found exactly one existing
KV value changed, with no keys or rows added/deleted and all other 55 tables
exact. The stopped snapshot matched stopped live data across all 56 tables.
Accepted public code has a once-per-minute networkless learning-scan writer;
the observed delta is consistent with that writer. The snapshots do not prove
forensic attribution to a particular writer.

## Limits of the proposed change

- Only the existing row's `value` may change. Both values must use the exact UTC
  ISO format emitted by the existing writer; the new instant must strictly
  advance within actual online capture start and inactive-service observation.
  Alternate offsets, `Z`, naive dates, counters, malformed values and backward
  timestamps fail. A tick during backup copying is bounded by capture start.
- Capture start/finish, stop request and inactive observation must be ordered,
  authenticated operation receipts. Complete snapshot hashes, the accepted main
  generation before capture and immediately before stop, and the exact accepted
  scheduler/entry/store source hashes must be bound to those receipts.
- All other schema/data/membership/cache/usage checks stay strict. Learning that
  changes any other row still blocks. No general operational-KV, shutdown-write,
  learning-state, cache or unknown-usage exception is proposed.
- The proof explicitly reports unequal online/stopped snapshots plus the sole
  permitted public delta. Both original complete snapshots and exact before/after
  hashes remain. No result may relabel that comparison as full equality.
- A new source/operator package, fresh root, nonce, custody and bounded window
  must be selected and independently reviewed after the decision. The consumed
  failed root, its claim, original window, controllers and receipts cannot replay.
  The timestamp policy changes no production scheduler behavior.

## Review artifact and verification

The private eight-file artifact is
`../_local/tg-ingest-agent/adr24-20261102/online-transition-proposal2/`.
It contains a pure comparator, synthetic tests, byte-exact qualified comparison
helpers, the public accepted writer reference, a README and a descriptor marked
`PROPOSED_NOT_AUTHORIZED` with null selection fields. It has no service,
installer, transport, publisher or authorization-writing entry point. A positive
fixture in a synthetic test is not an owner decision.

Independent static review cleared the boundary. Fresh network-isolated Linux
checks passed all 24 synthetic tests, F-rule lint and compile. The first run
retains one new fixture failure: assigning the identical old timestamp yields
strict equality, so no exception is used. The successor corrects only that
fixture to assert exact parity; comparator and negative cases are unchanged.
The
[verification record](cara-adr24-release-verification-2026-11-02.json) records the
frozen artifact and fresh isolated Linux test result. Successful tests do not
authorize integration or a live release. Until the owner decides, the application
stays on accepted ADR-0018 source and the original strict guard remains active.

Permission is required by the owner's standing rule to preserve release checks:
implementation already authorizes deployment, but this proposal changes the
online-to-stopped comparison's concrete acceptance criterion. The decision is
limited to this one existing timestamp and a newly reviewed attempt.
