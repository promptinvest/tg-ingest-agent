# ADR-0023: Voice transcription leaves the poll thread, with the DB staying on it

- Status: Proposed
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
