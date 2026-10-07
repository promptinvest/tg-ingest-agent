# Cara review fixes - October 7, 2026

Owner instruction: "fix all & deploy", following the code and live-performance review.
This release addresses the confirmed October 7 findings; remaining historical ADR
subtasks are explicitly status-tracked and are not represented as completed.

## Scope

- Enforce cost reservation before every paid chat, embedding and remote STT call;
  settle known usage, release definite rejections, preserve ambiguous/missing-usage
  reservations. Historical usage records remain intact.
- Apply absolute inference deadlines across retries and failover; keep network-only
  transport helpers separate from SQLite. Reuse genuine recent successful chat for
  model health instead of paying for redundant probes.
- Compute reminder notification offsets from the event time and source wording;
  clarify absent/ambiguous times, accept named weekdays on reminder follow-ups,
  preserve conversation language for short Latin entities.
- Keep explicit replies to the gratitude reminder journal-bound after pending expiry;
  retain confirmation and command/question precedence.
- Ground KB answers in exact retrieved source excerpts and real note numbers;
  refuse when context is empty or selections invalid.
- Run own-voice transcription off the polling thread; persist its transcript before
  routing, meter only on the main thread and resume cached transcripts on restart.
- Stop and disable Mentor and its runner, preserve their source/history/results,
  replace weekly automation with the owner-approved monthly redacted open-issue digest.
- Check local snapshots daily, preserve weekly encrypted off-box timing and prior
  weekly recovery points, retain monthly real restore checks and protected key files.
- Clip router hints while preserving storage, forwarding fences and note references;
  expose router input statistics and unknown spend. Describe configured search and
  calendar availability honestly.
- Extract media/conversation methods without changing their behavior, retain Telegram
  entry-module hooks, share test fixtures and split three subject suites. Provide
  named test subsets, slow-case timings, offline tmpfs fixtures and seeded shuffle.

## Baseline and verification

The review matched all 51 installed Python source files to the local baseline after
LF normalization. Production baseline build: `e0ac2be522cb`, runtime source `07eb81a`.
The unchanged Linux suite passed before implementation: 1,656 cases, nine skips,
643.455 seconds. Windows platform-only failures were not treated as runtime defects.

The first candidate full run failed four cases (transport timing mock/socket timeout,
backup cadence expectation, configuration catalogue). They were corrected; the daily
local-backup test now explicitly preserves the independent weekly off-box default.
The first post-extraction run exposed entry-module Telegram hooks and an old class
reference; corrected without weakening assertions. All 266 focused media/gateway
cases then passed. Failed results remain under protected `/tmp/cara-fix-verify-20261007-*`
verification directories on the VPS.

The full Linux gate passed 1,681 cases (nine unchanged checkout-dependent skips)
in 81.934 seconds at `/tmp/cara-fix-verify-20261007-1bcf2c6231`. The complete-checkout
seeded shuffled gate then passed 1,682 cases with no skips in 89.173 seconds at
`/tmp/cara-fix-verify-20261007-1800cf005f`, including shell syntax and shellcheck.
A final added health-cache regression and retention of the previous Mentor source
snapshot are included in the final deployment gate recorded below.
No manual Telegram test messages or paid model canaries are part of verification.

## Observed limits

Latency improvement in live model calls needs new production observations; offline
regressions prove the new bounds and routing behavior, not provider speed. Text can
be answered before a preceding voice transcript completes. Unqualified late journal
content still requires journal wording or an explicit reply to its reminder card.
Research and direct Google Calendar credentials remain unconfigured; existing .ics
export stays available. No external key escrow or provider billing reconciliation is
claimed. No historical failed jobs, Mentor candidates or campaigns are replayed.


## First deployment and retention correction

Release `7995fad`, build `fc768547dbeb`, was installed October 7 at 14:33:37 UTC;
manifest `1a776d81538f7700690473a1` verified at 14:34:32 UTC. The exact deployment
full gate passed 1,683 cases (nine unchanged stage skips) in 87.638 seconds.
Runtime/source/worker/spool/SQLite checks passed. The protected installer backup is
`/root/codex-hardening-backups/20261007T143314Z-tg-ingest-agent` (database 0600),
including the old Mentor source. All prior 91 messages, three files, 67 journal
entries, 1,219 conversation turns, 108 reminders, 11 Mentor cycles and 20,632 usage
rows matched verbatim afterward. One fleet deployment receipt was recorded sent.

