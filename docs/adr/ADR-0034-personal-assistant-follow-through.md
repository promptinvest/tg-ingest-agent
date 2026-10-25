# ADR-0034: Reliable action outcomes and personal-assistant continuity

- Status: Implemented and deployed `ee1a319`; accepted 2026-10-25 11:37:18 UTC
- Date: 2026-10-25
- Authority: owner “implement these fixes” after the current architecture review;
  primary purpose is the owner's personal AI assistant. Implementation includes
  guarded deployment under the existing standing rule.

## Decision

Extend the existing in-process skills, SQLite persistence and bounded transports.
Use source-bound local action receipts plus a delivery outbox, with reminder
effects and confirmation consumption committed together. Preserve uncertain
delivery without automatic resend, and preserve typed purge semantics.

Move slow maintenance into owner-settled sessions. Keep the single SQLite owner
and bounded worker/transport slots; yield inference waits to due reminders while
preserving the in-flight turn's target and confirmation state. Preserve all
model/provider, budget and executor boundaries.

Derive current-work context from existing valid records, prioritize recent
equal-confidence/frequency guidance, and honor current answer-format requests.
Fix fleet queue starvation and give valid accepted investigations a final status
when evidence authority changes. Existing invalid-source suppression stays.

Add an explicit independent recovery rehearsal API without claiming missing
provenance or moving a recovery key. Complete only the recovery evidence actually
available to the operator.

## Verification and release

Keep original tests/assertions and all failed evidence. Require deterministic
failure reproductions, focused and full isolated Linux gates, actual-source
operator checks, additive migration/original-row parity, exact publication and
a fresh protected deployment window/package. Prompt/context changes cannot be
described as unchanged-model-facing-source qualification.

See [release record](../cara-assistant-reliability-2026-10-25.md).

## Status log

- 2026-10-25 11:37:18 UTC: guarded acceptance completed for `ee1a319`, build
  `de3e62ab43f8`. Exact-source offline/CI/model qualification, additive migration
  and stopped original-data parity, namespace/MCP/scoped reads, restart, health
  and the native once-only notice pass. The failed first transition remains;
  separate forward completion corrects only the authenticated Daily Lingo release
  binding in one host policy, with no reinstall, migration or additional model
  qualification. The owner does not know the independent artifact/key locations;
  recovery after host loss remains unverified.
