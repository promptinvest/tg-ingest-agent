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
