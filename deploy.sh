#!/usr/bin/env bash
# One-connection deploy to Pilot-VPS.
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
# Override connection via env: DEPLOY_KEY, DEPLOY_PORT, DEPLOY_HOST, DEPLOY_KH
set -euo pipefail

KEY="${DEPLOY_KEY:-$HOME/.ssh/do-pilot}"
PORT="${DEPLOY_PORT:-49191}"
HOST="${DEPLOY_HOST:-root@209.38.175.16}"
KH="${DEPLOY_KH:-known_hosts_pilot_rnd}"
STAGE="/root/tg-ingest-agent-stage"
SRC="/opt/tg-ingest-agent-src"
BOX_KEY="/root/.ssh/github-tg-ingest-deploy"
REPO="git@github.com:promptinvest/tg-ingest-agent.git"
MODE="${1:-deploy}"

SSH_OPTS=(-i "$KEY" -p "$PORT" -o IdentitiesOnly=yes -o ConnectTimeout=25
          -o UserKnownHostsFile="$KH" -o ServerAliveInterval=15)

git_env="export GIT_SSH_COMMAND='ssh -i $BOX_KEY -o IdentitiesOnly=yes -o UserKnownHostsFile=/root/.ssh/known_hosts'"
install_verify="
echo '--- install ---'
STAGE_DIR='$SRC' bash '$SRC/install-tg-ingest-agent-pilot-remote.sh' 2>&1 | tail -1
echo -n 'service: '; systemctl is-active tg-ingest-agent"

case "$MODE" in
  --pull)
    ssh "${SSH_OPTS[@]}" "$HOST" "
set -e
$git_env
if [ ! -d '$SRC/.git' ]; then echo '--- clone ---'; git clone '$REPO' '$SRC'; fi
cd '$SRC'
git fetch --quiet origin
git reset --hard origin/main
echo \"at \$(git rev-parse --short HEAD): \$(git log -1 --format=%s)\"
echo '--- tests ---'
python3 -m unittest discover -p 'test_*.py' 2>&1 | tail -3
$install_verify"
    ;;
  --rollback)
    REF="${2:-}"
    if ! [[ "$REF" =~ ^[0-9a-fA-F]{4,40}$|^[A-Za-z0-9._/-]+$ ]]; then
      echo "usage: ./deploy.sh --rollback <sha|branch>" >&2; exit 2
    fi
    ssh "${SSH_OPTS[@]}" "$HOST" "
set -e
$git_env
cd '$SRC'
git fetch --quiet origin
git checkout --quiet '$REF'
echo \"rolled back to \$(git rev-parse --short HEAD): \$(git log -1 --format=%s)\"
$install_verify
echo '(return to latest with: ./deploy.sh --pull)'"
    ;;
  deploy|--test)
    FILES=(*.py install-tg-ingest-agent-pilot-remote.sh tg-ingest-agent.env.example)
    SHA="$(git rev-parse --short HEAD 2>/dev/null || echo '?')"
    DIRTY=""; [ -n "$(git status --porcelain 2>/dev/null)" ] && DIRTY=" +local-changes"
    echo "deploying from ${SHA}${DIRTY}"
    remote_script="
set -e
mkdir -p '$STAGE'
tar xzf - -C '$STAGE'
cd '$STAGE'
sed -i 's/\r\$//' *.py *.sh
echo '--- tests ---'
python3 -m unittest discover -p 'test_*.py' 2>&1 | tail -3"
    if [ "$MODE" != "--test" ]; then
      remote_script+="
echo '--- install ---'
bash install-tg-ingest-agent-pilot-remote.sh 2>&1 | tail -1
echo -n 'service: '; systemctl is-active tg-ingest-agent"
    fi
    tar czf - "${FILES[@]}" | ssh "${SSH_OPTS[@]}" "$HOST" "$remote_script"
    ;;
  *)
    echo "unknown mode: $MODE (use: --test | --pull | --rollback <ref> | <none>)" >&2; exit 2
    ;;
esac
