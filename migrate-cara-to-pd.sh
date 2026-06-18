#!/usr/bin/env bash
# Migrate Cara (tg-ingest-agent) from the current host (Pilot-VPS) to the PD VPS
# (174.138.108.85), which was freed when the PD platform was retired 2026-06-18.
#
# Moves ALL of Cara's state in one consistent snapshot:
#   - the SQLite DB  -> notes, reminders, journals (categories.kind), AND all
#     memory: boss_profile_items, memory_candidates, self_facts,
#     relationship_events, cara_life, preferences, facts, chunks (embeddings),
#     conversation, traces/trace_events, issues, events/jobs, llm_usage.
#   - media/        -> stored photos/files by file_id.
#   - the env file  -> bot token, DO key, allowlist, timezone, STT settings
#     (same bot token => Cara keeps her identity; no re-pairing).
# Then installs the app + a local whisper.cpp STT server on the target and cuts
# over. The app CODE ships via the normal deploy.sh (tests run on the box).
#
# SAFETY
#   - Default mode is `check` (read-only pre-flight). The destructive cutover
#     runs only with `--go` and a typed confirmation.
#   - The SOURCE install is NEVER deleted, only stopped + disabled, so rollback
#     is just re-enabling it (until the target has received new traffic).
#   - One bot token => exactly one long-poller (Telegram 409). The cutover STOPS
#     the source before STARTING the target.
#   - The local staging dir briefly holds the DB + env (secrets); it is removed
#     at the end. Keep it on an encrypted disk; it is git-ignored.
set -euo pipefail

# --- connection config (override via env) -----------------------------------
SRC_HOST="${SRC_HOST:-root@209.38.175.16}"          # Pilot-VPS (current Cara host)
SRC_PORT="${SRC_PORT:-49191}"
SRC_KEY="${SRC_KEY:-$HOME/.ssh/do-pilot}"
SRC_KH="${SRC_KH:-known_hosts_pilot_rnd}"

TGT_HOST="${TGT_HOST:-root@174.138.108.85}"          # PD VPS (new Cara host)
TGT_PORT="${TGT_PORT:-22}"
TGT_KEY="${TGT_KEY:-$HOME/.ssh/digitalocean-dataplatform-asus}"
TGT_KH="${TGT_KH:-known_hosts_pd_dataplatform}"      # supply if not in this repo

STATE_DIR=/var/lib/tg-ingest-agent
ENV_FILE=/etc/tg-ingest-agent.env
SERVICE=tg-ingest-agent
APP_USER="${APP_USER:-tg-ingest}"
WHISPER_PORT="${WHISPER_PORT:-8089}"
STAGE="${STAGE:-./.migrate-stage}"

SSH_SRC=(ssh -i "$SRC_KEY" -p "$SRC_PORT" -o IdentitiesOnly=yes -o ConnectTimeout=25
         -o UserKnownHostsFile="$SRC_KH" -o ServerAliveInterval=15)
SSH_TGT=(ssh -i "$TGT_KEY" -p "$TGT_PORT" -o IdentitiesOnly=yes -o ConnectTimeout=25
         -o UserKnownHostsFile="$TGT_KH" -o ServerAliveInterval=15)
SCP_SRC=(scp -i "$SRC_KEY" -P "$SRC_PORT" -o IdentitiesOnly=yes -o UserKnownHostsFile="$SRC_KH")
SCP_TGT=(scp -i "$TGT_KEY" -P "$TGT_PORT" -o IdentitiesOnly=yes -o UserKnownHostsFile="$TGT_KH")

log(){ echo "[$(date -u +%H:%M:%S)] $*"; }
die(){ echo "ERROR: $*" >&2; exit 1; }

preflight(){
  log "== SOURCE ($SRC_HOST) =="
  "${SSH_SRC[@]}" "$SRC_HOST" "
    echo -n 'service: '; systemctl is-active $SERVICE || true
    echo -n 'db: '; du -h $STATE_DIR/ingest.db 2>/dev/null || echo 'NO DB'
    echo -n 'media: '; du -sh $STATE_DIR/media 2>/dev/null || echo '(none)'
    echo -n 'env: '; (test -f $ENV_FILE && echo present || echo MISSING)
    sqlite3 --version | head -1" || die "source unreachable"
  log "== TARGET ($TGT_HOST) =="
  "${SSH_TGT[@]}" "$TGT_HOST" "
    free -m | awk 'NR==1||NR==2'
    df -h / | tail -1
    echo -n 'cpu: '; nproc; python3 --version
    (ls -d /opt/tg-ingest-agent >/dev/null 2>&1 && echo 'WARN: target already has Cara') || echo 'target clean'" \
    || die "target unreachable"
  log "Pre-flight OK. Re-run with --go to perform the cutover."
}

