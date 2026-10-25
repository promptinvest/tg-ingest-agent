# Cara notification and gratitude fixes — 2026-10-25

## Scope and current status

The owner requested review and fixes to notifications and gratitude handling.
This extends the existing reminder and journal paths (ADR-0004/0005), with no new
skill/process. Source implementation is ready for full qualification; production
remains the accepted October 24 application until fresh guarded acceptance.
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

Final canonical Git archive, full normal/shuffled distro-Python tests, compilation,
undefined-name checks, shell syntax/shellcheck and exact public-head CI remain
required. Preserve all original 47 regression specifications and 15 MCP/parity
scenarios byte-for-byte and execute their existing assertions. Protocol checks
alone do not qualify business outcomes. Original failed attempts remain retained.

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

Deployment acceptance, selected source/archive and terminal receipt are pending.
Existing incorrect entries remain unchanged; normal future preview/confirmation
is required for a correction. Finite deterministic coverage does not establish
universal RU/EN understanding or live owner-turn latency. No manual test messages
or model calls have been sent by these tests.
