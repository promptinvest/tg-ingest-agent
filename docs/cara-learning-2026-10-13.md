# Cara procedural learning — October 13, 2026

Owner requested self-improvement, reviewed the Hermes comparison, then said "go".
This first release implements the approved procedural-learning scope.

The new learning module captures exact supported owner corrections and input-free
recipes from fully successful, delivered tasks. It retrieves relevant lessons for
conversation, routing and planning, stores source-bound immutable versions and
supports /learning and /learning undo N vV. Existing owner guidance also reaches
matching router/planner requests without changing historical profile rows.
Learning makes no additional model calls and cannot execute tools or deploy code.
The monthly digest and retired disabled Mentor services are preserved.

## Verification

Final Python source passed 25 focused learning cases and the full shuffled Linux
suite: 1,743 tests in 105.336 seconds, nine unchanged checkout-dependent skips.
Run: /tmp/cara-learning-20261013-final-evidence; fixtures/source in a separate
tmpfs checkout, with network disabled. Python compilation and all shell syntax passed.
The initial absence of ShellCheck (exit 127) is retained. A digest-verified official
portable ShellCheck 0.11.0 exposed an existing installer word-splitting warning.
The compile invocation now uses an array preserving the same module paths.
Final ShellCheck and installer checks follow that correction.

Earlier verification records remain: initial new fixtures misused the normalized
plan contract; corrected to re-resolve and validate stored plans through the existing
task contract. A stale task snapshot was caught and corrected without weakening
validators. The intermediate full run passed 1,736 tests with nine unchanged skips
in 595.795 seconds. Those completed test drivers are not replayed.

## Scope and limits

All existing router executors, confirmation/callback semantics, reminders, journals,
notes, backup policy, models, budgets and spend history remain. Four additive learning
tables and one startup marker are introduced. Old completed tasks are not learned
retroactively; old Mentor jobs and notices are not replayed. Purge-all discloses
and deletes derived learning, leaving independent spend/preferences intact.
Practical model-quality improvement remains unmeasured; structural/provenance tests
do not establish live improvement. General executable skill creation and automatic
source-code fixes are later work.

Deployment and exact accepted source/build/receipt are recorded below once verified.

## Accepted deployment

Source `7de363c`, build `05f8e80d1687`, receipt `387cd0c8d238f0b087484eaa`.
Installed 2026-10-13 07:49:12 UTC; verified 07:49:45 UTC; postcheck passed
07:52:15 UTC. The exact deployment gate passed 1,743 tests, nine unchanged skips,
in 83.909 seconds. Portable ShellCheck and all shell/Python syntax passed after
the installer quoting correction; the original warning and missing-tool result remain.

All 59 installed source files match the committed package. Cara/worker are
active/enabled with zero unexpected restarts; retained Mentor/runner are
inactive/disabled. SQLite integrity/foreign keys pass. All original rows in 44
application/history tables match the installer snapshot, including notes, reminders,
journals, conversation, profile, task and Mentor history. Every historical usage
and notification row also matches; six ordinary post-restart health checks appended
USD0.00005932 and one deployment receipt was sent on one attempt. The environment
hash, models, budgets, weekly backup pins and original October digest receipt
remain. No direct paid learning experiment or manual learner message occurred.

Four additive learning tables and the startup marker are live. Newly learned
lessons are initially empty; relevant existing owner guidance is available to the
router/planner. Future supported corrections and delivered tasks create lessons
normally. Quality improvement remains unmeasured; no artificial teaching case was
inserted into the live database. Whisper/Nikki and both Daily Lingo containers are
healthy; the latter retained their four-day uptime. October 13 alert opt-outs remain.

Protected installer backup:
`/root/codex-hardening-backups/20261013T074857Z-tg-ingest-agent`.
Protected migration/postcheck evidence:
`/root/cara-learning-20261013-preflight2`.
Initial preflight helper mistakes (disabled systemctl exit status and sqlite3.Row
object representations) were corrected in scratch evidence; all 47 original tables
and existing settings passed the value-based copied-database upgrade comparison.
No live rollback/database restore or historical controller replay was performed.
