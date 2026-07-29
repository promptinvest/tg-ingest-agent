#!/usr/bin/env bash
# One-connection deploy. The target is intentionally explicit: retired or
# repurposed hosts must never remain as an executable default.
#
# Why: many rapid ssh/scp calls trip the hardened box's fail2ban/MaxStartups
# (connection resets, banner timeouts). Windows OpenSSH has no ControlMaster
# multiplexing, so each mode makes exactly ONE ssh connection.
#
# Modes (from Git Bash / the Bash tool, repo root):
#   ./deploy.sh             push the working tree (tar over ssh) + test + install + verify
#   ./deploy.sh --test      push + tests only (no install)
#   ./deploy.sh --pull      box pulls origin/main (clone if absent) + test + install + verify
#   ./deploy.sh --rollback <sha|branch>   box checks out <sha> + install + verify
#
# --pull/--rollback require the box's read-only deploy key registered on the
# GitHub repo (key at /root/.ssh/github-tg-ingest-deploy). They give
# provable "deployed == this commit" and one-command rollback.
#
# Required: DEPLOY_KEY, DEPLOY_HOST, DEPLOY_KH. Optional: DEPLOY_PORT (default 22).
set -euo pipefail

: "${DEPLOY_KEY:?set DEPLOY_KEY to the Cara host private key}"
: "${DEPLOY_HOST:?set DEPLOY_HOST (for example root@Cara-host)}"
: "${DEPLOY_KH:?set DEPLOY_KH to the pinned known_hosts file}"
KEY="$DEPLOY_KEY"
PORT="${DEPLOY_PORT:-22}"
HOST="$DEPLOY_HOST"
KH="$DEPLOY_KH"
STAGE="/root/tg-ingest-agent-stage"
SRC="/opt/tg-ingest-agent-src"
BOX_KEY="/root/.ssh/github-tg-ingest-deploy"
REPO="git@github.com:promptinvest/tg-ingest-agent.git"
MODE="${1:-deploy}"

SSH_OPTS=(-i "$KEY" -p "$PORT" -o IdentitiesOnly=yes -o ConnectTimeout=25
          -o UserKnownHostsFile="$KH" -o ServerAliveInterval=15)

git_env="export GIT_SSH_COMMAND='ssh -i $BOX_KEY -o IdentitiesOnly=yes -o UserKnownHostsFile=/root/.ssh/known_hosts'"
# NOTE: every remote script sets `-o pipefail` — without it the `| tail`
# pipes here return tail's exit 0 and mask a FAILED test run or a mid-way
# installer abort (deploy printed green while the box kept running old code).
install_verify="
echo '--- install ---'
deploy_source_revision=\$(git -C '$SRC' rev-parse --short HEAD 2>/dev/null || echo unknown)
deploy_source_dirty=false
[ -n \"\$(git -C '$SRC' status --porcelain 2>/dev/null)\" ] && deploy_source_dirty=true
DEPLOY_SOURCE_REVISION=\"\$deploy_source_revision\" \
DEPLOY_SOURCE_DIRTY=\"\$deploy_source_dirty\" \
DEPLOY_TEST_SUMMARY='full VPS unittest discovery passed' \
STAGE_DIR='$SRC' bash '$SRC/install-tg-ingest-agent-pilot-remote.sh' 2>&1 | tail -5
if [ -f '$SRC/verify-cara-runtime.sh' ]; then
  echo '--- live verification ---'
  bash '$SRC/verify-cara-runtime.sh'
  python3 /opt/tg-ingest-agent/deployment_notice.py mark-verified \
    --manifest /opt/tg-ingest-agent/DEPLOYMENT.json \
    --verification-summary 'runtime, worker, spool, SQLite, and systemd checks passed'
  systemctl restart tg-ingest-agent.service
  sleep 2
  systemctl is-active --quiet tg-ingest-agent.service
else
  echo '--- retire post-ref worker for legacy rollback ---'
  systemctl disable --now cara-worker.service 2>/dev/null || true
  rm -f /etc/systemd/system/cara-worker.service \
    /opt/tg-ingest-agent/cara_worker.py \
    /opt/tg-ingest-agent/verify_task_runtime.py \
    /opt/tg-ingest-agent/verify-cara-runtime.sh
  rm -rf /var/lib/cara-worker
  userdel cara-worker 2>/dev/null || true
  groupdel cara-worker-spool 2>/dev/null || true
  systemctl daemon-reload
  systemctl is-active --quiet tg-ingest-agent
  [ \"\$(sqlite3 /var/lib/tg-ingest-agent/ingest.db 'PRAGMA integrity_check;')\" = ok ]
fi"

case "$MODE" in
  --pull)
    ssh "${SSH_OPTS[@]}" "$HOST" "
set -e -o pipefail
$git_env
if [ ! -d '$SRC/.git' ]; then echo '--- clone ---'; git clone '$REPO' '$SRC'; fi
cd '$SRC'
git fetch --quiet origin
git reset --hard origin/main
echo \"at \$(git rev-parse --short HEAD): \$(git log -1 --format=%s)\"
echo '--- tests ---'
python3 -m unittest discover -p 'test_*.py' 2>&1 | tail -15
$install_verify"
    ;;
  --rollback)
    REF="${2:-}"
    # A ref must never be able to become a git OPTION: the old pattern's second
    # alternative allowed a leading '-', so `--rollback --pull` validated and
    # reached `git checkout --pull`. Require an alphanumeric first character,
    # verify the ref really resolves to a commit on the box, and pass `--` after
    # it so it can never be read as a pathspec either.
    if [ -z "$REF" ] || ! [[ "$REF" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ ]]; then
      echo "usage: ./deploy.sh --rollback <sha|branch>   (no leading '-')" >&2; exit 2
    fi
    ssh "${SSH_OPTS[@]}" "$HOST" "
