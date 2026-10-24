# Cara fleet integration

Owner-approved first release: natural RU/EN investigations across the six monitored
hosts, project-state evidence, follow-up context and available historical receipts.
The only initial write runbook restarts `cara-worker.service` on PD after exact
confirmation. Existing research tasks and the networkless worker stay unchanged.

`fleet_protocol.py` is the closed contract. `fleet.py` handles conversation,
durable request/event records and card approvals. `cara_fleet.py` performs network
work outside the owner poller. `fleet_host.py` is the forced-command host gateway;
it reads structured measurements, fixed services and configured project metadata.
Log access returns bounded severity counts without MESSAGE text. Docker inspection
exposes only selected state/image fields. No environment or command lines leave
the host. The optional Exit relay connects only to the fixed Russian SSH endpoint.

Install per-host policy and dedicated source-restricted keys using the reviewed
operator package. Keep gateways, policy, SSH authorized keys and sudoers root-owned.
No public listener, new alert sender or sampling timer is needed. Executor keys and
transport configuration are inaccessible to Cara and her existing worker.
The executor uses the repository's `cara-fleet.service`; the owner service receives
only a spool group/path drop-in and `CARA_FLEET_ENABLED=true` after readiness.
Normal application installation includes the new Python modules. The host package
is independently fingerprinted and installed before activation.

Release checks: complete offline Linux regression suite, current CI lint, fixture
spool/card/ambiguity tests, real read-only snapshots through the restricted keys,
live sandbox/secret denial, original row/column parity on a stopped copy, original
backup/recovery and alert/monitor preservation, actual bounded model routing checks
and final healthy application/runtime checks. Preserve all failed attempts.

Never replace a changed host key from an unauthenticated connection. Never contact
the retired PI/Pilot/ARB hosts or the unverified testing address. No archived
one-shot deployment controller is a fleet runbook. Production application changes
retain that application's own exact release gates and forward-only recovery rules.

Investigation receipts/events remain in the existing checked daily/weekly database
backup. Consumed authorization markers remain indefinitely. A transport result may
expire after 24 hours only after the request has been consumed; the durable database
receipt remains. Missing or stale data always means unknown. Short resource samples
are not long-term performance measurements. Repository CI/issues remain unavailable
until an explicitly scoped integration is installed.

Initial production accepted October 23, 2026: source `fb20e4d`, build `bb4054457736`.
See the [accepted release](../../docs/cara-fleet-deployment-2026-10-23.md) for exact gates,
receipts, recovery evidence and limits. Ask Cara, for example,
“How is Daily Lingo doing?”, “What is using its memory?” or
“Что с российским прокси и его выходом?”.

Inventory/identity correction accepted: source `54b7e15`, build `86db8e9d2812`.
[Correction record](../../docs/cara-fleet-answerfix-2026-10-23.md).

Contextual follow-ups/readable evidence accepted: source `a7168bd`, build `a01f3e740a3b`.
[Current correction](../../docs/cara-fleet-contextfix-2026-10-23.md).

Typed evidence/complete reports accepted: source `e925af3`, build `38e1d657420c`.
[Architecture release](../../docs/cara-fleet-report-architecture-2026-10-23.md).
`fleet_context.py` owns canonical owner scope and `fleet_report.py` owns factual
clauses/coverage. Root-owned observation updates use the source-bound one-use
`update-observations.py`; read its receipt and never replay it. PI observes
`pi-prod-health-watch.timer` and `pi-prod-encrypted-backup.timer`. A loaded/active
timer still does not establish backup success; fresh backup receipts and repository
progress remain unconnected capabilities. No service, alert or schedule is changed.

## October 24 backlog increments

[Increment 01](../../docs/cara-fleet-increment-01-2026-10-24.md) repairs final
emphasis cleanup and pending fleet completion polling. It introduces no gateway,
transport or maintenance policy change. Run focused cleanup/poll tests in the
isolated Linux stage, then retain all full release and preservation gates above.
No package is complete until its business, compatibility and release criteria pass.
Model I/O and queue lifecycle remain separate work; a short poll does not make a
synchronous model call nonblocking. The original 47+15 specifications remain
required, with their qualification recorded independently from interface checks.
### Reviewed descriptive facts (first publication accepted)

