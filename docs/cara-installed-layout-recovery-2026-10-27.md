# Cara installed-layout delivery recovery - October 27, 2026

Status: implementation and qualification in progress. No deployment acceptance
is claimed by this checkpoint.

The owner reported that Cara stopped responding properly, then explicitly
requested: "fix this and ensure your tests cover such cases when you deploy".
This request authorizes the narrow correction, qualification and deployment.
Exact source, package and bounded deployment window will be operator selections;
no separate hash-specific approval is claimed.

## Confirmed incident

The running application is accepted source e242516, build77f6380d9912,
deployment73e0133c10ad848334a43e76. Repository head f345bec is documentation only.
At06:44UTC October27 the main, worker and fleet retained their accepted PIDs and
zero restarts; resources were healthy. The delivery module imported
`tg_ingest_agent`, while the installer deploys that entry file as `agent.py`.
A no-op reminder handler lies on ordinary dispatch before routing, so even
unrelated text failed before an LLM call. Reminder expiry failed the same way.

Five retained updates from October26 15:00UTC through October27 06:21UTC each
failed three attempts with this exact import error. None had a committed action
receipt; no pending or ambiguous reply outbox entry remained. Generic error
replies were recorded as sent. These observations authorize no automatic replay.
Read-only journal aggregation since the final October25 main startup found413
scheduler errors and15 failed inbound attempts, as of06:44UTC October27. The
first scheduler failure was20:04:21.151615UTC October25, before that release's
acceptance. An isolated evaluation of the exact installed delivery function with
an empty in-memory outbox reproduced the import error without application
imports, model calls, Telegram calls or production writes.

The defect entered with the earlier delivery-recovery addition9ae0cb3 and was
already present in all three model-trial application epochs. Source-layout tests
imported the repository filename and missed the installed rename. The old static
module audit also incorrectly exempted that repository filename as installed.
Model health calls continued, budgets were below their guards and the failed
requests never reached routing; changing models would leave this fault present.

The existing six-hour observer remained on schedule, but its last snapshot
contained failed inbound traces while labeling traffic insufficient for quality
review. Treat this period as application failure evidence, not successful
ordinary-answer quality evidence or a clean model comparison. Historical
snapshots and the original October28 deadline remain intact.

## Correction and permanent deployment gate

Keep delivery in the existing module and task/outbox architecture. Resolve the
sender through the actual running Agent so source import, installed import as
`agent`, and script-style `__main__` share the same dynamically patchable
transport seam. Preserve atomic action receipts, delivery-only recovery,
known-unsent retries and ambiguous-send containment. Do not add an installed
alias or reload another entry module.

Ship a stdlib-only installed-layout verifier. It derives the candidate runtime
from the installer's exact module list, checks installed counterparts against
source when source is provided, and exercises fresh isolated import/script
processes containing only installed filenames. Synthetic fixtures use fake
model and Telegram calls; network/process effects inside scenarios are denied.
The gate covers ordinary text reaching its handler, no-op expiry, committed
reminder delivery recovery, known-unsent rejection and ambiguous outcomes.

Run it against the packaged source before installation, against the exact copied
runtime before any worker/main restart, and through native runtime verification.
All existing source-layout regressions and assertions remain. A negative baseline
must reproduce the original defect; the corrected artifact must pass. Future
CI runs the installed-layout regressions alongside the existing full suite.

## Release boundary

Preserve data and schema, all failed updates and original delivery evidence,
models/fallbacks and USD2/day, USD12/month, USD0.15/task/four-profile-call guards.
Preserve neighboring services, alert opt-outs, retention, source publications,
protected recovery material and both prior release roots. Use a fresh guarded
operator and fresh receipts; never replay completed controllers. Record a fourth
application epoch, retain the original trial end, and bind monitoring and
conditional model-only reversion to the new accepted source. No code/database
rollback, message replay, paid qualification cohort or data import is included.

Exact qualification, publication, transition and acceptance evidence will be
appended after those steps complete.
