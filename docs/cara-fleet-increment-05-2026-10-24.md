# Cara fleet increment 05 - October 24, 2026

Status: existing-restart resource guard implementation candidate; not deployed.
It follows published durable-MCP/task source `319dd95` and project-read source
`498cb11`. Production remains accepted `99037c5` / build `f54906e304fe`, with MCP
disabled. Earlier [increment04](cara-fleet-increment-04-2026-10-24.md) records its
separate focused evidence and retained failures.

The owner's backlog implementation request and standing implementation-includes-
deployment rule authorize the scoped guarded release. They are not a separate
hash-specific approval. Final source, canonical archive, operator package and
active-session window are UNSELECTED. No deployment, live restart or purge is
claimed for this increment. All CF packages remain open.

## Existing business behavior protected

The existing exact-card worker restart is the only affected effect. A second
independently approved request for the same unit cannot overlap the first one.
It receives a useful busy explanation instead of inspecting a new fingerprint,
consuming a new host token or starting another restart. Unrelated resource keys
do not share this lock; the existing global/per-host executor bounds still apply.

A released OS lock is insufficient after a crash. Durable unresolved resource
state blocks a new token until the previous attempt's outcome is known. Closed
RU/EN result text distinguishes a new request not started because the resource
is busy, a previous outcome needs reconciliation, or authorization expired. A
new-request failure does not claim that the previous uncertain effect stopped.

Source/card/inventory/fingerprint/expiry and both existing token boundaries keep
their original meanings. The host checks expiry after fingerprint collection and
again immediately before the unchanged bounded systemctl restart. The existing
20-second command timeout remains; timeout is not proof that the effect did not
happen. No retry is inferred from an absent result.

## Lock, start boundary and durable outcome

fleet_host derives a fixed resource identity from operation restart and the
allowlisted service. A nonblocking fcntl lock uses a stable inode under the fixed
protected host ledger. A different same-unit token receives resource_busy before
fingerprint or host-token consumption. Already-consumed-token behavior remains
ambiguous/rejected and cannot authorize another attempt.

Before host-token consumption, the gateway publishes and fsyncs a separate minimal
resource hold, then preserves the existing durable token-consumption boundary.
Executing or unknown state survives process death, timeout and OS-lock release.
The result receipt is durable before the resource can be marked settled. A known
failed result can settle an attempted operation; an ambiguous result cannot.

After a receipt/hold-publication crash, automatic reconciliation accepts only the
durable known succeeded/failed receipt for that exact held token and same unit.
A new token's receipt, a changed fingerprint, current health, elapsed time or an
expired lease is not settlement. Missing, malformed, unsafe or ambiguous evidence
fails closed. No force flag, clear-unknown tool, timer or second effect attempt is
introduced.

Other unknown-outcome reconciliation remains an operator evidence problem under
the existing recovery process. Establish and retain what actually happened before
a later effect; that later effect requires fresh original approval and current
fingerprint/expiry. This increment provides no automated ledger-clearing operation
and does not authorize manufacturing a final receipt or replaying an old token.

## Protected operational residue and compatibility

Descriptor-relative no-follow opens protect the fixed ledger and resource files.
The new hold contains only contract version, fixed service, opaque token, state
and timestamp. Stable lock files and holds are non-content operational evidence,
not owner questions, answers, task payloads or approval prose. They do not create
another task store or change the conversation schema.

Preserve this evidence beside existing consumed-token tombstones through typed
content purge, process restart and host recovery. It has no automatic time-based
clearance. Existing spend/preferences, content preview/quiescence, normal database
backup policy and release history remain unchanged. The host ledger is outside
the conversation database; checked host preservation/recovery must retain it.
Restoring old markers or deleting an unknown hold cannot grant new authority.

Legacy reads, envelopes, receipts, cards, tokens and unsupported-effect behavior
remain compatible. Main/worker/executor credential separation and existing
transport/sandbox bounds remain. MCP has no restart or protected project-write
exposure. No new runbook, shell, deployment, cleanup, database, backup-start,
generation or messaging effect is added. CF-01 through CF-06 do not depend on MCP.

## Verification status