Keep the reviewed projection and source register in private operator storage. Run
`python3 fleet_facts_publish.py status --projection <private-projection> --register
<private-register>` locally to detect pending source changes. Review changed facts,
record their actual source/check times, then use `export` with the same arguments
and `--output <sanitized-export>`. Export refusal preserves the previous export.
Never copy the raw KB or source register to the server or public repository.

After exact-source qualification, stage only the sanitized export under a protected
root-owned path. Create the destination with root ownership, no group/world writes,
and read access for the existing assistant group. Run as root:
`python3 fleet_facts_publish.py install-export --artifact <sanitized-export>
--directory <protected-facts-directory>`. Fixture artifacts, untrusted ancestors,
tampered integrity and history rewrites are refused. Retain the receipt and verify
its revision/current integrity using the service identity. Preserve the previous
revision and recovery backups. Facts do not change gateway transport authority.

This increment adds no timer: operators must review/export source changes and
record publication lag explicitly. Do not report a source date as a live probe.
Fleet inference preparation and settlement must run on the SQLite owner thread. Persist the phase and reservation before dispatch, retain unknown usage, and pump cancellation until accounting settles. Never reconstruct/resend a started paid phase after restart. Qualify the exact integrated source before activation; the primitive alone does not close CF-04.

Project-read activation requires exact source/hash and root mapping checks for each selected gateway. Discovery must advertise the reviewed read version before collection; an older or unmapped gateway returns its specific capability gap. Never retry as legacy all-project inspection. Root policy alone maps canonical project IDs to fixed resources. Do not infer backup success, development progress or client-route health from timers, containers or release manifests. Preserve the original restart contract and all sandbox denial checks.

Qualify catalog answers by required entities/fields, dates, unknowns and zero host inspections. A successful model/tool call is insufficient. Retain the original question and canonical owner scope for corrections; assistant prose and unsupported completeness claims cannot provide authority. Keep all 47 original and 15 additional public specifications unchanged.

The offline MCP facts adapter is not activated by source installation. Any later activation requires root-owned caller/facts configuration, unprivileged runtime/denial checks, bounded nonblocking client integration and parity against requested answers. Discovery/version negotiation alone does not qualify a business requirement or permit SSH/effects. Never advertise durable/project/Tasks support before its lifecycle and acceptance exist.

### Integrated business candidate release gates

Freeze the exact source before full normal/shuffled Linux and pinned CI gates.
Retain all qualification failures. Actual-model receipts must check requested
fields/entities/format and grounded final answers, with existing accounting and a
bounded aggregate cap; no Telegram sends, live effects or owner-update replay.
Do not run an old controller or use a mixed dirty stage as a published artifact.

Upgrade all selected root-owned scoped gateways from the exact verified package,
preserving keys/sudo/wrappers/policy and every existing denial. Install only the
reviewed sanitized facts export. After application files are installed, restart the
separate executor quiescently and verify its loaded source/PID/readiness hash before
activating new queue lifecycle. Retain legacy receipt/card compatibility. Confirm
actual results directory ownership is the executor and Cara cannot write/replace
it or its protected ancestors. No maintenance effect is a release demonstration.

Set CARA_FLEET_CONTENT_LIFECYCLE=true for this installed package and record this
single intended environment addition; all preexisting environment values and other
applications/monitor/alert/retention identities remain pinned. An absent setting
keeps never-installed defaults; malformed values refuse startup. Keep it true if
investigation is later disabled, so dormant content copies remain in typed purge.
Purge validation uses isolated fixtures only. Keep consumed tokens indefinitely.
Use the original backup/stopped-data-parity/recovery/readiness/runtime gates and a
fresh one-use active-session source-bound controller. Do not enable MCP yet.

### Accepted business release and operator publication rules

Source99037c5/buildf54906e304fe is accepted. Read the increment record for the exact
receipts and retained failures; never replay its controllers. Facts revision1 is
installed, lifecycle coverage is explicitly enabled, and MCP remains disabled.

Publish watched operator plans/receipts atomically: transfer to a private incoming
path, verify complete bytes/root ownership/digest, fsync, then rename into place.
A reader must never treat a file that is still being transferred as complete.
Static compilation is insufficient for operator qualification. Check fixed binary
paths, namespace working directory and exact service UID/GID/supplementary groups.
The outer executor state directory is executor-owned; the spool container is
root-owned and protected. Preserve the accepted inner ownership/denial boundaries.
Never change permissions merely to make a new operator assertion pass.

