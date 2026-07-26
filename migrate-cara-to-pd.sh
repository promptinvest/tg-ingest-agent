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
#   - The local staging dir briefly holds the DB + env (secrets). It defaults
#     OUTSIDE the OneDrive-synced repo (%LOCALAPPDATA%), is created 0700, and a
#     `trap … EXIT` removes it on EVERY exit path — the old default put an
#     unencrypted copy of the whole DB into a cloud-synced folder and only
#     cleaned it up when the migration succeeded.
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
# Staging holds an unencrypted copy of the DB and of the env file (bot token, DO
# key): it must NEVER default inside this OneDrive-synced repo.
STAGE_BASE="${LOCALAPPDATA:-/tmp}"
STAGE_BASE="${STAGE_BASE//\\//}"                     # Git Bash inherits C:\… paths
STAGE="${STAGE:-$STAGE_BASE/cara-migrate-stage}"

SSH_SRC=(ssh -i "$SRC_KEY" -p "$SRC_PORT" -o IdentitiesOnly=yes -o ConnectTimeout=25
         -o UserKnownHostsFile="$SRC_KH" -o ServerAliveInterval=15)
SSH_TGT=(ssh -i "$TGT_KEY" -p "$TGT_PORT" -o IdentitiesOnly=yes -o ConnectTimeout=25
         -o UserKnownHostsFile="$TGT_KH" -o ServerAliveInterval=15)
SCP_SRC=(scp -i "$SRC_KEY" -P "$SRC_PORT" -o IdentitiesOnly=yes -o UserKnownHostsFile="$SRC_KH")
SCP_TGT=(scp -i "$TGT_KEY" -P "$TGT_PORT" -o IdentitiesOnly=yes -o UserKnownHostsFile="$TGT_KH")

log(){ echo "[$(date -u +%H:%M:%S)] $*"; }
die(){ echo "ERROR: $*" >&2; exit 1; }

cleanup(){
  # Idempotent and armed on EVERY exit path (abort, die, Ctrl-C): the staging
  # dir holds the DB and the env file in clear text.
  [ -d "$STAGE" ] || return 0
  log "Removing local staging (held the DB + env/secrets)…"
  rm -rf "$STAGE" || true
  if [ -d "$STAGE" ]; then
    echo "WARNING: $STAGE could not be removed and still holds an unencrypted DB" >&2
    echo "         copy and the env file (bot token, DO key). Delete it by hand." >&2
  fi
}
trap cleanup EXIT

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
  # umask 077 + chmod 700 + a failure trap: the snapshot is the whole DB and the
  # env file in clear text, and /tmp is world-readable by default — it used to
  # be left there 0755/0644, and to survive an abort.
  "${SSH_SRC[@]}" "$SRC_HOST" "
    set -e; umask 077
    rm -rf /tmp/cara-migrate; mkdir -p /tmp/cara-migrate; chmod 700 /tmp/cara-migrate
    trap 'rc=\$?; [ \$rc -eq 0 ] || rm -rf /tmp/cara-migrate; exit \$rc' EXIT
    sqlite3 $STATE_DIR/ingest.db \".backup '/tmp/cara-migrate/ingest.db'\"
    if [ -d $STATE_DIR/media ]; then tar czf /tmp/cara-migrate/media.tgz -C $STATE_DIR media; fi
    cp $ENV_FILE /tmp/cara-migrate/tg-ingest-agent.env
    chmod 600 /tmp/cara-migrate/*
    ls -la /tmp/cara-migrate"
  umask 077
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
  # Runs while the SOURCE is still live (no token here yet) -> no downtime, no 409.
  # The installer creates user/dirs/unit + apt deps (incl. python3-pdfminer) and,
  # because the env still has REPLACE_ME, ENABLES but leaves the service STOPPED.
  log "Installing app on target (tests + installer; stays stopped until env is placed)…"
  tar czf - *.py install-tg-ingest-agent-pilot-remote.sh tg-ingest-agent.env.example \
        tg-ingest-agent.service \
    | "${SSH_TGT[@]}" "$TGT_HOST" "
        set -e
        rm -rf /root/tg-ingest-agent-stage; mkdir -p /root/tg-ingest-agent-stage
        tar xzf - -C /root/tg-ingest-agent-stage
        cd /root/tg-ingest-agent-stage
        sed -i 's/\r\$//' *.py *.sh tg-ingest-agent.service tg-ingest-agent.env.example
        echo '--- tests ---'; python3 -m unittest discover -p 'test_*.py' 2>&1 | tail -3
        echo '--- install ---'; bash install-tg-ingest-agent-pilot-remote.sh 2>&1 | tail -3"
  log "Building whisper.cpp STT server on target (several minutes)…"
  "${SCP_TGT[@]}" install-whisper-pilot-remote.sh "$TGT_HOST:/root/install-whisper.sh"
  "${SSH_TGT[@]}" "$TGT_HOST" "bash /root/install-whisper.sh 2>&1 | tail -6"
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

confirm_gate(){
  # Non-interactive friendly: CONFIRM=MIGRATE env skips the prompt.
  if [ "${CONFIRM:-}" != "MIGRATE" ]; then
    read -r -p 'Type MIGRATE to proceed with the cutover: ' CONFIRM || true
  fi
  [ "${CONFIRM:-}" = "MIGRATE" ] || die "aborted — no destructive change made"
}

do_cutover(){
  echo "CUTOVER: stop Cara on $SRC_HOST -> copy state -> start Cara on $TGT_HOST with"
  echo "the SAME bot token -> disable (NOT delete) the source. Short ingest/voice gap."
  confirm_gate
  snapshot_source
  restore_state
  start_and_verify
  retire_source
  cleanup
  log "CUTOVER DONE. Test @cara_assist_bot: a note; 'покажи заметки' (migrated notes,"
  log "1..N numbers); a reminder that fires; a voice note. Source kept installed+disabled."
  log "Rollback (ONLY before the target takes new traffic):"
  log "  ${SSH_SRC[*]} $SRC_HOST 'systemctl enable --now $SERVICE'  + stop the target"
  log "  (after the target has handled messages, copy its DB back to source first)."
}

case "${1:-check}" in
  check)   preflight ;;
  prepare) preflight; install_app
           log "PREPARE DONE: app + whisper on target; SOURCE STILL LIVE (no downtime)."
           log "Next:  CONFIRM=MIGRATE $0 cutover" ;;
  cutover) do_cutover ;;
  --go)    preflight; install_app; do_cutover ;;
  *) die "usage: $0 [check|prepare|cutover|--go]   (cutover/--go need CONFIRM=MIGRATE or a typed MIGRATE)" ;;
esac
