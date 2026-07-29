#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root." >&2
  exit 1
fi

STAGE_DIR="${STAGE_DIR:-/root/tg-ingest-agent-stage}"
SERVICE=tg-ingest-agent
WORKER_SERVICE=cara-worker
MENTOR_SERVICE=cara-mentor
MENTOR_RUNNER_SERVICE=cara-mentor-runner
APP_USER=tg-ingest
WORKER_USER=cara-worker
WORKER_GROUP=cara-worker-spool
MENTOR_USER=cara-mentor
MENTOR_GROUP=cara-mentor-spool
MENTOR_RUNNER_USER=cara-mentor-runner
MENTOR_RUNNER_GROUP=cara-mentor-runner-spool
APP_DIR=/opt/tg-ingest-agent
STATE_DIR=/var/lib/tg-ingest-agent
ENV_FILE=/etc/tg-ingest-agent.env
MENTOR_ENV_FILE=/etc/cara-mentor.env
MENTOR_RUNNER_ENV_FILE=/etc/cara-mentor-runner.env
MENTOR_STATE=/var/lib/cara-mentor
MENTOR_RUNNER_STATE=/var/lib/cara-mentor-runner
MENTOR_SOURCE=/opt/cara-mentor-source
UNIT_FILE=/etc/systemd/system/${SERVICE}.service
WORKER_UNIT_FILE=/etc/systemd/system/${WORKER_SERVICE}.service
MENTOR_UNIT_FILE=/etc/systemd/system/${MENTOR_SERVICE}.service
MENTOR_RUNNER_UNIT_FILE=/etc/systemd/system/${MENTOR_RUNNER_SERVICE}.service
UNIT_SRC=tg-ingest-agent.service
WORKER_UNIT_SRC=cara-worker.service
MENTOR_UNIT_SRC=cara-mentor.service
MENTOR_RUNNER_UNIT_SRC=cara-mentor-runner.service
ENV_TEMPLATE=tg-ingest-agent.env.example

MODULES="common.py texts.py store.py tg_api.py llm.py router.py ingest.py reminders.py reminders_svc.py notes_svc.py spend.py gcal.py review.py sysinfo.py fetch.py web_search.py storage.py backup.py knowledge.py skill_manifest.py tool_broker.py tasking.py task_runner.py tasks_svc.py worker_client.py improvement.py deployment_notice.py cara_worker.py mentor_protocol.py mentor_client.py cara_mentor.py mentor_runner.py verify_task_runtime.py verify_mentor_runtime.py trace.py events.py jobs.py runtime.py self_model.py boss_model.py persona.py converse.py memory_curator.py relationship.py action_truth.py proactive.py pdftext.py hermes.py journals.py media.py"

# The unit file and the env template are STAGED FILES now, not heredocs in this
# script: one copy of each, versioned in the repo. A stage dir that predates that
# change must fail loudly here rather than install half a service.
for required in tg_ingest_agent.py verify-cara-runtime.sh "$UNIT_SRC" \
  "$WORKER_UNIT_SRC" "$MENTOR_UNIT_SRC" "$MENTOR_RUNNER_UNIT_SRC" \
  "$ENV_TEMPLATE" $MODULES; do
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
for existing in "$ENV_FILE" "$MENTOR_ENV_FILE" "$MENTOR_RUNNER_ENV_FILE" \
  "$UNIT_FILE" "$WORKER_UNIT_FILE" \
  "$MENTOR_UNIT_FILE" "$MENTOR_RUNNER_UNIT_FILE"; do
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
apt-get install -y --no-install-recommends python3 ca-certificates sqlite3 \
  python3-pdfminer git

id "$APP_USER" >/dev/null 2>&1 || \
  useradd --system --home "$STATE_DIR" --shell /usr/sbin/nologin "$APP_USER"
getent group "$WORKER_GROUP" >/dev/null 2>&1 || groupadd --system "$WORKER_GROUP"
id "$WORKER_USER" >/dev/null 2>&1 || \
  useradd --system --gid "$WORKER_GROUP" --home /var/lib/cara-worker \
    --shell /usr/sbin/nologin "$WORKER_USER"
getent group "$MENTOR_GROUP" >/dev/null 2>&1 || groupadd --system "$MENTOR_GROUP"
id "$MENTOR_USER" >/dev/null 2>&1 || \
  useradd --system --gid "$MENTOR_GROUP" --home "$MENTOR_STATE" \
    --shell /usr/sbin/nologin "$MENTOR_USER"
getent group "$MENTOR_RUNNER_GROUP" >/dev/null 2>&1 || \
  groupadd --system "$MENTOR_RUNNER_GROUP"
id "$MENTOR_RUNNER_USER" >/dev/null 2>&1 || \
  useradd --system --gid "$MENTOR_RUNNER_GROUP" --home "$MENTOR_RUNNER_STATE" \
    --shell /usr/sbin/nologin "$MENTOR_RUNNER_USER"