snapshot_source(){
  log "Freezing source poller (stop $SERVICE)…"
  "${SSH_SRC[@]}" "$SRC_HOST" "systemctl stop $SERVICE"
  log "Consistent DB backup + media tar on source…"
  "${SSH_SRC[@]}" "$SRC_HOST" "
    set -e; rm -rf /tmp/cara-migrate; mkdir -p /tmp/cara-migrate
    sqlite3 $STATE_DIR/ingest.db \".backup '/tmp/cara-migrate/ingest.db'\"
    if [ -d $STATE_DIR/media ]; then tar czf /tmp/cara-migrate/media.tgz -C $STATE_DIR media; fi
    cp $ENV_FILE /tmp/cara-migrate/tg-ingest-agent.env
    ls -la /tmp/cara-migrate"
  mkdir -p "$STAGE"; chmod 700 "$STAGE"
  "${SCP_SRC[@]}" "$SRC_HOST:/tmp/cara-migrate/ingest.db" "$STAGE/ingest.db"
  "${SCP_SRC[@]}" "$SRC_HOST:/tmp/cara-migrate/tg-ingest-agent.env" "$STAGE/tg-ingest-agent.env"
  "${SSH_SRC[@]}" "$SRC_HOST" "test -f /tmp/cara-migrate/media.tgz" \
    && "${SCP_SRC[@]}" "$SRC_HOST:/tmp/cara-migrate/media.tgz" "$STAGE/media.tgz" \
    || log "(no media to copy)"
  "${SSH_SRC[@]}" "$SRC_HOST" "rm -rf /tmp/cara-migrate"
  log "Snapshot staged locally in $STAGE."
}

install_app(){
  log "Installing app code on target via deploy.sh (runs the test suite on the box)…"
  DEPLOY_HOST="$TGT_HOST" DEPLOY_PORT="$TGT_PORT" DEPLOY_KEY="$TGT_KEY" DEPLOY_KH="$TGT_KH" \
    ./deploy.sh   # installer creates user/dirs/unit + apt deps incl. python3-pdfminer; won't start (no env yet)
  log "Building whisper.cpp STT server on target…"
  "${SCP_TGT[@]}" install-whisper-pilot-remote.sh "$TGT_HOST:/root/install-whisper.sh"
  "${SSH_TGT[@]}" "$TGT_HOST" "bash /root/install-whisper.sh"
}

restore_state(){
  log "Restoring DB + media + env on target…"
  "${SSH_TGT[@]}" "$TGT_HOST" "systemctl stop $SERVICE 2>/dev/null || true; mkdir -p $STATE_DIR/media"
  "${SCP_TGT[@]}" "$STAGE/ingest.db" "$TGT_HOST:$STATE_DIR/ingest.db"
  # clean any stale WAL sidecars (the .backup is a single consistent file)
  "${SSH_TGT[@]}" "$TGT_HOST" "rm -f $STATE_DIR/ingest.db-wal $STATE_DIR/ingest.db-shm"
  if [ -f "$STAGE/media.tgz" ]; then
    "${SCP_TGT[@]}" "$STAGE/media.tgz" "$TGT_HOST:/tmp/media.tgz"
    "${SSH_TGT[@]}" "$TGT_HOST" "tar xzf /tmp/media.tgz -C $STATE_DIR && rm -f /tmp/media.tgz"
  fi
  "${SCP_TGT[@]}" "$STAGE/tg-ingest-agent.env" "$TGT_HOST:$ENV_FILE"
  # ensure STT points at the local whisper-server on this host (idempotent upsert)
  "${SSH_TGT[@]}" "$TGT_HOST" "
    set -e
    grep -q '^STT_MODE='          $ENV_FILE || echo 'STT_MODE=local_server'                         >> $ENV_FILE
    grep -q '^WHISPER_SERVER_URL=' $ENV_FILE || echo 'WHISPER_SERVER_URL=http://127.0.0.1:$WHISPER_PORT' >> $ENV_FILE
    chmod 600 $ENV_FILE
    chown -R $APP_USER:$APP_USER $STATE_DIR
    chmod 600 $STATE_DIR/ingest.db"
}

start_and_verify(){
  log "Starting Cara on target…"
  "${SSH_TGT[@]}" "$TGT_HOST" "systemctl enable --now $SERVICE; sleep 3
    echo -n 'service: '; systemctl is-active $SERVICE
    echo '--- recent log ---'
    journalctl -u $SERVICE -n 25 --no-pager | tail -25"
  log "Verify above: 'active', a getUpdates poll, and NO 'HTTP 409' (would mean the source is still polling)."
}

retire_source(){
  log "Disabling source service (kept installed as cold standby for rollback)…"
  "${SSH_SRC[@]}" "$SRC_HOST" "systemctl disable $SERVICE; systemctl is-enabled $SERVICE || true"
}

cleanup(){
  log "Removing local staging (held the DB + env/secrets)…"
  rm -rf "$STAGE"
}

case "${1:-check}" in
  check) preflight ;;
  --go)
    preflight
    echo
    echo "This will STOP Cara on $SRC_HOST, copy all state to $TGT_HOST, install"
    echo "the app + whisper there, start Cara on the target, and disable the source."
    echo "There will be a short ingest/voice gap during the copy."
    read -r -p 'Type MIGRATE to proceed: ' ans
    [ "$ans" = "MIGRATE" ] || die "aborted (no changes made beyond the read-only pre-flight)"
    snapshot_source
    install_app
    restore_state
    start_and_verify
    retire_source
    cleanup
    log "DONE. Verify on Telegram (send a note; 'покажи заметки'; reminders; a voice note)."
    log "Rollback window: if the target misbehaves BEFORE it receives new messages,"
    log "  re-enable the source:  ${SSH_SRC[*]} $SRC_HOST 'systemctl enable --now $SERVICE'"
    log "  (after the target has taken traffic, copy its DB back to the source first)."
    ;;
  *) die "usage: $0 [check|--go]" ;;
esac
