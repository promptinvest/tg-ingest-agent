# ADR-0022: The worker is dormant until it has a real tool; research is either real or absent

- Status: Proposed
- Date: 2026-09-07
- Phase: Phase D — decisions the owner must make
- Source: 2026-09-07 review of architecture, code and live behavior against build e3208b8 (report kept off-repo; findings are referenced by id)

## Context

the worker's only tool is echo, its only caller is the deploy canary, the planner still offers echo to the model, and check_assistant_tasks runs first on every loop iteration with zero tasks; research plans queue web.search with no provider and block on step 1 with the reason hidden.

## Decision

TASK_WORKER_ENABLED=false in the live env and the unit disabled, code and tests kept so re-enabling is one flip; worker-site specs are excluded from the planner manifest; either configure a Brave key, capped already at 2 queries and 0.15 USD per task, and record one real research receipt chain in CARA.md, or filter web.search out of the manifest and have the planner name "web search unavailable" as a capability gap before queuing; check_assistant_tasks moves after fire_due_reminders; task and mentor ticks back off for 60 s when nothing is open; CARA §10 and SOLUTION §12 record the task runtime as dormant with its entry points.

## Consequences

the isolation boundary is preserved on paper and re-enabled when a workload exists; the six-step research pipeline stops promising what it cannot do.

## Resolves

architecture#4, task-runtime-mentor#9, #11, #13, dialog-trends#9 in part

## Depends on

owner decision on the Brave key

## Status log

- 2026-09-07: proposed by the review; not yet discussed with the owner.
