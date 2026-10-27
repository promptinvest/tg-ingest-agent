# Cara functional review — October 27, 2026

Status: fixes implemented in an isolated source stage; exact-source Linux,
model-response qualification and guarded deployment remain required.

The owner requested a current functional review and fixes for bugs found.
This continues the observed false error reply to “А сейчас?” after the earlier
installed-layout repair. That update completed successfully; its generated
reply repeated an older failure notice. The prior release and all failure
evidence remain intact.

## Confirmed defects and compatible corrections

| Area | Reproduced outcome | Correction |
| --- | --- | --- |
| Conversation | Old operational notices replayed as ordinary assistant turns; a successful request received an invented current failure. | Persist native notice provenance through the outbox; fence historical notices as data; preserve raw history and dated recall. Check initial and repaired current-status claims with the existing bounded correction path. |
| Clarification | A useful router question was discarded before warm conversation. | Carry the bounded candidate as unverified context through the existing guarded conversation path, restoring per-turn context afterward. |
| Media capture | Old buttons or explicit replies confirmed or discarded a newer staged card. | Bind new confirmations to their actually delivered card; retain the current staged set when an older card is answered. |
| Reminder undo | A missing explicit number/title fell back to an unrelated uniquely moved reminder. | Preserve the explicit target; use the unique fallback only for a genuinely unspecified target. |
| Notes | Old confirmations overwrote changed summaries, deleted reused SQLite row IDs, or archived a partially replaced batch. | Pin identity/content versions for new offers and recheck the entire batch before mutation; bind explicit replies to the delivered question. |
| Edited source notes | An older edit button applied or discarded newer staged text, including a different offer for the same row. | Bind private staged text to the delivered card and saved-note version; reject stale or undelivered new offers and invalid callback decisions. Independent offers for different notes remain answerable. |
| Voice | Pre-submission failures leaked files/reservations; definitely unsent or completed noisy transcription retained incorrect unknown usage; cleanup failure retained completed jobs. | Settle only proven outcomes, retain unknown submitted usage, and complete cleanup/job removal on the relevant failure paths. |
| Task delivery | An artifact was marked delivered before a valid Telegram acknowledgment or committed final-task state. | Validate the positive integer message ID, then atomically bind task and artifact delivery. Ambiguous sends remain terminal for automatic resend. |
| Task synthesis | One profile invocation could make several native calls while counting once and checking task cost afterward. | Guard each native reservation before transport with the existing task limits; bind exact usage rows, reconcile known cost, and refuse automatic replacement of unknown paid outcomes. |
| Issue reporting | New processing-status guard outcomes needed the existing actionable/reporting lifecycle. | Add translated diagnostic categories; completed rewrites remain history rather than new unresolved monthly problems. |

## Compatibility and scope

No provider, model, profile, price, budget, schema, scheduling, retention or alert
preference is changed. Existing data/history and legacy pending shapes remain
readable. New version/card metadata applies to newly issued offers; older offers
cannot acquire provenance retrospectively and keep their original expiry.
Operational history is retained, including legacy notices whose native origin
cannot be proved. Owner quotations and forwarded data retain their own origins.

The review includes conversation/memory, notes/media, reminders/calendar,
journals, confirmation/purge, tasks/analysis/fleet, voice and inference accounting.
Existing calendar, journal and typed-purge checks did not expose another confirmed
defect. This records tested behavior, not a guarantee that every possible defect
has been eliminated.

## Verification and release requirements

Meaningful failing reproductions precede the fixes. Existing outcome assertions
remain; the media callback fixture uses its own fake transport's delivered ID.
The original completed-handling fixture explicitly seeds its original three
records; a separate registry-wide test verifies every current handled category
and exact unchanged historical rows. Every original assertion is preserved.
The first candidate's CI failure and full verification evidence remain; its
production bytes are unchanged by these fixture corrections. Run the full
normal/shuffled Linux gates and the exact installed `agent.py` artifact gate.
Model qualification uses synthetic state and fake Telegram, the native profiles
and meter, a selected USD0.25/max20-native-call ceiling, and unchanged native
daily/monthly/task limits. Stop if any bound or outcome check fails.

Freeze the exact canonical source/archive, CI and qualification receipts before
the guarded operator transition. Preserve backups, stopped 56-table parity,
protected artifacts, readiness checks and source-bound monitor/reverter evidence.
The fifth trial segment keeps the original October28 end. No manual message,
production-update replay, data restore or model-only revert is part of this work.