Discover selected capabilities before writing verification expectations. An empty
binding is not configured; do not assert a live observation from it or disguise
the gap. Qualify configured reads individually and retain their full timestamps.
A protocol-success result alone does not qualify the requested business answer.

## Typed project source installation and refresh

Project adapters are opt-in additions to protected project_adapters policy. Pin
project/capability, exact implementation version and fixed source_id/path/max age;
for development also bind the exact repository. Model input supplies none of these.
Install the adapter module before enabling its policy; verify discovery and actual
selected-project response as the real executor/gateway identity. Never broaden to
host/all collection when a source or implementation is unavailable.

The backup publisher is a manual root operator command:
`python3 -B /opt/tg-ingest-agent/fleet_project_publish.py --policy /etc/cara-fleet-backup-publisher.json`.
Its protected policy schema is cara-fleet-backup-publisher-policy/v1, with only
schema,database_path,database_uid,output_path,project_id,source_id,max_age_seconds.
Provision protected root-owned output ancestry and review the configured existing
jobs database/UID before invocation. Test actual UID/PATH/cwd and configured source;
no timer or backup operation is installed. Python isolated mode is not yet supported
for this publisher; do not claim a tested -I invocation.

Retain last good protected output on failure; never relabel its capture timestamp.
A failed/newer backup attempt, incomplete restore coverage, stale snapshot or CI run
that does not cover repository head stays explicit in final answers. Capture and
root-bind development snapshots separately through the existing approved account.
Keep all real bindings, private receipts and bootstrap data outside public Git.

## Optional MCP facts readiness

Keep CARA_FLEET_MCP_ENABLED=false until exact-source gates and same-UID readiness
pass. Provision root-owned, non-writable ancestry and no-symlink config with schema
cara-fleet-mcp-local/v1, principal, allowed_subjects and facts_path. Facts-only
activation omits references; tool discovery must then contain exactly fleet_lookup.
Keep actual subjects/bindings private and include authorized historical descriptive
subjects without making them executable. Set only the existing reviewed config path
and true flag in a protected operator drop-in; preserve original application env.

Before and after the real final restart, enter the actual main mount namespace and
run under the service UID, primary and supplementary groups, with explicit PATH/cwd.
Call the installed fixed client with real stdio; require path=mcp and
verified_equivalent_facts plus exact cells/pages versus internal rendering. Fallback
success alone is insufficient. Verify code/config/facts hashes, zero model/probes,
source/policy/freshness and denied symlink/untrusted/oversize/version/scope cases.
Preserve all main/worker/executor secret and write denials. The child inherits the
unprivileged service sandbox; do not relax it or infer extra OS read-only isolation.

Retained reads require separately reviewed policy and same-UID WAL/source/purge/
cursor qualification. Durable MCP creation, Tasks, writes and A2A are absent. Task
waiting uses existing task/fleet records; unknown-started reads reconcile without
paid replay. Purge must settle both ordinary phases and MCP slot before deletion;
late pages cannot recreate content. No manual Telegram qualification messages.

Full offline tests must bind synthetic/absent scratch facts paths; a root stage can otherwise read the installed descriptive publication even with networking disabled. Keep production facts and root bindings outside fixtures. Failed actual model plans remain accounted evidence and never bypass validators; correct the fleet contract and qualify a fresh bounded attempt.


## Increment 03: existing monitor sources and public development capture

The [increment 03 candidate](../../docs/cara-fleet-increment-03-2026-10-24.md) is not
a deployment receipt. Its target source/archive/operator/window remain UNSELECTED.
Complete the exact-source/full/model and original preservation gates before
installing a binding or enabling a path. Keep real values and receipts private.

### Existing monitor readers

Bind only the reviewed runtime implementation under the selected project's
project_adapters entry. The outer adapter is an exact implementation version;
all other keys must pass fleet_monitor_adapters.validate_binding. Discovery
validates policy without reading a source. The existing root gateway reads the
files; no producer-UID exception or permission relaxation is needed.

For project-daily-lingo-observer/v1, bind source_id, fixed latest_path/snapshot_root,
image, manifest_sha256, step5_receipt_sha256, max_age_seconds and
budget_max_age_seconds. Optional accepted_release needs its verified release,
image/manifest/readiness identity and optional exact targets. Do not infer these
values from a container or a review-time document. Read current project release
instructions before creating that private binding.

