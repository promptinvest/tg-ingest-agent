# ADR-0039 — provider response-size limits

Status: **deployed and accepted; separate audit, physical closure and production
tag verified**. Exact source `5928fd966a77fbbb98799840311679919e22f30f`, archive
SHA256 `887573ca081dec38474011759b7af8ba3421587493e8a6f8c1a4ecb5e894ef3d`,
build `7787d9c8e55c`, deployment `caa2636ba2f71e9f8ac8b54b`. The verified native
manifest is `c833aa1d661be05142208f56bdbf0c5ea37b3dd5e8ae36f5aefbd86f098aa1e3`.
[PR 22](https://github.com/promptinvest/tg-ingest-agent/pull/22) merged as
`71af9bc723e044e3c6d960963ec56d9472667b58`. Detailed receipts remain private;
the [verification summary](cara-adr39-release-verification-2026-11-04.json) records public hashes.

The shared inference transport reads at most 8 MiB plus one overflow byte in
chunks no larger than 64 KiB. An exact-limit response is accepted after EOF;
overflow fails before UTF-8 decoding or JSON parsing, independently of response
headers. Chat, embeddings, remote STT and loopback Whisper use this reader.
Oversized dispatched responses raise the existing typed transient failure with
usage unknown; no unsent claim or refund is introduced. Oversized local Whisper
replies do not trigger another transcription. Ordinary below-limit decoding,
plain-text fallback, prices, deadlines, slots and transport cleanup remain.

HTTP errors read only a 64 KiB diagnostic prefix, retain the existing redaction
and 500-character display limit, and close the stream on every path. The real
HTTP status still determines accounting and retry classification. The cap bounds
encoded-body reading; it does not bound all JSON allocations, nesting or parse
time. Larger future request configurations must explicitly revisit the limit.

Exact-source Linux verification passed 1,618 focused checks with eight skips and
3,535 checks in each normal/shuffled order with nine existing skips. All 397
committed files match tested bytes. Of 127 original test/support files, 119 are
byte-exact; eight adapt only the selected finite-read fixture methods. Every
original assertion and all other test logic remain exact. Nineteen new cases
cover size boundaries, short reads, UTF-8/header behavior, parsing exclusion,
stream/slot cleanup and conservative chat/embed/STT accounting. Ruff, compile,
shell syntax, ShellCheck, helper suites and all five installed-layout scenarios
passed. [Source CI](https://github.com/promptinvest/tg-ingest-agent/actions/runs/41643283178)
passed. The release operator passed 170 cases, unchanged historical cases 139,
transport 27, two nonroot import roles and three actual cgroup cleanup cases.
The read-only closeout helpers passed 12 cases, lint and compilation.

Earlier generations remain preserved: the 18-case baseline reproduced missing
guards, the first scratch package exposed a reused fake and working-copy CRLF
packaging, and the first full verification lost SSH transport after its normal
suite passed. A fresh supervised full verification completed both orders; its
actual kernel completion and later process/cgroup closure are recorded
separately from dispatch success. The first two operator qualifications failed
on diagnostic-contract fixtures and the third on a synthetic transport inventory
count. The fourth passed after narrow fixture corrections, retaining original
assertions and runtime gates. None of these results was rewritten or replayed.

All 21 native phases passed. Canonical acceptance completed at **2026-11-04T08:07:00.166678+00:00**,
SHA256 `0bccf2fa5d8c515237502cc6ed1aac375a03dfe66ce77590f1e626b5121e48f2`.
The separate read-only audit passed at **2026-11-04T08:07:57.439762+00:00**,
SHA256 `d570d727e02529118adcc22e952a7da777ad5a8757371140a01d466d7497025f`.
Physical closure passed at **2026-11-04T08:09:22.168458+00:00**,
SHA256 `2e9412d775023027c22068be33bb587c43009b4feee1acd7c0c960f58e88c4f2`.
Actual controller, supervisor, guardian, durable outer-parent and audit waits
returned zero. The initial SSH result proves dispatch only. Recorded actors and
groups, the outer process and owned tool cgroup were absent; all
690 sealed files remained exact. Four services were active with
zero recorded restarts at closure. These are dated observations.

Annotated tag
[`production-2026-11-04-adr39-provider-response-limits`](https://github.com/promptinvest/tg-ingest-agent/tree/production-2026-11-04-adr39-provider-response-limits),
object `624cfe2b412c02335e17761b84e6ef6d877f7394`, is remotely
verified to peel to exact source `5928fd96`. All 17 earlier tags and 33 earlier
refs remain exact; only the new tag and peeled ref were added. Publication proof
is `91a3c1f9c743566c187d89cc4943602367ac11089bd1fdea08902755544bc41f`.

Models, prompts, effective USD2/day, USD12/month and USD0.15/task limits, schema
56, stored history/cache, protected configuration, neighboring services and
notification preferences remain preserved. Current unknown usage was zero and
all 40 historical unknown rows stayed exact. No paid qualification or manual
message was added; ordinary application accounting remains in force.
Independent backup-key custody and host-loss recovery remain unproved; ADR-0022
stays postponed. This documentation closeout changes no runtime file.
