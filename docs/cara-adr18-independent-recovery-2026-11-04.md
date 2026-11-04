# ADR-0018 — independent database recovery, 4 November 2026

Status: **selected off-box database recovery verified; no runtime deployment.**

The owner's continuation selected the outstanding ADR-0018 recovery evidence.
The earlier request requires explicit confirmation before any deployment.
The existing `backup.verify_independent_recovery` helper already covers this
operation; no new skill, service, module, configuration or backup policy was needed.

## Evidence

Two pre-existing workstation key copies match the active VPS backup key. They
remain outside the repository and OneDrive; their Windows access controls were
inspected. No key was created, copied, printed or exported. The July record of
workstation copies and the later "location unknown" notes remain dated history;
the fresh observation establishes current availability, not uninterrupted custody.

The selected `ingest-20261031T000116Z.db.gz.enc` attachment was downloaded through
the existing logged-in Telegram Desktop session from the fleet notification bot.
Its Cara caption, October 31 message and source profile were observed. The
5,879,584-byte encrypted file exactly matches the retained
VPS archive through pinned read-only SSH metadata. The newer November 3/4 local
archives are separate inventory observations, not off-box retrieval proof.

The unchanged helper matches the installed runtime bytes. On the workstation,
it used only the downloaded archive and pre-existing workstation key: one
OpenSSL decryption, bounded gzip expansion and one read-only scratch database
open. It restored **43,999,232 bytes**, found **56 tables** and
returned `integrity_check=ok`. The explicit expansion cap was 256 MiB.
The archive and key remained exact; temporary plaintext was removed and the
scratch directory was empty afterward. Actual process return code was zero.
Completion: `2026-11-04T09:21:20.394679+00:00`.

The existing two independent-recovery regressions passed on this workstation
outside OneDrive with no skips. The observer's separate synthetic success and
bad-hash cases were checked before handling the real archive. Initial local
invocation/preparation failures remain in the private evidence, separately from
the successful measured recovery. [Public measurements](cara-adr18-independent-recovery-2026-11-04.json)
bind the selected archive and private receipt without exposing keys or database
contents. Private operator evidence is retained separately from Git.

## Functionality and remaining limits

The selected database snapshot can be recovered off the VPS with an already
independent key. Production runtime, schema, data/history/accounting, environment,
models, budgets, services, notifications and daily-local/weekly-off-box policy
were unchanged. No deployment, restart, live database restore, new backup upload,
manual message, paid model call or consumed-controller replay was performed.
The optional fingerprint setting remains unset; failed inbox evidence remains.

This is database recovery for the selected October 31 snapshot. It does not
establish parity with today's live rows, uninterrupted key custody or complete
host/service reconstruction. Configuration and credentials, media and external
dependencies still need separate reconstruction evidence before claiming a
complete service recovery. No new recovery destination or policy is selected.
The earlier [accepted source release](cara-adr18-release-2026-11-02.md) and its
exceptions remain consumed and unchanged. ADR-0022 stays postponed.