The reader opens only latest, snapshots/<validated timestamp>.json and latest
again. Changed pointer, missing file or invalid release/read-only/date proof
produces a typed gap; there is no scan/retry into history. It strips profiles,
jobs, assignments and private errors. Generation, reserve, budget heartbeat/probe,
capture/collection and read dates retain distinct meanings. A ceiling is not
actual spend or evidence that generation completed.

For project-authenticated-proxy-route/v1, bind one fixed path, source_kind
host/collector, source_id, role, source_sha256, approved profile modes/metric kinds,
max_age_seconds and, where reviewed, expected_boot_id/route binding. A collector
selects only the bound host. Verify the existing monitor source and sample date;
do not refresh it for a release demonstration. Current connections and cumulative
connection counters must remain labelled separately. Missing/stale/failed protocol
or route proof cannot establish client-route health.

Only these monitor reads opt into the 2 MiB source bound; default protected source
reads stay64 KiB. Outputs stay10 KiB. Retain root ownership/non-writable ancestry,
no-symlink regular-file checks and the actual service's existing permissions.
Qualify selected project isolation, discovery version, source/read dates and final
useful answers under the real gateway/service identity. No new monitor, probe,
timer, generation, alert or notification is part of installation.

### Fixed public GitHub development export

Use the existing authenticated operator connector to read only the policy-bound
public repository, simple branch, exact revision, bounded Actions page and selected
issue numbers. Do not use broad account discovery or copy credentials into a capture.
First read repository and branch, then the exact commit, matching Actions runs and
selected issues, then the branch again within the bounded capture window. Recheck
the same repository endpoint's public visibility/name at the end and retain that
operator review privately. A moved head or changed visibility requires fresh review,
not a permissive exporter fallback.

The raw private capture schema is cara-fleet-github-capture/v1. Each exact-URL record
has url,captured_at,sha256,value. Records are repository,branch_before,commit,actions,
issues and branch_after. The policy cara-fleet-development-publisher-policy/v1 pins
project_id,source_id,repository,ref,revision,capture_sha256,source_sha256s,output_path,
max_age_seconds and sorted issue_numbers. source_sha256s has repository,
branch_before,commit,actions,branch_after and issue-<number> for each selected issue.
Digests bind canonical reviewed JSON, not raw HTTP bytes or a GitHub signature.
No app/exporter network access or remote authenticity claim is implied.

Export to a new private staged artifact with:
`python3 -B fleet_development_publish.py export --policy <private-policy> --capture <private-capture>`.
The sealed sanitized envelope goes to stdout; stage/verify it atomically. Copy
only the reviewed sanitized artifact and protected capture-specific policy to the
operator destination. Never install raw GitHub responses in the app, publish them
as fixtures or print private raw input in logs.

After qualification, install as root with:
`python3 -B /opt/tg-ingest-agent/fleet_development_publish.py install-export --policy <protected-policy> --artifact <protected-export>`.
Both inputs and destination ancestry must be protected, root-owned and unlinked.
The destination cannot be either input. An exact unchanged snapshot is idempotent;
source/policy/revision mismatch, rollback dates and same-date conflicts preserve
last good output. Keep the publication receipt and existing backup/recovery policy.
For ambiguous-after-replacement exit2, inspect the current publication and
durability evidence; never automatically roll back or repeat.

Bind the installed source as project-development-actions/v1 only after validating
the exact repository/source/max age and service-identity read. Existing generic
project-development-snapshot/v1 remains valid independently. Display Actions
updated_at as source updated, and issue updated_at as an issue source date.
Completion time is not present merely because status=completed. CI/head mismatch,
stale captures, missing sources and partial CI/issues coverage remain explicit.
There is no schedule: later source changes require another reviewed capture,
policy/export and forward installation. A model answer cannot refresh the source.

### Parallel MCP work remains outside increment 03

Durable base admission/client-intent/cancellation changes and the all 15 source-case
runner are UNCOMMITTED parallel work. Do not enable them from the project-read
checkpoint or infer live readiness. Their own coherent specifications, exact-source
qualification, same-UID stdio/policy and lifecycle gates are required separately.
MCP remains disabled. Existing independent package criteria remain unchanged.

## Increment 04: durable base MCP/client readiness

