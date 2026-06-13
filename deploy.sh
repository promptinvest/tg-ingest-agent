#!/usr/bin/env bash
# One-connection deploy to Pilot-VPS.
#
# Why: many rapid ssh/scp calls trip the hardened box's fail2ban/MaxStartups
# (connection resets, banner timeouts). Windows OpenSSH has no ControlMaster
# multiplexing, so instead of N handshakes we make exactly ONE: pipe a tarball
# of the project over a single ssh stdin, then run stage + tests + install +
# verify remotely in that same session.
#
# Usage (from Git Bash / the Bash tool, repo root):
#   ./deploy.sh            # tests + install + verify
#   ./deploy.sh --test     # tests only (no install)
#
# Override connection via env: DEPLOY_KEY, DEPLOY_PORT, DEPLOY_HOST, DEPLOY_KH
set -euo pipefail

KEY="${DEPLOY_KEY:-$HOME/.ssh/do-pilot}"
PORT="${DEPLOY_PORT:-49191}"
HOST="${DEPLOY_HOST:-root@209.38.175.16}"
KH="${DEPLOY_KH:-known_hosts_pilot_rnd}"
STAGE="/root/tg-ingest-agent-stage"
MODE="${1:-deploy}"

SSH_OPTS=(-i "$KEY" -p "$PORT" -o IdentitiesOnly=yes -o ConnectTimeout=25
          -o UserKnownHostsFile="$KH" -o ServerAliveInterval=15)

# Files to ship: all python modules, installer, env example.
FILES=(*.py install-tg-ingest-agent-pilot-remote.sh tg-ingest-agent.env.example)

remote_script="
set -e
mkdir -p '$STAGE'
tar xzf - -C '$STAGE'
cd '$STAGE'
sed -i 's/\r\$//' *.py *.sh
echo '--- tests ---'
python3 -m unittest discover -p 'test_*.py' 2>&1 | tail -3
"
if [ "$MODE" != "--test" ]; then
  remote_script+="
echo '--- install ---'
bash install-tg-ingest-agent-pilot-remote.sh 2>&1 | tail -1
echo -n 'service: '; systemctl is-active tg-ingest-agent
"
fi

# Exactly one ssh connection: tarball in via stdin, pipeline runs remotely.
tar czf - "${FILES[@]}" | ssh "${SSH_OPTS[@]}" "$HOST" "$remote_script"
