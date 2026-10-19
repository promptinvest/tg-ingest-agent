# Cara reminder replies, schedule detection and CI - October 19, 2026

The owner requested fixing all four items in the additional code review: text-reply
targeting, missing repeat phrases, undefined-name lint and stale architecture text.
This extends the existing reminder module and CI; it adds no application service.

## Behavior

- Explicit text replies must name the delivered draft or partial prompt currently
  awaiting confirmation. Stale replies cannot confirm, cancel or amend its replacement,
  including model-routed responses. Ordinary unquoted answers retain their behavior.
- Replies to delivered alarms resolve before reminder drafts, reopen questions and
  reminder selection. An unrelated draft and its original expiry remain intact.
  Existing recurring-reminder source/echo snooze choices keep their binding.
- Russian number-word intervals, monthly/yearly and multiple-times-per-period forms
  are refused when unavailable, including when the model omits repeat fields. One-time,
  daily and weekly creation, one-shot offsets and quoted reminder titles remain supported.
- CI pins Ruff 0.17.1 and runs F821/F822/F823 for undefined names. The VPS application
  keeps its existing stdlib runtime and installed packages.
- README, the entry module and both specifications describe the actual poll/voice
  threads, isolated worker and retired Pilot host. Historical ADR logs stay preserved.

No schema, model, budget, personality, backup policy, historical repair, manual message,
paid evaluation, learning activation or Mentor/runner activation is included. The normal
deployment receipt remains part of the existing installer flow.

## Verification

New regression tests were run first against unchanged runtime source `3d18403` /
`bc67c89`: 29 cases, 18 expected subtest failures, no errors. Original results remain
in `/tmp/cara-reply-completeness-20261019-baseline.log` and the isolated baseline
source under `/dev/shm/cara-reply-completeness-20261019-baseline`.

All 56 focused reminder cases pass in 3.872 seconds. The full shuffled Linux suite
passes 1,776 tests, with nine unchanged skips, in 88.307 seconds. Ruff's exact CI
rules pass across the checkout; Python compilation and all shell syntax pass.
The runs use isolated scratch databases with networking disabled. Final verified
runtime/test archive SHA256:
`5a4cbbddb713017673b43466bb1a3327cc34dbc971505479e7b23bf9a641aa9b`.
Logs: `/tmp/cara-reply-completeness-20261019-attempt3-{focused,full}.log`.

The first two focused attempts retained two new-fixture language assertion failures:
English replies correctly produced English expiry text, while the test expected
Russian after per-turn language state had reset. Those fixture expectations now
assert exact English/Russian output; runtime code did not change between attempts.
All original assertions remain. The original failed logs and archives are preserved.
The post-test ADR-0007 wording correction changes documentation only.

Ruff is verified in a temporary QA directory, without installing a system package.
Its 0.17.1 wheel is checked against PyPI's SHA256; the extracted binary SHA256 is
`20cf97c5db04ca9c51249dec1a54cbc96c24462df280334ada04b8f3c877b746`.
Metadata receipt: `/tmp/cara-ci-ruff-0.17.1-20261019/receipt.json`.

## Accepted deployment

Published source `6a430adaec8f68e7e5cddc406514387fb1e5ea46`, build `4cc84f818306`,
receipt `f69a035e89d8b23ac681a9ff`. Accepted 2026-10-19 09:19:30 UTC / 12:19:30
Moscow. The owner request was “fix all of these”; deployment follows the standing
implementation-includes-deployment rule supplied in this session. No separate
hash-specific approval is claimed. Recorded window: 09:16:22-10:16:22 UTC.

Published archive SHA256:
`75a85a282b548c5ff8351c832aa1af66aca0d77f991ffc47e528e2d4791ead19`.
Single-use controller SHA256:
`ca772cc75c25109d5d1ad7d481bf6561f447b278cf4dc25d8247de7b377c9ec4`.
Protected release root: `/root/cara-reply-completeness-20261019`.
Acceptance SHA256:
`085df5ffd8f51711ca66534a333427da24aa98bed5ecadfd22e62a0c1bb6c5ff`.
Never replay this completed controller or previous notification receipts.

Fresh preflight and the original full release gate pass: 1,776 tests, nine unchanged
skips, 91.878 seconds. The exact CI lint command also passes on the committed stage.
The archive's runtime/test/shell/service/config bytes match the isolated verified
source. The original installer and runtime/worker/spool/SQLite verifier pass; no
system package was installed or upgraded. GitHub Actions execution is not inferred
from the local equivalent checks.

Stopped pre-install state is retained as `before.db` and `before-source/` in the
protected root. Installer backup:
`/root/codex-hardening-backups/20261019T091816Z-tg-ingest-agent`.
All 59 installed Python files match the selected source. Schema and every original
row in 43 retained application/history tables are preserved, including 99 messages,
116 reminders, 555 reminder events, 75 journal entries, 1,366 conversation turns,
241 issues, 111 profile items and 22,679 usage records. Mutable scheduler, trace and
cache tables continue normally and are not claimed byte-identical. Pending draft
data, preferences and protected backup/digest/learning anchors remain exact.

All three protected environment files and all 23 original recovery files/modes are
unchanged. Seven weekly backup pins and the daily-local/weekly-off-box policy remain.
Cara and worker are active with zero unexpected restarts; Mentor and runner remain
inactive and disabled. Speech PID 871 and both healthy Daily Lingo container identities
are unchanged. The normal deployment receipt was sent once on one attempt. No manual
test message, historical repair, learning experiment or notice replay occurred.
There is no uninterrupted-uptime claim. A fresh post-acceptance check has no pending
Telegram updates and retains the original 241 issue rows.
