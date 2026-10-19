# Cara reminder correctness - October 19, 2026

Owner requested implementing fixes for the three defects confirmed in the code and
live-behavior review. This is a focused extension of the existing reminder module.

## Scope

- Bind delivered overdue nudges to the exact visible reminder targets. Explicit
  replies override unrelated snooze context; no binding is created on failed sends.
- Bind draft buttons to their delivered card revision; reject old buttons and a
  move target other than the twin offered on that card. Preserve confirmation TTL,
  restart persistence and ordinary text confirmation.
- Reject unsupported repetition, intervals and repeat end conditions before any
  state change, including model output and supported pending-draft entry paths.
  Preserve one-time/daily/weekly creation and the time-only reschedule contract.
- Correct stale current-release pointers in both specifications.

No schema/service/model/budget change, retroactive reminder repair, task replay,
learning experiment or manual Telegram test message is included. Existing notes,
journals, reminders, event history, conversation, spend, backup pins and notification
receipts remain preserved. The broader ADR-0024 table and inverse-action work remain
open; this release implements its relevant reminder-boundary safeguards only.

## Evidence and verification

The review baseline matched all 59 installed Python files, source `7de363c`, build
`05f8e80d1687`, receipt `387cd0c8d238f0b087484eaa`. The unchanged October 19 shuffled
Linux suite passed 1,744 tests, nine skips, in 82.758 seconds. Two failures were
confirmed in October 15 live update/trace/history records, and three isolated
correct-behavior checks failed on that source. Original evidence is retained under
`/dev/shm/cara-review-20261019-525ad339` and its `/tmp` review logs.

Final source passed 81 focused cases in 3.405 seconds and the full shuffled Linux
suite: 1,763 tests, nine unchanged skips, in 90.470 seconds. Python compilation,
all shell syntax and patch whitespace checks pass. All tests ran with networking
disabled and scratch databases on tmpfs. The final source archive is
`479ebe4974ba28f8db75fd2495d5b2af1e6d34aed3820ea55d77b18ab6039c68`.
Final logs: `/tmp/cara-reminder-fixes-20261019-attempt5-{focused,full}.log`.

Earlier evidence remains intact: new tests fail on the original source; attempt1
had one new-fixture failure because its one-letter title cannot form a twin;
attempt2 did not unpack after an upload failure; attempt3 passed 80 focused and
1,762 full-suite cases; attempt4's added source-omission case caught a missing
entry-module import, corrected before the final gates. No production transition
occurred during these runs. Documentation additions after verification do not
change the verified runtime/test bytes.

## Accepted deployment

Published code `bc67c89b036dc1e6977dd0906f3dfb66741c7e89`, build `c605cb100b37`,
receipt `3d54e432e890e1b38f779a41`. Accepted 2026-10-19 08:46:21 UTC / 11:46:21
Moscow. The single-use controller ran in its recorded 08:43:03-09:43:03 UTC window,
under the owner implementation request and standing implementation-includes-
deployment rule supplied in this session; no separate hash-specific approval is
claimed. Controller SHA256:
`655f75bf56b3c66748161e69e5b24d48601ab8f20f559cbc566172eded09b051`.
Published archive SHA256:
`03fb3f878eacf159b1fa1de31af2010ab6a760b909946639bf1899ceadfb8d06`.

The original full deployment gate passed 1,763 tests, nine unchanged skips, in
86.982 seconds. Fresh preflight, a stopped SQLite backup/parity check, the original
installer and live runtime/worker/spool/SQLite gates passed. All 59 installed Python
files match the selected source. Schema and every original row in 43 retained
application/history tables are preserved, including 99 stored message records, 116 reminders,
554 reminder events, 75 journal entries, 1,365 conversation turns, 241 issues,
111 profile items and 22,668 original usage records. Mutable scheduler/trace/cache
tables continue their normal lifecycle; their historical contents are not claimed
byte-identical. Protected backup/digest/learning anchors and preferences are exact.

All 23 original recovery files and their modes, all three protected environment
files, speech PID and both healthy Daily Lingo container identities are unchanged.
Cara/worker are active with zero unexpected restarts; Mentor/runner stay inactive
and disabled. No package was installed or upgraded. The normal fleet deployment
receipt was delivered once, on one recorded attempt; no manual test message or
historical notice was sent. There is no uninterrupted-uptime claim.

Protected evidence root: `/root/cara-reminder-safety-20261019`.
Its `before.db` and `before-source/` preserve the stopped pre-install state.
Original installer backup: `/root/codex-hardening-backups/20261019T084458Z-tg-ingest-agent`.
Acceptance SHA256:
`2ea1db5ae0a20ffaf3f993c2d8b37084962efee1d63d71dece527b115686f3d1`.
Never replay the completed controller or prior completed deployment receipts.
