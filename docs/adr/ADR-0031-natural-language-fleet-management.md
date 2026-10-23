# ADR-0031: Natural-language fleet investigations with a restricted executor

- Status: Implemented and deployed `fb20e4d`, 2026-10-23
- Owner authorization: “go with this” after the natural-language fleet design.

Cara gains a dedicated skill module behind the existing closed router. Owners ask
ordinary RU/EN questions about resources, project state and follow-ups. Bounded
plans choose inventory IDs and observation facets. A separate executor owns
restricted SSH credentials; the existing networkless worker retains its boundary.

The initial release covers six monitored hosts. Testing VPS 130.49.190.246 is
excluded: October 20 evidence already required independent identity verification;
the old host KB has no verified retirement record. Retired hosts are not targets.

Reads use fresh structured measurements, safe service/project observations and
explicitly dated project records. Prior investigation receipts support comparisons
when available. Missing history, GitHub CI/issues or unconfigured runbooks remain
visible capability gaps. Broad conversation never grants arbitrary shell access.

Maintenance starts with one reviewed operation: restart Cara's isolated worker.
It requires a fresh service fingerprint, an exact delivered owner confirmation
card, unchanged inventory/source, a ten-minute expiry and one-time consumption on
both executor and host. Connection loss is ambiguous and never blindly retried.
Other deployments, updates, cleanup and backups require dedicated future runbooks.
They are not silently substituted with a restart.

Two additive SQLite tables preserve durable requests/events. All original tables,
existing pending confirmations, history, budgets, reminders, worker isolation,
monitoring, alert preferences and application-specific deployment rules remain.
The implementation request includes deploying this integration under the standing
owner rule; it does not authorize manual learner messages or exercising a live
maintenance action merely to demonstrate the feature.

## Status log

- 2026-10-23: owner accepted implementation; source8dbf4a1 then fb20e4d.
- 2026-10-23T09:42:36.751288+00:00: deployed and accepted after all original gates;
  [release record](../cara-fleet-deployment-2026-10-23.md).
