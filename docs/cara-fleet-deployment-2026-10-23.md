# Cara fleet release - October 23, 2026

Accepted 2026-10-23T09:42:36.751288+00:00. Owner authorization is “go with this” for the
natural-language fleet design, plus the standing implementation-includes-deployment
rule. No separate hash-specific approval is claimed. Active-session window:
2026-10-23T09:38:17.053106+00:00 to 2026-10-23T10:23:17.053106+00:00.

## Behavior

Owners ask ordinary Russian or English questions such as “Check resource usage
across the fleet”, “How is Daily Lingo doing?”, “What is using its memory?” or
“Что с российским прокси и его выходом?”. The current inventory connects named
projects and hosts to fresh resources, selected service state, error counts,
installed release/container metadata and configured HTTP health checks. Follow-ups
collect fresh evidence. Available prior investigation receipts support dated
comparisons; missing CI, issues, backups or historical data are reported as unknown.

Reads run in a separate restricted executor. Cara and the original networkless
worker cannot read its SSH private keys or transport configuration. Six root-owned
host gateways accept closed structured requests using pinned identities and
source-restricted forced-command keys. They return no log MESSAGE text, environment
values, command lines or arbitrary files. The optional relay connects only to the
fixed Russian SSH endpoint. No public listener, routing change or new alert timer.

The sole initial write runbook restarts `cara-worker.service` on PD. It requires a
fresh service fingerprint, a delivered ten-minute owner card, exact source/inventory
binding and one-time consumption by both executor and host. A text reply to that
card shares the same decision path. Competing confirmations require the intended
card. The restart binding is committed before publication, so a process interruption
cannot reconcile a restart as its preceding inspection. An ambiguous effect is
never automatically retried. Updates, deployments, cleanup and backups require
their own reviewed runbooks and application release gates.

Testing VPS 130.49.190.246 remains excluded. Its saved SSH key mismatches the current
endpoint. October 20 operator evidence already excluded it pending independent
identity verification; the older July KB still called it active. No verified
retirement decision was found, and no host-key replacement or subsequent contact
was made. Retired PI/Pilot/ARB hosts are also excluded.

## Exact source and gates

- Source: `fb20e4d6d1bf88dd39538b0da16b2e08b5e3484f`; build `bb4054457736`.
- Published archive SHA256: `e885f0ceae412b8306b25a3ccde7591ab5a90fd66ab47d45c49defe9e4ecde7b`.
- Controller SHA256: `1ebaaab659d61e42396cbc2453b7d9e7a1e28229761e595e6e7faa49356ba2f5`.
- Installed receipt: `783c4e2526aed3d08e9bc581`.
- Acceptance SHA256: `13145b724ad512a27f2fe7b9bda4f87e2fec54b48c88898741718fe92aba6e9f`.
- Protected root: `/root/cara-fleet-20261023/release-fb20e4d-attempt2`.

The final immutable attempt6 passed 1,821 Linux tests in 343.603 seconds with nine
unchanged skips; all 45 focused fleet cases pass. Pinned Ruff 0.17.1 F821/F822/F823
passes. [GitHub CI run 39932622469](https://github.com/promptinvest/tg-ingest-agent/actions/runs/39932622469)
passed for this exact source, including the regular and shuffled suites, compile,
shell syntax and shellcheck. The 102 executable/test/service/config files match
the tested stage; only inventory JSON CRLF/LF differs, with identical normalized
bytes and parsed content. The five actual routing/planning scenarios passed with
the existing model gateway accounting (0.01616286 USD for the accepted run),
zero Telegram messages and zero maintenance executions. Planner/router code is
unchanged from that qualified stage.

Preserved earlier attempts exposed real default-capability/router compatibility,
fixture and routing problems. They were corrected without weakening existing
behavioral assertions. All failed sources/logs/model qualification receipts and
unlaunched release preparation remain; never replay completed controllers.

## Deployment and preservation

The controller retained an online backup, the stopped `before.db`, previous source
and every existing installer backup in a verified protected archive. Archive:
`5cf86f446b9487f105e891eb27e61d22ffcd9b0d8a43d2924d3bd10b5cfe1990`
(11 directories, 1595 files).
The unchanged stock installer retains its normal bounded recovery rotation. Its
prerequisite adapter verified all five installed dependencies without changing
package indexes, installed versions or dependencies.

While Cara was stopped, exact new `store.open_db` added only `fleet_requests`,
`fleet_events` and their index. Every original table, column, row and schema object
across all 52 original tables matched. Integrity/FK checks passed. The original
installer and runtime/worker/spool/SQLite verifier passed. All 64 installed Python
files match the selected source.

The owner service receives only the fleet flag, narrow spool group and request/
result paths. The new executor has its own account, SSH identities and capped
systemd sandbox. Live mount/UID probes deny executor secrets to Cara and the old
worker, and deny Cara's database/token/recovery key to the executor. A real
read-only request through the production spool and executor returned fresh results
from all six hosts. No live restart runbook was exercised.

Protected environment files, backup keys, existing recovery files/pins and selected
monitor/alert/retention files remain exact. Speech PID and both Daily Lingo container
IDs/images/start times and HTTP health remain unchanged. Cara, worker and executor
are healthy with zero unexpected restarts; Mentor/runner stay inactive and disabled.
The normal verified deployment notification was sent once in one recorded attempt;
no manual test send or historical notice replay occurred. This release has a bounded
Cara restart and makes no uninterrupted-uptime claim.

Durable fleet receipts are included in the existing checked SQLite backups. Consumed
maintenance markers stay indefinitely. Transport results may expire after 24 hours
only after request consumption. Post-acceptance checks retain healthy services,
the accepted source/build and all neighbor/recovery invariants. Recovery after any
real effect is forward-only: inspect receipts and current state; never replay this
controller or restore an older database over new effects.
