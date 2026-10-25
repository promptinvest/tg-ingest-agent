# Cara notification and gratitude fixes — 2026-10-25

## Scope and current status

The owner requested review and fixes to notifications and gratitude handling.
This extends the existing reminder and journal paths (ADR-0004/0005), with no new
skill/process. Deployment was accepted **2026-10-25 09:08:55 UTC** for `dcdd5ee`.
The exact published source and guarded native transition both passed.
The standing implementation-includes-deployment rule authorizes the transition;
there is no separate hash-specific approval claim.

Live review confirmed delayed gratitude context loss, retrospective correction
text captured as a new journal entry and pronouns treated as named people. Private
operator evidence stays outside Git. Existing records are preserved, not silently
repaired. Synthetic regressions reproduce the business defects on the original
committed source without resetting the current checkout.

## Implemented behavior

- Delivered gratitude invitations retain occurrence date and conversation boundary.
  Same-day delayed unthreaded content can recover the latest uninterrupted context;
  explicit old-card content retains its original date. Late notices name the date.
- Retrospective correction resolves an exact owner source, previews raw text,
  category/date/fields and requires card/nonce/source-bound confirmation. Ambiguous
  duplicates ask for a target. The transaction preserves raw text, receipt time,
  entry identity/creation time and complete prior journal metadata in feedback.
- Existing entry corrections default to the existing event date. Preview, saved
  storage, acknowledgement and edited card agree. Invalid dates keep the offer
  editable; ordinary category corrections remain available. Unrelated live
  reminder context retains its original expiry after correction or cancellation.
- Pronoun-only values are excluded from named people and person statistics while
  names/family groups remain. Legacy payloads are not rewritten.
- Recurring notification actions use the exact firing event; an old card cannot
  acknowledge a later occurrence. Legacy unknown/ambiguous recurring bindings
  refuse mutation; one-shot actions keep existing behavior. Failed notification
  delivery creates no delivered binding. Ordinary questions and commands retain
  their routes; no new model stage is introduced.

## Verification evidence

The original-source negative qualification reproduces three outcome failures:
pronoun/name output, old-card preview date and correction-as-new-entry. It records
assertion failures, not missing new APIs. The reviewed source passes 141 focused
Linux tests covering new outcomes and existing reminder/review behavior. Three
fixture corrections retain all assertions: Telegram timestamps have second
precision, source-race tests change actual hashed source and fired fixtures include
real durable fired events. Coverage also preserves explicit new-entry prefixes, unstructured journals and
explanation requests; it verifies stale-date refusal restores unrelated pending
context and that previews display real line breaks. Independent narrow source
review cleared the final code; test evidence remains a separate gate.

The first published CI (`84a1899`, run 40178152480) caught an undeclared final
verb in the date-clarification template; 2,893 tests ran with one catalogue error
and 62 optional-environment skips. Original logs remain. The clarification now
makes no completion claim, and the correction success template explicitly maps
to confirmed state after its atomic transaction. The original catalogue assertion
is unchanged. This adds the reviewed lifecycle declaration in `action_truth.py`
to the eight-file production delta; model-facing prompts/calls remain exact.

