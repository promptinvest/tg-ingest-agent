# ADR-0024 narrow release guard — approved and verified

ADR-0024 source is installed and accepted. The
[release record](cara-adr24-release-2026-11-02.md) records verification and the
preserved failures.

The owner approved the recommendation with “OK, go”: apply this narrow guard
change and proceed with a fresh reviewed rollout. The earlier failed attempt and
its receipts remain consumed. No exact package or window is implied by that reply.

The approved change permits only the existing `kv.learning_last_scan` timestamp
to advance during the authenticated online-capture-to-stop interval. Both values
must have the writer's canonical UTC format and strictly advance within those
bounds. Every other schema object, row, field, usage record and cache value
remains exact. Stopped/live and post-install comparisons keep their original
strict whole-database equality checks. No production scheduler behavior changes.

The full before/after snapshots remain, with an explicit unequal-online-state
proof naming the one permitted delta. The change grants no general learning,
operational-KV, shutdown-write or unknown-usage exception. A new reviewed package,
fresh attempt and bounded window were selected for the approved execution; the
failed attempt remains consumed and cannot replay. Tests do not authorize integration or a live attempt.

The inert private artifact passed independent static review and 24 synthetic
Linux tests, F-rule lint and compile. Its default comparator remains strict, and
the historical proposal descriptor remains `PROPOSED_NOT_AUTHORIZED`, with no
selected attempt. A new independently authenticated approval descriptor bound
the approved scope to the fresh package and selection. The actual rollout passed
with one permitted timestamp advance; every other value remained exact.
The initial fixture failure is preserved. Detailed source, tests and receipts are
retained privately for owner review.

The owner's standing rule preserves release checks. Implementation already
authorizes deployment; the owner’s later reply grants the specific permission to change the online-to-stopped check's acceptance criterion from literal equality
to equality with the one bounded timestamp advance described above.
