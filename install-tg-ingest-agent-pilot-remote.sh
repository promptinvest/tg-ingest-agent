#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root." >&2
  exit 1
fi

STAGE_DIR="${STAGE_DIR:-/root/tg-ingest-agent-stage}"
SERVICE=tg-ingest-agent
APP_USER=tg-ingest
APP_DIR=/opt/tg-ingest-agent
STATE_DIR=/var/lib/tg-ingest-agent
ENV_FILE=/etc/tg-ingest-agent.env
UNIT_FILE=/etc/systemd/system/${SERVICE}.service

MODULES="common.py texts.py store.py tg_api.py llm.py router.py ingest.py reminders.py spend.py gcal.py review.py sysinfo.py fetch.py storage.py knowledge.py skill_manifest.py trace.py events.py jobs.py runtime.py self_model.py boss_model.py persona.py converse.py memory_curator.py relationship.py action_truth.py proactive.py"

for required in tg_ingest_agent.py $MODULES; do
  if [ ! -f "$STAGE_DIR/$required" ]; then
    echo "Missing $STAGE_DIR/$required (stage it first)." >&2
    exit 1
  fi
done

BACKUP_DIR="/root/codex-hardening-backups/$(date -u +%Y%m%dT%H%M%SZ)-tg-ingest-agent"
mkdir -p "$BACKUP_DIR"
for existing in "$ENV_FILE" "$UNIT_FILE"; do
  if [ -f "$existing" ]; then
    cp -a "$existing" "$BACKUP_DIR/"
  fi
done
if [ -d "$APP_DIR" ]; then
  cp -a "$APP_DIR" "$BACKUP_DIR/opt-app"
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends python3 ca-certificates sqlite3

id "$APP_USER" >/dev/null 2>&1 || \
  useradd --system --home "$STATE_DIR" --shell /usr/sbin/nologin "$APP_USER"

install -d -m 0755 "$APP_DIR"
install -d -m 0700 -o "$APP_USER" -g "$APP_USER" "$STATE_DIR" "$STATE_DIR/media"
install -m 0755 "$STAGE_DIR/tg_ingest_agent.py" "$APP_DIR/agent.py"
for module in $MODULES; do
  install -m 0644 "$STAGE_DIR/$module" "$APP_DIR/$module"
done
rm -rf "$APP_DIR/__pycache__"

if [ ! -f "$ENV_FILE" ]; then
  cat >"$ENV_FILE" <<'ENV'
# tg-ingest-agent configuration. Required values are marked REPLACE_ME.
# systemd EnvironmentFile: no inline comments after values, no quotes needed.
TELEGRAM_BOT_TOKEN=REPLACE_ME
# Comma-separated numeric chat ids allowed to feed the bot.
ALLOWED_CHAT_IDS=REPLACE_ME
DO_MODEL_ACCESS_KEY=REPLACE_ME
# Optional overrides (defaults shown):
# BOT_LANGUAGE=ru
# TIMEZONE_OFFSET_HOURS=3
# BUDGET_DAILY_USD=1.0
# BUDGET_MONTHLY_USD=15.0
# STT_ENABLED=true
# STT_MODEL=whisper-large-v3
# ROUTER_MODEL=anthropic-claude-haiku-4.5
# ROUTER_CONFIDENCE_THRESHOLD=0.6
# HABIT_THRESHOLD=10
# Optional seed taxonomy; categories also emerge from confirmed suggestions.
# CATEGORIES=news,tools,ideas
# Google Calendar sync (optional; .ics export works without it):
# GCAL_CALENDAR_ID=you@gmail.com
# GCAL_SA_KEY_FILE=/etc/tg-ingest-agent/gcal-sa.json
# EVENT_DURATION_MINUTES=30
# CATEGORIES_FILE=/etc/tg-ingest-agent/categories.txt
# FALLBACK_CATEGORY=uncategorized
# DO_CHAT_MODEL=anthropic-claude-haiku-4.5
# DO_INFERENCE_BASE_URL=https://inference.do-ai.run/v1
# PRICING_JSON={"model-id": [in_usd_per_1m, out_usd_per_1m]}
# DB_PATH=/var/lib/tg-ingest-agent/ingest.db
# MEDIA_DIR=/var/lib/tg-ingest-agent/media
# POLL_TIMEOUT_SECONDS=50
# ALBUM_SETTLE_SECONDS=3
# MAX_LLM_IMAGES=4
# LLM_TIMEOUT_SECONDS=90
# LLM_MAX_ATTEMPTS=5
# RETRY_INTERVAL_SECONDS=300
ENV
  chmod 0600 "$ENV_FILE"
else
  chmod 0600 "$ENV_FILE"
fi

cat >"$UNIT_FILE" <<'UNIT'
[Unit]
Description=Telegram ingest agent (LLM categorization)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=tg-ingest
Group=tg-ingest
EnvironmentFile=/etc/tg-ingest-agent.env
ExecStart=/usr/bin/python3 /opt/tg-ingest-agent/agent.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/var/lib/tg-ingest-agent
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
UNIT

python3 -m py_compile "$APP_DIR/agent.py" $(for m in $MODULES; do echo "$APP_DIR/$m"; done)
rm -rf "$APP_DIR/__pycache__"
systemctl daemon-reload
systemctl enable "$SERVICE.service"

if grep -q '=REPLACE_ME' "$ENV_FILE"; then
  systemctl stop "$SERVICE.service" 2>/dev/null || true
  echo "WARNING: $ENV_FILE still contains REPLACE_ME placeholders."
  echo "Fill in the secrets, then: systemctl start $SERVICE"
else
  systemctl restart "$SERVICE.service"
  sleep 2
  systemctl --no-pager --full status "$SERVICE.service" || true
fi

echo "tg-ingest-agent install complete (backups in $BACKUP_DIR)"
