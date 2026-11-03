# ADR-0039: Bound provider response bytes before parsing

- Status: Accepted; implementation and fresh release verification in progress
- Date: 2026-11-03
- Selection: owner requested "implement this one: **Provider response-size limits** before JSON parsing". The standing implementation rule includes verified deployment.
- Baseline: accepted ADR-0038 runtime `edf5d81e`, documentation/main `dec56e19`.

## Context

Response-envelope validation happens after the complete response body has been
read, decoded and parsed. The shared transport and loopback Whisper reader still
accepted unbounded success bodies, while HTTP diagnostics read the complete
error body before truncating its displayed text. A small displayed answer or
error therefore did not bound transport allocation.

## Decision

Extend `inference_io.py` and the existing gateway; no new skill, module, service
or setting is needed. Read at most 8 MiB plus one overflow byte in chunks of at
most 64 KiB. Accept an exact-limit body after observing EOF; reject overflow
before UTF-8 decoding or JSON parsing. Measure actual bytes without trusting
Content-Length. Apply the shared success reader to chat, embeddings, remote STT
and loopback Whisper, preserving each caller's existing decoding behavior.

Oversized success replies raise a transient `LLMError` with usage unknown and
never claim the request was unsent. Preserve conservative reservations, current
profile failover, ordinary deadlines, background admission and transport cleanup.
The byte cap covers current normal request sizes; a future configuration that
needs larger bodies must explicitly revisit the limit.

Read only a 64 KiB HTTP error prefix before existing redaction and the unchanged
500-character diagnostic display limit. Close that stream even if prefix reading
or decoding fails. Keep the actual HTTP status classification and rejection
accounting; error-prefix truncation grants no refund or new retry policy.

## Compatibility and verification

Normal below-limit responses preserve content, usage, pricing and existing
fallback behavior. Keep loopback Whisper's replacement decoding and plain-text
fallback below the limit. Oversized local replies fail directly rather than
starting another transcription. No model, prompt, budget, schema, source-recovery,
capability, notification or backup-custody policy changes are selected.

The existing response test doubles model a no-argument whole-body read. Correct
only their selected `read(size=-1)` implementations to finite byte streams;
retain every original assertion and all other test logic. Preserve byte-exact
baseline files, hash-bind each selected method and independently compare the
complete before/after AST outside those fixture methods. Production never falls
back to an unbounded read to accommodate a fake.

Exercise byte boundaries, short reads, multibyte text, incorrect/absent headers,
parse exclusion on overflow, response/HTTP-error closing, slot/background lease
release and conservative chat/embed/STT accounting using synthetic offline
responses. Run focused and full normal/shuffled Linux suites, static/helper and
installed-layout gates, exact-source CI, fresh native release checks and
independent closure before claiming deployment acceptance. All older completed
and failed controllers remain consumed and unchanged. ADR-0022 stays postponed;
independent backup-key custody and host-loss recovery remain unproved.

## Verification checkpoint

The new 18-case baseline against unchanged accepted source failed as expected
before the cap existed (29 failing subchecks and two errors). The final
19-case module and all affected application modules passed 1,618 checks in
isolated Linux with eight environment skips. The initial scratch failure is
retained: one reused fake needed a fresh stream on context entry, and the
working-copy scratch package had Windows line endings in unchanged scripts.
A fresh canonical-source package fixed packaging; all original assertions
remain. Full exact-commit verification and native acceptance follow separately.

## Status log

- 2026-11-03: owner selected provider response-size limits for implementation;
  implementation extends the existing transport and gateway with fixed byte caps.
