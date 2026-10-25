# Cara personal-assistant reliability — October 25, 2026

## Scope and authority

Owner request: “implement these fixes”, following the current architecture/code
review and the clarification that Cara's primary purpose is a personal AI
assistant. The standing implementation-includes-deployment rule applies; no
separate hash-specific approval is claimed. ADR-0034 records the design.

The baseline checkout is a88ec30; accepted installed runtime is dcdd5ee. The
review independently confirmed the relevant installed modules match the checkout,
and reproduced reminder replay/confirmation failures, lost reply/expiry delivery,
fleet queue starvation and newest-preference exclusion in networkless scratch.
Existing focused regressions passed; reproductions are synthetic, not claims
about incidents in the owner's real data.

## Candidate implementation

Source-bound action outcomes and outbound delivery records; atomic reminder
effects/confirmation consumption; conservative uncertain-send containment;
purge-compatible new copies; responsive owner-settled maintenance and inference
heartbeat; recent-preference ordering; current-work projection and requested
answer formats; fair fleet scanning and valid-owner terminal stale statuses;
explicit independent-artifact recovery verification; truthful legacy rollback
test-receipt wording. Existing limits, policies and consent remain.

## Verification and acceptance

All earlier attempts remain preserved. The first full disk-backed run was
stopped during slow isolated fixture I/O; it did not qualify the candidate.
The next full run completed with two failures: an administrative purge preview
counted its own delivery error, and the historical restart fixture did not
distinguish a legacy send from a send with durable delivery evidence. The purge
flow now retains delivery evidence without generating that self-referential
issue. The restart fixture explicitly creates a pre-outbox send and retains
every original assertion. Separate regressions cover new uncertain sends.

Legacy reminders with no recorded delivery retain the existing recovery path.
For newly recorded sends, an interrupted send remains uncertain and is not
automatically sent again. Known undelivered replies can retry without replaying
their action. Interactive prompts requiring an immediate result or a current
confirmation context cannot be delivered later as an unbound ordinary reply.

Pending exact-source focused/full normal/shuffled Linux checks, canonical source
publication/CI, independent review, exact additive schema/original-data rehearsal,
fresh operator fixtures/prepared-input gate, protected preflight/backups,
monitor/readiness bindings and installed acceptance. No deployment is claimed.

The first published candidate `9ae0cb3` passed exact-head CI. Its root Linux
harness incorrectly assigned its temporary parent to the non-root test account,
so protected-root ancestry checks failed; the original logs are preserved.
The corrected harness uses root-owned fixtures for root gates. A subsequent
source review also found a preparation error could repeat a cost reservation.
The corrected candidate terminally contains that error and releases only an
exact provably unsent reservation. A fresh canonical full gate is required.

Candidate `d9a2e14` passed canonical compile, normal and shuffled Linux gates
(2,982 tests in each order, nine existing skips), static checks and exact-head
CI. Its finite five-case model trial cost USD0.01560504 with no unknown attempts.
Manual review rejected the current-work answer: it called the recorded step
pending while also saying the owner had just performed it. The structured reply
also added unsolicited opening/closing text. Those raw responses and charges
remain preserved; no passing model proof or application deployment is claimed.
The prompt correction explicitly separates displayed context from performed work
and makes exact requested structures exclusive. Fresh exact-source qualification
must include the prior charge within the same total USD0.15 ceiling.

Prompt/context changes are explicit. Existing sealed model evidence remains
bound to its original source and cannot be relabeled fresh candidate evidence.
No paid qualification, manual Telegram message, historical replay, facts/source
publication, recovery-key copying or historical data repair is implied.

Independent off-box recovery requires the operator's independently retained
artifact/key references. A working-copy decryption test alone does not prove
their provenance or survival after host loss.
