# Cara reminder conversation correction - October 7, 2026

The owner requested analysis and repair of the latest irritating exchange,
following the standing "fix & deploy what needed" instruction. Read-only chat,
reminder events and routing traces establish three linked failures:

- The requested move to tomorrow was applied, but the third-snooze warning hid
  its success and asked for a day that was already supplied.
- The fired pending was consumed by re-arming. A short day/yes reply then reached
  the router without a pending action. Its pending-only outputs were correctly
  rejected, but conversation repair contradicted the actual reminder state,
  denied Cara's capabilities and asked the owner to repeat an already complete
  command. The repeated full command also missed operational dispatch.
- Repeated absolute-day button presses wrote duplicate snooze events and
  replaced the useful previous date with the already-selected date.

The actual initial move succeeded. No owner command needs replay, and no
production reminder is manually changed as part of this repair.

## Change

The existing reminder module owns the correction; no new agent, process,
provider or skill is introduced. A future local day is confirmed without another
escalation question. Repeated same-day snoozes retain their existing optional
escalation, with the actual result first and a 30-minute `reminder_snooze_choice`
bound to the effective one-shot. Short day/close replies act on that target;
bare yes requests an alternative and cannot close or move implicitly.

Explicit stored-title/time moves resolve before inference using the existing
follow-up time grammar and reschedule handler. Exact title matches precede
unique substrings. Duplicate titles request a pinned selection. Existing
numbered, ordinal, compound and unmatched-title routes remain. Recently fired
recurring reminders still create echoes; the series anchor stays intact.

Identical absolute-date moves do not create another event or overwrite the
undo date. Repeated callbacks confirm the existing result, remove obsolete
controls and add no repeated chat warning. The repair prompt requests only
missing information and forbids capability denials or an unbound yes offer.

Existing 09:00 day defaults, time zones, day/clock parsing, subject guard,
foreign confirmation cards, owner isolation, journal semantics, destructive
confirmations, pending-only validation and action-truth guards remain.
No model, environment, budget, schema, lessons, backup, monthly digest or Mentor
settings change. Private conversation evidence stays protected on the VPS;
regressions use synthetic subjects and identifiers.

## Verification and deployment

The initial focused gate retained one new-fixture capitalization assertion
failure; the actual handler succeeded. The corrected focused/source-hygiene
gate passed 44 cases at `/tmp/cara-fix-verify-20261007-3623253c56`.
It covers the requested-day flow, the retained escalation context, bare yes,
close, repeated callbacks, echo/anchor behavior, overlapping/duplicate titles,
foreign pending cards, unrelated subjects, owner isolation and original Phase A
reminder controls. Additional compatibility cases cover unique title substrings
and preserved numbered/compound routing.

Fresh read-only preflight passed at `2026-10-07T17:59:11.606961+00:00`, with
installed source `cb639e7`, no drift, healthy Cara/worker, disabled Mentor/runner,
SQLite quick_check ok and zero foreign-key errors. All seven weekly gzip/encrypted
archive pairs are protected. October's digest receipt and schedule remain.
Protected evidence root: `/root/cara-conversation-fix-20261007-cf346a81`.
Full verification and deployment acceptance are pending at this checkpoint.

The complete networkless Linux gate then passed **1,715 tests** in **82.174
seconds** at `/tmp/cara-fix-verify-20261007-014fd657c8` (84.296 seconds including
setup); AST, shell syntax and shellcheck passed. Final review added a focused
batch-member regression so a named move retains the other fired members' pending
context; the deploy gate will verify that exact final source again.
The final focused/source-hygiene gate passed **47 cases** in **3.449 seconds**
at `/tmp/cara-fix-verify-20261007-56ad437fa1` (5.386 seconds including setup).
