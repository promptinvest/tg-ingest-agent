# Cara monthly digest correction - October 7, 2026

Owner requested review of the strange deployment-time text, then "fix & deploy
what needed". The stored October 7 14:35:30 UTC chat message confirmed raw
provider HTML, internal identifiers, broken example fragments and historical
handling events presented as monthly unresolved failures. Deployment receipts
were valid; this correction concerns the issue digest only.

## Change

- Closed, bilingual summaries replace raw diagnostic bodies. A bounded HTTP
  error number may be shown; unknown kinds use a generic localized category.
- Owner-reported/request examples are redacted, kept to one line, clipped at a
  word boundary with an explicit ellipsis and limited to one per category.
  HTML, tracebacks and structured diagnostic payloads are not examples.
- The period starts at the previous successful digest, or one calendar month
  before the current owner-local date on first use. Recent/repeated entries and
  older entries still marked open are separated and dated.
- Counts explicitly describe open entries, rather than lifetime occurrences.
  Successful repairs, learned corrections and deliberately skipped learning
  after fabricated actions are omitted from the unresolved list. Their original
  statuses, details and observations remain untouched.
- Only nonzero, period-bound resolved-entry counts are shown; they describe
  recorded issue lifecycle, not implementation or deployment success.
- Length limits preserve complete entries and the closing line. An omitted-entry
  count points to the existing on-demand problems command.

The existing monthly schedule, failure backoff, Telegram routing and October
receipt remain. No replacement digest or historical message edit is included.
No schema, environment, model, budget, reminder, KB-answer, voice, backup or
Mentor behavior change is included.

## Verification and deployment

Twelve focused content/scheduler regressions passed in the non-OneDrive clone.
The existing scheduler fixture now uses the real owner-reported kind rather than
an unknown internal kind; its original example assertion is retained. Unknown
kinds have their own regression asserting no raw identifier or diagnostic leak.
The networkless Linux focused/source-hygiene gate passed 43 cases in 3.932
seconds at `/tmp/cara-fix-verify-20261007-b8223aa0b7`; AST, shell syntax and
shellcheck passed. The live candidate preview passed against a read-only SQLite
connection without a Telegram send or model call. Preflight matched all installed
source to the current release, healthy Cara/worker and retained disabled Mentor,
SQLite integrity, seven protected weekly archive pairs and the exact October
digest receipt. Protected preflight: `/root/cara-digest-fix-20261007-821b03cc/preflight.json`.

The exact full deployment gate passed **1,694 cases, nine unchanged stage skips**
in **97.332 seconds**. Source `cb639e7e0f273e0ace19cd0fe887aa210ef74cf6`, clean.
Installed build `bb2a35395bb8`, receipt `bdff7ad30587f268eb05c122`.
Installed `2026-10-07T16:02:19.632258+00:00`; runtime/worker/spool/source/SQLite
verification completed `2026-10-07T16:03:15.391058+00:00`.
Protected installer backup: `/root/codex-hardening-backups/20261007T160211Z-tg-ingest-agent`.
Initial postcheck overlapped the installer's final graceful restart and observed
Cara deactivating; that observation is retained, with no claim of a runtime fault.

Final acceptance passed `2026-10-07T16:04:28.806379+00:00`. Cara and worker are
active/enabled; Mentor/runner inactive/disabled. Cara reports zero unexpected
restarts. Whisper/Nikki retain their PIDs; both Daily Lingo containers are healthy.
Normalized installed source bytes match the candidate. SQLite quick_check is ok
and foreign_key_check has zero errors. The environment file hash is unchanged.

Every original row matched the protected installer snapshot: 232 issue observations,
193 issue patterns, 91 messages, three files, 67 journal entries, 1,222 conversation
turns, 108 reminders, 11 Mentor cycles and 20,652 usage records. Original issue
statuses/details were not rewritten. All seven weekly gzip/encrypted archive pairs
match their preflight hashes and remain mode 0600; backup dates/pins are unchanged.

The October month stamp, failures and delivery timestamp
`2026-10-07T14:35:30.203859+00:00` are unchanged; the digest was not resent or edited.
One normal deployment notice was accepted at `2026-10-07T16:04:10.887359+00:00`,
one recorded attempt. The corrected formatter also passed a read-only live preview
without a model call or Telegram send. Protected final record:
`/root/cara-digest-fix-20261007-821b03cc/acceptance.json`.

The existing spool-canary cleanup permission warnings were retained; the canary
and verifier passed. Final documentation changes do not alter the verified runtime.

Final documentation/operator regression check: 43 cases passed in 5.442 seconds
at `/tmp/cara-fix-verify-20261007-3a6ecd4831`; static/shell checks passed.
