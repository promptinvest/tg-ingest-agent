# ADR-0028 — pending resolver table and mixin contracts

Status: **deployed and accepted; independent closure and production tag verified**. Installed source
`125aaefe242134e6135291eb0d5cf32da59cdb8d`, archive SHA256
`1cb38c7dbba4c5f88ba988ed2875cc69294686a1204bb3a45ad74bdd1a36e4f6`, build
`2fec08f110b0`, deployment `5ea41d8ef1cbae19d4134d3f`.
[PR 18](https://github.com/promptinvest/tg-ingest-agent/pull/18) is merged as
`b239d749e811037730ce251f2ac520b4ac91569c`. Detailed operational receipts remain
private. Native acceptance, the independent audit and separate physical closure
and production-tag publication are complete.

`PENDING_RESOLVERS` selects the thirteen existing confirmation handlers on the
same Agent. Each kind-specific body is a pure move. The shared card/reply guards,
journal-correction precedence, cancellation cleanup, unknown-kind clear-only
fallback and outer `resolve_pending` reminder delivery wrapper retain their
existing contracts. Live-instance method lookup preserves patch and subclass
seams. The earlier media/conversation moves remain intact; no additional runtime
module or service is introduced.

A static check covers required mixin `self` dependencies across the complete
Agent MRO, including required literal `getattr` reads. Class members and instance
declarations can supply a dependency; optional probes remain optional. Positive
and negative controls check the guard. This proves declaration coverage, not
initialization on every execution path.

Exact-source Linux verification passed 216 focused checks and 3,470 full checks
in each normal/shuffled order, with nine skips retained per full run. Ruff,
compile, shell syntax, ShellCheck, helper suites and all five installed-layout
scenarios passed. [Source CI](https://github.com/promptinvest/tg-ingest-agent/actions/runs/41593508555)
passed. All 383 committed source files match the actually tested bytes; all 117
original test/support files and their assertions remain unchanged. The
[verification summary](cara-adr28-release-verification-2026-11-03.json) retains
source verification separately from native acceptance and final closure.

The first operator qualification failed on stale synthetic fixture identities
and an outdated diagnostic label. The reviewed successor preserves all failure
assertions, makes the fake candidate distinct from the accepted baseline and
corrects that label. It passed 139 strict operator checks, all 139 unchanged
historical checks and 27 transport checks, plus three actual cgroup cleanup
cases, two nonroot import roles and the actual preparer timeout/KILL/reaping
fixture. The failed qualification remains preserved.

The first read-only preparation stopped before native authorization,
materialization, controller dispatch or any service action. Its inherited
protected map omitted four MCP qualification files from the accepted ADR-0024
release. A separate metadata-only diagnostic proved all 136 original entries
exact, with no missing or changed entry. The four additions exactly match the
accepted state's hash-bound `approved_changes`, including ownership and modes.
The original template, failed root, consumed claim and child return code 1 remain
unchanged; the actual child/group absence was independently observed.

A fresh preparation continuation retained the strict thirteen-file native
operator, all original deployment gates and the original consumed claim/root.
Only the disjoint four-entry accepted metadata overlay was added. Its 45-check
qualification and independent review passed, with new identities and a new
bounded window. It cites the same implementation request and standing deployment
rule; it claims no new owner approval, old-claim reset or old controller replay.
Fresh exact-source materialization and input verification passed before native
dispatch.

All 21 native release phases passed. Canonical acceptance completed at
**08:04:41.793556 UTC**, SHA256
`39d6f0f3cd27500896e835fb225ee11703eecf696da8cf570cbdc5517ef9620a`.
The independent read-only audit passed at **08:07:18.879686 UTC**, SHA256
`a73d7c78ec8c922f945d6968a719d7a9538710c12cbfee8fa4dbfe435f3e1db6`.
The verified native manifest is
`95d6906e5e3077c1ec7922051043ea865a7a8176fa4d840385d107a274c7b089`.
Strict accounting reports zero current unknowns and retains all 40 historical
unknown rows without refund, reset or reclassification. Original schema 56,
stored history, usage/cache, protected configuration, models, prompts, budgets
and peer state remain preserved. The durable deployment notice is observed sent
once. The actual native controller, supervisor, guardian and foreground waits
returned zero; the independent audit's native and foreground waits also
returned zero.

The first final physical observer refused at its `/dev/null` standard-I/O audit
guard before the public unit query, with no operational effects. Its failure and
foreground return code 1 remain preserved; it produced no success receipt.
A separately reviewed successor observer passed its six fixture/static checks,
qualification SHA256
`4d485502dfd98fdcc9d2893ce0462d6a92d0f4d87d562e67b552bb348defd4c3`.
Its actual foreground wait returned zero. Independent final physical closure
passed at **08:22:25.484564 UTC** as SHA256
`951bb1621d174a2ee20f7856aa81aced6965a26c62c9bf37f6d043ec54fe9c7b`:
the recorded native controller, supervisor, guardian and audit generations and
their groups are absent; actual recorded waits/kernel results are zero and the
owned tool cgroup is absent. All four business/speech services remain healthy.
This read-only observation made no service, model, message or database mutation.

Annotated tag
[`production-2026-11-03-adr28-pending-resolvers`](https://github.com/promptinvest/tg-ingest-agent/tree/production-2026-11-03-adr28-pending-resolvers),
object `bb03d09c97dca9d75b9b55cac479051ff9c9a04e`, is published and remotely
verified to peel to the exact accepted source. All 15 earlier tags and all 29
earlier tag refs remain exact; only the new annotation and peeled refs were added.
The later documentation publication leaves the installed runtime unchanged.

Existing confirmation behavior, stored history, schema, models, prompts,
capabilities, budgets, backup policy and notification preferences remain within
their existing boundaries. Independent backup-key custody and host-loss recovery
remain unproved. ADR-0022 remains postponed pending its separate design decision.
No consumed exception, controller or protected recovery operation is reopened.
This dated evidence does not promise uninterrupted future health.
