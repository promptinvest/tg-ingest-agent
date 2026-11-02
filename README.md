# tg-ingest-agent

Cara — a single-owner conversational assistant on Telegram, running on the
PD-VPS (`174.138.108.85`). The former Pilot-VPS was retired in 2026-06.

One dedicated bot, no slash commands needed: you write or **speak** (voice
notes) in Russian or English, a closed-world intent router decides which skill
handles the request, and everything that changes state is confirmed
conversationally ("да" / "нет, лучше крипта" / "через полчаса"). Forwarded
channel posts are ingested automatically without burning router tokens.
Stdlib-only Python 3 (urllib, sqlite3), with one long-poll thread owning the
bot's SQLite connection and state changes. Voice transcription uses a serial
helper thread; the separate, isolated `cara-worker` service executes allowlisted
local tasks. Long polling needs no inbound ports, so the host firewall stays
SSH-only. The bot runs as systemd service `tg-ingest-agent` under the non-root
user `tg-ingest`; Mentor and its candidate runner remain disabled.

Current accepted runtime: ADR-0026 source `75f0f34`, build `931c14f8bb74`,
accepted November 1 at 11:36:09 UTC and independently audited. See the
[release record](docs/cara-adr26-release-2026-11-01.md) and
[verification](docs/cara-adr26-verification-2026-11-01.json).
The published tag is
[production-2026-11-01-adr26-tooling](https://github.com/promptinvest/tg-ingest-agent/tree/production-2026-11-01-adr26-tooling).
ADR-0027 updates documentation and checkout guards without changing that runtime.

## Where the real documentation lives

**[`CARA.md`](CARA.md) and [`SOLUTION.md`](SOLUTION.md) are the maintained
specs. This README is a pointer and setup guide only** — it is deliberately
thin so it cannot drift out of agreement with them (it did, for months).

- **What she can do**, the request flow, the persona and honesty rules, the
  data model, the configuration catalogue and the known limits →
  [`CARA.md`](CARA.md).
- **Why it is built this way** — design principles, module map, skill
  permission model, security posture, operations, roadmap →
  [`SOLUTION.md`](SOLUTION.md).
- **Working agreement for agents** (analyze first, argue before acting, deploy
  and test discipline) → [`CLAUDE.md`](CLAUDE.md).
- **Every env var**, with defaults and what each one costs you if it is wrong →
  [`tg-ingest-agent.env.example`](tg-ingest-agent.env.example), which is also
  the file that seeds `/etc/tg-ingest-agent.env` on a fresh box. A guard test
  fails the suite if a key `common.load_config` reads is missing from it.

Skills at a glance: ingest (notes, forwards, documents, journals), reminders +
calendar, spend/budget, knowledge Q&A over your own notes, remote fetch of a
link, VPS stats, weekly review, memory and proactive nudges. Guardrails at a
glance: owner-only allowlist, closed router action set, state-changing replies
from validated templates rather than free model prose, untrusted-content
fences, budget hard stop. Both lists are specified properly in `CARA.md` §3
and §7 — do not treat these two sentences as complete.

## One-time setup

1. Create a new bot with @BotFather (`/newbot`), save the token. Do NOT reuse
   the fleet notification bot from `/etc/codex-auto-update/telegram.env`.
2. Have a dedicated DO Gradient serverless inference access key
   (`DO_MODEL_ACCESS_KEY`).
3. Check the STT model slug in the DO console and set `STT_MODEL` if it
   differs from `whisper-large-v3` (voice input degrades gracefully when the
   endpoint is unavailable). For on-box transcription instead, install
   whisper.cpp once via `install-whisper-pilot-remote.sh` and set
   `STT_MODE=local_server` — note the code default is `remote`, which sends
   audio off the box.

## Deployment and recovery

[CARA section 9](CARA.md#9-operations-runbook) is the single operations runbook
for units, schedules, state roots, exact-source verification, transport,
preservation and acceptance. The current guarded release operator is required;
a successful deploy.sh transport alone does not establish acceptance. Source
recovery must preserve current schema, data, workers and receipts. A database
integrity check does not authorize replacing live data.

Populated environment files stay protected on the host. The installer seeds
only a missing environment; do not copy secrets or state into the checkout.

Bootstrap your chat id while the service is stopped: message the bot once, then
run `python3 bootstrap_chat_id.py <expected_numeric_chat_id>`. The id is
mandatory — run it with no argument to LIST the pending private chats (it exits
non-zero and binds nobody), then re-run with the right one. It refuses to run
while the service is polling and rewrites the env file atomically (keeping a
`.bak`). It reads the queue with a plain no-offset `getUpdates`, the only form
the Bot API guarantees consumes nothing; that returns the OLDEST 100 pending
updates, so on a deeper queue it says so rather than hiding the rest. The
opt-in `--deep-read` additionally reads the NEWEST 100 with ONE negative-offset
call and, per the API ("all previous updates will be forgotten"), DISCARDS
everything older — it prints that warning, and reports a gap when updates
between the two pages were destroyed unseen. One call is all the API can give:
the first destructive read leaves only the newest page in the queue, so there
is nothing older left for a deeper offset to address.

## Layout on the VPS

The complete current state/configuration roots and service boundaries are in
[CARA section 9](CARA.md#9-operations-runbook). Backups follow the existing daily
local and weekly encrypted off-box policy; independent key escrow and host-loss
recovery remain unproved.

## Tests

The full offline suite (no network, temp SQLite) also runs in GitHub Actions on
every push and pull request. On this Windows/OneDrive workstation, run it only
in the disposable VPS stage — never locally:

Use the Linux scratch/test path described in
[CARA section 9](CARA.md#9-operations-runbook). It runs scripted model/Telegram
boundaries and synthetic state without a production installation.

Only one poller may hold a bot token: never run the agent, a test, or a manual
`getUpdates` against the live token elsewhere (Telegram answers HTTP 409).

## Backups

The backup policy, protected state roots and recovery limits are documented in
[CARA section 9](CARA.md#9-operations-runbook). Keep independently retained key
custody distinct from an on-box integrity check; it remains unproved.

## Operational checks

Use [CARA section 9](CARA.md#9-operations-runbook) for current verification and
recovery. Inspect owner content only within the authorized diagnostic scope.
