# tg-ingest-agent

Cara — a single-owner conversational assistant on Telegram, running on the
PD-VPS (`174.138.108.85`). The former Pilot-VPS was retired in 2026-06.

One dedicated bot, no slash commands needed: you write or **speak** (voice
notes) in Russian or English, a closed-world intent router decides which skill
handles the request, and everything that changes state is confirmed
conversationally ("да" / "нет, лучше крипта" / "через полчаса"). Forwarded
channel posts are ingested automatically without burning router tokens.
Stdlib-only Python 3 (urllib, sqlite3), one process, one thread, one SQLite
connection; long polling, so no inbound ports — the host firewall stays
SSH-only. Runs as systemd service `tg-ingest-agent` under the non-root user
`tg-ingest`.

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

## Deploy (from Git Bash, repo root)

```bash
DEPLOY_HOST=root@174.138.108.85 DEPLOY_PORT=22 \
  DEPLOY_KEY="$HOME/.ssh/digitalocean-dataplatform-asus" \
  DEPLOY_KH=known_hosts_pd_dataplatform bash deploy.sh
```

`DEPLOY_HOST`, `DEPLOY_KEY`, and `DEPLOY_KH` are required; the script has no
retired-host fallback. `--test` pushes the working tree and runs the suite in
the disposable stage dir without installing; `--pull` deploys `origin/main`;
`--rollback <sha|branch>` checks that ref out on the box and reinstalls.

The installer is idempotent: it backs up replaced files to
`/root/codex-hardening-backups/<ts>-tg-ingest-agent/` (newest 10 kept), installs
the tracked `tg-ingest-agent.service` and — only when `/etc/tg-ingest-agent.env`
does not yet exist — seeds it from `tg-ingest-agent.env.example`, gates on
`py_compile`, and leaves the service stopped while any `REPLACE_ME` placeholder
remains.

Fill `/etc/tg-ingest-agent.env` **in an SSH session on the box** (never push
it from PowerShell with `Out-File`/redirect — UTF-16/BOM trap), then
`systemctl start tg-ingest-agent`.

Bootstrap your chat id while the service is stopped: message the bot once, then
run `python3 bootstrap_chat_id.py <expected_numeric_chat_id>`. The id is
mandatory — run it with no argument to LIST the pending private chats (it exits
non-zero and binds nobody), then re-run with the right one. It refuses to run
while the service is polling and rewrites the env file atomically (keeping a
`.bak`). It reads the queue with a plain no-offset `getUpdates`, the only form
the Bot API guarantees consumes nothing; that returns the OLDEST 100 pending
updates, so on a deeper queue it says so rather than hiding the rest. The
opt-in `--deep-read` reaches the END of a flooded queue with negative offsets
and, per the API ("all previous updates will be forgotten"), DISCARDS everything
older — it prints that warning before you use it.

## Layout on the VPS

| Path | Purpose |
|---|---|
| `/opt/tg-ingest-agent/` | the service modules (`agent.py` + the modules listed in the installer's `MODULES`) |
| `/etc/tg-ingest-agent.env` | config + secrets, mode 0600 (seeded from `tg-ingest-agent.env.example`) |
| `/var/lib/tg-ingest-agent/ingest.db` | SQLite |
| `/var/lib/tg-ingest-agent/media/` | downloaded photos and voice files |
| `/var/lib/tg-ingest-agent/backups/` | daily DB snapshots (rotated; off-box copies are encrypted) |
| `/etc/systemd/system/tg-ingest-agent.service` | unit — installed verbatim from the tracked `tg-ingest-agent.service` (single source of truth) |

## Tests

The full offline suite (no network, temp SQLite) also runs in GitHub Actions on
every push and pull request. On this Windows/OneDrive workstation, run it only
in the disposable VPS stage — never locally:

```bash
DEPLOY_HOST=root@174.138.108.85 DEPLOY_PORT=22 \
  DEPLOY_KEY="$HOME/.ssh/digitalocean-dataplatform-asus" \
  DEPLOY_KH=known_hosts_pd_dataplatform bash deploy.sh --test
```

Only one poller may hold a bot token: never run the agent, a test, or a manual
`getUpdates` against the live token elsewhere (Telegram answers HTTP 409).

## Backups

Off-box database backups require `BACKUP_ENCRYPTION_KEY_FILE` (default
`/etc/tg-ingest-agent-backup.key`). Keep a recovery copy outside both the VPS
and this repo. Restore an `.enc` object with OpenSSL AES-256-CBC, PBKDF2 and
`-iter 200000`, then gunzip the resulting `.db.gz`.

## Quick verification queries (on the VPS)

```bash
sqlite3 /var/lib/tg-ingest-agent/ingest.db \
  "SELECT id, category, status, substr(summary,1,60) FROM messages ORDER BY id DESC LIMIT 5;"
sqlite3 /var/lib/tg-ingest-agent/ingest.db \
  "SELECT day, skill, model, ROUND(SUM(cost_usd),4) FROM llm_usage GROUP BY day, skill, model;"
sqlite3 /var/lib/tg-ingest-agent/ingest.db \
  "SELECT id, title, due_utc, recurrence, status FROM reminders ORDER BY id DESC LIMIT 5;"
journalctl -u tg-ingest-agent -n 30 --no-pager
```
