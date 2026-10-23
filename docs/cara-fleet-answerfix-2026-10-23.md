# Cara fleet inventory and identity correction - October 23, 2026

Accepted 2026-10-23T11:32:46.882938+00:00. Source `54b7e15aa100672d17544131aeb3a73692c248e6`;
build `86db8e9d2812`; receipt `902d076941789cfaa6a57743`.
The owner reported “check her answers - they're completely wrong and weird”.
This fixes the observed failure of the already-approved fleet implementation;
deployment follows the existing implementation authorization and standing rule.
No separate hash-specific approval is claimed.

## Observed failures and resulting behavior

The actual request “Напиши список моих VPS” was classified as `vps_stats` and
returned only PD resource measurements. “Это какой сервер?” then went to
`converse`, which invented `ru-1`, an operating-system version and other locations.
No fleet request existed for either turn. Conversation formatting also stripped
Markdown host names into empty asterisks. The first release's transport, contract
and five planning checks missed this basic inventory-and-identity exchange.

Narrow RU/EN list and identity questions now take a deterministic read path through
the existing fleet skill, before a model prompt is built. Lists render actual
configured IDs/names as plain text, without invoking models or SSH. Local stats
name PD while preserving all existing measurements and the exact disabled-feature
output. Identity follows the actual prior observation's host scope, including a
real legacy `vps_stats` trace. An ambiguous multi-host context asks for a target;
it never substitutes the assistant's local host. Missing identity stays unknown.

The general-chat fallback also delegates direct inventory/identity questions to
the fleet skill. Other conversation receives configured host names/roles as
identity authority and is instructed to treat older assistant claims and retrieved
notes as unverified infrastructure evidence. Original warm conversation and
roleplay cleanup remain; deterministic fleet answers bypass that formatting path.
The correction does not add maintenance operations or alter existing approvals.

## Verification and release

- 54 focused fleet cases pass, including the exact real request and follow-up
  through the full Agent dispatcher, source binding, delivery ambiguity, competing
  confirmations, disabled behavior and unrelated reminder intent boundaries.
- The full isolated Linux gate passes1,830 tests in 353.005s,
  with nine unchanged skips. Attempt8 is immutable; the earlier incomplete pending
  fixture failure in attempt7 remains preserved.
- Pinned Ruff0.17.1 F821/F822/F823 and
  [CI39945041324](https://github.com/promptinvest/tg-ingest-agent/actions/runs/39945041324)
  pass, including the regular/shuffled suites and original shell gates.
- An offline full-dispatch qualification uses the actual public six-host inventory
  and recreates the invented prior reply. All seven checks pass, with zero model
  calls, zero Telegram messages and no writes to the live conversation database.
- 101 executable/test/service/config source files match the verified stage byte
  for byte. Existing gateway/executor policy and inventory remain unchanged.

Published archive SHA256: `c057b8544c12d75458027a0e204b90deb515f659aa35ed0b9ae48ff7d1c868c4`.
Controller SHA256: `4372e00486f74d00ef0f0cb8309db59df00b4c4d8a211ac43e618833abc51dbb`.
Qualification SHA256: `185ec18b8ba162e963dd9c9e9ddcd36e74e07f8b4f7b20f990e6d777fae52400`.
Acceptance SHA256: `e36af0600f794386fa63675b448370071af9c5cbc6e34ce5f7062441cc5e7513`.
Window: 2026-10-23T11:28:29.542622+00:00 to 2026-10-23T12:13:29.542622+00:00.
Protected root: `/root/cara-fleet-answerfix-20261023/release-54b7e15`.
Never replay its completed controller or earlier completed attempts.

Protected online/stopped backups, previous source and a verified archive of
existing installer recovery directories precede the transition. All54 original
tables, rows, columns and schema objects match at the stopped gate; no migration
or new table is introduced. The unchanged stock installer, prerequisite checks
without package mutation, runtime/worker/spool/SQLite verifier and64-file installed
source parity pass. Fresh production reads through the restricted executor reach
all six hosts. Original environment, recovery files/pins, monitor/alert/retention
configuration, worker isolation, speech PID and neighboring Daily Lingo container
identities/images/start times/health remain exact.

Cara, worker and executor are healthy with zero unexpected restarts. Mentor and
runner stay disabled. The normal deployment receipt is sent once/one attempt.
Stored wrong replies remain as history; no owner message, update or maintenance
operation was manually sent or replayed. Final checks retain the accepted source,
enabled fleet flag, unchanged service identities and no new issue rows or pending
updates. Testing130.49.190.246 remains excluded with unverified identity/status;
this release makes no retirement claim.

Subsequent ordinary traffic exposed unresolved contextual follow-ups and raw-data
presentation; see the [complete follow-up correction](cara-fleet-contextfix-2026-10-23.md).
