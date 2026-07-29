# Cara Chief-of-Staff Upgrade

Status: approved for implementation  
Decision date: 2026-07-29

This is the decision-complete implementation specification for evolving Cara
from a collection of single-step skills into an intelligent, self-improving
chief of staff. `CARA.md` and `SOLUTION.md` remain the maintained product and
architecture sources of truth; this document owns the staged delivery plan
until every phase is shipped.

## 1. Locked product decisions

- Cara remains owner-only, Telegram-first, bilingual, and the same human
  character. Nikki remains the companion; this project stays focused on
  assistance.
- The first release is provider-neutral. It builds planning, research over
  supplied sources and Cara's own knowledge, durable tasks, drafts, and
  follow-through before Gmail/Outlook/Drive/Notion integrations.
- Existing direct skills remain the fast path for one-step requests. Compound
  requests stop returning “one at a time” and instead become a durable plan.
- Risk-tiered permissions:
  - local/external reads and draft preparation may run automatically;
  - persistent state changes and external writes require a rendered preview
    and explicit approval;
  - destructive actions retain typed-phrase confirmation;
  - financial transactions, credential changes, host administration, arbitrary
    shell, and code deployment are outside the runtime authority.
- Self-improvement is propose-and-approve. Cara may collect evidence, run
  evaluations, and prepare a change proposal. She may not silently change a
  prompt, threshold, permission, skill, source file, service, or deployment.
- Execution stays on the Cara VPS, but privileged or dependency-heavy work runs
  in a separate local worker service and OS account. The Telegram poll process
  never receives browser, shell, container-daemon, or connector credentials.

## 2. Target architecture

```text
Telegram update
  -> existing owner gate + durable inbox + closed router
      -> existing direct skill (single-step)
      -> task_start (compound/open-ended work)
           -> planner: strict TaskPlan JSON
           -> plan validator + permission manifest
           -> assistant_tasks / task_steps
           -> task runner
                -> in-process safe tools (notes, reminders, supplied URL fetch)
                -> local worker spool (sandboxed research/code tools)
                -> approval pause for every write
           -> grounded result + receipts + artifact

task traces + corrections + explicit ratings + failures
  -> evaluator
  -> improvement_proposals (evidence + replay results + risks)
  -> boss review/accept/reject
  -> accepted engineering proposal, never an automatic release
```

The planner is not an open-ended agent. It emits a bounded plan whose every
operation names a tool in the closed registry. The broker validates the tool,
input schema, risk, permission, dependency state, budget, and idempotency key
before execution. Model output never becomes executable code or a shell
command.

### 2.1 New modules

- `tasking.py`: plan/step validation, task lifecycle, rendering, and state
  transitions.
- `tool_broker.py`: closed `ToolSpec` registry, input validation, risk gate,
  invocation, result receipts, and redaction.
- `task_runner.py`: durable task-step scheduler and registered job handler.
- `tasks_svc.py`: Telegram handlers for create/list/show/cancel/resume and
  approval review.
- `improvement.py`: feedback signals, evaluation cases/runs, proposal
  generation, review, and export.
- `worker_client.py`: bounded local spool protocol. No arbitrary command API.
- `cara_worker.py`: separate service entry point; accepts only registry-known
  worker tool ids and runs each job under a sandbox profile.

`Agent` gains `TasksMixin`; this is a specialized skill subsystem behind the
router, not another agent/persona. The worker is a separate process solely for
fault and privilege isolation.

### 2.2 Public internal contracts

`TaskPlan`:

```json
{
  "objective": "short user-grounded objective",
  "deliverable": "answer|brief|comparison|checklist|draft",
  "steps": [
    {
      "key": "s1",
      "tool": "knowledge.search",
      "input": {},
      "depends_on": [],
      "purpose": "why this step is necessary"
    }
  ]
}
```

Rules: 1–8 steps, unique keys, dependencies refer only to prior steps, no
cycles, bounded strings/arrays, and tool inputs must pass that tool's explicit
validator. The planner may copy URLs and identifiers from the boss's request or
prior tool receipts; it may not invent credentials, local paths, note numbers,
recipients, or write targets.

`ToolSpec`:

```text
id, title, risk, execution_site, input_validator, output_limit,
uses_llm, external_network, writes_state, destructive,
requires_confirmation, allowed_proactive, timeout_seconds
```

Risk values remain compatible with `skill_manifest`: `read_only`,
`network_read`, `draft_write`, `state_write`, `external_write`, and
`destructive`.

`ToolReceipt`:

```json
{
  "tool": "knowledge.search",
  "status": "ok|partial|failed|cancelled",
  "summary": "bounded, secret-scrubbed result",
  "evidence": [{"source": "note:#12", "label": "…"}],
  "artifact_id": null,
  "effect_id": null
}
```

Only a successful receipt may support “done/saved/sent/changed” language.
Receipts from write tools include the stable external/local effect id returned
by the real system.

### 2.3 Initial neutral tool registry

- `knowledge.search` — read-only semantic/keyword search over confirmed and
  explicitly reachable archived notes; returns stable note citations.
- `knowledge.read` — read one real note by stable number.
- `reminders.read` — read active reminder state.
- `source.fetch` — SSRF-guarded read of an HTTP(S) URL supplied by the boss or
  returned in an earlier trusted receipt; does not ingest it.
- `research.synthesize` — grounded synthesis over prior receipts; every factual
  claim cites a receipt source, and missing evidence is disclosed.
- `artifact.markdown` — create a bounded draft artifact inside Cara's managed
  review directory; sending it to the boss is allowed as the requested
  deliverable, but no third party receives it.
- `reminder.propose` — render a reminder write preview and pause. The existing
  confirmed reminder creation path performs the write after approval.

No general web search provider is required in the first release. The registry
reserves `web.search`, but it remains unavailable until a provider adapter is
configured and independently reviewed. A missing tool is reported as a
capability gap, never simulated.

## 3. Persistence and lifecycle

Additive SQLite tables:

- `assistant_tasks`: owner chat, objective, deliverable, status
  (`planned|running|waiting_approval|blocked|completed|failed|cancelled`),
  source update, plan version, timestamps, final summary/artifact, trace.
- `assistant_task_steps`: task, ordered key, tool, risk, sanitized input JSON,
  dependencies, status, attempts, idempotency key, approval id, receipt id,
  timestamps, error.
- `tool_receipts`: task/step/tool, status, bounded summary/evidence JSON,
  artifact/effect ids, trace, timestamps. No secrets or unbounded response
  bodies.
- `task_approvals`: task/step, immutable preview JSON + preview hash, decision
  (`pending|approved|rejected|expired`), decision source/message and timestamps.
  Approval is valid only while the step input still hashes to the preview.
- `task_artifacts`: task, kind, safe filename, local path beneath the managed
  artifacts root, size/hash, created time, delivered time.
- `task_feedback`: explicit boss rating/correction plus derived outcome signals;
  source update/trace and timestamps.
- `evaluation_cases`: named, versioned, redacted input, expected invariants,
  source (`golden|incident|task_feedback`), active flag.
- `evaluation_runs`: candidate/baseline version, case, scores, invariant
  failures, model/cost/latency, timestamps.
- `improvement_proposals`: kind (`prompt|routing|tool|bug|policy|model`),
  evidence ids, hypothesis, proposed change, risk, baseline/candidate metrics,
  status (`draft|ready|accepted|rejected|implemented`), decision timestamps.

Tasks and approvals are durable product state and are not telemetry-pruned.
Evaluation runs and tool receipts may be pruned after the normal telemetry
window only after their aggregate proposal evidence is retained. Artifacts
follow the existing review-file retention policy unless pinned by an active
task or proposal.

Lifecycle:

1. `task_start` persists the boss's exact objective, obtains strict JSON from
   the planner profile, validates it, and stores the plan atomically.
2. Read/draft-only plans queue immediately. A plan containing a write is still
   stored, but execution pauses at that step—not at harmless earlier research.
3. `task_runner` claims one ready step, revalidates its inputs and permission,
   executes once under an idempotency key, writes the receipt, and unlocks
   dependants.
4. A write step creates an immutable preview and approval row. Approval
   rechecks the preview hash and current target state before invoking the real
   skill; drift causes a fresh preview, never a stale write.
5. Failure retries only errors declared transient by the tool, with backoff.
   Ambiguous/missing evidence blocks and asks; permanent failures end the step
   honestly. Cancellation prevents new claims and asks the worker to cancel any
   unstarted/running step best-effort.
6. Completion renders a concise answer with citations and attaches any real
   artifact. Partial completion names exactly what succeeded and what did not.

## 4. Self-improvement loop

Signals:

- explicit rating/correction tied to a task or reply;
- task completion, cancellation, retry, blocked reason and elapsed time;
- tool receipt failure/partial status;
- repeated issue-pattern recurrence;
- action-truth/ungrounded-number/artifact guard trips;
- follow-up rephrasing of the same objective within a short window;
- model cost, latency and fallback outcome.

The evaluator turns selected real incidents into redacted invariant cases. Core
invariants are deterministic:

- no unknown action/tool;
- no write without a matching approved preview;
- no stale approval after target/input drift;
- no ungrounded factual or effect claim;
- no secret in plan, receipt, artifact, trace, or proposal;
- forwarded/saved content remains data, not instructions;
- confirmed memory outranks inferred memory;
- budget and owner gates cannot be bypassed.

The improvement job may propose a change only when it names evidence, a
reproducible failure, the narrowest affected component, risk, rollback, and
baseline/candidate evaluation results. A proposal is `ready` only when it
passes every safety invariant and improves its target metric without exceeding
the configured cost/latency regression ceilings.

Reviewing/accepting a proposal changes only its workflow status. Runtime code
and prompts do not load proposal text. Implementation remains an engineering
change through the normal tests, deploy, verification, specs, commit, and push
workflow. This keeps “self-improving” real and auditable without creating a
self-modifying production service.

## 5. Local worker on the Cara host

The worker ships after the in-process registry and task lifecycle are stable.

- Dedicated `cara-worker` user, service, state directory and Unix-domain spool;
  no TCP listener and no Telegram/DO/backup/connector secrets.
- The Cara service may submit/read/cancel only through the spool directories
  shared by a narrow group; filenames are generated ids, payloads are strict
  JSON, and atomic rename publishes jobs/results.
- Worker accepts only compiled-in tool ids. It has no generic
  `command`/`argv`/`script` field.
- Every job gets a fresh scratch directory, size/time/CPU/memory/process limits,
  no host path mounts, no device access and network disabled by default.
- Browser/search tools, when later enabled, use a dedicated sandbox profile and
  SSRF/metadata/private-range egress denial. Authenticated account operations
  belong to future API connectors, not browser session replay.
- Worker result files are hashed and bounded before the Cara process accepts
  them. Unknown files, symlinks, path traversal and oversize output fail closed.
- `tg-ingest-agent` remains healthy if the worker is absent; affected task steps
  become blocked and the boss gets one actionable notice.

## 6. Delivery phases and acceptance gates

### Phase A — durable tasks and plans

- Add schema/helpers, planner profile, task actions, list/detail/cancel/resume,
  strict plan validator, direct-skill fast path, and task runner.
- Replace the `multi_action` decline with `task_start`.
- Gate: deterministic tests for schema migration, plan validation, dependency
  order, crash reclaim, idempotency, cancellation and truthful partial results.

### Phase B — neutral tools and approvals

- Add the initial registry, knowledge/supplied-URL/synthesis/artifact tools,
  approval previews and reminder proposal bridge.
- Gate: every registry tool has a manifest policy and tests; reads never write
  domain state; writes cannot execute without a current approval; prompt
  injection and SSRF suites stay green.

### Phase C — evaluation and improvement proposals

- Add task feedback, invariant cases, baseline/candidate runs, proposal
  creation/review/export and a weekly low-priority analysis job.
- Gate: proposal text cannot enter runtime prompts or policies; unsafe or
  regressing candidates never become ready; every claim links to real evidence.

### Phase D — isolated local worker

- Add spool client/worker, service/user/install/rollback wiring and one harmless
  sandbox smoke tool (`worker.echo`) to prove transport, limits, cancellation,
  result hashing and outage behavior. Do not add arbitrary shell/browser.
- Gate: no public listener; service hardening verified; worker compromise
  cannot read Cara/Nikki secrets or databases; stopping worker does not stop
  Telegram/reminders.

### Phase E — chief-of-staff experience

- Add task status cards, concise progress only on request, morning-brief open
  loops, overdue task follow-up, and review metrics focused on completion/use.
- Gate: at most one non-urgent proactive task nudge per local day; no progress
  spam; all status/action statements derive from rows/receipts.

## 7. Release-level success criteria

- Zero unauthorized state/external writes across unit, integration, replay and
  live smoke tests.
- 100% of supported compound golden requests produce a valid bounded plan or an
  honest targeted clarification; none fall to a fabricated free-form promise.
- 100% of completion/effect statements carry a successful real receipt.
- At least 90% of the supported neutral end-to-end scenarios complete without
  operator repair; the remainder fail or clarify honestly.
- A crash/restart at every task boundary yields at-most-once external effects
  and resumable internal work through idempotency and receipts.
- Existing single-step skills, reminders, ingest, memory, budgets, owner gate,
  backup, and persona tests remain behaviorally unchanged.
- No new public listener. Cara and Nikki remain independently restartable.
- Full VPS test gate passes, deployment verifies both services and SQLite
  integrity/FKs, specs and host KB are updated, then commits are pushed.

