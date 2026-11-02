# ADR-0024 release guard proposal — pending owner decision

ADR-0024 source is verified but not installed. The accepted runtime is running;
the [release record](cara-adr24-release-2026-11-02.md) records the hold.

The proposed change permits only the existing `kv.learning_last_scan` timestamp
to advance during the authenticated online-capture-to-stop interval. Both values
must have the writer's canonical UTC format and strictly advance within those
bounds. Every other schema object, row, field, usage record and cache value
remains exact. Stopped/live and post-install comparisons keep their original
strict whole-database equality checks. No production scheduler behavior changes.

The full before/after snapshots remain, with an explicit unequal-online-state
proof naming the one permitted delta. The change grants no general learning,
operational-KV, shutdown-write or unknown-usage exception. A new reviewed package,
fresh attempt and bounded window would be required; the failed attempt cannot
replay. Tests do not authorize integration or a live attempt.

The inert private artifact passed independent static review and 24 synthetic
Linux tests, F-rule lint and compile. Its default comparator remains strict, and
its descriptor is marked `PROPOSED_NOT_AUTHORIZED`, with no selected attempt.
The initial fixture failure is preserved. Detailed source, tests and receipts are
retained privately for owner review.

The owner's standing rule preserves release checks. Implementation already
authorizes deployment; explicit permission is needed here because this proposal
changes the online-to-stopped check's acceptance criterion from literal equality
to equality with the one bounded timestamp advance described above.
