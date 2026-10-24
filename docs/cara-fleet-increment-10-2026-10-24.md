# Cara fleet increment 10 - October 24, 2026

Status: CF-02 collection correction and additional business parity; not deployed.
Production remains accepted source `99037c5` / build `f54906e304fe`, MCP disabled.
All CF-01 through CF-07 acceptance criteria remain open.

## Confirmed collection defect and correction

Source `9505c60` passes full Linux and CI. Its model returns a concise memory-only
plan with focus:[memory], projects:[], scope:explicit and resources, while omitting
optional read_scope. The existing semantic mixin returns the base legacy plan,
which unconditionally adds projects. Output filtering produces a correct RAM-only
answer but still collects unrelated colocated release/container/health metadata.
This fails CF-02's requested-evidence obligation; the answer alone is insufficient.

For newly validated semantic inspect plans, explicit narrow memory/cpu/disk/
services/errors/trend focus without project targets or an inherited scoped
obligation now derives kind:host with empty projects/capabilities. The established
scoped executor/gateway removes the injected project facet and prevents project
reads. Do not normalize raw model focus values or grant new target authority.

The base validator, stored envelopes/results, broad resources/overview, explicit
project/all-project scope, inherited obligations and restart/card semantics stay
compatible. The fresh narrow semantic legacy-test expectation is revised for this
specifically requested behavior; its underlying base parser/wire coverage remains.
No model prompt/profile/provider, budget, host probe, account, schedule, listener,
write exposure, live maintenance demonstration or manual message is introduced.

## Retained qualification evidence and limits

Source `9505c60` complete isolated Linux checks pass: compile 1.9459 s,
normal 160.9497 s and shuffled 164.4920 s; 2,783 tests, nine archive checkout skips.
Receipt SHA256: `43bf9a83e442f8e94e88f97c34e132072f9a4bc1d26589620219f224dc30f1d5`.
[CI 40097770216](https://github.com/promptinvest/tg-ingest-agent/actions/runs/40097770216)
passes every step. This evidence belongs to the base, not this changed source.

The first base model cohort stops after five known calls: its synthetic evidence
omits selected unavailable facets, causing a legitimate additional read/synthesis
that violates the harness's expected call count. Keep failed raw SHA256
`9c49ae4fc158ca88043061136b604c59a125c374164a070949776751de756ab0`
and USD 0.01550688. A three-line private fixture correction adds explicit unavailable
metadata only; cached proof confirms identical model-visible evidence and unchanged
business/assertion requirements. It is not actual model qualification.

A new actual cohort stops at phase eight after seven passing phases, including
strong concise-memory and development-table assertions. The model returns an
unsupported focus:[runtime]; strict validation rejects it. Keep failed raw SHA256
`7fab11e8beb7aae64e159674703bfc63ca52cb591a4886877e46356505005929`
and all eight known calls, USD 0.025008672. Existing AsyncMixin permits exactly one
budgeted schema-repair planning attempt; this private harness had called validation
directly and did not exercise that existing path. An offline actual-async proof
exercises invalid plan, existing repair, typed dated runtime evidence and retained
result, with two planning attempts/three total phases and no actual model, host or
message. It cannot qualify this changed source or turn either failed raw into a pass.

Known aggregate model qualification spend is USD 0.12944208 under the unchanged
USD 0.15 cap. Every earlier failed cohort, usage row and original status remains.
No further paid call accompanies this source correction. New-source accounted
qualification must preserve the cap and disclose fresh versus retained evidence;
no first-pass, nine-fresh-call or universal language success is claimed here.

## Additive parity and current-source checks

A private source-pinned base overlay passes 104 tests in 12.480 s, including the
new paired negative-runtime/facts/preference cases and existing original/MCP
runners. Log SHA256:
`d718954098c64f79317e26de956f26501f4f06d434680679f9de58696b0251b9`.
The new public test module retains each original source assertion, compares exact
base final pages, scoped retained observations and canonical stored re-rendering,
and covers stale health, backup timers, inactive/unknown units, application versus
container health, proxy versus host load, stability and false cited backup claims.
Original 47 and additional 15 specification files are unchanged. These deterministic
results do not claim 62 real-model questions or installed transport acceptance.

The changed source's focused overlay passes 219 tests in 68.039 s, with no
failures or skips, in an isolated Linux network namespace and empty environment.
It covers the actual Agent, request spool, executor run_once, gateway, real receipt
poll and concise RAM answer, asserting zero unrelated project/HTTP/container/unit
reads. Existing broad, project, async, task, answer, catalog and legacy fleet cases
pass with their original assertions. Log SHA256:
`7f35d4d64754647e6568596ab49593bea46bb05f9f66f805236ad1b7911b339e`.

Complete canonical Linux/CI, model and installed release qualification remain
pending. Preserve exact source/archive receipts,
protected backups and stopped original data/schema parity, real service identities/
groups/namespace/stdio, denial and selected-scope checks, original monitor/alert/
retention/recovery policy, authenticated one-shot gateway transitions and final
acceptance. Never replay a started/completed controller or unknown operation.
The implementation request and standing deployment rule remain the authorization;
no redundant owner hash/window permission is required.
