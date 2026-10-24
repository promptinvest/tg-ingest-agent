# Cara fleet increment 04 - October 24, 2026

Status: durable MCP/client implementation candidate; not deployed. This increment
follows published project-read commit `498cb11` and the shared task/facts foundation
on `8f50282`. Production remains accepted `99037c5` / build `f54906e304fe`.
[Increment 03](cara-fleet-increment-03-2026-10-24.md) records project-source work;
[earlier qualification](cara-fleet-increment-02-2026-10-24.md) retains its failures.

The owner's backlog implementation request and standing implementation-includes-deployment
rule authorize the guarded release within scope. They are not a separate
hash-specific approval. The target source, canonical archive, operator package and
active-session window are UNSELECTED. No controller, installer or gateway update
has been launched for this increment.

## Business behavior and authority

An approved client can start one durable fleet read from an existing canonical
owner message, poll its actual phase and obtain the useful retained answer through
base MCP operations. Catalog answers use local facts and zero host inspections.
Selected runtime/project reads still use the existing bounded executor and exact
qualified project scope. Retained pages, provenance, source dates and uncertainty
remain the answer; protocol success cannot substitute CPU for an address or invent
completion from CI.

Protected local policy supplies the principal, existing database, owner and allowed
subjects/hosts/projects/capabilities/facets. Start arguments contain only a real
source_update, caller idempotency key and allowed language/presentation/detail.
They cannot supply a question, target, path, command, owner, principal or approval
flag. Source ownership, source edit/age, current policy/scope and original request
binding are checked before admission, model/probe dispatch and result publication.

The adapter never constructs Agent, loads app secrets, calls a model, sends Telegram
or opens privileged transport. The owner loop runs the ordinary budgeted fleet
workflow only after admission. Existing semantic calls, reservations, original
profiles/providers, eight-request capacity and global/per-host bounds are retained.

Read-only describes protected project/business state. Start/cancel update existing
orchestration rows and their annotations reflect that; they are not project-write
tools. The SQL-restricted same-UID database API is not claimed as OS-level read-only
isolation. Main/worker/executor credential separation and inherited service sandbox
remain required.

## Durable client intent before first submission

fleet_mcp_client.start_investigation uses fleet_mcp_admission.prepare_start to
commit an admission_prepared intent in the existing fleet_requests/events ledger
before the first stdio exchange. Key generation/recovery is under the source-row
transaction lock. The exact key, canonical owner source/input hash, principal,
policy and requested format are retained together. The explicit-key
investigation_start client helper also prepares before transport.

A prepared row has no inference, executor job, observations, answer or Telegram
delivery. The owner loop may fence expired/edited/revoked intents but cannot start
work from a prepared intent. Only the server's matching atomic start promotes the
same row to admission_queued. The original raw direct-server start remains valid;
it does not create a second client store.

Two clients, process loss, an unavailable exchange or a response lost after server
commit recover the same key/request. No local admission or replacement-key fallback
can start another investigation. Explicit retries reconcile that identity first.
Conflicting key, format, source, principal or policy cannot repurpose the row.
The client performs one exchange per invocation, not an automatic logical retry.

Prepared intents count within the unchanged eight-active-request cap. Expired or
fenced intents release capacity without dispatch. An explicitly cancelled prepared
intent is definitely unsent and settles locally; reconnect cannot promote it.
Checked existing backup/restore preserves its key and source binding. No table,
outbox, new content ledger or additional retention policy is introduced.

## Cancellation, unknown outcomes and content lifecycle

An RPC cancellation notification does not alter an application handle. Explicit
investigation_cancel first reports requested, suppresses publication and retains
any live transport/model handle. An owner-loop tick while the facts future is still
running must continue reporting requested, without a cancelled claim or answer
pages. Only an actual settlement receipt permits cancelled/settled.

A process restart after a started cancellation loses proof of settlement. Both
loss after a durable waiting receipt and loss before the first owner cancellation
tick report ambiguous, cancellation unknown and unknown_outcome. Neither resubmits
transport/model work or invents a completed answer. A paid model that cannot be
stopped remains accounted even when its result is discarded; generic failure alone
does not become confirmation that a cancellation stopped work.

All prepared/admitted states join existing source checks, capacity, pending cadence,
shared readers and typed content lifecycle. All-content preview includes original
questions, context, results, approval/event content and disclosed transport copies.
The same source/nonce/PID/preview-bound quiescence and async settlement must precede
deletion. Changed counts renew the existing typed confirmation. Interrupted purge
continues durably; late output cannot recreate rows or pages.

Spend/preferences, minimal non-content consumed effect markers, protected facts/
configuration and existing backup/release evidence retain their disclosed policy.
Normal backups and recovery/retention are unchanged. No live purge or maintenance
demonstration is part of verification.