Network-isolated Linux with an empty environment passes 80 tests in 4.645 s:
20 new resource-lock cases and 60 existing fleet cases (attempt 3). The log SHA256
is `d5861cad8c22d7a3c80a24038154ed713ded2cb92de2e73caaea0ad50c5cc0f9`.
The qualified fleet_host.py SHA256 is
`392f805ca850e713264c16f464bfece0efe7a5eb646b5d4eaae002178638db73`.
These bind the focused checkpoint, not a selected final release/archive.

The fixtures exercise real independent-process locks with synthetic command
doubles: busy before fingerprint/token, independent units, consumed duplicates,
gateway death while a subordinate remains active, timeout ambiguity and durable
unknown, known final-receipt/hold crash reconciliation, fingerprint drift, both
expiry boundaries, protected path/file guards and closed RU/EN replies. No actual
service restart is used.

Attempt 1's fixture FD-inheritance failure is retained. The fixture correction
reproduces the real subprocess descriptor boundary without changing production.
Attempt 2 passed; the final attempt 3 uses a valid changed fingerprint and retains
the original drift rejection assertion. No failed original is erased or replayed.
This pass does not close every CF-06 crash-boundary/restore criterion. The separate
CF-04 logical-90-second and simultaneous-completion proof is pending for a later
verification increment; it is not included in this 80-test total.

Published increment04 `319dd957bad6416463e7dea67111e30f0a1a6431` passes
[CI run 40092662581](https://github.com/promptinvest/tg-ingest-agent/actions/runs/40092662581).
That CI belongs to increment04, not this uncommitted increment05 source. Final
frozen-source full/model/CI/installed/operator qualification remains pending.

Original 47 and additional 15 specification files remain unchanged, with
executable source-linked business assertions from increment04. Preserve all
source hashes and original assertions. No live maintenance/purge demonstration,
manual Telegram message, model/provider change, budget increase or paid corpus
work is part of testing.

## Independent acceptance and release

| Package | Increment05 contribution | Remaining acceptance |
|---|---|---|
| CF-01 | No descriptive authority change | Independent source/publication/history/privacy and compatibility criteria |
| CF-02 | Closed existing-effect result reasons | Requested RU/EN facts/context/format and bounded model qualification |
| CF-03 | Useful busy/unknown/expired explanation | Entailment/omission/preferences and full compatibility |
| CF-04 | Cross-process effect exclusion and truthful unresolved outcome | Final all-phase/cancellation/accounting/recovery and installed readiness |
| CF-05 | Existing project reads retained | Fixed source bindings, dated useful answers/gaps and actual identity checks |
| CF-06 | Existing-restart resource lock and durable unknown hold | New crash/path/concurrency fixtures plus full authority/task/purge/backup/sandbox audit |
| CF-07 | Existing read-only consumer retained; no write exposure | Exact full/model/CI and actual policy/stdio/readiness/optional activation |

| Final release field | State |
|---|---|
| Exact target commit and canonical archive | UNSELECTED |
| Focused resource-lock/reply qualification | PASS: 80 tests / 4.645 s (20 new + 60 existing), attempt 3 |
| Complete CF-06 crash/recovery and separate CF-04 timing acceptance | PENDING independent audit/proof |
| Full normal/shuffled Linux, pinned lint and CI | PENDING for final source |
| Required bounded accounted model qualification | PENDING for final source |
| Actual gateway source/policy/ledger protection and secret/write denials | PENDING |
| Fixed project source bindings and business-answer checks | PENDING where not previously accepted |
| Checked online/stopped backups, original rows/columns, environment and recovery parity | PENDING |
| Main/executor loaded-source readiness, neighbors and monitor/alert/retention bindings | PENDING |
| Fresh single-use operator package and active-session window | UNSELECTED |
| Deployment, acceptance and package closure | Not started; all packages open |

Release only the exact verified source through the existing paused transition,
checked backups/stopped parity, readiness, monitor-binding and forward-only
containment gates. Preserve the installed source and all evidence after a failure;
never replay started/completed controllers, unknown effects or historical updates.
Watched operator inputs arrive atomically. Source publication alone does not
activate MCP or prove a business package complete.
