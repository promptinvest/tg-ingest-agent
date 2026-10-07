# ADR-0023: Voice transcription leaves the poll thread, with the DB staying on it

- Status: Implemented 2026-10-07
- Date: 2026-09-07
- Phase: Phase D — decisions the owner must make
- Source: 2026-09-07 review of architecture, code and live behavior against build e3208b8 (report kept off-repo; findings are referenced by id)

## Context

STT blocks the only thread about 1 min per 30 s of audio, reminders and other messages wait, and the documented «~1 min precision» is unqualified; llm.transcribe meters usage on the connection, which is check_same_thread=True.

## Decision

split _transcribe_local_server/_transcribe_local into a pure transport function with no sqlite access and a main-thread metering step; on an own voice note without a cached transcript, download, reply the ADR-0007 listening line, start a helper thread and return defer like album parts; the loop polls in-flight jobs with a 2 s poll timeout and re-enters handle_update on the main thread with the transcript; a test runs the transport with conn=None; CARA §3/§5 state the real precision; the worker is not used, its PrivateNetwork and cold model make it the wrong host.

## Consequences

one thread, no DB on it; turns sent during a transcription are processed before the voice turn they may answer, which must be documented and watched.

## Resolves

architecture#1, routing-traces#10

## Depends on

ADR-0007 first; owner decision

## Status log

- 2026-09-07: proposed by the review; not yet discussed with the owner.
- 2026-09-07: ACCEPTED by the owner («it's OK» — the trade-off that a text sent during a transcription is answered before the voice turn is acceptable). Implementation deferred to the backlog by owner instruction; not started.

- 2026-10-07: owner authorized "fix all & deploy" following the code/live review. Implemented 2026-10-07. Verification and deployment evidence: `../../docs/cara-review-fixes-2026-10-07.md`.

- 2026-10-07: shipped in reviewed release `7995fad` and corrected release `36f2075`; live build `9d041a123f25`, receipt `0f839bc1e8c673d4a6f196df`. Remaining subtasks named in the status line stay open.
