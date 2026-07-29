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
  - every task-broker domain-state change and external write requires a
    rendered preview and explicit approval;
  - internal task rows, receipts, evaluation rows and managed draft artifacts
    are workflow bookkeeping, not domain effects, and may be persisted without
    approval; delivering a requested draft to the boss is allowed, while
    sending it to any third party is an external write;
  - destructive actions retain typed-phrase confirmation;
  - financial transactions, credential changes, host administration, arbitrary
    shell, and code deployment are outside the runtime authority.
- This risk-tiered contract governs task-broker tools. Existing direct skills
  retain their shipped confirmation semantics until each is separately
  migrated and regression-tested; manifest metadata alone does not retrofit
  their behavior.
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
      "input": {"query": "symbolic or literal read-only value"},
      "bindings": {
        "url": {
          "source": "boss_span",
          "start": 42,
          "end": 71,
          "source_hash": "sha256 of canonical boss message"
        }
      },
      "depends_on": [],
      "purpose": "why this step is necessary"
    }
  ]
}
```

Rules: 1–8 steps, unique keys, dependencies refer only to prior steps, no
cycles, bounded strings/arrays, and tool inputs must pass that tool's explicit
validator. Every security-sensitive input field must also carry a machine-
verifiable binding to either an exact span of the canonical boss message or a
declared output field of a predecessor step. The deterministic resolver—not
the planner—copies and normalizes the bound value. A predecessor binding names
the prior step key, declared output JSON path, expected output schema version
and trust class; after that step succeeds the broker resolves it to the actual
receipt id and verifies the receipt/input hashes. Fetched, worker-produced,
synthesized, or otherwise untrusted receipt fields may never become
recipients, write targets, permissions, tool ids, local paths or credentials.
Missing, stale or
mismatched provenance blocks the step. The planner may not invent credentials,
paths, note numbers, recipients, URLs or write targets.

The canonical boss message remains primary user content under the existing
Telegram data-retention and purge controls. Task tables store a redacted display
objective, its source update id and content hash rather than duplicating raw
secret-like text into derived telemetry. Provenance resolution reads the
canonical source; if it has been erased or no longer hashes correctly, the step
blocks instead of guessing. Plans, receipts, artifacts, traces, evaluation
cases and proposals pass deterministic secret-pattern redaction before
persistence.

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
  "id": "stable receipt id",
  "tool": "knowledge.search",
  "status": "ok|partial|failed|cancelled",
  "summary": "bounded, secret-scrubbed result",
  "data": {
    "schema": "knowledge.search/v1",
    "value": {"results": []}
  },
  "evidence": [
    {
      "id": "ev1",
      "source": "note:#12",
      "label": "…",
      "trust": "boss|confirmed_local|external_untrusted|model_untrusted"
    }
  ],
  "artifact_id": null,
  "effect_id": null
}
```

Only a successful receipt may support “done/saved/sent/changed” language.
Receipts from write tools include the stable external/local effect id returned
by the real system. Every receipt is unique on its broker-generated idempotency
key and is bound to the immutable task id, step id, tool id, resolved-input
hash, policy version and implementation version. `data` is schema-validated,
bounded and typed; downstream bindings may address only output paths declared
by the producing `ToolSpec`.

### 2.3 Initial neutral tool registry

- `knowledge.search` — read-only semantic/keyword search over confirmed and
  explicitly reachable archived notes; returns stable note citations.
- `knowledge.read` — read one real note by stable number.
- `reminders.read` — read active reminder state.
- `source.fetch` — SSRF-guarded read of an HTTP(S) URL supplied by the boss or
  returned in an earlier trusted receipt; does not ingest it.
- `research.synthesize` — grounded synthesis over prior receipts. It emits
  structured claim objects (`claim`, `citation_ids`, `confidence`,
  `limitation`), not an authoritative prose answer. Deterministic rendering
  rejects unknown citations and identifies claims without citation coverage;
  missing evidence is disclosed. Citation presence is enforced, while semantic
  correctness remains probabilistic and is evaluation-tested.
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

