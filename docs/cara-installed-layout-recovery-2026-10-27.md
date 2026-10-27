# Cara installed-layout delivery recovery - October 27, 2026

Status: deployed and accepted at 2026-10-27T07:45:35.531546+00:00. Actual sandbox
observer and independent final audit pass. The production tag is remotely verified.

The owner requested: "fix this and ensure your tests cover such cases when you
deploy". The source, private package and bounded window below are operator
selections under that request, not a separate hash-specific approval.

## Incident and correction

The earlier accepted application e242516 installed its entry as `agent.py`, but
`action_delivery.deliver_action` imported the repository name `tg_ingest_agent`.
A no-op reminder resolver on ordinary dispatch reached that import before routing,
so unrelated text and scheduled reminder expiry failed despite healthy services.
The defect originated in the earlier delivery-recovery addition `9ae0cb3`; changing
models would not correct it. Source-layout imports and the old static module
exemption had hidden the deployed rename.

Five retained updates from October 26 15:00 UTC through October 27 06:21 UTC each
failed three attempts with the import exception. No committed action receipt or
pending/ambiguous reply outbox remained for those updates. Generic error replies
were recorded as sent. Read-only aggregation at 06:44 UTC found 413 scheduler errors
and 15 failed inbound attempts; the first scheduler failure was20:04:21.151615 UTC
October25. These rows and historical observations remain unchanged, without replay.

The existing delivery module now obtains the current sender through the running
Agent. Import as `agent`, script execution as `__main__`, dynamic sender patching,
transactional receipts, known-unsent retries and ambiguous-send containment share
the same transport seam. No repository-name alias or second entry/client is added.

A stdlib verifier derives the exact installed artifact from the installer's module
list. Fresh isolated Python processes use only installed filenames and exercise
ordinary text, empty expiry, committed reminder recovery, known-unsent retry and
ambiguous delivery across restart. Fixtures use scratch databases and fake model
and Telegram boundaries, with network/process effects denied. The gate runs on
packaged source, after exact runtime copy before first worker/main startup, and
through native verification. Future deployment checks therefore cover the actual
rename rather than relying on source-import tests alone.

## Qualified source and retained attempts

Source `d9343ad0186e1cafa3d56999eb6e59c16b1808f0`, archive
`04088030c0527f9d14b96a01a7d8551ee482b777e55ab4757b0da4fa49422ec7`.
Six production paths change: delivery, entry, the new verifier, deployment,
installer and native verification. Model-facing behavior and schema are preserved.
The normalized baseline review covers 16 repository paths: 13 active fix paths and
three exact inherited October25 documentation paths; it does not mislabel the
whole baseline delta as 13 paths.

- Normal and shuffled Linux suites each ran 3,122 cases, with nine existing skips
  and no failures; compile and shell/static checks pass.
