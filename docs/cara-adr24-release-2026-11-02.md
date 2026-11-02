# ADR-0024 — precedence, parameter contracts and latest-action undo

Status: **deployed and accepted**. Source `2b6da6ee0ca43f7c8aa1073fe5fddb069bfd91b2`,
archive SHA256 `be7b83b2d33b844c98ae02749fca7cc17d692e3d1c246533571a2f26f9161725`,
build `2b8c4237a9a4`. [PR 15](https://github.com/promptinvest/tg-ingest-agent/pull/15)
contains the application change. Detailed operational receipts remain private.

`PRE_ROUTER` declares guard order and pending-state ownership; the regression
matrix pins existing winners. Per-action parameter contracts reject unusable
supplied shapes while retaining missing-field defaults, established coercions,
safe handler fallbacks and ignored extension fields.

Say «отмени последнее действие» / “undo last action” within ten minutes to undo
the latest supported reminder close, reschedule or rename, note recategorization,
or note lifecycle change. The inverse binds stable targets and expected state,
survives restart, is consumed once and restores an entire batch atomically.
Changed targets and overdue rearming are refused. Journal membership, payloads
and dates restore while issued note numbers, categories and original history
remain. Content purges invalidate affected inverse records. Durable outcome and
delivery records prevent an associated known-unsent original success reply from
arriving after undo; sent and ambiguous delivery evidence remains.

Pending cards, task approvals and replied-card bindings retain ownership.
«Отмени», «верни как было», «не то», targeted reminder undo and learning undo keep
their existing meanings. No schema migration, router prompt, model or budget
change is included.

Fresh exact-source Linux verification passed 208 focused cases and 3,450 full
cases in each normal/shuffled run, retaining nine skips per full run. Static,
compile, shell, helper, all five installed-layout scenarios and
[source CI](https://github.com/promptinvest/tg-ingest-agent/actions/runs/41430788706)
passed. Original test/support bytes and regression assertions remain intact.
Release tooling passed operator, transport, nonroot import and actual cgroup and
timeout cleanup qualifications. The [verification summary](cara-adr24-release-verification-2026-11-02.json)
records the final results.

The first rollout stopped before installation when the existing learning-scan
clock advanced between online and stopped captures. The owner approved a guard
that permits only that canonical, strictly advancing timestamp inside the exact
capture-to-stop interval. All other schema, rows, usage and cache fields stay
exact. The fresh rollout passed this policy with one permitted advance and
retained strict stopped/live parity across all 56 tables.

Installation, native runtime, sandbox, initial namespace/MCP readiness, scoped
reads and data-preservation checks passed. The release controller then stopped
on a transient startup reservation; the requests settled normally without manual
accounting changes. A same-source availability helper later rejected an early
identity sample before reaching its stability wait. The services were subsequently
observed under their expected nonroot identities. That failure did
not prove physical containment: the new services remained running and settled
to their expected nonroot identities. All failed artifacts, windows and consumed
claims remain unchanged; none was replayed.

A newly reviewed read-only continuation pinned the running generation and a
fresh bounded verification window. It made zero business-service changes,
completed namespace readiness, fresh MCP, strict accounting, preservation, health
and sent-once-notice checks, then published its own acceptance. A separate final
read-only audit passed. Actual custody completed successfully and its owned
release processes were independently absent. Worker and speech generations,
stored history, protected configuration and historical usage remain preserved.
This dated acceptance establishes the observed release state, not uninterrupted
future health.
