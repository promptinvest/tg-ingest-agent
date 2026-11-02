# ADR-0024 source verified; deployment held

ADR-0024 is implemented and verified as source
`2b6da6ee0ca43f7c8aa1073fe5fddb069bfd91b2`, merged through
[PR 15](https://github.com/promptinvest/tg-ingest-agent/pull/15).
Deployment failed before installation. Production still runs accepted ADR-0018
source `6e947694`; its availability was restored and independently verified.
The new generic undo command is not live.

The source adds the precedence table and matrix, compatible parameter contracts,
and durable latest-supported-action undo using the owner's chosen new command.
Existing undo phrases, history, schema, models, prompts and budgets are preserved.
See the [implementation record](cara-adr24-2026-11-02.md).

Exact-source Linux verification passed 208 focused checks and 3,450 tests in each
normal/shuffled run, with nine skips each. Static, helper and all five installed-
layout scenarios passed. [Exact-head CI](https://github.com/promptinvest/tg-ingest-agent/actions/runs/41430788706)
passed. Every original test/support file remains byte-identical. Separate
operator, transport and actual isolation/cleanup qualifications passed.

The strict online-to-stopped comparison detected one existing learning-scan
timestamp advancing while the accepted service was active. All other data was
exact; the frozen stopped backup and live state matched across all 56 tables.
The delta is consistent with the existing public writer; exact writer attribution
is not proved. No guard was waived, candidate installed, data restored or source
rolled back. The failed attempt and its evidence remain consumed and preserved.

The unchanged accepted main/fleet services were started once. The recovery's
first postcheck failed on a transient startup reservation. That failure remains;
a separate read-only observation subsequently verified healthy services, exact
accepted source/settings/history and the strict current-usage guard. It is not
candidate acceptance.

A [concrete guard proposal](cara-adr24-release-guard-proposal-2026-11-02.md)
remains pending. Its inert comparator separately passed 24 synthetic Linux tests,
lint and compile. The first new-fixture failure remains; only its identical-value
expectation was corrected, with comparator and negative cases unchanged.

This documentation-only follow-up passed 14 native checkout checks and 29
preservation-contract checks. Runtime, tests and scripts are unchanged; no
installation, restart or production tag is required. The
[public verification summary](cara-adr24-release-verification-2026-11-02.json)
contains source/test status. Detailed operator receipts, attempt identities,
original failures and frozen proposals are retained in the private operator
workspace; raw production data and secrets are excluded from this record.