## Optional Tasks, scope and compatibility

The pinned local stdio protocol is 2026-07-28; application schema/capability/policy
versions remain separate. Exact discovery and returned schemas are checked against
the compiled manifest. Descriptive server instructions, clientInfo and tool
advertisements cannot enter the closed action set or grant authority.

The optional base manifest is fleet_lookup plus investigation_start/status/result/
cancel. Existing retained project/status/result reads remain separately configured.
Tasks is not supported or advertised. An ordinary or Tasks-capable client receives
explicit unsupported Tasks behavior and uses the same working base lifecycle.
No successful both-peers Tasks negotiation is claimed or required.

Unknown tools/fields/versions, unauthorized principals/projects, forged answers and
model approval flags fail closed. An authorization denial is never reclassified as
an outage to bypass policy. Read-only outage fallback reloads current principal,
source, scope and purge policy and returns the same canonical result; uncertain
submission/cancel responses require reconciliation rather than fallback admission.

No maintenance/restart/deploy/cleanup/backup-start/generation/database/repository/
messaging effects are exposed. Existing exact restart cards/tokens are unchanged.
No public listener, new account, provider, budget, credential store or A2A scope.

## Task answer-page compatibility

Valid catalog/fleet pages up to the existing 3,700-character bound are losslessly
re-paged within the original task limit of 3,500 characters per page and 32 pages.
The immutable task receipt retains the exact full original answer text. When that
actual fleet answer exceeds ordinary delivery, the existing managed Markdown and
send_document path delivers the exact answer once. Short/non-fleet summaries,
error flows and explicit draft artifacts retain their behavior.

The original 64 KiB artifact cap remains; excess content produces an explicit
blocked gap, never truncation. Deterministic artifact metadata is registered before
file bytes so write/crash residue is disclosed, counted and recoverable. A typed
document-channel failure may be resumed explicitly through existing owner/source/
policy/effect fences only when succeeded steps, linked fleet receipt and exact
artifact agree. It never reruns the read/model; ambiguous delivery is not an
automatic retry. Existing explicitly drafted artifacts are preserved.

Ten new taskbridge regressions (45 methods total in that suite) cover actual
3,501--3,700-character renderer-to-wait/receipt/artifact/document delivery, crash,
ambiguity, checked restore, purge, bounds, orphan accounting and resume authority.
The frozen taskbridge suite passes 45 tests in 22.201 s in fresh isolated Linux
with networking disabled and an empty environment (attempt 6). Its retained log
SHA256 is `41b4d15c4c29312799ba2f69e7018a7b6537dbfbf0baa56b46c2f32936ffce22`.
All failed originals remain; none was replayed or erased. This focused pass does
not replace final frozen-source combined/full/model/installed qualification.

## Executable business corpus and focused evidence

The unchanged [47 original specifications](backlog/cara-fleet-2026-10-24/regression-cases.json)
and [15 additional MCP specifications](backlog/cara-fleet-2026-10-24/mcp-regression-cases.json)
now all have executable source-linked business assertions. No three lifecycle
skips remain. The [crosswalk](../test_fleet_business_corpus.py),
[extended original runner](../test_fleet_business_extended.py) and
[MCP runner](../test_fleet_mcp_business.py) preserve the original assertions.
Several source IDs share a test; source-case counts are not test-method totals.

Reconnect joins a lost durable response with actual Agent planning and one useful
requested answer, one accounted semantic call and no host/duplicate delivery.
The separate client-intent suite observes the committed prepared row through a
different SQLite connection before the first exchange, then checks server-only
promotion, source-key restart recovery, concurrency, expiry, purge and checked
backup/restore. Cancellation holds the actual future through an owner tick and
then verifies real settlement. Owner stats and reminders continue while stdio or
planning is blocked.

