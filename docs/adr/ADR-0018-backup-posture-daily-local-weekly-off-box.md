# ADR-0018: Backup posture: daily local, weekly off-box, verified before rotation

- Status: Proposed
- Date: 2026-09-07
- Phase: Phase C — robustness, operations, security
- Source: 2026-09-07 review of architecture, code and live behavior against build e3208b8 (report kept off-repo; findings are referenced by id)

## Context

BACKUP_INTERVAL_DAYS=7 means a wipe on day six loses six days; the key lives on the same droplet and nothing checks an off-box copy exists; integrity is checked only on the restored copy monthly; failed inbox rows are never pruned; SOLUTION §10 claims off-box key escrow that §9 denies.

## Decision

one db_backup job: snapshot, rotate and encrypt daily when kv backup_day is a day old; offsite only when backup_offsite_day is BACKUP_INTERVAL_DAYS old, or daily when Spaces is configured; quick_check runs on the raw snapshot before rotate and aborts the run on failure; optional BACKUP_KEY_ESCROW_SHA256 compared monthly, mismatch files a backup_key_not_escrowed issue worded as "operator confirmed"; PRAGMA secure_delete=ON stated explicitly; prune_telemetry deletes done and failed inbox rows past retention; SOLUTION §10 corrected.

## Consequences

seven daily encrypted local points; the off-box cadence the owner chose stays; no VACUUM, so restore_budget's bound holds.

## Resolves

store-data#2, #4, #5, store-data P1, P6, P9

## Depends on

owner confirms the 2026-08-21 weekly decision concerned the off-box post, not local disk

## Status log

- 2026-09-07: proposed by the review; not yet discussed with the owner.
