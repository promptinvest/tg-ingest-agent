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
### Reviewed descriptive facts (pending activation)

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
