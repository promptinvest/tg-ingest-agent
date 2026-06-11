# tg-ingest-agent

Telegram message ingest + LLM categorization service for Pilot-VPS.

A dedicated Telegram bot receives messages (typed or forwarded from channels)
containing text, photos, and URLs. For each logical message (an album counts
as one) the agent:

1. stores TG message id, chat id, forward-origin metadata, raw text, URLs, and
   the photos themselves (SQLite + files on disk),
2. asks DigitalOcean Gradient serverless inference (vision-capable model,
   default `anthropic-claude-haiku-4.5`) to **suggest** a category and a short
   summary — the LLM reuses the taxonomy built from previously confirmed
   categories and proposes a new category only when nothing fits,
3. replies with the suggestion and inline buttons: tap ✅ to confirm, tap an
   alternative, or reply to the suggestion message with your own category
   text. Only confirmed categories become part of the taxonomy.

Stdlib-only Python 3 (urllib, sqlite3); long polling, so no inbound ports —
UFW on Pilot-VPS stays SSH-only. Runs as systemd service `tg-ingest-agent`
under the non-root user `tg-ingest`.

## Layout on the VPS

| Path | Purpose |
|---|---|
| `/opt/tg-ingest-agent/agent.py` | the service (this repo's `tg_ingest_agent.py`) |
| `/etc/tg-ingest-agent.env` | config + secrets, mode 0600 (see `tg-ingest-agent.env.example`) |
| `/var/lib/tg-ingest-agent/ingest.db` | SQLite (tables: `messages`, `urls`, `images`, `kv`) |
| `/var/lib/tg-ingest-agent/media/` | downloaded photos, named `<file_unique_id>.<ext>` |
| `/etc/systemd/system/tg-ingest-agent.service` | unit (reference copy: `tg-ingest-agent.service`) |

## One-time setup

1. Create a new bot with @BotFather (`/newbot`), save the token. Do NOT reuse
   the fleet notification bot from `/etc/codex-auto-update/telegram.env`.
2. Have a DO Gradient serverless inference access key (`DO_MODEL_ACCESS_KEY`).
3. Optionally seed the taxonomy via `CATEGORIES` (e.g. `news,tools,ideas`) —
   not required; categories also emerge as you confirm suggestions.

## Deploy (from Windows, repo root)

```powershell
ssh -i ~/.ssh/do-pilot -p 49191 -o UserKnownHostsFile=known_hosts_pilot_rnd root@209.38.175.16 "mkdir -p /root/tg-ingest-agent-stage"
scp -i ~/.ssh/do-pilot -P 49191 -o UserKnownHostsFile=known_hosts_pilot_rnd tg_ingest_agent.py install-tg-ingest-agent-pilot-remote.sh root@209.38.175.16:/root/tg-ingest-agent-stage/
ssh -i ~/.ssh/do-pilot -p 49191 -o UserKnownHostsFile=known_hosts_pilot_rnd root@209.38.175.16 "bash /root/tg-ingest-agent-stage/install-tg-ingest-agent-pilot-remote.sh"
```

The installer is idempotent: it backs up replaced files to
`/root/codex-hardening-backups/<ts>-tg-ingest-agent/`, preserves an existing
env file, and leaves the service stopped if `REPLACE_ME` placeholders remain.

Fill `/etc/tg-ingest-agent.env` **in an SSH session on the box** (never push
it from PowerShell with `Out-File`/redirect — UTF-16/BOM trap), then
`systemctl start tg-ingest-agent`.

Bootstrap your chat id: message the bot once, then
`journalctl -u tg-ingest-agent | grep ignored` shows
`ignored message from chat_id=...`; put that id into `ALLOWED_CHAT_IDS` and
restart.

## Behavior details

- **Confirmation flow**: message status goes `pending` → `suggested` (LLM
  suggestion sent with buttons) → `confirmed` (you confirmed). Unconfirmed
  messages keep their suggestion but never enter the taxonomy. Confirm via
  button, or reply to the suggestion message with any category text (new
  categories are created on the fly, matched case-insensitively).
- **Albums** (media groups) are buffered ~3 s and stored as ONE message row
  with N image rows; the reply goes to the first album message.
- **Duplicates**: redelivered updates are dropped via
  `UNIQUE(chat_id, tg_message_id)`; re-forwarding the same channel post is
  detected via the forward origin, skips the LLM, and gets `status=duplicate`
  with the original's classification copied.
- **LLM outage**: store-first, suggest-second. Failed rows stay `pending`
  and are retried every `RETRY_INTERVAL_SECONDS` (max `LLM_MAX_ATTEMPTS`,
  then `failed`). Messages are never lost.
- **Commands**: `/start` (help), `/stats` (counts per status/category),
  `/categories` (the taxonomy with confirmed-message counts).
- **Bad model output**: defensive JSON parsing + one corrective retry; if
  still unusable, the suggestion falls back to `FALLBACK_CATEGORY`.

## Known limitations (v1)

- Images sent as **documents** (uncompressed files) are stored metadata-only
  and not analyzed; videos/stickers/audio are ignored (text/caption still
  ingested).
- Media disk usage grows unbounded (slow at personal volume); pruning is a
  future task.
- Only one poller per bot token: never run the agent or test `getUpdates`
  calls against the same token elsewhere (causes HTTP 409).
- If the bot is ever added to a group, disable BotFather privacy mode or it
  will not see ordinary messages.

## Tests

Offline unit tests (no network, temp SQLite):

```powershell
python -m unittest test_tg_ingest_agent -v
```

(No Python on the Windows workstation? Run them on the VPS from the stage
dir: `python3 -m unittest test_tg_ingest_agent -v`.)

## Quick verification queries (on the VPS)

```bash
sqlite3 /var/lib/tg-ingest-agent/ingest.db \
  "SELECT id, category, status, substr(summary,1,60) FROM messages ORDER BY id DESC LIMIT 5;"
sqlite3 /var/lib/tg-ingest-agent/ingest.db \
  "SELECT message_id, url FROM urls ORDER BY id DESC LIMIT 5;"
journalctl -u tg-ingest-agent -n 30 --no-pager
```