The first monthly digest was delivered at 14:35:30 UTC. The first daily local
snapshot completed October 7 while the off-box anchor remained October 3.
Post-deployment archive inventory then found an error in weekly pin selection:
two manually named archives consumed slots, permitting rotation of the oldest
scheduled local recovery point (`ingest-20260822T000428Z.db.gz` and its encrypted
companion). Current application data and all checked historical database rows are
preserved; the August 22 job records successful encrypted fleet delivery. No exact
local duplicate was found in the scoped VPS search. The exact encrypted file was
subsequently found in the owner's Telegram downloads and restored after verification.

The correction filters weekly pins by the module's exact automated filename pattern
and off-box date anchor, preserves valid existing pins, and adopts older automated
points still on disk. Regression coverage includes manual copies, newer daily copies
and all seven original weekly points. The corrective release and recovery status
are recorded below.


## Exact archive recovery

The existing local Telegram download `ingest-20260822T000428Z.db.gz.enc` was uploaded
to a protected VPS recovery directory and decrypted using the existing on-host key.
The gzip size matched the original job's 7,093,517 bytes; expansion was 35,323,904
bytes, all 46 tables passed integrity checking, and latest archived usage was
August 21 at 23:36:49 UTC. Both the gzip archive and original encrypted companion
were restored as `tg-ingest:tg-ingest`, mode 0600. The live database was not replaced.

Encrypted SHA256: `94ecb6ea1da361b212278fb505bcfd9674e5be4bb4d789445fa82c4ca1d591a1`.
Decrypted gzip SHA256: `21136e837cbb8d45758924ee20cbb25a69d6032b35beaa2b0e8a96a36f83df68`.
Expanded database SHA256: `59987462b11622772b78c25a40daaad9eceeb438facd74b978596a1626178e36`.
Protected recovery evidence: `/root/cara-retention-recovery-20261007-144140/receipt.json`.
The expanded scratch database was removed after validation; original local download
and protected recovery source archives remain preserved. The temporary request for
an archive location was resolved by this discovery; no owner input is outstanding.


## Accepted corrective release

Running source: `36f20752ef9a75cf759dc3aeca115983bce8513e` (clean working tree).
Build: `9d041a123f25`. Deployment receipt: `0f839bc1e8c673d4a6f196df`.
Installed: `2026-10-07T14:44:24.992822+00:00`.
Verified: `2026-10-07T14:44:51.729681+00:00`.
Protected installer backup: `/root/codex-hardening-backups/20261007T144410Z-tg-ingest-agent`.

The exact full deployment gate passed 1,684 cases, nine unchanged stage skips,
in 92.166 seconds. Live runtime, worker isolation, spool canary, immutable source,
deployment manifest, SQLite integrity and foreign keys passed. All prior content,
conversation, reminders, Mentor cycles and usage rows matched the second protected
snapshot; the initial snapshot comparison remains above. No app database restore,
schema migration, profile change or history rewrite occurred.

After verification, an atomic metadata-only correction replaced the faulty weekly
pin list using the installed verified selector. All seven August 22–October 3
weekly gzip archives and encrypted companions were present. Its previous JSON value
was preserved in `kv` audit key `backup_weekly_pins_repair:0f839bc1e8c673d4a6f196df`
and `/root/cara-retention-recovery-20261007-144140/pins-repair.json`.
Prior-value SHA256: `309fcc87d00dcf258ce6d0f45a4b0be016be298b17ca23f0d14d67a6c168fec7`.
The October 7 local stamp, October 3 off-box anchor and October monthly digest
receipt stayed unchanged across the corrective restart; the digest was not resent.

Final service checks show Cara and its worker active/enabled, both Mentor units
inactive/disabled, Whisper and Nikki healthy with their original PIDs, and both
Daily Lingo containers healthy. Final checked source bytes match this release.
Live provider latency improvement remains unmeasured; new production observations
are needed to quantify it. Verification itself used no paid model canary or manual
Telegram test send. GitHub Actions produced no workflow-run record when queried;
the executed local and on-VPS gates above are the available verification evidence.

Final documentation checks passed 43 focused hygiene/operator regressions at
`/tmp/cara-fix-verify-20261007-97c92269a1`. A final read-only inspection confirmed
both actual deployment receipts sent once, the corrective receipt at
`2026-10-07T14:45:47.654914+00:00`; the recovered archive hashes matched and all
seven weekly archive pairs remained mode 0600. Source, service, SQLite and
protected-snapshot history checks remained successful.
