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
