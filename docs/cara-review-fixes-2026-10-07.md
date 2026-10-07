# Cara review fixes ? October 7, 2026

Owner instruction: ?fix all & deploy?, following the code and live-performance review.
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
