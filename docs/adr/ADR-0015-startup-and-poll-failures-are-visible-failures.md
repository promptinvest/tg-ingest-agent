# ADR-0015: Startup and poll failures are visible failures

- Status: Proposed
- Date: 2026-09-07
- Phase: Phase C — robustness, operations, security
- Source: 2026-09-07 review of architecture, code and live behavior against build e3208b8 (report kept off-repo; findings are referenced by id)

## Context

Restart=always with RestartSec=10 never trips the start limiter; every numeric env knob is a bare int() and the live env is never validated before a restart; June shows 18- and 29-restart loops with no alert possible; persistent getUpdates failure is log-only.

## Decision

agent.py --check-config parses the EnvironmentFile with the same reader the one-shots use and runs load_config; the installer runs it before any mutation; the unit gains StartLimitIntervalSec=600, StartLimitBurst=5 and OnFailure=cara-failed-notify@%n.service, a root one-shot that posts one fleet notice from the fleet env; consecutive poll failures for 10 min, or a 409 at once, send one poll_stalled alert and file an issue, with poll_back on recovery; terminal failure log sites emit journald warning/err priorities.

## Consequences

a bad edit stops the unit visibly instead of looping; one new unit file and sender script enter the deploy payload and the FILES guard test.

## Resolves

ops-deploy-ci#4, #9, #11

## Depends on

nothing

## Status log

- 2026-09-07: proposed by the review; not yet discussed with the owner.