This candidate supersedes the earlier absent-durable-path and uncommitted-MCP
checkpoint descriptions. It is not a deployment receipt. See
[increment 04](../../docs/cara-fleet-increment-04-2026-10-24.md).
Target source/archive/operator/window remain UNSELECTED. Keep the MCP default false
until exact full/model/CI and actual installed identity/readiness pass.

Facts-only and optional retained/admission configuration are separate choices.
Facts-only omits those policies and advertises only fleet_lookup. Admission uses
the root-owned investigations policy with its exact compiled version/capability
hash, existing database/owner and allowlisted subjects, hosts, projects, capabilities
and facets. It must agree with any legacy retained policy. Generate no reviewed
hash from an older source or arbitrary client declaration. Keep real policy and
bootstrap values out of Git/issues.

Use fleet_mcp_client.start_investigation only from asynchronous/external work,
with a real canonical source_update and optional allowed format. The control
helper is synchronous and must not block the owner loop. It commits a source-row
admission_prepared intent and recovers its exact key before its first stdio call.
The explicit-key investigation_start helper also prepares. The raw server start
still accepts only source_update,idempotency_key and allowed lang/presentation/
detail; no question/owner/target/address/path/command/approved override is accepted.

Verify through a second connection that the key/input/principal/policy/format is
durable before transport, prepared state has no inference/job/answer/send, and
owner ticks cannot dispatch it. Only matching server promotion to admission_queued
can begin existing paid phases. Prepared work counts within the original eight
requests; source expiry/edit/revoked policy fences it without dispatch. Prepared
cancellation can settle immediately because it is definitely unsent.

After unavailable transport or a response lost after promotion, recover the same
source/key/handle. Return reconcile_required for uncertain start/cancel; never
create another key, perform local admission or automatically retry an unknown
operation. Read-only fallback reloads current principal/source/scope/purge policy
and returns the same canonical retained result. Denials cannot become outage
fallbacks. Status/result never start a new model, probe or delivery.

Protocol notifications do not cancel application work. Explicit cancellation stays
requested through owner ticks while the transport future is running, suppressing
late pages but retaining the memory handle and usage accounting. Confirm
cancelled/settled only after actual settlement. Lost started settlement after a
restart becomes ambiguous/unknown_outcome, including a loss before the first cancel
tick; do not reconstruct/resubmit the original operation.

Discovery may advertise the qualified base investigation_start/status/result/cancel
tools in addition to fleet_lookup. Start/cancel annotations reflect orchestration
writes; no protected project-state effect exists. Tasks is unsupported/not
advertised, including for a Tasks-capable caller; base start/status/result must
still work. No maintenance, restart, deploy, cleanup, backup-start, generation,
project DB/repository write, messaging or A2A tool.

Before activation, enter the actual final service mount namespace and use the
real UID, primary/supplementary groups, explicit binary paths/PATH/cwd. Require
actual installed stdio success with current config/code/facts/capability hashes,
one same-ledger handle, useful requested answer, exact scope/dates/uncertainty,
zero catalog probes and no extra paid calls. Qualify owner updates/reminders while
work is blocked. Internal fallback alone is not stdio readiness. Preserve every
main/worker/executor secret/write denial; the restricted SQL API does not prove an
OS-level read-only database sandbox.

Prepared/admitted states join original pending/capacity/readers, backup/restore and
typed all-content purge. Hold admission, settle model/stdio/executor work, refresh
counts and require the original nonce/source/PID/preview-bound quiescence before
delete. Changed counts require the original renewed typed phrase. Keep spend,
preferences, non-content consumed markers and protected configuration/facts/
backup/release evidence. No live purge test.

The existing catalog/MCP page limit and task receipt/delivery page limit differ.
The candidate losslessly re-pages to at most 3,500 characters per task page/32
pages and binds the exact retained answer to its immutable receipt. Actual answer
overflow uses existing managed Markdown/send_document, keeping the 64 KiB cap and
explicit draft artifacts. Register metadata before bytes; preserve counted crash
residue. Explicit failed-document resume requires original source/owner/policy,
succeeded steps, linked receipt and exact artifact, with no read/model rerun or
automatic ambiguous-delivery retry. Its frozen taskbridge suite passes 45 tests
in 22.201 s in network-isolated Linux with an empty environment, including ten
new actual pipeline regressions. Preserve all failed originals. Final frozen-
source combined/full/model/installed qualification remains required.

