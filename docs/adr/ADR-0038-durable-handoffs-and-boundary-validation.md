# ADR-0038: Durable handoffs and boundary validation

- Status: Accepted; implementation and fresh release verification in progress
- Date: 2026-11-03
- Selection: owner approved the architecture-review proposal, including implementation and deployment: "OK, go with your proposal (you can implement & deploy)".
- Baseline: accepted ADR-0028 runtime source `125aaefe`, documentation/main `c7862918`.

## Context

The current-source review found deferred voice updates without a running retry
owner, expired fleet cards intercepting ordinary confirmations, scheduled sends
receiving new identities after ambiguous delivery, knowledge limits consumed by
unconfirmed suggestions, and unchecked provider usage counters. Voice download
also remained on the SQLite/poll owner before transcription was delegated.

## Decision

Extend the existing modules and release workflow. Keep SQLite on its owner and
use one admitted serial voice transport with durable overflow. Commit consumed
transcript/accounting handoffs together, retain results through SQLite outages,
and retry cached routing within existing update caps. Paid dispatch uncertainty
must never authorize automatic retranscription. Downloads receive captured plain
inputs; no Agent or connection crosses the worker boundary.

Ignore elapsed fleet approvals only for unquoted confirmations; preserve exact
old-card responses, source/expiry checks and live-card ambiguity guards. Give
scheduled occurrences stable per-recipient delivery identities. Preserve
unknown outcomes, bounded known-unsent retries, announcement cadence and policy;
later observed health episodes receive new identities. Grouped model state
settles atomically after acknowledged transport.

Apply confirmed-note eligibility before knowledge result limits, with general
browse behavior unchanged. Validate provider envelopes and strict nonnegative
bounded integer token counters before settlement. Invalid usage retains the
unknown reservation and a typed error; missing/null/zero usage keeps existing
conservative estimation. No historical accounting or prices change.

## Compatibility and boundaries

Preserve models, prompts, budget limits, schema, stored data/history, current
notification preferences, worker/fleet privilege boundaries and disabled Mentor
units. No web-search activation, response-byte-cap project, backup-policy/key
custody change, paid qualification or protected restore is included. ADR-0022
remains postponed. Existing failed and consumed operators remain immutable;
deployment uses fresh identities, exact-source verification, all existing gates
and independent closure.

## Verification

New regressions were first run against unchanged source in isolated Linux:
red1 source `8b6b4f49`, actual test return code 1, 38 cases. The expected defect
assertions and malformed-response errors are retained in private stage evidence.
Original test/support files and assertions remain unchanged. Focused, full
normal/shuffled, static, installed-layout, operator and native release results
will be recorded separately; this ADR does not claim deployment acceptance.
