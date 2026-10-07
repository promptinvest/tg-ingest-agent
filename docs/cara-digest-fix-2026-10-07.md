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
The full deployment gate and live receipt will be appended after completion.