usermod -a -G "$MENTOR_GROUP","$MENTOR_RUNNER_GROUP" "$APP_USER"

install -d -m 0755 "$APP_DIR"
install -d -m 0700 -o "$APP_USER" -g "$APP_USER" "$STATE_DIR" "$STATE_DIR/media"
install -d -m 0700 -o "$APP_USER" -g "$APP_USER" "$STATE_DIR/task-artifacts"
# One-way spool ownership: Cara can create/remove requests and cancellation
# markers but cannot alter results; the networkless worker can create/remove
# results but cannot alter requests/cancellation.
install -d -m 0750 -o root -g "$WORKER_GROUP" /var/lib/cara-worker
install -d -m 0750 -o root -g "$WORKER_GROUP" /var/lib/cara-worker/spool
install -d -m 2750 -o "$APP_USER" -g "$WORKER_GROUP" \
  /var/lib/cara-worker/spool/requests /var/lib/cara-worker/spool/cancel
install -d -m 2750 -o "$WORKER_USER" -g "$WORKER_GROUP" \
  /var/lib/cara-worker/spool/results
install -d -m 0700 -o "$WORKER_USER" -g "$WORKER_GROUP" \
  /var/lib/cara-worker/scratch
install -d -m 0750 -o root -g "$MENTOR_GROUP" "$MENTOR_STATE"
install -d -m 0750 -o root -g "$MENTOR_GROUP" "$MENTOR_STATE/spool"
install -d -m 2750 -o "$APP_USER" -g "$MENTOR_GROUP" \
  "$MENTOR_STATE/spool/requests"
install -d -m 2750 -o "$MENTOR_USER" -g "$MENTOR_GROUP" \
  "$MENTOR_STATE/spool/results"
install -d -m 0700 -o "$MENTOR_USER" -g "$MENTOR_GROUP" \
  "$MENTOR_STATE/inflight"
install -d -m 0700 -o "$MENTOR_USER" -g "$MENTOR_GROUP" \
  "$MENTOR_STATE/usage"
install -d -m 0750 -o root -g "$MENTOR_RUNNER_GROUP" "$MENTOR_RUNNER_STATE"
install -d -m 0750 -o root -g "$MENTOR_RUNNER_GROUP" \
  "$MENTOR_RUNNER_STATE/spool"
install -d -m 2750 -o "$APP_USER" -g "$MENTOR_RUNNER_GROUP" \
  "$MENTOR_RUNNER_STATE/spool/requests"
install -d -m 2750 -o "$MENTOR_RUNNER_USER" -g "$MENTOR_RUNNER_GROUP" \
  "$MENTOR_RUNNER_STATE/spool/results"
install -d -m 0700 -o "$MENTOR_RUNNER_USER" -g "$MENTOR_RUNNER_GROUP" \
  "$MENTOR_RUNNER_STATE/scratch"
install -m 0755 "$STAGE_DIR/tg_ingest_agent.py" "$APP_DIR/agent.py"
for module in $MODULES; do
  install -m 0644 "$STAGE_DIR/$module" "$APP_DIR/$module"
done
install -m 0755 "$STAGE_DIR/verify-cara-runtime.sh" \
  "$APP_DIR/verify-cara-runtime.sh"
rm -rf "$APP_DIR/__pycache__"

