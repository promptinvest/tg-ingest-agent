# ADR-0033: Fleet knowledge and project capability development backlog

- Status: Proposed; backlog created 2026-10-24.
- Owner request: create a backlog for further development after a fleet behavior/architecture review.
- Implementation authorization: no implementation is claimed by this planning record.

## Context and direction

The review identified missing descriptive fleet facts/synchronization, inspection-only query handling, rigid technical reports, content-erasing output cleanup, poll/model responsiveness gaps and incomplete project/task lifecycle boundaries.

The owner explicitly wants Cara to use the existing LLM for free-form question interpretation and an appropriate reply format. Known facts should not require host probes. The proposed architecture retains closed tools, grounded evidence, owner-bound scope, budget/approval/recovery rules and existing behavior outside approved changes.

Extend the existing fleet skill with a sanitized facts view, semantic query/answer contracts, adaptive grounded presentation, responsive durable I/O and project-scoped read adapters. Integrate task/effect lifecycle before broader project writes. No new daemon, provider or generic shell tool is a prerequisite.

## Backlog and consequences

[Complete developer handoff](../backlog/cara-fleet-2026-10-24/README.md);
[parent tracker CF-00](https://github.com/promptinvest/tg-ingest-agent/issues/7).
The handoff contains the original six business package specs plus downstream CF-07, dependency/release requirements, the preserved 47 business regression specifications and 15 additional MCP/parity specifications.

This record leaves ADR-0031/0032 and historical release claims intact. CF-03 proposes revising exhaustive report presentation while preserving ADR-0032's factual/scope/uncertainty safeguards. Each implementing change must separately record its accepted decision and update both primary specifications.

Public files contain no real fleet bindings or private operational evidence; production inputs remain operator-owned. Creating this backlog changes neither runtime nor permissions.

## MCP extension and preservation of business goals

The owner requested adding MCP planning while preserving the backlog business purposes and goals. CF-07 adds a thin read-only interface and explicit Cara client over shared capabilities. CF-01–CF-06 retain their original priorities, acceptance criteria and independent release gates; none depends on MCP. CF-05/06 expose reusable capability/policy contracts without adding protocol prerequisites. A2A and new write interfaces remain future scoped work.

## Resolves and dependencies

Tracks review findings F01-F07 through CF-01..CF-06; CF-07 consumes their qualified outcomes. Dependencies and independently startable increments are in the handoff. It supplements ADR-0031/0032 and coordinates with the existing responsiveness, routing, test and documentation backlog.

## Status log

- 2026-10-24: owner requested durable development backlog; proposed work, fixtures and trackers prepared. No runtime implementation, paid qualification or deployment.
- 2026-10-24: owner requested the MCP backlog extension with explicit preservation of existing business goals; CF-07 and additional parity scenarios added. Planning only.
