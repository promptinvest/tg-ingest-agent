# ADR-0024 release held, 2 November 2026

ADR-0024 source `2b6da6ee0ca43f7c8aa1073fe5fddb069bfd91b2` is implemented,
verified and merged through [PR 15](https://github.com/promptinvest/tg-ingest-agent/pull/15)
at `daf1ffe931c5f38d2c8e8f309f0edaddba377e20`. Its deployment failed before
installation. The accepted ADR-0018 source `6e947694518659e4a41d516f6f35c4f3012f49f6`
remains installed and verified. Its main and fleet services were resumed once;
a separate read-only observation passed at **11:46:55.225406 UTC**. The new
generic undo command is not live.

The selected source changes eight runtime files. It preserves the schema,
prompts, models, budgets and existing undo phrases. The owner selected the new
exact latest-action command alongside the existing targeted behavior.

## Verification before deployment

- Exact-source Linux: 208 focused checks; 3,450 tests in each normal and shuffled
  run, with nine skips retained in each. F-rule lint, compile, shell/helper and
  five installed-layout scenarios passed. Actual outer wait returned zero and
  the owned group was absent.
- Exact-head CI passed. Every current original test/support file remains byte
  identical: 112 files, map hash `a7542be6a9c20f248c33fe1c4449feb78e689099980a5a400fb5d70426e8c661`.
  The dated historical 110-file subset also remains byte identical; its old
  12,452-line figure is historical provenance, not a newly computed count.
- Fresh operator snapshot4 passed 103 tests, lint and compile. Fresh transport
  passed 20 tests, lint and compile. Two actual nonroot import-probe roles and
  three actual cgroup cleanup qualifications passed.
- Fresh native baseline-check2 passed with all 136 protected entries, the
  56-table schema, historical unknown cohort40 and current-process unknown0.
  Earlier failed qualification/baseline attempts remain preserved.

## Preparation and failed transition

The original selected root is
`/root/cara-adr24-20261102/release-attempt1-9a6ea38b48425d08`.
Its one-shot claim selected the original window
`11:10:34.787921–12:10:34.787921 UTC`; the claim and window were never reset.
Authorization records the implementation request and standing deployment rule,
without implying a separate hash-specific approval or unknown-usage exception.

The first preparation failed after the exclusive claim and copy stage because
the reference module namespace was already occupied by the qualification tree.
It failed before live baseline collection or any production operation. A fresh
interpreter completed only the unwritten preparation tail in the same root.
All 647 original partial leaves, bytes, owners and modes were rechecked before
sealing. The original failure, selection, claim, source, nonce and window remain.
The continuation returned zero at 11:21:42 UTC and did not launch a controller.

One separately reviewed dispatch invoked the sealed launcher/guardian. The
native controller completed **five of its unchanged 21 phases**, then failed at
`stopped_backups` at 11:30:31 UTC with
`stopped full schema/rows/cache changed`. The first comparison, between the
stopped backup and stopped live database, passed. The traceback identifies the
second comparison, between the earlier online backup and stopped backup.

The read-only diagnostic found equal schemas across all 56 tables. Between the
online and stopped backups, only one existing `kv.value` changed, for
`learning_last_scan`; no key was inserted or deleted. The stopped backup and
stopped live database matched in every table. This advance is consistent with
the accepted source's public once-per-minute learning-maintenance writer.
Retained snapshots alone do not establish the exact writing actor.

No preservation guard was waived. No candidate installation, migration,
database restore, source rollback or candidate acceptance occurred. Native
containment physically paused main and fleet, retaining the accepted source
and database. Actual controller and guardian waits returned one; owned children
were reaped, groups absent and the nonce-owned tool cgroup empty. Worker and
speech stayed on their original generations.

## Accepted-source availability recovery

A separate once-only recovery verified the accepted manifest and complete
95-module/96-runtime-file maps, 136 protected entries, environment/settings,
peer and recovery evidence, schema56, all56-table stopped/live equality,
original usage and historical unknown40/current0, and absence of release
actors and cgroup population. It issued only
`systemctl start tg-ingest-agent cara-fleet` for the unchanged accepted source.
The actual start returned zero.

The recovery's first postcheck failed with `new unknown usage blocks release`.
That failure is retained and is not represented as a successful recovery run.
A later, separate read-only availability observation passed three samples:
all four services active with zero restarts; main/fleet/worker generations
stable; original worker and speech generations preserved; accepted source,
manifest, settings, budgets, protected files, peers and original usage retained;
strict current-process unknown0 and exact historical40. It issued no service
action, database mutation, replay or manual message. Its receipt hash is
`e64647e3053d785afce73f538e477a07566e701b979ffad9af529549121cace3`.
This is a dated observation, not a claim of uninterrupted future health.

## Remaining boundary

ADR-0024 remains **not installed and not accepted**. Its original controller,
claim and failed attempt remain consumed; the accepted-source recovery and its
failed first postcheck also remain consumed. Nothing may replay or reset them.
The [narrow online-to-stopped guard proposal](cara-adr24-release-guard-proposal-2026-11-02.md)
is a separate pending owner decision. It changes neither the failed original receipts nor the strict
stopped/live comparison. The inert proposal separately passed 24 synthetic Linux tests, lint and compile;
its original fixture failure remains. These checks authorize no live attempt.

The [verification JSON](cara-adr24-release-verification-2026-11-02.json) lists
original evidence hashes. A separately validated allowlist export retained
sanitized projections of 60 original artifacts in 62 archive members, with a
per-file SHA256 manifest; archive hash
`84eaf23eb55ac1aa0abe10f5d364d4a8bdb9ddbb42a1d7ee8eec328bfef84444`.
Database files, environments, keys, private logs, raw protected manifests and
runtime data are excluded. Original private evidence remains untouched.

The documentation-only follow-up passed 14 native Linux checkout checks and
29 preservation-contract checks, with no runtime/test/script bytes changed.
It requires no installation, restart or production tag. All original
production tags remain historical accepted-release references.