Retain the focused 227-test/16-intent, 17 pure operator/orchestration-fixture and
separate 18 pure gateway-updater-fixture receipts as supporting checkpoints. The
gateway draft remains source/archive/module-unbound; its fixtures use no live
updater entry point. Unbound pure fixtures are not operator qualification. The
nine-path frozen model-harness precheck also makes zero paid calls/probes and
does not qualify actual model behavior. These do not replace exact normal/shuffled Linux,
pinned lint/CI, accounted model qualification, checked backups/stopped parity,
loaded-source readiness, monitor/alert/recovery/neighbors and final acceptance.
Use a fresh bounded-session one-use controller and atomic watched-file publication.
Never replay failed/completed controllers, historical updates or unknown paid work.
No manual Telegram sends or live maintenance demonstration.

### Post-acceptance descriptive publication

Keep accepted facts revision 1 until a new checked publication succeeds. After
final app acceptance, update the private operational KB first, then use the
existing facts status/review/export/forward-install workflow. Only reviewed
changed source digests move; preserve other registered hashes and actual evidence/
check dates. Keep status/lag in its existing operator CLI, without new query
fields or self-referential publication-receipt content. Do not claim revision 2
or current source hashes before the successful receipt. No new timer/schedule.

## Increment 05 existing-restart resource qualification

Published increment04 `319dd95` remains a source publication, not installed
acceptance. The separate increment05 candidate protects only the existing reviewed
worker-restart runbook. Do not exercise a live restart to qualify the lock.

The fixed host ledger gains a protected resources directory, a stable per-unit
lock inode and a separate durable hold. The host gateway takes a nonblocking
fcntl lock before reading the fingerprint or consuming a new host token. Distinct
same-unit contention returns resource_busy with no effect. A replayed token keeps
its original consumed/ambiguous meaning. Keep the lock inode stable; replacing
its path while processes hold it would split mutual exclusion.

New hold fields are version, fixed service, token, executing/unknown/settled state
and timestamp. These contain no owner question, answer, task payload or approval
prose. Preserve this non-content resource evidence and existing consumed markers
through content purge and host recovery. It is outside the conversation database;
normal application backup policy remains unchanged. Host release/recovery evidence
must preserve the existing ledger, rather than assume database backup covers it.
Do not delete/restore old markers to authorize another attempt.

A crash releases the OS lock but does not settle the durable hold. Before any new
token can proceed, the gateway requires known settlement. It can reconcile only
from the exact held token's durable succeeded/failed receipt for that same unit.
Malformed/missing/ambiguous evidence stays held. Time, service health, a new
fingerprint, approval or process absence alone cannot establish the previous
effect's outcome. No timer, force flag or clear-unknown endpoint is provided.

For a remaining unknown outcome, retain the evidence and use the existing guarded
operator/recovery process to establish what actually happened. Record the actual
outcome and its proof; any later restart still needs fresh original source/card/
fingerprint/expiry authorization. Do not invent a successful outcome or repeat the
old token to reconcile it. No ad-hoc shell effect or automated ledger editing is
part of this increment.

Qualify in isolated Linux with synthetic units/command doubles: two independent
processes and distinct tokens on the same unit; busy before fingerprint/token;
independent unit isolation; crash before/after consumption, effect and receipt;
durable unknown after lock release; same-token final-receipt reconciliation;
expiry immediately before effect; protected no-follow paths/files and old token
compatibility. Verify closed RU/EN busy/unknown/expired answers through existing
result handling. All actual service restarts remain outside fixture verification.

The focused network-isolated Linux run passes 80 tests in 4.645 s: 20 new resource
guards plus 60 existing fleet cases (attempt 3). Preserve the initial fixture
FD-inheritance failure and the earlier corrected run; the final drift fixture
uses a valid changed fingerprint and keeps the original rejection assertion.
This does not replace the complete crash-boundary/restore audit or the separate
pending CF-04 logical-90-second/two-completion proof.

Final source/archive/operator/window are UNSELECTED;
require original full/model/CI, actual protected ledger/source hashes, sandbox,
checked backups/stopped parity, readiness, monitor/alert/recovery/neighbors and
fresh one-use release acceptance. Keep production accepted `99037c5` and MCP
disabled until the exact new package passes. All CF packages remain open.
[Increment 05 release record](../../docs/cara-fleet-increment-05-2026-10-24.md).

