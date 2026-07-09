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
STAGE_DIR='$SRC' bash '$SRC/install-tg-ingest-agent-pilot-remote.sh' 2>&1 | tail -5
echo -n 'service: '; systemctl is-active tg-ingest-agent"

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
    if ! [[ "$REF" =~ ^[0-9a-fA-F]{4,40}$|^[A-Za-z0-9._/-]+$ ]]; then
      echo "usage: ./deploy.sh --rollback <sha|branch>" >&2; exit 2
    fi
    ssh "${SSH_OPTS[@]}" "$HOST" "
set -e -o pipefail
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
set -e -o pipefail
mkdir -p '$STAGE'
tar xzf - -C '$STAGE'
cd '$STAGE'
sed -i 's/\r\$//' *.py *.sh
echo '--- tests ---'
python3 -m unittest discover -p 'test_*.py' 2>&1 | tail -15"
    if [ "$MODE" != "--test" ]; then
      remote_script+="
echo '--- install ---'
bash install-tg-ingest-agent-pilot-remote.sh 2>&1 | tail -5
echo -n 'service: '; systemctl is-active tg-ingest-agent"
    fi
    tar czf - "${FILES[@]}" | ssh "${SSH_OPTS[@]}" "$HOST" "$remote_script"
    ;;
  *)
    echo "unknown mode: $MODE (use: --test | --pull | --rollback <ref> | <none>)" >&2; exit 2
    ;;
esac
