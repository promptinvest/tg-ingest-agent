# ADR-0017: Deploys are recoverable and honest about their outcome

- Status: Proposed
- Date: 2026-09-07
- Phase: Phase C — robustness, operations, security
- Source: 2026-09-07 review of architecture, code and live behavior against build e3208b8 (report kept off-repo; findings are referenced by id)

## Context

--pull/--rollback fail before mutation on the box because the deploy key and clone are absent while README and CARA §9 promise them; the stage dir is never wiped; a failed live verification leaves the new build running with no receipt; three of four deployments were built from a dirty tree.

## Decision

the installer snapshots ingest.db into the backup dir before any restart; the stage payload is wiped before untar, dotfiles preserved; deployment_notice gains mark-failed and the remote block traps ERR to write a failed manifest, with the operator's single terminal notice quoting it; the box gets the read-only deploy key or --pull/--rollback exit 2 with the checkout-and-deploy recipe; README and CARA §9 document that recipe as the rollback that works today; CI prints suite duration and runs shellcheck; dirty deploys stay allowed because the KB workflow depends on them, but the receipt already flags them.

## Consequences

rollback has a matching DB; stale files stop entering the Mentor source snapshot; every deploy attempt ends with one terminal message as the fleet rules require.

## Resolves

ops-deploy-ci#1, #3, #5, #7, #8, store-data#3

## Depends on

nothing

## Status log

- 2026-09-07: proposed by the review; not yet discussed with the owner.
