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

if [ ! -f "$STAGE_DIR/tg_ingest_agent.py" ]; then
  echo "Missing $STAGE_DIR/tg_ingest_agent.py (stage it first)." >&2
  exit 1
fi

BACKUP_DIR="/root/codex-hardening-backups/$(date -u +%Y%m%dT%H%M%SZ)-tg-ingest-agent"
mkdir -p "$BACKUP_DIR"
for existing in "$ENV_FILE" "$UNIT_FILE" "$APP_DIR/agent.py"; do
  if [ -f "$existing" ]; then
    cp -a "$existing" "$BACKUP_DIR/"
  fi
done

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends python3 ca-certificates sqlite3

id "$APP_USER" >/dev/null 2>&1 || \
  useradd --system --home "$STATE_DIR" --shell /usr/sbin/nologin "$APP_USER"

install -d -m 0755 "$APP_DIR"
install -d -m 0700 -o "$APP_USER" -g "$APP_USER" "$STATE_DIR" "$STATE_DIR/media"
install -m 0755 "$STAGE_DIR/tg_ingest_agent.py" "$APP_DIR/agent.py"

if [ ! -f "$ENV_FILE" ]; then
  cat >"$ENV_FILE" <<'ENV'
# tg-ingest-agent configuration. Required values are marked REPLACE_ME.
# systemd EnvironmentFile: no inline comments after values, no quotes needed.
TELEGRAM_BOT_TOKEN=REPLACE_ME
# Comma-separated numeric chat ids allowed to feed the bot.
ALLOWED_CHAT_IDS=REPLACE_ME
DO_MODEL_ACCESS_KEY=REPLACE_ME
# Comma- or pipe-separated fixed category list, e.g. news,tools,jobs,ideas
CATEGORIES=REPLACE_ME
# Optional overrides (defaults shown):
# CATEGORIES_FILE=/etc/tg-ingest-agent/categories.txt
# FALLBACK_CATEGORY=uncategorized
# DO_CHAT_MODEL=anthropic-claude-haiku-4.5
# DO_INFERENCE_BASE_URL=https://inference.do-ai.run/v1
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
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/var/lib/tg-ingest-agent
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
UNIT

python3 -m py_compile "$APP_DIR/agent.py"
systemctl daemon-reload
systemctl enable "$SERVICE.service"

if grep -q 'REPLACE_ME' "$ENV_FILE"; then
  systemctl stop "$SERVICE.service" 2>/dev/null || true
  echo "WARNING: $ENV_FILE still contains REPLACE_ME placeholders."
  echo "Fill in the secrets, then: systemctl start $SERVICE"
else
  systemctl restart "$SERVICE.service"
  sleep 2
  systemctl --no-pager --full status "$SERVICE.service" || true
fi

echo "tg-ingest-agent install complete (backups in $BACKUP_DIR)"
