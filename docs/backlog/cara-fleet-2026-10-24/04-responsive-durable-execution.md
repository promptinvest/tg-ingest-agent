# CF-04 — Responsive durable planning, synthesis and fleet queues

- Status: Implementation authorized October 24; partial work in progress.
- Priority: P1; can proceed independently of knowledge work.
- Dependencies: none; coordinate CF-02/CF-03 contracts.
- Size: Large; split completion wakeup, asynchronous inference and queue lifecycle.

## Verified timing and sources

The review observed completed fleet results waiting for the normal long-poll cycle. The source-level reproduction uses a fake executor that completes while getUpdates is waiting; it requires no real host or production conversation. Exact production timing samples are retained only in the private operator supplement.

tg_ingest_agent.py:1007 shortens the 50-second long poll for albums/voice jobs only. check_assistant_tasks invokes fleet handling before due reminders at799-821. fleet.py:371 and 550 call model planning/synthesis synchronously; task profiles in llm.py:612-617 allow 90 seconds. SSH is separately executed, but owner-thread model I/O still blocks.

cara_fleet.py:103-133 processes investigations serially, with three concurrent hosts within each request and 55-second remote call limits. fleet.py:343 permits eight active requests;505-508 expires a submission after 240 seconds without distinguishing queue wait from execution.

## Required design

- Persist a canonical source-bound request before asynchronous planning; maintain explicit versioned planning, queued, started, completed, consumed and delivery phases.
- Move model transport/planning/synthesis I/O off the owner poller using existing inference_io/call_budget/voice patterns where appropriate. Keep SQLite and budget reservation/settlement/phase acceptance on the owner thread; never share its SQLite connection with threads.
- Bind completion to request/phase/nonce/input hash, owner source hash, inventory/facts/capability revisions and policy version. Reject stale/edited/mismatched completions.
- Use the existing short pending-work polling convention or an equivalently bounded completion wakeup; restore normal idle polling when work is finished.
- Distinguish queue deadline from execution deadline. Do not start expired/cancelled jobs; late results cannot revive terminal requests.
- Bound inference, global transport and per-host concurrency. Add fair scheduling and backpressure without increasing existing total spend/call or remote resource limits.
- Preserve paid-call reservation and conservative unknown-cost settlement after ambiguous transport. Do not automatically repeat an unknown completed paid phase.
- Preserve explicit uncertain outcomes and no automatic replay of maintenance or ambiguous message deliveries.
- Record timing by phase without putting internal IDs into ordinary replies.

## Compatibility and migration

Read legacy request/event payloads without rewriting them. Add versioned fields/states with explicit recovery rules. Preserve old done/ambiguous/card meanings, original receipts, exact source/approval bindings, ten-minute maintenance expiry and existing SSH boundaries.

The initial wakeup fix should not depend on the larger task-broker migration. Do not replace the scheduler or change unrelated voice/reminder cadence incidentally. Existing deterministic inventory answers should retain their current cheap path; arbitrary catalog interpretation may still use the LLM.

## Acceptance criteria

- With a fake planner/synthesizer blocked for 90 seconds, owner update handling and due reminders continue rather than waiting for the model.
- A ready result is consumed within one configured pending-work poll interval plus measured processing overhead; no extra normal 50-second wait.
- Two simultaneous report completions cannot serialize long model waits on the reminder/poll thread.
- Eight slow six-host fixture jobs show correct queued/started status and stay within global/per-host concurrency; waiting work does not silently expire as if it had run.
- Cancelled/expired work never starts later; late/duplicate results, wrong nonce and revision/source edits cannot deliver or spend twice.
- Crash/restart at each phase preserves budget accounting, source bindings and honest ambiguity; no repeated unknown paid operation.
- Existing voice, albums, research, notes, reminders, budget notices and exact fleet approvals remain responsive/correct.
- Use fake-clock and synchronization-based assertions; do not add flaky wall-clock sleeps as the primary proof.

## Implementation and tests

Inspect fleet.py, cara_fleet.py, tg_ingest_agent.py, voice_svc.py, inference_io.py, call_budget.py, runtime.py and task_runner.py. Extend test_fleet.py, test_task_runtime.py, test_gateway.py and appropriate assistant scheduler tests. Document deadlines, maximum concurrency and crash recovery before rollout.

Out of scope: changing models, budgets, remote host limits, alert schedules, monitoring ownership or introducing broader effect permissions.

## Tracking

Parent roadmap: [#7](https://github.com/promptinvest/tg-ingest-agent/issues/7). Blocked by: none. Blocks: [#5](https://github.com/promptinvest/tg-ingest-agent/issues/5), [#6](https://github.com/promptinvest/tg-ingest-agent/issues/6). Track the partial-independent increments described above separately; no dependency implies new runtime permission.

## Development contract

This is proposed development work recorded on October 24, 2026. The owner requested a thorough review and a backlog for further development; implementation, new capabilities and production changes are not represented as completed or authorized by creating this issue. A later implementation request follows the owner's standing implementation-includes-deployment rule within its explicit scope and all existing release gates.

Read AGENTS.md, the full CLAUDE.md, CARA.md, SOLUTION.md, docs/adr/README.md and docs/backlog/cara-fleet-2026-10-24/README.md. Canonical repository: promptinvest/tg-ingest-agent, local projects/tg-ingest-agent; projects/DLingo is another application. Preserve working notes, reminders, calendar, journals, speech, research, memory, budgets, history, backups and confirmations. The existing research worker remains networkless; fleet credentials stay in its separate executor; model output never supplies executable addresses, paths or commands.

Use the existing budgeted LLM gateway for free-form interpretation and appropriate reply format. Zero host probes for catalog questions does not mean zero model calls. Do not introduce a required exact-phrase interface, another routing stage/provider/daemon, or a generic shell tool as an incidental remedy.

Update BOTH CARA.md and SOLUTION.md, relevant ADR/backlog status and operator docs in every implementing commit. Tests run in an isolated Linux/VPS stage, not the OneDrive checkout. Preserve full required Linux/CI, installed-source/runtime, sandbox-denial, stopped-data parity, backup, monitor/alert and guarded-release checks. No manual Telegram test sends, historical update/controller replay, live maintenance demonstration, unrelated paid work or change to another project's release rules.

Full handoff, source map, sanitized production evidence, bootstrap facts and semantic regression corpus:
https://github.com/promptinvest/tg-ingest-agent/tree/main/docs/backlog/cara-fleet-2026-10-24


## Implementation progress — October 24

CF-04 completion-poll increment in progress; async inference and queue lifecycle remain.
See [increment 01](../../cara-fleet-increment-01-2026-10-24.md). Original acceptance criteria remain required.