Final canonical archive `50cb0d0c65fad2d05d229d487b659a72da9b6c894fb604a465cba5cbca670b3b`
is from committed source `dcdd5ee95c2bb8fd86a4ab861548d6d6078b7311`.
Both distro-Python Linux orders pass **2,893 tests, nine unchanged archive-checkout
skips**, in 232.327 / 173.488 seconds. Compilation and all shell syntax checks pass.
[Exact-source CI](https://github.com/promptinvest/tg-ingest-agent/actions/runs/40178337080)
passes all job steps including undefined-name checks, normal/shuffled tests and
shellcheck. Its hosted optional-environment skip count is distinct from the VPS.
The original 47 regression specifications and 15 MCP/parity files remain byte-exact
and their existing assertions execute in the full suite.

All **375 unchanged original operator regressions** plus their unchanged actual
binder input rehearsal pass against their original source. Separately, **37 fresh
operator fixtures** and the complete actual new-source prepared-input handoff pass;
neither claims a live deployment. The real guarded installer/runtime acceptance
below supplies that evidence. Original failures are retained: the first operator
harness correctly refused a world-writable ancestor; a subsequent fixture assumed
CRLF in a baseline that contains LF. The corrected fixture introduces CRLF and
still requires byte-delta refusal. No assertions or protection checks were relaxed.

Full verification receipt SHA256:
`be63d6a936c78f3a0105ba99cda8401e9df2692a22a941caeb8a2e259525faf0`.

The extractor prompt/profile and all model/provider/call-budget/inference modules
remain unchanged. No new paid qualification is needed for deterministic handling;
retain the sealed prior finite model evidence against its original source plus
an independent proof of unchanged model-facing code. The prior qualification
ceiling and Cara production budgets remain unchanged; no model calls are part of
this task's deterministic tests.

## Release procedure and gates

1. Freeze/push the exact reviewed source and qualify its canonical archive in
   isolated Linux, with clean environment/network namespace and bounded resources.
   Bind exact CI, installed-module delta and unchanged extraction/fleet semantics.
2. Use a fresh single-use operator with a new nonce and bounded active-session
   window. Never replay historical installer/controllers, completed source/facts
   publishers, deployment notices or recovery adapters.
3. Capture current idle/health, source, database/schema, environment, protection,
   monitor/readiness and unrelated-service identities. Preserve all original
   backups, recovery files, consumed authority/ledgers, current/last-good facts,
   accepted policies, budgets and notification preferences.
4. Require protected stopped database/source backups and parity before install;
   preserve the original installer and native mark/restart/once-only deployment
   notice, without package installation/upgrades. Rebind and verify all existing
   readiness/monitor contracts, strict nonroot namespace, health, worker, spool,
   SQLite and exact installed-source checks. Keep recovery forward-only.
5. Accept only after fresh runtime checks and bounded preservation review. No
   manual Telegram test, historical-update replay, live maintenance demonstration,
   data repair/purge, new write exposure or automatic facts/development refresh.

## Deployment evidence and limitations

Accepted **2026-10-25T09:08:55.895719+00:00** (12:08:55 Moscow).
Source `dcdd5ee95c2bb8fd86a4ab861548d6d6078b7311`, build `c39b8684b7db`,
native receipt `f5bad2f470066a136a80f297`; normal notice sent once on one attempt.
Acceptance SHA256:
`e52a961667eac9e2217da77788c4a100f71c8959b44439b9f96191a865d17fec`.
Controller SHA256:
`146e50ebbd9eb70e82fe41b2ea09e083efabff83059b3c22d5cf2897f39bff6d`.
It launched once in the selected 09:00–10:00 UTC active-session window. Never
replay it, the installer, marking operation or notice.

Fresh preflight, online/source/stopped backups and exact original 54-table
rows/columns/schema parity passed. All 89 installed modules match the selected
source (eight changed, 81 unchanged). Before/after final restart checks use actual
nonroot service identities, supplementary groups and mount namespaces; MCP60
facts obligations and all nine existing scoped-read readiness operations pass.
Three final health samples pass; Cara/executor/worker have zero restarts. Protected
environments, budgets/models, current/last-good facts2, source snapshots, alert /
retention policies, monitors, complete recovery material and unrelated services
remain preserved. No system package was installed/upgraded, gateway updated or source
publisher replayed. No uninterrupted-uptime claim is made.

The read-only postacceptance comparison finds 51 tables byte-identical in logical
rows and no removed or modified original rows in the remaining tables except
three expected KV runtime markers. The only additions are the new native receipt
and existing routine health-check usage, independently accounted under unchanged
production budgets. All note/journal/reminder/history/preferences remain exact.
Detailed operator, data and accounting evidence stays private.

Existing incorrect entries remain unchanged; normal future preview/confirmation
is required for correction. Finite deterministic coverage does not establish
universal RU/EN understanding or live owner-turn latency. Tests and new candidate
qualification made zero model calls; sealed prior model evidence is retained
against its original source. No manual Telegram test, historical update replay,
data repair/purge or completed-controller replay occurred. Existing manual facts
and development snapshots retain their original evidence dates.
