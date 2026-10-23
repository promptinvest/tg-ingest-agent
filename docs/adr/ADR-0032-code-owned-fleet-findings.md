# ADR-0032: Owner-bound fleet scope and code-owned findings

- Status: Implementation in verification, October 23, 2026.
- Owner authorization: “go and implement” after the architecture review.
- Scope: fleet conversation/evidence/reporting and corrected PI probe mapping.

The deployed citation validator proved only that an evidence ID existed. An
arbitrary interpretation could still claim backup health from a CPU sample,
discard half the requested hosts, confuse absent probes with absent projects,
and present two short snapshots as long-term stability. Passing the previous
regression suite did not qualify those semantic claims.

Keep the closed router, isolated executor, pinned transport, forced-command host
gateways, original networkless worker and exact maintenance confirmation path.
Extend the existing fleet skill with two small modules, without another daemon.

`fleet_context` persists canonical owner-bound frames in existing request payloads.
Frames contain hosts/projects/facets/focus and original/latest questions, never
assistant narrative. Inherited/all scopes resolve in code. An intervening owner
task or stale/edited source ends the context; prior receipts remain readable.

`fleet_report` represents observations with host, field, status, source, time and
window. Unit LoadState distinguishes nonexistent configuration from inactive
services. Unconfigured project probes are explicit capability gaps. Dated project
records remain separate from current runtime observations. History supports exact
dated snapshot comparisons, without asserting stability or causality.

The model selects finding IDs, prerequisite-bound hypothesis types and bounded
additional read facets. Arbitrary factual prose is rejected. One optional extra
read uses a durable nonce/step binding, retained per-facet observation times,
original host scope and existing model/cost limits. No mutation is inferred.

Code owns report coverage and factual clauses. Every requested host has findings
or an explicit observation gap. Long reports split at line boundaries with durable
page receipts; interrupted/ambiguous deliveries are never automatically replayed.

Qualification includes the owner's actual list/details exchange, real six-host
receipts, adversarial missing/contradictory evidence, actual bounded model calls,
and existing source/approval/spool/backup/runtime regression gates. Fix PI's probe
names without changing its backup/health service configuration or executing them.
