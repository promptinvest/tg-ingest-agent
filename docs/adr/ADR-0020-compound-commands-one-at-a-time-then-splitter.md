# ADR-0020: Compound commands go back to «давай по одному», then to a deterministic splitter

- Status: Implemented 04f69a7
- Date: 2026-09-07
- Phase: Phase D — decisions the owner must make
- Source: 2026-09-07 review of architecture, code and live behavior against build e3208b8 (report kept off-repo; findings are referenced by id)

## Context

multi_action is wired to the task planner, which has no close/reschedule tool; the router's own canonical example ends in a paid deepseek-v4-pro refusal in planner prose while CARA §10, SOLUTION §3/§12 and the router comment promise one-at-a-time; the one_at_a_time template is dead code; pending_actions is a single slot per chat.

## Decision

step 0 now: dispatch multi_action to the one_at_a_time template, flip the test, remove "executed by the same durable task engine" from the router prompt and fix the chief-of-staff doc; step 1 later: router.split_compound on «, потом / и потом / ; / newline / , а» only when the right side starts with a command verb, at most 4 fragments, each routed with the shared context and executed sequentially, pausing at the first fragment that opens a card and resuming from a kv queue; fragments sharing a referent are excluded and documented as such; task_start stays for research only.

## Consequences

the documented behaviour returns in one line; the splitter waits for ADR-0024 so the single pending slot cannot be clobbered.

## Resolves

router-dispatch#1, task-runtime-mentor#3

## Depends on

ADR-0024 for step 1

## Status log

- 2026-09-07: proposed by the review; not yet discussed with the owner.
- 2026-09-07: implemented in `04f69a7` — owner decision 2026-09-07 («let's do the compound commands processing»): BOTH steps shipped. Step 0: `multi_action` dispatches to the `one_at_a_time` template (never the task planner), the router prompt no longer claims the task engine executes it, the chief-of-staff doc is corrected, the manifest policy is `read_only`. Step 1: `router.split_compound` — separators «, потом / и потом / ; / newline / , а / then», ≤4 fragments, every later fragment must open with a command verb (or an ordinal/number and one), a shared pronoun referent («…перенеси ЕГО…») excludes the split; `Agent._run_compound` runs fragments through the full dispatcher in order, pauses at the first fragment that opens a card (`compound_queue:<chat>` kv, «Сначала вот это — остальное (N) сделаю сразу после»), and `_resume_compound` continues after the message/button that answers the card (10-minute TTL, then dropped). Deviation: the splitter shipped BEFORE ADR-0024 — the pause-at-card rule is what protects the single pending slot meanwhile. `task_start` stays research-only.
