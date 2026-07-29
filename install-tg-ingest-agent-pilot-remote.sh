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
UNIT_SRC=tg-ingest-agent.service
ENV_TEMPLATE=tg-ingest-agent.env.example

MODULES="common.py texts.py store.py tg_api.py llm.py router.py ingest.py reminders.py reminders_svc.py notes_svc.py spend.py gcal.py review.py sysinfo.py fetch.py storage.py backup.py knowledge.py skill_manifest.py tool_broker.py tasking.py trace.py events.py jobs.py runtime.py self_model.py boss_model.py persona.py converse.py memory_curator.py relationship.py action_truth.py proactive.py pdftext.py hermes.py journals.py media.py"

# The unit file and the env template are STAGED FILES now, not heredocs in this
# script: one copy of each, versioned in the repo. A stage dir that predates that
# change must fail loudly here rather than install half a service.
for required in tg_ingest_agent.py "$UNIT_SRC" "$ENV_TEMPLATE" $MODULES; do
  if [ ! -f "$STAGE_DIR/$required" ]; then
    echo "Missing $STAGE_DIR/$required (stage it first)." >&2
    exit 1
  fi
done

# Pre-install backups: each one holds a copy of the env file (secrets), and they
# used to accumulate forever — disk growth plus a widening secrets footprint.
# Keep the newest 10 of THIS tool's dirs, and keep the root private.
# `-name '*-tg-ingest-agent'` is load-bearing (2026-07-27): the root is a
# FLEET-SHARED convention — other tools' one-shots (nightly-updater installs,
# TLS/nginx hardening) park their own pre-change state in sibling dirs, and an
# unscoped prune deleted the operator's rollback material for unrelated
# subsystems as soon as ten Cara deploys had passed. Prune only what this
# script itself creates (the $BACKUP_DIR stamp below carries the suffix).
BACKUP_ROOT=/root/codex-hardening-backups
mkdir -p "$BACKUP_ROOT"
chmod 700 "$BACKUP_ROOT"   # every writer here runs as root; the copies hold secrets
find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -name '*-tg-ingest-agent' -printf '%T@ %p\n' 2>/dev/null \
  | sort -rn | tail -n +11 | cut -d' ' -f2- | xargs -r rm -rf -- || true

BACKUP_DIR="$BACKUP_ROOT/$(date -u +%Y%m%dT%H%M%SZ)-tg-ingest-agent"
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
# python3-pdfminer (pdfminer.six) — best-effort PDF text extraction beyond the
# stdlib regex fallback; apt-managed so the nightly updater keeps it current.
apt-get install -y --no-install-recommends python3 ca-certificates sqlite3 python3-pdfminer

id "$APP_USER" >/dev/null 2>&1 || \
  useradd --system --home "$STATE_DIR" --shell /usr/sbin/nologin "$APP_USER"

install -d -m 0755 "$APP_DIR"
install -d -m 0700 -o "$APP_USER" -g "$APP_USER" "$STATE_DIR" "$STATE_DIR/media"
install -m 0755 "$STAGE_DIR/tg_ingest_agent.py" "$APP_DIR/agent.py"
for module in $MODULES; do
  install -m 0644 "$STAGE_DIR/$module" "$APP_DIR/$module"
done
rm -rf "$APP_DIR/__pycache__"

# Build stamp: a content hash of the installed code, so the agent announces a
# new build to the boss only when the code actually changed (not every reboot).
( cd "$APP_DIR" && cat agent.py $MODULES | sha1sum | cut -c1-12 ) > "$APP_DIR/VERSION"
chmod 0644 "$APP_DIR/VERSION"

# Seed a MISSING env file from the tracked example — never touch an existing one
# (a reinstall must not blank the live secrets). This script used to carry its own
# 40-line template, which had already drifted from the example: different budget
# defaults, dozens of keys missing, and no mention of STT_MODE, whose default
# `remote` ships voice audio off the box. One template, one place.
if [ ! -f "$ENV_FILE" ]; then
  install -m 0600 "$STAGE_DIR/$ENV_TEMPLATE" "$ENV_FILE"
else
  chmod 0600 "$ENV_FILE"
fi

# The unit is the tracked tg-ingest-agent.service, installed verbatim. It used to
# be a heredoc here with the repo's copy as a dead duplicate beside it — two files
# to keep in sync, and only one of them deployed.
install -m 0644 "$STAGE_DIR/$UNIT_SRC" "$UNIT_FILE"

python3 -m py_compile "$APP_DIR/agent.py" $(for m in $MODULES; do echo "$APP_DIR/$m"; done)
rm -rf "$APP_DIR/__pycache__"
systemctl daemon-reload
systemctl enable "$SERVICE.service"

# Anchored to line start: only an ACTIVE `KEY=REPLACE_ME` counts. A commented
# example line (`# SPACES_KEY=REPLACE_ME`, present in env.example) must not stop
# a healthy service on reinstall. The `=` must stay in the pattern.
if grep -qE '^[A-Za-z_][A-Za-z0-9_]*=REPLACE_ME' "$ENV_FILE"; then
  systemctl stop "$SERVICE.service" 2>/dev/null || true
  echo "WARNING: $ENV_FILE still contains REPLACE_ME placeholders."
  echo "Fill in the secrets, then: systemctl start $SERVICE"
else
  systemctl restart "$SERVICE.service"
  sleep 2
  systemctl --no-pager --full status "$SERVICE.service" || true
fi

echo "tg-ingest-agent install complete (backups in $BACKUP_DIR)"
