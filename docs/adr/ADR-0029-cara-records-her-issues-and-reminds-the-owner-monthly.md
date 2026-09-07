# ADR-0029: Cara records her issues and reminds the owner of them once a month

- Status: Accepted (owner decision 2026-09-07; implementation deferred — backlog)
- Date: 2026-09-07
- Phase: Phase D — decisions the owner must make
- Source: owner decision on the Mentor question raised by ADR-0021 (2026-09-07 review)

## Context

The weekly Mentor pipeline (evidence envelope → separate `cara-mentor` inference service → proposal → candidate patch → networkless `cara-mentor-runner` → owner review) produced no usable output in seven cycles (W31–W36 plus one replay, all `failed`/`candidate_failed`; three `draft` proposals never shown). The owner does not want that machinery repaired or extended now. What he wants is simpler: Cara already keeps a durable record of what went wrong (`issues`, `issue_patterns`, the problem log he can add to with «запиши в проблемы») — she should keep recording, and once a month put the open items in front of him.

## Decision

- The Mentor cycle (proposal request, candidate construction, runner) is **not scheduled** any more; ADR-0021's repairs are not implemented. The units stay installed until this ADR is implemented; then `cara-mentor` and `cara-mentor-runner` are stopped and disabled (code and tests kept so the boundary can be revived deliberately).
- Cara keeps recording issues exactly as today (`store.issue_add`, issue patterns, `report_problem`); nothing new is invented on the recording side.
- A **monthly issue digest** to the owner replaces the Mentor: on a fixed schedule, first send on **2026-10-07**, then every month, one message in her voice listing the open issue patterns (grouped by kind, with counts and one example each, redacted like the weekly review) and the problems he reported himself, plus the count resolved since the last digest. Delivery-gated and retried like the weekly review; skipped entirely when there is nothing open. «покажи проблемы» / `issues_report` stays available on demand.
- Nothing is changed by the digest itself — it is a reminder, not a proposal; he decides what to do with the items.

## Consequences

One fewer paid weekly job and two fewer active units once implemented; the improvement loop becomes owner-driven (he reads the digest, asks for the fix he wants); the evidence-boundary code remains but dormant. Supersedes ADR-0021.

## Resolves

task-runtime-mentor#1 … #12 (by retirement rather than repair), proactive-jobs-logs#2

## Depends on

nothing (implementation deferred by owner instruction: do not start now)

## Status log

- 2026-09-07: accepted by the owner («let her record her issues and let her remind me of them once a month, starting a month from now. Don't start any implementation now, put that into the backlog»). Not implemented.
