# ADR-0038: Durable handoffs and boundary validation

- Status: Implemented/deployed/accepted `edf5d81e`, 2026-11-03; separate audit, physical closure and production tag verified
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
All 120 original test/support files and assertions remain unchanged. Exact-source
Linux passed 224 focused checks and 3,516 full checks in each normal/shuffled
order, retaining nine skips. Source CI, static/helper and installed-layout gates
passed. The 46 new application tests cover the selected fixes.

The first native attempt remains FAILED after 16 phases at the strict current-
unknown guard. Its failed receipt, containment, process closure and ledger are
preserved. A later read-only contained check passed the unchanged strict-zero
predicate and exact historical 40-row fingerprint; it does not identify the
reservation seen by the failed check or establish a provider fault.

Fresh same-installed forward completion passed 13 phases without reinstallation,
re-marking the verified deployment or sending another notice. Its bounded
read-only settlement observation samples the complete unchanged strict validator
in fresh transactions for the same process generation, up to 30 seconds within
the selected window. Persistent unknowns and every other rejection still fail.
Canonical acceptance completed at 10:55:20.036644 UTC, separate audit at
10:55:59.330167 UTC and physical closure at 10:56:50.037273 UTC. The
[release record](../cara-adr38-release-2026-11-03.md) and
[verification summary](../cara-adr38-release-verification-2026-11-03.json)
retain the exact source, failure and completion bindings.

## Status log

- 2026-11-03: owner selected implementation and deployment of the architecture-
  review proposal; the initial 38-case Linux baseline reproduced the defects.
- 2026-11-03: source `edf5d81e` passed exact-source verification and merged through
  PR 20 as `51d95516`. The first native failure remains immutable.
- 2026-11-03: the same installed build `7c37b7fc8e7d`, deployment
  `2adbff506160eedd86df7095`, completed fresh forward acceptance, independent audit
  and physical closure. Production tag
  `production-2026-11-03-adr38-durable-handoffs` is verified at exact source
  `edf5d81e`; all 16 previous tags remain exact. ADR-0022 and backup-custody
  boundaries remain unchanged.