# Immutable, secret-free source snapshot for Mentor analysis and candidate
# testing. It is rebuilt atomically from the exact tested stage; no .git,
# environment values, deployment key, DB, media, or generated artifact enters.
MENTOR_SOURCE_TMP=$(mktemp -d "${MENTOR_SOURCE}.tmp.XXXXXX")
chmod 0755 "$MENTOR_SOURCE_TMP"
for source_file in "$STAGE_DIR"/*.py \
  "$STAGE_DIR/deploy.sh" \
  "$STAGE_DIR/install-tg-ingest-agent-pilot-remote.sh" \
  "$STAGE_DIR/install-whisper-pilot-remote.sh" \
  "$STAGE_DIR/verify-cara-runtime.sh" \
  "$STAGE_DIR/tg-ingest-agent.env.example" \
  "$STAGE_DIR/tg-ingest-agent.service" \
  "$STAGE_DIR/cara-worker.service" \
  "$STAGE_DIR/cara-mentor.service" \
  "$STAGE_DIR/cara-mentor-runner.service"; do
  install -m 0644 "$source_file" "$MENTOR_SOURCE_TMP/$(basename "$source_file")"
done

# Build stamp covers every runtime file this installer owns, including the two
# systemd sandboxes and the live verifier—not only Python imports.
( cd "$APP_DIR" && cat agent.py $MODULES verify-cara-runtime.sh \
    "$STAGE_DIR/$UNIT_SRC" "$STAGE_DIR/$WORKER_UNIT_SRC" \
    "$STAGE_DIR/$MENTOR_UNIT_SRC" "$STAGE_DIR/$MENTOR_RUNNER_UNIT_SRC" |
    sha1sum | cut -c1-12 ) > "$APP_DIR/VERSION"
chmod 0644 "$APP_DIR/VERSION"
install -m 0644 "$APP_DIR/VERSION" "$MENTOR_SOURCE_TMP/VERSION"
( cd "$MENTOR_SOURCE_TMP" && {
    for source_file in $(find . -maxdepth 1 -type f ! -name SOURCE_HASH -printf '%f\n' | sort); do
      printf '%s\0' "$source_file"
      cat "$source_file"
    done
  } | sha256sum | cut -d' ' -f1 ) > "$MENTOR_SOURCE_TMP/SOURCE_HASH"
chmod 0644 "$MENTOR_SOURCE_TMP/SOURCE_HASH"
rm -rf "$MENTOR_SOURCE"
mv "$MENTOR_SOURCE_TMP" "$MENTOR_SOURCE"
python3 "$APP_DIR/deployment_notice.py" write-installed \
  --manifest "$APP_DIR/DEPLOYMENT.json" \
  --build-version "$(cat "$APP_DIR/VERSION")" \
  --source-revision "${DEPLOY_SOURCE_REVISION:-unknown}" \
  --source-dirty "${DEPLOY_SOURCE_DIRTY:-false}" \
  --test-summary "${DEPLOY_TEST_SUMMARY:-verification gate not reported}" \
  --backup-dir "$BACKUP_DIR"

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
install -m 0644 "$STAGE_DIR/$WORKER_UNIT_SRC" \
  "/etc/systemd/system/${WORKER_SERVICE}.service"
install -m 0644 "$STAGE_DIR/$MENTOR_UNIT_SRC" "$MENTOR_UNIT_FILE"
install -m 0644 "$STAGE_DIR/$MENTOR_RUNNER_UNIT_SRC" \
  "$MENTOR_RUNNER_UNIT_FILE"
python3 "$APP_DIR/cara_mentor.py" write-env "$ENV_FILE" "$MENTOR_ENV_FILE"
chown root:"$MENTOR_GROUP" "$MENTOR_ENV_FILE"
chmod 0640 "$MENTOR_ENV_FILE"
python3 "$APP_DIR/cara_mentor.py" write-runner-env \
  "$ENV_FILE" "$MENTOR_RUNNER_ENV_FILE"
chown root:"$MENTOR_RUNNER_GROUP" "$MENTOR_RUNNER_ENV_FILE"
chmod 0640 "$MENTOR_RUNNER_ENV_FILE"

python3 -m py_compile "$APP_DIR/agent.py" $(for m in $MODULES; do echo "$APP_DIR/$m"; done)
rm -rf "$APP_DIR/__pycache__"
systemctl daemon-reload
systemctl enable "$SERVICE.service"
systemctl enable "$WORKER_SERVICE.service"
systemctl enable "$MENTOR_SERVICE.service"
systemctl enable "$MENTOR_RUNNER_SERVICE.service"

# Anchored to line start: only an ACTIVE `KEY=REPLACE_ME` counts. A commented
# example line (`# SPACES_KEY=REPLACE_ME`, present in env.example) must not stop
# a healthy service on reinstall. The `=` must stay in the pattern.
if grep -qE '^[A-Za-z_][A-Za-z0-9_]*=REPLACE_ME' "$ENV_FILE"; then
  systemctl stop "$SERVICE.service" 2>/dev/null || true
  systemctl stop "$WORKER_SERVICE.service" 2>/dev/null || true
  systemctl stop "$MENTOR_SERVICE.service" 2>/dev/null || true
  systemctl stop "$MENTOR_RUNNER_SERVICE.service" 2>/dev/null || true
  echo "WARNING: $ENV_FILE still contains REPLACE_ME placeholders."
  echo "Fill in the secrets, then: systemctl start $SERVICE"
else
  systemctl restart "$WORKER_SERVICE.service"
  systemctl restart "$MENTOR_RUNNER_SERVICE.service"
  systemctl restart "$MENTOR_SERVICE.service"
  systemctl restart "$SERVICE.service"
  sleep 2
  systemctl is-active --quiet "$WORKER_SERVICE.service" || {
    systemctl --no-pager --full status "$WORKER_SERVICE.service"; exit 1; }
  systemctl is-active --quiet "$MENTOR_SERVICE.service" || {
    systemctl --no-pager --full status "$MENTOR_SERVICE.service"; exit 1; }
  systemctl is-active --quiet "$MENTOR_RUNNER_SERVICE.service" || {
    systemctl --no-pager --full status "$MENTOR_RUNNER_SERVICE.service"; exit 1; }
  systemctl is-active --quiet "$SERVICE.service" || {
    systemctl --no-pager --full status "$SERVICE.service"; exit 1; }
fi

echo "tg-ingest-agent install complete (backups in $BACKUP_DIR)"