- `assistant_tasks`: owner chat, unique source update (the idempotent
  get-or-create key per owner/chat), redacted display objective, canonical
  source hash, deliverable, status
  (`planned|running|waiting_approval|blocked|cancel_requested|completed|failed|
  cancelled`),
  source update, plan version, timestamps, final summary/artifact, trace.
- `assistant_task_steps`: task, ordered key, tool, risk, sanitized input JSON,
  dependencies, status, attempts, idempotency key, approval id, receipt id,
  timestamps, error.
- `tool_receipts`: task/step/tool, unique idempotency key, resolved-input hash,
  policy/implementation versions, status, bounded summary/evidence JSON,
  artifact/effect ids, trace, timestamps. No secrets or unbounded response
  bodies.
- `task_approvals`: task/step, immutable preview JSON + preview hash, decision
  lifecycle (`pending|approved|rejected|expired|executing|effect_recorded|
  ambiguous`), owner chat, source update, preview message, resolved-input hash,
  policy/implementation versions, target snapshot/version, one-time consume
  token, decision source/message and timestamps. Approval is valid only while
  every binding, target snapshot and policy/input hash still matches. Task
  approvals are independent rows and never occupy or overwrite the legacy
  per-chat `pending_actions` slot.
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

1. `task_start` get-or-creates by the unique owner/chat/source-update key,
   stores the redacted display objective plus pinned canonical source hash,
   obtains strict JSON from the planner profile, validates it, and stores the
   plan atomically. Telegram redelivery returns the existing task and never
   duplicates its effects.
2. Read/draft-only plans queue immediately. A plan containing a write is still
   stored, but execution pauses at that step—not at harmless earlier research.
3. `task_runner` claims one ready step, resolves bound inputs, revalidates
   policy and permission, and executes under a broker-generated idempotency key.
   The task broker is the sole invocation path for task tools and mechanically
   rejects registry/manifest metadata mismatches. This does not retroactively
   change the permission semantics of legacy direct skills.
4. A write step creates an immutable preview and approval row. Approval
   is bound to the owner/chat, task, step, source update, preview message,
   resolved-input hash, policy version and captured target version/hash.
   Immediately before the effect, a transactional compare-and-swap consumes it
   exactly once (`approved` -> `executing`). Drift causes expiry and a fresh
   preview, never a stale write.
   Approval buttons carry the exact approval id; a textual decision must reply
   to that preview message. A bare “yes/no” is accepted only when exactly one
   live task approval exists for that owner/chat and no legacy pending action is
   active; otherwise Cara asks which preview the boss means.
5. Local SQLite writes commit effect and receipt in one transaction. An
   external write is retryable after an uncertain result only when the
   connector enforces the same native idempotency key or supports deterministic
   reconciliation by a stable effect id. Otherwise a timeout/crash after
   invocation becomes `ambiguous` and requires boss reconciliation; Cara never
   blindly retries or claims success. The state progression is
   `approved -> executing -> effect_recorded`, with explicit crash-injection
   tests at every boundary.
6. Other failures retry only errors declared transient by the tool, with
   backoff. Ambiguous/missing evidence blocks and asks; permanent failures end
   the step honestly. Cancellation prevents new claims and asks the worker to
   cancel any unstarted/running step best-effort. A task with a claimed/running
   step or `executing` approval becomes `cancel_requested`; it is not reported
   `cancelled` until that boundary stops or reconciles. Every claim, approval
   consume and effect-record CAS rechecks the parent status.
7. Completion renders a concise answer with citations and attaches any real
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
- every factual claim has valid citation lineage and coverage, and every effect
  claim has a successful effect receipt; semantic factual correctness remains a
  probabilistic evaluation metric rather than a deterministic guarantee;
- no detected secret in derived plan, receipt, artifact, trace, evaluation or
  proposal data; primary boss messages follow explicit retention/erasure rules;