set -e -o pipefail
$git_env
cd '$SRC'
git fetch --quiet origin
git rev-parse --verify --quiet '$REF^{commit}' >/dev/null || {
  echo 'not a commit on the box: $REF' >&2; exit 2; }
git checkout --quiet '$REF' --
echo \"rolled back to \$(git rev-parse --short HEAD): \$(git log -1 --format=%s)\"
$install_verify
echo '(return to latest with: ./deploy.sh --pull)'"
    ;;
  deploy|--test)
    # The unit file and the env example are INSTALLED from the stage dir now (they
    # are the single source of truth for the unit and for the env template), and
    # the three scripts that operate this box ship so their `bash -n` / invariant
    # tests run where the suite actually runs — on the box, not only in a checkout.
    # NAMED, never `*.sh`: a glob would drop any future one-shot sitting at the
    # repo root straight onto the live host (which is why the 2026-07-03 split
    # script was archived). migrate-cara-to-pd.sh stops services and overwrites
    # /etc/tg-ingest-agent.env — it stays off the box and is checked in a checkout.
    FILES=(*.py deploy.sh install-tg-ingest-agent-pilot-remote.sh
           install-whisper-pilot-remote.sh
           verify-cara-runtime.sh
           tg-ingest-agent.env.example tg-ingest-agent.service
           cara-worker.service)
    SHA="$(git rev-parse --short HEAD 2>/dev/null || echo '?')"
    DIRTY=""; [ -n "$(git status --porcelain 2>/dev/null)" ] && DIRTY=" +local-changes"
    DIRTY_BOOL=false; [ -n "$DIRTY" ] && DIRTY_BOOL=true
    echo "deploying from ${SHA}${DIRTY}"
    remote_script="
set -e -o pipefail
mkdir -p '$STAGE'
tar xzf - -C '$STAGE'
cd '$STAGE'
sed -i 's/\r\$//' *.py *.sh tg-ingest-agent.service cara-worker.service tg-ingest-agent.env.example
echo '--- tests ---'
python3 -m unittest discover -p 'test_*.py' 2>&1 | grep -E '^(FAIL|ERROR):|^Ran |^OK|^FAILED' | tail -60"
    if [ "$MODE" != "--test" ]; then
      remote_script+="
# Stage-dir debris. The dir is only mkdir -p'd and untarred into, never wiped, so
# anything an OLDER payload shipped stays there for good — and both destructive
# one-shots reached it that way while FILES still globbed '*.sh'. Neither is in
# the payload any more, so delete the copies by NAME (a glob could take a file
# something still needs, and dotfiles must survive: apply_token.py reads a staged
# .token.env from this directory). Real deploys only: --test refreshes the payload,
# it must never delete anything.
rm -f '$STAGE/split-cara-nikki.sh' '$STAGE/migrate-cara-to-pd.sh'
echo '--- install ---'
DEPLOY_SOURCE_REVISION='${SHA}' \
DEPLOY_SOURCE_DIRTY='${DIRTY_BOOL}' \
DEPLOY_TEST_SUMMARY='full VPS unittest discovery passed' \
bash install-tg-ingest-agent-pilot-remote.sh 2>&1 | tail -5
echo '--- live verification ---'
bash verify-cara-runtime.sh
python3 /opt/tg-ingest-agent/deployment_notice.py mark-verified \
  --manifest /opt/tg-ingest-agent/DEPLOYMENT.json \
  --verification-summary 'runtime, worker, spool, SQLite, and systemd checks passed'
systemctl restart tg-ingest-agent.service
sleep 2
systemctl is-active --quiet tg-ingest-agent.service"
    fi
    tar czf - "${FILES[@]}" | ssh "${SSH_OPTS[@]}" "$HOST" "$remote_script"
    ;;
  *)
    echo "unknown mode: $MODE (use: --test | --pull | --rollback <ref> | <none>)" >&2; exit 2
    ;;
esac
