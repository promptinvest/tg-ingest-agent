# tg-ingest-agent

Conversational personal assistant on Telegram, running on Pilot-VPS.

One dedicated bot, no slash commands needed: you write or **speak** (voice
notes) in Russian or English, a closed-world intent router decides which
skill handles the request, and everything risky is confirmed conversationally
("да" / "нет, лучше крипта" / "через полчаса"). Forwarded channel posts are
ingested automatically without burning router tokens.

## Skills

- **Ingest** — stores forwarded posts and notes (text, URLs, photos; an album
  counts as one message) in SQLite + media files; a vision-capable LLM on
  DigitalOcean Gradient serverless inference suggests a category (reusing the
  taxonomy of previously confirmed categories, matching by meaning across
  RU/EN) plus a summary in the source language. You confirm by reply or
  inline button; only confirmed categories enter the taxonomy.
- **Reminders** — "напомни завтра в 10 позвонить в банк", one-shot or
  daily/weekly; the parsed draft is confirmed before scheduling; fired
  reminders snooze via natural replies ("через 30 минут", "готово").
- **Spend** — every LLM/STT call goes through a budget-guarded gateway that
  prices and logs usage; ask "сколько потратили на AI за месяц?" for a
  breakdown by skill and model. At 80% of `BUDGET_DAILY_USD`/`BUDGET_MONTHLY_USD`
  the bot warns; at 100% model calls stop (messages keep being stored and are
  processed after the period resets).
- **Memory** — "запомни: отвечай по-английски", "что ты обо мне знаешь?",
  "забудь...". Special keys: `language`, `timezone_offset`; everything else
  is stored as notes. The ingest skill also **learns**: operator corrections
  are fed back into the suggestion prompt, and after `HABIT_THRESHOLD`
  same-category confirmations from one source the bot offers to auto-confirm
  that source.

## Guardrails

- Closed action set in the router — there is no generic "chat" action, so the
  bot cannot drift into GPT-style conversation; off-topic requests get a fixed
  capabilities reply.
- The LLM never free-writes to the user: model output is JSON filling
  validated slots; user-facing text comes from bilingual templates
  (`texts.py`).
- Forwarded content is wrapped in delimiters and treated as untrusted data
  (prompt-injection defense); low router confidence asks a clarifying
  question instead of guessing.
- Chat-ID allowlist; per-skill argument validation; budget hard stop.

Stdlib-only Python 3 (urllib, sqlite3); long polling, so no inbound ports —
UFW on Pilot-VPS stays SSH-only. Runs as systemd service `tg-ingest-agent`
under the non-root user `tg-ingest`.

## Module layout

| File | Responsibility |
|---|---|
| `tg_ingest_agent.py` | entry point: poll loop, dispatch, pending-action resolution (installed as `agent.py`) |
| `router.py` | closed-world intent router (LLM, JSON-only output) |
| `ingest.py` | message parsing, URL extraction (UTF-16-safe), category suggestion |
| `reminders.py` | reminder drafts, recurrence, local-time rendering |
| `spend.py` | usage aggregation and reports |
| `llm.py` | DO inference gateway: chat, STT (Whisper), pricing, budgets |
| `store.py` | SQLite schema + helpers (messages, categories, usage, prefs, reminders…) |
| `tg_api.py` | Telegram Bot API client |
| `texts.py` | bilingual (ru/en) reply templates |
| `common.py` | config loading |

## Layout on the VPS

| Path | Purpose |
|---|---|
| `/opt/tg-ingest-agent/` | the service modules (`agent.py` + the files above) |
| `/etc/tg-ingest-agent.env` | config + secrets, mode 0600 (see `tg-ingest-agent.env.example`) |
| `/var/lib/tg-ingest-agent/ingest.db` | SQLite |
| `/var/lib/tg-ingest-agent/media/` | downloaded photos and voice files |
| `/etc/systemd/system/tg-ingest-agent.service` | unit (reference copy: `tg-ingest-agent.service`) |

## One-time setup

1. Create a new bot with @BotFather (`/newbot`), save the token. Do NOT reuse
   the fleet notification bot from `/etc/codex-auto-update/telegram.env`.
2. Have a dedicated DO Gradient serverless inference access key
   (`DO_MODEL_ACCESS_KEY`).
3. Check the STT model slug in the DO console and set `STT_MODEL` if it
   differs from `whisper-large-v3` (voice input degrades gracefully when the
   endpoint is unavailable).

## Deploy (from Windows, repo root)

```powershell
ssh -i ~/.ssh/do-pilot -p 49191 -o UserKnownHostsFile=known_hosts_pilot_rnd root@209.38.175.16 "mkdir -p /root/tg-ingest-agent-stage"
scp -i ~/.ssh/do-pilot -P 49191 -o UserKnownHostsFile=known_hosts_pilot_rnd *.py install-tg-ingest-agent-pilot-remote.sh root@209.38.175.16:/root/tg-ingest-agent-stage/
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

- **Confirmation flow**: ingest status goes `pending` → `suggested` →
  `confirmed`; reminder drafts and habit proposals follow the same
  suggest-then-confirm shape via a per-chat pending action (1 h TTL). Inline
  buttons remain as a silent fallback alongside conversational replies.
- **Albums** are buffered ~3 s and stored as ONE message row with N image
  rows.
- **Duplicates**: redelivered updates are dropped via
  `UNIQUE(chat_id, tg_message_id)`; re-forwarded posts get `status=duplicate`
  with the original's classification copied, no LLM call.
- **LLM outage / budget stop**: store-first. Pending rows retry every
  `RETRY_INTERVAL_SECONDS` (max `LLM_MAX_ATTEMPTS`, then `failed`).
- **Voice**: OGG voice notes are downloaded, transcribed via the
  OpenAI-compatible `/v1/audio/transcriptions` endpoint, quoted back to you,
  then routed like text.
- **Hidden command aliases** for debugging: `/start`, `/stats`, `/categories`.
- **Bilingual categories**: dedup uses Python `casefold()` (SQLite NOCASE is
  ASCII-only and would split «Крипта»/«крипта»).

## Known limitations

- Images sent as **documents** are stored metadata-only and not analyzed;
  videos/stickers are ignored (text/caption still ingested).
- Media disk usage grows unbounded (slow at personal volume); pruning is a
  future task.
- Only one poller per bot token: never run the agent or test `getUpdates`
  calls against the same token elsewhere (causes HTTP 409).
- Recurrence is limited to daily/weekly; no calendar sync yet.
- The STT model slug on DO must be verified once at deploy time.

## Tests

Offline unit tests (no network, temp SQLite):

```powershell
python -m unittest discover -p "test_*.py" -v
```

(No Python on the Windows workstation? Run them on the VPS from the stage
dir: `python3 -m unittest discover -p "test_*.py"`.)

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