- Exact-source CI [40465112375](https://github.com/promptinvest/tg-ingest-agent/actions/runs/40465112375) passes.
- Exact older e242 installed-layout negative controls reproduce the import failure
  in both entry forms. Corrected exact-source installed-layout checks pass with
  zero actual model/Telegram calls and no native database access.
- Selected private V3 passes 154 isolated operator cases, retaining all 133 original
  cases, and actual old/new kernel shim probes prove isolated execution with
  bytecode disabled. V1's 153-case result is retained but blocked before rollout:
  its intermediate protection count incorrectly expected 106 after the first MCP
  phase, where the exact set is 104. V2 corrected 104 then 106 and was not consumed;
  V3 also restores fresh-nonce absence and broken-symlink checks before backup/stop.
- The first source selection refused `/tmp` ancestry before creating a release
  root or changing the application. An exact archive copy in protected fresh
  inputs satisfied the original guard. No guard was bypassed.

Full receipt `2f1c8734849b0b36fe2058e208abd1fcc9c0bcadc23c9a0bcec5d54a8ba69534`;
CI `8459250a7204f66fab61d2c0bd2650e64340b6260b37b5acb141f97823408ed5`;
layout proof `6a6a9519b5ebd513d1367db9abd7310491a4cd081a5070a66776d57338c587af`,
report `686d71df0c592bba464525e0db4aa7eaf3ca58a66d0d0d1b14b02722d84cb937`;
source review `de75ad428428ee2e4d5bb2117adcffa6cbf43c8a0a7f3c7c3f58160caf3f7a1b`.
V3 inventory `a1fa6c62d70520f342595be1c0f0ce51bb3a0f7a0bf4d6e6636bbebefe94aa8b`,
archive `4a99f7e0b3330398bcfffbd8176392222157b3a1b22603a73fd8b429e9c83169`,
qualification `fb157d19e107fb9dcbf4a9bcd87cd0903c73349b8de044850428ba5ec5356aff`.

## Transition and acceptance

The single-use controller started at 07:41:27 UTC in the selected
07:40:43.611450-08:40:43.611450 UTC active-session window, authorization
`92e0e92bca0188ec5bf2bccfcd555b985d2adf976db76422a999f49178ecdc06`.
Fresh preflight at 07:41:29 UTC and stopped 56-table parity/backups passed.
Native installation began at 07:42:24 UTC; full parity passed before worker startup
at 07:43:02.017421 UTC and main startup at 07:43:15.813334 UTC. Native verification
passed at 07:44:33.381685 UTC, final-restart readiness at 07:45:05 UTC. The normal
deployment notice records one successful send. The controller completed once;
it must not be replayed.

Accepted at 07:45:35.531546 UTC, receipt
`e981e38e782856ad9077289e49e2fb9dd03c602d209f23ea61790663c111261c`.
Installed build `447464bdaf47`, deployment `78582200ef0e63991778091e`, verified
native marker `8e3a7085da23c39d5e49cdaa0c93290332e6d511a99b3b7813944083e21d4899`.
The three final health samples at 07:45:21.454329, 07:45:26.817450 and
07:45:32.130089 UTC show active main/worker/fleet/Whisper with zero restarts,
healthy neighboring database and HTTP200.

The actual sandbox observer passed at 07:48:12.954935 UTC, installation receipt
`eaf32e0c1598731553010b3f7c2c0c2fc96c11afd497318c859d07b4f970ff62`,
manifest `5040f3153221f559bb498593c0cfe1ad3c45785da8630caa66214f975bcc25f2`.
Its successful exit 0 first snapshot is `observation-042h-new-runtime.json`,
`5fe3c6ad1974725e593ec934a06ed3c5ca72917311d0f551c547472d5856ae77`.
The first V2 audit retains a schema expectation failure: it omitted two correct
incident classification fields. V3 preserved its earlier checks, added strict
checks for those fields and passed 11 network-isolated cases, receipt
`1d4c465d45a0c000474cc28646121494e3d42560020b330e32dd1211996800f1`.
The actual independent V3 audit passed at 08:32:44.112330 UTC, receipt
`83ad2983294273fa5279bc902726866d933bddc96e3d1a03e0621c32728373d7`.
No application or observer change accompanied the audit correction.

Final preservation passes all 94 runtime counterparts, 106 protected identities
plus two new observer units, unchanged 56-table schema, 24,456 prior usage rows,
all 29 historical unknowns, and the five failed updates/fifteen failed attempts.
No current-runtime unknown or replay is introduced. The installer-generated retired
Mentor env projection was conditionally restored at 07:42:59.744854 UTC before
worker startup to exact protected original 51745bec/root:983/0640; generated 0797f0a8
and recovery receipt `67a2da333de8353f6ec482dca6b6d9dbf7b9bf083704f1ceb27a8a5492a37417`
remain retained. Main model environment, application and database were not restored.
Preflight `94cf5b27b04e4b53bdebc2889c7b38b8db1ffaf16659df87d460a19cdcddacf2`
and backup receipt `ed37e2b2764d401e92dd72ceede916d24ea802c130b6c0989eab3212e3bbeed5`
are retained with every earlier failed/superseded attempt.

Fourth-epoch continuation
`c013531c8d1459b4d25857a0b853427e8d89e3fb26da923e0fe04b01bd0716e9`
starts the new segment at 07:43:15.813334 UTC after the recorded
07:42:22.653514-07:43:15.813334 UTC source transition gap. Earlier epochs,
pauses and observations remain; the original end is 2026-10-28T13:36:17.924552+00:00.
Models/fallbacks, conversation cap, vision, embeddings, local Whisper and
USD 2/day, USD 12/month, USD 0.15/task/four task-profile-call guards remain.
No paid qualification, manual message or failed-update replay is included.
Conditional model-only reversion preserves application/data/later unrelated edits.

At 08:32:04.742326 UTC all four services remain active with zero restarts and no
current-main import/update errors. No new owner inbound traffic had arrived, so
live ordinary-response quality is not measured by this release. Installed-layout
behavior passes; general model quality or ChatGPT-agent parity is not established.
The failed interval remains application incident evidence, not clean model evidence.

Tag `production-2026-10-27-installed-layout` is published and remotely verified:
annotated object `ff18b470eb00741b05c1ff067c8e894068e0899b`,
peeled commit `d9343ad0186e1cafa3d56999eb6e59c16b1808f0`.
