# Cara fleet increment 06 - October 24, 2026

Status: narrow compatibility and acceptance-proof candidate; not deployed.
Published base is `fca4ac4ab4fe6dc611b27465ca2ebaf442dfbc15`. Production remains
accepted `99037c5` / build `f54906e304fe`, MCP disabled. The owner implementation
request and standing implementation-includes-deployment rule authorize guarded
release within scope, not a separate hash-specific approval. Final source,
canonical archive, operator package and active-session window are UNSELECTED.

## Existing-effect compatibility

The per-unit restart hold from [increment05](cara-fleet-increment-05-2026-10-24.md)
also needs to respect token evidence written before that hold existed. The gateway
now reads bounded old history under the same fixed resource lock before a new
fingerprint, host-token consumption or effect. Executing/ambiguous same-unit
records block fresh tokens even without a modern hold. A known final receipt can
permit a later fresh approval; valid other-unit records remain independent.
Malformed, unsafe or unscoped evidence fails closed.

The scan is limited to 4,096 entries, 4 MiB aggregate and 16 KiB per record. It never
rewrites old bytes, inodes or modification times, migrates markers, refreshes a
lease or clears an outcome by time/fingerprint. Existing consumed tokens retain
their ambiguous/rejected duplicate meaning. Source/card/fingerprint/expiry,
double-token protection and the bounded existing restart are unchanged. No new
effect, maintenance capability, clear-unknown tool or MCP write is exposed.

Old token/resource state remains minimal non-content operational evidence across
content purge and recovery. Normal conversation DB backup is not a host-ledger
restore mechanism. Actual gateway rollout must preserve full protected ledger
presence/absence and file metadata/hashes with checked backup/manifests. Fixed
module/policy/sudo/key/relay preservation alone does not satisfy this gate.

## Useful development revision after final cleanup

The typed renderer previously emitted a full public repository revision, which
the final generic secret scrubber replaced with***. The answer now displays a
12-character prefix from that validated revision, matching existing CI reference
style. The complete revision remains in typed evidence; global credential patterns
and source privacy remain. An actual admitted selected-project Agent answer with
CI disconnected verifies the visible prefix, exact retained full revision, no
full long-hash disclosure and a precise unconnected-CI gap. Existing source dates,
Actions update/completion distinctions, names, issue details and coverage remain.

## Executable acceptance proofs

Three added process tests use actual fork/os._exit and event-only effect doubles:
crash after a durable hold before token creation, after token/directory fsync
before dispatch, and after synthetic effect completion before final receipt.
New tokens remain resource_unknown without fingerprint, effect or new host token;
consumed old tokens remain ambiguous and original evidence bytes stay exact.
The existing known-final-receipt/hold-settlement crash proof remains separately.
No real service operation is used as a demonstration.

Three new real Agent.run/update-batch/dispatch/scheduler tests use synchronization
and fake UTC advancing in actual two-second poll increments. Planner and synthesis
are held for 90 logical seconds; two executor completions occur at the same
logical instant before two held background syntheses under the unchanged one-slot
bound. Owner updates and due reminders continue within two logical seconds, ready
phases are consumed within one pending poll, normal 50-second idle returns and
original call/accounting counts remain. No 90-second wall-clock sleep.

The new parity suite runs all19 source-linked catalog cases with their original
check_case assertions intact through disabled and enabled Agent paths. It compares
actual answer obligations/pages and equivalent typed MCP cells, preserving direct
legacy roster shortcuts and inert clarifications. Two actual base-MCP admissions
continue through owner planning, spool, executor/gateway selected-project reads
and useful backup/development/runtime answers, including CI disconnected. Five
retained adapter variants compare exact internal/wire typed observations, dates,
uncertainty and scope: backup, development, Actions development, application
observer and authenticated proxy route. No unrelated colocated project or host
CPU is substituted, and no extra model/probe is introduced by the interface.

All original47 and additional15 JSON bytes remain unchanged. Unrelated transaction
and lifecycle cases remain in their full original suites. Frozen semantic choices
qualify deterministic business paths, not unrestricted model language behavior.

## Focused evidence and retained corrections

| Cohort | Result | Retained log SHA256 |
|---|---|---|
| Legacy13 + resource20 + existing fleet60 + original poll7 + new waits3 | 103 PASS / 6.298 s | `9adb880b4834ff909d65f40208f8b0b098a85857e97ea9db851949977c4deb36` |
| Additional three early process-crash boundaries | 3 PASS / 0.052 s | `1c92618fe48d2d5439c26e685a5c1fdc28b320327781ae1c87f97a78e986a913` |
| Full applicable catalog/fresh-project/retained internal-MCP parity | 26 PASS / 8.465 s | `b75f80da66ec44b4d9a3529e3f2d9f697fd218573f6d395d7d27de32b3473859` |
| Corrected original-seven plus new-three poll/waits checkpoint (included above) | 10 PASS / 1.015 s | `42104fe224c101b0f7bf5d702d1de98c7e6ef3380c48c203f74a4fddf5c5e8ef` |