## Increment 06 compatibility and proof qualification

Keep original host token history in place when updating the gateway. Under the
existing unit lock the candidate reads at most 4,096 entries/4 MiB total/16 KiB
per record before fingerprint/new host-token/effect. An unresolved same-unit
legacy record blocks fresh tokens without requiring a modern resource hold.
Malformed/unsafe/unscoped records fail closed; valid other-unit evidence remains
independent. A known final receipt allows a later fresh approval. Old tokens keep
their duplicate/ambiguous meaning. Do not rewrite/migrate/refresh old markers,
clear by time/fingerprint or add a force/clear-unknown tool.

The repository display prefix is 12 characters from the validated typed revision.
Full revision/provenance remain in evidence; global secret scrubbing is unchanged.
The actual CI-disconnected Agent path must show that prefix with a precise CI gap.

Retain separate focused evidence: 103 legacy/resource/fleet/poll/wait tests pass
6.298 s, three early real-process crash boundaries pass 0.052 s, and 26 internal/
MCP parity tests pass 8.465 s. The original seven poll tests remain byte-exact;
new logical90-second/simultaneous-completion cases are in the separate waits
module. Catalog parity reuses the original19 assertions through both Agent paths;
fresh selected-project and retained adapter parity preserve dates/scope/gaps.
These overlapping checkpoints are not a full frozen-source or actual-model gate.
Keep the retained fixture failures and unlaunched intermediate preparations.

Before any fresh gateway update, bind complete host-ledger presence/absence and
all existing file metadata/hashes, including consumed tokens and resource state,
to the reviewed source/operator inputs. Preserve checked root-only backup bytes
and manifests across source/policy replacement and read-only probes. Fixed sudo/
keys/wrapper/relay checks alone do not establish ledger preservation. Discovery/
project/legacy read probes must not create restart resource artifacts. No host
restore API, ledger deletion/recreation, old-token replay or live restart trial
is authorized as verification.

Pin the supported stdlib client/server protocol pair separately from application
capabilities: MCP 2026-07-28 only; no SDK dependency or unqualified legacy-version
fallback. Official discovery/stdio references were rechecked October24; real
isolated stdio tests qualify this pair, not every external client. Tasks is
unsupported and A2A/new writes remain outside scope; base async still works.

Published base fca4ac4 CI is separate from candidate qualification. Final source/
archive/operator/window remain UNSELECTED. Require full normal/shuffled/pinnedlint/
CI, bounded accounted model answers, exact real service namespace/groups/stdio/
secret denial, fixed source bindings, checked online/stopped backups/original
parity, loaded-source readiness, monitor/alert/retention/neighbors/recovery and
fresh once-only guarded acceptance. Production remains accepted 99037c5, MCP
disabled, all packages open. [Increment 06](../../docs/cara-fleet-increment-06-2026-10-24.md).

## Increment 07 fixture correction and failed full qualification

Do not treat source 12a47d7 as fully qualified. Its normal isolated Linux run
completed 2,769 tests with two failures/nine baseline skips; shuffled was not
reached. CI 40094268134 failed the same two checks. Retain those source/archive/
log/result artifacts and use a fresh exact-source attempt after correction.

The correction changes tests only: expect the already approved 12-character
repository display while independently asserting the full revision in typed
state, no masked placeholder/full long hash in the answer and original CI/issue/
date/scope assertions. The legacy fixture cleanup uses equivalent explicit
if/else instead of a conditional expression rejected by the test-hygiene check;
all its assertions stay unchanged. No application or budget/provider/model change.

Fresh separate isolated Linux runs pass 42 project integration/parity tests in
7.357 s and104 legacy/resource/fleet/poll/wait/exact-hygiene tests in 7.342 s.
They do not replace the next exact whole-source normal/shuffled/lint/CI or bounded
accounted model gate. All installed source/policy/service-identity/stdio/sandbox,
backups/stopped parity, ledger preservation, readiness, monitor/alert/retention/
recovery and guarded acceptance remain required. Production remains accepted
99037c5/MCPdisabled; all packages open. Next source/archive/operator/window:
UNSELECTED. No paid call, live effect, purge, Telegram test or deployment occurred
for this correction. [Increment 07](../../docs/cara-fleet-increment-07-2026-10-24.md).