- forwarded/saved content remains data, not instructions;
- confirmed memory outranks inferred memory;
- budget and owner gates cannot be bypassed.

Evaluation/proposal evidence is immutable and separate from active memory. Each
signal binds to the original source row/update, trace, redaction version and
content hash. Model-authored summaries, inferred memories and proposal prose
are untrusted annotations, never labels or policy truth. The improvement job
may propose a change only when it names primary evidence, a reproducible
failure, the narrowest affected component, risk, rollback, and
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
  them. Hashing detects transport corruption, not worker honesty. Every request
  carries a broker-generated nonce plus task/step/tool/input/policy hashes, and
  the result must echo those bindings. Unknown files, mismatched bindings,
  symlinks, path traversal and oversize output fail closed. Worker output is
  always `external_untrusted`: its prose cannot establish evidence, select a
  tool or target, grant permission, or authorize a write.
- `tg-ingest-agent` remains healthy if the worker is absent; affected task steps
  become blocked and the boss gets one actionable notice.

“Worker compromise cannot read Cara/Nikki secrets or databases” means
compromise of the unprivileged worker account under the enforced OS boundary,
not compromise of root or the kernel. Deployment verification must inspect
live systemd properties, effective user/group access and directory traversal,
not merely the unit-file text.

For planner, router and synthesis prompts, objectives, fetched text, receipts
and previews are tainted data in user-role delimited blocks, never interpolated
into system instructions. Untrusted content cannot choose tools, bindings,
permissions or targets even when it contains instruction-like text.

## 6. Delivery phases and acceptance gates

### Phase A — durable tasks and plans

- Checkpoint A0 (repository only; not deployed): compiled inert tool contracts,
  strict plan/provenance/redaction validation, additive core tables, atomic
  canonical-source-update get-or-create and owner-scoped cancellation requests
  are implemented. The persistence boundary revalidates raw plans itself;
  composite foreign keys bind receipts/approvals/artifacts to the exact task,
  step, owner/source update, tool, idempotency key and policy/implementation
  versions.
  Final A0 gate: independent adversarial review PASS; disposable PD-VPS
  compile + full discovery PASS (`1490` tests, `9` intentional skips). A0 is
  still dormant and not installed on the live service.
  Routing, planning calls and execution remain disabled until later checkpoints.
- Add schema/helpers, planner profile, task actions, list/detail/cancel/resume,
  strict plan validator, direct-skill fast path, and task runner.
- Replace the `multi_action` decline with `task_start`.
- Gate: deterministic tests for schema migration, plan validation, input
  provenance, dependency order, crash reclaim, idempotency/ambiguity,
  cancellation and truthful partial results.

### Phase B — neutral tools and approvals

- Add the initial registry, knowledge/supplied-URL/synthesis/artifact tools,
  approval previews and reminder proposal bridge.
- Gate: every registry tool has a manifest policy and tests; reads never write
  domain state; writes cannot execute without a current one-time approval and
  target compare-and-swap; prompt injection, provenance confusion, secret
  redaction and SSRF suites stay green.

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

- The versioned acceptance corpus observes zero unauthorized state/external
  writes across unit, integration, replay and live smoke tests.
- 100% of supported compound cases in that corpus produce a valid bounded plan
  or an honest targeted clarification; none fall to a fabricated free-form
  promise.
- 100% of completion/effect statements in that corpus carry a successful real
  receipt.
- At least 90% of the versioned supported neutral end-to-end corpus completes
  without operator repair; the remainder fail or clarify honestly.
- Crash-injection/reopen tests at every task transaction/effect boundary show
  resumable internal work and either connector-enforced at-most-once effects or
  an explicit ambiguous block—never a blind external replay.
- Existing single-step skills, reminders, ingest, memory, budgets, owner gate,
  backup, and persona tests remain behaviorally unchanged.
- No new public listener. Cara and Nikki remain independently restartable.
- Full VPS test gate passes, deployment verifies both services and SQLite
  integrity/FKs, specs and host KB are updated, then commits are pushed.