The published project-only source `498cb11e29a520229e57d1ebd42f89d367e2f652`
passed [CI run 40090835125](https://github.com/promptinvest/tg-ingest-agent/actions/runs/40090835125).
That source/CI evidence belongs to increment 03 and does not qualify the
uncommitted increment 04 source or its deployment. The nine-path synthetic model-harness
precheck passes with nine frozen completions and zero paid calls, probes or live
actions. This checks the harness; actual new model qualification awaits the
canonical full-source gates.

The latest isolated Linux durable-client checkpoint passed 227 tests in 14.395 s,
including 16 client-intent tests and all 15 MCP source IDs without lifecycle skips.
Seventeen pure operator/orchestration fixtures pass in 0.017 s, superseding
the earlier ten-case checkpoint. The backend is still unbound: this is offline
draft evidence, not operator qualification or a live transition. Separately,
18 pure gateway-updater fixtures pass in 0.373 s under network-isolated Linux
with the draft source/archive/module bindings still unselected. The initial
runner-only static precheck failure is retained; no updater entry point ran.
These groups overlap neither release claims nor actual service-identity gates.
The separate frozen 45-test taskbridge pass above now covers the ten new actual
long-page regressions. Final frozen-source combined/full/model/installed
qualification remains pending. Retained qualification/fixture failures are not
replayed or erased. Test groups overlap and must not be added as separate totals.

Frozen semantic outputs qualify deterministic scope/grounding/presentation paths,
not unrestricted model language behavior. Synchronization does not measure
production p95. Protected actual stdio in an isolated stage does not establish
installed service-namespace readiness. Existing account/provider/model/budget
configuration is unchanged; fresh bounded accounted qualification remains required.

## Facts publication follow-through

The accepted descriptive publication remains revision 1. Publication status/source
lag is available through the existing operator status CLI, not an invented model
query field. After final application acceptance, update the private operational
KB, review the changed source digest and export/install a new forward facts
revision. Preserve the other reviewed source hashes, actual evidence/check dates,
last good artifact/history and publication receipts. Do not insert self-referential
receipt fields or claim revision 2/current sources before its actual receipt.
This remains a manual workflow without a new timer or continuous-sync claim.

## Independent acceptance and release gates

All CF packages remain open until their own original acceptance and compatibility
criteria are audited and release requirements pass. CF-01 through CF-06 have no
dependency on MCP adoption or availability.

| Package | This increment's contribution | Remaining acceptance |
|---|---|---|
| [CF-01](backlog/cara-fleet-2026-10-24/01-fleet-facts-and-sync.md) | Same local facts/provenance/gaps during disabled/outage MCP; no new descriptive authority | Independent source-status/lag, history/privacy/failure and compatibility audit |
| [CF-02](backlog/cara-fleet-2026-10-24/02-semantic-queries-and-evidence.md) | Current source/scope/format and requested answers survive optional base admission | Fresh bounded RU/EN/paraphrase/correction/mixed-answer qualification and final full release |
| [CF-03](backlog/cara-fleet-2026-10-24/03-adaptive-grounded-replies.md) | Useful retained pages and task-page compatibility without protocol dumps or content loss | Fresh entailment/omission/format and full compatibility; focused lossless-page/artifact suite passes |
| [CF-04](backlog/cara-fleet-2026-10-24/04-responsive-durable-execution.md) | Inert before-send intent, same-handle recovery, truthful settlement and original cadence/capacity | Final all-phase/accounting/cancellation/restore regressions and installed readiness |
| [CF-05](backlog/cara-fleet-2026-10-24/05-project-read-capabilities.md) | Optional client narrows to the same existing qualified project implementations | Independent real source bindings and useful dated answers/gaps under actual gateway identity |
| [CF-06](backlog/cara-fleet-2026-10-24/06-governed-capabilities-and-lifecycle.md) | Original authority/receipt/accounting and typed purge includes every admission state | Full task/card/effect/lock/purge/backup/sandbox acceptance audit before broader effects |
| [CF-07](backlog/cara-fleet-2026-10-24/07-mcp-fleet-interface-and-client.md) | Base durable read lifecycle and prepared caller key; all 15 source IDs executable | Final exact full/model/CI, actual unprivileged policy/stdio/readiness and separately recorded optional activation |

| Final release field | State |
|---|---|
| Exact source/commit and canonical archive | UNSELECTED |
| Full normal/shuffled Linux, pinned lint and CI | PENDING for final source |
| Required bounded accounted model qualification | PENDING for final source |
| Long-page/artifact focused fixtures | PASS: 45 tests / 22.201 s, attempt 6; failed originals retained |
| Final combined frozen-source qualification | PENDING |
| Actual service UID/GID/groups, namespace, stdio/config/source hashes and denials | PENDING; candidate installer catalog is 87 modules plus agent |
| Fixed project source bindings and requested business-answer checks | PENDING where not previously accepted |
| Checked online/stopped backups, original rows/columns, environment and recovery parity | PENDING |
| Main/executor loaded-source readiness, neighbors and existing monitor/alert/retention bindings | PENDING |
| Fresh single-use operator package and active-session window | UNSELECTED |
| Deployment, final acceptance and package closure | Not started; no package complete |

Keep default MCP disabled until qualified actual-identity readiness and reviewed
caller-policy activation. Facts-only activation may omit retained/admission policy;
installing code does not enable any of them. Preserve all main/worker/executor
secret/write denials and original host keys/transport bounds. Do not infer OS-level
read-only isolation from API restrictions.

Release only the exact verified source using the original backup, stopped-data,
readiness, monitor-binding and forward-only containment process. Watched operator
inputs must arrive atomically. Preserve all failed originals; never replay a
started/completed controller. No manual Telegram test, historical update replay,
paid corpus work, live purge or maintenance effect.
