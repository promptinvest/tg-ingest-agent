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

Publication and deployment status will be recorded after their gates complete.
A verified candidate is not an accepted running release.
