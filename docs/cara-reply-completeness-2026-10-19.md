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

Publication and deployment are pending. Do not treat this document as acceptance.