All runs use isolated Linux, networking disabled, an empty environment and private
temporary storage; no paid model, production transport, Telegram or live effect.
The cohorts overlap and bind their own source manifests; do not add them as a
unique full-suite count or treat them as final combined-source qualification.

The legacy cohort binds fleet_host.py
`9cdd418c29703c3c777b387f7e778c52f607e8c6c1a3c7dcd7ff4e1ebe30370d`
and test_fleet_resource_legacy.py
`3dcbe5e263609381fb63f112ac745aa7417ba45bc72dd1477f4265f7a6c72e88`.
The parity cohort binds fleet_report.py
`e5048d88f255bdf574cc74c408ae784b7b5d44b24c57053e7497d6350a3a1fc2`
and test_fleet_mcp_full_parity.py
`623a0deb0d27a70c20bc1fedeabea9dc63a55fd8f6565113be3975ed49b88294`.
The additional crash fixture is
`1ca936b065388a0e145c2fe41fcb39e3577a56a06729d6004423d15fbbbe0cd4`.

During wait-fixture preparation the new tests collided with an existing test
filename. The original seven were restored byte-for-byte before publication;
new tests moved to test_fleet_responsiveness_waits.py, then both were qualified
together. The earlier three-only checkpoint is retained and is not the combined
proof. Final original polling module SHA256 remains
`4f61d3d8065c20436017b518f688d6fa0e693bd7c12ff4dd01234732b18beca7`.

The first new parity run passed24 of25 and failed an incorrect new expectation
that a private archive matching basename would be printed. The corrected fixture
checks exact success/restore dates, integrity, matching coverage and absence of
that private basename; all original source-case assertions remain. An intermediate
preparation was unlaunched when the actual revision-display defect was found.
The final26-case run includes its narrow fix and disconnected-CI assertion.
The extra crash fixture also received a valid InvocationID before its fresh final
run. All original sources, logs and receipts remain; none is replayed or erased.

Published increment05 `fca4ac4ab4fe6dc611b27465ca2ebaf442dfbc15` passes
[CI run 40093075719](https://github.com/promptinvest/tg-ingest-agent/actions/runs/40093075719).
That is earlier-source CI, not CI for this uncommitted candidate.

## Protocol and independent acceptance

Official [discovery](https://modelcontextprotocol.io/specification/2026-07-28/server/discover)
and [stdio transport](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports)
references were rechecked October24. The selected stdlib pair remains MCP
2026-07-28 with per-request metadata, checked discovery and bounded newline UTF-8
stdio. Actual isolated stdio tests qualify that pair; no SDK dependency, broad
external-client compatibility or unqualified legacy-version downgrade is claimed.
Tasks remains explicitly unsupported and unnecessary for base async. A2A/new
write exposure remains deferred/excluded, not an unimplemented requirement of
this read-only package.

All seven packages remain open. CF-01 retains authenticated source-status/review/
forward-publication and history/privacy acceptance, including planned facts
revision2 only after final app acceptance and private KB update. CF-02/03 need
fresh bounded accounted RU/EN, correction, format, omission and entailment evidence.
CF-04 needs final combined phase/accounting/recovery and installed readiness.
CF-05 needs actual fixed source/connector bindings and useful dated answers under
real gateway identity. CF-06 needs full authority/purge/backup/sandbox acceptance
and complete protected host-ledger preservation. CF-07 needs exact business,
protocol and installed client/policy evidence separately. No original package
depends on MCP adoption, and protocol success cannot close business acceptance.

| Final release field | State |
|---|---|
| Exact target commit and canonical archive | UNSELECTED |
| Final combined/full normal/shuffled Linux, pinned lint and CI | PENDING for frozen source |
| Required bounded accounted actual-model qualification | PENDING for frozen source |
| Actual fixed project sources/public connector capture and dated business answers | PENDING where not previously accepted |
| Actual service namespace/UID/groups/stdio/source-policy hashes and secret denials | PENDING |
| Protected gateway/complete host-ledger backups, file/unit preservation and receipts | PENDING |
| Checked online/stopped application backups, original rows/columns, environment and recovery parity | PENDING |
| Loaded main/executor readiness, neighbors and monitor/alert/retention bindings | PENDING |
| Fresh single-use operator package and bounded active-session window | UNSELECTED |
| Deployment, final acceptance and package closure | Not started; all packages open |

Use only the exact verified source through the original paused transition,
backup/stopped-data/readiness/monitor-binding and forward-only containment gates.
Watched operator inputs arrive atomically. Keep current source/database/evidence
after failures; never replay controllers, historical updates, unknown effects or
ambiguous delivery. Preserve all accepted ordinary skills, data, spend, preferences,
confirmation/backup/recovery behavior and notification opt-outs. No manual Telegram
test, paid corpus work or live maintenance demonstration.
