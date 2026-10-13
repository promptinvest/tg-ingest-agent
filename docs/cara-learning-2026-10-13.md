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
