# 2026-07-03 Cara/Nikki memory split — executed, archived

**Do not run anything in this directory.** These files are the audit record of a
one-shot migration that already happened on 2026-07-03: Cara's DB was stripped of
the companion-register memory and Nikki's DB (`/var/lib/nikki-agent/nikki.db`) was
created from a copy of it.

| File | What it is |
|---|---|
| `split-cara-nikki.sh` | the migration itself. Its DEFAULT mode is the destructive split (`--verify` is the read-only one), so any argument typo re-runs a migration against the live DBs. Kept only as the record of exactly what ran. |
| `split-curation-2026-07-03.json` | the operator-confirmed row curation it consumed (ids, counts, digests — no message text). |
| `split-baseline-counts-2026-07-03.json` | the Phase-0 baseline row counts it verified against. |

Why archived (review-fixes T10.6, 2026-07-26): the script sat armed at the repo
root long after it was needed, one mistyped argument away from re-splitting a live
database. The 2026-07-17 PRD (item 16) had already called for archiving it.

Rerunning it today would strip Cara's DB a second time against a stale curation
list. If a comparable migration is ever needed again, write a new script from
this one — do not invoke this file.

A staged copy still lives on the PD box at `/root/cara-nikki-split/` (the script
plus these inputs). It **must be removed** by the deploy that lands this change —
`rm -rf /root/cara-nikki-split` — and the removal recorded in the PD-VPS knowledge
base. Nothing in this repo performs or verifies it; until it is done, the armed
one-shot is still on the live host. See `../../CARA.md`.