### Published increment 07 documentation-guard follow-up

Source 38b7d1 passes both earlier fixture checks; CI 40094842974 then fails the
unchanged standing guard against hard-coded suite totals in rolling specs.
Correct CARA/SOLUTION wording only and keep exact historical results in the
increment 07 record. Do not relax the guard or replay the failed verifier. No
application/test/model/budget change or live action accompanies this correction.
Require fresh complete-source qualification and every existing installed/release
gate. Production remains accepted 99037c5/MCP disabled; packages remain open.

## Increment 08 task admission guidance release gate

Published `8cbf06f` passes CI but fails its accounted model check at task admission
following two passing catalog phases. Reject the malformed array input and
retain its usage/failure. Do not normalize it, replay the attempt or change
validator, retry, model or budget policy. New guidance supplies an exact JSON
object/binding example and preserves complete owner investigation intent.

Focused isolated task/source/hygiene regressions pass; fresh whole-source
full/CI and bounded model qualification remain gates before the existing paused
installation/backups/parity/readiness/monitor/recovery/acceptance process. No
live task, host effect, Telegram test or model retry accompanies this source
increment. Compare original corpus bytes from canonical archives; working-copy
line endings are a separate representation. Production remains accepted `99037c5`,
MCP disabled; all packages open. [Increment 08](../../docs/cara-fleet-increment-08-2026-10-24.md).

## Increment 09 planner scope and focus release gate

Source `9566d1b` passes full Linux/CI but fails the eighth accounted model phase:
no canonical prior exists for its requested operational inheritance. The
validator rejects before a project read. An earlier recorded memory-answer pass
used weak relevance checks; review finds unrelated broad diagnostics. Retain
failed raw output, review, usage and all previous receipts; no accepted full
model cohort or deployment follows.

Planner guidance now requires explicit scope for a self-contained named target,
separates evidence time from conversation references, preserves genuine prior
and local catalog inheritance, and avoids umbrella resource focus for a narrow
metric. No validator, renderer, source-age or inference-budget change. Focused
isolated checks pass, but fresh exact whole-source full/CI/model and every
existing protected source/identity/backup/parity/readiness/monitor/recovery gate
remain required. No controller replay, model retry, host effect or live message
is introduced. Production `99037c5` / MCP disabled; all packages open.
[Increment 09](../../docs/cara-fleet-increment-09-2026-10-24.md).

## Increment 10 narrow semantic reads and paired business proof

Correct a remaining collection defect: new explicitly narrow host metric plans
without optional read_scope still inherited the legacy blanket project facet.
The semantic mixin now derives the existing host-only contract when no project
or inherited scoped obligation applies. Executor/gateway project reads are then
excluded. Base legacy parsing/wire, stored observations, broad resource/overview,
project scopes and exact restart confirmations remain unchanged.

Additional source-linked parity tests exercise original negative runtime,
conflict/completeness/date, mixed and preference assertions through internal and
MCP reads with exact observations/final pages. Preserve both original corpora.
These tests do not constitute model or installed qualification; current-source
complete Linux/CI, bounded accounted requested answers and every backup/readiness/
monitor/recovery gate remain required. Production remains accepted `99037c5`,
MCP disabled; every package stays open.


## Increment 11 requested runtime fields

Manual review of a formally passing answer finds unrelated healthy internals,
resource gaps and repeated timestamps in a requested concise application reply.
Add optional closed semantic answer_fields and typed presentation within existing
project/runtime scope. Preserve full evidence and original legacy/no-field/full
reports, requested failures/gaps/dates, model/production budgets and authority.
A real-model field-presence pass alone cannot close business acceptance. New
semantic, reply, source, actual-model and original installed/release checks remain
required; production99037c5/MCP disabled, packages open. [Increment 11](../../docs/cara-fleet-increment-11-2026-10-24.md).

## Increment 12 - stale and partial development evidence

Dated development replies preserve source age and incomplete sample scope
together. A current read cannot make an old subset fresh or complete. This is a
reply-only correction using existing validated complete:false metadata; no
adapter/source/probe/schema/prompt or authority change. Preserve prior unpaid
replay failures and all original release gates. New exact committed source,
full/CI and bounded actual qualification precede a fresh single-use selection;
never replay earlier qualification or deployment controllers.
