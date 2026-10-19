# Cara host cleanup - October 19, 2026

Owner requested clearing the reviewed clutter and introducing Daily Lingo retention.
This is operational maintenance; Cara runtime source remains6a430ad/build4cc84f818306.

Cleared63 empty journal-draft keys,13 legacy settings with no current installed-code
references and7 expired model cooldowns. A protected SQLite backup is retained at
`/root/vps-cleanup-retention-20261019/cara-before.db`, SHA256
`4cc9d98ba62d30ab277ddb6f5449536ca6d8fbcf2c1feba837c6a3954b9861f1`.
The short transaction changes only the selected kv/cooldown rows; no triggers,
schema change or VACUUM. SQLite quick_check and FK checks pass. User history,
notes,reminders,spend,preferences,backup anchors and seven pins remain intact.
Unconfirmed notes37/84/97 remain for deliberate owner triage. The historical Mentor
patch remains linked to its original failed cycle and is preserved.

All reviewed41 unused Cara scratch roots plus11 stray/empty root entries are
archived before removal. Their original paths,byte hashes,metadata and roots are
in `cara-scratch-and-strays-manifest.json`; original bytes are in the matching
`.tar.gz` under the protected root above. This includes failed fixtures,logs,
archives and completed controllers formerly in `/tmp` or `/dev/shm`. Never replay
completed controllers. Rebuildable APT,pip and two old type-check caches are cleared.
Original protected release roots,source/config/recovery material and installed
Mentor source remain; worker/Mentor spools were empty. No app install,restart,
new dependency,paid call,manual Telegram send or historical notice replay occurs.

The host cleanup controller SHA256 is
`5aaf5f9b21953e092e215e7ead570c5ab4136a3d3c30f7149648d58648ec0644`.
Daily Lingo's separately verified host package isd6a57e8; it changes no app image
or monitor implementation. Its snapshot policy keeps current day plus14 complete
UTC days raw and retains older unpinned snapshots in verified archives indefinitely.
System-journal retention follows the approved90-day/1GiB limit after archival of
original closed journals. Overall maintenance accepted2026-10-19T10:12:24.947747UTC.
Acceptance SHA256:`1ee6e59555dae221d9d033399302f8a60ca5ff5d890da866e272c8860db4bb04`.
Recorded active window10:00:40-12:00:40UTC. Net4,495,253,504 disk bytes and64,245,760
tmpfs bytes recovered. All94 protected hashes and application process/container
identities remain exact; Cara/worker/speech healthy with zero restarts. No app
transition or runtime/dependency change. The13 host-tool tests pass again on the
committed package; original applications retain their accepted verification.

The scratch archive contains1,636 files/113,869,559 original bytes in58,178,084
compressed bytes. SHA256:`076b734d4996bea4c2c29ec3577ab30561d3d4739351ce2697e68a34f41e1370`.
Original paths and metadata remain in its adjacent manifest. All79 closed original
journals have a separate verified root-only archive before working retention.
The new nightly snapshot timer is active:3,059 old snapshots archived and3,710
historical references retained raw. These archives are retained indefinitely.
