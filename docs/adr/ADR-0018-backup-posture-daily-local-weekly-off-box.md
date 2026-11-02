# ADR-0018: Backup posture: daily local, weekly off-box, verified before rotation

- Status: Narrow follow-ups deployed/accepted 6e947694, 2026-11-02; independent audit passed; custody/off-box provenance unproved; failed-inbox deletion excluded
- Date: 2026-09-07
- Phase: Phase C — robustness, operations, security
- Source: 2026-09-07 review of architecture, code and live behavior against build e3208b8 (report kept off-repo; findings are referenced by id)

## Context

BACKUP_INTERVAL_DAYS=7 means a wipe on day six loses six days; the key lives on the same droplet and nothing checks an off-box copy exists; integrity is checked only on the restored copy monthly; failed inbox rows are never pruned; SOLUTION §10 claims off-box key escrow that §9 denies.

## Decision (original proposal; narrowed by the reconciled scope below)

one db_backup job: snapshot, rotate and encrypt daily when kv backup_day is a day old; offsite only when backup_offsite_day is BACKUP_INTERVAL_DAYS old, or daily when Spaces is configured; quick_check runs on the raw snapshot before rotate and aborts the run on failure; optional BACKUP_KEY_ESCROW_SHA256 compared monthly, mismatch files a backup_key_not_escrowed issue worded as "operator confirmed"; PRAGMA secure_delete=ON stated explicitly; prune_telemetry deletes done and failed inbox rows past retention; SOLUTION §10 corrected.

## Consequences

seven daily encrypted local points; the off-box cadence the owner chose stays; no VACUUM, so restore_budget's bound holds.

## Current reconciled scope

The later evidence-preservation rules supersede the proposed deletion of failed
inbox rows: retain that recovery evidence. No backup destination, schedule,
rotation policy or independently held key is selected by this implementation.
Optional `BACKUP_KEY_ESCROW_SHA256` confirms only exact key-file bytes against an
operator-supplied fingerprint during the existing monthly restore check. An
absent value leaves the result and file access unchanged; mismatch/unreadable
files record an observation without replacing the actual restore outcome.
Matching bytes do not establish custody, off-box provenance or host-loss recovery.

The native Python 3.12.3 / SQLite 3.45.1 build was checked read-only: its
`SECURE_DELETE` compile option already makes `secure_delete=1` the default.
Explicit `secure_delete=ON` therefore pins existing native behavior. No VACUUM,
retrospective scrub, WAL/checkpoint change or forensic erasure guarantee follows.

## Resolves

store-data#2, #4, #5, store-data P1, P6, P9

## Depends on

owner confirms the 2026-08-21 weekly decision concerned the off-box post, not local disk

## Status log

- 2026-09-07: proposed by the review; not yet discussed with the owner.

- 2026-10-07: owner authorized "fix all & deploy" following the code/live review. Partially implemented 2026-10-07 (daily local / weekly off-box / snapshot integrity; escrow and telemetry follow-ups remain). Verification and deployment evidence: `../../docs/cara-review-fixes-2026-10-07.md`.

- 2026-10-07: shipped in reviewed release `7995fad` and corrected release `36f2075`; live build `9d041a123f25`, receipt `0f839bc1e8c673d4a6f196df`. The August 22 recovery archive is restored and all seven weekly pins are verified. Remaining subtasks named in the status line stay open.

- 2026-11-02: owner requested the next Cara ADR after the reconciled ADR0027
  closure. Narrow optional fingerprint confirmation and explicit existing native
  secure-delete mode are implemented, verified and published as source `0be55ff`.
  Full native and CI normal/shuffled suites, static/operator/layout gates pass;
  strict release preflight still blocks installation pending an owner decision.
  The prior exact-four exception remains consumed. Independent custody requires
  separate evidence; failed-inbox deletion is excluded.
  [Implementation and evidence](../cara-adr18-2026-11-02.md) distinguish completed
  source work from the held release and remaining independent-custody evidence.

- 2026-11-02: the owner's later “I approve” selected one exact healthcheck
  exception for this release. Fresh exact-source verification, all 21 native
  release phases and the separate audit passed for source `6e947694`; native
  acceptance completed at 09:25:38.717106 UTC. The exception is consumed;
  retained records are unchanged and every new unknown remains blocking.
  [Release and verification](../cara-adr18-release-2026-11-02.md) supersede the
  earlier held release checkpoint without replacing its evidence. Independent
  custody/off-box provenance remain unproved; failed-inbox deletion is excluded.
